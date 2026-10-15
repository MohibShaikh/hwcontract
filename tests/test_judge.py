"""Verdict semantics and contract validation."""
import math

import pytest

from hwcontract.judge import (FAIL, MISSING, MARGINAL, PASS, ContractError,
                              judge, load_contract, run, validate_contract)

BASE_EDGES = [
    {"name": "T0H", "min": 250, "typ": 400, "max": 550},
    {"name": "RESET", "min": 50000, "typ": 50000, "max": None},
]


def timing_contract(**over):
    c = {"contract": "t", "headroom_pct": 20, "edges": [dict(e) for e in BASE_EDGES]}
    c.update(over)
    return c


def obs(name, value):
    return {"name": name, "value": value}


# ---- verdict semantics -------------------------------------------------------

def test_in_spec_passes():
    results, ok = run(timing_contract(), [obs("T0H", 400), obs("RESET", 60000)])
    assert ok is True
    assert [r["status"] for r in results] == [PASS, PASS]


def test_out_of_spec_fails_with_direction():
    _, _ = run(timing_contract(), [])
    status, hint = judge(BASE_EDGES[0], obs("T0H", 200), 20)
    assert status == FAIL and "short" in hint and "400" in hint
    status, hint = judge(BASE_EDGES[0], obs("T0H", 700), 20)
    assert status == FAIL and "long" in hint


def test_marginal_fails_the_overall_verdict():
    results, ok = run(timing_contract(), [obs("T0H", 520), obs("RESET", 60000)])
    assert ok is False, "marginal is a fail: in spec but rail-hugging"
    assert results[0]["status"] == MARGINAL
    assert results[1]["status"] == PASS


def test_missing_edge_fails():
    results, ok = run(timing_contract(), [obs("T0H", 400)])
    assert ok is False
    assert results[1]["status"] == MISSING


def test_boundary_values_are_marginal_not_pass():
    for v in (250, 550):
        status, hint = judge(BASE_EDGES[0], obs("T0H", v), 20)
        assert status == MARGINAL, f"on the rail at {v}"


def test_unbounded_max_never_fails_high():
    assert judge(BASE_EDGES[1], obs("RESET", 4_000_000), 20)[0] == PASS


def test_non_finite_measurement_fails():
    assert judge(BASE_EDGES[0], obs("T0H", float("nan")), 20)[0] == FAIL
    assert judge(BASE_EDGES[0], obs("T0H", float("inf")), 20)[0] == FAIL


def test_headroom_threshold_is_fraction_of_window():
    # window 250..550 (300 wide), headroom 20 -> 60ns rail zone
    assert judge(BASE_EDGES[0], obs("T0H", 311), 20)[0] == PASS    # 61ns from min
    assert judge(BASE_EDGES[0], obs("T0H", 309), 20)[0] == MARGINAL  # 59ns from min


# ---- validation --------------------------------------------------------------

def expect_error(c, fragment):
    with pytest.raises(ContractError) as e:
        validate_contract(c)
    assert fragment in str(e.value)


def test_rejects_non_mapping():
    expect_error([{"name": "T0H"}], "must be a mapping")


def test_rejects_missing_name():
    expect_error({"headroom_pct": 20, "edges": BASE_EDGES}, "'contract' must be a non-empty string")


def test_rejects_missing_headroom():
    expect_error({"contract": "t", "edges": BASE_EDGES}, "headroom_pct must be a number")


def test_rejects_headroom_out_of_range():
    expect_error(timing_contract(headroom_pct=150), "headroom_pct must be <= 100")
    expect_error(timing_contract(headroom_pct=-1), "headroom_pct must be >= 0")


def test_rejects_min_gt_typ():
    expect_error(timing_contract(edges=[{"name": "E", "min": 500, "typ": 400, "max": 600}]),
                 "min 500 > typ 400")


def test_rejects_typ_gt_max():
    expect_error(timing_contract(edges=[{"name": "E", "min": 100, "typ": 700, "max": 600}]),
                 "typ 700 > max 600")


def test_rejects_duplicate_edge_names():
    dup = [{"name": "T0H", "min": 1, "typ": 2, "max": 3}] * 2
    expect_error(timing_contract(edges=dup), "duplicate edge name")


def test_rejects_non_finite_and_negative():
    expect_error(timing_contract(edges=[{"name": "E", "min": 0, "typ": float("nan"), "max": 5}]),
                 "must be finite")
    expect_error(timing_contract(edges=[{"name": "E", "min": -5, "typ": 0, "max": None}]),
                 "must be >= 0")


def test_rejects_bool_as_number():
    expect_error(timing_contract(edges=[{"name": "E", "min": True, "typ": 2, "max": 3}]),
                 "must be a number")


def test_rejects_unknown_keys():
    expect_error(timing_contract(edgez=BASE_EDGES), "unknown keys")
    expect_error(timing_contract(edges=[{"name": "E", "min": 1, "typ": 2, "max": 3, "unit": "us"}]),
                 "unknown keys")


def test_rejects_wrong_unit():
    expect_error(timing_contract(unit="us"), "unit must be 'ns'")


