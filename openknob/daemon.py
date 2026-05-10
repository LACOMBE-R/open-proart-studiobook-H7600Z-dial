#!/usr/bin/env python3
import argparse
import os
import queue
import select
import socket
import struct
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from openknob.actions import execute_action
from openknob.profiles import ProfileManager
from openknob.window_watcher import WindowWatcher

DEFAULT_DEVICE = None  # auto-detected via sysfs
DEFAULT_SOCKET = f"/run/user/{os.getuid()}/openknob.sock"

ASUS_DIAL_HID_ID = "0B05:0220"  # vendorID:productID in sysfs path


def find_asus_dial_device() -> Optional[str]:
    """Scan /sys/class/hidraw/ for the ASUS Dial and return its /dev/hidrawN path."""
    hidraw_root = Path("/sys/class/hidraw")
    if not hidraw_root.exists():
        return None
    for entry in sorted(hidraw_root.iterdir()):
        try:
            real = entry.resolve()
            if ASUS_DIAL_HID_ID in str(real):
                dev = Path("/dev") / entry.name
                if dev.exists():
                    return str(dev)
        except Exception:
            continue
    return None
HID_REPORT_SIZE = 4
HID_READ_SIZE = 64


@dataclass
class KnobEvent:
    event_type: str
    delta: int = 0
    raw: bytes = b""

    def serialize(self) -> str:
        if self.event_type in ("rotate_cw", "rotate_ccw"):
            return f"{self.event_type} {self.delta}"
        return self.event_type


def signed_int16(packet: bytes, offset: int) -> int:
    return struct.unpack_from("<h", packet, offset)[0]


def decode_packet(packet: bytes) -> Optional[KnobEvent]:
    if len(packet) != HID_REPORT_SIZE:
        return None

    if packet == b"\x0A\x01\x00\x00":
        return KnobEvent("press", raw=packet)
    if packet == b"\x0A\x00\x00\x00":
        return KnobEvent("release", raw=packet)

    delta = signed_int16(packet, 2)
    if delta != 0:
        return KnobEvent("rotate_cw" if delta > 0 else "rotate_ccw", delta=delta, raw=packet)

    return None


def read_packets(device: str) -> Iterable[KnobEvent]:
    try:
        with open(device, "rb", buffering=0) as fh:
            while True:
                data = fh.read(HID_REPORT_SIZE)
                if not data:
                    raise RuntimeError("hidraw device closed unexpectedly")
                if len(data) < HID_REPORT_SIZE:
                    continue
                event = decode_packet(data)
                if event:
                    yield event
    except PermissionError as exc:
        raise RuntimeError(
            f"Permission denied when opening {device}. Ensure the user can read the hidraw device."
        ) from exc


def remove_stale_socket(path: Path) -> None:
    if path.exists():
        try:
            path.unlink()
        except OSError as exc:
            raise RuntimeError(f"Unable to remove existing socket {path}: {exc}") from exc


def create_unix_server(path: Path) -> socket.socket:
    remove_stale_socket(path)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(str(path))
    path.chmod(0o777)  # allow non-root processes (overlay) to connect
    server.listen(4)
    server.setblocking(False)
    return server


def open_hidraw_device(path: str) -> int:
    flags = os.O_RDONLY
    try:
        return os.open(path, flags)
    except PermissionError as exc:
        raise RuntimeError(
            f"Permission denied when opening {path}. Ensure the user can read the hidraw device."
        ) from exc


def read_hidraw_buffer(fd: int, pending: bytes) -> tuple[bytes, bytes]:
    try:
        data = os.read(fd, HID_READ_SIZE)
    except BlockingIOError:
        return pending, b""
    if not data:
        return pending, b""
    pending += data
    reports = []
    while len(pending) >= HID_REPORT_SIZE:
        reports.append(pending[:HID_REPORT_SIZE])
        pending = pending[HID_REPORT_SIZE:]
    return pending, b"".join(reports)


def accept_new_clients(server: socket.socket, clients: list[socket.socket]) -> None:
    try:
        while True:
            conn, _ = server.accept()
            conn.setblocking(False)
            clients.append(conn)
            print(f"Client connected: {conn.fileno()}")
    except BlockingIOError:
        return


def cleanup_clients(clients: list[socket.socket]) -> None:
    for client in clients[:]:
        try:
            client.send(b"")
        except OSError:
            clients.remove(client)
            client.close()


def broadcast(clients: list[socket.socket], message: str) -> None:
    dead = []
    data = message.encode("utf-8")
    for client in clients:
        try:
            client.sendall(data)
        except OSError:
            dead.append(client)
    for client in dead:
        clients.remove(client)
        client.close()


def _build_event_line(event: KnobEvent, label: str, fi: int, fc: int,
                      show_pct: bool, show_ring: bool) -> str:
    sp, sr = int(show_pct), int(show_ring)
    if event.event_type in ("rotate_cw", "rotate_ccw"):
        return f"{event.event_type}\t{event.delta}\t{label}\t{fi}\t{fc}\t{sp}\t{sr}\n"
    if event.event_type == "press":
        return f"press\t{label}\t{fi}\t{fc}\t{sp}\t{sr}\n"
    return f"{event.event_type}\n"


