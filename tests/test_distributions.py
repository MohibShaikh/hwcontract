"""Distribution-based measurement: every pulse counts, not just the median."""
import hashlib
import json

from hwcontract import __version__
from hwcontract.judge import (FAIL, MARGINAL, PASS, evidence, judge, run,
                              sha256_of)
from hwcontract.sigrok_adapter import observe, synth

DT = 1e9 / 24_000_000
EDGE = {"name": "T0H", "min": 250, "typ": 400, "max": 550}


def by_name(obs):
    return {o["name"]: o for o in obs}


# ---- adapter emits distributions --------------------------------------------

def test_observe_carries_distribution_fields():
    obs = by_name(observe(synth(DT), DT))
    for k in ("count", "min", "max", "p5", "p50", "p95", "jitter", "widths", "value"):
        assert k in obs["T0H"], k
    d = obs["T0H"]
    assert d["count"] == len(d["widths"])
    assert d["widths"] == sorted(d["widths"])
    assert d["min"] <= d["p5"] <= d["p50"] <= d["p95"] <= d["max"]
    assert d["jitter"] == d["p95"] - d["p5"]
    assert d["value"] == d["p50"]


def test_reset_single_pulse_distribution():
    obs = by_name(observe(synth(DT), DT))["RESET"]
    assert obs["count"] == 1
    assert obs["jitter"] == 0
    assert obs["p50"] == obs["min"] == obs["max"]


# ---- the judge escalates on violating pulses --------------------------------

def obs_with_widths(p50, widths):
    """Full distribution observation, the shape the adapter emits."""
    s = sorted(widths)
    return {"name": "T0H", "value": p50, "p50": p50, "count": len(s),
            "min": round(s[0]), "max": round(s[-1]),
            "p5": round(s[0]), "p95": round(s[-1]),
            "jitter": round(s[-1] - s[0]), "widths": [round(w) for w in s]}


def test_all_pulses_in_window_passes():
    widths = [340.0] * 100
    status, hint = judge(EDGE, obs_with_widths(400, widths), 20)
    assert (status, hint) == (PASS, "")


def test_rare_outlier_escalates_pass_to_marginal():
    # 1 of 200 pulses (0.5%) outside 250..550: median looks perfect, edge is MARGINAL
    widths = [340.0] * 199 + [700.0]
    status, hint = judge(EDGE, obs_with_widths(400, widths), 20)
    assert status == MARGINAL
    assert "1 of 200 pulses out of window (0.5%)" in hint


def test_violation_burst_fails_even_with_clean_median():
    # 5 of 200 (2.5%) outside the window, p50 dead-on typ: still a FAIL
    widths = [400.0] * 195 + [700.0] * 5
    status, hint = judge(EDGE, obs_with_widths(400, widths), 20)
    assert status == FAIL
    assert "p50 in spec but the capture violates the window" in hint


def test_violations_annotate_an_already_failing_edge():
    widths = [340.0] * 198 + [700.0] * 2
    status, hint = judge(EDGE, obs_with_widths(700, widths), 20)  # p50 out too
    assert status == FAIL
    assert "long (typ 400)" in hint and "2 of 200 pulses out of window" in hint


def test_summary_only_observation_judges_on_median():
    status, hint = judge(EDGE, {"name": "T0H", "value": 400}, 20)
    assert (status, hint) == (PASS, "")
    results, _ = run({"contract": "t", "headroom_pct": 20, "edges": [EDGE]},
                     [{"name": "T0H", "value": 400}])
    assert "violations" not in results[0]


def test_run_rows_carry_distribution_summary():
    widths = [340.0] * 199 + [700.0]
    results, ok = run({"contract": "t", "headroom_pct": 20, "edges": [EDGE]},
                      [obs_with_widths(400, widths)])
    row = results[0]
    assert ok is False
    assert row["violations"] == 1 and row["violation_pct"] == 0.5
    assert row["count"] == 200 and row["jitter"] >= 0
    assert "widths" not in row, "raw widths stay in observations, not in verdict rows"


# ---- evidence ----------------------------------------------------------------

def test_evidence_hashes_the_contract_bytes(tmp_path):
    p = tmp_path / "c.contract.yaml"
    p.write_text("contract: t\nheadroom_pct: 20\nedges:\n  - {name: E, min: 1, typ: 2, max: 3}\n")
    ev = evidence(str(p), capture_sha256="abc")
    assert ev["contract_sha256"] == hashlib.sha256(p.read_bytes()).hexdigest()
    assert ev["hwcontract_version"] == __version__
    assert ev["capture_sha256"] == "abc"
    assert "timestamp_utc" in ev


def test_sha256_of_objects_is_canonical():
    assert sha256_of({"a": 1, "b": 2}) == sha256_of({"b": 2, "a": 1})
    assert sha256_of(b"x") == hashlib.sha256(b"x").hexdigest()


def test_example_observations_file_is_distribution_shaped():
    import os
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "hwcontract", "examples", "observations.example.json")
    obs = json.load(open(p))
    for o in obs:
        assert o["count"] == len(o["widths"])
        assert o["value"] == o["p50"]
    _, ok = run({"contract": "t", "headroom_pct": 20, "edges": [
        {"name": o["name"], "min": 0, "typ": o["p50"], "max": None} for o in obs]}, obs)
    assert ok is True


