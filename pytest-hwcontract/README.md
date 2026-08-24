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
    obs = hwcontract.capture_csv("captures/strip.csv", samplerate=24_000_000)
    hwcontract.timing("ws2812b", obs)          # bundled contract by name
    hwcontract.timing("contracts/esc.contract.yaml", obs)   # or a path

def test_boot(hwcontract):
    hwcontract.serial("boot", open("boot.log").read())
```

Contracts resolve as: an existing path, then `--hwcontract-root <dir>`, then
the 28 contracts bundled with `hwcontract` by name.

A failing verdict reads like the CLI's:

```
Failed: hwcontract verdict FAIL (4f2b9c1a0e7d)
edge        typ   actual  status    hint
------------------------------------------------------------
T1L         450   583     MARGINAL  only 17ns from max; nudge toward typ 450
```

## Why marginal fails

In spec but rail-hugging works on your bench and dies in the field. The judge
fails the test on MARGINAL by design; that is the whole point of gating CI on
wire-level measurements.
