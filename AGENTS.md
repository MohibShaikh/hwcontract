# AGENTS.md

hwcontract judges hardware behavior against YAML contracts: pulse widths,
serial boot logs, and relationships between decoded events. Verdicts are
PASS / MARGINAL / FAIL / MISSING. MARGINAL and MISSING fail the verdict.

## Layout

- `hwcontract/judge.py` — pure judge: timing edges, serial regex, contract
  validation, evidence hashes. The enums and `validate_contract` live here.
- `hwcontract/temporal.py` — cross-event assertions (`when/require/within`,
  `forbid/while`, `forbid/before`), latency percentiles, first-failure hints.
- `hwcontract/jsontrace.py` — sigrok jsontrace (Google Trace Event) importer,
  microseconds to nanoseconds.
- `hwcontract/sigrok_adapter.py` — logic-analyzer capture to pulse
  distributions (T0H/T1H/T0L/T1L/RESET + raw widths).
- `hwcontract/serial_adapter.py` — serial capture and replay.
- `hwcontract/server.py` — MCP server (stdio + HTTP). Tools are entries in
  `TOOLS`; add a tool there, nothing else.
- `hwcontract/examples/` — the 28 bundled contracts; they ship in the wheel.
- `tests/` — pytest suite. `pytest-hwcontract/` — the plugin package with its
  own suite under `pytest-hwcontract/tests/`.
- `demo/` — runnable demos (`spi_dma_temporal.py`, `ws2812b_neopixel.py`
  downloads a real capture, `serial_boot.py`), the vhs tape and the GIF.
- `action/check.py` + `action.yml` — the GitHub Action.
- `docs/writing-contracts.md` — the contract-writing guide.

## Commands

```bash
pytest -q                          # root suite (testpaths pinned to tests/)
pytest pytest-hwcontract/tests -q  # plugin suite; needs pip install -e . then
                                   #   pip install -e ./pytest-hwcontract --no-deps
python3 -m hwcontract.server --selftest
python3 -m hwcontract.judge --demo         # also sigrok_adapter / serial_adapter
python3 demo/spi_dma_temporal.py           # offline, deterministic
uv build
```

CI (`.github/workflows/tests.yml`) runs all of it on push/PR for Python 3.9
and 3.13. The PyPI publish job waits for the test job.

## Release flow

1. Bump the version in THREE files: `pyproject.toml`,
   `hwcontract/__init__.py`, `server.json`.
2. Commit `Bump to X.Y.Z`, tag `vX.Y.Z`, push. CI tests, then publishes to
   PyPI via trusted publishing (OIDC, no tokens).
3. Plugin: bump `pytest-hwcontract/pyproject.toml` and
   `pytest-hwcontract/pytest_hwcontract/__init__.py`, tag `pytest-vX.Y.Z`.
4. MCP registry is manual: `./mcp-publisher login github` then
   `./mcp-publisher publish server.json` (the JWT expires fast).
5. Re-point the `action-v0` tag at the release commit and push it. Never name
   a moving ref `v*`: that glob triggers the PyPI publish workflow.
6. `docs/`, `README.md` and `SKILL.md` must say the same thing the code does;
   contract counts included.

## Invariants (do not break)

- MARGINAL fails the verdict. Uppercase enums only, serial included.
- Contracts are validated on load; a malformed contract raises
  `ContractError` listing every problem, never a KeyError mid-judgment.
- Captures are judged per pulse, not per median: more than
  `VIOLATION_FAIL_PCT` (1%) of pulses outside a window fails the edge even
  with a clean median.
- Every verdict carries evidence: contract sha256, capture/observations/log
  sha256, capture parameters, tool version, timestamp.
- The MCP stdio and HTTP loops survive any hostile request: missing tool
  name, non-object arguments, garbage lines all get error responses.
- Regexes in contract YAML use doubled backslashes (`"\\d+"`). A single
  `\d` is a YAML parse error and `\b` alone is a backspace character.
- No new example contracts without a working judge path. Two protocols done
  deeply beat twenty done decoratively.

## Contract kinds

- timing: `edges` of min/typ/max in ns, `headroom_pct`, `max: null` where the
  spec is one-sided.
- serial: `expect`/`forbid` Python regex against the capture window.
- events: `assertions` over decoded events. Selectors are `source.type` with
  an optional `field=value` tail; the last dotted component is always the
  type. `within` accepts signed durations (negative bounds look before the
  trigger). Zero triggers fail; vacuous forbids are reported in the hint.

Details and copy-paste examples: `docs/writing-contracts.md`.
