#!/usr/bin/env python3
"""The judge: join a hardware contract against observations -> pass/marginal/fail.

Pure logic, no hardware, no framework. Imported by server.py (MCP) and runnable
standalone:  python -m hwcontract.judge ws2812b observations.json
              python -m hwcontract.judge --demo
To judge a raw capture CSV, a serial log or an event trace: hwcontract --check.

Verdicts are the four uppercase enums PASS / MARGINAL / FAIL / MISSING.
MARGINAL is in spec but rail-hugging; it makes the overall verdict fail.
Contracts are validated on load (validate_contract): a malformed contract is
a ContractError with every problem listed, never a KeyError mid-judgment.
"""
import hashlib
import json
import math
import os
import sys
from datetime import datetime, timezone
from functools import lru_cache

import yaml

try:                       # prefer google-re2 (pip install google-re2): linear-time, ReDoS-immune
    import re2 as re
except ImportError:
    import re              # stdlib fallback; the input cap in run_serial bounds ReDoS

from hwcontract import __version__

PASS, MARGINAL, FAIL, MISSING = "PASS", "MARGINAL", "FAIL", "MISSING"

# Maximum decoded characters in a serial capture. Reject larger logs rather
# than judging a prefix that might hide forbidden output in the tail.
SERIAL_CAP = 1_000_000

# An observation carrying raw pulse widths ("widths") is judged on every pulse,
# not just the median. Up to this fraction of violating pulses keeps the edge
# MARGINAL (p50 in spec, tails out); beyond it the edge FAILs outright.
VIOLATION_FAIL_PCT = 1.0

_TIMING_KEYS = {"contract", "kind", "unit", "headroom_pct", "edges"}
_SERIAL_KEYS = {"contract", "kind", "expect", "forbid"}
_EVENTS_KEYS = {"contract", "kind", "assertions"}
_EDGE_KEYS = {"name", "min", "typ", "max", "measure", "after", "required"}
MEASURES = ("high", "low", "period")   # what an edge times on a 0/1 capture
_ASSERTION_KEYS = {"name", "when", "require", "within", "forbid", "while", "before"}


class ContractError(ValueError):
    """A contract that fails validation. Message lists every problem found."""


def parse_duration(value, signed=False):
    """'80ns' / '2us' / '1.5ms' -> int ns. Bare ints are already ns.
    signed=True accepts negative values (within-windows that look before the
    trigger); the default rejects them."""
    if isinstance(value, bool):
        raise ValueError(f"invalid duration {value!r}")
    sign = 1
    if signed and isinstance(value, str) and value.strip().startswith("-"):
        sign, value = -1, value.strip()[1:]
    ns = _unsigned_duration(value)
    if ns < 0:
        raise ValueError(f"duration must be >= 0, got {value!r}")
    return sign * ns


def _unsigned_duration(value):
    if isinstance(value, bool):
        raise ValueError(f"invalid duration {value!r}")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"invalid duration {value!r}")
        return round(value)
    mult = {"ns": 1, "us": 1_000, "µs": 1_000, "ms": 1_000_000, "s": 1_000_000_000}
    text = str(value).strip()
    for unit in ("ns", "us", "µs", "ms", "s"):
        if text.endswith(unit):
            num = text[: -len(unit)].strip()
            try:
                v = float(num)
            except ValueError:
                break
            if not math.isfinite(v):
                break
            return round(v * mult[unit])
    raise ValueError(f"invalid duration {value!r} (try '80ns', '2us', '1.5ms')")


def parse_selector(sel):
    """'gpio.cs.value=0' -> {source: gpio, type: cs, field: value, value: '0'}
       'spi0.transfer'  -> {source: spi0, type: transfer}
       'transfer'       -> {source: None, type: transfer}
    The last dotted component is the event type; everything before it is the
    source; a field=value tail filters on event fields."""
    if not isinstance(sel, str) or not sel.strip():
        raise ValueError(f"selector must be a non-empty string, got {sel!r}")
    parts = sel.strip().split(".")
    field = value = None
    if "=" in parts[-1]:
        field, _, value = parts[-1].partition("=")
        if not field or not value:
            raise ValueError(f"invalid field filter in selector {sel!r}")
        parts = parts[:-1]          # the field=value tail is a filter, not a path component
    if not parts or not all(parts):
        raise ValueError(f"invalid selector {sel!r}: empty component")
    source = ".".join(parts[:-1]) or None
    return {"source": source, "type": parts[-1], "field": field, "value": value}


