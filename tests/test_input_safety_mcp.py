"""Input safety through the MCP boundary; invalid calls must not stop serving."""
import io
import json
import os

import pytest

from hwcontract import server
from hwcontract.judge import FAIL, SERIAL_CAP


def request(name, contract, **arguments):
    return json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                       "params": {"name": name, "arguments": {
                           "contract_path": os.path.join(server.EXAMPLES, contract + ".contract.yaml"),
                           **arguments}}})


@pytest.mark.parametrize("bad,diagnostic", [
    (request("judge_serial", "ws2812b", log="panic"), "expected a serial contract"),
    (request("judge_contract", "boot", observations=[]), "expected a timing contract"),
    (request("judge_events", "ws2812b", events=[]), "expected an events contract"),
    (request("judge_serial", "boot", log="boot v3\nIMU init OK\n" + "x" * SERIAL_CAP + "panic"),
     "exceeds limit"),
])
def test_invalid_capture_errors_and_next_valid_call_passes(bad, diagnostic):
    output = io.StringIO()
    server.serve(io.StringIO(bad + "\n" + request("judge_serial", "boot",
                                                 log="boot v3\nIMU init OK\n")), output)
    rejected, recovered = [json.loads(line)["result"] for line in output.getvalue().splitlines()]
    assert rejected["isError"] is True
    assert diagnostic in rejected["content"][0]["text"]
    verdict = json.loads(recovered["content"][0]["text"])
    assert verdict["verdict"] == "PASS"
    assert verdict["evidence"]["log_sha256"]


@pytest.mark.parametrize("observations,diagnostic", [
    ('[{"name": "T0H", "p50": 400, "widths": [400, NaN, NaN]}]', None),
    ('[{"name": "T0H", "value": 10}, {"name": "T0H", "value": 400}]',
     "duplicate observation name"),
])
def test_judge_contract_does_not_pass_hostile_observations(observations, diagnostic):
    # raw JSON: Python's json accepts NaN, so a client can send it
    clean = ', '.join('{"name": "%s", "value": %d}' % e for e in
                      (("T0L", 850), ("T1H", 800), ("T1L", 450), ("RESET", 60000)))
    obs = observations[:-1] + ", " + clean + "]"
    line = ('{"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": '
            '"judge_contract", "arguments": {"contract_path": "ws2812b", "observations": '
            + obs + '}}}')
    output = io.StringIO()
    server.serve(io.StringIO(line + "\n"), output)
    result = json.loads(output.getvalue())["result"]
    text = result["content"][0]["text"]
    if diagnostic:
        assert result["isError"] is True and diagnostic in text
    else:
        assert result["isError"] is False
        body = json.loads(text)
        assert body["verdict"] == "FAIL"
        assert body["results"][0]["status"] == FAIL
