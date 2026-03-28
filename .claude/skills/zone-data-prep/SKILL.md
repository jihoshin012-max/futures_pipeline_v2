# SKILL: NQ Zone Touch Data Preparation

version: 2.0
last_reviewed: 2026-03-27

## Purpose

Load ZTE touch data, 1-tick bar data, 250-volume bar data, and ray
context data. Filter, validate, join, and output clean files ready
for baseline analysis and backtesting in the futures pipeline scaffold.

## Trigger Conditions

Use this skill when the user:
- Mentions processing zone data, ZTE data, or zone touch files
- Asks to prepare data for the zone touch pipeline
- References "data prep" or "onboard zone data"
- Asks to add new period data to existing zone touch files

## Source Data

All source files live in `data/` following the scaffold naming
convention. See `data/README.md` for current file listing.

### Required Files

| Data | Description | Source |
|------|-------------|--------|
| ZTE touch data | Zone touches with features, geometry, outcomes (52 cols) | ZoneTouchEngine v4.0 on 250-vol chart |
| 1-tick bar data | Tick-level OHLCV for simulation resolution | Sierra Chart 1-tick export |
| 250-volume bar data | Volume bars for feature context | Sierra Chart 250-vol export |
| Ray context data | Broken zone rays with source metadata (7 cols, long format) | ZoneTouchEngine v4.0 |

## Configuration (read from project, never hardcode)

- **Instrument constants:** `_config/instruments.md` (tick_size, tick_value, session times)
- **Period boundaries:** `_config/period-config.md` (calibration/holdout date ranges)
- **Cost assumption:** `_config/instruments.md` (experiment_cost_ticks)

---

## ZTE Raw Schema (52 columns)

| # | Column | Type | Description |
|---|--------|------|-------------|
| 1 | DateTime | datetime | Touch timestamp |
| 2 | BarIndex | int | 250-vol chart bar index |
| 3 | TouchType | string | DEMAND_EDGE, SUPPLY_EDGE, or VP_RAY |
| 4 | ApproachDir | int | -1 = from above (demand), +1 = from below (supply) |
| 5 | TouchPrice | float | Price at zone edge |
| 6 | ZoneTop | float | Zone upper boundary |
| 7 | ZoneBot | float | Zone lower boundary |
| 8 | HasVPRay | int | 1 = VP imbalance ray present (dead — always 0) |
| 9 | VPRayPrice | float | VP ray price (dead — always 0) |
| 10 | Reaction | float | Max favorable excursion (ticks, on 250-vol bars) |
| 11 | Penetration | float | Max adverse excursion (ticks, on 250-vol bars) |
| 12 | ReactionPeakBar | int | Bar index of max reaction |
| 13 | ZoneBroken | int | 1 = zone broken during observation |
| 14 | BreakBarIndex | int | Bar index where zone broke (-1 if not) |
| 15 | BarsObserved | int | Bars between touch and resolution |
| 16 | TouchSequence | int | Nth touch of this zone |
| 17 | ZoneAgeBars | int | Bars since zone appeared |
| 18 | ApproachVelocity | float | 10-bar lookback price change (ticks) |
| 19 | TrendSlope | float | 50-bar lookback price change (ticks) |
| 20 | SourceLabel | string | TF label (15m-720m) |
| 21 | SourceChart | int | SC chart number (drop) |
| 22 | SourceStudyID | int | SC study ID (drop) |
| 23-29 | RxnBar_30..360 | int | First bar reaching reaction threshold (-1 = never) |
| 30-33 | PenBar_30..120 | int | First bar reaching penetration threshold (-1 = never) |
| 34 | ZoneWidthTicks | int | (ZoneTop - ZoneBot) / tick_size |
| 35 | CascadeState | string | PRIOR_HELD, PRIOR_BROKE, NO_PRIOR, UNKNOWN |
| 36 | CascadeActive | int | 1 = cascade within lookback |
| 37 | TFWeightScore | int | TF weight component |
| 38 | TFConfluence | int | # of higher TFs with aligned zones |
| 39 | SessionClass | int | 0=Open, 1=MidDay, 2=Afternoon, 3=OffHours |
| 40 | DayOfWeek | int | 0=Sun..6=Sat |
| 41 | ModeAssignment | string | M1F, M1H, M3, M4, M5, SKIP |
| 42 | QualityScore | int | A-Cal quality score |
| 43 | ContextScore | int | A-Cal context score |
| 44 | TotalScore | int | QualityScore + ContextScore |
| 45 | SourceSlot | int | Chart slot index (0-8) |
| 46 | ConfirmedBar | int | Bar where signal confirmed |
| 47 | HtfConfirmed | int | 1 = HTF confirmation |
| 48 | Active | int | 1 = unresolved at export |
| 49 | DemandRayPrice | float | Nearest broken demand ray price |
| 50 | SupplyRayPrice | float | Nearest broken supply ray price |
| 51 | DemandRayDistTicks | float | Distance to demand ray (ticks) |
| 52 | SupplyRayDistTicks | float | Distance to supply ray (ticks) |

