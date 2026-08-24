#!/usr/bin/env python3
"""Import sigrok-cli --protocol-decoder-jsontrace output (Google Trace Event JSON).

    sigrok-cli -i capture.sr -P spi -A spi --protocol-decoder-jsontrace > trace.json

sigrok emits decoder annotations as B/E pairs (decode.c jsontrace_annotation):
    {"ph": "B", "ts": 1.0, "pid": "spi", "tid": "Data", "name": "DATA 9F"}
    {"ph": "E", "ts": 2.0, "pid": "spi", "tid": "Data", "name": "DATA 9F"}
Those become one event: source = pid, type = tid, fields = {row, text}.
Plain X (complete) and i (instant) events are also accepted, so hand-written
and other Trace-Event producers work too. Timestamps arrive in microseconds;
everything is converted to the nanoseconds the judge speaks.
"""
import json
import math
import sys

from hwcontract.temporal import EventError, normalize_events


def _us_to_ns(v):
    if not isinstance(v, (int, float)) or isinstance(v, bool) or not math.isfinite(v) or v < 0:
        raise EventError(f"timestamp must be a finite number >= 0, got {v!r}")
    return v * 1000


def from_traceevents(doc):
    if not isinstance(doc, dict) or not isinstance(doc.get("traceEvents"), list):
        raise EventError("jsontrace document must be a mapping with a traceEvents list")
    events, skipped, open_spans = [], [], {}
    for i, e in enumerate(doc["traceEvents"]):
        if not isinstance(e, dict):
            raise EventError(f"traceEvents[{i}] must be a mapping")
        ph = e.get("ph")
        if ph in ("B", "E"):
            key = (e.get("pid"), e.get("tid"))
            ts = _us_to_ns(e.get("ts"))
            if ph == "B":
                open_spans.setdefault(key, []).append((i, ts, e))
                continue
            if not open_spans.get(key):
                raise EventError(f"traceEvents[{i}]: ph E without a matching B "
                                 f"for pid={e.get('pid')!r} tid={e.get('tid')!r}")
            bi, bts, b = open_spans[key].pop()
            events.append({"source": b.get("pid"), "type": b.get("tid"),
                           "start_ns": round(bts), "end_ns": round(ts),
                           "fields": {"row": b.get("tid"), "text": b.get("name"),
                                      **(b.get("args") or {})}})
        elif ph in ("X", "i", "I"):
            ts = _us_to_ns(e.get("ts"))
            dur = e.get("dur", 0) if ph == "X" else 0
            if not isinstance(dur, (int, float)) or isinstance(dur, bool) \
                    or not math.isfinite(dur) or dur < 0:
                raise EventError(f"traceEvents[{i}].dur must be a finite number >= 0, got {dur!r}")
            events.append({"source": e.get("cat") or e.get("pid"), "type": e.get("name"),
                           "start_ns": round(ts), "end_ns": round(ts + dur * 1000),
                           "fields": dict(e.get("args") or {})})
        else:
            skipped.append(f"traceEvents[{i}]: unsupported ph {ph!r}")
    dangling = [f"ph B at traceEvents[{i}] never closed (pid={b.get('pid')!r} tid={b.get('tid')!r})"
                for i, ts, b in [item for stack in open_spans.values() for item in stack]]
    if dangling:
        raise EventError("unclosed jsontrace spans:\n  - " + "\n  - ".join(dangling))
    if not events:
        raise EventError("no importable events (ph X, i or B/E pairs) in traceEvents"
                         + (f"; skipped: {skipped[:5]}" if skipped else ""))
    if skipped:
        print(f"jsontrace: skipped {len(skipped)} non-annotation events", file=sys.stderr)
    return normalize_events(events)


def load(path):
    with open(path) as f:
        return from_traceevents(json.load(f))
