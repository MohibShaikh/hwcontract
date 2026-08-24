"""The GitHub Action runner: pairs parsing, verdicts, JUnit, exit codes."""
import importlib.util
import json
import os

import pytest

from hwcontract.sigrok_adapter import synth

HERE = os.path.dirname(os.path.abspath(__file__))
DT = 1e9 / 24_000_000


@pytest.fixture
def check():
    spec = importlib.util.spec_from_file_location(
        "check", os.path.join(HERE, "..", "action", "check.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def good_csv(tmp_path):
    p = tmp_path / "good.csv"
    p.write_text("\n".join(str(s) for s in synth(DT)) + "\n")
    return str(p)


def stretch_highs(samples, by=10):
    """Every high pulse `by` samples longer: T0H and T1H blow past max."""
    out, cur, n = [], samples[0], 0
    for s in samples:
        if s == cur:
            n += 1
        else:
            out.extend([cur] * (n + (by if cur == 1 else 0)))
            cur, n = s, 1
    out.extend([cur] * (n + (by if cur == 1 else 0)))
    return out


def set_env(monkeypatch, tmp_path, timing="", serial="", events="", junit="", evidence=""):
    monkeypatch.setenv("INPUT_TIMING", timing)
    monkeypatch.setenv("INPUT_SERIAL", serial)
    monkeypatch.setenv("INPUT_EVENTS", events)
    monkeypatch.setenv("INPUT_SAMPLERATE", "24000000")
    monkeypatch.setenv("INPUT_JUNIT", junit)
    monkeypatch.setenv("INPUT_EVIDENCE", evidence)
    monkeypatch.chdir(tmp_path)


def test_bundled_contract_by_name_passes(check, monkeypatch, tmp_path, good_csv, capsys):
    set_env(monkeypatch, tmp_path, timing=f"ws2812={good_csv}")
    assert check.main() == 0
    assert "all 1 checks PASS" in capsys.readouterr().out


def test_stretched_capture_fails_with_annotation(check, monkeypatch, tmp_path, capsys):
    bad = tmp_path / "bad.csv"
    bad.write_text("\n".join(str(s) for s in stretch_highs(synth(DT))) + "\n")
    set_env(monkeypatch, tmp_path, timing=f"ws2812={bad}")
    assert check.main() == 1
    out = capsys.readouterr().out
    assert "1 of 1 checks FAIL" in out
    assert "::error title=hwcontract" in out
    assert "T0H" in out


def test_junit_report_written(check, monkeypatch, tmp_path, good_csv):
    bad = tmp_path / "bad.csv"
    bad.write_text("\n".join(str(s) for s in stretch_highs(synth(DT))) + "\n")
    junit = tmp_path / "report.xml"
    set_env(monkeypatch, tmp_path,
            timing=f"ws2812={good_csv}\nws2812={bad}", junit=str(junit))
    assert check.main() == 1
    import xml.etree.ElementTree as ET
    ts = ET.parse(junit).getroot()
    assert ts.tag == "testsuite" and ts.get("tests") == "2" and ts.get("failures") == "1"
    assert ts.find("testcase/failure") is not None


def test_serial_checks(check, monkeypatch, tmp_path, capsys):
    good = tmp_path / "good.log"
    good.write_text("boot v3\nIMU init OK\nready\n")
    bad = tmp_path / "bad.log"
    bad.write_text("Guru Meditation Error\n")
    set_env(monkeypatch, tmp_path,
            serial=f"boot={good}, boot={bad}")
    assert check.main() == 1
    out = capsys.readouterr().out
    assert "serial:boot" in out and "forbidden match: Guru Meditation" in out


def test_missing_glob_is_a_clean_error(check, monkeypatch, tmp_path):
    set_env(monkeypatch, tmp_path, timing="ws2812b=nothing/*.csv")
    with pytest.raises(SystemExit) as e:
        check.main()
    assert "no files match" in str(e.value)


def test_missing_equals_is_a_clean_error(check, monkeypatch, tmp_path, good_csv):
    set_env(monkeypatch, tmp_path, timing=good_csv)
    with pytest.raises(SystemExit) as e:
        check.main()
    assert "not contract=glob" in str(e.value)


def test_unknown_contract_is_a_clean_error(check, monkeypatch, tmp_path, good_csv):
    set_env(monkeypatch, tmp_path, timing=f"nonexistent-part={good_csv}")
    with pytest.raises(SystemExit) as e:
        check.main()
    assert "no contract file" in str(e.value)


def test_no_checks_at_all_is_a_clean_error(check, monkeypatch, tmp_path):
    set_env(monkeypatch, tmp_path)
    with pytest.raises(SystemExit) as e:
        check.main()
    assert "nothing to check" in str(e.value)


CLEAN_SPI_TRACE = {"traceEvents": [
    {"ph": "B", "ts": 0.1, "pid": "gpio.cs", "tid": "falling", "name": "CS"},
    {"ph": "E", "ts": 0.1, "pid": "gpio.cs", "tid": "falling", "name": "CS"},
    {"ph": "B", "ts": 0.4, "pid": "spi.sck", "tid": "rising", "name": "SCK"},
    {"ph": "E", "ts": 0.4, "pid": "spi.sck", "tid": "rising", "name": "SCK"},
    {"ph": "B", "ts": 9.7, "pid": "gpio.cs", "tid": "rising", "name": "CS"},
    {"ph": "E", "ts": 9.7, "pid": "gpio.cs", "tid": "rising", "name": "CS"},
]}


def test_events_input_judges_jsontrace(check, monkeypatch, tmp_path, capsys):
    trace = tmp_path / "spi.json"
    trace.write_text(json.dumps(CLEAN_SPI_TRACE))
    set_env(monkeypatch, tmp_path, events=f"spi-frame={trace}")
    assert check.main() == 0
    out = capsys.readouterr().out
    assert "events:spi-frame" in out and "all 1 checks PASS" in out


def test_events_input_fails_on_broken_ordering(check, monkeypatch, tmp_path, capsys):
    broken = {"traceEvents": [
        {"ph": "B", "ts": 0.4, "pid": "spi.sck", "tid": "rising", "name": "SCK"},
        {"ph": "E", "ts": 0.4, "pid": "spi.sck", "tid": "rising", "name": "SCK"},
        {"ph": "B", "ts": 0.6, "pid": "gpio.cs", "tid": "falling", "name": "CS"},
        {"ph": "E", "ts": 0.6, "pid": "gpio.cs", "tid": "falling", "name": "CS"},
        {"ph": "B", "ts": 9.7, "pid": "gpio.cs", "tid": "rising", "name": "CS"},
        {"ph": "E", "ts": 9.7, "pid": "gpio.cs", "tid": "rising", "name": "CS"},
    ]}
    trace = tmp_path / "spi.json"
    trace.write_text(json.dumps(broken))
    set_env(monkeypatch, tmp_path, events=f"spi-frame={trace}")
    assert check.main() == 1
    assert "cs-precedes-first-clock" in capsys.readouterr().out


def test_evidence_file_carries_hashes(check, monkeypatch, tmp_path, good_csv):
    evidence = tmp_path / "evidence.json"
    set_env(monkeypatch, tmp_path, timing=f"ws2812={good_csv}", evidence=str(evidence))
    assert check.main() == 0
    data = json.loads(evidence.read_text())
    assert data["checks"][0]["verdict"] == "PASS"
    assert len(data["checks"][0]["contract_sha256"]) == 64
    assert len(data["checks"][0]["input_sha256"]) == 64
    assert data["checks"][0]["input_kind"] == "timing"
