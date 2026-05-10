#!/usr/bin/env python3
import argparse
import os
import struct
import sys
import time


DEFAULT_DEVICE = "/dev/hidraw1"
PACKET_SIZE = 4


def signed_byte(value: int) -> int:
    return value - 256 if value > 0x7F else value


def signed_int16(packet: bytes, offset: int) -> int:
    if offset + 2 > len(packet):
        raise ValueError("offset out of packet range")
    return struct.unpack_from("<h", packet, offset)[0]


def print_packet(packet: bytes, index: int = 0) -> None:
    values = list(packet)
    hexline = packet.hex()
    signed_values = [signed_byte(v) for v in values]
    print(f"[{index:04d}] raw={hexline}  bytes={values}")
    for i, (u, s) in enumerate(zip(values, signed_values)):
        if u != 0:
            print(f"    idx={i:>2} unsigned={u:>3} signed={s:>4}")

    try:
        delta = signed_int16(packet, 2)
        if delta != 0:
            print(f"    rot16[@2]={delta:+d}")
    except Exception:
        pass

    if packet == b"\x0A\x01\x00\x00":
        print("    button=pressed")
    elif packet == b"\x0A\x00\x00\x00":
        print("    button=released")


def probe(device: str, count: int, interval: float) -> None:
    if not os.path.exists(device):
        print(f"Error: device '{device}' does not exist.")
        sys.exit(1)

    try:
        with open(device, "rb") as fh:
            print(f"Probing HID raw device: {device}")
            print(f"Reading {count} packets of {PACKET_SIZE} bytes each.\n")
            for idx in range(count):
                packet = fh.read(PACKET_SIZE)
                if len(packet) < PACKET_SIZE:
                    print(f"Short read: expected {PACKET_SIZE} bytes, got {len(packet)} bytes")
                    break
                print_packet(packet, idx)
                if interval > 0 and idx < count - 1:
                    time.sleep(interval)
    except PermissionError:
        print("Permission denied: make sure your user can read the hidraw device (udev rule / group membership).")
        sys.exit(1)
    except Exception as exc:
        print(f"Unexpected error: {exc}")
        sys.exit(1)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Probe ASUS Dial HID raw packets and display byte-level values."
    )
    parser.add_argument("device", nargs="?", default=DEFAULT_DEVICE, help="hidraw device path")
    parser.add_argument("--count", type=int, default=20, help="number of packets to read")
    parser.add_argument("--interval", type=float, default=0.05, help="delay between packets in seconds")
    args = parser.parse_args()

    probe(args.device, args.count, args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
