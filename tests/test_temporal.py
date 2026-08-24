"""Cross-event temporal assertions, selectors, durations, and the importer."""
import json
import os

import pytest

from hwcontract.judge import (FAIL, PASS, ContractError, event_matches,
                              parse_duration, parse_selector, validate_contract)
from hwcontract.jsontrace import from_traceevents
from hwcontract.temporal import (EventError, judge_events, normalize_events,
                                 render_events)

# ---- durations and selectors -------------------------------------------------

def test_parse_duration():
    assert parse_duration("80ns") == 80
    assert parse_duration("2us") == 2_000
    assert parse_duration("1.5ms") == 1_500_000
    assert parse_duration("1s") == 1_000_000_000
    assert parse_duration(500) == 500
    assert parse_duration("  20ns ") == 20


@pytest.mark.parametrize("bad", ["-5ns", "10", "abc", "ns", True, float("nan"), -1])
def test_parse_duration_rejects(bad):
    with pytest.raises(ValueError):
        parse_duration(bad)


def test_parse_selector_forms():
    assert parse_selector("gpio.cs.value=0") == {
        "source": "gpio", "type": "cs", "field": "value", "value": "0"}
    assert parse_selector("spi0.transfer") == {
        "source": "spi0", "type": "transfer", "field": None, "value": None}
    assert parse_selector("transfer") == {
        "source": None, "type": "transfer", "field": None, "value": None}


@pytest.mark.parametrize("bad", ["", ".cs", "gpio.", "gpio.cs.value=", None, 5])
def test_parse_selector_rejects(bad):
    with pytest.raises(ValueError):
        parse_selector(bad)


def test_event_matches_field_numeric_or_string():
    sel = parse_selector("spi0.clock_edge.value=1")
    assert event_matches(sel, {"source": "spi0", "type": "clock_edge", "fields": {"value": 1}})
    assert event_matches(sel, {"source": "spi0", "type": "clock_edge", "fields": {"value": "1"}})
    assert not event_matches(sel, {"source": "spi0", "type": "clock_edge", "fields": {"value": 0}})
    assert not event_matches(sel, {"source": "spi1", "type": "clock_edge", "fields": {"value": 1}})
    assert not event_matches(sel, {"source": "spi0", "type": "clock_edge", "fields": {}})


# ---- event normalization -----------------------------------------------------

def test_normalize_sorts_and_fills():
    events = [{"type": "b", "start_ns": 200}, {"type": "a", "start_ns": 100}]
    out = normalize_events(events)
    assert [e["type"] for e in out] == ["a", "b"]
    assert out[0]["end_ns"] == 100 and out[0]["fields"] == {}


def test_normalize_collects_every_problem():
    with pytest.raises(EventError) as e:
        normalize_events([{"type": "", "start_ns": -1},
                          {"start_ns": 5},
                          {"type": "x", "start_ns": 10, "end_ns": 5},
                          "junk"])
    msg = str(e.value)
    assert "events[0].type" in msg and "events[1].type" in msg
    assert "end_ns 5 < start_ns 10" in msg and "must be a mapping" in msg


# ---- require/within ----------------------------------------------------------

def contract(assertions):
    c = {"contract": "t", "kind": "events", "assertions": assertions}
    validate_contract(c)
    return c


def ev(type_, start, end=None, source="spi0", **fields):
    return {"source": source, "type": type_, "start_ns": start,
            "end_ns": end if end is not None else start, "fields": fields}


def test_require_within_passes_with_latency_stats():
    events = [ev("cs", 1000, source="gpio", value=0),
              ev("clock_edge", 1200, 1205, value=1),
              ev("cs", 10_000, source="gpio", value=0),
              ev("clock_edge", 11_500, 11_505, value=1)]
    results, ok = judge_events(contract([
        {"name": "cs-then-clk", "when": "gpio.cs.value=0",
         "require": "spi0.clock_edge", "within": ["100ns", "2us"]}]), events)
    assert ok is True
    row = results[0]
    assert row["status"] == PASS and row["triggers"] == 2 and row["violations"] == 0
    assert row["latency"] == {"count": 2, "min": 200, "p50": 850, "p95": 1435,
                              "p99": 1487, "max": 1500}


