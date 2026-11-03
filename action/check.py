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

from hwcontract.check import check_file, report
from hwcontract.contracts import cli_contract
from hwcontract.sigrok_adapter import positive_finite, sample_period


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


def run_case(kind, contract, path, samplerate_hz):
    """(name, verdict-dict, table) for one file judged against one contract.
    The judging itself is hwcontract.check, shared with the CLI and MCP."""
    v, table, notes = check_file(cli_contract(contract), path, samplerate_hz, kind=kind)
    name = f"{kind}:{contract}:{path}"
    print(f"== {name} -> {v['verdict']} ==\n{report(v, table, notes)}")
    if not v["ok"]:
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
                       **({"samplerate_hz": v["samplerate_hz"]} if "samplerate_hz" in v else {}),
                       "hwcontract_version": v["hwcontract_version"],
                       "timestamp_utc": v["timestamp_utc"]} for name, v, _ in cases]}
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    print(f"evidence -> {path}")


def main(argv=None):
    try:
        samplerate_hz = positive_finite(os.environ.get("INPUT_SAMPLERATE", "24000000"), "samplerate")
        sample_period(samplerate_hz)
    except ValueError as e:
        raise SystemExit(f"hwcontract-action: {e}") from e
    if samplerate_hz.is_integer():
        samplerate_hz = int(samplerate_hz)
    kinds = (("timing", os.environ.get("INPUT_TIMING")),
             ("serial", os.environ.get("INPUT_SERIAL")),
             ("events", os.environ.get("INPUT_EVENTS")))
    cases = []
    for kind, spec in kinds:
        for contract, files in parse_pairs(spec, kind):
            for path in files:
                try:
                    cases.append(run_case(kind, contract, path, samplerate_hz))
                except (OSError, ValueError) as e:      # unknown contract, unreadable file
                    raise SystemExit(f"hwcontract-action: {kind}:{contract}:{path}: {e}") from e

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
