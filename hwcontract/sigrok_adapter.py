#!/usr/bin/env python3
"""Turn a logic-analyzer capture of a WS2812 data line into observations JSON.

    # live capture (needs sigrok-cli + a $10 fx2lafw analyzer on D0):
    python sigrok_adapter.py --driver fx2lafw --channel D0 --samplerate 24000000 \
        --samples 200000 > observations.json
    python assert.py ws2812.contract.yaml observations.json

    # offline: feed a CSV of one 0/1 sample per line (for testing / replay):
    python sigrok_adapter.py --csv capture.csv --samplerate 24000000 > observations.json
    python sigrok_adapter.py --demo        # self-check, no hardware

We measure every HIGH/LOW pulse width, split highs and lows into two clusters by
a threshold (short=0-bit, long=1-bit), and report the median of each -> robust to
jitter. The thresholds are the calibration knobs: real strips and clone chips vary.
"""
import json
import subprocess
import sys
from statistics import median

# calibration knobs (ns). WS2812: 0-bit high ~350, 1-bit high ~700; lows ~800/600.
HIGH_SPLIT = 525     # high pulse >  this => T1H, else T0H
LOW_SPLIT = 700      # low  pulse <  this => T1L, else T0L
RESET_NS = 5000      # low pulse > this => the latch/RESET gap, not a bit


def runs(samples, dt_ns):
    """(level, width_ns) for every run, boundary runs included."""
    out, prev, n = [], samples[0], 0
    for s in samples:
        if s == prev:
            n += 1
        else:
            out.append((prev, n * dt_ns))
            prev, n = s, 1
    out.append((prev, n * dt_ns))
    return out


def observe(samples, dt_ns, high_split=HIGH_SPLIT, low_split=LOW_SPLIT, reset_ns=RESET_NS):
    """Bucket pulse widths into bit encodings. Thresholds are the calibration knobs;
    defaults are WS2812. Pass DShot's splits to reuse this for DShot."""
    all_runs = runs(samples, dt_ns)
    interior = all_runs[1:-1]                        # drop capture-boundary partials for bit widths
    highs = [w for lvl, w in interior if lvl == 1]
    lows = [w for lvl, w in interior if lvl == 0 and w <= reset_ns]
    buckets = {
        "T0H": [w for w in highs if w <= high_split],
        "T1H": [w for w in highs if w > high_split],
        "T1L": [w for w in lows if w < low_split],
        "T0L": [w for w in lows if w >= low_split],
        # RESET scanned across ALL runs so a latch gap at the capture edge isn't lost
        "RESET": [w for lvl, w in all_runs if lvl == 0 and w > reset_ns],
    }
    return [{"name": k, "value": round(median(v)), "unit": "ns"}
            for k, v in buckets.items() if v]


def read_csv(path):
    return [int(t) for line in open(path)
            for t in [line.strip()] if t in ("0", "1")]


def capture(driver, channel, samplerate, samples, timeout=30):
    cmd = ["sigrok-cli", "--driver", driver, "--channels", channel,
           "--config", f"samplerate={samplerate}",
           "--samples", str(samples), "-O", "csv"]
    out = subprocess.run(cmd, capture_output=True, text=True,
                         check=True, timeout=timeout).stdout   # don't hang on a stuck analyzer
    return [int(t) for line in out.splitlines()
            for t in [line.strip()] if t in ("0", "1")]


def synth(dt_ns):
    """Fake a clean-ish stream: a few 0 and 1 bits, then a reset gap."""
    def pulse(width, level):
        return [level] * max(1, round(width / dt_ns))
    s = [0] * 3
    for _ in range(4):
        s += pulse(350, 1) + pulse(800, 0)   # '0' bits
        s += pulse(700, 1) + pulse(600, 0)   # '1' bits
    return s + pulse(60000, 0)                 # RESET as the LAST run; must still be caught


def demo():
    dt = 1e9 / 24_000_000
    obs = {o["name"]: o["value"] for o in observe(synth(dt), dt)}
    print(json.dumps(obs, indent=2))
    assert abs(obs["T0H"] - 350) < dt * 2      # recovered within one sample
    assert abs(obs["T1H"] - 700) < dt * 2
    assert obs["RESET"] > 50000
    print("\nself-check OK")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--csv")
    p.add_argument("--driver", default="fx2lafw")
    p.add_argument("--channel", default="D0")
    p.add_argument("--samplerate", type=int, default=24_000_000)
    p.add_argument("--samples", type=int, default=200_000)
    p.add_argument("--demo", action="store_true")
    a = p.parse_args()

    if a.demo:
        demo()
        sys.exit(0)
    samples = read_csv(a.csv) if a.csv else capture(a.driver, a.channel, a.samplerate, a.samples)
    json.dump(observe(samples, 1e9 / a.samplerate), sys.stdout, indent=2)
    print()
