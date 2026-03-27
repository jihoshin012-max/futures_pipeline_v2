# SKILL: NQ Zone Touch Data Preparation
version: 1.0
last_reviewed: 2026-03-20

## Purpose

Automate the zone touch data preparation pipeline: load raw Sierra Chart exports (ZRA touches, ZB4 signals, rotational bar data), merge them, verify data quality, split into analysis periods, and output clean files ready for feature engineering and backtesting.

## Trigger Conditions

Use this skill when the user:
- Mentions processing zone data, ZRA data, ZB4 data, or zone touch files
- Asks to prepare data for the zone touch pipeline
- Uploads files matching: `NQ_ZRA_Hist_*`, `NQ_ZB4_signals_*`, `NQ_BarData_250vol_rot_*`
- References "Prompt 0" or "data prep" in the context of the zone touch strategy
- Asks to add a new period (P3, P4, etc.) to existing zone data

## Input Files

| Pattern | Description | Source |
|---------|-------------|--------|
| `NQ_ZRA_Hist_{period}.csv` | Zone touches with outcomes (33 cols) | ZoneReactionAnalyzer |
| `NQ_ZB4_signals_{period}.csv` | Zone touches with scoring features (30 cols) | ZoneBounceSignalsV4 |
| `NQ_BarData_250vol_rot_{period}.csv` | 250-volume rotational bar data (35 cols) | Sierra Chart |

Period labels: P1, P2, P3, etc. Each covers a contiguous date range.

Located in: `stages/01-data/data/touches/` (ZRA, ZB4) and `stages/01-data/data/bar_data/volume/` (bar data).

## Output Files

All outputs go to `stages/01-data/output/zone_prep/`:

| File | Description |
|------|-------------|
| `NQ_merged_{subperiod}.csv` | Merged zone touches for one sub-period (P1a, P1b, P2a, P2b) |
| `NQ_bardata_{parent}.csv` | Rotational bar data (copied unchanged from input, one per parent period) |
| `period_config.json` | Date boundaries, touch counts, instrument metadata |
| `data_preparation_report.md` | Full verification results and distributions |

---

## Configuration (read from project, never hardcode)

- **Instrument constants:** Read from `_config/instruments.md` (tick_size, tick_value, session times)
- **Period boundaries:** Read from `_config/period_config.md` (start/end dates per period)
- **Data paths:** Read from `_config/data_registry.md` (file patterns, source IDs)
- **Split rule:** Read from `_config/period_config.md` (`p1_split_rule` field)

Current NQ constants (for reference only -- always read from registry):
- tick_size = 0.25
- tick_value = $5.00
- cost_ticks = 3

---

## Column Schemas

### ZRA CSV (33 columns -- primary data source)

| # | Column | Type | Keep? |
|---|--------|------|-------|
| 1 | DateTime | timestamp | YES |
| 2 | BarIndex | int | YES |
| 3 | TouchType | string | YES (DEMAND_EDGE / SUPPLY_EDGE) |
| 4 | ApproachDir | string | YES |
| 5 | TouchPrice | float | YES |
| 6 | ZoneTop | float | YES |
| 7 | ZoneBot | float | YES |
| 8 | HasVPRay | 0/1 | YES |
| 9 | VPRayPrice | float | YES |
| 10 | Reaction | float (ticks) | YES -- max favorable excursion |
| 11 | Penetration | float (ticks) | YES -- max adverse excursion |
| 12 | ReactionPeakBar | int | YES |
| 13 | ZoneBroken | 0/1 | YES |
| 14 | BreakBarIndex | int | YES |
| 15 | BarsObserved | int | YES |
| 16 | TouchSequence | int | YES |
| 17 | ZoneAgeBars | int | YES |
| 18 | ApproachVelocity | float | YES |
| 19 | TrendSlope | float | YES |
| 20 | SourceChart | int | DROP (SC internal) |
| 21 | SourceStudyID | int | DROP (SC internal) |
| 22 | SourceLabel | string | YES (15m-720m TF labels) |
| 23-29 | RxnBar_30 -- RxnBar_360 | float | YES |
| 30-33 | PenBar_30 -- PenBar_120 | float | YES |

