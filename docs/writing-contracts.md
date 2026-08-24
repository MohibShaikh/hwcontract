# Writing a contract

A contract is one YAML file that says what correct looks like on the wire.
It lives in `hwcontract/examples/`, ships with the package, and the judges
evaluate it without touching hardware. The 28 bundled contracts are the
reference library: pick the closest part, copy it, adjust the numbers.

Three kinds of contracts exist, judged by three different tools:

| kind            | what it checks             | tool                                          | examples                 |
|-----------------|----------------------------|-----------------------------------------------|--------------------------|
| timing, no kind | pulse widths in ns         | `judge_contract`, `check_ws2812`, `check_dshot` | ws2812b, dshot, servo, hc-sr04 |
| serial          | regexes against a log      | `judge_serial`, `check_serial`                | boot, esp32, nrf54l15    |
| events          | relationships between decoded events | `judge_events`, the `temporal` CLI  | spi-frame                |

The `kind` line selects which schema validates the file, and the MCP tool
you call picks the judge: `judge_contract` runs the edge table,
`judge_serial` runs expect/forbid, `judge_events` runs temporal assertions.
A serial contract without `kind: serial` still judges fine, but the label
keeps the file self-describing and validation strict.

## Timing contract anatomy

`hwcontract/examples/ws2812b.contract.yaml` in full:

```yaml
contract: ws2812b
headroom_pct: 20
edges:
  - {name: T0H, min: 250, typ: 400, max: 550}    # '0' bit high
  - {name: T0L, min: 700, typ: 850, max: 1000}   # '0' bit low
  - {name: T1H, min: 650, typ: 800, max: 950}    # '1' bit high
  - {name: T1L, min: 300, typ: 450, max: 600}    # '1' bit low
  - {name: RESET, min: 50000, typ: 50000, max: null}
```

Three fields per edge:

- `min` / `max` bound the window the pulse must land in.
- `typ` is what a healthy driver emits. The judge uses it for the FAIL hint:
  "183ns short (typ 600)" tells the agent which way to move the delay.
- `max: null` means the spec has no upper bound. A WS2812B RESET is "at least
  50us": the strip must stay latched for minutes, so only the minimum is
  checked. The judge skips the max rail when it is null, and marginal checks
  only apply to bounded edges.

Take min/typ/max from the datasheet's timing table, not from what your driver
happens to emit. The WS2812B datasheet lists each time as a nominal value with
a +/-150ns tolerance; that spread is exactly the min/max window. When the
datasheet gives one number, set min = max = typ, as `hc-sr04.contract.yaml`
does for TRIG: `min: 10000, typ: 10000, max: null` for ">=10us trigger".

## headroom_pct

`headroom_pct` decides what counts as marginal. From `judge.py`:

```python
threshold = headroom_pct / 100 * (hi - lo)
if min(v - lo, hi - v) < threshold:
    near = "min" if v - lo < hi - v else "max"
    return MARGINAL, f"only {min(v - lo, hi - v)}ns from {near}; nudge toward typ {typ}"
```

The threshold is a fraction of the whole window, measured from each rail. For
WS2812B T0H, the window is 250..550ns, 300ns wide. At `headroom_pct: 20` the
threshold is 60ns: a measured 520ns sits 30ns from max, so the verdict is
`MARGINAL: only 30ns from max; nudge toward typ 400`. A healthy 333ns sits
83ns from min and passes.

Marginal is a fail, and the judge enforces it: any MARGINAL edge makes the
overall verdict fail, same as FAIL or MISSING. In spec but rail-hugging works
on your bench and dies on a cold board in the field. Keep the value used by
the closest example: LEDs use 20, DShot uses 15 because ESCs are less
forgiving. Don't crank the number down to silence marginals; fix the driver
instead.

## Edge naming

Names must match what the adapter emits. The shared timing adapter in
`sigrok_adapter.py` buckets pulse widths into these names:

- `T0H`, `T1H`, `T1L`, `T0L`, `RESET` for WS2812-family and DShot, with the
  per-protocol split thresholds passed in.

