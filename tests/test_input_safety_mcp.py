"""Input safety through the MCP boundary; invalid calls must not stop serving."""
import io
import json
import os

import pytest

from hwcontract import server
from hwcontract.judge import SERIAL_CAP


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