### ZB4 CSV (30 columns -- supplementary)

Only TWO columns are pulled from ZB4:

| # | Column | Pull? | Notes |
|---|--------|-------|-------|
| 1 | DateTime | Match key only | |
| 5 | TouchPrice | Match key only | |
| 3 | TouchType | Match key only | |
| 16 | SourceLabel | Match key only | |
| 18 | TFConfluence | **YES** | # of TFs with zone at this price |
| 19 | CascadeState | **YES -- critical** | PRIOR_HELD / NO_PRIOR / PRIOR_BROKE |

NEVER pull: ModeAssignment, QualityScore, ContextScore, TotalScore, or any other ZB4 column.

### Rotational Bar Data (35 columns)

| Cols | Content |
|------|---------|
| 1-2 | Date, Time |
| 3-6 | Open, High, Low, Last |
| 7 | Volume |
| 8 | # of Trades |
| 9-11 | OHLC Avg, HLC Avg, HL Avg |
| 12-13 | Bid Volume, Ask Volume |
| 14-21 | Zig Zag indicators (Line Length at col 17 is non-zero only at swing endpoints) |
| 22 | Sum |
| 23-26 | Channel set 1 (narrow): Top, Bottom, Top MovAvg, Bottom MovAvg |
| 27-30 | Channel set 2 (wide): Top, Bottom, Top MovAvg, Bottom MovAvg |
| 31-34 | Channel set 3 (medium): Top, Bottom, Top MovAvg, Bottom MovAvg |
| 35 | ATR |

Duplicate column names exist (cols 22-33). Load with `header=0` + rename by positional index.

---

## Merge Logic

### Match Key Construction

```
key = floor(DateTime, 'min') | round(TouchPrice, 2) | TouchType | SourceLabel
```

All four parts are required. SourceLabel distinguishes touches from different TFs at the same price/time. TouchPrice must be rounded to 2 decimal places to avoid float precision mismatches.

### Merge Steps

1. Left join: ZRA <- ZB4 on match key (ZRA is primary -- every ZRA row kept)
2. Pull only `CascadeState` and `TFConfluence` from ZB4
3. Unmatched ZRA rows: set CascadeState = "UNKNOWN", TFConfluence = -1
4. Multiple ZB4 matches to one ZRA key: keep first ZB4 match
5. Multiple ZRA rows sharing a key: keep ALL (print warning if count > 1% of total)

### Expected Match Rates

- P1: 100% (verified on 4,964 rows)
- P2: 99.8% (7 unmatched -- first 4hrs of Dec 15, ZB4 state building)
- New periods: expect >= 99.5%. Investigate if below 99%.

---

## Derived Columns

| Column | Formula | Notes |
|--------|---------|-------|
| ZoneWidthTicks | (ZoneTop - ZoneBot) / tick_size | tick_size from instruments.md |
| SBB_Label | "SBB" if ZoneBroken=1 AND (BreakBarIndex - BarIndex) <= 1, else "NORMAL" | Informational -- do NOT filter |
| RotBarIndex | Index of nearest bar in parent period bar data where bar timestamp <= touch DateTime | P1a and P1b both reference NQ_bardata_P1.csv |
| Period | Sub-period label (P1a, P1b, P2a, P2b) | Assigned after split |

### Columns Dropped

- SourceChart (ZRA col 20) -- SC internal
- SourceStudyID (ZRA col 21) -- SC internal

---

## VP_RAY Touch Handling

ZRA may contain VP_RAY touches (TouchType = "VP_RAY"). Filter these out BEFORE merging. Print count removed per period. Confirm only DEMAND_EDGE and SUPPLY_EDGE remain.

---

## Period Splitting

### Rules

1. Each parent period (P1, P2) splits into two halves (P1->P1a+P1b, P2->P2a+P2b)
2. Split method: read `p1_split_rule` from `_config/period_config.md` (default: midpoint)
3. For midpoint: sort by DateTime, find median, round to nearest day boundary
4. Sub-periods must be contiguous and non-overlapping
5. Determined from data, never hardcoded

### Period Roles