def event_matches(sel, event):
    if sel["source"] is not None and event.get("source") != sel["source"]:
        return False
    if sel["type"] != "*" and event.get("type") != sel["type"]:
        return False
    if sel["field"] is not None:
        fields = event.get("fields") or {}
        if sel["field"] not in fields:
            return False
        got = fields[sel["field"]]
        if str(got) != sel["value"]:
            try:
                if float(got) != float(sel["value"]):
                    return False
            except (TypeError, ValueError):
                return False
    return True


def _num(value, what, problems):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        problems.append(f"{what} must be a number, got {value!r}")
        return None
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite:
        problems.append(f"{what} must be finite, got {value!r}")
        return None
    if value < 0:
        problems.append(f"{what} must be >= 0, got {value!r}")
        return None
    return value


def _try(fn, value, problems, what):
    try:
        return fn(value)
    except (ValueError, TypeError, OverflowError) as e:
        problems.append(f"{what}: {e}")
        return None


def _validate_events(contract, problems):
    """Validate an events contract: selectors parse, durations parse, and each
    assertion picks a coherent shape (require+within, or forbid+while/before)."""
    unknown = set(contract) - _EVENTS_KEYS
    if unknown:
        problems.append(f"unknown keys for an events contract: {sorted(unknown, key=str)}")
    assertions = contract.get("assertions")
    if not isinstance(assertions, list) or not assertions:
        problems.append("'assertions' must be a non-empty list")
        return
    seen = set()
    for i, a in enumerate(assertions):
        label = f"assertions[{a.get('name', i) if isinstance(a, dict) else i}]"
        if not isinstance(a, dict):
            problems.append(f"{label} must be a mapping")
            continue
        unknown = set(a) - _ASSERTION_KEYS
        if unknown:
            problems.append(f"{label} has unknown keys: {sorted(unknown, key=str)}")
        name = a.get("name")
        if not isinstance(name, str) or not name.strip():
            problems.append(f"{label}.name must be a non-empty string")
        elif name in seen:
            problems.append(f"duplicate assertion name: {name!r}")
        else:
            seen.add(name)
        when = None
        has_require, has_forbid = "require" in a, "forbid" in a
        standalone = has_forbid and "while" in a
        if has_require == has_forbid:
            problems.append(f"{label}: exactly one of 'require' or 'forbid' is required")
        if standalone and "when" in a:
            problems.append(f"{label}: 'when' has no effect with forbid+while; drop 'when' or use 'before'")
        if not standalone:
            when = _try(parse_selector, a.get("when"), problems, f"{label}.when")
        if "within" in a and not has_require:
            problems.append(f"{label}: 'within' needs 'require'")
        if "while" in a and not has_forbid:
            problems.append(f"{label}: 'while' needs 'forbid'")
        if "before" in a and not has_forbid:
            problems.append(f"{label}: 'before' needs 'forbid'")
        if "while" in a and "before" in a:
            problems.append(f"{label}: 'while' and 'before' are mutually exclusive")
        if has_require:
            _try(parse_selector, a.get("require"), problems, f"{label}.require")
            within = a.get("within")
            if within is not None:
                if (not isinstance(within, list) or len(within) != 2):
                    problems.append(f"{label}.within must be [min, max]")
                else:
                    signed = lambda v: parse_duration(v, signed=True)
                    lo = _try(signed, within[0], problems, f"{label}.within[0]")
                    hi = _try(signed, within[1], problems, f"{label}.within[1]")
                    if lo is not None and hi is not None and lo > hi:
                        problems.append(f"{label}.within: min {lo} > max {hi}")
        if has_forbid:
            _try(parse_selector, a.get("forbid"), problems, f"{label}.forbid")
            if "while" not in a and "before" not in a:
                problems.append(f"{label}: 'forbid' needs 'while' or 'before'")
            if "while" in a:
                _try(parse_selector, a.get("while"), problems, f"{label}.while")
            if "before" in a:
                _try(parse_duration, a.get("before"), problems, f"{label}.before")


