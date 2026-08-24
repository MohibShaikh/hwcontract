#!/usr/bin/env python3
"""Import sigrok-cli --protocol-decoder-jsontrace output (Google Trace Event JSON).

    sigrok-cli -i capture.sr -P spi -A spi --protocol-decoder-jsontrace > trace.json

The Trace Event format timestamps in microseconds; everything is converted to
the ns the judge speaks. cat becomes the event source, name the type, args the
fields. Complete events ("ph": "X") keep their duration; instants ("ph": "i")
get start == end.
"""
import json
import math

from hwcontract.temporal import EventError, normalize_events


def from_traceevents(doc):
    if not isinstance(doc, dict) or not isinstance(doc.get("traceEvents"), list):
        raise EventError("jsontrace document must be a mapping with a traceEvents list")
    events, skipped = [], []
    for i, e in enumerate(doc["traceEvents"]):
        if not isinstance(e, dict):
            raise EventError(f"traceEvents[{i}] must be a mapping")
        ph = e.get("ph")
        ts = e.get("ts")
        if not isinstance(ts, (int, float)) or not math.isfinite(ts) or ts < 0:
            raise EventError(f"traceEvents[{i}].ts must be a finite number >= 0, got {ts!r}")
        if ph == "X":
            dur = e.get("dur", 0)
            if not isinstance(dur, (int, float)) or not math.isfinite(dur) or dur < 0:
                raise EventError(f"traceEvents[{i}].dur must be a finite number >= 0, got {dur!r}")
            start, end = ts * 1000, (ts + dur) * 1000
        elif ph in ("i", "I"):
            start = end = ts * 1000
        else:
            skipped.append(f"traceEvents[{i}]: unsupported ph {ph!r}")
            continue
        events.append({"source": e.get("cat"), "type": e.get("name"),
                       "start_ns": round(start), "end_ns": round(end),
                       "fields": dict(e.get("args") or {})})
    if not events:
        raise EventError("no importable events (ph X or i) in traceEvents"
                         + (f"; skipped: {skipped[:5]}" if skipped else ""))
    if skipped:
        print(f"jsontrace: skipped {len(skipped)} non-X/i events", file=__import__("sys").stderr)
    return normalize_events(events)


def load(path):
    with open(path) as f:
        return from_traceevents(json.load(f))
