#!/usr/bin/env python3
"""Zero-dependency MCP server exposing the hardware-contract judge.

Speaks MCP over stdio (newline-delimited JSON-RPC 2.0) so any MCP client,
Claude Code, Codex, opencode can call it with no pip install:

    { "mcpServers": { "hwcontract": {
        "command": "python3", "args": ["/abs/path/server.py"] } } }

Speaks two protocol eras. In 2026-07-28 every request carries its own version
and capabilities in _meta. Older clients open with an initialize handshake
instead, and most still do. Each request picks its own era, so the server holds
no session and cannot get stuck in the wrong mode.

Transport is stdlib only. All real work lives in judge.py / sigrok_adapter.py
(pure, tested). Add a tool = append one entry to TOOLS.  Self-test: --selftest
"""
import base64
import argparse
import json
import os
import re
import sys

from hwcontract import __version__
from hwcontract.judge import (load_contract, run, run_serial, render,
                              evidence, sha256_of, validate_judge_contract)
from hwcontract.measure import measures
from hwcontract.sigrok_adapter import capture, observe
from hwcontract.temporal import judge_events, render_events
from hwcontract import serial_adapter
from hwcontract.check import check_file, judge_samples, report
from hwcontract.contracts import cli_contract, list_contracts, resolve_contract

MODERN_VERSION = "2026-07-28"
LEGACY_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")
SERVER_INFO = {"name": "hwcontract", "version": __version__}
CAPABILITIES = {"tools": {}}
INSTRUCTIONS = ("Use list_contracts to discover bundled templates and capture support. "
                "Judges accept a bundled name or a contract path under HWCONTRACT_ROOT. "
                "judge_file judges a saved capture CSV, log or trace under that root. "
                "check_/capture_ tools need sigrok or a serial port attached. A contract "
                "you write from a datasheet works the same as a bundled one.")

# _meta keys: the first two are required on every modern request, the third is
# what a modern result answers with.
_V = "io.modelcontextprotocol/protocolVersion"
_CAPS = "io.modelcontextprotocol/clientCapabilities"
_SRV = "io.modelcontextprotocol/serverInfo"

# ---- security boundary: this server is called by an LLM that can be prompt-
# injected, so every argument is treated as hostile ------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = os.path.join(_HERE, "examples")     # bundled example contracts, found regardless of cwd
# Contracts/paths are confined here (default: the working dir where the agent runs,
# i.e. the user's project). Override with HWCONTRACT_ROOT. Traversal outside -> rejected.
ROOT = os.path.realpath(os.environ.get("HWCONTRACT_ROOT", os.getcwd()))
MAX_SAMPLES, MAX_SECONDS, MAX_SR = 5_000_000, 60, 500_000_000
MAX_FILE_BYTES = 64 * 1024 * 1024                  # judge_file input
_TOKEN = re.compile(r"^[A-Za-z0-9:_./=-]{1,64}$")   # driver/channel/port charset

# Kill switch: set HWCONTRACT_SAFE=1, or drop a file named KILLSWITCH next to this
# server, to make every hardware-touching tool refuse. Pure judge tools still work.
_KILLSWITCH = os.path.join(_HERE, "KILLSWITCH")


def _guard():
    if os.environ.get("HWCONTRACT_SAFE") == "1" or os.path.exists(_KILLSWITCH):
        raise RuntimeError("hardware disabled by kill switch (HWCONTRACT_SAFE / KILLSWITCH)")


def _path(p):
    """Resolve bundled contracts while confining arbitrary files to ROOT."""
    return resolve_contract(p, ROOT)


def _tok(name, v):
    """Charset-validate a value handed to sigrok-cli / pyserial. Blocks argument injection."""
    if not _TOKEN.match(str(v)):
        raise ValueError(f"invalid {name}: {v!r}")
    return str(v)


def _int(v, hi, lo=1):
    return max(lo, min(int(v), hi))


# ---- tool implementations (thin wrappers over the pure core) ----------------

def _summarize(results, ok, ev=None):
    out = {"ok": ok, "verdict": "PASS" if ok else "FAIL",
           "results": results, "table": render(results)}
    if ev:
        out["evidence"] = ev
    return out


