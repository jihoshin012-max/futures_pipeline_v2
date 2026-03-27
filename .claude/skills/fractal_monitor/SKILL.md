---
name: fractal-monitor
description: Run NQ fractal structure analysis on 1-tick data and compare against stored baseline to detect structural drift. Produces quarterly reports with STABLE/DRIFT/BREAK verdicts for the six structural facts underpinning the rotation strategy.
---

# NQ Fractal Structure Monitor

## What This Skill Does

Runs the full four-part NQ fractal structure analysis on 1-tick bar data and compares results against a stored baseline (founding analysis: Sept 2025 - Mar 2026). Flags any structural drift that could invalidate rotation strategy parameters.

## When To Use

- Quarterly data review (new ~3 months of 1-tick data available)
- Before re-calibrating strategy parameters
- After significant market regime changes
- To validate that the six structural facts still hold

## The Six Structural Facts Monitored

1. **Self-Similarity** — Swing size distributions maintain consistent shape across 7 threshold scales (3-50pt)
2. **Completion Degradation** — Parent-scale moves complete at predictable rates conditioned on child retracement count (HIGHEST PRIORITY)
3. **Parent-Child Ratio** — The 25pt/10pt pair maintains the best completion rate at 1 retracement
4. **Waste %** — Retracement waste stays within 38-52% across parent-child pairs
5. **Time Stability** — Completion rates are stable across RTH time blocks (low spread)
6. **Half-Block Curve** — The progress-to-completion curve maintains its acceleration shape past 50%

## Usage

### Run quarterly analysis with baseline comparison
```bash
python .claude/skills/fractal_monitor/scripts/run_fractal_analysis.py \
  --data-path "stages/01-data/data/bar_data/tick/" \
  --date-range "2026-03-14 to 2026-06-13" \
  --session all \
  --baseline ".claude/skills/fractal_monitor/baseline/baseline_2025Q4_2026Q1.json" \
  --output ".claude/skills/fractal_monitor/output/2026_Q2/"
```

### Run standalone (no baseline comparison)
```bash
python .claude/skills/fractal_monitor/scripts/run_fractal_analysis.py \
  --data-path "stages/01-data/data/bar_data/tick/" \
  --session RTH \
  --output ".claude/skills/fractal_monitor/output/standalone/"
```

### Run specific parts only
```bash
python .claude/skills/fractal_monitor/scripts/run_fractal_analysis.py \
  --data-path "..." \
  --parts 1,2 \
  --output output/
```

### Save results as new baseline
```bash
python .claude/skills/fractal_monitor/scripts/run_fractal_analysis.py \
  --data-path "..." \
  --save-baseline \
  --output output/
```

## CLI Arguments

| Arg | Required | Default | Description |
|-----|----------|---------|-------------|
| `--data-path` | Yes | — | Directory with NQ_BarData_1tick_*.csv files |
| `--date-range` | No | full dataset | "YYYY-MM-DD to YYYY-MM-DD" |
| `--session` | No | all | RTH, ETH, Combined, or all |
| `--baseline` | No | — | Path to baseline JSON for drift detection |
| `--prev-baseline` | No | — | Previous quarter baseline for short-term comparison |
| `--output` | Yes | — | Output directory |
| `--parts` | No | 1,2,3,4 | Comma-separated parts to run |
| `--save-baseline` | No | false | Save results as new baseline JSON |

## Drift Verdicts

Each fact gets one of three verdicts:
- **STABLE** — Within tolerance of baseline
- **DRIFT** — Moderate shift, monitor closely
- **BREAK** — Exceeds safe bounds, strategy re-evaluation required

Overall report verdict:
- **ALL_STABLE** — All 6 facts stable
- **DRIFT_DETECTED** — Any drift, no breaks
- **STRUCTURE_BREAK** — Any break (Fact 2 is highest priority)

## Output Files

- `fractal_quarterly_YYYY_QN.md` — Full markdown report with drift verdicts
- `fractal_quarterly_YYYY_QN.json` — Machine-readable results (can become new baseline)
- `part4_timeofday_30min.csv` / `part4_timeofday_60min.csv` — Time-of-day data

## Dependencies

- numpy, pandas, numba, scipy, matplotlib
- 1-tick NQ bar data in Sierra Chart CSV format (Date, Time, Open, High, Low, Last, Volume, ...)

## Data Size Warning

6 months of 1-tick NQ data is ~60M rows. The script uses:
- Chunked CSV loading with minimal columns (Date, Time, Last only)
- float32 dtypes where possible
- Numba-accelerated zig-zag (processes 60M rows in ~1 second per threshold)
- Sequential session processing to limit peak memory

Expect ~6-8 minutes for data loading, ~3 seconds for all zig-zag passes.

## Baseline Files

Stored in `baseline/`. Each covers a date range:
- `baseline_2025Q4_2026Q1.json` — Founding analysis (Sept 2025 - Mar 2026)
- Future baselines named `baseline_YYYYQN_YYYYQN.json`

Always compare against BOTH the founding baseline (long-term drift) and the previous quarter (short-term changes).