## Ray Context Schema (7 columns, long format)

One row per (touch, nearby ray) pair. A touch with 6 nearby rays = 6 rows.

| # | Column | Type | Description |
|---|--------|------|-------------|
| 1 | TouchID | string | Composite key: BarIndex_TouchType_SourceLabel |
| 2 | RayPrice | float | Broken zone ray price level |
| 3 | RaySide | string | DEMAND or SUPPLY |
| 4 | RayDirection | string | ABOVE or BELOW relative to touch edge |
| 5 | RayDistTicks | float | Distance from touch edge to ray (always positive) |
| 6 | RayTF | string | TF of the zone that produced the ray |
| 7 | RayAgeBars | int | Bars since ray accumulated |

### Ray Join Key

```python
zte['TouchID'] = zte['BarIndex'].astype(str) + '_' + zte['TouchType'] + '_' + zte['SourceLabel']
```

---

## Execution Sequence

### Step 1: Load all source files

Load ZTE touch data, 1-tick bars, 250-vol bars, and ray context
for each period. Read file names from `data/README.md`.

Read tick_size from `_config/instruments.md`.
Read calibration/holdout dates from `_config/period-config.md`.

Print row counts and date ranges for each file.

### Step 2: Filter VP_RAY touches

Remove rows where TouchType = "VP_RAY" from ZTE data.
Print count removed per period.
Confirm only DEMAND_EDGE and SUPPLY_EDGE remain.

### Step 3: Drop SC internal columns

Drop SourceChart (col 21) and SourceStudyID (col 22) from ZTE data.

### Step 4: Filter ray context data

Remove rays that fail either filter:
- **Exclude** rays where RayTF < 60 minutes (15m, 30m rays removed)
- **Exclude** rays from SBB zones

