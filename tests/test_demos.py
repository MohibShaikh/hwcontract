"""The demos must keep running: they are the front door."""
import os
import subprocess
import sys
import hashlib
import io
import json
from pathlib import Path
import re

import pytest

from hwcontract import demo_ws2812b

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


def test_installed_module_spi_runs_outside_repo(tmp_path):
    result = subprocess.run([sys.executable, "-m", "hwcontract.demo", "spi"],
                            capture_output=True, text=True, timeout=120, cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    assert "100 healthy frames" in result.stdout
    assert "trigger at 1540310ns" in result.stdout
    assert "10ns before" in result.stdout
    assert "artificial B/E jsontrace" in result.stdout
    evidence = [json.loads(line[len("evidence: "):]) for line in result.stdout.splitlines()
                if line.startswith("evidence: ")]
    assert len(evidence) == 3
    assert all(e["input_origin"] == "synthetic" and e["event_count"] > 0 for e in evidence)


def test_capture_download_is_verified_cached_and_offline(tmp_path, monkeypatch):
    payload = b"a stand-in download; this test exercises caching, not real replay"
    monkeypatch.setattr(demo_ws2812b, "SHA256", hashlib.sha256(payload).hexdigest())
    requests = []

    def download(url, timeout):
        requests.append((url, timeout))
        return io.BytesIO(payload)

    monkeypatch.setattr(demo_ws2812b.urllib.request, "urlopen", download)
    path = demo_ws2812b.get_capture(tmp_path)
    assert path.read_bytes() == payload
    assert demo_ws2812b.get_capture(tmp_path, offline=True) == path
    assert demo_ws2812b.get_capture(capture_path=path) == path
    assert len(requests) == 1
    assert demo_ws2812b.REVISION in requests[0][0]
    path.write_bytes(b"corrupted")
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        demo_ws2812b.get_capture(tmp_path)
    path.unlink()
    with pytest.raises(FileNotFoundError, match="without --offline"):
        demo_ws2812b.get_capture(tmp_path, offline=True)
    assert len(requests) == 1


def test_bad_download_never_becomes_cached_capture(tmp_path, monkeypatch):
    monkeypatch.setattr(demo_ws2812b.urllib.request, "urlopen",
                        lambda *a, **kw: io.BytesIO(b"not the capture"))
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        demo_ws2812b.get_capture(tmp_path)
    assert not list(tmp_path.iterdir())


def test_offline_neopixel_missing_capture_is_actionable(tmp_path):
    result = subprocess.run([sys.executable, "-m", "hwcontract.demo", "ws2812b",
                             "--offline", "--cache-dir", str(tmp_path)],
                            cwd=tmp_path, capture_output=True, text=True, timeout=10)
    assert result.returncode == 1
    assert "capture not found" in result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("broken, code", [(False, 0), (True, 1)])
def test_documented_raw_spi_recipe(tmp_path, monkeypatch, broken, code):
    from hwcontract import demo_spi
    monkeypatch.setattr(demo_spi, "N_FRAMES", 1)
    channels = demo_spi.build_channels(broken_cs_frame=0 if broken else None)
    with (tmp_path / "spi.csv").open("w") as stream:
        stream.write("D0,D1,D2\n")
        for row in zip(channels["spi.sck"], channels["spi.mosi"], channels["gpio.cs"]):
            stream.write(",".join(map(str, row)) + "\n")
    guide = Path(ROOT, "docs", "writing-contracts.md").read_text()
    recipe = next(block for block in re.findall(r"```python\n(.*?)```", guide, re.S)
                  if 'channels = read_multi_csv("spi.csv")' in block)
    result = subprocess.run([sys.executable, "-c", recipe], cwd=tmp_path,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == code, result.stderr
    verdict = json.loads((tmp_path / "spi.verdict.json").read_text())
    assert verdict["verdict"] == ("FAIL" if broken else "PASS")
    assert verdict["capture_sha256"] == hashlib.sha256((tmp_path / "spi.csv").read_bytes()).hexdigest()
    assert verdict["samplerate_hz"] == 100_000_000


def test_ws2812b_export_writes_a_csv_that_check_reads(tmp_path, monkeypatch):
    samples = [0] * 2000 + ([1] * 10 + [0] * 20) * 24 + [0] * 2000 + [1]
    monkeypatch.setattr(demo_ws2812b, "get_capture", lambda *a: "capture.sr")
    monkeypatch.setattr(demo_ws2812b, "load_data_in", lambda path: samples)
    out = tmp_path / "capture.csv"
    assert demo_ws2812b.main(export=out) == 0
    from hwcontract.sigrok_adapter import read_csv
    assert read_csv(str(out)) == samples
