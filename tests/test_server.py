"""The MCP server must survive hostile requests and confine contract paths."""
import io
import json
import os

import pytest

from hwcontract import server
from hwcontract.server import LEGACY_VERSIONS, MODERN_VERSION, _V, _CAPS, EXAMPLES, serve

HERE = os.path.dirname(os.path.abspath(__file__))
EX = os.path.join(HERE, "..", "hwcontract", "examples")


def frame(**params):
    return {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": params}


def modern_frame(**params):
    return {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {**params, "_meta": {_V: MODERN_VERSION, _CAPS: {}}}}


def serve_lines(lines):
    out = io.StringIO()
    serve(io.StringIO("\n".join(lines)), out)
    return [json.loads(l) for l in out.getvalue().splitlines()]


def call_result(resp):
    """(result, body): body is parsed JSON for ok results, raw text for isError."""
    assert "result" in resp, resp
    text = resp["result"]["content"][0]["text"]
    if resp["result"].get("isError"):
        return resp["result"], text
    return resp["result"], json.loads(text)


def test_missing_name_is_an_error_and_server_survives():
    good = "boot v3\nIMU init OK\n"
    resps = serve_lines([json.dumps(frame()),
                         json.dumps(frame(name="judge_serial",
                                          arguments={"contract_path": os.path.join(EX, "boot.contract.yaml"),
                                                     "log": good}))])
    assert resps[0]["result"]["isError"] is True
    _, body = call_result(resps[1])
    assert body["ok"] is True, "server must keep serving after a malformed call"


def test_non_object_arguments_is_an_error():
    resp = serve_lines([json.dumps(frame(name="judge_serial", arguments="oops"))])[0]
    assert resp["result"]["isError"] is True


def test_non_object_request_gets_internal_error_not_silence():
    resps = serve_lines(["[1, 2, 3]", json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})])
    assert resps[0]["error"]["code"] == -32603
    assert "tools" in resps[1]["result"]


def test_malformed_json_gets_parse_error():
    resp = serve_lines(['{"jsonrpc": broken'])[0]
    assert resp["error"]["code"] == -32700


def test_unknown_tool_is_error_result():
    resp = serve_lines([json.dumps(frame(name="nope"))])[0]
    assert resp["result"]["isError"] is True


def test_path_traversal_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "ROOT", str(tmp_path))
    secret = tmp_path.parent / "outside.yaml"
    secret.write_text("contract: x\n")
    resp = serve_lines([json.dumps(frame(name="judge_contract",
                                         arguments={"contract_path": str(secret),
                                                    "observations": []}))])[0]
    assert resp["result"]["isError"] is True
    assert "escapes allowed root" in resp["result"]["content"][0]["text"]


def test_invalid_contract_is_a_clean_tool_error(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "ROOT", str(tmp_path))
    bad = tmp_path / "bad.contract.yaml"
    bad.write_text("contract: bad\nheadroom_pct: 20\nedges:\n  - {name: E, min: 30, typ: 20, max: 10}\n")
    resp = serve_lines([json.dumps(frame(name="judge_contract",
                                         arguments={"contract_path": str(bad), "observations": []}))])[0]
    assert resp["result"]["isError"] is True
    assert "min 30 > typ 20" in resp["result"]["content"][0]["text"]


def test_bundled_contract_roundtrip():
    obs = [{"name": "T0H", "value": 350}, {"name": "T0L", "value": 800},
           {"name": "T1H", "value": 700}, {"name": "T1L", "value": 600},
           {"name": "RESET", "value": 60000}]
    resp = serve_lines([json.dumps(frame(name="judge_contract",
                                         arguments={"contract_path": os.path.join(EX, "ws2812.contract.yaml"),
                                                    "observations": obs}))])[0]
    _, body = call_result(resp)
    assert body["ok"] is True
    assert {r["status"] for r in body["results"]} <= {"PASS", "MARGINAL", "FAIL", "MISSING"}


def test_marginal_contract_fails_over_tool_verdict():
    # ws2812b contract on the same in-spec-but-rail-hugging signal -> ok False
    obs = [{"name": "T0H", "value": 333}, {"name": "T0L", "value": 792},
           {"name": "T1H", "value": 708}, {"name": "T1L", "value": 583},
           {"name": "RESET", "value": 60583}]
    resp = serve_lines([json.dumps(frame(name="judge_contract",
                                         arguments={"contract_path": os.path.join(EX, "ws2812b.contract.yaml"),
                                                    "observations": obs}))])[0]
    _, body = call_result(resp)
    assert body["ok"] is False
    statuses = {r["edge"]: r["status"] for r in body["results"]}
    assert statuses["T1L"] == "MARGINAL"


def test_initialize_handshake_unchanged():
    resp = serve_lines([json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})])[0]
    assert resp["result"]["protocolVersion"] == LEGACY_VERSIONS[0]
