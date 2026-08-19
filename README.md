<!-- mcp-name: io.github.MohibShaikh/hwcontract -->

# hwcontract

Your firmware is correct on paper and wrong on the wire.

Coding agents write WS2812 drivers, ESC bitstreams, and boot logs that pass
review and then fail the moment the signal hits a real chip. hwcontract closes
that loop. It captures what the hardware *actually* did and returns a verdict you
can act on:

- **pass**: within spec
- **marginal**: in spec but too close to a rail. Works on your bench, dies on a
  cold board in the field. Treat it as a fail.
- **fail**: out of spec, with the measured value and how far off it is

No hardware in your hand? The demo below runs the whole thing on a real recorded
signal, so you can see exactly what you get before wiring anything up.

## See it work in 30 seconds

```bash
pip install hwcontract
python3 -m hwcontract.judge --demo
```

That judges a real 24-LED NeoPixel capture against two contracts. Same signal,
two verdicts:

```
measured on the real WS2812B signal (300000 samples @24MHz):
  T0H 333 ns   T1H 833 ns   T1L 417 ns   T0L 917 ns   RESET 992250 ns

=== generic WS2812 contract -> FAIL ===
  T1L   600   417   FAIL   183ns short (typ 600)      # real WS2812B low times are shorter
  T0L   800   917   marginal
  T1H   700   833   marginal

=== matching WS2812B contract -> PASS ===
  (all pass)
```

Same hardware, two contracts: the generic one fails, the chip-specific one
passes. A WS2812B isn't a WS2812. Measure the real signal, hold it to a spec, and
match the contract to the actual chip.

## What you get

- **27 bundled contracts** for the parts people actually use: WS2812/WS2813/
  SK6812 NeoPixels, DShot ESCs (150/300/600/1200), servos, I2C, NEC IR remotes,
  DS18B20, DHT11/DHT22, HC-SR04, A4988/DRV8825 stepper drivers, PWM fans, plus
  serial boot logs for ESP32, ESP8266, Zephyr, MicroPython, Raspberry Pi, U-Boot,
  and STM32 bootloaders. Each one has the datasheet's real min/typ/max numbers.
- **Add a protocol by dropping in one YAML file.** No code change.
- **An MCP server** your agent can call, or plain CLI commands you can run by hand.
- **Reasonable by default:** timing edges are all measured in nanoseconds, serial
  contracts are Python regex, verdicts come back with the measured value and the
  delta so an agent knows exactly what to fix.

## Install

```bash
pip install hwcontract              # judge + logic-analyzer adapter
pip install "hwcontract[serial]"    # + live serial capture (pyserial)
pip install "hwcontract[untrusted]" # + google-re2 (ReDoS-immune, for untrusted contracts)
pip install "hwcontract[all]"       # everything
```

Live logic-analyzer capture (`check_ws2812` / `check_dshot`) also needs
`sigrok-cli` on PATH. Judge-only tools (`judge_contract`, `judge_serial`) need
nothing extra.

## Wire it into an agent

One stanza per client, add it once. After install, the `hwcontract` command is
on your PATH.

**Claude Code**
```bash
claude mcp add hwcontract -- hwcontract
```

**Codex CLI:** `~/.codex/config.toml`
```toml
[mcp_servers.hwcontract]
command = "hwcontract"
```

**opencode / Cursor / Gemini / any stdio MCP client**
```json
{ "mcpServers": { "hwcontract": { "command": "hwcontract" } } }
```

> Transport is **stdio** by default (local, no auth surface). For remote-only
> clients (e.g. ChatGPT connectors), run `hwcontract --http 8791` and expose it
> via a tunnel with `HWCONTRACT_TOKEN` set for bearer auth.
>
> Speaks MCP 2026-07-28, the stateless revision: per-request `_meta`,
> `server/discover`, no handshake. Clients that still open with `initialize` get
> the old shape back. Each request picks its own era, so nothing to configure.

### If the client can't find `hwcontract` (PATH issues)

GUI apps and some agents don't inherit your shell `PATH`, so a bare `hwcontract`
can fail with "command not found". Two robust fixes:

- Use the **absolute path**: `which hwcontract` → put that full path in `command`.
- Or invoke via Python (no PATH lookup for the script): `command: "python3"`,
  `args: ["-m", "hwcontract.server"]`. Works from any directory once installed.

**Contract paths:** pass an **absolute** `contract_path`, or set `HWCONTRACT_ROOT`
to your contracts folder. Relative paths resolve against it, defaulting to the
process's working directory, which the client controls and may not be your
project. Paths outside the root are rejected. Bundled examples install with the
package under `hwcontract/examples/`.

## The tools

| Tool | Hardware? | What it does |
|------|-----------|--------------|
| `judge_contract` | no | Judge given observations against a timing contract. Replay / testing. |
| `judge_serial` | no | Judge a given log string against a serial contract's expect/forbid. |
| `check_ws2812` | yes | Capture a live WS2812 line **and** judge it, one call. |
| `check_dshot` | yes | Same, for a DShot600 ESC signal. |
| `capture_ws2812` | yes | Just capture → observations (no judging). |
| `check_serial` | yes | Read a serial port for N seconds and judge the log. |

## How it fits together

```
  observers (capture)                judge (this repo)
  ─────────────────────              ─────────────────
  logic analyzer  ─ pulse widths ─┐
  serial port     ─ log text ─────┼─►  contract × observation  ─►  pass/marginal/fail
                                  ┘         (judge.py)
```

- **`judge.py`.** The pure judge for timing and serial. No hardware, no framework, cached.
- **`sigrok_adapter.py`.** Turns a logic-analyzer capture into pulse-width observations for WS2812 and DShot.
- **`serial_adapter.py`.** Captures a serial log, or replays a saved one.
- **`server.py`.** The MCP server, stdio and HTTP JSON-RPC, stdlib only.
- **`*.contract.yaml`.** What "correct" looks like. Human-editable, and they double as regression tests.

## The contract format

Timing (`ws2812.contract.yaml`, `dshot.contract.yaml`): pulse widths in ns
```yaml
contract: ws2812
headroom_pct: 20         # in-spec but within 20% of a rail => "marginal"
edges:
  - {name: T0H, min: 200, typ: 350, max: 500}   # '0' bit high time
```

Serial (`boot.contract.yaml`) uses Python regex:
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

Every tool argument is treated as hostile, since the caller is an LLM that can be
prompt-injected. Contract paths are confined to the server dir, `HWCONTRACT_ROOT`
overrides that. `driver`/`channel`/`port` are charset-validated,
`samples`/`seconds`/`samplerate` are clamped, `sigrok-cli` runs with a timeout,
YAML is `safe_load`. Do not expose this server over the network without adding
authentication.

## Self-tests: no hardware, run from anywhere

```bash
hwcontract --selftest                       # full MCP round-trip
python3 -m hwcontract.judge --demo
python3 -m hwcontract.sigrok_adapter --demo
python3 -m hwcontract.serial_adapter --demo
```
