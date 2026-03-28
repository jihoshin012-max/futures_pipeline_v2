# Lab Output

Layer 4 working artifacts. Every file here changes between runs.
Read these as input, not as stable reference.

**When you add or change a file in this directory, update this README.**

## What Lives Here

Experiment results, frozen configs, findings, journals, and
baseline analysis output. Each file follows the scaffold naming
convention: `[arch]-[inst]-[type]-[status].[ext]`

## File Inventory

| File | Type | Archetype | Description |
|------|------|-----------|-------------|
| `rotational-NQ-journal.md` | journal | rotational | Strategy progression narrative |
| `zone-touch-NQ-journal.md` | journal | zone-touch | Strategy progression narrative |

| `zone-touch-NQ-baseline-raw.csv` | baseline | zone-touch | Raw trade results (381,528 rows) |
| `zone-touch-NQ-baseline-summary.md` | baseline | zone-touch | Prompt 0 summary statistics |
| `zone-touch-NQ-zone-geometry.md` | baseline | zone-touch | Zone width + nested zone analysis |
| `zone-touch-NQ-ray-analysis.md` | baseline | zone-touch | Ray availability analysis |

## Expected Files (when populated)

| Pattern | Type | Produced By |
|---------|------|-------------|
| `[arch]-[inst]-results.tsv` | results | features / hypotheses / params stages |
| `[arch]-[inst]-findings.md` | findings | Analysis after experiment runs |
| `[arch]-[inst]-features-frozen.json` | features | features stage (human approved) |
| `[arch]-[inst]-hypothesis-frozen.json` | hypothesis | hypotheses stage (human approved) |
| `[arch]-[inst]-params-frozen.json` | params | params stage (human approved) |
| `[arch]-[inst]-verify-report.md` | verify-report | Cross-language verification |
| `[arch]-[inst]-baseline-raw.csv` | baseline | dataprep prompt 0 |
| `[arch]-[inst]-baseline-summary.md` | baseline | dataprep prompt 0 |
| `[arch]-[inst]-zone-geometry.md` | baseline | dataprep prompt 0 |
| `[arch]-[inst]-ray-analysis.md` | baseline | dataprep prompt 0 |
