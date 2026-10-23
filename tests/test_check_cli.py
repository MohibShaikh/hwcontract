"""hwcontract --check: judge one local file from the shell, no MCP client."""
import json
import subprocess
import sys

import pytest

from hwcontract.judge import FAIL, judge
from hwcontract.sigrok_adapter import observe, synth

SR = 24_000_000
DT = 1e9 / SR


def cli(*args, cwd):
    return subprocess.run([sys.executable, "-m", "hwcontract.server", *args],
                          cwd=cwd, capture_output=True, text=True, timeout=20)


def pulse(ns, level):
    return [level] * max(1, round(ns / DT))


def frame_with_gap(gap_ns):
    """Two WS2812 bits, a low of gap_ns, two more bits, then a full reset."""
    bit0, bit1 = pulse(350, 1) + pulse(800, 0), pulse(700, 1) + pulse(600, 0)
    s = pulse(60000, 0) + bit0 + bit1
    s += pulse(700, 1) + pulse(gap_ns, 0)               # third bit's low stretched
    return s + bit0 + bit1 + pulse(700, 1) + pulse(60000, 0) + [1]


def write_csv(path, samples):
    path.write_text("\n".join(map(str, samples)) + "\n")
    return path


def test_clean_capture_passes(tmp_path):
    write_csv(tmp_path / "good.csv", synth(DT))
    r = cli("--check", "ws2812", "good.csv", "--samplerate", str(SR), cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert r.stdout.startswith("verdict: PASS")


def test_a_gap_no_edge_explains_fails_and_is_located(tmp_path):
    write_csv(tmp_path / "gap.csv", frame_with_gap(7000))
    r = cli("--check", "ws2812b", "gap.csv", "--samplerate", str(SR), cwd=tmp_path)
    assert r.returncode == 1
    assert "1 low pulse of 7000ns that match no edge (over 2x outside every low window)" in r.stdout
    assert "(frame 0, after 3 highs)" in r.stdout
    r = cli("--check", "ws2812b", "gap.csv", "--samplerate", str(SR), "--json", cwd=tmp_path)
    v = json.loads(r.stdout)
    row = next(row for row in v["results"] if row["edge"] == "unmatched low")
    assert row["count"] == 1 and row["locations"][0]["width_ns"] == 7000
    assert row["locations"][0]["highs_before"] == 3
    assert v["samplerate_hz"] == SR and v["input_kind"] == "timing"


def test_observe_files_a_stretched_low_under_its_own_bit():
    # capture_csv's fixed bucketing: a 1-bit whose low stretched to 2us is T1L
    s = pulse(60000, 0) + (pulse(700, 1) + pulse(600, 0)) * 5
    s += pulse(700, 1) + pulse(2000, 0) + pulse(350, 1) + pulse(800, 0)
    s += pulse(700, 1) + pulse(60000, 0) + [1]
    by = {o["name"]: o for o in observe(s, DT)}
    assert by["T1L"]["count"] == 6 and by["T1L"]["max"] == 2000   # 5 clean + the stretched one
    assert by["T0L"]["widths"] == [792]                        # 800ns at 24MHz is 19 samples


def test_csv_needs_a_samplerate(tmp_path):
    write_csv(tmp_path / "good.csv", synth(DT))
    r = cli("--check", "ws2812", "good.csv", cwd=tmp_path)
    assert r.returncode == 2 and "--samplerate" in r.stderr and not r.stdout


def test_csv_for_a_contract_without_measures_is_refused(tmp_path):
    write_csv(tmp_path / "servo.csv", synth(DT))
    r = cli("--check", "servo", "servo.csv", "--samplerate", str(SR), cwd=tmp_path)
    assert r.returncode == 2
    assert "PULSE_MIN" in r.stderr and "measure: high, low or period" in r.stderr


def test_timing_observations_json(tmp_path):
    (tmp_path / "obs.json").write_text(json.dumps(observe(synth(DT), DT)))
    r = cli("--check", "ws2812", "obs.json", cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr


def test_serial_log(tmp_path):
    (tmp_path / "boot.log").write_text("boot v3\nIMU init OK\nready\n")
    (tmp_path / "panic.log").write_text("Guru Meditation Error\n")
    assert cli("--check", "boot", "boot.log", cwd=tmp_path).returncode == 0
    assert cli("--check", "boot", "panic.log", cwd=tmp_path).returncode == 1


def test_events_trace(tmp_path):
    events = [{"source": "gpio.cs", "type": "falling", "start_ns": 0, "end_ns": 0},
              {"source": "spi.sck", "type": "rising", "start_ns": 300, "end_ns": 300},
              {"source": "gpio.cs", "type": "rising", "start_ns": 9700, "end_ns": 9700}]
    (tmp_path / "spi.json").write_text(json.dumps(events))
    r = cli("--check", "spi-frame", "spi.json", cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize("args, diagnostic", [
    (["--check", "nope", "x.csv"], "no contract"),
    (["--check", "ws2812", "missing.csv", "--samplerate", "24000000"], "missing.csv"),
    (["--samplerate", "24000000"], "--samplerate requires --check"),
    (["list"], "hwcontract takes flags only: --list-contracts"),      # no subcommands
])
def test_usage_errors_have_no_traceback(tmp_path, args, diagnostic):
    r = cli(*args, cwd=tmp_path)
    assert r.returncode == 2
    assert diagnostic in r.stderr and "Traceback" not in r.stderr


def test_the_table_shows_each_window_and_the_starting_idle_is_no_reset(tmp_path):
    # the idle the capture starts in is long enough, but the real frame gaps are short
    frame = (pulse(400, 1) + pulse(850, 0)) * 23 + pulse(400, 1)
    s = pulse(80000, 0) + frame + pulse(30000, 0) + frame + pulse(30000, 0) + frame + [0]
    write_csv(tmp_path / "c.csv", s)
    r = cli("--check", "ws2812b", "c.csv", "--samplerate", "24MHz", cwd=tmp_path)
    lines = {l.split()[0]: l for l in r.stdout.splitlines()[1:] if l.strip()}
    assert lines["edge"].split()[:3] == ["edge", "window", "typ"]
    assert lines["T0L"].split()[1] == "700-1000" and lines["RESET"].split()[1] == ">=50000"
    assert lines["RESET"].split()[3:5] == ["30000", "FAIL"]
    assert "2 of 2 pulses out of window" in lines["RESET"]


def test_judge_cli_accepts_a_bundled_name(tmp_path):
    (tmp_path / "obs.json").write_text(json.dumps(observe(synth(DT), DT)))
    r = subprocess.run([sys.executable, "-m", "hwcontract.judge", "ws2812", "obs.json"],
                       cwd=tmp_path, capture_output=True, text=True, timeout=20)
    assert r.returncode == 0, r.stdout + r.stderr


# ---- hints say which pulses are out, not just where the median sits ---------

EDGE = {"name": "T0L", "min": 700, "typ": 850, "max": 1000}
RESET = {"name": "RESET", "min": 50000, "typ": 50000, "max": None}


def obs(widths, p50):
    return {"name": "x", "value": p50, "p50": p50, "widths": sorted(widths)}


def test_violation_hint_shows_the_out_of_window_range():
    status, hint = judge(EDGE, obs([850] * 95 + [1800, 2400, 3200, 2000, 1900], 850), 20)
    assert status == FAIL
    assert "5 of 100 pulses out of window (5.0%): 1800-3200ns" in hint


def test_two_populations_do_not_read_as_all_short():
    # 4 real resets and 7 mid-frame gaps: the median is a gap, the resets are fine
    status, hint = judge(RESET, obs([300000] * 4 + [7000] * 7, 7000), 20)
    assert status == FAIL
    assert hint == ("7 of 11 pulses below min: 7000ns, down to 0.14x min; "
                    "4 of 11 in window, so the p50 mixes both")


def test_hints_say_how_far_past_the_limit():
    status, hint = judge(EDGE, obs([850] * 199 + [2000], 850), 20)
    assert status == "MARGINAL" and "1 of 200 pulses out of window (0.5%): 2000ns, up to 2x max" in hint
    status, hint = judge({"name": "T1H", "min": 1900, "typ": 2000, "max": 2100}, obs([1750] * 9, 1750), 20)
    assert status == FAIL and hint.startswith("250ns short (typ 2000), 150ns past min")


def test_out_of_window_and_unmatched_of_one_spread_get_a_note(tmp_path):
    # stalled lows from 1.8 to 3us: the 2x line cuts them into T0L and unmatched
    s = pulse(80000, 0)
    for i in range(400):
        s += pulse(400, 1) + pulse([1800, 2400, 3000][i % 3] if i % 40 == 0 else 850, 0)
    s += pulse(300000, 0) + [1]
    write_csv(tmp_path / "c.csv", s)
    r = cli("--check", "ws2812b", "c.csv", "--samplerate", "24MHz", cwd=tmp_path)
    assert r.returncode == 1 and "unmatched low" in r.stdout
    note = " ".join(r.stdout.split("note:")[1].split())
    assert note.startswith("the out-of-window pulses of T0L and the unmatched lows of")
    assert "are one spread of widths" in note
    assert max(map(len, r.stdout.split("note:")[1].splitlines())) <= 78


# ---- one core, every surface ------------------------------------------------

def dshot_frame(one_high):
    """One DShot600 frame alternating 1 and 0 bits, then the inter-frame gap."""
    s = pulse(20000, 0)
    for i in range(16):
        high = one_high if i % 2 == 0 else 625
        s += pulse(high, 1) + pulse(1667 - high, 0)
    return s + pulse(20000, 0) + [1]


@pytest.mark.parametrize("value, hz", [(24_000_000, 24_000_000), ("24000000", 24e6),
                                       ("24e6", 24e6), ("24M", 24e6), ("24 MHz", 24e6),
                                       ("500kHz", 5e5)])
def test_samplerate_accepts_units(value, hz):
    from hwcontract.check import parse_hz
    assert parse_hz(value) == hz


def test_samplerate_rejects_garbage():
    from hwcontract.check import parse_hz
    with pytest.raises(ValueError, match="like 24000000 or 24MHz"):
        parse_hz("fast")


def test_contract_resolves_by_its_own_name(tmp_path):
    from hwcontract import contracts
    assert contracts.resolve_contract("dshot600", tmp_path).endswith("dshot.contract.yaml")


def test_every_dshot_rate_takes_a_csv(tmp_path):
    write_csv(tmp_path / "esc.csv", dshot_frame(1250))
    r = cli("--check", "dshot600", "esc.csv", "--samplerate", "24MHz", cwd=tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    write_csv(tmp_path / "short.csv", dshot_frame(1042))   # 1-bit duty 62.5%, not 75%
    r = cli("--check", "dshot", "short.csv", "--samplerate", "24MHz", cwd=tmp_path)
    assert r.returncode == 1 and "T1H" in r.stdout


def test_wrong_samplerate_says_so(tmp_path):
    write_csv(tmp_path / "esc.csv", dshot_frame(1250))
    r = cli("--check", "dshot", "esc.csv", "--samplerate", "12MHz", cwd=tmp_path)
    assert r.returncode == 1 and "Check that 12MHz is the rate" in r.stdout


def test_explicit_kind_rejects_a_contract_of_another_kind(tmp_path):
    from hwcontract.check import check_file
    from hwcontract.contracts import cli_contract
    write_csv(tmp_path / "x.csv", synth(DT))
    with pytest.raises(ValueError, match="expected a timing contract"):
        check_file(cli_contract("boot"), str(tmp_path / "x.csv"), SR, kind="timing")


def test_mcp_judge_file_matches_the_cli(tmp_path, monkeypatch):
    from hwcontract import server
    monkeypatch.setattr(server, "ROOT", str(tmp_path))
    write_csv(tmp_path / "gap.csv", frame_with_gap(7000))
    out = server.tool_judge_file("ws2812b", "gap.csv", "24MHz")
    assert out["verdict"] == "FAIL" and out["evidence"]["samplerate_hz"] == SR
    assert out["results"][-1]["edge"] == "unmatched low"
    response = server._call({"name": "judge_file", "arguments": {
        "contract_path": "ws2812b", "path": "gap.csv", "samplerate": SR}})
    assert not response["isError"]


@pytest.mark.parametrize("path, diagnostic", [("../outside.csv", "escapes allowed root"),
                                              ("missing.csv", "not a regular file"),
                                              ("", "non-empty")])
def test_mcp_judge_file_stays_inside_root(tmp_path, monkeypatch, path, diagnostic):
    from hwcontract import server
    root = tmp_path / "root"
    root.mkdir()
    write_csv(tmp_path / "outside.csv", synth(DT))
    monkeypatch.setattr(server, "ROOT", str(root))
    with pytest.raises(ValueError, match=diagnostic):
        server.tool_judge_file("ws2812", path, SR)


def test_mcp_judge_file_rejects_a_symlink_out_of_root(tmp_path, monkeypatch):
    from hwcontract import server
    root = tmp_path / "root"
    root.mkdir()
    write_csv(tmp_path / "outside.csv", synth(DT))
    (root / "link.csv").symlink_to(tmp_path / "outside.csv")
    monkeypatch.setattr(server, "ROOT", str(root))
    with pytest.raises(ValueError, match="escapes allowed root"):
        server.tool_judge_file("ws2812", "link.csv", SR)
