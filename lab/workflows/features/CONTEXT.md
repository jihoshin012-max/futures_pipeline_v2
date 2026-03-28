# Stage 01: Feature Screening

## Inputs

- **Layer 3 (internalize):** `lab/docs/feature-rules.md`, `_config/instruments.md`
- **Layer 4 (process):** `lab/output/[arch]-[inst]-results.tsv` (prior runs, if exists)

## Process

Edit `lab/[arch]-[inst]-feature-engine.py`. One feature change per experiment.

Run: `python harness/evaluate_features.py --archetype [arch]`

Metric: best-bin vs worst-bin predictive spread on calibration windows.
Keep rule: spread > threshold → keep. Else revert.
Budget: 300 experiments per calibration period.

Features must be entry-time computable. The evaluator truncates bar
data to the entry bar — any feature reading beyond that boundary
raises an error and the experiment auto-reverts.

## Outputs

- Append row to `lab/output/[arch]-[inst]-results.tsv`
- Append entry to `lab/output/[arch]-[inst]-journal.md` with reasoning
- When human approves: `lab/output/[arch]-[inst]-features-frozen.json`
- Frozen features flow to Stage 02 as Layer 4 input

Human approval = the frozen file exists. If no frozen file, the
output is still draft.
