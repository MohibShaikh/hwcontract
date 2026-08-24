#!/usr/bin/env python3
"""Demo: judge serial boot logs against a contract. No hardware.

Two logs, one contract (boot.contract.yaml):

  - a clean boot      -> PASS
  - a panicking board -> FAIL, the forbidden match is named

expect patterns must all be seen, forbid patterns must all be absent.
That is the whole contract.

Run from the repo root:  python3 demo/serial_boot.py
Stdlib only. No hardware, no network.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir))
from hwcontract.judge import load_contract, run_serial, render

CONTRACT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        os.pardir, "hwcontract", "examples", "boot.contract.yaml")

LOGS = [
    ("clean boot", "boot v3\nsensor warmup...\nIMU init OK\nready\n"),
    ("panicking board", "rst:0x3 (SW_RESET)\nGuru Meditation Error: Core 0 panic\n"),
]


def main():
    contract = load_contract(CONTRACT)
    for name, log in LOGS:
        results, ok = run_serial(contract, log)
        print(f"\n=== {name} -> {'PASS' if ok else 'FAIL'} ===")
        print(render(results))
    print("\nexpect must all be seen, forbid must all be absent. Reset the board")
    print("inside the capture window: a boot banner prints once.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
