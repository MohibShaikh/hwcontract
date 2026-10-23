"""Judge a capture, log or trace against a contract: the core behind
`hwcontract --check`, the judge_file and check_capture MCP tools, the pytest
plugin's check() and the GitHub Action.

No hardware and no path policy: each caller resolves the contract (and confines
paths) its own way, then hands over real paths. The file is read by the
contract's kind: a 0/1 capture CSV or observations JSON for timing, a log for
serial, a sigrok jsontrace or bare event list for events.
"""
import json
import re
import textwrap

from hwcontract import serial_adapter
from hwcontract.jsontrace import from_traceevents
from hwcontract.judge import (FAIL, MARGINAL, load_contract, out_of_window, render,
                              render_verdict, run, run_serial, sha256_of,
                              validate_judge_contract, verdict)
from hwcontract.measure import TOLERANCE, measure, measures
from hwcontract.sigrok_adapter import positive_finite, read_csv, sample_period
from hwcontract.temporal import judge_events, normalize_events, render_events

MAX_LOCATIONS = 20                  # located pulses kept per row
CLUSTER_RATIO = 1.5                 # widths further apart than this are separate problems
_HZ = re.compile(r"\s*([0-9.]+(?:[eE][+-]?[0-9]+)?)\s*([kKmMgG]?)(?:[hH][zZ])?\s*")
_SCALE = {"": 1, "k": 1e3, "m": 1e6, "g": 1e9}


def parse_hz(value):
    """A samplerate in Hz from 24000000, 24e6, '24M', '24MHz' or '24 MHz'."""
    if not isinstance(value, str):
        return value
    m = _HZ.fullmatch(value)
    if not m:
        raise ValueError(f"samplerate must be a rate in Hz like 24000000 or 24MHz, got {value!r}")
    return float(m.group(1)) * _SCALE[m.group(2).lower()]


def _fmt_hz(hz):
    for unit, scale in (("GHz", 1e9), ("MHz", 1e6), ("kHz", 1e3)):
        if hz >= scale:
            return f"{hz / scale:g}{unit}"
    return f"{hz:g}Hz"


def _fmt_t(ns):
    for unit, scale in (("s", 1e9), ("ms", 1e6), ("us", 1e3)):
        if ns >= scale:
            return f"{ns / scale:.4g}{unit}"
    return f"{ns:g}ns"


def _where(p):
    """'at 1.234ms (frame 0, after 1019 highs)' for one located pulse."""
    out = f"at {_fmt_t(p['t_ns'])}"
    if "frame" in p:
        h = p["highs_before"]
        out += f" (frame {p['frame']}, after {h} high{'' if h == 1 else 's'})"
    return out


def load_events(raw):
    doc = json.loads(raw)
    if isinstance(doc, dict) and "traceEvents" in doc:
        return from_traceevents(doc)
    return normalize_events(doc)


def _clusters(pulses, ratio=CLUSTER_RATIO):
    """Pulses grouped by width, split wherever the next width is more than
    ratio times the last: 2-3us stalls and 6-9us gaps are two problems."""
    ordered = sorted(pulses, key=lambda p: p["width_ns"])
    groups = [[ordered[0]]]
    for p in ordered[1:]:
        if p["width_ns"] > ratio * groups[-1][-1]["width_ns"]:
            groups.append([])
        groups[-1].append(p)
    return groups


def judge_samples(c, samples, samplerate):
    """(results, ok, params, notes) for 0/1 samples, measured the way the
    contract says. Rows that fail on pulses carry those pulses' locations; runs
    the contract cannot explain add an 'unmatched' row and fail the verdict."""
    samplerate = positive_finite(parse_hz(samplerate), "samplerate")
    samplerate = int(samplerate) if samplerate.is_integer() else samplerate
    dt_ns = sample_period(samplerate)
    observations, pulses, unmatched = measure(c, samples, dt_ns)
    results, ok = run(c, observations)
    edges, spec = {e["name"]: e for e in c["edges"]}, measures(c)
    blamed = {}                                     # measure -> [(edge, out-of-window widths)]
    for row in results:
        if row["status"] in (FAIL, MARGINAL) and row.get("violations"):
            bad = [p for p in pulses[row["edge"]]
                   if out_of_window(edges[row["edge"]], p["width_ns"], dt_ns)]
            row["locations"] = bad[:MAX_LOCATIONS]
            row["hint"] += f"; first {_where(bad[0])}"
            blamed.setdefault(spec[row["edge"]][0], []).append(
                (row["edge"], [p["width_ns"] for p in bad]))
    notes = []
    for kind, ps in unmatched.items():
        for group in _clusters(ps):
            lo, hi = group[0]["width_ns"], group[-1]["width_ns"]
            span = f"{lo}ns" if lo == hi else f"{lo}-{hi}ns"
            n = len(group)
            group.sort(key=lambda p: p["t_ns"])
            results.append({"edge": f"unmatched {kind}", "typ": "-", "actual": None,
                            "status": FAIL, "count": n, "locations": group[:MAX_LOCATIONS],
                            "hint": (f"{n} {kind} pulse{'s' if n > 1 else ''} of {span} that "
                                     f"match no edge (over {TOLERANCE:g}x outside every {kind} "
                                     f"window); first {_where(group[0])}")})
            ok = False
            near = [e for e, ws in blamed.get(kind, ())
                    if any(w <= lo <= CLUSTER_RATIO * w or hi <= w <= CLUSTER_RATIO * hi
                           for w in ws)]
            if near:        # one population cut in two by TOLERANCE, not by the part
                notes.append(f"the out-of-window pulses of {' and '.join(near)} and the unmatched "
                             f"{kind}s of {span} are one spread of widths. The {TOLERANCE:g}x line "
                             "between them is where this tool stops blaming an edge, not a limit "
                             "of the part.")
    empty = [row["edge"] for row in results if row["status"] == "MISSING"]
    if empty:
        notes.append(f"no pulses landed in {', '.join(empty)}. Check that {_fmt_hz(samplerate)} "
                     "is the rate the capture was taken at and that it is a "
                     f"{c['contract']} signal: every width scales with the samplerate.")
    return results, ok, {"samplerate_hz": samplerate}, notes


def check_file(contract_path, path, samplerate=None, kind=None):
    """(verdict, table, notes) for one file. kind defaults to the contract's;
    a caller that already knows what the file is passes it, and a contract of
    another kind is then an error rather than a silent reinterpretation."""
    c = load_contract(contract_path)
    kind = kind or c.get("kind", "timing")
    with open(path, "rb") as fh:
        raw = fh.read()
    params, notes = {}, []
    if kind == "serial":
        results, ok = run_serial(c, serial_adapter.read_log(path))
        table = render(results)
    elif kind == "events":
        results, ok = judge_events(c, load_events(raw))
        table = render_events(results)
    elif str(path).endswith(".json"):
        results, ok = run(c, json.loads(raw))
        table = render(results)
    else:
        validate_judge_contract(c, "timing")            # before the CSV is read
        if samplerate is None:
            raise ValueError("a 0/1 CSV has no time base: pass the samplerate it was "
                             "captured at (--samplerate HZ)")
        results, ok, params, notes = judge_samples(c, read_csv(path), samplerate)
        table = render(results)
    v = verdict(contract_path, results, ok, input_kind=kind, input_sha256=sha256_of(raw),
                file=str(path), **params)
    return v, table, notes


def report(v, table, notes=()):
    """The human-readable form: verdict line, table, then any notes."""
    return render_verdict(v, table) + "".join(
        "\n\n" + textwrap.fill(n, 78, initial_indent="note: ", subsequent_indent=" " * 6)
        for n in notes)