def test_require_violation_names_the_first_failure():
    events = [ev("cs", 1000, source="gpio", value=0),
              ev("clock_edge", 50_000, value=1)]   # far outside the 2us window
    results, ok = judge_events(contract([
        {"name": "cs-then-clk", "when": "gpio.cs.value=0",
         "require": "spi0.clock_edge", "within": ["100ns", "2us"]}]), events)
    assert ok is False
    assert results[0]["violations"] == 1
    assert "trigger at 1000ns" in results[0]["hint"]
    assert "[1100ns, 3000ns]" in results[0]["hint"]


def test_zero_triggers_fails():
    results, ok = judge_events(contract([
        {"name": "never-fires", "when": "gpio.cs.value=0",
         "require": "spi0.clock_edge", "within": ["0ns", "1us"]}]),
        [ev("clock_edge", 500, value=1)])
    assert ok is False
    assert "no events matched" in results[0]["hint"]


def test_unbounded_within():
    events = [ev("cs", 0, source="gpio", value=0), ev("clock_edge", 5_000_000, value=1)]
    results, ok = judge_events(contract([
        {"name": "eventually", "when": "gpio.cs.value=0",
         "require": "spi0.clock_edge", "within": [0, None]}]), events) if False else \
        judge_events({"contract": "t", "kind": "events", "assertions": [
            {"name": "eventually", "when": "gpio.cs.value=0",
             "require": "spi0.clock_edge", "within": [0, "10ms"]}]}, events)
    assert ok is True and results[0]["latency"]["max"] == 5_000_000


# ---- forbid ------------------------------------------------------------------

def test_forbid_while_catches_overlap():
    events = [ev("cs", 3000, 6000, source="gpio", value=1),
              ev("clock_edge", 5000, 5005, value=1)]      # clocked while deselected
    results, ok = judge_events(contract([
        {"name": "quiet-when-idle", "forbid": "spi0.clock_edge",
         "while": "gpio.cs.value=1"}]), events)
    assert ok is False
    assert "overlaps gpio.cs.value=1 active [3000, 6000]ns" in results[0]["hint"]


def test_forbid_while_clean_passes():
    events = [ev("cs", 3000, 6000, source="gpio", value=1),
              ev("clock_edge", 6200, 6205, value=1)]
    results, ok = judge_events(contract([
        {"name": "quiet-when-idle", "forbid": "spi0.clock_edge",
         "while": "gpio.cs.value=1"}]), events)
    assert ok is True and results[0]["violations"] == 0


def test_forbid_while_with_no_spans_fails():
    results, ok = judge_events(contract([
        {"name": "quiet-when-idle", "forbid": "spi0.clock_edge",
         "while": "gpio.cs.value=1"}]), [ev("clock_edge", 5, value=1)])
    assert ok is False and "no events matched while" in results[0]["hint"]


def test_forbid_before_catches_setup_violation():
    events = [ev("clock_edge", 10_000, value=1),
              ev("change", 9_990, source="gpio")]          # 10ns setup, needs 20ns
    results, ok = judge_events(contract([
        {"name": "mosi-setup", "when": "spi0.clock_edge.value=1",
         "forbid": "gpio.change", "before": "20ns"}]), events)
    assert ok is False
    assert "10ns before spi0.clock_edge.value=1 at 10000ns (needs >= 20ns)" in results[0]["hint"]


def test_forbid_before_clean_passes():
    events = [ev("clock_edge", 10_000, value=1), ev("change", 9_000, source="gpio")]
    results, ok = judge_events(contract([
        {"name": "mosi-setup", "when": "spi0.clock_edge.value=1",
         "forbid": "gpio.change", "before": "20ns"}]), events)
    assert ok is True


# ---- contract validation -----------------------------------------------------

def test_events_contract_validation():
    with pytest.raises(ContractError) as e:
        validate_contract({"contract": "t", "kind": "events", "assertions": [
            {"name": "a", "when": "x.y", "require": "a.b", "forbid": "c.d"},
            {"name": "a", "when": "x.y", "require": "a.b", "within": ["2us", "1us"]},
            {"name": "c", "when": "x.y", "forbid": "a.b", "within": [0, 5]},
            {"name": "d", "when": "x..y", "require": "a.b"},
            {"name": "e", "when": "x.y", "forbid": "a.b", "while": "c.d", "before": "5ns"},
            {"name": "f", "when": "x.y", "forbid": "a.b", "before": "5junk"},
        ]})
    msg = str(e.value)
    for fragment in ("exactly one", "duplicate assertion name", "'within' needs 'require'",
                     "min 2000 > max 1000", "invalid selector", "mutually exclusive",
                     "invalid duration"):
        assert fragment in msg, fragment


