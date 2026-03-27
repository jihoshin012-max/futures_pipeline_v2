# Stage 04: Cross-Language Verification

**Optional.** Run when both Python and C++ implementations exist
for the same archetype+instrument. Skip if only one language exists.

## Inputs

- **Layer 3 (internalize):** `_config/instruments.md`
- **Layer 4 (process):** `lab/[arch]-[inst]-simulator.py` (Python),
  `lab/[arch]-[inst]-study.cpp` (C++),
  `lab/output/[arch]-[inst]-params-frozen.json`

## Process

Run both implementations against the same calibration data window
with the same frozen parameters.

Compare:
- Trade count: must be exact match
- Profit factor: must match within 0.01
- Individual trade entries: spot-check entry/exit bars and prices

If mismatch: identify discrepancy source (rounding, trail logic,
session boundary handling, cost calculation). Fix in the
implementation that's wrong. Re-run comparison. Iterate until match.

## Outputs

- `lab/output/[arch]-[inst]-verify-report.md`
  - Which implementations were compared
  - Calibration window used
  - Trade count comparison
  - PF comparison
  - Any discrepancies found and resolved
- Append entry to `lab/output/[arch]-[inst]-journal.md`

## Handoff to Bench

When verify passes: copy `lab/output/[arch]-[inst]-params-frozen.json`
and `lab/[arch]-[inst]-study.cpp` to `bench/output/`.

Human approval = verify-report.md exists with PASS status.
If verify fails, iterate in stage 03 or C++ development until match.
