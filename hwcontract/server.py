#!/usr/bin/env python3
"""Zero-dependency MCP server exposing the hardware-contract judge.

Speaks MCP over stdio (newline-delimited JSON-RPC 2.0) so any MCP client -
Claude Code, Codex, opencode - can call it with no pip install:

    { "mcpServers": { "hwcontract": {
        "command": "python3", "args": ["/abs/path/server.py"] } } }

Transport is stdlib only. All real work lives in judge.py / sigrok_adapter.py
(pure, tested). Add a tool = append one entry to TOOLS.  Self-test: --selftest
"""
import json
import os
import re
import sys

from hwcontract.judge import load_contract, run, run_serial, render
from hwcontract.sigrok_adapter import capture, observe
from hwcontract import serial_adapter

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "hwcontract", "version": "0.1.0"}

# DShot600 pulse-width thresholds for the shared timing adapter (ns).
DSHOT600 = {"high_split": 937, "low_split": 730, "reset_ns": 1800}

# ---- security boundary: this server is called by an LLM that can be prompt-
# injected, so every argument is treated as hostile ------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = os.path.join(_HERE, "examples")     # bundled example contracts, found regardless of cwd
# Contracts/paths are confined here (default: the working dir where the agent runs,
# i.e. the user's project). Override with HWCONTRACT_ROOT. Traversal outside -> rejected.
ROOT = os.path.realpath(os.environ.get("HWCONTRACT_ROOT", os.getcwd()))
MAX_SAMPLES, MAX_SECONDS, MAX_SR = 5_000_000, 60, 500_000_000
_TOKEN = re.compile(r"^[A-Za-z0-9:_./=-]{1,64}$")   # driver/channel/port charset

# Kill switch: set HWCONTRACT_SAFE=1, or drop a file named KILLSWITCH next to this
# server, to make every hardware-touching tool refuse. Pure judge tools still work.
_KILLSWITCH = os.path.join(_HERE, "KILLSWITCH")


def _guard():
    if os.environ.get("HWCONTRACT_SAFE") == "1" or os.path.exists(_KILLSWITCH):
        raise RuntimeError("hardware disabled by kill switch (HWCONTRACT_SAFE / KILLSWITCH)")


def _path(p):
    """Confine a contract path to ROOT — block traversal / arbitrary file read."""
    full = os.path.realpath(p if os.path.isabs(p) else os.path.join(ROOT, p))
    if full != ROOT and not full.startswith(ROOT + os.sep):
        raise ValueError(f"path escapes allowed root: {p}")
    return full


def _tok(name, v):
    """Charset-validate a value handed to sigrok-cli / pyserial (arg-injection defense)."""
    if not _TOKEN.match(str(v)):
        raise ValueError(f"invalid {name}: {v!r}")
    return str(v)


def _int(v, hi, lo=1):
    return max(lo, min(int(v), hi))


# ---- tool implementations (thin wrappers over the pure core) ----------------

def _summarize(results, ok):
    return {"ok": ok, "results": results, "table": render(results)}


def tool_judge_contract(contract_path, observations):
    return _summarize(*run(load_contract(_path(contract_path)), observations))


def tool_judge_serial(contract_path, log):
    return _summarize(*run_serial(load_contract(_path(contract_path)), log))


def _capture(driver, channel, samplerate, samples):
    _guard()
    sr = _int(samplerate, MAX_SR)
    return observe(capture(_tok("driver", driver), _tok("channel", channel),
                           sr, _int(samples, MAX_SAMPLES)), 1e9 / sr), sr


def tool_capture_ws2812(driver="fx2lafw", channel="D0",
                        samplerate=24_000_000, samples=200_000):
    obs, _ = _capture(driver, channel, samplerate, samples)
    return {"observations": obs}


def tool_check_ws2812(contract_path, driver="fx2lafw", channel="D0",
                      samplerate=24_000_000, samples=200_000):
    obs, _ = _capture(driver, channel, samplerate, samples)
    return _summarize(*run(load_contract(_path(contract_path)), obs))


def tool_check_dshot(contract_path, driver="fx2lafw", channel="D0",
                     samplerate=24_000_000, samples=200_000):
    _guard()
    sr = _int(samplerate, MAX_SR)
    obs = observe(capture(_tok("driver", driver), _tok("channel", channel),
                          sr, _int(samples, MAX_SAMPLES)), 1e9 / sr, **DSHOT600)
    return _summarize(*run(load_contract(_path(contract_path)), obs))


