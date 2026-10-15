"""Serial judge: expect/forbid semantics and the capture cap."""
import pytest

from hwcontract.judge import ContractError, FAIL, PASS, SERIAL_CAP, run_serial

CONTRACT = {"contract": "boot", "kind": "serial",
            "expect": ["IMU init OK", r"boot v\d+"],
            "forbid": ["panic", "Guru Meditation", r"\bnan\b"]}

GOOD = "boot v3\nsensor warmup...\nIMU init OK\nready\n"


def test_clean_boot_passes():
    results, ok = run_serial(CONTRACT, GOOD)
    assert ok is True
    assert all(r["status"] == PASS for r in results)


def test_missing_expect_fails():
    results, ok = run_serial(CONTRACT, "boot v3\nready\n")
    assert ok is False
    missing = [r for r in results if r["edge"] == "IMU init OK"]
    assert missing and missing[0]["status"] == FAIL
    assert missing[0]["hint"] == "expected pattern not in capture"


def test_forbidden_match_fails_and_names_it():
    results, ok = run_serial(CONTRACT, GOOD + "\nGuru Meditation Error: Core 0 panic\n")
    assert ok is False
    bad = [r for r in results if r["typ"] == "forbid" and r["status"] == FAIL]
    assert len(bad) == 2
    assert any(r["hint"] == "forbidden match: Guru Meditation" for r in bad)


def test_word_boundary_regex():
    _, ok = run_serial(CONTRACT, "boot v3\nIMU init OK\nnan readings\n")
    assert ok is False
    _, ok = run_serial(CONTRACT, "boot v3\nIMU init OK\nnano board\n")
    assert ok is True


@pytest.mark.parametrize("field,pattern", [("expect", "(["), ("forbid", "*bad")])
def test_broken_regex_is_a_contract_error(field, pattern):
    with pytest.raises(ContractError, match="bad regex"):
        run_serial({"contract": "b", "kind": "serial", field: [pattern]}, GOOD)


@pytest.mark.parametrize("length", [SERIAL_CAP - 1, SERIAL_CAP])
def test_capture_limit_accepts_complete_logs(length):
    log = GOOD + "x" * (length - len(GOOD))
    assert run_serial(CONTRACT, log)[1] is True


@pytest.mark.parametrize("tail", ["x", "panic"])
def test_oversized_capture_never_judges_a_successful_prefix(tail):
    log = GOOD + "x" * (SERIAL_CAP - len(GOOD)) + tail
    with pytest.raises(ValueError, match="exceeds limit.*characters"):
        run_serial(CONTRACT, log)


def test_forbidden_pattern_at_limit_is_checked():
    log = GOOD + "x" * (SERIAL_CAP - len(GOOD) - len("panic")) + "panic"
    assert run_serial(CONTRACT, log)[1] is False


def test_non_text_log_is_rejected():
    with pytest.raises(ValueError, match="must be text"):
        run_serial(CONTRACT, b"boot")


def test_read_log_uses_the_same_limit(tmp_path):
    from hwcontract.serial_adapter import read_log
    path = tmp_path / "boot.log"
    # The limit counts decoded characters, not UTF-8 bytes.
    text = "é" * SERIAL_CAP
    path.write_text(text, encoding="utf-8")
    assert read_log(path) == text
    path.write_text(text + "x", encoding="utf-8")
    with pytest.raises(ValueError, match="exceeds limit"):
        read_log(path)


def fake_serial(monkeypatch, chunks):
    import sys
    from types import SimpleNamespace
    from hwcontract import serial_adapter
    class Port:
        closed = False
        def read(self, size):
            chunk = chunks[0][:size]
            chunks[0] = chunks[0][size:]
            if not chunks[0]:
                chunks.pop(0)
            return chunk
        def close(self):
            self.closed = True
    port = Port()
    monkeypatch.setitem(sys.modules, "serial", SimpleNamespace(Serial=lambda *a, **kw: port))
    monkeypatch.setattr(serial_adapter.time, "monotonic", lambda: 0 if chunks else 2)
    return port


@pytest.mark.parametrize("length", [SERIAL_CAP, SERIAL_CAP + 1])
def test_live_capture_never_returns_a_truncated_prefix(monkeypatch, length):
    from hwcontract.serial_adapter import capture
    port = fake_serial(monkeypatch, [b"x" * length])
    if length > SERIAL_CAP:
        with pytest.raises(ValueError, match="exceeds limit"):
            capture("fake", seconds=1)
    else:
        assert capture("fake", seconds=1) == "x" * length
    assert port.closed


def test_live_capture_decodes_split_utf8_once(monkeypatch):
    from hwcontract.serial_adapter import capture
    port = fake_serial(monkeypatch, [b"\xc3", b"\xa9", b"\xc3"])
    assert capture("fake", seconds=1) == "é�"
    assert port.closed


@pytest.mark.parametrize("seconds", [0, -1, float("inf"), float("nan"), True])
def test_capture_duration_is_positive_finite(seconds):
    from hwcontract.serial_adapter import capture
    with pytest.raises(ValueError, match="seconds must be a positive finite"):
        capture("unused", seconds=seconds)


def test_live_capture_limit_includes_final_decoder_flush(monkeypatch):
    from hwcontract.serial_adapter import capture
    port = fake_serial(monkeypatch, [b"x" * SERIAL_CAP, b"\xc3"])
    with pytest.raises(ValueError, match="exceeds limit"):
        capture("fake", seconds=1)
    assert port.closed