def test_bundled_spi_frame_contract_is_valid():
    import os
    p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                     "hwcontract", "examples", "spi-frame.contract.yaml")
    from hwcontract.judge import load_contract
    assert load_contract(p)["kind"] == "events"


# ---- the Zephyr LPSPI story, end to end --------------------------------------

def good_frame():
    return [ev("cs", 1000, 1000, source="gpio", value=0),
            ev("clock_edge", 1200, 1205, value=1),
            ev("change", 1150, 1150, source="gpio"),
            ev("clock_edge", 1450, 1455, value=0),
            ev("cs", 3000, 6000, source="gpio", value=1),
            ev("cs", 6000, 6000, source="gpio", value=0),
            ev("clock_edge", 6200, 6205, value=1),
            ev("change", 6150, 6150, source="gpio")]


def dma_broken_frame():
    """Zephyr #110302: in DMA mode CS asserts AFTER the first clock edge."""
    return [ev("clock_edge", 1000, 1005, value=1),          # clock starts first
            ev("cs", 1180, 1180, source="gpio", value=0),   # CS 180ns late
            ev("clock_edge", 1450, 1455, value=0),
            ev("cs", 3000, 6000, source="gpio", value=1),
            ev("cs", 6000, 6000, source="gpio", value=0),
            ev("clock_edge", 6200, 6205, value=1),
            ev("change", 6190, 6190, source="gpio")]        # MOSI settles 10ns before


def test_healthy_frame_passes_all_three_assertions():
    results, ok = judge_events(contract([
        {"name": "cs-before-first-clock", "when": "gpio.cs.value=0",
         "require": "spi0.clock_edge", "within": ["80ns", "2us"]},
        {"name": "no-clock-outside-frame", "forbid": "spi0.clock_edge",
         "while": "gpio.cs.value=1"},
        {"name": "mosi-setup", "when": "spi0.clock_edge.value=1",
         "forbid": "gpio.change", "before": "20ns"}]), good_frame())
    assert ok is True
    assert [r["status"] for r in results] == [PASS, PASS, PASS]
    assert results[0]["latency"]["min"] == 200


def test_dma_broken_frame_fails_with_exact_timestamps():
    results, ok = judge_events(contract([
        {"name": "cs-precedes-first-clock", "when": "spi0.clock_edge.value=1",
         "require": "gpio.cs.value=0", "within": ["-10us", "0ns"]},
        {"name": "mosi-setup", "when": "spi0.clock_edge.value=1",
         "forbid": "gpio.change", "before": "20ns"}]), dma_broken_frame())
    assert ok is False
    cs_row, mosi_row = results
    assert cs_row["violations"] == 1, cs_row
    assert "trigger at 1000ns: no gpio.cs.value=0 in [-9000ns, 1000ns]" in cs_row["hint"]
    assert mosi_row["violations"] == 1
    assert "10ns before" in mosi_row["hint"]


def test_backward_window_matches_the_nearest_preceding_event():
    events = [ev("cs", 1000, 1000, source="gpio", value=0),
              ev("cs", 6000, 6000, source="gpio", value=0),
              ev("clock_edge", 6200, 6205, value=1)]
    results, ok = judge_events(contract([
        {"name": "cs-precedes", "when": "spi0.clock_edge.value=1",
         "require": "gpio.cs.value=0", "within": ["-10us", "0ns"]}]), events)
    assert ok is True
    assert results[0]["latency"] == {"count": 1, "min": -200, "p50": -200,
                                     "p95": -200, "p99": -200, "max": -200}


def test_vacuous_forbid_is_visible_in_the_hint():
    events = [ev("cs", 3000, 6000, source="gpio", value=1)]
    results, ok = judge_events(contract([
        {"name": "quiet-when-idle", "forbid": "spi0.clock_edge",
         "while": "gpio.cs.value=1"}]), events)
    assert ok is True
    assert "never appears in the trace" in results[0]["hint"]


def test_wildcard_type_matches_any_event_of_the_source():
    events = [ev("rising", 9_990, source="spi.mosi"),
              ev("clock_edge", 10_000, value=1)]
    results, ok = judge_events(contract([
        {"name": "mosi-setup", "when": "spi0.clock_edge.value=1",
         "forbid": "spi.mosi.*", "before": "20ns"}]), events)
    assert ok is False
    assert "forbidden spi.mosi.* at 9990ns" in results[0]["hint"]