def run_daemon(device: str, socket_path: str, verbose: bool = False) -> None:
    socket_path = os.fspath(socket_path)
    server_path = Path(socket_path)
    server = create_unix_server(server_path)
    clients: list[socket.socket] = []
    hidraw_fd: int = -1

    profile_mgr = ProfileManager()
    profile_q: queue.Queue[str] = queue.Queue()

    def _on_app_change(app_name: str) -> None:
        if not profile_mgr.match_app(app_name):
            return
        func = profile_mgr.current_function
        label = func.label if func else ""
        msg = (
            f"profile_change\t{profile_mgr.active_profile_name}"
            f"\t{label}\t{profile_mgr.func_index}\t{profile_mgr.func_count}\n"
        )
        profile_q.put(msg)
        if verbose:
            print(f"profile → {profile_mgr.active_profile_name} (app={app_name})", flush=True)

    watcher = WindowWatcher(_on_app_change)
    watcher.start()

    print(f"openknob daemon listening on {server_path} and reading {device}", flush=True)
    try:
        hidraw_fd = open_hidraw_device(device)
        server_fd = server.fileno()
        if verbose:
            print(f"hidraw fd={hidraw_fd} server fd={server_fd}", flush=True)

        pending = b""
        reload_tick = 0
        while True:
            # Broadcast pending profile-change messages from the watcher thread
            try:
                while True:
                    msg = profile_q.get_nowait()
                    if clients:
                        broadcast(clients, msg)
            except queue.Empty:
                pass

            # Hot-reload profiles from disk every ~2 s
            reload_tick += 1
            if reload_tick >= 4:
                reload_tick = 0
                if profile_mgr.reload_if_changed() and verbose:
                    print(f"profiles reloaded ({profile_mgr.func_count} functions in '{profile_mgr.active_profile_name}')", flush=True)

            ready, _, _ = select.select([server_fd, hidraw_fd], [], [], 0.5)
            if verbose:
                print(f"select ready={ready}", flush=True)
            if server_fd in ready:
                accept_new_clients(server, clients)
            if hidraw_fd in ready:
                pending, data = read_hidraw_buffer(hidraw_fd, pending)
                if verbose:
                    print(f"hidraw read raw={data.hex() if data else ''} pending={pending.hex()}", flush=True)
                if not data:
                    continue
                for i in range(0, len(data), HID_REPORT_SIZE):
                    packet = data[i : i + HID_REPORT_SIZE]
                    if len(packet) != HID_REPORT_SIZE:
                        if verbose:
                            print(f"hidraw fragment len={len(packet)}", flush=True)
                        continue
                    event = decode_packet(packet)
                    if verbose:
                        if event is None:
                            print(f"raw={packet.hex()} no-action", flush=True)
                        else:
                            print(f"event={event.serialize()} raw={packet.hex()}", flush=True)
                    if event is None:
                        continue

                    # Execute the bound action for this event
                    func = profile_mgr.current_function
                    if func is not None:
                        if event.event_type == "rotate_cw":
                            if execute_action(func.rotate_cw):
                                profile_mgr.next_function()
                        elif event.event_type == "rotate_ccw":
                            if execute_action(func.rotate_ccw):
                                profile_mgr.next_function()
                        elif event.event_type == "press":
                            if execute_action(func.press):
                                profile_mgr.next_function()

                    func = profile_mgr.current_function
                    label    = func.label if func else ""
                    show_pct = func.show_percentage if func else True
                    show_ring = func.show_ring if func else True
                    line = _build_event_line(
                        event, label, profile_mgr.func_index, profile_mgr.func_count,
                        show_pct, show_ring,
                    )
                    if verbose:
                        print(f"broadcast: {line.rstrip()}", flush=True)
                    if clients:
                        broadcast(clients, line)
    except KeyboardInterrupt:
        print("openknob daemon exiting", flush=True)
    finally:
        watcher.stop()
        for client in clients:
            client.close()
        server.close()
        if hidraw_fd != -1:
            try:
                os.close(hidraw_fd)
            except OSError:
                pass
        if server_path.exists():
            server_path.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description="openknob daemon reading ASUS Dial HID packets and broadcasting actions.")
    parser.add_argument("--device", default=None, help="hidraw device path (auto-detected if omitted)")
    parser.add_argument("--socket", default=DEFAULT_SOCKET, help="Unix socket path")
    parser.add_argument("--verbose", action="store_true", help="print detected actions to stdout")
    args = parser.parse_args()

    device = args.device
    if device is None:
        device = find_asus_dial_device()
        if device is None:
            print(
                f"Could not find ASUS Dial ({ASUS_DIAL_HID_ID}) in /sys/class/hidraw. "
                "Is the device connected? Try --device /dev/hidrawN to specify manually.",
                file=sys.stderr,
            )
            return 1
        print(f"Auto-detected ASUS Dial at {device}", flush=True)

    try:
        run_daemon(device, args.socket, verbose=args.verbose)
        return 0
    except RuntimeError as exc:
        print(exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