- **P1a:** Calibrate everything (scoring, features, exits)
- **P1b:** Validate calibration (frozen P1a params, check overfit)
- **P2a:** First holdout (one-shot, no iteration)
- **P2b:** Second holdout (independent confirmation)

---

## Verification Checks (all must pass)

| Check | Criterion |
|-------|-----------|
| No nulls in key columns | TouchPrice, ZoneTop, ZoneBot, Reaction, Penetration all non-null |
| Valid TouchType | All values in {DEMAND_EDGE, SUPPLY_EDGE} |
| Valid SourceLabel | All values in {15m, 30m, 60m, 90m, 120m, 240m, 360m, 480m, 720m} |
| Valid CascadeState | All values in {PRIOR_HELD, NO_PRIOR, PRIOR_BROKE, UNKNOWN} |
| Non-negative outcomes | Reaction >= 0 and Penetration >= 0 |
| Zone ordering | ZoneTop > ZoneBot for all rows |
| Date range | All rows within expected period boundaries |
| Minimum sample | Each sub-period has >= 500 touches |

Fail loudly on any check failure -- do not proceed.

---

## Output Merged CSV Column Order (canonical)

DateTime, BarIndex, TouchType, ApproachDir, TouchPrice, ZoneTop, ZoneBot, HasVPRay, VPRayPrice, Reaction, Penetration, ReactionPeakBar, ZoneBroken, BreakBarIndex, BarsObserved, TouchSequence, ZoneAgeBars, ApproachVelocity, TrendSlope, SourceLabel, RxnBar_30, RxnBar_60, RxnBar_90, RxnBar_120, RxnBar_180, RxnBar_240, RxnBar_360, PenBar_30, PenBar_60, PenBar_90, PenBar_120, ZoneWidthTicks, CascadeState, TFConfluence, SBB_Label, RotBarIndex, Period

Do NOT reorder columns. This schema is canonical.

---

## Reporting Outputs

### data_preparation_report.md

1. **File inventory:** all input files with row counts and date ranges
2. **Merge results:** matched/unmatched counts per period, unmatched list (if <= 20)
3. **Distributions per sub-period:**
   - TouchType breakdown
   - SourceLabel (TF) breakdown
   - CascadeState breakdown
   - SBB rate per TF
   - ZoneWidthTicks stats (min, max, mean, median)
   - TouchSequence distribution (1, 2, 3, 4, 5+)
   - HasVPRay rate
4. **Bar data join:** match rate, max gap
5. **Period split:** boundaries, touch counts per sub-period
6. **Sanity check results:** pass/fail for each check

### period_config.json

```json
{
  "instrument": "NQ",
  "tick_size": 0.25,
  "tick_value_dollars": 5.00,
  "bar_type": "250-volume",
  "periods": {
    "P1a": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD", "touches": 0, "parent": "P1"},
    "P1b": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD", "touches": 0, "parent": "P1"},
    "P2a": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD", "touches": 0, "parent": "P2"},
    "P2b": {"start": "YYYY-MM-DD", "end": "YYYY-MM-DD", "touches": 0, "parent": "P2"}
  },
  "bar_data_files": {
    "P1": "NQ_bardata_P1.csv",
    "P2": "NQ_bardata_P2.csv"
  },
  "total_touches": 0,
  "generated_at": "ISO-8601 timestamp"
}
```

---

## Execution Sequence

Follow these steps in order. Each depends on the previous.

### Step 1: Load all input files

Load ZRA, ZB4, and bar data CSVs for each period. Print row counts and date ranges.

Read tick_size from `_config/instruments.md`. Read period boundaries from `_config/period_config.md`.

### Step 2: Filter VP_RAY touches from ZRA

Remove rows where TouchType = "VP_RAY". Print count removed per period. Confirm all remaining rows are DEMAND_EDGE or SUPPLY_EDGE.

### Step 3: Trim ZB4 to ZRA date ranges

ZB4 may extend beyond ZRA's boundaries. Trim to match.

> REMINDER: Only CascadeState and TFConfluence come from ZB4. Do NOT pull ModeAssignment, QualityScore, ContextScore, TotalScore, or any other ZB4 column.

### Step 4: Build match keys and merge

