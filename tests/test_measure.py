"""Contract-driven measurement: a contract alone teaches hwcontract a new protocol."""
import pytest

from hwcontract.check import judge_samples
from hwcontract.judge import ContractError, validate_contract
from hwcontract.measure import TOLERANCE, measure, measures

SR = 24_000_000
DT = 1e9 / SR


def pulse(ns, level):
    return [level] * max(1, round(ns / DT))


def timing(*edges, name="part"):
    return validate_contract({"contract": name, "headroom_pct": 10, "edges": list(edges)})


# A protocol no code knows about: a sensor answering with 3us/9us highs for
# 0/1 bits, a 4us low after each, and a >=100us gap between frames.
SENSOR = timing({"name": "ZERO", "measure": "high", "min": 2500, "typ": 3000, "max": 3500},
                {"name": "ONE", "measure": "high", "min": 8000, "typ": 9000, "max": 10000},
                {"name": "SPACE", "measure": "low", "min": 3500, "typ": 4000, "max": 4500},
                {"name": "GAP", "measure": "low", "min": 100000, "typ": 100000, "max": None},
                name="made-up-sensor")


def sensor_frame(bits, space=4000):
    s = []
    for b in bits:
        s += pulse(9000 if b else 3000, 1) + pulse(space, 0)
    return s[:-len(pulse(space, 0))] + pulse(150000, 0)


def test_an_unknown_protocol_is_judged_from_its_contract_alone():
    s = pulse(150000, 0) + sensor_frame([1, 0, 1, 1]) + [1]
    results, ok, _, _ = judge_samples(SENSOR, s, SR)
    assert ok, results
    by = {r["edge"]: r for r in results}
    assert by["ONE"]["count"] == 3 and by["ZERO"]["count"] == 1 and by["SPACE"]["count"] == 3


def test_a_bad_pulse_is_blamed_on_the_nearest_edge_and_located():
    s = pulse(150000, 0) + sensor_frame([1, 0]) + sensor_frame([0, 1], space=5200) + [1]
    results, ok, _, _ = judge_samples(SENSOR, s, SR)
    by = {r["edge"]: r for r in results}
    assert not ok and by["SPACE"]["status"] == "FAIL"
    assert by["SPACE"]["locations"][0]["frame"] == 1               # the second frame
    assert by["SPACE"]["locations"][0]["highs_before"] == 1
    assert "first at " in by["SPACE"]["hint"]


def test_a_run_near_no_edge_is_unmatched_and_fails():
    s = pulse(150000, 0) + pulse(3000, 1) + pulse(30000, 0) + pulse(3000, 1) + pulse(150000, 0) + [1]
    results, ok, _, _ = judge_samples(SENSOR, s, SR)
    row = results[-1]
    assert not ok and row["edge"] == "unmatched low" and row["count"] == 1
    assert row["locations"][0]["width_ns"] == 30000


def test_tolerance_bounds_blame():
    edge = {"name": "X", "measure": "high", "min": 1000, "typ": 1500, "max": 2000}
    c = timing(edge)
    inside = pulse(10000, 0) + pulse(2000 * TOLERANCE - 100, 1) + pulse(10000, 0)
    outside = pulse(10000, 0) + pulse(2000 * TOLERANCE + 500, 1) + pulse(10000, 0)
    assert measure(c, inside, DT)[1]["X"] and not measure(c, inside, DT)[2]
    assert not measure(c, outside, DT)[1]["X"] and measure(c, outside, DT)[2]["high"]


def test_after_pairs_a_low_with_the_high_before_it():
    # SK6812: T1L 450-750 and T0L 750-1050 touch; a 900ns low after a 1-bit is a
    # T1L violation, not a clean T0L
    from hwcontract.contracts import bundled_contract
    from hwcontract.judge import load_contract
    c = load_contract(bundled_contract("sk6812"))
    s = pulse(90000, 0) + pulse(600, 1) + pulse(900, 0) + pulse(300, 1) + pulse(900, 0)
    s += pulse(600, 1) + pulse(90000, 0) + [1]
    _, pulses, _ = measure(c, s, DT)
    assert [p["width_ns"] for p in pulses["T1L"]] == [917] and len(pulses["T0L"]) == 1


def test_classic_names_need_no_annotation():
    c = timing({"name": "T0H", "min": 250, "typ": 400, "max": 550},
               {"name": "T1L", "min": 300, "typ": 450, "max": 600},
               {"name": "RESET", "min": 50000, "typ": 50000, "max": None})
    assert measures(c) == {"T0H": ("high", None), "T1L": ("low", None), "RESET": ("low", None)}


def test_period_edges_time_rising_edge_to_rising_edge():
    from hwcontract.contracts import bundled_contract
    from hwcontract.judge import load_contract
    fan = load_contract(bundled_contract("pwm-fan"))
    s = pulse(5000, 0) + (pulse(10000, 1) + pulse(30000, 0)) * 5 + [1]   # 25kHz
    results, ok, _, _ = judge_samples(fan, s, SR)
    assert ok and results[0]["count"] == 5
    s = pulse(5000, 0) + (pulse(400000, 1) + pulse(600000, 0)) * 3 + [1]   # 1kHz: whines
    results, ok, _, _ = judge_samples(fan, s, SR)
    assert not ok and results[-1]["edge"] == "unmatched period"


