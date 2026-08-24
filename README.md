<!-- mcp-name: io.github.MohibShaikh/hwcontract -->

# hwcontract

Your firmware is correct on paper and wrong on the wire.

Coding agents write WS2812 drivers, ESC bitstreams, and boot logs that pass
review and then fail the moment the signal hits a real chip. hwcontract closes
that loop. It captures what the hardware *actually* did and returns a verdict you
can act on:

- **pass**: within spec
- **marginal**: in spec but too close to a rail. Works on your bench, dies on a
  cold board in the field. It fails the verdict: the judge will not ship it.
- **fail**: out of spec, with the measured value and how far off it is

Two things a green verdict gives you beyond the table:

- **Every pulse is judged, not just the median.** Captures carry the full
  pulse distribution; a glitchy tail that a median hides comes back as
  marginal or fail, with the violating-pulse count in the hint.
- **Evidence on every verdict:** contract hash, capture hash, capture
  parameters, tool version, timestamp. A green build in CI traces back to the
  exact bytes that produced it.

No hardware in your hand? The demo below runs the whole thing on a real recorded
signal, so you can see exactly what you get before wiring anything up.

![hwcontract judging a real WS2812B capture, a DMA-broken SPI trace, and a serial boot log](https://raw.githubusercontent.com/MohibShaikh/hwcontract/main/demo/hwcontract.gif)

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
  T0H     350   333  PASS
  T0L     800   917  MARGINAL  only 33ns from max; nudge toward typ 800
  T1H     700   833  MARGINAL  only 17ns from max; nudge toward typ 700
  T1L     600   417  FAIL      183ns short (typ 600)
  RESET 50000    -  PASS

=== matching WS2812B contract -> PASS ===
  (all five edges PASS)
```

Same hardware, two contracts: the generic one fails, the chip-specific one
passes. A WS2812B isn't a WS2812. Measure the real signal, hold it to a spec, and
match the contract to the actual chip.

## See the temporal engine catch a DMA bug

```bash
python3 demo/spi_dma_temporal.py
```

100 synthesized SPI frames, judged against the bundled `spi-frame` contract.
Frame 77 has the Zephyr LPSPI DMA fault: chip-select asserts after the clock
starts. Frame 42 settles MOSI 10ns before the sampling edge. Both come back
with exact timestamps, and the same broken stream is re-judged through the
sigrok jsontrace importer:

```
cs-precedes-first-clock  800  1  FAIL  trigger at 1540300ns: no gpio.cs.value=0
                                          in [1530300ns, 1540300ns] (first of 1)
mosi-setup               800  1  FAIL  forbidden gpio.change at 843290ns is 10ns
                                          before spi0.clock_edge.value=1 at 843300ns
```

The data is perfect in all 100 frames; a loopback test passes. The ordering is
broken in two, and only a cross-signal assertion notices.

## What you get

- **28 bundled contracts** for the parts people actually use: WS2812/WS2813/
  SK6812 NeoPixels, DShot ESCs (150/300/600/1200), servos, I2C, NEC IR remotes,
  DS18B20, DHT11/DHT22, HC-SR04, A4988/DRV8825 stepper drivers, PWM fans, plus
  serial boot logs for ESP32, ESP8266, Zephyr, MicroPython, Raspberry Pi, U-Boot,
  and STM32 bootloaders. Each one has the datasheet's real min/typ/max numbers.
- **Temporal assertions between decoded events.** SVA-style cross-signal checks
  (ordering, setup windows, forbidden states) on sigrok jsontrace output, judged
  for every occurrence with latency percentiles and first-failure timestamps.
- **Add a protocol by dropping in one YAML file.** No code change.
- **An MCP server** your agent can call, or plain CLI commands you can run by hand.
- **Evidence on every verdict:** contract hash, capture hash, capture parameters,
  tool version, timestamp. A green build traces back to the exact bytes.
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
| `judge_events` | no | Judge decoded events against temporal assertions (when/require/within, forbid/while/before). |
| `check_ws2812` | yes | Capture a live WS2812 line **and** judge it, one call. |
| `check_dshot` | yes | Same, for a DShot600 ESC signal. |
| `capture_ws2812` | yes | Just capture → observations (no judging). |
| `check_serial` | yes | Read a serial port for N seconds and judge the log. |

Event contracts are the SVA-style layer: relationships between decoded
events, checked for every occurrence, with latency distributions and
first-failure timestamps. Feed them `sigrok-cli --protocol-decoder-jsontrace`
output and judge from the CLI:

```bash
python3 -m hwcontract.temporal spi-frame.contract.yaml trace.json
```

The bundled `spi-frame.contract.yaml` catches the Zephyr LPSPI class of bug
(CS asserting after SCK starts, MOSI setup collapse) that loopback tests
cannot see.

Prefer plain pytest over MCP? [`pytest-hwcontract`](pytest-hwcontract/) is a
plugin that turns verdicts into tests: a FAIL, MARGINAL or MISSING edge fails
the test with the verdict table in the message, JUnit included.

## Gate CI on it

The repo ships a GitHub Action, so captures checked into the repo get judged
on every PR:

```yaml
- uses: MohibShaikh/hwcontract@action-v0
  with:
    timing: "ws2812b=captures/strip.csv"     # contract=capture-glob, bundled names work
    serial: "boot=logs/boot.log"
    samplerate: 24000000                     # for CSV captures (0/1 per line)
    junit: hwcontract-junit.xml              # shows in the tests tab
```

A FAIL, MARGINAL or MISSING edge fails the step, annotates the failing line,
and writes JUnit. The action self-tests on every push to this repo with one
clean and one deliberately broken capture.

## How it fits together

```
  observers (capture)                  judge (this repo)
  ─────────────────────                ─────────────────
  logic analyzer  ─ pulse widths ─┐
  serial port     ─ log text ─────┼─►  contract × observation  ─►  pass/marginal/fail
  sigrok jsontrace ─ events ──────┘         (judge.py / temporal.py)
```

- **`judge.py`.** The pure judge for timing and serial, plus contract validation. No hardware, no framework, cached.
- **`temporal.py`.** Cross-event temporal assertions: selectors, signed windows, latency distributions, first-failure timestamps.
- **`jsontrace.py`.** Imports sigrok-cli's Google Trace Event JSON into normalized events.
- **`sigrok_adapter.py`.** Turns a logic-analyzer capture into pulse-width distributions for WS2812 and DShot.
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

Events (`spi-frame.contract.yaml`) assert relationships between decoded
events — SVA-style temporal checks on real traces:
```yaml
contract: spi-frame
kind: events
assertions:
  - {name: cs-precedes-first-clock, when: spi0.clock_edge.value=1,
     require: gpio.cs.value=0, within: [-10us, 0ns]}
  - {name: mosi-setup, when: spi0.clock_edge.value=1,
     forbid: gpio.change, before: 20ns}
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
pytest                                      # the tests/ suite; pip install -e .[dev]
```

CI runs the suite on every push and PR, and the PyPI publish job waits for it.
