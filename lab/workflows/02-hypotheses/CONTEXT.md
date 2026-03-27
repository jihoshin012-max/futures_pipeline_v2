# Stage 02: Hypothesis Testing

## Inputs

- **Layer 3 (internalize):** `lab/docs/simulation-rules.md`, `_config/period-config.md`
- **Layer 4 (process):** `lab/output/[arch]-[inst]-features-frozen.json`

## Process

Edit `lab/[arch]-[inst]-hypothesis-configs.py`. One structural
parameter change per experiment (stop_ticks, leg_targets,
trail_steps, routing).

Run: `python harness/hypothesis_generator.py --archetype [arch]`

Metric: calibration PF at `experiment_cost_ticks` from `_config/instruments.md`,
minimum `min_trades` from same file.
Keep rule: PF improves by > 0.1 → keep. Else revert.
Budget: 200 experiments per archetype per calibration period.

**Replication gate:** Every hypothesis must pass on both replica
halves (A and B) of the calibration windows. Replica-B failure →
revert (hard mode) or flag as weak (review mode).
Gate mode read from `_config/period-config.md`.

## Outputs

- Append row to `lab/output/[arch]-[inst]-results.tsv`
- Append entry to `lab/output/[arch]-[inst]-journal.md` with reasoning
- When human approves: `lab/output/[arch]-[inst]-hypothesis-frozen.json`
- Promoted hypothesis flows to Stage 03 as Layer 4 input

Human approval = the frozen file exists. If no frozen file, the
output is still draft.
