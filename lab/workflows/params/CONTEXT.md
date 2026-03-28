# Stage 03: Parameter Optimization

## Inputs

- **Layer 3 (internalize):** `lab/docs/simulation-rules.md`, `lab/docs/exit-templates.md`, `_config/instruments.md`
- **Layer 4 (process):** `lab/output/[arch]-[inst]-hypothesis-frozen.json`

## Process

Edit exit params config. One parameter change per experiment.

Run: `python harness/backtest_engine.py --config [config]`

Metric: calibration PF at `experiment_cost_ticks` from `_config/instruments.md`,
minimum `min_trades` from same file.
Keep rule: PF improves by > 0.05 → keep. Else revert.
Budget: 500 experiments per archetype per IS period.

## Outputs

- Append row to `lab/output/[arch]-[inst]-results.tsv`
- Append entry to `lab/output/[arch]-[inst]-journal.md` with reasoning
- When human approves: `lab/output/[arch]-[inst]-params-frozen.json`

## Handoff to Bench

Copy `lab/output/[arch]-[inst]-params-frozen.json` to `bench/output/`.
Human approval = the frozen file exists. If no frozen file, the
output is still draft and not ready for validation.
