#!/usr/bin/env python3
"""Demo: judge a REAL WS2812B capture, no hardware needed.

Downloads a real 24-LED NeoPixel ring capture (recorded off real hardware by the
sigrok project, 24 MHz), extracts the DATA_IN line, and runs it through hwcontract
against two contracts:

  - the generic WS2812 contract  -> FAILs on T1L (real WS2812B low times are shorter)
  - the matching WS2812B contract -> passes

That contrast is the whole lesson: the tool measures the real signal and holds it
to a spec; contracts are chip-specific. Run from the repo root:  python3 demo/ws2812b_neopixel.py

Stdlib only (urllib, zipfile). Capture is downloaded at runtime, not vendored.
"""
import os
import sys
import urllib.request
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
from hwcontract.judge import load_contract, run, render
from hwcontract.sigrok_adapter import observe

URL = ("https://raw.githubusercontent.com/sigrokproject/sigrok-dumps/master/"
       "led/ws2812b_neopixel24/ws2812b_neopixel24_24mhz.sr")
SAMPLERATE = 24_000_000
WINDOW = 300_000                                    # samples to judge (~12.5 ms; plenty of bits + a RESET)
HERE = os.path.dirname(os.path.abspath(__file__))
EX = os.path.join(HERE, os.pardir, "hwcontract", "examples")


def load_data_in(sr_path):
    """Extract the DATA_IN bit (probe 1 = bit 0) as a 0/1 sample sequence."""
    z = zipfile.ZipFile(sr_path)
    parts = sorted((n for n in z.namelist() if n.startswith("logic-1-")),
                   key=lambda n: int(n.split("-")[-1]))
    data = b"".join(z.read(n) for n in parts)
    bits = data.translate(bytes(b & 1 for b in range(256)))   # each byte -> its bit0
    start = bits.find(bytes([1 - bits[0]]))                    # first edge out of idle
    return list(bits[max(0, start - 50): max(0, start - 50) + WINDOW])


def main():
    cap = os.path.join(HERE, "ws2812b_neopixel24_24mhz.sr")
    if not os.path.exists(cap):
        print(f"downloading real capture -> {cap}")
        urllib.request.urlretrieve(URL, cap)
    samples = load_data_in(cap)
    obs = observe(samples, 1e9 / SAMPLERATE)
    print(f"\nmeasured on the real WS2812B signal ({len(samples)} samples @24MHz):")
    for o in obs:
        print(f"  {o['name']:<6} {o['value']} ns")

    for label, contract in [("generic WS2812 contract", "ws2812.contract.yaml"),
                            ("matching WS2812B contract", "ws2812b.contract.yaml")]:
        results, ok = run(load_contract(os.path.join(EX, contract)), obs)
        print(f"\n=== {label} -> {'PASS' if ok else 'FAIL'} ===")
        print(render(results))


if __name__ == "__main__":
    main()
