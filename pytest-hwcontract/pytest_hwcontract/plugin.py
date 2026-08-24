"""The hwcontract fixture: a failing hardware check is a failing test.

    def test_strip(hwcontract):
        obs = hwcontract.capture_csv("strip.csv", samplerate=24_000_000)
        hwcontract.timing("ws2812b", obs)          # bundled contract by name
        hwcontract.serial("boot", open("boot.log").read())

A FAIL, MARGINAL or MISSING edge raises AssertionError with the rendered
verdict table, so JUnit and CI show exactly which edge failed and why.
Contracts resolve as: an existing path, then --hwcontract-root, then the
contracts bundled with the hwcontract package.
"""
import os

import pytest

import hwcontract
from hwcontract.judge import load_contract, run, run_serial, render, evidence

BUNDLED = os.path.join(os.path.dirname(hwcontract.__file__), "examples")


def pytest_addoption(parser):
    group = parser.getgroup("hwcontract", "hardware contract judgments")
    group.addoption("--hwcontract-root", action="store", default=None,
                    help="Directory to resolve contract names against (default: cwd)")


class ContractNotFound(Exception):
    pass


class HwContract:
    def __init__(self, root):
        self._root = root

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

    def _fail(self, path, results):
        ev = evidence(path)
        raise AssertionError(
            f"hwcontract verdict FAIL ({ev['contract_sha256'][:12]})\n{render(results)}")

    def timing(self, contract, observations):
        """Judge pulse-width observations against a timing contract."""
        path = self.resolve(contract)
        results, ok = run(load_contract(path), observations)
        if not ok:
            self._fail(path, results)
        return results

    def serial(self, contract, log):
        """Judge a serial log against expect/forbid patterns."""
        path = self.resolve(contract)
        results, ok = run_serial(load_contract(path), log)
        if not ok:
            self._fail(path, results)
        return results

    def capture_csv(self, path, samplerate, **splits):
        """Turn a 0/1-per-line logic capture into timing observations."""
        from hwcontract.sigrok_adapter import observe, read_csv
        return observe(read_csv(path), 1e9 / int(samplerate), **splits)


@pytest.fixture
def hwcontract(request):
    return HwContract(request.config.getoption("--hwcontract-root") or os.getcwd())
