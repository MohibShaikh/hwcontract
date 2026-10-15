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
from decimal import Decimal, InvalidOperation

from hwcontract.sigrok_adapter import positive_finite, validate_samples
from hwcontract.temporal import normalize_events


def edge_events(channels, dt_ns):
    """{channel_name: [0/1 samples]} -> sorted, normalized edge events.
    The first sample is the baseline level; captures should lead with a
    little idle so a transition at t=0 stays visible."""
    dt_ns = positive_finite(dt_ns, "dt_ns")
    if not isinstance(channels, dict) or not channels:
        raise ValueError("channels must be a non-empty mapping of names to 0/1 samples")
    count = None
    for name, samples in channels.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError("channel names must be non-empty strings")
        validate_samples(samples, name)
        if count is not None and len(samples) != count:
            raise ValueError("channels must have the same number of samples")
        count = len(samples)
    positive_finite(count * dt_ns, "capture duration")
    events = []
    for name, samples in channels.items():
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
    dropped; separators may be commas or semicolons (sigrok -O csv).

    Each row must contain every channel exactly once. Time values, if present,
    must be finite, nonnegative and uniformly increasing; dt_ns supplied to
    edge_events remains the time scale. Deduplicated/event CSV is unsupported.
    """
    names = None
    channels = {}
    delimiter = None
    previous_time = interval = None

    def error(line, message):
        return ValueError(f"{path}: line {line}: {message}")

    with open(path, newline="") as f:
        for line_number, line in enumerate(f, 1):
            text = line.strip()
            if not text:
                continue
            if names is None and text.startswith("#"):
                # Historical sigrok-style '#Time,CS,SCK' header.
                candidate = text[1:].strip()
                if candidate.split(",")[0].split(";")[0].strip().lower() in ("time", "t"):
                    text = candidate
                else:
                    continue
            elif text.startswith("#"):
                continue
            elif (text.startswith("; ") or text == ";") and (names is None or delimiter != ";"):
                # With semicolon-separated data, a leading empty cell must
                # not be mistaken for a comment and erase an entire sample.
                continue
            if delimiter is None:
                delimiter = ";" if ";" in text and "," not in text else ","
            try:
                row = [cell.strip() for cell in next(csv.reader([text], delimiter=delimiter,
                                                               strict=True))]
            except csv.Error as e:
                raise error(line_number, f"malformed CSV: {e}") from e
            if names is None:
                start = int(row[0].lower() in ("time", "t", ""))
                names = row[start:]
                if not names or any(not n or n in ("0", "1") for n in names):
                    raise error(line_number, "expected a header of non-empty channel names")
                if len(set(names)) != len(names):
                    raise error(line_number, "duplicate channel names in header")
                channels = {name: [] for name in names}
                continue
            if len(row) != len(names) + start:
                raise error(line_number, f"expected {len(names) + start} columns, got {len(row)}")
            values = row[start:]
            for name, value in zip(names, values):
                if value not in ("0", "1"):
                    raise error(line_number, f"channel {name!r}: expected 0 or 1, got {value!r}")
            if start:
                try:
                    timestamp = Decimal(row[0])
                except InvalidOperation:
                    raise error(line_number, f"invalid Time value {row[0]!r}") from None
                if not timestamp.is_finite() or timestamp < 0:
                    raise error(line_number, "Time must be finite and nonnegative")
                if previous_time is not None:
                    delta = timestamp - previous_time
                    if delta <= 0 or (interval is not None and delta != interval):
                        raise error(line_number, "Time must be uniformly increasing; "
                                    "deduplicated/event CSV is unsupported")
                    interval = delta
                previous_time = timestamp
            for name, value in zip(names, values):
                channels[name].append(int(value))
    if not channels or not any(channels.values()):
        raise ValueError(f"{path}: no samples; expected a channel header followed by 0/1 rows")
    return channels