A protocol with its own adapter picks its own names. `servo.contract.yaml`
uses `PULSE_MIN`, `PULSE_NEUTRAL`, `PULSE_MAX`, `FRAME`; `hc-sr04.contract.yaml`
uses `TRIG`, `ECHO`, `CYCLE`.

A misspelled edge never matches an observation and reports `MISSING: no
observation for this edge`, which fails the judgment. That is the behavior you
want: a wrong name cannot silently pass.

## Validation

`load_contract` validates before judging and raises `ContractError` listing
every problem at once. A timing contract rejects: missing `contract`,
`headroom_pct` (0..100) or `edges`; unknown top-level keys; a `unit` other
than `ns`; edge names that are empty, duplicated, or not strings; min/typ/max
that are missing, negative, non-numeric, NaN or infinite; `min > typ` and
`typ > max`. A serial contract rejects: unknown keys, patterns that are not
strings, regexes that do not compile, and a file with no expect or forbid
patterns at all. The bundled examples are all covered by this in `tests/`,
so a typo cannot reach the judge.

## Serial contracts

`hwcontract/examples/boot.contract.yaml` in full:

```yaml
contract: boot
kind: serial
expect:
  - "IMU init OK"       # required success line
  - "boot v\\d+"        # a version banner
forbid:
  - "panic"             # crash markers must be absent
  - "Guru Meditation"   # ESP32 exception handler
  - "\\bnan\\b"         # NaN sensor reads
```

`expect` patterns must each match somewhere in the log. `forbid` patterns must
match nowhere. A broken forbid regex fails closed (`judge.py`): if the pattern
itself errors, the edge fails. The log is capped at 1,000,000 chars before
matching. With `google-re2` installed (the `untrusted` extra) matching is
linear-time and contracts can be untrusted; with stdlib re keep patterns
simple.

Patterns are Python regex. Copy the well-tested forms from `boot.contract.yaml`
and `esp32.contract.yaml` instead of writing fresh ones.

## Regex escaping in YAML

This is the trap that eats new contract writers. YAML double-quoted strings
process escape sequences, so the backslashes you want the regex engine to see
must be doubled in the file:

- `"boot v\\d+"` in the YAML is the regex `boot v\d+`. Correct.
- `"boot v\d+"` with one backslash is a YAML parse error: `\d` is not a valid
  YAML escape, PyYAML raises `found unknown escape character`.
- `"\bnan\b"` with one backslash is worse: it parses fine, because `\b` is the
  YAML escape for backspace. The regex engine receives actual 0x08 bytes, the
  pattern never matches, and the contract fails on hardware that is fine.
  `"\\bnan\\b"` is the word-boundary regex you meant.

Single-quoted YAML strings pass backslashes through literally, so `'boot v\d+'`
also works. The repo convention is double quotes with doubled backslashes;
stay with it so the files read consistently.

Verify with the parse, not the eye:

```bash
python3 -c "import yaml; print(yaml.safe_load(open('hwcontract/examples/boot.contract.yaml'))['expect'])"
```

## Distributions and outliers

A capture is not one number per edge. The adapter measures every pulse and the
observation carries the full distribution: `count`, `min`, `max`, `p5`, `p50`,
`p95`, `jitter` (p95 minus p5), plus the raw `widths`. The judge checks every
pulse against the window, not just the median:

- All pulses in window, median comfortable: `PASS`.
- Median in spec but a few pulses outside: `MARGINAL`, with the count in the
  hint ("3 of 1200 pulses out of window (0.25%)").
- More than 1% of pulses outside the window: `FAIL`, even when the median sits
  dead on typ. A driver that glitches one bit in a hundred is not a driver
  you ship.

The 1% threshold is `VIOLATION_FAIL_PCT` in `judge.py`. Observations without
raw widths (hand-written summaries passed to `judge_contract`) are judged on
the median alone; the capture tools always carry widths, so live judgments
always see the tails.

