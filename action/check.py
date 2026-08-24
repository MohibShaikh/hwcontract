#!/usr/bin/env python3
"""CI runner for the hwcontract GitHub Action (action.yml).

Reads INPUT_* env vars set by the composite action, judges every matched
capture/log/trace, prints verdict tables, emits ::error annotations for
failures, writes optional JUnit plus an evidence JSON (contract and input
hashes per check). Exit 1 if any check fails.
"""
import glob
import json
import os
import sys
import xml.etree.ElementTree as ET

from hwcontract.judge import (load_contract, run, run_serial, render,
                              render_verdict, sha256_of, verdict)
from hwcontract.jsontrace import from_traceevents
from hwcontract.sigrok_adapter import observe, read_csv
from hwcontract.temporal import judge_events, normalize_events, render_events

BUNDLED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "hwcontract", "examples")


def resolve(name):
    """A path that exists wins, else a bundled example by name."""
    if os.path.isfile(name):
        return name
    base = name if name.endswith(".yaml") else f"{name}.contract.yaml"
    bundled = os.path.join(BUNDLED, base)
    if os.path.isfile(bundled):
        return bundled
    raise SystemExit(f"hwcontract-action: no contract file for {name!r} "
                     "(tried path, bundled examples)")


def parse_pairs(spec, what):
    """'ws2812b=caps/*.csv, boot=logs/*.log' -> [(contract, [files...]), ...]"""
    pairs = []
    for chunk in (spec or "").replace(",", "\n").split("\n"):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "=" not in chunk:
            raise SystemExit(f"hwcontract-action: {what} entry {chunk!r} is not contract=glob")
        contract, pattern = chunk.split("=", 1)
        files = sorted(glob.glob(pattern.strip()))
        if not files:
            raise SystemExit(f"hwcontract-action: no files match {pattern.strip()!r}")
        pairs.append((contract.strip(), files))
    return pairs


def annotate(name, table):
    first = next((l for l in table.splitlines()[2:] if "FAIL" in l or "MARGINAL" in l), "")
    print(f"::error title=hwcontract {name}::{(first or 'verdict FAIL').strip()}")


def load_events(path):
    with open(path) as f:
        doc = json.load(f)
    if isinstance(doc, dict) and "traceEvents" in doc:
        return from_traceevents(doc)
    return normalize_events(doc)


def run_case(kind, contract, path, dt_ns):
    """(name, verdict-dict, table) for one file judged against one contract."""
    cpath = resolve(contract)
    c = load_contract(cpath)
    if kind == "timing":
        results, ok = run(c, observe(read_csv(path), dt_ns))
        table = render(results)
    elif kind == "serial":
        with open(path, errors="replace") as fh:
            results, ok = run_serial(c, fh.read())
        table = render(results)
    else:
        results, ok = judge_events(c, load_events(path))
        table = render_events(results)
    with open(path, "rb") as fh:
        input_sha = sha256_of(fh.read())
    v = verdict(cpath, results, ok, input_kind=kind, input_sha256=input_sha, file=path)
    name = f"{kind}:{contract}:{path}"
    print(f"== {name} -> {v['verdict']} ==\n{table}")
    if not ok:
        annotate(name, table)
    return name, v, table


def junit_write(path, cases):
    ts = ET.Element("testsuite", {"name": "hwcontract", "tests": str(len(cases)),
                                  "failures": str(sum(1 for _, v, _ in cases if not v["ok"]))})
    for name, v, table in cases:
        tc = ET.SubElement(ts, "testcase", {"name": name, "classname": "hwcontract"})
        props = ET.SubElement(tc, "properties")
        for k in ("contract_sha256", "input_sha256", "input_kind", "hwcontract_version"):
            ET.SubElement(props, "property", {"name": f"hwcontract.{k}", "value": str(v[k])})
        if not v["ok"]:
            f = ET.SubElement(tc, "failure", {"message": f"hwcontract verdict {v['verdict']}"})
            f.text = table
    ET.ElementTree(ts).write(path, encoding="utf-8", xml_declaration=True)


def evidence_write(path, cases):
    out = {"checks": [{"name": name, "verdict": v["verdict"],
                       "contract_sha256": v["contract_sha256"],
                       "input_sha256": v["input_sha256"],
                       "input_kind": v["input_kind"],
                       "file": v.get("file"),
                       "hwcontract_version": v["hwcontract_version"],
                       "timestamp_utc": v["timestamp_utc"]} for name, v, _ in cases]}
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"evidence -> {path}")


def main(argv=None):
    dt_ns = 1e9 / int(os.environ.get("INPUT_SAMPLERATE", "24000000"))
    kinds = (("timing", os.environ.get("INPUT_TIMING")),
             ("serial", os.environ.get("INPUT_SERIAL")),
             ("events", os.environ.get("INPUT_EVENTS")))
    cases = []
    for kind, spec in kinds:
        for contract, files in parse_pairs(spec, kind):
            for path in files:
                cases.append(run_case(kind, contract, path, dt_ns))

    junit_path = os.environ.get("INPUT_JUNIT", "")
    if junit_path:
        junit_write(junit_path, cases)
        print(f"junit report -> {junit_path}")
    evidence_path = os.environ.get("INPUT_EVIDENCE", "")
    if evidence_path:
        evidence_write(evidence_path, cases)
    if not cases:
        raise SystemExit("hwcontract-action: nothing to check (set the timing, serial and/or events inputs)")

    nfail = sum(1 for _, v, _ in cases if not v["ok"])
    if nfail:
        print(f"hwcontract: {nfail} of {len(cases)} checks FAIL")
        return 1
    print(f"hwcontract: all {len(cases)} checks PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
