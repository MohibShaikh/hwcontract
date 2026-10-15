#!/usr/bin/env python3
"""Demo: the temporal engine catches a SPI DMA ordering fault. No hardware.

Synthesizes 100 frames of raw CS/SCK/MOSI waveforms at 100 MHz, extracts pin
edges (edges.py), and judges them against the bundled spi-frame contract:

  - all-healthy frames                     -> PASS, with latency percentiles
  - frame 77: CS asserts after SCK starts  -> FAIL, simulated DMA ordering fault
  - frame 42: MOSI settles 10ns too late   -> FAIL, setup-time violation

The broken edges also roundtrip through artificial B/E jsontrace annotations
to exercise the importer. These are NOT real SPI decoder output.

This demo checks pin ordering and setup timing; it does not decode SPI bytes
or run a loopback test.

Run after installation: python -m hwcontract.demo spi
No hardware, no network.
"""
import json
from hwcontract.edges import edge_events
from hwcontract.judge import load_contract, evidence, sha256_of
from hwcontract.contracts import EXAMPLES
from hwcontract.temporal import judge_events, render_events

CONTRACT = str(EXAMPLES / "spi-frame.contract.yaml")

DT_NS = 10            # 100 MHz: fine enough to see a 10ns setup violation
PERIOD = 20_000       # ns between frames
SETUP = 300           # CS asserts 300ns before the first rising SCK edge
HALF = 500            # SCK half period
N_EDGES = 8
N_FRAMES = 100


def frame_transitions(i, broken_cs=False, mosi_late=False):
    """[(time_ns, channel, value)] for one frame, in wire order."""
    t0 = i * PERIOD
    trans = [("gpio.cs", t0, 0)]                          # CS asserted (active low)
    for k in range(N_EDGES):
        rising = t0 + SETUP + k * 2 * HALF
        trans.append(("spi.mosi", rising - (10 if (mosi_late and k == 3) else 100), k % 2))
        trans.append(("spi.sck", rising, 1))
        trans.append(("spi.sck", rising + HALF, 0))
    trans.append(("gpio.cs", t0 + SETUP + N_EDGES * 2 * HALF + 200, 1))
    if broken_cs:
        trans[0] = ("gpio.cs", t0 + SETUP + 300, 0)       # DMA bug: CS 300ns late
    return trans


def build_channels(broken_cs_frame=None, mosi_late_frame=None):
    total = N_FRAMES * PERIOD + 5000
    # one leading idle sample: a transition at t=0 must stay visible as an edge
    channels = {"gpio.cs": [1] * (total // DT_NS + 1),
                "spi.sck": [0] * (total // DT_NS + 1),
                "spi.mosi": [0] * (total // DT_NS + 1)}
    for i in range(N_FRAMES):
        for ch, t, v in frame_transitions(i, broken_cs=(i == broken_cs_frame),
                                          mosi_late=(i == mosi_late_frame)):
            idx = round(t / DT_NS) + 1
            channels[ch][idx:] = [v] * (len(channels[ch]) - idx)
    return channels


def to_jsontrace(edges):
    """Artificial B/E importer fixture; not real SPI decoder output."""
    out = []
    for e in edges:
        for ph in ("B", "E"):
            ts = e["start_ns"] if ph == "B" else e["end_ns"]
            out.append({"ph": ph, "ts": ts / 1000, "pid": e["source"],
                        "tid": e["type"], "name": f"{e['type']} value={e['fields'].get('value')}"})
    return {"traceEvents": out}


def main():
    contract = load_contract(CONTRACT)
    outcomes = []

    def print_evidence(events):
        print("evidence: " + json.dumps(evidence(
            CONTRACT, events_sha256=sha256_of(events), event_count=len(events),
            input_origin="synthetic", samplerate_hz=100_000_000, frames=N_FRAMES),
            sort_keys=True))
    channels = build_channels()
    n_trans = sum(1 for ch in channels.values()
                  for a, b in zip(ch, ch[1:]) if a != b)
    edges = edge_events(channels, DT_NS)

    results, ok = judge_events(contract, edges)
    outcomes.append(ok)
    print(f"\n=== {N_FRAMES} healthy frames: {n_trans} raw edges from "
          f"{len(channels)} channels @ {1e9 / DT_NS / 1e6:.0f}MHz -> {'PASS' if ok else 'FAIL'} ===")
    print(render_events(results))
    print_evidence(edges)

    broken_channels = build_channels(broken_cs_frame=77, mosi_late_frame=42)
    broken = edge_events(broken_channels, DT_NS)
    results, ok = judge_events(contract, broken)
    outcomes.append(ok)
    print(f"\n=== frame 77: CS asserts after SCK starts; frame 42: MOSI settles late "
          f"-> {'PASS' if ok else 'FAIL'} ===")
    print(render_events(results))

    print_evidence(broken)
    from hwcontract.jsontrace import from_traceevents
    roundtripped = from_traceevents(json.loads(json.dumps(to_jsontrace(broken))))
    imported, ok = judge_events(contract, roundtripped)
    outcomes.append(ok)
    fails = [r for r in imported if r["violations"]]
    print(f"\n=== same broken edges as artificial B/E jsontrace -> "
          f"{'PASS' if ok else 'FAIL'} ===")
    for r in fails:
        print(f"  {r['assertion']}: {r['hint']}")
    print_evidence(roundtripped)

    print("\nSynthetic pin timing only: no SPI byte decoding or loopback test is performed.")
    print("The ordering is broken in two frames; the judge names the edge and nanosecond.")
    return 0 if outcomes == [True, False, False] else 1


if __name__ == "__main__":
    raise SystemExit(main())
