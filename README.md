<!-- mcp-name: io.github.MohibShaikh/hwcontract -->

# hwcontract

Your firmware is correct on paper and wrong on the wire.

Coding agents write WS2812 drivers, ESC bitstreams, and boot logs that pass
review and then fail the moment the signal hits a real chip. hwcontract closes
that loop. It captures what the hardware *actually* did and returns a verdict you
can act on:

- **PASS**: within spec
- **MARGINAL**: in spec but too close to a rail. Works on your bench, dies on a
  cold board in the field. It fails the verdict: the judge will not ship it.
- **FAIL**: out of spec, with the measured value and how far off it is
- **MISSING**: a required observation is absent; this also fails the verdict

Two things a green verdict gives you beyond the table:

- **Every pulse is judged, not just the median.** Captures carry the full
  pulse distribution; a glitchy tail that a median hides comes back as
  marginal or fail, with the violating-pulse count in the hint.
- **No verdict finer than the capture can see.** A width measured from
  samples is only known to one sample period. FAIL needs the signal out of spec
  by more than that, PASS needs it inside by at least that; anything closer is
  MARGINAL with a hint to capture at a higher samplerate.
- **Evidence on every verdict:** contract hash, capture hash, capture
  parameters, tool version, timestamp. A green build in CI traces back to the
  exact bytes that produced it.

No hardware in your hand? The demo below runs the whole thing on a real recorded
signal, so you can see exactly what you get before wiring anything up.

