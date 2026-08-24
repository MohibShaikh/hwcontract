"""Serial judge: expect/forbid semantics and the capture cap."""
from hwcontract.judge import FAIL, PASS, run_serial

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


def test_broken_expect_regex_reports_not_crashes():
    c = {"contract": "b", "kind": "serial", "expect": ["(["]}
    results, ok = run_serial(c, GOOD)
    assert ok is False
    assert "error" in results[0]["hint"].lower() or "Error" in results[0]["hint"]


def test_broken_forbid_regex_fails_closed():
    c = {"contract": "b", "kind": "serial", "forbid": ["*bad"]}
    results, ok = run_serial(c, GOOD)
    assert ok is False, "a forbid rule that cannot run must fail the verdict"
    assert results[0]["status"] == FAIL


def test_capture_is_capped():
    from hwcontract.judge import SERIAL_CAP
    log = "x" * (SERIAL_CAP + 10) + "IMU init OK"
    _, ok = run_serial(CONTRACT, log)
    assert ok is False, "the tail beyond the cap must not be matched"
