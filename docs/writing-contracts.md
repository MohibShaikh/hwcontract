# Writing a contract

A contract is one YAML file that says what correct looks like on the wire.
Keep project contracts in your project; the 28 templates in
`hwcontract/examples/` ship with the package. The judges evaluate observations
without touching hardware. Discover the library with `hwcontract --list-contracts`
or the MCP `list_contracts` tool, then verify the closest template's numbers
against your part. The list reports capture requirements as well as names:
a bundled template does not imply a working protocol capture adapter.

Three kinds of contracts exist, judged by three different tools:

| kind            | what it checks             | tool                                          | examples                 |
|-----------------|----------------------------|-----------------------------------------------|--------------------------|
| timing, no kind | pulse widths in ns         | `judge_contract`, `judge_file`, `check_capture` | ws2812b, dshot, pwm-fan, servo |
| serial          | regexes against a log      | `judge_serial`, `check_serial`                | boot, esp32, nrf54l15    |
| events          | relationships between decoded events | `judge_events`, the `temporal` CLI  | spi-frame                |

The `kind` line selects which schema validates the file, and the MCP tool
you call picks the judge: `judge_contract` runs the edge table,
`judge_serial` runs expect/forbid, `judge_events` runs temporal assertions.
Use explicit `kind: serial` or `kind: events` for those schemas; timing is the
default. The judges reject the wrong contract kind instead of returning an
empty or misleading verdict. A 0/1 capture (`judge_file`, `check_capture`,
`hwcontract --check`) can be judged against any timing contract whose edges
say what they measure; see [Measuring a capture](#measuring-a-capture).

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
  "183ns short (typ 600), 33ns past min" tells the agent which way to move
  the delay and how far it is from passing.
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

## Measuring a capture

A 0/1 capture is measured by the contract, not by protocol code, so a part
nobody has added to hwcontract works from its datasheet numbers alone. Each
timing edge says what it times:

- `measure: high`: the width of a high run.
- `measure: low`: the width of a low run.
- `measure: period`: rising edge to rising edge, within a frame. A period
  that would span a frame delimiter (see below) is not measured: the gap is
  its delimiter's to judge.
- `after: EDGE` (high/low only): the edge applies only to the run right after
  `EDGE`. That is how a bit's low belongs to its bit: `T1L` is `after: T1H`,
  so a stretched low after a 1-bit counts against `T1L` however long it is.
- `required: false`: the edge explains pulses when they appear but its absence
  is not `MISSING`. DShot's `FRAME_GAP` and an IR remote's repeat code are
  this kind of edge.

`T0H`, `T1H`, `T0L`, `T1L` and `RESET` need no annotation: `T0H`/`T1H` are
highs, `T0L`/`T1L` the lows after them, `RESET` a low. A new protocol:

```yaml
contract: my-sensor                 # 3us/9us highs for 0/1, 4us low, >=100us idle
headroom_pct: 10
edges:
  - {name: ZERO,  measure: high, min: 2500,   typ: 3000,   max: 3500}
  - {name: ONE,   measure: high, min: 8000,   typ: 9000,   max: 10000}
  - {name: SPACE, measure: low,  min: 3500,   typ: 4000,   max: 4500}
  - {name: GAP,   measure: low,  min: 100000, typ: 100000, max: null}
```

How every run is placed:

1. It goes to the edge whose window holds it; if several do, the nearest typ.
2. If it is more than 2x outside every window of its measure (`TOLERANCE` in
   `measure.py`), it is **unmatched**: the contract does not explain it, and an
   `unmatched low` (or `high`, `period`) row fails the verdict. Unmatched
   pulses are grouped by width, so 2-3us stalls and 6-9us gaps are two rows.
   When one spread of widths straddles the 2x line (stalls of 1.8-3us against
   a 1us max), the out-of-window rows and the unmatched row are one problem,
   and the verdict adds a note saying so.
3. Otherwise it is blamed on the nearest edge allowed after the previous run,
   so a stretched low counts against its own bit's low. Width alone decides
   unmatched; `after` only decides which edge is blamed.
4. A run cut off by the start or end of the capture has a true width of at
   least what was seen. It counts only for an open-ended edge (`max: null`,
   such as a reset gap), only once it reaches that edge's min, and only when
   the capture holds no complete run of that edge. The idle a capture starts
   in stands in for RESET in a one-frame capture, but never passes for a good
   gap between frames when the real gaps are short.

Every failing row locates its pulses: the start time in the capture, and when
the contract has a frame delimiter (an open-ended edge longer than every
bounded edge of its measure, like `RESET`) the frame number and how many highs
of that frame came before. For an LED strip that count is the bit index, so
`after 1019 highs` is LED 42, bit 11. `--json` keeps up to 20 locations per row.

Edges that cannot be read from one line (I2C setup/hold, STEP/DIR timing,
servo positions that are different commands) have no `measure:`; judge them
from observations JSON produced by your own adapter. `servo.contract.yaml`
uses `PULSE_MIN`, `PULSE_NEUTRAL`, `PULSE_MAX`, `FRAME`; `hc-sr04` uses `TRIG`,
`ECHO`, `CYCLE` on two lines.

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
itself errors, the edge fails. Logs longer than 1,000,000 characters are
rejected with an error; no suffix is silently discarded before matching. With `google-re2` installed (the `untrusted` extra) matching is
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
  hint, the range of the bad ones and how far past the limit the worst is
  ("3 of 1200 pulses out of window (0.25%): 1012-1090ns, up to 1.09x max").
- More than 1% of pulses outside the window: `FAIL`, even when the median sits
  dead on typ. A driver that glitches one bit in a hundred is not a driver
  you ship.

The 1% threshold is `VIOLATION_FAIL_PCT` in `judge.py`. Observations without
raw widths (hand-written summaries passed to `judge_contract`) are judged on
the median alone; the capture tools always carry widths, so live judgments
always see the tails.

Hand-written observations are validated before judging: names must be
unique (a second `T0H` would otherwise replace a failing first one), and
`p50`/`value` and every width must be a number. A NaN or infinite width counts
as a pulse outside the window.

### Sample resolution

A width measured from a logic capture is a whole number of samples, so the true
width is only known to plus or minus one sample period. The capture adapters
record that period as `resolution_ns` on every observation, and the judge
respects it:

- `FAIL` needs the median outside the window by more than one sample.
- Outside by one sample or less, or inside but within one sample of `min` or
  `max`: `MARGINAL`, with a hint to capture at a higher samplerate.
- Per pulse, only pulses outside by more than one sample count as violations.
  Pulses outside by less are reported separately as `unresolved`. More than 1%
  violations still FAILs, even when the median is within one sample.

At 24MHz one sample is 41.7ns, which is a large slice of a WS2812's 300ns
window. If a verdict says "within one sample", capture faster rather than
loosening the contract. Observations without `resolution_ns` are judged
exactly as given.

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
  [start_ns, end_ns] span. The while-events need real durations: a `while`
  that matches only instant events, such as raw pin edges, FAILs because
  nothing can overlap it. Use `when` + `forbid` + `before` for those.
- `when` + `forbid` + `before`: no forbidden event may start in the
  `before` window preceding each trigger. This is the setup-time check.

Every trigger is checked, not a sample. Zero triggers fails: a contract
that never fires proves nothing. Violations name the first failure with its
exact timestamps, so the trace window is one search away.

### Raw SPI capture to a verdict

The bundled `spi-frame` contract checks **raw transitions**, not decoded bytes.
For a saved logic capture with D0=SCK, D1=MOSI, D2=active-low CS, export every
sample with channel labels:

```bash
sigrok-cli -i capture.sr -C D0,D1,D2 \
    -O csv:header=false:label=channel:time=false:dedup=false > spi.csv
```

These [sigrok CSV options](https://github.com/sigrokproject/libsigrok/blob/master/src/output/csv.c)
keep one row per sample, omit metadata comments, and label channels by name.
The first row must be `D0,D1,D2`; subsequent rows contain exactly three `0`/`1`
values. Confirm the wiring and names in your capture; edit the map below if
they differ. Use a single continuous capture with idle samples before CS
asserts and samples through CS release. Do not deduplicate rows or concatenate
discontinuous acquisition windows. Malformed/empty input must be fixed or
recaptured; do not discard bad rows, which would shift event times.

Run this complete conversion and judgment after `pip install hwcontract`,
from the directory containing `spi.csv`:

```python
import json
from pathlib import Path
from hwcontract.contracts import EXAMPLES
from hwcontract.edges import read_multi_csv, edge_events
from hwcontract.judge import load_contract, sha256_of, verdict, render_verdict
from hwcontract.temporal import judge_events, render_events

# Set this to the ACTUAL capture sample rate, not the SPI clock rate.
samplerate_hz = 100_000_000
channels = read_multi_csv("spi.csv")
wire_names = {"D0": "spi.sck", "D1": "spi.mosi", "D2": "gpio.cs"}
if set(channels) != set(wire_names):
    raise ValueError(f"expected channels {list(wire_names)}, got {list(channels)}")
events = edge_events({wire_names[k]: v for k, v in channels.items()},
                     dt_ns=1e9 / samplerate_hz)
Path("spi.edges.json").write_text(json.dumps(events, indent=2))
contract_path = str(EXAMPLES / "spi-frame.contract.yaml")
results, ok = judge_events(load_contract(contract_path), events)
bundle = verdict(contract_path, results, ok, input_kind="events",
                 input_sha256=sha256_of(events),
                 capture_sha256=sha256_of(Path("spi.csv").read_bytes()),
                 samplerate_hz=samplerate_hz, channel_map=wire_names)
Path("spi.verdict.json").write_text(json.dumps(bundle, indent=2))
print(render_verdict(bundle, render_events(results)))
raise SystemExit(0 if ok else 1)
```

The 100 MHz example has 10 ns sample spacing. A 24 MHz capture has about
41.7 ns spacing and cannot establish a 20 ns setup margin. Choose adequate
capture resolution and adjust the contract's clock polarity, setup and frame
windows for your device. The template requires a CS falling edge within 10 us
before **each** rising clock edge and CS release within 4–30 us; it is not a
universal SPI specification. Keep the raw capture and `spi.verdict.json`.
`spi.edges.json` is also accepted by MCP `judge_events`, the pytest fixture,
and the Action's `events` input. For a hardware-free example of this path:
`python3 -m hwcontract.demo spi`.

### Semantic decoder annotations

For semantic checks, export the real decoder output separately:

```bash
sigrok-cli -i capture.sr -P spi:clk=D0:mosi=D1:cs=D2 -A spi \
    --protocol-decoder-jsontrace > decoded.json
```

Inspect what the decoder actually emits:

```python
from hwcontract.jsontrace import load
for event in load("decoded.json")[:10]:
    print(event["source"], event["type"], event["fields"])
```

For B/E pairs, `pid=spi` and `tid=Data` become source `spi`, type `Data`
(selector `spi.Data`), with the annotation text in `fields.text`. They do not
become `spi.sck.rising` or `gpio.cs.falling`. Write a separate events contract
using the observed decoder row names and semantics, then run:

```bash
python3 -m hwcontract.temporal my-decoder.contract.yaml decoded.json
```

The demo's artificial B/E annotations only test the importer. They must not be
presented as evidence that a real SPI decoder exposes pin transitions.

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

Timing captures have the same shape of problem. Runs truncated by capture
start and stop are only judged by open-ended edges, so a latch gap at the
capture edge still counts as RESET. One WS2812 LED takes about
30us of wire time (24 bits at 1.25us each) plus a 50us reset at the end of the
frame. A 200,000-sample capture at 24MHz is 8.3ms: comfortably one frame for a
long strip and its reset. Capture enough samples for one full frame plus the
reset gap, or the frame ends up mid-capture and the reset never appears.

## Chip vs clone drift

Contracts are chip-specific. The bundled demo proves it with a real signal:
`python3 -m hwcontract.demo ws2812b` downloads a genuine 24-LED capture and judges it
against two contracts. The generic WS2812 contract rejects it: T1L measures
417ns against a 450ns minimum, 33ns short. That miss is under one 41.7ns sample
at 24MHz, so the row is MARGINAL rather than FAIL, and the verdict still fails.
Same wire, same capture, and the WS2812B contract passes it. A WS2812B is not a WS2812: shorter lows, different
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

1. Take a real capture. The installed WS2812-family demo downloads a pinned one:

```bash
python3 -m hwcontract.demo ws2812b
```

   This downloads the real 24-LED capture, measures it, and judges it against
   the generic and WS2812B contracts. Replace the contract path with yours to
   see your numbers against real silicon. The first run requires network;
   `--offline` reuses the verified cache. See [provenance and license](../hwcontract/demo-provenance.md).
   `python3 -m hwcontract.judge --demo` is only a tiny synthetic self-test with
   an intentionally missing RESET observation, not this recorded replay.

2. Or capture your own and judge it from the command line:

```bash
sigrok-cli --driver fx2lafw --channels D0 --config samplerate=24M \
    --samples 200000 -O csv > capture.csv
hwcontract --check my.contract.yaml capture.csv --samplerate 24MHz
```

   The capture is measured the way your edges say. Observations JSON from
   your own adapter works the same way: `hwcontract --check my.contract.yaml
   observations.json`.

3. Serial contracts, judged the same way the demo tape does:

```bash
python3 -m hwcontract.demo serial   # offline synthetic PASS/FAIL demonstration
```

   For your own board, pass a captured log to MCP `judge_serial` with
   `contract_path="boot"`, or use the pytest fixture's `serial` method. Test
   a good log and a log with the failure symptom; retain the evidence.

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

6. Keep project-specific contracts in your project. A new bundled template
   belongs in `hwcontract/examples/` only with a working judge/adapter path
   and known-good/known-bad capture tests; adding YAML alone does not implement
   capture support. `pyproject.toml` packages `examples/*.yaml`.

CI replay of unchanged captures checks those recordings and the contract, not
newly built firmware. To test a new firmware revision, flash it and take a
fresh capture. Keep the firmware revision with the capture and verdict hashes.

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
