#!/usr/bin/env python3
"""CI runner for the hwcontract GitHub Action (action.yml).

Reads INPUT_* env vars set by the composite action, judges every matched
capture/log, prints verdict tables, emits ::error annotations for failures
and optionally a JUnit XML report. Exit 1 if any check fails.
"""
import glob
import os
import sys
import xml.etree.ElementTree as ET

import hwcontract
from hwcontract.judge import load_contract, run, run_serial, render, evidence
from hwcontract.sigrok_adapter import observe, read_csv

BUNDLED = os.path.join(os.path.dirname(hwcontract.__file__), "examples")


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
    rows = table.splitlines()[2:]
    first = next((l for l in rows if "FAIL" in l or "MARGINAL" in l), "")
    print(f"::error title=hwcontract {name}::{(first or 'verdict FAIL').strip()}")


def junit_write(path, cases):
    ts = ET.Element("testsuite", {"name": "hwcontract", "tests": str(len(cases)),
                                  "failures": str(sum(1 for _, ok, _ in cases if not ok))})
    for name, ok, msg in cases:
        tc = ET.SubElement(ts, "testcase", {"name": name, "classname": "hwcontract"})
        if not ok:
            f = ET.SubElement(tc, "failure", {"message": "hwcontract verdict FAIL"})
            f.text = msg
    ET.ElementTree(ts).write(path, encoding="utf-8", xml_declaration=True)


def main(argv=None):
    samplerate = os.environ.get("INPUT_SAMPLERATE", "24000000")
    dt = 1e9 / int(samplerate)
    cases, nfail = [], 0

    for contract, files in parse_pairs(os.environ.get("INPUT_TIMING"), "timing"):
        path = resolve(contract)
        c = load_contract(path)
        for f in files:
            name = f"timing:{contract}:{f}"
            results, ok = run(c, observe(read_csv(f), dt))
            table = render(results)
            print(f"== {name} -> {'PASS' if ok else 'FAIL'} ==\n{table}")
            cases.append((name, ok, table))
            if not ok:
                nfail += 1
                annotate(name, table)

    for contract, files in parse_pairs(os.environ.get("INPUT_SERIAL"), "serial"):
        path = resolve(contract)
        c = load_contract(path)
        for f in files:
            name = f"serial:{contract}:{f}"
            with open(f, errors="replace") as fh:
                log = fh.read()
            results, ok = run_serial(c, log)
            table = render(results)
            print(f"== {name} -> {'PASS' if ok else 'FAIL'} ==\n{table}")
            cases.append((name, ok, table))
            if not ok:
                nfail += 1
                annotate(name, table)

    junit_path = os.environ.get("INPUT_JUNIT", "")
    if junit_path:
        junit_write(junit_path, cases)
        print(f"junit report -> {junit_path}")

    if not cases:
        raise SystemExit("hwcontract-action: nothing to check (set the timing and/or serial inputs)")

    if nfail:
        print(f"hwcontract: {nfail} of {len(cases)} checks FAIL")
        return 1
    print(f"hwcontract: all {len(cases)} checks PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