def tool_check_serial(contract_path, port, baud=115200, seconds=3.0):
    _guard()
    log = serial_adapter.capture(_tok("port", port), _int(baud, 4_000_000, 50),
                                 min(float(seconds), MAX_SECONDS))
    return _summarize(*run_serial(load_contract(_path(contract_path)), log))


_NUM = {"type": "number"}
_STR = {"type": "string"}
_CAP = {"driver": _STR, "channel": _STR, "samplerate": _NUM, "samples": _NUM}

TOOLS = {
    "judge_contract": {
        "fn": tool_judge_contract,
        "description": "Judge observations against a contract -> pass/marginal/fail. "
                       "No hardware; use for replay or when an adapter already captured.",
        "schema": {"type": "object",
                   "properties": {"contract_path": _STR,
                                  "observations": {"type": "array", "items": {"type": "object"}}},
                   "required": ["contract_path", "observations"]},
    },
    "capture_ws2812": {
        "fn": tool_capture_ws2812,
        "description": "Capture the WS2812 data line via sigrok and return measured "
                       "pulse-width observations (T0H/T1H/...).",
        "schema": {"type": "object", "properties": dict(_CAP)},
    },
    "check_ws2812": {
        "fn": tool_check_ws2812,
        "description": "Capture a live WS2812 signal AND judge it against a contract in "
                       "one call. The one-shot an agent reaches for.",
        "schema": {"type": "object",
                   "properties": {"contract_path": _STR, **_CAP},
                   "required": ["contract_path"]},
    },
    "check_dshot": {
        "fn": tool_check_dshot,
        "description": "Capture a live DShot600 ESC signal AND judge it against a contract "
                       "in one call.",
        "schema": {"type": "object",
                   "properties": {"contract_path": _STR, **_CAP},
                   "required": ["contract_path"]},
    },
    "judge_serial": {
        "fn": tool_judge_serial,
        "description": "Judge a captured serial log against expect/forbid patterns. "
                       "No hardware; use for replay or logs another tool already captured.",
        "schema": {"type": "object",
                   "properties": {"contract_path": _STR, "log": _STR},
                   "required": ["contract_path", "log"]},
    },
    "check_serial": {
        "fn": tool_check_serial,
        "description": "Read a serial port for N seconds AND judge its log against a "
                       "contract's expect/forbid patterns.",
        "schema": {"type": "object",
                   "properties": {"contract_path": _STR, "port": _STR,
                                  "baud": _NUM, "seconds": _NUM},
                   "required": ["contract_path", "port"]},
    },
}


# ---- MCP stdio transport (JSON-RPC 2.0, newline-delimited) -------------------

def _handle(msg):
    """Return a response dict, or None for notifications."""
    method, mid = msg.get("method"), msg.get("id")

    if method == "initialize":
        return _ok(mid, {"protocolVersion": PROTOCOL_VERSION,
                         "capabilities": {"tools": {}},
                         "serverInfo": SERVER_INFO})
    if method == "tools/list":
        return _ok(mid, {"tools": [{"name": n, "description": t["description"],
                                    "inputSchema": t["schema"]}
                                   for n, t in TOOLS.items()]})
    if method == "tools/call":
        name = msg["params"]["name"]
        args = msg["params"].get("arguments", {})
        tool = TOOLS.get(name)
        if tool is None:
            return _text(mid, f"unknown tool: {name}", is_error=True)
        try:
            result = tool["fn"](**args)
            return _text(mid, json.dumps(result, indent=2))
        except Exception as e:                          # tool errors are results, not protocol errors
            return _text(mid, f"{type(e).__name__}: {e}", is_error=True)
    if mid is None:                                     # a notification (e.g. initialized)
        return None
    return {"jsonrpc": "2.0", "id": mid,
            "error": {"code": -32601, "message": f"method not found: {method}"}}


def _ok(mid, result):
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _text(mid, text, is_error=False):
    return _ok(mid, {"content": [{"type": "text", "text": text}], "isError": is_error})


def serve(stdin, stdout):
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            _emit(stdout, {"jsonrpc": "2.0", "id": None,
                           "error": {"code": -32700, "message": "parse error"}})
            continue
        resp = _handle(msg)
        if resp is not None:
            _emit(stdout, resp)


def _emit(stdout, obj):
    stdout.write(json.dumps(obj) + "\n")
    stdout.flush()