## Event contracts: cross-signal temporal assertions

Pulse-window contracts judge one signal. Event contracts judge the
relationships *between* decoded events, which is the fault class loopback
tests cannot see (chip-select asserting after the clock started, MOSI
changing too close to the sampling edge, a response that never arrives).

Events are normalized dicts. Two ingestion paths feed them:

- **Raw pin edges** (`hwcontract/edges.py`): multi-channel 0/1 waveforms in,
  `rising`/`falling` events out, one per transition. This is what pin-level
  contracts like the SPI ordering checks judge.
- **Sigrok decoder annotations** (`jsontrace.py`): the B/E pairs that
  `sigrok-cli --protocol-decoder-jsontrace` emits become one event each,
  with source = pid, type = tid, and the annotation text in
  `fields.text`. Selectors then match against the decoder's rows.

```json
{"source": "gpio.cs", "type": "falling", "start_ns": 184020,
 "end_ns": 184020, "fields": {"value": 0}}
```

```yaml
contract: spi-frame
kind: events
assertions:
  - name: cs-precedes-first-clock
    when: spi.sck.rising             # trigger: each rising SCK edge
    require: gpio.cs.falling         # CS (active low) must already be asserted...
    within: [-10us, 0ns]             # ...before the edge (negative = look back)
  - name: mosi-setup
    when: spi.sck.rising
    forbid: spi.mosi.*               # no MOSI transition of any kind...
    before: 20ns                     # ...in the 20ns before the sampling edge
  - name: cs-frame-width
    when: gpio.cs.falling
    require: gpio.cs.rising
    within: [4us, 30us]              # frame occupies 4..30us of wire time
```

Selector grammar: the last dotted component is the event type, everything
before it is the source, and a `field=value` tail filters event fields.
`gpio.cs.value=0` is source `gpio`, type `cs`, field `value` equal to 0.
A selector like `gpio.mosi.change` means source `gpio.mosi`, type `change`;
that reads naturally and means something different, so check the parse with
`parse_selector` when a forbid passes suspiciously cleanly. `*` matches any
type of the named source. A forbid that never appears anywhere in the trace
says so in its hint.

Three assertion shapes:

- `when` + `require` + `within`: every trigger must see a matching event in
  the window. Negative window bounds look backward. The verdict reports the
  measured trigger-to-response latency distribution (min/p50/p95/p99/max).
- `forbid` + `while`: no forbidden event may overlap a while-event's
  [start_ns, end_ns] span. The while-events need real durations.
- `when` + `forbid` + `before`: no forbidden event may start in the
  `before` window preceding each trigger. This is the setup-time check.

Every trigger is checked, not a sample. Zero triggers fails: a contract
that never fires proves nothing. Violations name the first failure with its
exact timestamps, so the trace window is one search away.

Judge from the command line:

```bash
sigrok-cli -i capture.sr -P spi:clk=D0:mosi=D1:cs=D2 -A spi \
    --protocol-decoder-jsontrace > trace.json
python3 -m hwcontract.temporal spi-frame.contract.yaml trace.json
```

## The capture-window pitfall

Serial capture is a time box. `serial_adapter.py` reads the port for N seconds
and that window is the whole story: a pattern seen in the capture was seen
within N seconds, and a pattern missed was never emitted in that window.

A boot banner prints once per power-on. If the board booted before the capture
started, "IMU init OK" is already gone and the contract fails even though the
firmware is fine. When using `check_serial` against a boot contract, start the
tool first, then reset the target. For a long-running board, boot patterns
only apply at boot: use repeating output (heartbeats, telemetry prompts)
instead.

Match patterns to how often things print. `nrf54l15.contract.yaml` expects
`VERIFY: place a finger`, a prompt the app repeats while waiting, and
`ACCEPT as user`, a one-shot success line that appears only when a
verification happens inside the window. If the user takes longer than the
capture window to present a finger, the contract correctly fails: lengthen
`seconds` rather than deleting the line.

