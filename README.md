<!-- mcp-name: io.github.MohibShaikh/hwcontract -->

# hwcontract

A zero-dependency **MCP server that judges hardware against a contract**. Coding
agents (Claude Code, Codex, opencode) write firmware that's correct on paper but
wrong on the wire — a WS2812 pulse 180ns short, an ESC bit out of spec, a boot log
that silently panics. This closes the loop: it captures what the hardware *actually*
did and returns **pass / marginal / fail** the agent can iterate on.

`marginal` is the valuable verdict — in-spec but low-headroom, the bug that works on
your bench and fails on a cold board in the field.

## How it fits together

```
  observers (capture)                judge (this repo)
  ─────────────────────              ─────────────────
  logic analyzer  ─ pulse widths ─┐
  serial port     ─ log text ─────┼─►  contract × observation  ─►  pass/marginal/fail
                                  ┘         (judge.py)
```

- **`judge.py`** — the pure judge (timing + serial). No hardware, no framework, cached.
- **`sigrok_adapter.py`** — logic-analyzer capture → pulse-width observations (WS2812/DShot).
- **`serial_adapter.py`** — serial log capture (or replay a saved log).
- **`server.py`** — the MCP server (stdio JSON-RPC, stdlib only).
- **`*.contract.yaml`** — what "correct" looks like. Human-editable. Also serve as regression tests.

## Install

```bash
pip install hwcontract              # judge + logic-analyzer adapter
pip install "hwcontract[serial]"    # + live serial capture (pyserial)
pip install "hwcontract[untrusted]" # + google-re2 (ReDoS-immune, for untrusted contracts)
pip install "hwcontract[all]"       # everything
```

Also needs `sigrok-cli` on PATH for live logic-analyzer capture (`check_ws2812` /
`check_dshot`). Judge-only tools (`judge_contract`, `judge_serial`) need nothing extra.

## Wire it into an agent

One stanza per client (not auto-discovered — add it once). After `pip install`, the
`hwcontract` command is on your PATH.

**Claude Code**
```bash
claude mcp add hwcontract -- hwcontract
```

**Codex CLI** — `~/.codex/config.toml`
```toml
[mcp_servers.hwcontract]
command = "hwcontract"
```

**opencode / Cursor / Gemini / any stdio MCP client**
```json
{ "mcpServers": { "hwcontract": { "command": "hwcontract" } } }
```

> Transport is **stdio** by default (local, no auth surface). For remote-only clients
> (e.g. ChatGPT connectors), run `hwcontract --http 8791` and expose it via a tunnel
> with `HWCONTRACT_TOKEN` set for bearer auth.

### If the client can't find `hwcontract` (PATH issues)

GUI apps and some agents don't inherit your shell `PATH`, so a bare `hwcontract`
can fail with "command not found". Two robust fixes:

- Use the **absolute path**: `which hwcontract` → put that full path in `command`.
- Or invoke via Python (no PATH lookup for the script): `command: "python3"`,
  `args: ["-m", "hwcontract.server"]` — works from any directory once installed.

**Contract paths:** pass an **absolute** `contract_path`, or set `HWCONTRACT_ROOT`
to your contracts folder — relative paths resolve against it (default: the process's
working directory, which the client controls and may not be your project). Paths
outside the root are rejected. Bundled examples install with the package under
`hwcontract/examples/`.

## Tools

| Tool | Hardware? | What it does |
|------|-----------|--------------|
| `judge_contract` | no | Judge given observations against a timing contract. Replay / testing. |
| `judge_serial` | no | Judge a given log string against a serial contract's expect/forbid. |
| `check_ws2812` | yes | Capture a live WS2812 line **and** judge it, one call. |
| `check_dshot` | yes | Same, for a DShot600 ESC signal. |
| `capture_ws2812` | yes | Just capture → observations (no judging). |
| `check_serial` | yes | Read a serial port for N seconds and judge the log. |

## Contracts

Timing (`ws2812.contract.yaml`, `dshot.contract.yaml`) — pulse widths in ns:
```yaml
contract: ws2812
headroom_pct: 20         # in-spec but within 20% of a rail => "marginal"
edges:
  - {name: T0H, min: 200, typ: 350, max: 500}   # '0' bit high time
```
Serial (`boot.contract.yaml`) — Python regex:
```yaml
contract: boot
kind: serial
expect: ["IMU init OK", "boot v\\d+"]
forbid: ["panic", "Guru Meditation", "\\bnan\\b"]
```
Add a protocol = drop a new YAML. No code change for another timing signal.

## Kill switch

Instantly disable every hardware-touching tool (captures) while leaving the pure
judge tools working:

```bash
export HWCONTRACT_SAFE=1          # env, or:
touch /home/tsd/projects/hardware/KILLSWITCH   # file next to server.py
```

## Security

Every tool argument is treated as hostile (the caller is an LLM that can be prompt-
injected): contract paths are confined to the server dir (override `HWCONTRACT_ROOT`),
`driver`/`channel`/`port` are charset-validated, `samples`/`seconds`/`samplerate` are
clamped, `sigrok-cli` runs with a timeout, YAML is `safe_load`. Do not expose this
server over the network without adding authentication.

## Self-tests (no hardware, run from anywhere)

```bash
hwcontract --selftest                       # full MCP round-trip
python3 -m hwcontract.judge --demo
python3 -m hwcontract.sigrok_adapter --demo
python3 -m hwcontract.serial_adapter --demo
```
