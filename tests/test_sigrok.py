"""Pulse-width observation: clean signals measure clean, junk measures to nothing."""
import pytest

from hwcontract.sigrok_adapter import observe, read_csv, runs, synth

DT = 1e9 / 24_000_000


def by_name(obs):
    return {o["name"]: o["value"] for o in obs}


def test_synth_signal_measures_near_nominal():
    obs = by_name(observe(synth(DT), DT))
    assert abs(obs["T0H"] - 350) < DT * 2
    assert abs(obs["T1H"] - 700) < DT * 2
    assert obs["RESET"] > 50000


def test_every_edge_has_a_positive_width():
    for o in observe(synth(DT), DT):
        assert o["value"] > 0


def test_constant_level_capture_yields_no_bit_observations():
    # an idle-low line reports only RESET (the whole capture is one long low run);
    # an idle-high line has no low pulses at all, so nothing at all
    assert [o["name"] for o in observe([0] * 1000, DT)] == ["RESET"]
    assert observe([1] * 1000, DT) == []


def test_tiny_capture_yields_no_observations():
    assert observe([0, 1], DT) == []


def test_runs_counts_transitions():
    assert runs([0, 0, 1, 1, 0], 10) == [(0, 20), (1, 20), (0, 10)]


def test_outlier_pulse_does_not_move_the_median():
    clean = synth(DT)
    dirty = synth(DT)
    # corrupt one 0-bit high pulse (samples ~8-9 long) into a 1-bit-length pulse
    i = 3  # inside the first '0' bit high pulse
    for j in range(i, i + 25):
        dirty[j] = 1
    a = by_name(observe(clean, DT))
    b = by_name(observe(dirty, DT))
    assert abs(a["T0H"] - b["T0H"]) <= DT * 2, "median must be robust to a single glitch"


def test_read_csv_ignores_junk_lines(tmp_path):
    p = tmp_path / "cap.csv"
    p.write_text("0\n1\n1\nhello\n\n0\n")
    assert read_csv(str(p)) == [0, 1, 1, 0]
