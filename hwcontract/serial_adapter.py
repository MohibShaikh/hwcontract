#!/usr/bin/env python3
"""Serial-log adapter: get UART text for the serial-kind judge.

Offline/replay:  python serial_adapter.py --file boot.log
Live (needs pyserial): python serial_adapter.py --port /dev/ttyUSB0 --baud 115200 --seconds 3
Self-check:      python serial_adapter.py --demo

Owns no device config beyond baud - it just reads what the port (or serial-mcp,
or a saved log) emits. The whole capture window IS the time bound: a pattern seen
in an N-second capture was seen within N seconds.
"""
import sys
import time


def read_log(path):
    return open(path, errors="replace").read()


def capture(port, baud=115200, seconds=3.0):
    import serial  # optional dep, only for live capture
    ser = serial.Serial(port, baud, timeout=0.1)
    end = time.monotonic() + seconds
    buf = []
    try:
        while time.monotonic() < end:
            buf.append(ser.read(4096).decode(errors="replace"))
    finally:
        ser.close()
    return "".join(buf)


def demo():
    from hwcontract.judge import run_serial, render
    contract = {"expect": ["IMU init OK", r"boot v\d+"],
                "forbid": ["panic", "Guru Meditation", r"\bnan\b"]}
    good = "boot v3\nsensor warmup...\nIMU init OK\nready\n"
    results, ok = run_serial(contract, good)
    print(render(results))
    assert ok is True
    _, bad_ok = run_serial(contract, "boot v3\nGuru Meditation Error\n")
    assert bad_ok is False
    print("\nself-check OK")


if __name__ == "__main__":
    import argparse
    if "--demo" in sys.argv:
        demo()
        sys.exit(0)
    p = argparse.ArgumentParser()
    p.add_argument("--file")
    p.add_argument("--port")
    p.add_argument("--baud", type=int, default=115200)
    p.add_argument("--seconds", type=float, default=3.0)
    a = p.parse_args()
    sys.stdout.write(read_log(a.file) if a.file else capture(a.port, a.baud, a.seconds))