# ---- MCP HTTP transport (for remote-only clients, e.g. ChatGPT connectors) ----
# Minimal Streamable-HTTP: one POST endpoint, JSON-RPC in -> JSON-RPC out. No SSE
# (these tools are request/response, no server-initiated messages). Binds localhost;
# set HWCONTRACT_TOKEN to require `Authorization: Bearer <token>` before exposing it.

def serve_http(port, host="127.0.0.1"):
    import hmac
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    token = os.environ.get("HWCONTRACT_TOKEN")
    expected = f"Bearer {token}" if token else None
    MAX_BODY = 8 * 1024 * 1024                       # cap request body to bound memory (DoS)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            # constant-time auth check (no token-length/prefix timing oracle)
            if expected and not hmac.compare_digest(self.headers.get("Authorization", ""), expected):
                self.send_error(401, "unauthorized")
                return
            try:
                length = int(self.headers.get("Content-Length", 0) or 0)
            except ValueError:
                length = -1
            if length < 0 or length > MAX_BODY:
                self.send_error(413, "payload too large")
                return
            try:
                msg = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, json.JSONDecodeError):
                return self._send(200, {"jsonrpc": "2.0", "id": None,
                                        "error": {"code": -32700, "message": "parse error"}})
            resp = _handle(msg)
            if resp is None:                            # notification -> 202, no body
                self.send_response(202)
                self.end_headers()
            else:
                self._send(200, resp)

        def _send(self, code, obj):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"hwcontract MCP on http://{host}:{port}  (auth: {'on' if token else 'OFF'})", file=sys.stderr)
    srv.serve_forever()


def selftest():
    """Drive initialize/tools/list/tools/call in-process, no hardware, no cwd dependency."""
    global ROOT
    import io
    from hwcontract.sigrok_adapter import synth, observe as _obs
    ROOT = os.path.realpath(EXAMPLES)           # confine to the bundled examples, wherever installed
    dt = 1e9 / 24_000_000
    obs = _obs(synth(dt), dt)

    dshot_obs = [{"name": "T0H", "value": 625}, {"name": "T1H", "value": 1250},
                 {"name": "T0L", "value": 1042}, {"name": "T1L", "value": 417}]
    good_log = "boot v3\nsensor warmup\nIMU init OK\nready\n"

    def call(i, name, **args):
        return {"jsonrpc": "2.0", "id": i, "method": "tools/call",
                "params": {"name": name, "arguments": args}}

    def ex(name):
        return os.path.join(EXAMPLES, name)     # absolute path into the bundled examples

    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},   # no id -> no response
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
        call(3, "judge_contract", contract_path=ex("ws2812.contract.yaml"), observations=obs),
        call(4, "judge_contract", contract_path=ex("dshot.contract.yaml"), observations=dshot_obs),
        call(5, "judge_serial", contract_path=ex("boot.contract.yaml"), log=good_log),
        call(6, "capture_ws2812"),                  # hardware -> should error cleanly (no sigrok here)
    ]
    out = io.StringIO()
    serve(io.StringIO("\n".join(json.dumps(m) for m in msgs)), out)
    r = {m["id"]: m for m in (json.loads(l) for l in out.getvalue().splitlines())}

    assert len(r) == 6                              # the notification produced no response
    assert r[1]["result"]["protocolVersion"] == PROTOCOL_VERSION
    assert {t["name"] for t in r[2]["result"]["tools"]} == set(TOOLS)
    assert json.loads(r[3]["result"]["content"][0]["text"])["ok"] is True    # WS2812 in-spec
    assert json.loads(r[4]["result"]["content"][0]["text"])["ok"] is True    # DShot  in-spec
    assert json.loads(r[5]["result"]["content"][0]["text"])["ok"] is True    # boot log clean
    assert r[6]["result"]["isError"] is True        # hardware tool fails gracefully, no crash
    print(render(json.loads(r[5]["result"]["content"][0]["text"])["results"]))
    print("\nself-check OK")


def main(argv=None):
    argv = sys.argv if argv is None else argv
    if "--selftest" in argv:
        selftest()
    elif "--http" in argv:
        serve_http(int(argv[argv.index("--http") + 1]))
    else:
        try:
            serve(sys.stdin, sys.stdout)                 # default: stdio
        except (KeyboardInterrupt, BrokenPipeError):
            pass


if __name__ == "__main__":
    main()