def test_rejects_bad_kind_and_empty_edges():
    expect_error(timing_contract(kind="pwm"), "kind must be")
    expect_error(timing_contract(edges=[]), "non-empty list")


def test_rejects_bad_regex_in_serial_contract():
    expect_error({"contract": "b", "kind": "serial", "expect": ["unbalanced("]},
                 "bad regex")


def test_rejects_serial_contract_without_patterns():
    expect_error({"contract": "b", "kind": "serial"}, "at least one expect or forbid")


def test_rejects_non_string_patterns():
    expect_error({"contract": "b", "kind": "serial", "expect": [42]}, "list of regex strings")


def test_validation_collects_every_problem():
    with pytest.raises(ContractError) as e:
        validate_contract({"contract": "x", "headroom_pct": 999, "edges": [], "junk": 1})
    msg = str(e.value)
    assert "headroom_pct" in msg and "non-empty list" in msg and "unknown keys" in msg


def test_word_boundary_regex_is_the_doubled_backslash_form():
    c = {"contract": "b", "kind": "serial", "forbid": ["\\bnan\\b"], "expect": ["boot v\\d+"]}
    validate_contract(c)  # compiles, so it loads


def test_load_contract_rejects_bad_file(tmp_path):
    p = tmp_path / "bad.contract.yaml"
    p.write_text("contract: bad\nheadroom_pct: 20\nedges:\n  - {name: E, min: 30, typ: 20, max: 10}\n")
    with pytest.raises(ContractError):
        load_contract(str(p))


def test_load_contract_accepts_bundled_examples():
    import os
    ex = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "hwcontract", "examples")
    import glob
    for path in sorted(glob.glob(os.path.join(ex, "*.contract.yaml"))):
        validate = load_contract(path)  # must not raise for any bundled contract
        assert validate["contract"]


@pytest.mark.parametrize("actual,expected", [
    (actual, expected) for actual in ("timing", "serial", "events")
    for expected in ("timing", "serial", "events") if actual != expected
])
def test_each_judge_rejects_other_contract_kinds_before_reading_input(actual, expected):
    from hwcontract.judge import run_serial
    from hwcontract.temporal import judge_events
    contracts = {"timing": timing_contract(),
                 "serial": {"kind": "serial", "expect": ["boot"]},
                 "events": {"kind": "events", "assertions": [
                     {"name": "a", "when": "ping", "require": "pong"}]}}
    judges = {"timing": run, "serial": run_serial, "events": judge_events}
    # None is deliberately invalid measurement input: kind errors come first.
    with pytest.raises(ContractError, match=f"expected an? {expected} contract"):
        judges[expected](contracts[actual], None)


def test_metadata_free_inline_contracts_are_supported_without_mutation():
    from hwcontract.judge import run_serial
    from hwcontract.temporal import judge_events
    timing = {"headroom_pct": 20, "edges": BASE_EDGES}
    assert run(timing, [obs("T0H", 400), obs("RESET", 60000)])[1]
    serial = {"expect": ["boot"], "forbid": ["panic"]}
    assert run_serial(serial, "boot")[1]
    events = {"assertions": [{"name": "a", "when": "ping", "require": "pong"}]}
    assert judge_events(events, [{"type": "ping", "start_ns": 0},
                                 {"type": "pong", "start_ns": 1}])[1]
    assert all("kind" not in c and "contract" not in c for c in (timing, serial, events))
    with pytest.raises(ContractError, match="expected a serial contract"):
        run_serial(timing, "panic")


def test_direct_judges_validate_malformed_contracts():
    from hwcontract.judge import run_serial
    from hwcontract.temporal import judge_events
    with pytest.raises(ContractError) as exc:
        run({"headroom_pct": -1, "edges": []}, [])
    assert "headroom_pct" in str(exc.value) and "non-empty list" in str(exc.value)
    with pytest.raises(ContractError, match="at least one expect or forbid"):
        run_serial({"kind": "serial"}, "panic")
    with pytest.raises(ContractError, match="'forbid' needs 'while' or 'before'"):
        judge_events({"kind": "events", "assertions": [
            {"name": "a", "when": "ping", "forbid": "pong"}]}, [])


def test_unbounded_edge_with_omitted_max_can_be_judged():
    c = timing_contract(edges=[{"name": "E", "min": 1, "typ": 2}])
    assert run(c, [{"name": "E", "value": 3, "widths": [3]}])[1]


def test_malformed_yaml_is_a_contract_error(tmp_path):
    p = tmp_path / "bad.yaml"
    p.write_text("contract: [broken\n")
    with pytest.raises(ContractError, match="invalid contract YAML"):
        load_contract(p)


def test_malformed_contract_key_types_and_large_durations_report_problems():
    with pytest.raises(ContractError) as exc:
        validate_contract({"contract": "t", "kind": "events", 1: True, "junk": 1,
                           "assertions": [{"name": "a", "when": "ping", "require": "pong",
                                           "within": ["1e308s", "bad"]}]})
    message = str(exc.value)
    assert "unknown keys" in message
    assert "within[0]" in message and "within[1]" in message
    with pytest.raises(ContractError, match="must be finite"):
        run(timing_contract(headroom_pct=10 ** 1000), [])
