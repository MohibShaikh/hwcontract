#!/usr/bin/env python3
"""Demo: the temporal engine catches a SPI DMA ordering fault. No hardware.

Synthesizes 100 decoded SPI frames (CS, SCK edges, MOSI changes) and judges
them against the bundled spi-frame.contract.yaml:

  - all-healthy frames                     -> PASS, with latency percentiles
  - frame 77: CS asserts after SCK starts  -> FAIL, Zephyr #110302's DMA bug
  - frame 42: MOSI settles 10ns too late   -> FAIL, setup-time violation

Then writes the broken stream as sigrok jsontrace and re-judges it through
the importer, so the sigrok-cli plumbing is proven end to end.

The point: the data is perfect in every frame. A loopback test passes. The
ordering is broken, and only a cross-signal assertion notices.

Run from the repo root:  python3 demo/spi_dma_temporal.py
Stdlib only. No hardware, no network.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
from hwcontract.judge import load_contract
from hwcontract.jsontrace import from_traceevents
from hwcontract.temporal import judge_events, render_events

HERE = os.path.dirname(os.path.abspath(__file__))
CONTRACT = os.path.join(HERE, os.pardir, "hwcontract", "examples", "spi-frame.contract.yaml")

PERIOD = 20_000       # ns between frames: idle gaps stay clear of the 10us lookback
SETUP = 300           # CS asserts 300ns before the first rising edge
HALF = 500            # SCK half period
N_EDGES = 8
N_FRAMES = 100


def ev(etype, start, end=None, source="spi0", **fields):
    return {"source": source, "type": etype, "start_ns": start,
            "end_ns": end if end is not None else start, "fields": fields}


def build_stream(broken_cs_frame=None, mosi_late_frame=None):
    events = []
    for i in range(N_FRAMES):
        t0 = i * PERIOD
        cs_at = t0 + SETUP + 300 if i == broken_cs_frame else t0   # DMA bug: CS late
        events.append(ev("cs", cs_at, source="gpio", value=0))
        for k in range(N_EDGES):
            rising = t0 + SETUP + k * 2 * HALF
            late = (i == mosi_late_frame and k == 3)
            events.append(ev("change", rising - (10 if late else 100),
                             source="gpio", line="mosi", value=k % 2))
            events.append(ev("clock_edge", rising, rising + 5, value=1))
            events.append(ev("clock_edge", rising + HALF, rising + HALF + 5, value=0))
        cs1_end = (i + 1) * PERIOD if i + 1 < N_FRAMES else N_FRAMES * PERIOD + 5000
        events.append(ev("cs", t0 + SETUP + N_EDGES * 2 * HALF + 200,
                         cs1_end, source="gpio", value=1))
    events.sort(key=lambda e: e["start_ns"])
    return events


def to_jsontrace(events):
    """Normalized events -> sigrok jsontrace shape (ts/dur in microseconds)."""
    return {"traceEvents": [
        {"name": e["type"], "cat": e["source"],
         "ph": "X" if e["end_ns"] > e["start_ns"] else "i",
         "ts": e["start_ns"] / 1000,
         "dur": (e["end_ns"] - e["start_ns"]) / 1000,
         "args": e["fields"]}
        for e in events]}


def main():
    contract = load_contract(CONTRACT)

    healthy = build_stream()
    results, ok = judge_events(contract, healthy)
    print(f"\n=== {N_FRAMES} healthy frames ({len(healthy)} events) -> {'PASS' if ok else 'FAIL'} ===")
    print(render_events(results))

    broken = build_stream(broken_cs_frame=77, mosi_late_frame=42)
    results, ok = judge_events(contract, broken)
    print(f"\n=== frame 77: CS asserts after SCK starts; frame 42: MOSI settles late "
          f"-> {'PASS' if ok else 'FAIL'} ===")
    print(render_events(results))

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(to_jsontrace(broken), f)
        trace_path = f.name
    imported, ok = judge_events(contract, from_traceevents(json.load(open(trace_path))))
    fails = [r for r in imported if r["violations"]]
    print(f"\n=== same broken stream through the sigrok jsontrace importer "
          f"-> {'PASS' if ok else 'FAIL'} ===")
    for r in fails:
        print(f"  {r['assertion']}: {r['hint']}")
    os.unlink(trace_path)

    print("\nThe data is perfect in all 100 frames; a loopback test passes.")
    print("The ordering is broken in two, and the judge names the edge and the nanosecond.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
