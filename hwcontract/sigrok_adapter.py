#!/usr/bin/env python3
"""Logic-analyzer I/O: read 0/1 captures (CSV or live sigrok) into pulse runs.

observe() is the fixed WS2812-style bucketing behind capture_ws2812 and the
pytest plugin's capture_csv. Judging a capture against a contract goes through
`hwcontract --check`, which measures it the way the contract says
(hwcontract/measure.py).

    python -m hwcontract.sigrok_adapter --csv capture.csv --samplerate 24000000 > observations.json
    python -m hwcontract.sigrok_adapter --driver fx2lafw --channel D0 \
        --samplerate 24000000 --samples 200000 > observations.json   # needs sigrok-cli
    python -m hwcontract.sigrok_adapter --demo        # self-check, no hardware

observe() sorts highs into 0/1 bits by a threshold, files each low under the
bit it follows, and reports every bucket's full distribution.
"""
import json
import math
import re
import subprocess
import sys
from collections.abc import Sequence

# calibration knobs (ns). WS2812: 0-bit high ~350, 1-bit high ~700; lows ~800/600.
HIGH_SPLIT = 525     # high pulse >  this => T1H, else T0H
LOW_SPLIT = 700      # a low with no high before it: <  this => T1L, else T0L
RESET_NS = 5000      # low pulse > this => the latch/RESET gap, not a bit


def positive_finite(value, name):
    """Validate numeric capture parameters without silently rounding them."""
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        number = float("nan")
    if isinstance(value, bool) or not math.isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be a positive finite number, got {value!r}")
    return number


def sample_period(samplerate):
    """Sample duration in ns, for a positive finite rate in Hz."""
    return positive_finite(1e9 / positive_finite(samplerate, "samplerate"), "dt_ns")


def validate_samples(samples, name="samples"):
    if not isinstance(samples, Sequence) or isinstance(samples, str):
        raise ValueError(f"{name}: expected a sequence of 0 or 1 samples")
    if not samples:
        raise ValueError(f"{name}: no samples; expected non-empty 0/1 data")
    for i, sample in enumerate(samples):
        if sample not in (0, 1):
            raise ValueError(f"{name}[{i}]: expected 0 or 1, got {sample!r}")


def runs(samples, dt_ns):
    """(level, width_ns) for every run, boundary runs included."""
    dt_ns = positive_finite(dt_ns, "dt_ns")
    validate_samples(samples)
    positive_finite(len(samples) * dt_ns, "capture duration")
    out, prev, n = [], samples[0], 0
    for s in samples:
        if s == prev:
            n += 1
        else:
            out.append((prev, n * dt_ns))
            prev, n = s, 1
    out.append((prev, n * dt_ns))
    return out


def _pctl(sorted_vals, p):
    """Linear-interpolated percentile of an already-sorted list."""
    if len(sorted_vals) == 1:
        return float(sorted_vals[0])
    k = (len(sorted_vals) - 1) * p / 100
    f, c = math.floor(k), math.ceil(k)
    if f == c:
        return float(sorted_vals[int(k)])
    return sorted_vals[f] * (c - k) + sorted_vals[c] * (k - f)


def distribution(widths):
    """Summary of one edge's pulse widths: count, spread, percentiles, jitter."""
    s = sorted(widths)
    p5, p50, p95 = _pctl(s, 5), _pctl(s, 50), _pctl(s, 95)
    return {"count": len(s), "min": round(s[0]), "max": round(s[-1]),
            "p5": round(p5), "p50": round(p50), "p95": round(p95),
            "jitter": round(p95 - p5), "widths": [round(w) for w in s]}