def validate_contract(contract):
    """Raise ContractError listing every problem, or return the contract untouched."""
    problems = []
    if not isinstance(contract, dict):
        raise ContractError(f"contract must be a mapping, got {type(contract).__name__}")

    kind = contract.get("kind", "timing")
    if kind not in ("timing", "serial", "events"):
        problems.append(f"kind must be 'timing', 'serial' or 'events', got {kind!r}")

    name = contract.get("contract")
    if not isinstance(name, str) or not name.strip():
        problems.append("'contract' must be a non-empty string")

    if kind == "serial":
        unknown = set(contract) - _SERIAL_KEYS
        if unknown:
            problems.append(f"unknown keys for a serial contract: {sorted(unknown, key=str)}")
        patterns = []
        for field in ("expect", "forbid"):
            pats = contract.get(field, [])
            if not isinstance(pats, list) or any(not isinstance(p, str) for p in pats):
                problems.append(f"'{field}' must be a list of regex strings")
                continue
            patterns += pats
        if not patterns:
            problems.append("a serial contract needs at least one expect or forbid pattern")
        for pat in patterns:
            if isinstance(pat, str):
                try:
                    re.compile(pat)
                except Exception as e:
                    problems.append(f"bad regex {pat!r}: {e}")
    elif kind == "events":
        _validate_events(contract, problems)
    else:
        unknown = set(contract) - _TIMING_KEYS
        if unknown:
            problems.append(f"unknown keys for a timing contract: {sorted(unknown, key=str)}")
        unit = contract.get("unit", "ns")
        if unit != "ns":
            problems.append(f"unit must be 'ns', got {unit!r}")
        headroom = _num(contract.get("headroom_pct"), "headroom_pct", problems)
        if headroom is not None and headroom > 100:
            problems.append(f"headroom_pct must be <= 100, got {headroom!r}")
        edges = contract.get("edges")
        if not isinstance(edges, list) or not edges:
            problems.append("'edges' must be a non-empty list")
            edges = []
        seen = set()
        for i, edge in enumerate(edges):
            if not isinstance(edge, dict):
                problems.append(f"edges[{i}] must be a mapping")
                continue
            unknown = set(edge) - _EDGE_KEYS
            if unknown:
                problems.append(f"edges[{i}] has unknown keys: {sorted(unknown, key=str)}")
            ename = edge.get("name")
            if not isinstance(ename, str) or not ename.strip():
                problems.append(f"edges[{i}].name must be a non-empty string")
            elif ename in seen:
                problems.append(f"duplicate edge name: {ename!r}")
            else:
                seen.add(ename)
            lo = _num(edge.get("min"), f"edges[{ename or i}].min", problems)
            typ = _num(edge.get("typ"), f"edges[{ename or i}].typ", problems)
            hi = edge.get("max")
            if hi is not None:
                hi = _num(hi, f"edges[{ename or i}].max", problems)
            if None not in (lo, typ) and lo > typ:
                problems.append(f"edges[{ename or i}]: min {lo} > typ {typ}")
            if None not in (typ, hi) and typ > hi:
                problems.append(f"edges[{ename or i}]: typ {typ} > max {hi}")
            if not isinstance(edge.get("required", True), bool):
                problems.append(f"edges[{ename or i}].required must be true or false")
            if "measure" in edge and edge["measure"] not in MEASURES:
                problems.append(f"edges[{ename or i}].measure must be one of {', '.join(MEASURES)}, "
                                f"got {edge['measure']!r}")
        names = {e.get("name") for e in edges if isinstance(e, dict)}
        for edge in edges:
            if not isinstance(edge, dict) or "after" not in edge:
                continue
            if edge["after"] not in names or edge["after"] == edge.get("name"):
                problems.append(f"edges[{edge.get('name')}].after must name another edge, "
                                f"got {edge['after']!r}")
            elif edge.get("measure") == "period":
                problems.append(f"edges[{edge.get('name')}]: a period edge cannot have after")

    if problems:
        raise ContractError("invalid contract:\n  - " + "\n  - ".join(problems))
    return contract


