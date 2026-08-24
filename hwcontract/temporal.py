#!/usr/bin/env python3
"""Cross-event temporal assertions: prove relationships between decoded hardware events.

Events are normalized dicts:

    {"source": "spi0", "type": "clock_edge", "start_ns": 184020,
     "end_ns": 184025, "fields": {"value": 1}}

An events contract (kind: events) asserts relationships between them:

    assertions:
      - name: cs-before-first-clock
        when: gpio.cs.value=0          # trigger: chip-select asserts
        require: spi0.clock_edge       # a clock edge must follow...
        within: [80ns, 2us]            # ...inside this window after the trigger
      - name: no-clock-outside-frame
        forbid: spi0.clock_edge        # no clocking...
        while: gpio.cs.value=1         # ...while the frame is deselected
      - name: mosi-setup
        when: spi0.clock_edge.value=1  # each sampling edge
        forbid: gpio.mosi.change
        before: 20ns                   # MOSI stable for 20ns before the edge

Every trigger is checked, not a sample of them. require-assertions report the
measured trigger->response latency distribution (min/p50/p95/p99/max). Zero
triggers FAIL: a contract that never fires proves nothing. Violations carry
the first failure's exact timestamps, so the trace window is one grep away.

CLI:  python3 -m hwcontract.temporal spi-frame.contract.yaml trace.json
(trace.json is sigrok jsontrace or a bare JSON event list)
"""
import bisect
import json
import math
import sys
from statistics import quantiles

from hwcontract.judge import (PASS, FAIL, ContractError, event_matches,
                              load_contract, parse_duration, parse_selector)


class EventError(ValueError):
    """Malformed events. Message lists every problem found."""


def normalize_events(events):
    """Validate and sort events by start_ns; fill defaults; list every problem."""
    if not isinstance(events, list):
        raise EventError(f"events must be a list, got {type(events).__name__}")
    problems, clean = [], []
    for i, e in enumerate(events):
        before = len(problems)
        if not isinstance(e, dict):
            problems.append(f"events[{i}] must be a mapping")
            continue
        etype = e.get("type")
        start = e.get("start_ns")
        end = e.get("end_ns", start)
        if not isinstance(etype, str) or not etype:
            problems.append(f"events[{i}].type must be a non-empty string")
        for label, v in (("start_ns", start), ("end_ns", end)):
            if isinstance(v, bool) or not isinstance(v, (int, float)) \
                    or not math.isfinite(v) or v < 0:
                problems.append(f"events[{i}].{label} must be a finite number >= 0, got {v!r}")
        if len(problems) == before and None not in (start, end) and end < start:
            problems.append(f"events[{i}]: end_ns {end} < start_ns {start}")
        if len(problems) == before:
            clean.append({"source": e.get("source"), "type": etype,
                          "start_ns": round(start), "end_ns": round(end),
                          "fields": dict(e.get("fields") or {})})
    if problems:
        raise EventError("invalid events:\n  - " + "\n  - ".join(problems))
    clean.sort(key=lambda e: e["start_ns"])
    return clean


def _latency(deltas):
    """min/p50/p95/p99/max of measured trigger->response deltas, in ns."""
    s = sorted(deltas)
    if len(s) == 1:
        p50 = p95 = p99 = float(s[0])
    else:
        q = quantiles(s, n=100, method="inclusive")
        p50, p95, p99 = q[49], q[94], q[98]
    return {"count": len(s), "min": round(s[0]), "p50": round(p50),
            "p95": round(p95), "p99": round(p99), "max": round(s[-1])}


def _check_require(a, triggers, events, starts):
    sel = parse_selector(a["require"])
    within = a.get("within")
    if within is None:
        lo, hi = 0, None
    else:
        lo = parse_duration(within[0], signed=True)
        hi = parse_duration(within[1], signed=True)
    deltas, violations, first = [], 0, None
    for t in triggers:
        t0 = t["start_ns"]
        hit = None
        if hi is not None and hi <= 0:
            # backward window: the required event must already exist; the
            # nearest one at or before the trigger is the relevant match
            j = bisect.bisect_right(starts, t0 + hi) - 1
            while j >= 0 and events[j]["start_ns"] >= t0 + lo:
                if event_matches(sel, events[j]):
                    hit = events[j]
                    break
                j -= 1
        else:
            i = bisect.bisect_left(starts, t0 + lo)
            while i < len(events) and (hi is None or events[i]["start_ns"] <= t0 + hi):
                if event_matches(sel, events[i]):
                    hit = events[i]
                    break
                i += 1
        if hit is None:
            violations += 1
            if first is None:
                window = f"[{t0 + lo}ns, {t0 + hi}ns]" if hi is not None else f"[{t0 + lo}ns, ...]"
                first = (f"trigger at {t0}ns: no {a['require']} in {window}")
        else:
            deltas.append(hit["start_ns"] - t0)
    row = {"assertion": a["name"], "triggers": len(triggers), "violations": violations}
    if deltas:
        row["latency"] = _latency(deltas)
    if not triggers:
        row["status"], row["hint"] = FAIL, f"no events matched when: {a['when']}"
    elif violations:
        row["status"] = FAIL
        row["hint"] = f"{first} (first of {violations})"
    else:
        row["status"], row["hint"] = PASS, ""
    return row


