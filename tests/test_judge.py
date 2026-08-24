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
