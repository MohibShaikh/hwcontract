# pytest-hwcontract

Judge hardware captures against [hwcontract](https://pypi.org/project/hwcontract/)
contracts as ordinary pytest tests. A FAIL, MARGINAL or MISSING edge is a
failing test with the rendered verdict table in the message, so JUnit and CI
show exactly which edge failed, the measured value, and the delta.

```bash
pip install pytest-hwcontract
```

## Use

```python
def test_strip(hwcontract):
    hwcontract.check("ws2812b", "captures/strip.csv", samplerate=24_000_000)

def test_esc(hwcontract):
    hwcontract.check("dshot", "captures/esc.csv", samplerate="24MHz")

def test_strip_by_hand(hwcontract):
    obs = hwcontract.capture_csv("captures/strip.csv", samplerate=24_000_000)
    hwcontract.timing("ws2812b", obs)          # bundled contract by name
    hwcontract.timing("contracts/esc.contract.yaml", obs)   # or a path

def test_boot(hwcontract):
    hwcontract.serial("boot", open("boot.log").read())
```

Contracts resolve as: an existing path, then `--hwcontract-root <dir>`, then
the 28 contracts bundled with `hwcontract` by name.

`check` reads a file by the contract's kind and measures a capture the way
the contract's edges say, exactly like `hwcontract --check`, so any timing
contract with `measure:` edges works. `capture_csv` is the lower-level path:
it buckets with WS2812-family thresholds unless you pass others, e.g. DShot600:
`capture_csv(path, samplerate=24_000_000, high_split=937, low_split=730, reset_ns=1800)`.

A failing verdict reads like the CLI's:

```
AssertionError: verdict: FAIL  hwcontract 0.6.1  sha256: contract 3b714248c15e, input fcc02d743809
edge      window     typ   actual  status    hint
---------------------------------------------------------------------
T0H      250-550     400      333  PASS
T0L     700-1000     850      792  PASS
T1H      650-950     800      708  MARGINAL  only 58ns from min; nudge toward typ 800
T1L      300-600     450      583  MARGINAL  within one sample (41.7ns) of max; capture at a higher samplerate to resolve
RESET    >=50000   50000    60583  PASS
```

## Why marginal fails

In spec but rail-hugging works on your bench and dies in the field. The judge
fails the test on MARGINAL by design; that is the whole point of gating CI on
wire-level measurements.