def observe(samples, dt_ns, high_split=HIGH_SPLIT, low_split=LOW_SPLIT, reset_ns=RESET_NS):
    """Bucket pulse widths into bit encodings and summarize each bucket's
    distribution. The judge checks every pulse against the contract windows,
    not just the median: a clean p50 with glitchy tails must not pass.
    Thresholds are the calibration knobs; defaults are WS2812. Pass DShot's
    splits to reuse this for DShot.

    A low belongs to the bit whose high it follows: T1L is the low after a
    1-bit high, however long it is, so a stretched low is blamed on the right
    row. low_split only sorts a low with no high before it in the capture."""
    high_split = positive_finite(high_split, "high_split")
    low_split = positive_finite(low_split, "low_split")
    reset_ns = positive_finite(reset_ns, "reset_ns")
    all_runs = runs(samples, dt_ns)
    buckets = {"T0H": [], "T1H": [], "T1L": [], "T0L": []}
    bit = None                                       # the bit whose low comes next
    for lvl, w in all_runs[1:-1]:                    # drop capture-boundary partials for bit widths
        if lvl == 1:
            bit = "1" if w > high_split else "0"
            buckets[f"T{bit}H"].append(w)
            continue
        if w <= reset_ns:
            buckets[f"T{bit or ('1' if w < low_split else '0')}L"].append(w)
        bit = None
    # RESET scanned across ALL runs so a latch gap at the capture edge isn't lost
    buckets["RESET"] = [w for lvl, w in all_runs if lvl == 0 and w > reset_ns]
    out = []
    for k, v in buckets.items():
        if not v:
            continue
        dist = distribution(v)
        dist.update({"name": k, "unit": "ns", "value": dist["p50"],
                     "resolution_ns": round(dt_ns, 3)})   # widths are known to +/- one sample
        out.append(dist)
    return out


def _read_csv_lines(lines, source):
    """One sample per row, with sigrok's comments and optional logic label."""
    samples = []
    labels = {"logic"}  # sigrok's default label for a digital channel
    ambiguous_label = False
    header_seen = False
    for line_number, line in enumerate(lines, 1):
        value = line.strip()
        if not value:
            continue
        if value.startswith(("#", ";")):
            # label=channel uses the name in sigrok's Channels preamble.
            match = re.fullmatch(r"[;#]\s*Channels \(1/\d+\):\s*([^,;]+)", value)
            if match and not samples:
                label = match.group(1).strip()
                if label in ("0", "1"):
                    ambiguous_label = True
                else:
                    labels.add(label)
            continue
        if not samples and not header_seen and value in labels:
            header_seen = True
            continue
        if not samples and not header_seen and ambiguous_label:
            raise ValueError(f"{source}: line {line_number}: ambiguous numeric channel label; "
                             "export with sigrok csv:label=units (logic header)")
        if value not in ("0", "1"):
            raise ValueError(f"{source}: line {line_number}: expected one 0 or 1 sample "
                             f"per line, got {value!r}; timestamp/channel CSV is unsupported")
        samples.append(int(value))
    if not samples:
        raise ValueError(f"{source}: no samples; expected one 0 or 1 sample per line")
    return samples


def read_csv(path):
    with open(path) as f:
        return _read_csv_lines(f, str(path))


def capture(driver, channel, samplerate, samples, timeout=30):
    sample_period(samplerate)
    count = positive_finite(samples, "samples")
    if not count.is_integer():
        raise ValueError("samples must be a positive integer")
    positive_finite(timeout, "timeout")
    cmd = ["sigrok-cli", "--driver", driver, "--channels", channel,
           "--config", f"samplerate={samplerate}",
           "--samples", str(int(count)), "-O", "csv"]
    out = subprocess.run(cmd, capture_output=True, text=True,
                         check=True, timeout=timeout).stdout   # don't hang on a stuck analyzer
    return _read_csv_lines(out.splitlines(), "sigrok-cli output")


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
    p.add_argument("--samplerate", type=int,
                   help="Hz; required with --csv (a 0/1 CSV has no time base), "
                        "default 24000000 for a live capture")
    p.add_argument("--samples", type=int, default=200_000)
    p.add_argument("--demo", action="store_true")
    a = p.parse_args()

    if a.demo:
        demo()
        sys.exit(0)
    if a.samplerate is None:
        if a.csv:
            p.error("--csv needs --samplerate: the rate the capture was taken at, in Hz")
        a.samplerate = 24_000_000
    try:
        dt_ns = sample_period(a.samplerate)
        samples = read_csv(a.csv) if a.csv else capture(a.driver, a.channel, a.samplerate, a.samples)
        observations = observe(samples, dt_ns)
    except ValueError as e:
        p.error(str(e))
    json.dump(observations, sys.stdout, indent=2)
    print()