def tool_judge_contract(contract_path, observations):
    path = _path(contract_path)
    results, ok = run(load_contract(path), observations)
    return _summarize(results, ok, evidence(path,
                                            observations_sha256=sha256_of(observations),
                                            observation_count=len(observations)))


def tool_judge_serial(contract_path, log):
    path = _path(contract_path)
    results, ok = run_serial(load_contract(path), log)
    return _summarize(results, ok, evidence(path,
                                            log_sha256=sha256_of(log),
                                            log_chars=len(log)))


def tool_judge_events(contract_path, events):
    path = _path(contract_path)
    results, ok = judge_events(load_contract(path), events)
    out = {"ok": ok, "verdict": "PASS" if ok else "FAIL", "results": results,
           "table": render_events(results),
           "evidence": evidence(path, events_sha256=sha256_of(events),
                                event_count=len(events))}
    return out


def _file(p):
    """A regular file under ROOT after symlink resolution, within the size cap."""
    if not isinstance(p, str) or not p or "\x00" in p:
        raise ValueError("path must be a non-empty string")
    real = os.path.realpath(os.path.join(ROOT, p))
    if os.path.commonpath([real, ROOT]) != ROOT:
        raise ValueError(f"path escapes allowed root: {p}")
    if not os.path.isfile(real):
        raise ValueError(f"not a regular file: {p}")
    if os.path.getsize(real) > MAX_FILE_BYTES:
        raise ValueError(f"file exceeds {MAX_FILE_BYTES} bytes: {p}")
    return real


def tool_judge_file(contract_path, path, samplerate=None):
    v, table, notes = check_file(_path(contract_path), _file(path), samplerate)
    ev = {k: v[k] for k in ("contract_sha256", "input_kind", "input_sha256", "samplerate_hz",
                            "hwcontract_version", "timestamp_utc") if k in v}
    return {"ok": v["ok"], "verdict": v["verdict"], "results": v["results"], "table": table,
            "notes": notes, "evidence": ev}


def _capture(driver, channel, samplerate, samples):
    """(samples, samplerate, capture evidence) from a live sigrok capture."""
    _guard()
    drv, chan = _tok("driver", driver), _tok("channel", channel)
    sr = _int(samplerate, MAX_SR)
    raw = capture(drv, chan, sr, _int(samples, MAX_SAMPLES))
    cap_ev = {"driver": drv, "channel": chan,
              "samplerate_hz": sr, "samples": len(raw),
              "capture_sha256": sha256_of("".join(map(str, raw)))}
    return raw, sr, cap_ev


def tool_capture_ws2812(driver="fx2lafw", channel="D0",
                        samplerate=24_000_000, samples=200_000):
    raw, sr, cap_ev = _capture(driver, channel, samplerate, samples)
    return {"observations": observe(raw, 1e9 / sr), "capture": cap_ev}


def tool_check_capture(contract_path, driver="fx2lafw", channel="D0",
                       samplerate=24_000_000, samples=200_000):
    path = _path(contract_path)
    c = load_contract(path)
    validate_judge_contract(c, "timing")
    measures(c)                                  # unmeasurable: fail before touching hardware
    raw, sr, cap_ev = _capture(driver, channel, samplerate, samples)
    results, ok, _, notes = judge_samples(c, raw, sr)
    return {**_summarize(results, ok, evidence(path, **cap_ev)), "notes": notes}


def tool_check_serial(contract_path, port, baud=115200, seconds=3.0):
    _guard()
    log = serial_adapter.capture(_tok("port", port), _int(baud, 4_000_000, 50),
                                 min(float(seconds), MAX_SECONDS))
    return _summarize(*run_serial(load_contract(_path(contract_path)), log),
                      evidence(_path(contract_path), port=_tok("port", port),
                               baud=_int(baud, 4_000_000, 50),
                               seconds=min(float(seconds), MAX_SECONDS),
                               log_sha256=sha256_of(log), log_chars=len(log)))


_NUM = {"type": "number"}
_STR = {"type": "string"}
_CAP = {"driver": _STR, "channel": _STR, "samplerate": _NUM, "samples": _NUM}