Key = `floor(DateTime, 'min') | round(TouchPrice, 2) | TouchType | SourceLabel`

Left join ZRA <- ZB4. Pull CascadeState and TFConfluence only. Set UNKNOWN/-1 for unmatched. Keep first ZB4 match on duplicates. Keep all ZRA rows even if key is shared (print warning with count if > 1% of total).

> CONFIRM: Only CascadeState and TFConfluence were pulled from ZB4. No scoring columns (ModeAssignment, QualityScore, ContextScore, TotalScore) present in merged data.

### Step 5: Compute derived columns

- ZoneWidthTicks = (ZoneTop - ZoneBot) / tick_size
- SBB_Label = "SBB" if ZoneBroken=1 AND (BreakBarIndex - BarIndex) <= 1, else "NORMAL"
- Drop SourceChart and SourceStudyID

> CONFIRM: SBB touches are labeled but NOT removed. All SBB_Label = 'SBB' rows remain in the dataset.

### Step 6: Join rotational bar data

For each touch, find the nearest bar where bar timestamp <= touch DateTime. Store as RotBarIndex (row index into parent period's bar data file). P1a and P1b touches both index into NQ_bardata_P1.csv.

Print match rate and max timestamp gap. Flag any touch with gap > 60 seconds.

### Step 7: Split into sub-periods

Read split rule from `_config/period_config.md`. For midpoint: find median DateTime per parent period, round to day boundary, assign Period labels.

Print touch counts per sub-period.

> CONFIRM: Period boundaries determined from data (median DateTime), not hardcoded. Print the split dates.

### Step 8: Run verification checks

Execute all checks from the Verification Checks section. Fail loudly if any check fails.

> CONFIRM: CascadeState contains UNKNOWN values for unmatched rows. These are valid and expected (<= 7 rows in P2).

SBB touches are NOT filtered. UNKNOWN cascade is valid.

### Step 9: Save output files

- 4 merged CSVs (one per sub-period) to `stages/01-data/output/zone_prep/`
- Copy bar data files as NQ_bardata_P1.csv, NQ_bardata_P2.csv (unchanged pass-through)
- Generate period_config.json
- Generate data_preparation_report.md

Bar data covers the full parent period (not split) because the simulator needs to walk past touch bars into subsequent bars.

---

## Anti-Patterns (never do these)

- Do NOT filter SBB touches. They stay. Downstream decides.
- Do NOT pull ZB4 scoring columns (ModeAssignment, QualityScore, ContextScore, TotalScore).
- Do NOT hardcode date boundaries. Data determines splits.
- Do NOT drop unmatched ZRA rows. They get UNKNOWN cascade and stay.
- Do NOT reorder columns. Schema above is canonical.
- Do NOT hardcode tick_size, cost_ticks, or session times. Read from `_config/instruments.md`.

---

## Self-Check (run before saving outputs)

- [ ] All input files loaded with correct row counts
- [ ] VP_RAY touches filtered before merge (count printed)
- [ ] Match key includes SourceLabel (4-part key, not 3-part)
- [ ] TouchPrice rounded to 2 decimals in key construction
- [ ] Only CascadeState and TFConfluence pulled from ZB4
- [ ] No ZB4 scoring columns in output
- [ ] Unmatched ZRA rows retained with UNKNOWN cascade
- [ ] SBB touches labeled but NOT filtered
- [ ] SourceChart and SourceStudyID dropped
- [ ] RotBarIndex maps to parent period bar data file
- [ ] Period split determined from data (not hardcoded)
- [ ] Each sub-period has >= 500 touches
- [ ] All sanity checks passed
- [ ] period_config.json includes instrument, tick_size, parent references, bar_data_files
- [ ] data_preparation_report.md documents all distributions and verification results
- [ ] All 4 merged CSVs + 2 bar data files + config + report saved

---

## Extensibility

1. **New periods:** Accept any number of period pairs. Extend period_config.json accordingly.
2. **New instruments:** tick_size is parameterized via instruments.md. Zone width calculation adapts.
3. **Incremental updates:** Support per-period execution (re-run P2 merge without touching P1).
4. **Different splits:** Support configurable split counts (2, 3, or no split) via period_config.md.