def validate_judge_contract(contract, expected_kind):
    """Validate judge input, retaining metadata-free inline Python contracts.

    YAML loading still requires a name and defaults to timing. Inline callers
    may omit the name/kind, but must supply an unambiguous valid rule shape.
    Never mutate the caller's mapping or reinterpret an explicit kind.
    """
    if not isinstance(contract, dict):
        return validate_contract(contract)  # raises ContractError
    candidate = dict(contract)
    if "kind" not in candidate:
        kinds = (["timing"] if "edges" in candidate else [])
        kinds += ["serial"] if "expect" in candidate or "forbid" in candidate else []
        kinds += ["events"] if "assertions" in candidate else []
        if len(kinds) > 1:
            raise ContractError("invalid contract: mixed timing, serial or events rules")
        candidate["kind"] = kinds[0] if kinds else "timing"
    candidate.setdefault("contract", "<inline>")
    validate_contract(candidate)
    if candidate["kind"] != expected_kind:
        article = "an" if expected_kind == "events" else "a"
        raise ContractError(f"expected {article} {expected_kind} contract, got {candidate['kind']!r}")
    return contract


def validate_serial_log(log):
    """Reject non-text or oversized logs before matching or reporting evidence."""
    if not isinstance(log, str):
        raise ValueError("serial log must be text")
    if len(log) > SERIAL_CAP:
        raise ValueError(f"serial log exceeds limit of {SERIAL_CAP} characters; "
                         "capture rejected (no partial judgment)")
    return log


@lru_cache(maxsize=64)
def _load(path, _mtime):
    with open(path) as f:
        try:
            doc = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ContractError(f"invalid contract YAML: {e}") from e
    return validate_contract(doc)


def load_contract(path):
    """Parse and validate a contract YAML, cached until the file's mtime changes."""
    return _load(path, os.path.getmtime(path))


def sha256_of(data):
    """Content hash of bytes or any JSON-able object (canonical, sort_keys)."""
    if not isinstance(data, (bytes, bytearray)):
        data = json.dumps(data, sort_keys=True).encode()
    return hashlib.sha256(data).hexdigest()


