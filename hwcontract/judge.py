#!/usr/bin/env python3
"""The judge: join a hardware contract against observations -> pass/marginal/fail.

Pure logic, no hardware, no framework. Imported by server.py (MCP) and runnable
standalone:  python judge.py ws2812.contract.yaml observations.json
              python judge.py --demo

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

# A serial capture longer than this is truncated before matching: bounds memory
# and the ReDoS blast radius under stdlib re.
SERIAL_CAP = 1_000_000

# An observation carrying raw pulse widths ("widths") is judged on every pulse,
# not just the median. Up to this fraction of violating pulses keeps the edge
# MARGINAL (p50 in spec, tails out); beyond it the edge FAILs outright.
VIOLATION_FAIL_PCT = 1.0

_TIMING_KEYS = {"contract", "kind", "unit", "headroom_pct", "edges"}
_SERIAL_KEYS = {"contract", "kind", "expect", "forbid"}
_EVENTS_KEYS = {"contract", "kind", "assertions"}
_EDGE_KEYS = {"name", "min", "typ", "max"}
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
    if not math.isfinite(value):
        problems.append(f"{what} must be finite, got {value!r}")
        return None
    if value < 0:
        problems.append(f"{what} must be >= 0, got {value!r}")
        return None
    return value


def _try(fn, value, problems, what):
    try:
        return fn(value)
    except ValueError as e:
        problems.append(f"{what}: {e}")
        return None


def _validate_events(contract, problems):
    """Validate an events contract: selectors parse, durations parse, and each
    assertion picks a coherent shape (require+within, or forbid+while/before)."""
    unknown = set(contract) - _EVENTS_KEYS
    if unknown:
        problems.append(f"unknown keys for an events contract: {sorted(unknown)}")
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
            problems.append(f"{label} has unknown keys: {sorted(unknown)}")
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
            problems.append(f"unknown keys for a serial contract: {sorted(unknown)}")
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
            problems.append(f"unknown keys for a timing contract: {sorted(unknown)}")
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
                problems.append(f"edges[{i}] has unknown keys: {sorted(unknown)}")
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

    if problems:
        raise ContractError("invalid contract:\n  - " + "\n  - ".join(problems))
    return contract


@lru_cache(maxsize=64)
def _load(path, _mtime):
    with open(path) as f:
        doc = yaml.safe_load(f)
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
    return (f"verdict: {v['verdict']}  contract {v['contract_sha256'][:12]}  "
            f"{v['input_kind']} {v['input_sha256'][:12]}  "
            f"hwcontract {v['hwcontract_version']}\n{table}")


def _violations(edge, obs):
    """(count, pct) of raw pulses outside the window, or (None, None) when the
    observation carries no raw widths (summary-only: median-only judging)."""
    widths = obs.get("widths")
    if not widths:
        return None, None
    lo, hi = edge["min"], edge["max"]
    bad = sum(1 for w in widths if w < lo or (hi is not None and w > hi))
    return bad, round(100.0 * bad / len(widths), 2)


def judge(edge, obs, headroom_pct):
    """(status, hint) for one edge given its observation (or None).

    An observation with raw widths is judged on EVERY pulse, not just the
    median: a glitchy tail escalates a clean-looking p50. Up to
    VIOLATION_FAIL_PCT percent of violating pulses keeps the edge MARGINAL;
    beyond that it FAILs outright."""
    if obs is None:
        return MISSING, "no observation for this edge"

    v = obs.get("p50", obs.get("value"))
    lo, hi, typ = edge["min"], edge["max"], edge["typ"]
    violations, pct = _violations(edge, obs)
    vhint = ""
    if violations:
        vhint = f"; {violations} of {len(obs['widths'])} pulses out of window ({pct}%)"

    if not math.isfinite(v):
        return FAIL, "non-finite measurement (NaN/inf) in the capture" + vhint

    if v < lo or (hi is not None and v > hi):
        short = typ - v
        return FAIL, f"{abs(short)}ns {'short' if short > 0 else 'long'} (typ {typ})" + vhint

    if violations and pct > VIOLATION_FAIL_PCT:
        return FAIL, "p50 in spec but the capture violates the window" + vhint

    if hi is not None:                                  # flag rail-hugging (marginal)
        threshold = headroom_pct / 100 * (hi - lo)
        if min(v - lo, hi - v) < threshold:
            near = "min" if v - lo < hi - v else "max"
            return MARGINAL, f"only {min(v - lo, hi - v)}ns from {near}; nudge toward typ {typ}" + vhint

    if violations:
        return MARGINAL, "p50 in spec but individual pulses violate the window" + vhint

    return PASS, ""


_DIST_KEYS = ("count", "min", "max", "p5", "p95", "jitter")


def run(contract, observations):
    """Return (results, ok). ok is False on FAIL, MISSING, and MARGINAL: marginal is a fail."""
    by_name = {o["name"]: o for o in observations}
    results, ok = [], True
    for edge in contract["edges"]:
        obs = by_name.get(edge["name"])
        status, hint = judge(edge, obs, contract["headroom_pct"])
        if status != PASS:
            ok = False
        row = {"edge": edge["name"], "typ": edge["typ"],
               "actual": obs.get("p50", obs.get("value")) if obs else None,
               "status": status, "hint": hint}
        if obs:
            for k in _DIST_KEYS:                        # distribution summary rides along
                if k in obs:
                    row[k] = obs[k]
            violations, pct = _violations(edge, obs)
            if violations is not None:
                row["violations"], row["violation_pct"] = violations, pct
        results.append(row)
    return results, ok


def run_serial(contract, log):
    """Check a captured serial log against expect/forbid patterns. Same (results, ok) shape."""
    log = log[:SERIAL_CAP]
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


def render(results):
    lines = [f"{'edge':<7} {'typ':>7} {'actual':>8}  {'status':<9} hint", "-" * 60]
    for r in results:
        lines.append(f"{r['edge']:<7} {r['typ']:>7} {str(r['actual'] if r['actual'] is not None else '-'):>8}"
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
    results, ok = run(load_contract(sys.argv[1]), json.load(open(sys.argv[2])))
    v = verdict(sys.argv[1], results, ok, input_kind="observations",
                input_sha256=sha256_of(open(sys.argv[2], "rb").read()))
    print(render_verdict(v, render(results)))
    sys.exit(0 if ok else 1)