def test_a_period_never_spans_a_frame_gap():
    # an LED-style part with a bit-period rule: the reset between frames is
    # RESET's to judge, not one very long bit period
    part = timing({"name": "T0H", "min": 400, "typ": 500, "max": 600},
                  {"name": "T0L", "min": 1900, "typ": 2000, "max": 2100},
                  {"name": "BIT", "measure": "period", "min": 2200, "typ": 2500, "max": 2800},
                  {"name": "RESET", "min": 24000, "typ": 24000, "max": None})
    frame = (pulse(500, 1) + pulse(2000, 0)) * 3
    s = pulse(50000, 0) + frame + pulse(50000, 0) + frame + pulse(50000, 0) + [1]
    results, ok, _, _ = judge_samples(part, s, SR)
    assert ok, results
    assert {r["edge"]: r for r in results}["BIT"]["count"] == 4    # 2 per frame, none across


def test_a_period_is_located_where_it_starts():
    part = timing({"name": "T0H", "min": 400, "typ": 500, "max": 600},
                  {"name": "T0L", "min": 1900, "typ": 2000, "max": 2100},
                  {"name": "BIT", "measure": "period", "min": 2200, "typ": 2500, "max": 2800},
                  {"name": "RESET", "min": 24000, "typ": 24000, "max": None})
    s = (pulse(50000, 0) + pulse(500, 1) + pulse(2000, 0) + pulse(500, 1) + pulse(4000, 0)
         + pulse(500, 1) + pulse(50000, 0) + [1])
    results, ok, _, _ = judge_samples(part, s, SR)
    loc = {r["edge"]: r for r in results}["BIT"]["locations"][0]
    assert not ok and loc["width_ns"] == 4500
    assert (loc["frame"], loc["highs_before"]) == (0, 1)          # starts at the second high


def test_partial_runs_at_the_capture_ends_are_not_judged():
    c = timing({"name": "H", "measure": "high", "min": 1000, "typ": 1500, "max": 2000},
               {"name": "L", "measure": "low", "min": 1000, "typ": 1500, "max": 2000})
    s = pulse(300, 1) + (pulse(1500, 0) + pulse(1500, 1)) * 3 + pulse(200, 0)
    results, ok, _, _ = judge_samples(c, s, SR)
    assert ok, results


def test_the_starting_idle_is_the_reset_only_when_there_is_no_other():
    c = timing({"name": "T0H", "min": 250, "typ": 400, "max": 550},
               {"name": "T0L", "min": 700, "typ": 850, "max": 1000},
               {"name": "RESET", "min": 50000, "typ": 50000, "max": None})
    frame = (pulse(400, 1) + pulse(850, 0)) * 3 + pulse(400, 1)
    results, ok, _, _ = judge_samples(c, pulse(80000, 0) + frame + [0], SR)   # one frame
    assert ok and {r["edge"]: r for r in results}["RESET"]["count"] == 1
    results, ok, _, _ = judge_samples(c, pulse(80000, 0) + frame + pulse(60000, 0) + frame + [0], SR)
    reset = {r["edge"]: r for r in results}["RESET"]
    assert ok and reset["count"] == 1 and reset["actual"] == 60000
    assert reset["hint"] == "1 more cut off by the capture's start or end, not counted"


def test_an_optional_edge_may_be_absent():
    from hwcontract.contracts import bundled_contract
    from hwcontract.judge import load_contract, run
    dshot = load_contract(bundled_contract("dshot"))
    obs = [{"name": n, "value": v} for n, v in
           (("T0H", 625), ("T1H", 1250), ("T0L", 1042), ("T1L", 417))]
    results, ok = run(dshot, obs)
    assert ok and results[-1]["hint"] == "not in this capture (not required)"


def test_unmeasurable_contract_names_the_edges():
    from hwcontract.contracts import bundled_contract
    from hwcontract.judge import load_contract
    with pytest.raises(ValueError, match="PULSE_MIN, PULSE_NEUTRAL, PULSE_MAX, FRAME"):
        measures(load_contract(bundled_contract("servo")))


@pytest.mark.parametrize("edge, diagnostic", [
    ({"measure": "width"}, "measure must be one of high, low, period"),
    ({"after": "NOPE"}, "after must name another edge"),
    ({"after": "A"}, "after must name another edge"),
    ({"measure": "period", "after": "B"}, "a period edge cannot have after"),
    ({"required": "no"}, "required must be true or false"),
])
def test_measure_keys_are_validated(edge, diagnostic):
    with pytest.raises(ContractError, match=diagnostic):
        timing({"name": "A", "min": 1, "typ": 2, "max": 3, **edge},
               {"name": "B", "measure": "high", "min": 1, "typ": 2, "max": 3})
