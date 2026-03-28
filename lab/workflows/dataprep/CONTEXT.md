# Stage: Data Preparation

## What This Stage Is

Prepare raw source data for baseline analysis and backtesting.
Load, filter, validate, join, and output clean files. Runs before
the experiment pipeline (features -> hypotheses -> params).

Each archetype+instrument combination has its own prompt spec
defining the data preparation and baseline analysis steps.

---

## Inputs

- **Layer 3 (internalize):** `_config/instruments.md`, `_config/period-config.md`
- **Layer 4 (process):** Raw data files from `data/` (ZTE, 1-tick, rays, etc.)

## Process

Each prompt spec (`.md`) defines the full procedure. The companion
`.py` implements it. Run the Python script to execute.

Prompt specs follow the naming pattern:
`[arch]-[inst]-prompt-[n]-[name].md`

## Outputs

- Baseline raw results -> `lab/output/`
- Summary statistics -> `lab/output/`
- Zone geometry analysis -> `lab/output/`
- Journal entry -> `lab/output/[arch]-[inst]-journal.md`
- Audit entry -> `audit/audit_log.md`

## Current Prompts

| Prompt | Archetype | Status |
|--------|-----------|--------|
| `zone-touch-NQ-prompt-0-baseline` | zone-touch | draft |