Timing captures have the same shape of problem. `sigrok_adapter.py` drops the
boundary runs, truncated by capture start and stop, and scans RESET across all
runs so a latch gap at the capture edge is not lost. One WS2812 LED takes about
30us of wire time (24 bits at 1.25us each) plus a 50us reset at the end of the
frame. A 200,000-sample capture at 24MHz is 8.3ms: comfortably one frame for a
long strip and its reset. Capture enough samples for one full frame plus the
reset gap, or the frame ends up mid-capture and the reset never appears.

## Chip vs clone drift

Contracts are chip-specific. The bundled demo proves it with a real signal:
`demo/ws2812b_neopixel.py` downloads a genuine 24-LED capture and judges it
against two contracts. The generic WS2812 contract fails it on T1L, measured
417ns against a 450ns minimum, 33ns short. Same wire, same capture, and the
WS2812B contract passes it. A WS2812B is not a WS2812: shorter lows, different
windows. Match the contract to the exact part, and read the file header
comments, which record the part's quirks.

Clones drift further than datasheets admit. The `ws2812.contract.yaml` header
warns that WS2812B clones want a RESET longer than 280us. SK6812 uses the same
wire protocol with tighter windows and a shorter 80us reset; it gets its own
contract, as does `ws2812b_mini`.

When a fresh contract fails identically on every real capture, one edge, same
delta, suspect the datasheet before the driver. The FAIL hint prints the
measured value and how far off it is, so you can see the pattern across
boards. Measure a few parts, then widen the window to the observed reality and
comment the file with the reason. If only one board drifts, fix the board.

## Verify a new contract against a known-good capture

A contract is not done until a known-good capture passes it and a known-bad
one fails the edges you expect.

1. Take a real capture. For WS2812-family, the repo ships one:

```bash
python3 demo/ws2812b_neopixel.py
```

   This downloads the real 24-LED capture, measures it, and judges it against
   the generic and WS2812B contracts. Replace the contract path with yours to
   see your numbers against real silicon.

2. Or capture your own and judge it from the command line:

```bash
python3 -m hwcontract.sigrok_adapter --driver fx2lafw --channel D0 \
    --samplerate 24000000 --samples 200000 > observations.json
python3 -m hwcontract.judge my.contract.yaml observations.json
```

   Offline replay from a saved CSV works the same way with `--csv`.

3. Serial contracts, judged the same way the demo tape does:

```bash
uv run python3 -c "from hwcontract.judge import load_contract, run_serial, render; \
c = load_contract('hwcontract/examples/boot.contract.yaml'); \
log = open('good_boot.log').read(); \
print(render(run_serial(c, log)[0]))"
```

   Use a log you captured from working hardware, then a log with the failure
   symptom present. Both verdicts must come out as expected.

4. Exercise the failure path. Feed observations that violate each edge and
   confirm the hints say what an agent needs to hear: the edge name, the
   measured value, the delta, and the typ to aim for. Vague hints are a
   contract bug.

5. Run the no-hardware self-tests before shipping:

```bash
hwcontract --selftest
python3 -m hwcontract.judge --demo
python3 -m hwcontract.sigrok_adapter --demo
python3 -m hwcontract.serial_adapter --demo
```

6. Drop the file in `hwcontract/examples/`. It ships in the wheel
   automatically: `pyproject.toml` packages `examples/*.yaml`.

## Checklist

- Numbers come from the datasheet, min/typ/max, not from your driver.
- `max: null` where the spec is one-sided, RESET included.
- Edge names match the adapter's output; MISSING edges fail loudly.
- Doubled backslashes in every regex; verified by parsing, not by eye.
- Boot-banner patterns match a boot that happens inside the capture window.
- Known-good capture passes, known-bad capture fails the right edges.
- Headers comment the part's quirks and the sources of the numbers.
- `pytest` passes: the suite validates every bundled contract.
- Every verdict carries evidence: contract hash, capture hash, capture
  parameters, tool version, timestamp. Keep those bundles; they are your
  regression record.