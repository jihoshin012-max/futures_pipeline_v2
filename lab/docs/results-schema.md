# Results Schema

<!--
Layer 3 reference. Defines the column format for results.tsv files.
Every experiment appends one row to lab/output/[arch]-[inst]-results.tsv
following this schema.
-->

## Columns

| Column | Type | Description |
|--------|------|-------------|
| run_id | string | Unique identifier (e.g., sha1 hash or sequential) |
| timestamp | ISO 8601 | When the experiment ran |
| arch | string | Archetype slug |
| instrument | string | Instrument slug |
| stage | string | `01-features`, `02-hypotheses`, or `03-params` |
| change_description | string | What was changed (one param, one feature) |
| metric_name | string | `spread` (stage 01), `pf` (stages 02-03) |
| metric_value | float | Measured value |
| threshold | float | Keep rule threshold for this stage |
| verdict | string | `kept`, `reverted`, `entry_time_violation`, `replication_fail` |
| n_trades | int | Number of trades in calibration run |
| calibration_range | string | Calibration date range used (e.g., "20250921-20251214") |
| notes | string | Free-text (e.g., git hash, flags) |

## Rules

- One row per experiment, no exceptions
- Never delete rows — append only
- TSV format (tab-separated), UTF-8
- Header row required as first line
