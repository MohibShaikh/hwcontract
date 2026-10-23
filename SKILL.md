---
name: hwcontract
description: Check a real hardware signal against the part's datasheet. Use when firmware drives a pulse protocol (LED strips, ESCs, PWM, any single-wire timing), prints a boot log, or toggles SPI lines, and there is a logic-analyzer capture, a serial log, or a board attached. Not for analog, power or wiring faults.
---

# hwcontract

Turn "it's not working" into a located, measured spec violation, fix the
firmware, and prove the fix on a fresh capture.

## Workflow

1. Find the contract: `hwcontract --list-contracts`. Match the exact chip and
   revision. If none fits, write one from the datasheet (below).
2. Judge the file: `hwcontract --check CONTRACT capture.csv --samplerate 24MHz`,
   or a log, or a trace JSON. Over MCP: `judge_file`; with hardware attached,
   `check_capture` or `check_serial`.
3. Read the result (below), name the cause, change the firmware.
4. Capture again from the new build and check again. Replaying an old capture
   says nothing about new firmware.

## Writing a timing contract

Datasheet min/typ/max in ns. Each edge says what it measures on the line:

```yaml
contract: my-part
headroom_pct: 10
edges:
  - {name: ZERO,  measure: high, min: 2500,   typ: 3000,   max: 3500}
  - {name: ONE,   measure: high, min: 8000,   typ: 9000,   max: 10000}
  - {name: SPACE, measure: low,  min: 3500,   typ: 4000,   max: 4500}
  - {name: GAP,   measure: low,  min: 100000, typ: 100000, max: null}
```

- `measure`: `high`, `low`, or `period` (rising edge to rising edge, within
  a frame).
- `after: EDGE`: only the run right after EDGE, e.g. a bit's low after its high.
- `required: false`: explains pulses when present; absence is not a failure.
- `T0H`/`T1H`/`T0L`/`T1L`/`RESET` imply their measure; no annotation needed.
- Timing across two lines (I2C setup/hold, STEP/DIR) cannot be read from one
  capture: judge observations JSON from your own adapter.

Serial contracts are `kind: serial` with `expect`/`forbid` regexes; double the
backslashes (`"\\d+"`). Event contracts: `docs/writing-contracts.md`.

## Reading results

- Only PASS passes. MARGINAL (near a limit, or within one sample of it), FAIL
  and MISSING all fail.
- `window` is the contract's min-max, `actual` the median. The hint counts
  pulses out of window, gives their range and how far past the limit the
  worst is ("up to 2x max"); more than 1% out fails even when the median is
  clean.
- `unmatched low` / `high` / `period`: pulses no edge explains (over 2x
  outside every window of that kind), one row per width cluster. Often this
  is the bug: a stall, a glitch, a wrong rate. A note says when unmatched
  pulses and an edge's out-of-window pulses are one spread of widths.
- `first at 1.412ms (frame 0, after 1019 highs)` locates the first bad pulse.
  For an LED strip the highs count is the bit index: LED 1019 // 24 = 42, bit
  1019 % 24 = 11 (32 bits per LED for RGBW). `--json` lists up to 20 per row.
- Edges MISSING across the board, or everything unmatched: wrong
  `--samplerate`, or the wrong contract for this signal.

## Guardrails

- Never widen a contract's limits to make a capture pass. Fix the firmware, or
  use the contract for the chip actually on the board.
- Label every number: measured (the tool's output), datasheet (the contract),
  or your inference.
- A 0/1 capture shows timing only. Voltage levels, power and ground need other
  instruments.
- Resolution is one sample: 24MHz (41.7ns) cannot resolve a 20ns margin.
- Capture a whole frame plus its reset or gap. Start a serial capture before
  resetting the board.
- Keep the verdict's evidence (contract and input sha256, samplerate, version)
  with the firmware revision it judged.
