#!/usr/bin/env python3
"""Dev shim: run the packaged server by file path. See hwcontract/server.py."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hwcontract.server import main

if __name__ == "__main__":
    main()