TOOLS = {
    "list_contracts": {
        "fn": list_contracts,
        "description": "List bundled contract templates, names, kinds, installed paths, "
                       "and capture adapter requirements. No hardware.",
        "schema": {"type": "object", "properties": {}},
    },
    "judge_contract": {
        "fn": tool_judge_contract,
        "description": "Judge observations against a timing contract -> PASS/MARGINAL/FAIL/MISSING. "
                       "No hardware; use for replay or when an adapter already captured.",
        "schema": {"type": "object",
                   "properties": {"contract_path": _STR,
                                  "observations": {"type": "array", "items": {"type": "object"}}},
                   "required": ["contract_path", "observations"]},
    },
    "judge_file": {
        "fn": tool_judge_file,
        "description": "Judge a saved file under HWCONTRACT_ROOT against a contract, read by "
                       "the contract's kind: a 0/1 capture CSV (needs samplerate in Hz; "
                       "measured like check_capture) or observations JSON for timing, a log "
                       "for serial, a trace JSON for events. No hardware.",
        "schema": {"type": "object",
                   "properties": {"contract_path": _STR, "path": _STR,
                                  "samplerate": {"type": ["number", "string"]}},
                   "required": ["contract_path", "path"]},
    },
    "capture_ws2812": {
        "fn": tool_capture_ws2812,
        "description": "Capture the WS2812 data line via sigrok and return measured "
                       "pulse-width observations (T0H/T1H/...).",
        "schema": {"type": "object", "properties": dict(_CAP)},
    },
    "check_capture": {
        "fn": tool_check_capture,
        "description": "Capture one live logic line via sigrok AND judge it against a timing "
                       "contract, measured the way the contract's edges say (WS2812, DShot, "
                       "PWM, or any contract with measure: high/low/period). Failing rows "
                       "locate their pulses; pulses no edge explains fail as 'unmatched'.",
        "schema": {"type": "object",
                   "properties": {"contract_path": _STR, **_CAP},
                   "required": ["contract_path"]},
    },
    "check_ws2812": {
        "fn": tool_check_capture,
        "description": "Same as check_capture; kept for existing clients.",
        "schema": {"type": "object",
                   "properties": {"contract_path": _STR, **_CAP},
                   "required": ["contract_path"]},
    },
    "check_dshot": {
        "fn": tool_check_capture,
        "description": "Same as check_capture; kept for existing clients.",
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
    "judge_events": {
        "fn": tool_judge_events,
        "description": "Judge decoded hardware events against an events contract's "
                       "temporal assertions (when/require/within, forbid/while/before). "
                       "Events: [{source, type, start_ns, end_ns, fields}].",
        "schema": {"type": "object",
                   "properties": {"contract_path": _STR,
                                  "events": {"type": "array", "items": {"type": "object"}}},
                   "required": ["contract_path", "events"]},
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


# ---- MCP dispatch (JSON-RPC 2.0) --------------------------------------------
# A _meta protocolVersion marks a modern request; without one it is a legacy
# handshake client and gets the old result shape.

def _meta_of(msg):
    if not isinstance(msg, dict):
        raise ValueError("request must be an object")
    params = msg.get("params")
    if params is None:
        return {}
    if not isinstance(params, dict):
        raise ValueError("params must be an object")
    meta = params.get("_meta")
    if meta is None:
        return {}
    if not isinstance(meta, dict):
        raise ValueError("_meta must be an object")
    return meta


def _handle(msg):
    """Return a response dict, or None for notifications."""
    meta = _meta_of(msg)
    return _modern(msg, meta) if _V in meta else _legacy(msg)


def _modern(msg, meta):
    mid, method = msg.get("id"), msg.get("method")
    if meta[_V] != MODERN_VERSION:
        return _err(mid, -32022, "unsupported protocol version",
                    {"supported": [MODERN_VERSION], "requested": meta[_V]})
    if _CAPS not in meta:
        return _err(mid, -32602, f"missing _meta {_CAPS}")
    if method == "server/discover":
        return _res(mid, {"supportedVersions": [MODERN_VERSION],
                          "capabilities": CAPABILITIES,
                          "instructions": INSTRUCTIONS})
    if method == "tools/list":
        return _res(mid, {"tools": _tools()})
    if method == "tools/call":
        return _res(mid, _call(msg.get("params") or {}))
    if mid is None:
        return None
    return _err(mid, -32601, f"method not found: {method}")


def _legacy(msg):
    mid, method = msg.get("id"), msg.get("method")
    if method == "initialize":
        asked = (msg.get("params") or {}).get("protocolVersion")
        return _ok(mid, {"protocolVersion": asked if asked in LEGACY_VERSIONS else LEGACY_VERSIONS[0],
                         "capabilities": CAPABILITIES,
                         "serverInfo": SERVER_INFO})
    if method == "tools/list":
        return _ok(mid, {"tools": _tools()})
    if method == "tools/call":
        return _ok(mid, _call(msg.get("params") or {}))
    if mid is None:                                     # a notification (e.g. initialized)
        return None
    return _err(mid, -32601, f"method not found: {method}")


def _tools():
    return [{"name": n, "description": t["description"], "inputSchema": t["schema"]}
            for n, t in TOOLS.items()]


def _call(params):
    """A tool that raises is a result with isError, not a protocol error. A malformed
    request (missing name, non-object arguments) is an error result too: the stdio
    loop must survive anything a client sends."""
    if not isinstance(params, dict):
        return _content("invalid params: expected an object with 'name'", is_error=True)
    name = params.get("name")
    tool = TOOLS.get(name) if isinstance(name, str) else None
    if tool is None:
        return _content(f"unknown tool: {name!r}", is_error=True)
    args = params.get("arguments")
    if args is None:
        args = {}
    if not isinstance(args, dict):
        return _content(f"invalid arguments for {name}: expected an object", is_error=True)
    try:
        return _content(json.dumps(tool["fn"](**args), indent=2))
    except Exception as e:
        return _content(f"{type(e).__name__}: {e}", is_error=True)


def _content(text, is_error=False):
    return {"content": [{"type": "text", "text": text}], "isError": is_error}


def _ok(mid, result):
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _res(mid, result):
    """Modern results must declare resultType and should carry serverInfo."""
    return _ok(mid, {"resultType": "complete", "_meta": {_SRV: SERVER_INFO}, **result})


def _err(mid, code, message, data=None):
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": mid, "error": err}


# ---- MCP stdio transport (newline-delimited) --------------------------------

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
        try:
            resp = _handle(msg)
        except Exception as e:                  # a hostile/broken request must never kill the loop
            mid = msg.get("id") if isinstance(msg, dict) else None
            resp = {"jsonrpc": "2.0", "id": mid,
                    "error": {"code": -32603, "message": f"internal error: {type(e).__name__}: {e}"}}
        if resp is not None:
            _emit(stdout, resp)


def _emit(stdout, obj):
    stdout.write(json.dumps(obj) + "\n")
    stdout.flush()


# ---- MCP HTTP transport (for remote-only clients, e.g. ChatGPT connectors) ----
# Streamable HTTP: one POST endpoint, JSON-RPC in -> JSON-RPC out. No SSE (these
# tools are request/response, no server-initiated messages), no sessions and no GET
# stream, both of which 2026-07-28 dropped. Binds localhost; set HWCONTRACT_TOKEN to
# require `Authorization: Bearer <token>` before exposing it.

# Error code -> HTTP status, for modern requests only. Legacy clients expect their
# JSON-RPC errors wrapped in a 200 and would read a 4xx as a dead endpoint.
HTTP_STATUS = {-32020: 400, -32021: 400, -32022: 400, -32602: 400, -32601: 404, -32603: 500}


def _unsentinel(v):
    """Header values that cannot be plain ASCII arrive as =?base64?...?=."""
    if v and v.startswith("=?base64?") and v.endswith("?="):
        return base64.b64decode(v[9:-2]).decode()
    return v


def _header_error(headers, msg):
    """Modern POSTs mirror body fields into headers so proxies can route without
    parsing them. A header disagreeing with the body means one of the two lied."""
    mirrored = [("MCP-Protocol-Version", msg["params"]["_meta"][_V]),
                ("Mcp-Method", msg.get("method"))]
    if msg.get("method") == "tools/call":
        mirrored.append(("Mcp-Name", (msg.get("params") or {}).get("name")))
    for header, body_value in mirrored:
        got = _unsentinel(headers.get(header))
        if got is None:
            return _err(msg.get("id"), -32020, f"missing header {header}")
        if got != body_value:
            return _err(msg.get("id"), -32020,
                        f"{header} header {got!r} does not match body {body_value!r}")
    return None


def _origin_ok(origin, host, port):
    """A wrong Origin is a web page reaching this port through the user's browser
    (DNS rebinding). No Origin at all is a non-browser client, which is normal."""
    if origin is None:
        return True
    allowed = {f"http://{h}:{port}" for h in (host, "localhost", "127.0.0.1")}
    allowed.update(o for o in os.environ.get("HWCONTRACT_ORIGINS", "").split(",") if o)
    return origin in allowed


def serve_http(port, host="127.0.0.1"):
    import hmac
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    token = os.environ.get("HWCONTRACT_TOKEN")
    expected = f"Bearer {token}" if token else None
    MAX_BODY = 8 * 1024 * 1024                       # cap request body to bound memory (DoS)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            if not _origin_ok(self.headers.get("Origin"), host, port):
                self.send_error(403, "forbidden origin")
                return
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
            modern = False
            try:
                modern = _V in _meta_of(msg)
                if modern:
                    bad = _header_error(self.headers, msg)
                    if bad:
                        return self._send(400, bad)
                resp = _handle(msg)
            except Exception as e:                  # mirror the stdio loop: answer, don't drop the connection
                resp = {"jsonrpc": "2.0", "id": msg.get("id") if isinstance(msg, dict) else None,
                        "error": {"code": -32603, "message": f"internal error: {type(e).__name__}: {e}"}}
            if resp is None:                            # notification -> 202, no body
                self.send_response(202)
                self.end_headers()
            elif modern and "error" in resp:
                self._send(HTTP_STATUS.get(resp["error"]["code"], 200), resp)
            else:
                self._send(200, resp)

        def do_GET(self):                               # the GET stream is gone as of 2026-07-28
            self.send_error(405, "method not allowed")

        do_DELETE = do_GET                              # sessions too, so nothing to delete

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
    """Drive both protocol eras in-process, no hardware, no cwd dependency."""
    import io
    from hwcontract.sigrok_adapter import synth, observe as _obs
    dt = 1e9 / 24_000_000
    obs = _obs(synth(dt), dt)

    dshot_obs = [{"name": "T0H", "value": 625}, {"name": "T1H", "value": 1250},
                 {"name": "T0L", "value": 1042}, {"name": "T1L", "value": 417}]
    good_log = "boot v3\nsensor warmup\nIMU init OK\nready\n"

    def call(i, name, **args):
        return {"jsonrpc": "2.0", "id": i, "method": "tools/call",
                "params": {"name": name, "arguments": args}}

    def modern(i, method, **params):
        return {"jsonrpc": "2.0", "id": i, "method": method,
                "params": {**params, "_meta": {_V: MODERN_VERSION, _CAPS: {}}}}

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
        modern(7, "server/discover"),
        modern(8, "tools/call", name="judge_serial",
               arguments={"contract_path": ex("boot.contract.yaml"), "log": good_log}),
        {"jsonrpc": "2.0", "id": 9, "method": "tools/list",
         "params": {"_meta": {_V: "1900-01-01", _CAPS: {}}}},
        # hostile requests: each must come back as an error result, never kill the loop
        {"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {}},
        {"jsonrpc": "2.0", "id": 11, "method": "tools/call",
         "params": {"name": "judge_serial", "arguments": "oops"}},
        {"jsonrpc": "2.0", "id": 12, "method": "tools/call", "params": {"name": 123}},
        call(13, "judge_serial", contract_path=ex("boot.contract.yaml"), log=good_log),
        call(14, "judge_events", contract_path=ex("spi-frame.contract.yaml"), events=[
            {"source": "gpio.cs", "type": "falling", "start_ns": 1000, "fields": {"value": 0}},
            {"source": "spi.mosi", "type": "rising", "start_ns": 1240, "fields": {"value": 1}},
            {"source": "spi.sck", "type": "rising", "start_ns": 1300, "fields": {"value": 1}},
            {"source": "spi.sck", "type": "falling", "start_ns": 1800, "fields": {"value": 0}},
            {"source": "gpio.cs", "type": "rising", "start_ns": 9700, "fields": {"value": 1}},
            {"source": "gpio.cs", "type": "falling", "start_ns": 21000, "fields": {"value": 0}},
            {"source": "spi.mosi", "type": "falling", "start_ns": 21240, "fields": {"value": 0}},
            {"source": "spi.sck", "type": "rising", "start_ns": 21300, "fields": {"value": 1}},
            {"source": "spi.sck", "type": "falling", "start_ns": 21800, "fields": {"value": 0}},
            {"source": "gpio.cs", "type": "rising", "start_ns": 29700, "fields": {"value": 1}},
        ]),
    ]
    raw = "\n".join([json.dumps(m) for m in msgs] + ['{"jsonrpc": broken', "[1, 2, 3]"])
    out = io.StringIO()
    previous_safe = os.environ.get("HWCONTRACT_SAFE")
    try:
        os.environ["HWCONTRACT_SAFE"] = "1"     # self-test never touches hardware
        serve(io.StringIO(raw), out)
    finally:
        if previous_safe is None:
            os.environ.pop("HWCONTRACT_SAFE", None)
        else:
            os.environ["HWCONTRACT_SAFE"] = previous_safe
    responses = [json.loads(l) for l in out.getvalue().splitlines()]
    r = {m["id"]: m for m in responses if m.get("id") is not None}
    strays = [m for m in responses if m.get("id") is None]

    assert len(responses) == 16                     # 14 ids + parse error + non-object request
    assert len(strays) == 2
    assert strays[0]["error"]["code"] == -32700     # malformed JSON -> parse error
    assert strays[1]["error"]["code"] == -32603     # non-object request -> internal error, loop lives
    assert r[1]["result"]["protocolVersion"] == LEGACY_VERSIONS[0]   # no version asked -> newest legacy
    assert {t["name"] for t in r[2]["result"]["tools"]} == set(TOOLS)
    assert json.loads(r[3]["result"]["content"][0]["text"])["ok"] is True    # WS2812 in-spec
    assert json.loads(r[4]["result"]["content"][0]["text"])["ok"] is True    # DShot  in-spec
    assert json.loads(r[5]["result"]["content"][0]["text"])["ok"] is True    # boot log clean
    assert r[6]["result"]["isError"] is True        # hardware tool fails gracefully, no crash
    assert r[7]["result"]["supportedVersions"] == [MODERN_VERSION]
    assert r[7]["result"]["_meta"][_SRV]["version"] == __version__
    assert r[8]["result"]["resultType"] == "complete"
    assert json.loads(r[8]["result"]["content"][0]["text"])["ok"] is True    # same tool, modern era
    assert r[9]["error"]["code"] == -32022 and r[9]["error"]["data"]["supported"] == [MODERN_VERSION]
    assert r[10]["result"]["isError"] is True       # missing tool name -> error result
    assert r[11]["result"]["isError"] is True       # non-object arguments -> error result
    assert r[12]["result"]["isError"] is True       # non-string tool name -> error result
    assert json.loads(r[13]["result"]["content"][0]["text"])["ok"] is True   # still alive after all that
    body14 = json.loads(r[14]["result"]["content"][0]["text"])
    assert body14["ok"] is True and body14["verdict"] == "PASS"              # temporal assertions hold
    assert body14["results"][0]["latency"]["count"] == 2                     # cs->clock latency seen twice

    hdrs = {"MCP-Protocol-Version": MODERN_VERSION, "Mcp-Method": "tools/call",
            "Mcp-Name": "judge_serial"}
    hdr_msg = modern(10, "tools/call", name="judge_serial", arguments={})
    assert _header_error(hdrs, hdr_msg) is None
    assert _header_error({**hdrs, "Mcp-Name": "other_tool"}, hdr_msg)["error"]["code"] == -32020
    assert _origin_ok(None, "127.0.0.1", 8000) and not _origin_ok("https://evil.example", "127.0.0.1", 8000)
    print(render(json.loads(r[5]["result"]["content"][0]["text"])["results"]))
    print("\nself-check OK")


_EPILOG = """\
a part with no bundled contract: write one from its datasheet (times in ns)
and pass its path to --check. Each edge says what it measures on the line:

  contract: my-part
  headroom_pct: 10   # required: this % of the window near a limit is MARGINAL
                     # (bundled LED contracts use 20, DShot 15)
  edges:
    - {name: ZERO, measure: high, min: 2500, typ: 3000, max: 3500}
    - {name: ONE,  measure: high, min: 8000, typ: 9000, max: 10000}
    - {name: SPACE, measure: low, min: 3500, typ: 4000, max: 4500}
    - {name: GAP,  measure: low, min: 100000, typ: 100000, max: null}

  measure: high | low | period (rising edge to rising edge within a frame,
                                 e.g. a bit period TH+TL)
  after: EDGE      only the run right after EDGE (T1L is after T1H)
  required: false  explains pulses when present, absence is not MISSING
  T0H/T1H/T0L/T1L/RESET need no measure. To start from a bundled contract,
  copy the YAML at its "path" in --list-contracts --json.

results: each run goes to the edge whose window holds it, else is blamed on the
nearest edge; more than 2x outside every window of its kind it is "unmatched"
and fails, together with the out-of-window pulses that continue its spread
(one row per problem). actual is the median (p50) of the pulses an edge got;
the hint counts the ones out of window. A run cut off by the capture's start
or end counts only for an open-ended edge, once it reaches that edge's min,
and only when the capture holds no complete run of that edge.

failing rows locate their pulses: "at 1.4ms (frame 2, after 1019 highs)" is the
time in the capture, the frame (counted at each reset-like gap: an open-ended
low edge such as RESET) and the high pulses before it in that frame. For an LED
strip that is the bit index: LED 1019 // 24 = 42, bit 1019 % 24 = 11.
The table shows the first; --json lists up to 20 per row.

demos: python -m hwcontract.demo ws2812b (downloaded real capture),
       python -m hwcontract.demo spi (offline synthetic frames)"""


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Hardware contract judges and MCP server (default: stdio).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_EPILOG)
    parser.add_argument("--version", action="version", version=f"hwcontract {__version__}")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--selftest", action="store_true", help="run the offline MCP self-test and exit")
    modes.add_argument("--http", type=int, metavar="PORT", help="serve MCP HTTP on localhost")
    modes.add_argument("--list-contracts", action="store_true", help="list bundled templates and capture requirements")
    modes.add_argument("--check", nargs=2, metavar=("CONTRACT", "FILE"),
                       help="judge one file against a contract (bundled name or path): a 0/1 "
                            "capture CSV or observations JSON for timing, a log for serial, a "
                            "trace for events. Exit 0 on PASS, 1 on FAIL")
    parser.add_argument("--samplerate", metavar="HZ",
                        help="with --check on a capture CSV: the rate it was taken at "
                             "(24000000, 24e6 or 24MHz)")
    parser.add_argument("--json", action="store_true", help="emit --list-contracts or --check as JSON")
    args, extra = parser.parse_known_args(argv)
    if extra:           # 'hwcontract list', 'hwcontract check ...': there are no subcommands
        parser.error(f"unrecognized arguments: {' '.join(extra)}. hwcontract takes flags only: "
                     "--list-contracts, --check CONTRACT FILE --samplerate HZ, --help")
    if args.json and not (args.list_contracts or args.check):
        parser.error("--json requires --list-contracts or --check")
    if args.samplerate is not None and not args.check:
        parser.error("--samplerate requires --check")
    if args.http is not None and not 1 <= args.http <= 65535:
        parser.error("--http PORT must be between 1 and 65535")
    if args.selftest:
        selftest()
    elif args.http is not None:
        serve_http(args.http)
    elif args.check:
        contract, path = args.check
        try:
            v, table, notes = check_file(cli_contract(contract), path, args.samplerate)
        except (OSError, ValueError) as e:          # ContractError and bad JSON are ValueErrors
            parser.exit(2, f"hwcontract: {e}\n")
        print(json.dumps(v, indent=2) if args.json else report(v, table, notes))
        sys.exit(0 if v["ok"] else 1)
    elif args.list_contracts:
        entries = list_contracts()
        if args.json:
            print(json.dumps(entries, indent=2))
        else:
            print(f"{len(entries)} bundled contract templates (not all can be measured from a capture):")
            for entry in entries:
                label = entry["name"]
                if entry["contract"] != label:          # either name works
                    label += f" ({entry['contract']})"
                print(f"{label:<32} {entry['kind']:<7} {entry['capture_path']}")
            print("\njudge a file: hwcontract --check NAME FILE [--samplerate HZ]")
    else:
        try:
            serve(sys.stdin, sys.stdout)                 # default: stdio
        except (KeyboardInterrupt, BrokenPipeError):
            pass


if __name__ == "__main__":
    main()
