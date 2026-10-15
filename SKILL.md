---
name: hwcontract
description: Judge firmware observations against timing, serial-log, or event contracts. Use for WS2812-family pulses, DShot600, serial boot logs, and SPI raw-edge ordering. The 28 bundled templates also cover other parts, but servo/I2C/sensor/stepper protocols need custom observation adapters. Not for power/wiring or torque/current issues.
---

# hwcontract

Judge measured behavior against the exact part's specification, preserving the
capture and verdict evidence.

## Workflow

1. Run `hwcontract --list-contracts` or MCP `list_contracts`. Read the kind and
   capture requirements; the 28 bundled templates are not 28 capture integrations.
2. Match the contract and adapter to the part. WS2812-family pulse buckets,
   DShot600 (`check_dshot`), and serial logs have capture tools. Other DShot
   rates need custom thresholds. Servo, I2C, HC-SR04 and other timing templates
   need an adapter that emits their named observations.
3. Use the matching judge: timing `judge_contract`, serial `judge_serial`,
   events `judge_events`. Wrong-kind calls are errors. MCP accepts bundled
   names (`ws2812b`, `dshot`, `boot`, `spi-frame`) or filenames. Existing project
   files take precedence. Arbitrary paths/symlinks must remain under
   `HWCONTRACT_ROOT` (default: server cwd); only exact bundled contract paths
   returned by discovery are allowed outside it.
4. `MARGINAL`, `FAIL`, and `MISSING` all fail. Iterate until every required check
   is `PASS`; retain contract/input hashes, parameters, version and timestamp.
5. For a firmware change, flash the new build and take a fresh capture. CI
   replaying an unchanged recording does not test that firmware build. Record
   its revision alongside the capture and verdict evidence.

## Installed demos and discovery

```bash
hwcontract --help
hwcontract --version
hwcontract --list-contracts --json
python3 -m hwcontract.demo ws2812b  # real recorded capture; first run downloads
python3 -m hwcontract.demo spi      # deterministic synthetic raw edges; offline
python3 -m hwcontract.demo serial   # synthetic boot logs; offline
```

The NeoPixel capture is pinned, hash-verified and cached in
`$XDG_CACHE_HOME/hwcontract` or `~/.cache/hwcontract`. `--offline` requires the
cache; `--cache-dir DIR` and `--capture PATH` select a local copy. See
[capture provenance and license](hwcontract/demo-provenance.md). Demos return
zero for expected outcomes, including intentional FAIL cases.
`python3 -m hwcontract.judge --demo` is a small synthetic self-test with a
missing RESET, not a real capture. Bare `hwcontract` waits for MCP requests on
stdio; `--http PORT` selects HTTP and `--selftest` runs the MCP self-test.

## Tools

- `list_contracts`: names, kinds, installed paths and capture requirements.
- `judge_contract` / `judge_serial` / `judge_events`: replay observations,
  serial text, or normalized events without hardware.
- `check_ws2812` / `check_dshot`: capture WS2812-family / DShot600 timing.
- `capture_ws2812`: observations without judgment.
- `check_serial`: capture a serial log and judge it.

Set `HWCONTRACT_SAFE=1` to disable hardware-touching tools while keeping pure
judges and discovery available.

## Writing a contract

Use datasheet min/typ/max values and observation names that your adapter
actually emits. Add YAML alone only when the adapter already supplies them.
New protocols may require adapter code. Verify both good and bad captures
before adding a bundled template; do not add decorative protocol YAMLs.

```yaml
contract: ws2812b
headroom_pct: 20
edges:
  - {name: T0H, min: 250, typ: 400, max: 550}
```

```yaml
contract: boot
kind: serial
expect: ["IMU init OK", "boot v\\d+"]
forbid: ["panic", "Guru Meditation"]
```

```yaml
contract: spi-frame
kind: events
assertions:
  - {name: cs-precedes-first-clock, when: spi.sck.rising,
     require: gpio.cs.falling, within: [-10us, 0ns]}
```

`spi-frame` requires raw CS/SCK/MOSI transitions. Use the
[complete raw CSV conversion recipe](docs/writing-contracts.md#raw-spi-capture-to-a-verdict).
Sigrok B/E decoder annotations with `pid=spi`, `tid=Data` become `spi.Data`,
not pin edges; write a separate semantic contract for the actual decoder rows.
The SPI demo's artificial B/E roundtrip is only an importer test.

## Pitfalls

- Match chip and revision; do not widen limits just to hide failures. Verify
  datasheet, wiring, measurement resolution and adapter thresholds first.
- Double backslashes in YAML double-quoted regexes; test against actual logs.
- Over 1% of pulses outside the window fails even if the median passes.
- Logs longer than 1,000,000 characters are rejected, not truncated. Empty
  captures and malformed CSV rows are errors; dropping rows changes timing.
- Start a boot-log capture before resetting the board. Capture complete frames
  and reset gaps, with initial idle samples for raw edge extraction.
- A 24 MHz analyzer cannot establish a 20 ns SPI setup margin. Use sufficient
  sample resolution and contract windows appropriate to your device.
