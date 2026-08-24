"""The hwcontract fixture: a failing hardware check is a failing test.

    def test_strip(hwcontract):
        obs = hwcontract.capture_csv("strip.csv", samplerate=24_000_000)
        hwcontract.timing("ws2812b", obs)          # bundled contract by name
        hwcontract.serial("boot", open("boot.log").read())
        hwcontract.events("spi-frame", trace="captures/spi.json")

A FAIL, MARGINAL or MISSING edge raises AssertionError with the rendered
verdict table, so JUnit and CI show exactly which edge failed and why.
Contract and input hashes land in the JUnit properties. Contracts resolve
as: an existing path, then --hwcontract-root, then the contracts bundled
with the hwcontract package.
"""
import json
import os

import pytest

import hwcontract
from hwcontract.judge import (load_contract, run, run_serial, render,
                              render_verdict, sha256_of, verdict)
from hwcontract.temporal import judge_events, normalize_events, render_events

BUNDLED = os.path.join(os.path.dirname(hwcontract.__file__), "examples")


def pytest_addoption(parser):
    group = parser.getgroup("hwcontract", "hardware contract judgments")
    group.addoption("--hwcontract-root", action="store", default=None,
                    help="Directory to resolve contract names against (default: cwd)")


class ContractNotFound(Exception):
    pass


class HwContract:
    def __init__(self, record_property, root):
        self._record = record_property
        self._root = root
        self.evidence = None            # last judgment's evidence block

    def resolve(self, name):
        """An existing path wins; then --hwcontract-root; then bundled examples."""
        if os.path.isfile(name):
            return name
        if self._root and os.path.isfile(os.path.join(self._root, name)):
            return os.path.join(self._root, name)
        base = name if name.endswith(".yaml") else f"{name}.contract.yaml"
        bundled = os.path.join(BUNDLED, base)
        if os.path.isfile(bundled):
            return bundled
        raise ContractNotFound(
            f"hwcontract: no contract file for {name!r} "
            "(tried cwd, --hwcontract-root, bundled examples)")

    def _judge(self, path, results, ok, input_kind, input_sha, table):
        v = verdict(path, results, ok, input_kind=input_kind,
                    input_sha256=input_sha)
        self.evidence = {k: v[k] for k in ("verdict", "contract_sha256",
                                           "input_sha256", "input_kind",
                                           "hwcontract_version", "timestamp_utc")}
        self._record("hwcontract.contract_sha256", v["contract_sha256"])
        self._record("hwcontract.input_sha256", v["input_sha256"])
        self._record("hwcontract.verdict", v["verdict"])
        if not ok:
            raise AssertionError(render_verdict(v, table))
        return results

    def timing(self, contract, observations):
        """Judge pulse-width observations against a timing contract."""
        path = self.resolve(contract)
        results, ok = run(load_contract(path), observations)
        return self._judge(path, results, ok, "observations",
                           sha256_of(observations), render(results))

    def serial(self, contract, log):
        """Judge a serial log against expect/forbid patterns."""
        path = self.resolve(contract)
        results, ok = run_serial(load_contract(path), log)
        return self._judge(path, results, ok, "log", sha256_of(log), render(results))

    def events(self, contract, trace):
        """Judge decoded events against an events contract. `trace` is a
        jsontrace JSON file/path (sigrok-cli --protocol-decoder-jsontrace)
        or an already-loaded event list."""
        path = self.resolve(contract)
        doc = trace
        if isinstance(trace, str):
            with open(trace) as f:
                doc = json.load(f)
        events = (from_traceevents(doc) if isinstance(doc, dict) and "traceEvents" in doc
                  else normalize_events(doc))
        results, ok = judge_events(load_contract(path), events)
        return self._judge(path, results, ok, "events", sha256_of(events),
                           render_events(results))

    def capture_csv(self, path, samplerate, **splits):
        """Turn a 0/1-per-line logic capture into timing observations."""
        from hwcontract.sigrok_adapter import observe, read_csv
        return observe(read_csv(path), 1e9 / int(samplerate), **splits)


def from_traceevents(doc):
    from hwcontract.jsontrace import from_traceevents as _f
    return _f(doc)


@pytest.fixture
def hwcontract(request, record_property):
    return HwContract(record_property,
                      request.config.getoption("--hwcontract-root") or os.getcwd())