For SBB identification in ray data: a ray comes from a broken zone.
Cross-reference with ZTE data — if the zone that produced the ray
was an SBB (ZoneBroken=1 AND BreakBarIndex - BarIndex <= 1 on the
original zone's touch record), exclude its ray.

Print: total rays before filter, after filter, removal counts by
reason (TF filter vs SBB filter).

### Step 5: Build tick-bar index mapping

For each touch, find the corresponding position in the 1-tick data
by matching on DateTime. This maps each touch to a starting index
in the tick data for simulation.

Store as `TickBarIndex` — the row index in the 1-tick file where
this touch occurs.

Print match rate. Flag any touch with no tick data match (gap > 1 second).

### Step 6: Build volume-bar index mapping

For each touch, confirm BarIndex maps to the correct row in the
250-vol bar data (bar timestamp <= touch DateTime, nearest match).

This should be a passthrough since ZTE already provides BarIndex
on the 250-vol chart. Verify, don't recompute.

Print match rate.

### Step 7: Join ray context to touches

Build TouchID key on ZTE data. Left join ray context (filtered)
onto touches. Result is one-to-many: each touch may have 0-N
ray rows.

For touches with no rays after filtering: note as ray_count=0.

Produce two outputs:
- **Touch-level file:** one row per touch, with ray summary columns
  (nearest_ray_dist, nearest_ray_side, ray_count, has_valid_ray)
- **Ray detail file:** the filtered long-format ray data (for
  detailed ray analysis in Prompt 0 Part A)

### Step 8: Tag periods

Using dates from `_config/period-config.md`, tag each touch as
`calibration` or `holdout` based on its DateTime.

Print touch counts per role.

### Step 9: Validation checks

All must pass. Fail loudly on any failure.

| Check | Criterion |
|-------|-----------|
| No nulls in key columns | TouchPrice, ZoneTop, ZoneBot, Reaction, Penetration non-null |
| Valid TouchType | All values in {DEMAND_EDGE, SUPPLY_EDGE} |
| Valid SourceLabel | All values in {15m, 30m, 60m, 90m, 120m, 240m, 360m, 480m, 720m} |
| Valid CascadeState | All values in {PRIOR_HELD, NO_PRIOR, PRIOR_BROKE, UNKNOWN} |
| Non-negative outcomes | Reaction >= 0 and Penetration >= 0 |
| Zone ordering | ZoneTop > ZoneBot for all rows |
| Date range | All rows within expected period boundaries |
| Minimum sample | Calibration has >= 1000 touches |
| Tick data coverage | >= 99% of touches have a tick bar match |
| Volume bar coverage | >= 99% of touches have a volume bar match |

### Step 10: Save output files

All outputs go to `data/` with scaffold naming convention.

| Output | Naming |
|--------|--------|
| Prepared touch data (one per role) | `zone-touch-NQ-touches-[role].csv` |
| Filtered ray detail (one per role) | `zone-touch-NQ-rays-[role].csv` |
| 1-tick bar data (passthrough) | Keep source name |
| 250-vol bar data (passthrough) | Keep source name |
| Preparation report | `zone-touch-NQ-data-prep-report.md` |

Update `data/README.md` with the new file listing.

---

## Reporting Output

### zone-touch-NQ-data-prep-report.md

1. **File inventory:** all source files with row counts and date ranges
2. **VP_RAY filter:** count removed per period
3. **Ray filter:** counts before/after, removals by reason (TF, SBB)
4. **Tick bar mapping:** match rate, gaps flagged
5. **Volume bar mapping:** match rate
6. **Ray join:** touches with 0/1/2/3+ valid rays
7. **Distributions per role (calibration/holdout):**
   - TouchType breakdown
   - SourceLabel (TF) breakdown
   - CascadeState breakdown
   - SBB rate by TF
   - ZoneWidthTicks stats (min, P25, median, P75, P90, max)
   - TouchSequence distribution (1, 2, 3, 4, 5+)
   - Ray availability rate
8. **Period tagging:** touch counts per role
9. **Validation results:** pass/fail for each check

---

## Anti-Patterns (never do these)

- Do NOT filter SBB touches from touch data. Label them, keep them. Downstream decides.
- Do NOT hardcode tick_size, cost_ticks, or session times. Read from `_config/instruments.md`.
- Do NOT hardcode date boundaries. Read from `_config/period-config.md`.
- Do NOT include VP_RAY touches in output.
- Do NOT include rays from zones with TF < 60 minutes in filtered ray output.
- Do NOT include rays from SBB zones in filtered ray output.
- Do NOT drop any ZTE columns except SourceChart and SourceStudyID.

---

## Self-Check (run before saving outputs)

- [ ] All source files loaded with correct row counts
- [ ] VP_RAY touches removed (count printed)
- [ ] SourceChart and SourceStudyID dropped
- [ ] Ray data filtered: TF < 60m removed, SBB-origin removed (counts printed)
- [ ] Tick bar index mapped for each touch (match rate printed)
- [ ] Volume bar index verified (match rate printed)
- [ ] Ray join complete: touch-level summary + ray detail file
- [ ] Touches tagged as calibration or holdout from period-config
- [ ] All validation checks passed
- [ ] Output files saved to data/ with scaffold naming
- [ ] data/README.md updated with new file listing
- [ ] data_prep_report.md documents all distributions and checks
