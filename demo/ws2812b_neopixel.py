#!/usr/bin/env python3
"""Compatibility wrapper; installed command: python -m hwcontract.demo ws2812b ."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hwcontract.demo import main

if __name__ == "__main__":
    raise SystemExit(main(["ws2812b", *sys.argv[1:]]))