def _check_forbid(a, triggers, events, starts):
    sel = parse_selector(a["forbid"])
    forbidden = [e for e in events if event_matches(sel, e)]
    row = {"assertion": a["name"], "triggers": len(triggers), "violations": 0}
    if "while" in a:
        wsel = parse_selector(a["while"])
        spans = [(w["start_ns"], w["end_ns"]) for w in events if event_matches(wsel, w)]
        hits = [f for f in forbidden
                if any(f["start_ns"] < b and f["end_ns"] > s for s, b in spans)]
        row["violations"] = len(hits)
        if not spans:
            row["status"] = FAIL
            row["hint"] = f"no events matched while: {a['while']}"
        elif hits:
            f = hits[0]
            span = next((s, b) for s, b in spans if f["start_ns"] < b and f["end_ns"] > s)
            row["status"] = FAIL
            row["hint"] = (f"forbidden {a['forbid']} at {f['start_ns']}ns overlaps "
                           f"{a['while']} active [{span[0]}, {span[1]}]ns "
                           f"(first of {len(hits)})")
        else:
            note = "" if forbidden else f"note: {a['forbid']} never appears in the trace"
            row["status"], row["hint"] = PASS, note
        return row
    # forbid + before: no forbidden event may start in [trigger - before, trigger)
    before = parse_duration(a["before"])
    violations, first = 0, None
    for t in triggers:
        t0 = t["start_ns"]
        lo, hi = t0 - before, t0
        i = bisect.bisect_left(starts, lo) if starts else 0
        while i < len(events) and events[i]["start_ns"] < hi:
            e = events[i]
            if event_matches(sel, e):
                violations += 1
                if first is None:
                    first = (f"forbidden {a['forbid']} at {e['start_ns']}ns is "
                             f"{t0 - e['start_ns']}ns before {a['when']} at {t0}ns "
                             f"(needs >= {before}ns)")
            i += 1
    row["violations"] = violations
    if not triggers:
        row["status"], row["hint"] = FAIL, f"no events matched when: {a['when']}"
    elif violations:
        row["status"], row["hint"] = FAIL, f"{first} (first of {violations})"
    else:
        note = "" if forbidden else f"note: {a['forbid']} never appears in the trace"
        row["status"], row["hint"] = PASS, note
    return row


def judge_events(contract, events):
    """(results, ok) for an events contract against normalized events."""
    events = normalize_events(events)
    starts = [e["start_ns"] for e in events]
    results, ok = [], True
    for a in contract["assertions"]:
        triggers = []
        if "when" in a:
            sel = parse_selector(a["when"])
            triggers = [e for e in events if event_matches(sel, e)]
        row = (_check_require(a, triggers, events, starts) if "require" in a
               else _check_forbid(a, triggers, events, starts))
        if row["status"] != PASS:
            ok = False
        results.append(row)
    return results, ok


def render_events(results):
    lines = [f"{'assertion':<24} {'triggers':>8} {'viol.':>6}  {'status':<7} hint", "-" * 78]
    for r in results:
        lines.append(f"{r['assertion']:<24} {r['triggers']:>8} {r['violations']:>6}  "
                     f"{r['status']:<7} {r['hint']}")
        if "latency" in r:
            lat = r["latency"]
            lines.append(f"{'  latency ns':<24} {lat['min']:>8}   p50 {lat['p50']:>7}  "
                         f"p95 {lat['p95']:>7}  p99 {lat['p99']:>7}  max {lat['max']:>7}")
    return "\n".join(lines)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        sys.exit(__doc__)
    from hwcontract.jsontrace import from_traceevents
    from hwcontract.judge import render_verdict, sha256_of, verdict
    try:
        doc = json.load(open(argv[1]))
    except (OSError, json.JSONDecodeError) as e:
        sys.exit(f"hwcontract: cannot read {argv[1]}: {e}")
    events = from_traceevents(doc) if isinstance(doc, dict) and "traceEvents" in doc else doc
    try:
        results, ok = judge_events(load_contract(argv[0]), events)
    except (ContractError, EventError) as e:
        sys.exit(f"hwcontract: {e}")
    v = verdict(argv[0], results, ok, input_kind="events",
                input_sha256=sha256_of(events), event_count=len(events))
    print(render_verdict(v, render_events(results)))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