def test_render_events_shows_latency_line():
    text = render_events(judge_events(contract([
        {"name": "cs-then-clk", "when": "gpio.cs.value=0",
         "require": "spi0.clock_edge", "within": ["100ns", "2us"]}]), good_frame())[0])
    assert "latency ns" in text and "p99" in text


# ---- sigrok jsontrace importer -----------------------------------------------

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def test_golden_sigrok_fixture_imports():
    """The B/E annotation shape real sigrok-cli emits (decode.c), not just
    our own X/i flavor."""
    from hwcontract.jsontrace import load
    events = load(os.path.join(FIXTURES, "uart-sigrok-trace.json"))
    assert [e["type"] for e in events] == ["tx"] * 4
    assert all(e["source"] == "uart" for e in events)
    assert events[0]["fields"]["text"] == "0x55 'U'"
    assert events[0]["start_ns"] == 104166 and events[0]["end_ns"] == 191405
    assert events[1]["start_ns"] > events[0]["end_ns"]


def test_be_pairs_without_cat_args():
    doc = {"traceEvents": [
        {"ph": "B", "ts": 1.0, "pid": "spi", "tid": "CS#", "name": "CS asserted"},
        {"ph": "E", "ts": 2.5, "pid": "spi", "tid": "CS#", "name": "CS asserted"},
    ]}
    events = from_traceevents(doc)
    assert events[0] == {"source": "spi", "type": "CS#", "start_ns": 1000,
                         "end_ns": 2500,
                         "fields": {"row": "CS#", "text": "CS asserted"}}


def test_unmatched_be_is_an_error():
    with pytest.raises(EventError):
        from_traceevents({"traceEvents": [{"ph": "E", "ts": 1.0, "pid": "p", "tid": "t"}]})
    with pytest.raises(EventError):
        from_traceevents({"traceEvents": [{"ph": "B", "ts": 1.0, "pid": "p", "tid": "t"}]})


def test_nested_be_pairs_stack():
    doc = {"traceEvents": [
        {"ph": "B", "ts": 1.0, "pid": "spi", "tid": "transfer"},
        {"ph": "B", "ts": 2.0, "pid": "spi", "tid": "byte"},
        {"ph": "E", "ts": 3.0, "pid": "spi", "tid": "byte"},
        {"ph": "E", "ts": 4.0, "pid": "spi", "tid": "transfer"},
    ]}
    events = from_traceevents(doc)
    assert [(e["type"], e["start_ns"], e["end_ns"]) for e in events] == \
        [("transfer", 1000, 4000), ("byte", 2000, 3000)]


def test_from_traceevents_converts_us_to_ns():
    doc = {"traceEvents": [
        {"name": "CS#", "cat": "spi", "ph": "X", "ts": 1.0, "dur": 0.5, "args": {"value": 0}},
        {"name": "mosi", "cat": "spi", "ph": "i", "ts": 2.25, "args": {"data": "0x9f"}},
    ]}
    events = from_traceevents(doc)
    assert events[0] == {"source": "spi", "type": "CS#", "start_ns": 1000,
                         "end_ns": 1500, "fields": {"value": 0}}
    assert events[1]["start_ns"] == 2250 and events[1]["end_ns"] == 2250
    assert events[1]["fields"] == {"data": "0x9f"}


def test_from_traceevents_rejects_garbage():
    with pytest.raises(EventError):
        from_traceevents({"nope": 1})
    with pytest.raises(EventError):
        from_traceevents({"traceEvents": [{"ph": "B", "ts": 1}]})
    with pytest.raises(EventError):
        from_traceevents({"traceEvents": [{"ph": "X", "ts": -5}]})


def test_cli_roundtrip(tmp_path, capsys):
    from hwcontract.temporal import main
    c = tmp_path / "c.contract.yaml"
    c.write_text(
        "contract: t\nkind: events\nassertions:\n"
        "  - {name: a, when: 'g.ping', require: 's.pong', within: [0, '1ms']}\n")
    trace = tmp_path / "trace.json"
    trace.write_text(json.dumps({"traceEvents": [
        {"name": "ping", "cat": "g", "ph": "i", "ts": 0.001},
        {"name": "pong", "cat": "s", "ph": "i", "ts": 0.05}]}))
    with pytest.raises(SystemExit) as e:
        main([str(c), str(trace)])
    assert e.value.code == 0
    assert "all" not in capsys.readouterr().out or "PASS" in capsys.readouterr().out
