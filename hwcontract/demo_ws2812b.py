"""Real NeoPixel replay. See demo-provenance.md for capture source and license."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import urllib.request
import zipfile

from hwcontract.check import judge_samples
from hwcontract.contracts import EXAMPLES
from hwcontract.judge import evidence, load_contract, render

REVISION = "f0d084a821370a4855a1305fc66439b6d3c54fce"
FILENAME = "ws2812b_neopixel24_24mhz.sr"
URL = (f"https://raw.githubusercontent.com/sigrokproject/sigrok-dumps/{REVISION}/"
       f"led/ws2812b_neopixel24/{FILENAME}")
SHA256 = "4d1558f7714f88a3a996bb90bb177e728976544efd8c6b03d38e1ba4ec644c26"
SAMPLERATE = 24_000_000
WINDOW = 300_000


def _verify(data):
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise ValueError("capture SHA256 mismatch; expected the pinned sigrok NeoPixel capture. "
                         "Remove a damaged cached file and rerun to download it again.")


def get_capture(cache_dir=None, capture_path=None, offline=False):
    """Use a verified local copy, or download atomically into a user-writable cache."""
    if capture_path is not None:
        path = Path(capture_path)
    else:
        cache = (Path(cache_dir) if cache_dir is not None else
                 Path(os.environ.get("XDG_CACHE_HOME", str(Path.home() / ".cache"))) / "hwcontract")
        path = cache / FILENAME
    if path.exists():
        _verify(path.read_bytes())
        return path
    if offline or capture_path is not None:
        raise FileNotFoundError(f"capture not found: {path}; run without --offline to download it")
    print(f"Downloading real sigrok capture (111455 bytes) -> {path}")
    with urllib.request.urlopen(URL, timeout=30) as response:
        data = response.read(1_000_001)
    _verify(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(data)
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return path


def load_data_in(sr_path):
    """Read probe 1 / bit 0, including idle before the first edge and a RESET."""
    with zipfile.ZipFile(sr_path) as archive:
        parts = sorted((n for n in archive.namelist() if n.startswith("logic-1-")),
                       key=lambda n: int(n.split("-")[-1]))
        data = b"".join(archive.read(n) for n in parts)
    if not data:
        raise ValueError("capture has no logic samples")
    bits = data.translate(bytes(b & 1 for b in range(256)))
    start = bits.find(bytes([1 - bits[0]]))
    if start < 0:
        raise ValueError("capture has no DATA_IN transitions")
    offset = max(0, start - 50)
    return list(bits[offset:offset + WINDOW])


def main(cache_dir=None, capture_path=None, offline=False, export=None):
    cap = get_capture(cache_dir, capture_path, offline)
    samples = load_data_in(cap)
    if export is not None:              # the same 0/1 CSV a logic analyzer exports
        Path(export).write_text("".join(f"{b}\n" for b in samples))
        print(f"wrote {len(samples)} samples @24MHz to {export}\n"
              f"judge it: hwcontract --check ws2812b {export} --samplerate 24MHz")
        return 0
    print(f"\nreal WS2812B signal: {len(samples)} samples @24MHz, measured the way "
          "each contract's edges say (same as hwcontract --check)")
    outcomes = []
    for label, filename in [("generic WS2812 contract", "ws2812.contract.yaml"),
                            ("matching WS2812B contract", "ws2812b.contract.yaml")]:
        contract_path = str(EXAMPLES / filename)
        results, ok, _, _ = judge_samples(load_contract(contract_path), samples, SAMPLERATE)
        outcomes.append(ok)
        print(f"\n=== {label} -> {'PASS' if ok else 'FAIL'} ===")
        print(render(results))
        print("evidence: " + json.dumps(evidence(
            contract_path, capture_sha256=SHA256, capture_url=URL,
            samplerate_hz=SAMPLERATE, samples=len(samples), channel="DATA_IN (bit 0)",
            window="50 samples before first transition, up to 300000 samples"), sort_keys=True))
    if outcomes != [False, True]:
        print("Unexpected demo verdicts: expected generic FAIL and matching PASS.")
        return 1
    return 0
