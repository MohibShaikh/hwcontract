#!/usr/bin/env python3
"""Demo: judge serial boot logs against a contract. No hardware.

Two logs, one contract (boot.contract.yaml):

  - a clean boot      -> PASS
  - a panicking board -> FAIL, the forbidden match is named

expect patterns must all be seen, forbid patterns must all be absent.
That is the whole contract.

Run after installation: python -m hwcontract.demo serial
Synthetic logs. No hardware, no network.
"""
import json

from hwcontract.contracts import EXAMPLES
from hwcontract.judge import evidence, load_contract, run_serial, render, sha256_of

CONTRACT = str(EXAMPLES / "boot.contract.yaml")

LOGS = [
    ("clean boot", "boot v3\nsensor warmup...\nIMU init OK\nready\n"),
    ("panicking board", "rst:0x3 (SW_RESET)\nGuru Meditation Error: Core 0 panic\n"),
]


def main():
    contract = load_contract(CONTRACT)
    outcomes = []
    for name, log in LOGS:
        results, ok = run_serial(contract, log)
        print(f"\n=== {name} -> {'PASS' if ok else 'FAIL'} ===")
        outcomes.append(ok)
        print(render(results))
        print("evidence: " + json.dumps(evidence(CONTRACT, log_sha256=sha256_of(log),
                                                input_origin="synthetic", log_chars=len(log))))
    print("\nexpect must all be seen, forbid must all be absent. Reset the board")
    print("inside the capture window: a boot banner prints once.")
    return 0 if outcomes == [True, False] else 1


if __name__ == "__main__":
    raise SystemExit(main())
