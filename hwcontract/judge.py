#!/usr/bin/env python3
"""The judge: join a hardware contract against observations -> pass/marginal/fail.

Pure logic, no hardware, no framework. Imported by server.py (MCP) and runnable
standalone:  python judge.py ws2812.contract.yaml observations.json
             python judge.py --demo
"""
import json
import os
import sys
from functools import lru_cache

import yaml

try:                       # prefer google-re2 (pip install google-re2): linear-time, ReDoS-immune
    import re2 as re
except ImportError:
    import re              # stdlib fallback; the input cap in run_serial bounds ReDoS


@lru_cache(maxsize=64)
def _load(path, _mtime):
    return yaml.safe_load(open(path))


def load_contract(path):
    """Parse a contract YAML, cached until the file's mtime changes."""
    return _load(path, os.path.getmtime(path))


def judge(edge, obs, headroom_pct):
    """(status, hint) for one edge given its observation (or None)."""
    if obs is None:
        return "MISSING", "no observation for this edge"

    v, lo, hi, typ = obs["value"], edge["min"], edge["max"], edge["typ"]

    if v < lo or (hi is not None and v > hi):
        short = typ - v
        return "FAIL", f"{abs(short)}ns {'short' if short > 0 else 'long'} (typ {typ})"

    if hi is not None:                                  # flag rail-hugging (marginal)
        threshold = headroom_pct / 100 * (hi - lo)
        if min(v - lo, hi - v) < threshold:
            near = "min" if v - lo < hi - v else "max"
            return "marginal", f"only {min(v - lo, hi - v)}ns from {near}; nudge toward typ {typ}"

    return "pass", ""


def run(contract, observations):
    """Return (results, ok). results = list of dicts; ok False if any FAIL/MISSING."""
    by_name = {o["name"]: o for o in observations}
    results, ok = [], True
    for edge in contract["edges"]:
        obs = by_name.get(edge["name"])
        status, hint = judge(edge, obs, contract["headroom_pct"])
        if status in ("FAIL", "MISSING"):
            ok = False
        results.append({"edge": edge["name"], "typ": edge["typ"],
                        "actual": obs["value"] if obs else None,
                        "status": status, "hint": hint})
    return results, ok


def run_serial(contract, log):
    """Check a captured serial log against expect/forbid patterns. Same (results, ok) shape."""
    # Bound the regex input (memory + ReDoS blast radius under stdlib re). With
    # google-re2 installed, matching is linear-time and contracts can be untrusted.
    log = log[:1_000_000]
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
                        "status": "pass" if good else "FAIL",
                        "hint": err or ("" if good else "expected pattern not in capture")})
    for pat in contract.get("forbid", []):
        hit, err = search(pat)
        bad = bool(hit) or bool(err)                 # a broken forbid-rule fails closed
        ok = ok and not bad
        results.append({"edge": pat, "typ": "forbid",
                        "actual": hit.group(0) if hit else "absent",
                        "status": "FAIL" if bad else "pass",
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
    assert st["T0H"] == "pass"
    assert st["T1H"] == "marginal"
    assert st["RESET"] == "MISSING"
    assert ok is False
    print("\nself-check OK")


if __name__ == "__main__":
    if "--demo" in sys.argv:
        demo()
        sys.exit(0)
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    results, ok = run(load_contract(sys.argv[1]), json.load(open(sys.argv[2])))
    print(render(results))
    sys.exit(0 if ok else 1)
