"""The demos must keep running: they are the front door."""
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_spi_dma_demo_runs_and_catches_both_faults():
    r = subprocess.run([sys.executable, os.path.join(ROOT, "demo", "spi_dma_temporal.py")],
                       capture_output=True, text=True, timeout=120, cwd=ROOT)
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "100 healthy frames" in out and "PASS" in out
    assert "trigger at 1540310ns" in out, "the DMA CS-ordering fault must be named"
    assert "10ns before" in out, "the MOSI setup fault must be named"
    assert "B/E jsontrace" in out
