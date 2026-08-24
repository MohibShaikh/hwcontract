#!/usr/bin/env python3
"""Raw multi-channel edge extraction: logic waveforms -> normalized edge events.

Decoder annotations describe protocol meaning; pin-level assertions need
transitions. This turns per-channel 0/1 sample arrays into events the
temporal judge understands:

    {"source": "gpio.cs", "type": "falling", "start_ns": 1000,
     "end_ns": 1000, "fields": {"value": 0}}

    from hwcontract.edges import edge_events, read_multi_csv
    events = edge_events(read_multi_csv("capture.csv"), dt_ns=10)

read_multi_csv accepts a header row of channel names followed by 0/1 rows,
including sigrok-cli -O csv output (a leading Time column is dropped).
"""
import csv

from hwcontract.temporal import normalize_events


def edge_events(channels, dt_ns):
    """{channel_name: [0/1 samples]} -> sorted, normalized edge events.
    The first sample is the baseline level; captures should lead with a
    little idle so a transition at t=0 stays visible."""
    events = []
    for name, samples in channels.items():
        if not samples:
            continue
        level = samples[0]
        for i, s in enumerate(samples):
            if s == level:
                continue
            events.append({"source": name, "type": "rising" if s else "falling",
                           "start_ns": round(i * dt_ns), "end_ns": round(i * dt_ns),
                           "fields": {"value": s}})
            level = s
    return normalize_events(events)


def read_multi_csv(path):
    """Header row of channel names, then 0/1 rows. A leading Time column is
    dropped; separators may be commas or semicolons (sigrok -O csv)."""
    with open(path, newline="") as f:
        rows = [r for r in csv.reader(f) if r]
    if not rows:
        return {}
    header = [c.strip().lstrip("#").strip() for c in rows[0]]
    start = 0
    if header and header[0].lower() in ("time", "t", ""):
        start = 1
    names = header[start:]
    if not names:
        raise ValueError(f"{path}: no channel columns in header {rows[0]!r}")
    channels = {n: [] for n in names}
    for r in rows[1:]:
        vals = [c.strip() for c in r[start:]]
        if len(vals) < len(names):
            continue                                    # truncated tail row
        for n, v in zip(names, vals):
            if v in ("0", "1"):
                channels[n].append(int(v))
    return channels
