#!/usr/bin/env python3
import argparse
import os
import struct
import sys
import time

DEFAULT_DEVICE = "/dev/hidraw1"
PACKET_SIZE = 4


def signed_int16(packet: bytes, offset: int) -> int:
    return struct.unpack_from("<h", packet, offset)[0]


def decode_action(packet: bytes) -> str | None:
    if len(packet) != PACKET_SIZE:
        return None

    if packet == b"\x0A\x01\x00\x00":
        return "press"
    if packet == b"\x0A\x00\x00\x00":
        return "release"

    delta = signed_int16(packet, 2)
    if delta != 0:
        return "rotate_cw" if delta > 0 else "rotate_ccw"

    return None


def read_actions(device: str, count: int, interval: float) -> None:
    if not os.path.exists(device):
        print(f"Error: device '{device}' does not exist.")
        raise SystemExit(1)

    try:
        with open(device, "rb") as fh:
            printed = 0
            for idx in range(count):
                packet = fh.read(PACKET_SIZE)
                if len(packet) < PACKET_SIZE:
                    break
                action = decode_action(packet)
                if action:
                    print(action)
                    printed += 1
                if interval > 0:
                    time.sleep(interval)
    except PermissionError:
        print("Permission denied: make sure you can read the hidraw device.")
        raise SystemExit(1)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read ASUS Dial HID packets and print simple actions for testing."
    )
    parser.add_argument("device", nargs="?", default=DEFAULT_DEVICE, help="hidraw device path")
    parser.add_argument("--count", type=int, default=1000, help="number of packets to read")
    parser.add_argument("--interval", type=float, default=0.01, help="delay between packet reads")
    args = parser.parse_args()

    read_actions(args.device, args.count, args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