def evidence(contract_path, **extra):
    """The reproducibility block attached to every verdict: what was judged,
    against what, with which tool. Content-addressed so a green build in CI
    can be traced back to the exact contract and capture bytes."""
    ev = {"hwcontract_version": __version__,
          "contract_sha256": sha256_of(open(contract_path, "rb").read()),
          "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    ev.update(extra)
    return ev


def verdict(contract_path, results, ok, input_kind, input_sha256, **params):
    """The canonical result every surface returns or prints: verdict, rows,
    and the hashes that make a green build reproducible."""
    ev = evidence(contract_path)
    return {"verdict": "PASS" if ok else "FAIL", "ok": ok, "results": results,
            "contract_sha256": ev["contract_sha256"], "input_kind": input_kind,
            "input_sha256": input_sha256, "hwcontract_version": __version__,
            "timestamp_utc": ev["timestamp_utc"], **params}


def render_verdict(v, table):
    return (f"verdict: {v['verdict']}  hwcontract {v['hwcontract_version']}  "
            f"sha256: contract {v['contract_sha256'][:12]}, "
            f"input {v['input_sha256'][:12]}\n{table}")


def _resolution(obs):
    """Sample period of the capture behind an observation, 0 when unknown.
    A width measured from samples is only known to +/- one sample."""
    r = obs.get("resolution_ns", 0)
    if isinstance(r, bool) or not isinstance(r, (int, float)) or not math.isfinite(r) or r < 0:
        raise ValueError(f"resolution_ns must be a finite number >= 0, got {r!r}")
    return r


def _outside(w, lo, hi, slack=0):
    """True when w is outside [lo, hi] by more than slack. A non-finite width
    is always outside: NaN compares false against everything."""
    return not math.isfinite(w) or w < lo - slack or (hi is not None and w > hi + slack)


def out_of_window(edge, width, resolution=0):
    """True when one pulse is outside the edge's window by more than resolution."""
    return _outside(width, edge["min"], edge.get("max"), resolution)


def _violations(edge, obs):
    """(count, pct, unresolved) for raw pulses, or (None, None, 0) when the
    observation carries no raw widths (summary-only: median-only judging).
    count/pct are pulses definitely outside the window; unresolved are outside
    by no more than one sample, so the capture cannot tell which side they are on."""
    widths = obs.get("widths")
    if not widths:
        return None, None, 0
    lo, hi, r = edge["min"], edge.get("max"), _resolution(obs)
    bad = sum(1 for w in widths if _outside(w, lo, hi, r))
    unresolved = sum(1 for w in widths if _outside(w, lo, hi)) - bad
    return bad, round(100.0 * bad / len(widths), 2), unresolved


def _span(widths):
    """': 1792-3208ns' for the given widths, so the worst pulse shows without
    the raw list. '' when there are none."""
    if not widths:
        return ""
    a, b = round(min(widths)), round(max(widths))
    return f": {a}ns" if a == b else f": {a}-{b}ns"


def _reach(widths, lo, hi):
    """', up to 2x max' or ', down to 0.5x min': how far past the limit the
    worst pulse is, so a pulse twice too long does not read as a near miss."""
    out = ""
    if widths and hi and max(widths) > hi:
        out += f", up to {max(widths) / hi:.3g}x max"
    if widths and lo > 0 and min(widths) < lo:
        out += f", down to {min(widths) / lo:.3g}x min"
    return out


def judge(edge, obs, headroom_pct):
    """(status, hint) for one edge given its observation (or None).

    An observation with raw widths is judged on EVERY pulse, not just the
    median: a glitchy tail escalates a clean-looking p50. Up to
    VIOLATION_FAIL_PCT percent of violating pulses keeps the edge MARGINAL;
    beyond that it FAILs outright.

    An observation with resolution_ns (the capture's sample period) is judged
    to +/- one sample: FAIL needs the value outside the window by more than one
    sample, PASS needs it inside by at least one. Anything closer is MARGINAL,
    because the capture cannot tell which side of the limit it is on."""
    if obs is None:
        if edge.get("required", True) is False:
            return PASS, "not in this capture (not required)"
        return MISSING, "no observation for this edge"

    v = obs.get("p50", obs.get("value"))
    lo, hi, typ = edge["min"], edge.get("max"), edge["typ"]
    r = _resolution(obs)
    one_sample = f"one sample ({round(r, 1):g}ns)"
    violations, pct, unresolved = _violations(edge, obs)
    n = len(obs["widths"]) if violations is not None else 0
    bad = [w for w in obs["widths"] if math.isfinite(w) and _outside(w, lo, hi, r)] if n else []
    uhint = f"; {unresolved} of {n} pulses within {one_sample} of the window" if unresolved else ""
    vhint = (f"; {violations} of {n} pulses out of window ({pct}%){_span(bad)}{_reach(bad, lo, hi)}"
             if violations
             else "") + uhint

    if not math.isfinite(v):
        return FAIL, "non-finite measurement (NaN/inf) in the capture" + vhint

    if _outside(v, lo, hi, r):
        inside = n - violations - unresolved if n else 0
        if inside:          # two populations: the p50 describes neither of them
            side = ("below min" if bad and max(bad) < lo else
                    "above max" if bad and hi is not None and min(bad) > hi else "out of window")
            return FAIL, (f"{violations} of {n} pulses {side}{_span(bad)}{_reach(bad, lo, hi)}; "
                          f"{inside} of {n} in window, so the p50 mixes both") + uhint
        short = typ - v
        near, past = ("min", lo - v) if v < lo else ("max", v - hi)
        limit = f", {round(past, 1):g}ns past {near}" if typ not in (lo, hi) else ""
        return FAIL, f"{abs(short)}ns {'short' if short > 0 else 'long'} (typ {typ}){limit}" + vhint

    # compare unrounded: pct is rounded for display and 1.001% must still fail
    burst = bool(violations) and 100 * violations > VIOLATION_FAIL_PCT * len(obs["widths"])

    if _outside(v, lo, hi):                             # out, but by no more than one sample
        near, past = ("min", lo - v) if v < lo else ("max", v - hi)
        if burst:
            return FAIL, (f"p50 {round(past, 1):g}ns past {near} and more than "
                          f"{VIOLATION_FAIL_PCT:g}% of pulses out of window") + vhint
        return MARGINAL, (f"{round(past, 1):g}ns past {near}, within {one_sample}; "
                          "capture at a higher samplerate to resolve") + vhint

    if burst:
        return FAIL, f"p50 in spec, but more than {VIOLATION_FAIL_PCT:g}% of pulses out of window" + vhint

    room = v - lo if hi is None else min(v - lo, hi - v)
    near = "min" if hi is None or v - lo < hi - v else "max"
    if room < r:
        return MARGINAL, (f"within {one_sample} of {near}; "
                          "capture at a higher samplerate to resolve") + vhint
    if hi is not None and room < headroom_pct / 100 * (hi - lo):   # flag rail-hugging (marginal)
        return MARGINAL, f"only {room}ns from {near}; nudge toward typ {typ}" + vhint

    if violations:
        return MARGINAL, (f"p50 in spec, but pulses out of window "
                          f"(FAIL above {VIOLATION_FAIL_PCT:g}%)") + vhint

    if unresolved:
        return MARGINAL, ("p50 in spec but some pulses are too close to the window to resolve; "
                          "capture at a higher samplerate") + vhint

    return PASS, ""


_DIST_KEYS = ("count", "min", "max", "p5", "p95", "jitter", "resolution_ns")


def _real(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _observations_by_name(observations):
    """{name: observation}, or ValueError listing every problem. A duplicated
    name is rejected: a later entry must not silently replace a failing one."""
    if not isinstance(observations, list):
        raise ValueError(f"observations must be a list, got {type(observations).__name__}")
    problems, by_name = [], {}
    for i, o in enumerate(observations):
        if not isinstance(o, dict):
            problems.append(f"observations[{i}] must be a mapping")
            continue
        name = o.get("name")
        if not isinstance(name, str) or not name:
            problems.append(f"observations[{i}].name must be a non-empty string")
            continue
        if name in by_name:
            problems.append(f"duplicate observation name: {name!r}")
            continue
        key = "p50" if "p50" in o else "value"
        if not _real(o.get(key)):
            problems.append(f"observations[{name}].{key} must be a number, got {o.get(key)!r}")
        widths = o.get("widths")
        if widths is not None and (not isinstance(widths, (list, tuple))
                                   or not all(_real(w) for w in widths)):
            problems.append(f"observations[{name}].widths must be a list of numbers")
        by_name[name] = o
    if problems:
        raise ValueError("invalid observations:\n  - " + "\n  - ".join(problems))
    return by_name


def run(contract, observations):
    """Return (results, ok). ok is False on FAIL, MISSING, and MARGINAL: marginal is a fail."""
    validate_judge_contract(contract, "timing")
    by_name = _observations_by_name(observations)
    results, ok = [], True
    for edge in contract["edges"]:
        obs = by_name.get(edge["name"])
        status, hint = judge(edge, obs, contract["headroom_pct"])
        if status != PASS:
            ok = False
        row = {"edge": edge["name"], "window": [edge["min"], edge.get("max")], "typ": edge["typ"],
               "actual": obs.get("p50", obs.get("value")) if obs else None,
               "status": status, "hint": hint}
        if obs:
            for k in _DIST_KEYS:                        # distribution summary rides along
                if k in obs:
                    row[k] = obs[k]
            violations, pct, unresolved = _violations(edge, obs)
            if violations is not None:
                row["violations"], row["violation_pct"] = violations, pct
                row["unresolved"] = unresolved
        results.append(row)
    return results, ok


def run_serial(contract, log):
    """Check a captured serial log against expect/forbid patterns. Same (results, ok) shape."""
    validate_judge_contract(contract, "serial")
    validate_serial_log(log)
    results, ok = [], True

    def search(pat):
        try:
            return re.search(pat, log), None
        except Exception as e:                       # invalid regex in a contract -> report, don't crash
            return None, f"{type(e).__name__}: {e}"

    for pat in contract.get("expect", []):
        hit, err = search(pat)
        good = hit and not err
        ok = ok and bool(good)
        results.append({"edge": pat, "typ": "expect", "actual": "seen" if good else "absent",
                        "status": PASS if good else FAIL,
                        "hint": err or ("" if good else "expected pattern not in capture")})
    for pat in contract.get("forbid", []):
        hit, err = search(pat)
        bad = bool(hit) or bool(err)                 # a broken forbid-rule fails closed
        ok = ok and not bad
        results.append({"edge": pat, "typ": "forbid",
                        "actual": hit.group(0) if hit else "absent",
                        "status": FAIL if bad else PASS,
                        "hint": err or (f"forbidden match: {hit.group(0)}" if hit else "")})
    return results, ok


def _window(r):
    if "window" not in r:
        return "-"
    lo, hi = r["window"]
    return f">={lo}" if hi is None else f"{lo}-{hi}"


def render(results):
    """The table. Timing rows show their window, so the limits a pulse missed
    are on screen next to the median; serial rows have none."""
    w = max([7] + [len(str(r["edge"])) for r in results])    # 'unmatched period' is 16
    ww = max([len("window")] + [len(_window(r)) for r in results]) if any(
        "window" in r for r in results) else 0
    win = (lambda s: f" {s:>{ww}}") if ww else (lambda s: "")
    lines = [f"{'edge':<{w}}{win('window')} {'typ':>7} {'actual':>8}  {'status':<9} hint",
             "-" * (w + 53 + (ww + 1 if ww else 0))]
    for r in results:
        lines.append(f"{r['edge']:<{w}}{win(_window(r))} {r['typ']:>7} "
                     f"{str(r['actual'] if r['actual'] is not None else '-'):>8}"
                     f"  {r['status']:<9} {r['hint']}")
    return "\n".join(lines)


def demo():
    contract = {"headroom_pct": 20, "edges": [
        {"name": "T0H", "typ": 350, "min": 200, "max": 500},
        {"name": "T1H", "typ": 700, "min": 550, "max": 850},
        {"name": "RESET", "typ": 50000, "min": 50000, "max": None},
    ]}
    obs = [{"name": "T0H", "value": 340},   # pass
           {"name": "T1H", "value": 560}]   # marginal; RESET omitted -> MISSING
    results, ok = run(contract, obs)
    print(render(results))
    st = {r["edge"]: r["status"] for r in results}
    assert st["T0H"] == PASS
    assert st["T1H"] == MARGINAL
    assert st["RESET"] == MISSING
    assert ok is False
    print("\nself-check OK")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
        sys.exit(0)
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    from hwcontract.contracts import cli_contract       # contracts imports judge
    try:
        path = cli_contract(sys.argv[1])
        results, ok = run(load_contract(path), json.load(open(sys.argv[2])))
    except (OSError, ValueError) as e:            # ContractError and bad JSON are ValueErrors
        sys.exit(f"hwcontract: {e}")
    v = verdict(path, results, ok, input_kind="observations",
                input_sha256=sha256_of(open(sys.argv[2], "rb").read()))
    print(render_verdict(v, render(results)))
    sys.exit(0 if ok else 1)
