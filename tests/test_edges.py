"""Raw multi-channel edge extraction (edges.py)."""
import os

import pytest

from hwcontract.edges import edge_events, read_multi_csv
from hwcontract.temporal import judge_events
from hwcontract.judge import load_contract

HERE = os.path.dirname(os.path.abspath(__file__))


def test_edges_extract_rising_and_falling():
    events = edge_events({"gpio.cs": [1, 1, 0, 0, 1, 1]}, dt_ns=10)
    assert [(e["type"], e["start_ns"], e["fields"]["value"]) for e in events] == \
        [("falling", 20, 0), ("rising", 40, 1)]


def test_edges_are_sorted_across_channels():
    events = edge_events({"a": [0, 1, 0], "b": [0, 0, 1]}, dt_ns=5)
    assert [(e["start_ns"], e["source"], e["type"]) for e in events] == \
        [(5, "a", "rising"), (10, "a", "falling"), (10, "b", "rising")]


def test_constant_channel_yields_no_edges():
    assert edge_events({"clk": [0] * 10}, dt_ns=10) == []


def test_read_multi_csv_sigrok_shape(tmp_path):
    p = tmp_path / "cap.csv"
    p.write_text("#Time,CS,SCK,MOSI\n0,1,0,0\n1,0,0,1\n2,0,1,1\n")
    channels = read_multi_csv(str(p))
    assert channels == {"CS": [1, 0, 0], "SCK": [0, 0, 1], "MOSI": [0, 1, 1]}


def test_read_multi_csv_plain_header(tmp_path):
    p = tmp_path / "cap.csv"
    p.write_text("gpio.cs,spi.sck\n1,0\n0,1\n")
    assert read_multi_csv(str(p)) == {"gpio.cs": [1, 0], "spi.sck": [0, 1]}


def test_edges_judge_the_bundled_spi_frame_contract():
    """A clean 3-channel capture passes the bundled contract end to end."""
    dt = 10
    total = 3 * 20_000 // dt
    channels = {"gpio.cs": [1] * (total + 1), "spi.sck": [0] * (total + 1),
                "spi.mosi": [0] * (total + 1)}

    def level(ch, idx, v):
        channels[ch][idx:] = [v] * (len(channels[ch]) - idx)

    for frame_t0 in (0, 20_000):
        idx = frame_t0 // dt + 1
        level("gpio.cs", idx, 0)                           # CS asserted
        for k in range(8):
            rising = idx + (300 + k * 1000) // dt
            level("spi.mosi", rising - 10, k % 2)          # MOSI settles early
            level("spi.sck", rising, 1)
            level("spi.sck", rising + 50, 0)
        level("gpio.cs", idx + 870, 1)                     # CS released
    events = edge_events(channels, dt)
    results, ok = judge_events(
        load_contract(os.path.join(HERE, "..", "hwcontract", "examples",
                                   "spi-frame.contract.yaml")), events)
    assert ok is True
    assert results[0]["triggers"] == 16