![hwcontract judging a real WS2812B capture, a DMA-broken SPI trace, and a serial boot log](https://raw.githubusercontent.com/MohibShaikh/hwcontract/main/demo/hwcontract.gif)

## Replay a real capture

```bash
pip install hwcontract
python3 -m hwcontract.demo ws2812b
```

This downloads a recorded 24-LED NeoPixel capture from sigrok (about 109 KiB)
on the first run, verifies its SHA256, then caches it in
`$XDG_CACHE_HOME/hwcontract` or `~/.cache/hwcontract`. It needs network access
once; subsequent runs can use `--offline`. Use `--cache-dir DIR` or
`--capture PATH` for a copy of the same pinned capture. No sigrok installation
or hardware is needed. [Capture provenance and license](https://github.com/MohibShaikh/hwcontract/blob/main/hwcontract/demo-provenance.md).

The capture is judged against two contracts. Same signal, two verdicts
(output excerpt; each judgment also prints its evidence hashes):

```
measured on the real WS2812B signal (300000 samples @24MHz):
  T0H 333 ns   T1H 833 ns   T1L 417 ns   T0L 917 ns   RESET 992250 ns

=== generic WS2812 contract -> FAIL ===
  T0H     350   333  PASS
  T0L     800   917  MARGINAL  within one sample (41.7ns) of max; 1842 of 4275
                               pulses within one sample of the window
  T1H     700   833  MARGINAL  within one sample (41.7ns) of max
  T1L     600   417  MARGINAL  33ns past min, within one sample (41.7ns);
                               capture at a higher samplerate to resolve
  RESET 50000 992250 PASS

=== matching WS2812B contract -> PASS ===
  (all five edges PASS)
```

Same hardware, two contracts: the generic one fails, the chip-specific one
passes. The generic rows are MARGINAL rather than FAIL because at 24MHz each
sample is 41.7ns, and every miss is smaller than that: the capture shows the
signal is too close to the WS2812 limits, not that it is definitely past them. A WS2812B isn't a WS2812. Measure the real signal, hold it to a spec, and
match the contract to the actual chip.

## See the temporal engine catch a DMA bug

```bash
python3 -m hwcontract.demo spi
```

100 synthesized SPI frames as raw CS/SCK/MOSI waveforms at 100MHz, reduced to
pin edges and judged against the bundled `spi-frame` contract. Frame 77 has
a simulated DMA ordering fault: chip-select asserts after the clock starts. Frame
42 settles MOSI 10ns before the sampling edge. Both come back with exact
timestamps. An additional artificial B/E jsontrace roundtrip tests the
importer; those annotations are **not real sigrok SPI decoder output**:

```
cs-precedes-first-clock  800  1  FAIL  trigger at 1540310ns: no gpio.cs.falling
                                          in [1530310ns, 1540310ns] (first of 1)
mosi-setup               800  1  FAIL  forbidden spi.mosi.* at 843300ns is 10ns
                                          before spi.sck.rising at 843310ns
```

This is an offline, deterministic pin-timing demonstration. It does not decode
SPI payload bytes or run a loopback test. `python3 -m hwcontract.demo serial`
also works offline with synthetic clean and panicking boot logs. Repository
commands such as `python3 demo/spi_dma_temporal.py` remain compatibility wrappers.
All demos exit zero when their expected PASS/FAIL outcomes occur; use the judge
CLI or MCP verdict for a production check.

## What you get

- **28 bundled contract templates**, including WS2812-family LEDs, DShot,
  servo/I2C/sensor/stepper timing, serial boot logs, and SPI pin ordering.
  They are a spec library, not 28 ready-to-use capture integrations. Verify the
  template against your exact part and its datasheet.
- **New hardware is a YAML file.** A timing edge says what it measures on a
  0/1 capture (`measure: high`, `low` or `period`, optionally `after:` another
  edge), so any single-line pulse protocol (an LED chip, a DShot rate, a PWM
  fan, a part released last week) is judged from its datasheet numbers alone.
  No protocol is hardcoded. Multi-line timing (I2C setup/hold, STEP/DIR) still
  needs an adapter that emits observations JSON.
- **Every pulse is accounted for.** Each one lands on the edge whose window
  holds it, or is blamed on the nearest window within 2x; a pulse the contract
  cannot explain fails as `unmatched`. Failing rows say where: the time, and
  with a frame delimiter such as RESET, the frame and how many highs came
  before (for an LED strip, the bit index). The tool reports facts; the model
  reading them brings the protocol knowledge.
- **Temporal assertions on normalized events.** Raw pin transitions feed
  `spi-frame`; decoder annotations need contracts matching the decoder's
  actual sources and event types.
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

Live logic-analyzer capture (`check_capture`) also needs
`sigrok-cli` on PATH. Judge-only tools (`judge_contract`, `judge_serial`) need
nothing extra.

```bash
hwcontract --help
hwcontract --version
hwcontract --list-contracts         # names, kinds, capture requirements
hwcontract --list-contracts --json  # also includes absolute installed paths
hwcontract --check ws2812b capture.csv --samplerate 24000000   # judge a file you have
```

`--check` reads the file by the contract's kind: a 0/1 capture CSV (measured
the way the contract's edges say) or observations JSON for timing, a log for
serial, a trace for events. It exits 0 on PASS and 1 on FAIL, and `--json`
prints the full verdict with every failing pulse's location.

These commands exit immediately. Bare `hwcontract` starts the MCP stdio server
and waits for JSON-RPC requests on stdin.

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

**Contract paths:** MCP tools accept short bundled names such as `ws2812b`,
`boot`, or `spi-frame`, or their full filenames (`ws2812b.contract.yaml`).
These names match the pytest plugin and Action; for DShot600 the name is
`dshot`, and the mini LED name is `ws2812b_mini`. An existing project file wins.
Relative paths resolve under `HWCONTRACT_ROOT` (default: the server's working
directory). Arbitrary absolute paths and symlinks must stay within that root.
Only exact installed paths returned by `list_contracts` may refer to bundled
contracts outside it; this does not grant access to other package files.
The lower-level `judge` and `temporal` module CLIs still take file paths.

## The tools

| Tool | Hardware? | What it does |
|------|-----------|--------------|
| `list_contracts` | no | List all 28 templates, kinds, installed paths and capture requirements. |
| `judge_contract` | no | Judge given observations against a timing contract. Replay / testing. |
| `judge_serial` | no | Judge a given log string against a serial contract's expect/forbid. |
| `judge_events` | no | Judge decoded events against temporal assertions (when/require/within, forbid/while/before). |
| `judge_file` | no | Judge a saved capture CSV, observations JSON, log or trace under `HWCONTRACT_ROOT`; same as `hwcontract --check`. |
| `check_capture` | yes | Capture one live line **and** judge it against any measurable timing contract, one call. `check_ws2812` / `check_dshot` are aliases. |
| `capture_ws2812` | yes | Just capture → WS2812-bucketed observations (no judging). |
| `check_serial` | yes | Read a serial port for N seconds and judge the log. |

Event contracts check relationships for every occurrence, with latency
distributions and first-failure timestamps. The bundled `spi-frame` contract
requires `gpio.cs.falling/rising`, `spi.sck.rising`, and `spi.mosi.*` raw pin
edges. A sigrok SPI `Data` annotation does not supply those events. Follow the
[complete multi-channel CSV-to-edge recipe](https://github.com/MohibShaikh/hwcontract/blob/main/docs/writing-contracts.md#raw-spi-capture-to-a-verdict)
to judge an actual capture. Semantic decoder checks use separate contracts
matching the imported decoder rows.

Use the judge matching the contract kind: timing, serial, or events. Wrong-kind
calls are errors. Serial logs longer than 1,000,000 characters are rejected,
not silently truncated. CSV inputs must contain valid digital samples;
malformed rows and empty captures are errors, not evidence of a passing device.

Prefer plain pytest over MCP? [`pytest-hwcontract`](https://github.com/MohibShaikh/hwcontract/tree/main/pytest-hwcontract) is a
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
and writes JUnit. Timing CSVs are measured the way the contract's edges say,
the same judging as `hwcontract --check`; contracts without `measure:` take
observations JSON. The
action self-tests on every push to this repo with one clean and one
deliberately broken capture.

Replaying an unchanged recorded capture checks the judge and contract against
those recorded bytes. It does **not** test newly built firmware. To gate a
firmware change, flash that build, collect a fresh capture/log, and judge it;
retain the firmware revision, capture parameters and verdict evidence together.

## How it fits together

```
  observers (capture)                  judge (this repo)
  ─────────────────────                ─────────────────
  logic analyzer  ─ pulse widths ─┐
  serial port     ─ log text ─────┼─►  contract × observation  ─►  PASS/MARGINAL/FAIL/MISSING
  sigrok jsontrace ─ events ──────┘         (judge.py / temporal.py)
```

- **`judge.py`.** The pure judge for timing and serial, plus contract validation. No hardware, no framework, cached.
- **`temporal.py`.** Cross-event temporal assertions: selectors, signed windows, latency distributions, first-failure timestamps.
- **`jsontrace.py`.** Imports sigrok-cli's Google Trace Event JSON into normalized events.
- **`sigrok_adapter.py`.** Reads 0/1 captures from CSV or live sigrok; its fixed WS2812 bucketing backs `capture_ws2812`.
- **`serial_adapter.py`.** Captures a serial log, or replays a saved one.
- **`measure.py`.** Turns a 0/1 capture into observations the way a contract's edges say, and locates every pulse. No protocol tables.
- **`check.py`.** Judges a capture, log or trace by its contract's kind. Shared by `hwcontract --check`, the MCP tools, the pytest plugin and the GitHub Action.
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
events — SVA-style temporal checks on raw pin edges or sigrok annotations:
```yaml
contract: spi-frame
kind: events
assertions:
  - {name: cs-precedes-first-clock, when: spi.sck.rising,
     require: gpio.cs.falling, within: [-10us, 0ns]}
  - {name: mosi-setup, when: spi.sck.rising,
     forbid: spi.mosi.*, before: 20ns}
```

A new contract needs matching observations. Add an adapter when the existing
capture paths do not emit them; YAML alone does not add protocol support.

## Kill switch

Instantly disable every hardware-touching tool (captures) while leaving the pure
judge tools working:

```bash
export HWCONTRACT_SAFE=1
```

## Security

Every tool argument is treated as hostile, since the caller is an LLM that can be
prompt-injected. Arbitrary contract paths are confined to the server working directory,
with `HWCONTRACT_ROOT` overriding it; exact bundled contracts are allowlisted. `driver`/`channel`/`port` are charset-validated,
`samples`/`seconds`/`samplerate` are clamped, `sigrok-cli` runs with a timeout,
YAML is `safe_load`. Do not expose this server over the network without adding
authentication.

## Self-tests: no hardware, run from anywhere

```bash
hwcontract --selftest                       # full MCP round-trip
python3 -m hwcontract.judge --demo           # tiny synthetic self-test, includes MISSING RESET
python3 -m hwcontract.sigrok_adapter --demo
python3 -m hwcontract.serial_adapter --demo
pytest                                      # the tests/ suite; pip install -e .[dev]
```

CI runs the suite on every push and PR, and the PyPI publish job waits for it.