# ---- sample resolution: no verdict finer than the capture -------------------

R = round(DT, 3)                                    # 41.667ns at 24MHz


def with_resolution(o, r=R):
    return dict(o, resolution_ns=r)


def test_observe_records_the_sample_period():
    for o in observe(synth(DT), DT):
        assert o["resolution_ns"] == R


def test_fail_needs_more_than_one_sample_past_the_window():
    status, hint = judge(EDGE, with_resolution({"name": "T0H", "value": 200}), 20)
    assert status == FAIL and "200ns short (typ 400)" in hint
    status, hint = judge(EDGE, with_resolution({"name": "T0H", "value": 220}), 20)
    assert status == MARGINAL
    assert "30ns past min, within one sample (41.7ns)" in hint


def test_pass_needs_one_sample_inside_the_window():
    status, hint = judge(EDGE, with_resolution({"name": "T0H", "value": 280}), 0)
    assert status == MARGINAL and "within one sample (41.7ns) of min" in hint
    assert judge(EDGE, with_resolution({"name": "T0H", "value": 300}), 0) == (PASS, "")


def test_unbounded_edge_respects_resolution_at_min():
    reset = {"name": "RESET", "min": 50000, "typ": 50000, "max": None}
    assert judge(reset, with_resolution({"name": "RESET", "value": 50020}), 20)[0] == MARGINAL
    assert judge(reset, with_resolution({"name": "RESET", "value": 50100}), 20)[0] == PASS


def test_pulses_within_one_sample_are_unresolved_not_violations():
    # 10% of pulses 8ns past max: the capture cannot tell, so MARGINAL, not FAIL
    o = with_resolution(obs_with_widths(400, [400.0] * 180 + [558.0] * 20))
    status, hint = judge(EDGE, o, 20)
    assert status == MARGINAL
    assert "20 of 200 pulses within one sample (41.7ns) of the window" in hint
    assert "out of window" not in hint


def test_pulses_past_one_sample_still_fail():
    o = with_resolution(obs_with_widths(400, [400.0] * 180 + [600.0] * 20))
    status, hint = judge(EDGE, o, 20)
    assert status == FAIL and "20 of 200 pulses out of window (10.0%)" in hint


def test_run_rows_carry_resolution_and_unresolved():
    o = with_resolution(obs_with_widths(400, [400.0] * 199 + [558.0]))
    results, ok = run({"contract": "t", "headroom_pct": 20, "edges": [EDGE]}, [o])
    row = results[0]
    assert ok is False
    assert row["resolution_ns"] == R
    assert (row["violations"], row["unresolved"]) == (0, 1)


def test_invalid_resolution_is_an_error():
    import pytest
    for bad in (-1, float("nan"), float("inf"), "41.7", True):
        with pytest.raises(ValueError, match="resolution_ns"):
            judge(EDGE, {"name": "T0H", "value": 400, "resolution_ns": bad}, 20)


def test_violation_burst_fails_when_the_median_is_unresolved():
    # p50 8ns past max (within one sample), but 10% of pulses are definitely out
    o = with_resolution(obs_with_widths(558, [558.0] * 180 + [700.0] * 20))
    status, hint = judge(EDGE, o, 20)
    assert status == FAIL
    assert "20 of 200 pulses out of window (10.0%)" in hint


def test_violation_threshold_is_not_rounded_away():
    # 1001 of 100001 pulses is 1.001%, over the 1% limit, though it rounds to 1.0
    o = obs_with_widths(400, [400.0] * 99000 + [700.0] * 1001)
    assert judge(EDGE, o, 20)[0] == FAIL


def test_non_finite_pulses_are_violations():
    for bad in (float("nan"), float("inf")):
        o = obs_with_widths(400, [400.0] * 10)
        o["widths"] = [400.0] * 10 + [bad] * 90
        status, hint = judge(EDGE, o, 20)
        assert status == FAIL and "90 of 100 pulses out of window" in hint


def test_duplicate_observation_names_are_rejected():
    import pytest
    contract = {"contract": "t", "headroom_pct": 20, "edges": [EDGE]}
    with pytest.raises(ValueError, match="duplicate observation name: 'T0H'"):
        run(contract, [{"name": "T0H", "value": 100}, {"name": "T0H", "value": 400}])


def test_malformed_observations_list_every_problem():
    import pytest
    contract = {"contract": "t", "headroom_pct": 20, "edges": [EDGE]}
    with pytest.raises(ValueError) as e:
        run(contract, [{"name": "T0H", "value": "400"}, {"value": 1}, 7,
                       {"name": "T1H", "value": True},
                       {"name": "T0L", "value": 1, "widths": [1, None]}])
    msg = str(e.value)
    for part in ("observations[T0H].value must be a number", "observations[1].name",
                 "observations[2] must be a mapping", "observations[T1H].value",
                 "observations[T0L].widths must be a list of numbers"):
        assert part in msg, part
    with pytest.raises(ValueError, match="observations must be a list"):
        run(contract, {"name": "T0H", "value": 400})
