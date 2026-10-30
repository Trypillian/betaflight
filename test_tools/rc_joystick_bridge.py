#!/usr/bin/env python3
"""Bridge a USB RC transmitter (or gamepad) into Betaflight SITL.

SITL takes RC over UDP :9004 as an rc_packet — a little-endian double timestamp
followed by 16 uint16 channel values in microseconds (see rc_packet in
src/platform/SIMULATOR/target/SITL/target.h). FEATURE_RX_UDP is the default RX
feature for SITL, so nothing needs configuring on the Betaflight side.

This reads the Linux joystick device directly (/dev/input/jsN, 8-byte events,
no third-party modules). Put an EdgeTX / OpenTX radio into USB Joystick mode
and it shows up as one of these.

The stream must keep running: SITL treats a gap as RX loss and failsafes.

Usage:
  rc_joystick_bridge.py --monitor              # discover axis numbers
  rc_joystick_bridge.py                        # fly with the default AETR map
  rc_joystick_bridge.py --invert 2 --map 0:1,1:2,2:3,3:4,4:5,5:6
"""

import argparse
import os
import socket
import struct
import time

RC_HOST = "127.0.0.1"
RC_PORT = 9004
CHANNEL_COUNT = 16          # SIMULATOR_MAX_RC_CHANNELS
RC_MIN, RC_MID, RC_MAX = 1000, 1500, 2000

JS_EVENT = struct.Struct("IhBB")    # time, value, type, number
JS_EVENT_BUTTON = 0x01
JS_EVENT_AXIS = 0x02
JS_EVENT_INIT = 0x80
AXIS_RANGE = 32767.0

# Betaflight's default channel map is AETR, so channels 1-4 are roll, pitch,
# throttle, yaw. Axes 4+ become AUX1.. for mode switches.
DEFAULT_MAP = "0:1,1:2,2:3,3:4,4:5,5:6,6:7,7:8"


def parse_map(text):
    """"0:1,1:2" -> {axis: zero-based channel index}."""
    mapping = {}
    for pair in filter(None, (p.strip() for p in text.split(","))):
        axis, _, channel = pair.partition(":")
        try:
            axis, channel = int(axis), int(channel)
        except ValueError:
            raise SystemExit(f"bad --map entry {pair!r}, want <axis>:<channel>")
        if not 1 <= channel <= CHANNEL_COUNT:
            raise SystemExit(f"channel {channel} out of range 1..{CHANNEL_COUNT}")
        mapping[axis] = channel - 1
    return mapping


def to_microseconds(value, invert):
    """Joystick axis (-32767..32767) -> RC microseconds."""
    if invert:
        value = -value
    scaled = RC_MID + (value / AXIS_RANGE) * (RC_MAX - RC_MIN) / 2
    return max(RC_MIN, min(RC_MAX, int(round(scaled))))


def find_device():
    candidates = sorted(p for p in os.listdir("/dev/input") if p.startswith("js"))
    if not candidates:
        raise SystemExit(
            "no /dev/input/js* device.\n"
            "  - put the radio in USB Joystick mode and replug it\n"
            "  - if it still does not appear: sudo modprobe joydev")
    return os.path.join("/dev/input", candidates[0])


def initial_channels():
    """Sticks centred, throttle low, aux low — a safe state before the first event."""
    channels = [RC_MID] * CHANNEL_COUNT
    channels[2] = RC_MIN                     # throttle
    for i in range(4, CHANNEL_COUNT):
        channels[i] = RC_MIN                 # aux
    return channels


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--device", help="joystick device (default: first /dev/input/js*)")
    ap.add_argument("--map", default=DEFAULT_MAP,
                    help=f"axis:channel pairs, 1-based channels (default: {DEFAULT_MAP})")
    ap.add_argument("--invert", default="", metavar="AXES",
                    help="comma-separated axis numbers to invert, e.g. 2 for throttle")
    ap.add_argument("--rate", type=float, default=50.0, help="send rate in Hz (default: 50)")
    ap.add_argument("--host", default=RC_HOST)
    ap.add_argument("--port", type=int, default=RC_PORT)
    ap.add_argument("--monitor", action="store_true",
                    help="print axis numbers and channel values instead of flying quietly")
    args = ap.parse_args()

    mapping = parse_map(args.map)
    inverted = {int(a) for a in filter(None, (s.strip() for s in args.invert.split(",")))}
    device = args.device or find_device()

    try:
        js = open(device, "rb")
    except PermissionError:
        raise SystemExit(f"cannot read {device}; add yourself to the 'input' group")
    except FileNotFoundError:
        raise SystemExit(f"{device} does not exist")
    os.set_blocking(js.fileno(), False)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    channels = initial_channels()
    period = 1.0 / args.rate
    started = time.monotonic()
    seen_axes = set()

    print(f"[rc] {device} -> udp://{args.host}:{args.port} at {args.rate:g} Hz")
    print(f"[rc] map {args.map}" + (f", inverted {sorted(inverted)}" if inverted else ""))
    print("[rc] streaming; Ctrl-C to stop (stopping triggers SITL failsafe)")

    try:
        while True:
            deadline = time.monotonic() + period

            # drain every pending joystick event before building the frame
            while True:
                data = js.read(JS_EVENT.size)
                if not data or len(data) < JS_EVENT.size:
                    break
                _, value, kind, number = JS_EVENT.unpack(data)
                if kind & JS_EVENT_INIT:
                    continue                  # synthetic startup event
                if kind & JS_EVENT_AXIS:
                    seen_axes.add(number)
                    if number in mapping:
                        channels[mapping[number]] = to_microseconds(value, number in inverted)
                elif kind & JS_EVENT_BUTTON and number in mapping:
                    channels[mapping[number]] = RC_MAX if value else RC_MIN

            packet = struct.pack(f"<d{CHANNEL_COUNT}H",
                                 time.monotonic() - started, *channels)
            sock.sendto(packet, (args.host, args.port))

            if args.monitor:
                shown = " ".join(f"{c:4d}" for c in channels[:8])
                print(f"\raxes seen {sorted(seen_axes)}  ch1-8 {shown}", end="", flush=True)

            remaining = deadline - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)
    except KeyboardInterrupt:
        print("\n[rc] stopped")
    finally:
        js.close()
        sock.close()


if __name__ == "__main__":
    main()
