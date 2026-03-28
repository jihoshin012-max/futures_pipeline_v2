# Zone Touch NQ — Prompt 0: Data Preparation & Baseline

Version: v4.0 (futures_pipeline)
Archetype: zone-touch | Instrument: NQ

## Purpose

Establish the raw edge of trading zone bounces using tick-level
simulation with structurally-defined risk. No features, no scoring,
no filtering. Every touch is measured equally. This baseline anchors
all subsequent analysis.

## Changes from v3.2

| What changed | v3.2 | v4.0 |
|---|---|---|
| Simulation resolution | 250-volume bars | 1-tick data at zone |
| Stop definition | Fixed ticks (60-200t grid) | Structural: other edge of zone/ray + buffer |
| Target definition | Fixed ticks (60-240t grid) | MFE distribution — measure actual bounces |
| Ray data | Separate analysis | Included from start, filtered (>=60min TF, non-SBB) |
| Sequence definition | ZTE only | ZTE + resolved sequence (comeback-based) |
| Data corrections | Separate prepend file | Baked into this prompt |
| Cost model | Fixed 3t assumption | Measure from tick data + fixed from config |
| Data scope | All data (no fitting) | All data — calibration + holdout (no fitting) |
| Bar data columns | 29 columns | 44 columns (includes SpeedRead) |

---

## Data Files

Read cost constants from `_config/instruments.md` (tick_size=0.25,
experiment_cost_ticks=3). Read period dates from `_config/period-config.md`
(calibration: 2025-09-21 to 2025-12-14, holdout: 2025-12-17 to 2026-03-13).

**Use ALL data (calibration + holdout) for this prompt.** The baseline
measures raw mechanics with no parameters fit. Holdout discipline
begins at Prompt 1 (feature screening).

### Source Files (in `data/`)

| File | Rows | Description |
|------|------|-------------|
| `NQ-zte-calibration.csv` | 3,278 | ZTE touch data — calibration |
| `NQ-zte-holdout.csv` | 4,102 | ZTE touch data — holdout |
| `NQ-250vol-calibration.csv` | 147,474 | 250-vol bar data — calibration (includes warmup) |
| `NQ-250vol-holdout.csv` | 128,293 | 250-vol bar data — holdout |
| `NQ-1tick-calibration.csv` | ~31.9M | 1-tick data — calibration (8.3GB, chunk-load) |
| `NQ-1tick-holdout.csv` | ~29.0M | 1-tick data — holdout (6.9GB, chunk-load) |
| `NQ-ray-context-calibration.csv` | 600,297 | Ray context (long format) — calibration |
| `NQ-ray-context-holdout.csv` | 1,576,643 | Ray context (long format) — holdout |
| `NQ-ray-reference-calibration.csv` | 1,716 | Ray reference — calibration |
| `NQ-ray-reference-holdout.csv` | 1,669 | Ray reference — holdout |

**Combined totals:** 7,380 touches, ~60.9M ticks.

**Loading note:** 1-tick files are very large. Use chunked reading
or memory-mapped access. For each touch, load only the tick segment
starting at touch timestamp through touch + max time cap (4hr).
Do not load entire tick file into memory.

**BarIndex alignment:** ZTE BarIndex maps directly to row index in
the 250-vol bar data file. Verified: 100% of touches align within
1 second.

---

## Data Preparation

Before analysis, run `/zone-data-prep` skill or perform these steps:

1. **Concatenate** calibration + holdout ZTE files (7,380 total touches)
2. **Filter** VP_RAY touches from ZTE (if any remain)
3. **Drop** SourceChart and SourceStudyID columns from ZTE
4. **Filter ray context:** remove rays where RayTF < 60 minutes and
   rays from SBB zones (ZoneBroken=1 AND BreakBarIndex - BarIndex <= 1)
5. **Map tick data:** for each touch, find corresponding start index in
   1-tick data by DateTime matching (searchsorted on tick timestamps)
6. **Join filtered rays** to touches via TouchID key:
   `zte['TouchID'] = BarIndex.astype(str) + '_' + TouchType + '_' + SourceLabel`
7. **Validate:** no nulls in key columns, valid TouchType, zone ordering,
   all touches have tick data coverage

---

## Part A: Zone Geometry Analysis

Before any simulation. Characterize the zones being traded.

### A1: Zone Width Distribution by Timeframe

For each timeframe (15m, 30m, 60m, 90m, 120m, 240m, 360m, 480m, 720m):
- Count of touches
- Width distribution: min, P25, median, P75, P90, P95, max (ticks and points)

**Output:** Table + histogram. Determines what "wide" vs "narrow"
means for Part B stop definitions.

### A2: Ray Availability (Filtered)

After applying ray filters (>=60min TF, non-SBB):
- Count of valid rays per period
- Distribution of ray positions within zones (near edge, mid-zone, far edge)
- How often does a valid ray exist inside a zone at the time of touch?
- DemandBrokenCount / SupplyBrokenCount distribution (from ray_reference)

**Output:** Ray availability rate.

### A3: Nested Zone Frequency

How often does a smaller/lower-TF zone exist inside a larger zone
at the time of a touch on the larger zone?

- Frequency by outer zone TF
- Nested zone width relative to outer zone width

**Output:** Nested zone availability rate.

---

## Part B: Tick-Level Baseline Simulation

For EVERY touch in the combined dataset (7,380 touches), simulate
on 1-tick data.

### B1: Trade Setup

**Entry point:** The touch price as reported by ZTE. The simulation
starts at the next tick after the touch event.

**Structural stop:** The other edge of the structure being traded:
- Zone touch → stop at opposite zone edge
- If a valid ray (filtered) exists closer than the opposite edge → stop at ray
- Buffer added to stop (sweep variable)

**Time cap:** Maximum duration before forced exit (sweep variable).

### B2: Sweep Variables

| Variable | Values | Notes |
|----------|--------|-------|
| Entry offset | 0, 10, 20, 40 ticks inside zone edge | How deep into zone before entering |
| Stop buffer | 0, 5, 10, 20 ticks past structural level | How much past the other edge/ray |
| Time cap | 30min, 1hr, 2hr, 4hr | Elapsed time, not bar count |

**Do NOT sweep targets.** Targets are discovered from MFE distribution.

Total sweep combinations: 4 x 4 x 4 = 64 per touch.
Total simulations: 64 x 7,380 = 472,320.

**Performance:** For each touch, load a tick segment from touch
timestamp through timestamp + 4hr (the max time cap). Run all 64
sweep combos against that same segment. This avoids re-reading
tick data for each combo. Process touches sequentially; each
touch's tick segment is independent.

### B3: Measurements Per Trade (tick-level)

For every sweep combination x every touch, record:

**Session tag:**
- Tag each trade as RTH (09:30-15:50 ET) or ETH (all other times)
  based on the touch DateTime

**Entry mechanics:**
- Touch bar penetration: how far the touch bar's extreme pushed past
  the zone edge into the zone (ticks). For demand: (ZoneTop - Low) / tick_size.
  For supply: (High - ZoneBot) / tick_size. This is how deep price already
  went during the 250-vol touch bar.
- Time-to-entry: elapsed time from touch event to when the offset entry
  price is first reached in tick data (seconds). Zero if the touch bar
  already penetrated past the offset level. This tells you whether the
  offset entry is a market fill or a limit order wait.

**Core mechanics:**
- MFE (Maximum Favorable Excursion) — furthest price in your favor (ticks)
- MAE (Maximum Adverse Excursion) — furthest price against you (ticks)
- Time-to-MFE — elapsed time from entry to MFE (seconds)
- Time-to-resolution — elapsed time until stop hit or time cap (seconds)
- Exit reason: structural_stop, time_cap, or open_at_data_end
- PnL in ticks (stop: -risk; time_cap: mark-to-market)

**Risk/Reward:**
- Risk: distance from entry to structural stop (ticks)
- R:R at MFE: MFE / risk
- Win rate at 1R, 1.5R, 2R, 2.5R, 3R targets
- PF at 1R, 1.5R, 2R targets: (win_rate x target) / ((1 - win_rate) x risk)
- Break-even R: where expected value = 0

**Comeback analysis:**
- After reaching MFE >= 10 points: did price return to within 5 ticks of entry edge?
- After reaching MFE >= 20 points: did price return?
- After reaching MFE >= 30 points: did price return?
- Comeback rate at each MFE threshold

**Zone break behavior:**
- Break rate: % of touches where price hits the structural stop
- Zone break velocity: ticks per second in the 10 seconds after stop hit
- Bounce shape: time-to-MFE / time-to-resolution ratio
  (low = V-shape snap, high = slow grind)

**Concurrent exposure:**
- At each touch timestamp, how many other trades are currently open?
- Distribution of concurrent open positions

**Cost measurement:**
- At the tick of entry, measure tick-to-tick price change magnitude
- Distribution across all entries — proxy for real execution cost
- Compare against fixed experiment_cost_ticks from `_config/instruments.md`

### B4: Resolved Sequence (second pass)

**Run AFTER the initial simulation completes.** This requires
knowing the outcome of prior touches to determine if the current
touch is a continuation or a new sequence.

Using comeback data from B3, define when a touch is "still playing out":
- If a prior touch on the same zone bounced (MFE >= 10 points) then
  came back to the zone edge, and the next ZTE touch event occurred
  before the prior touch resolved (hit stop, hit time cap), then
  the next touch is the SAME sequence — not a new one.
- Tag each touch with both:
  - `zte_seq`: ZTE's reported TouchSequence
  - `resolved_seq`: recounted based on resolution status of prior touch

Process touches in chronological order per zone. Walk forward:
if the prior touch is still open (hasn't hit stop or time cap)
when the next touch fires, the next touch inherits the same
resolved_seq number.

---

## Part C: Summary Statistics

Aggregate Part B results into baseline reference tables.

### C1: MFE Distribution (core deliverable)

| Metric | All | Demand | Supply | RTH | ETH |
|--------|-----|--------|--------|-----|-----|
| Touch count | | | | | |
| Median MFE (ticks) | | | | | |
| P75 MFE | | | | | |
| P90 MFE | | | | | |
| Mean risk (entry to stop) | | | | | |
| Median R:R at MFE | | | | | |
| Win rate at 1R | | | | | |
| Win rate at 1.5R | | | | | |
| Win rate at 2R | | | | | |
| Win rate at 3R | | | | | |
| PF at 1R | | | | | |
| PF at 1.5R | | | | | |
| PF at 2R | | | | | |
| Break-even R | | | | | |

Grouped by: entry offset, stop buffer, time cap.

### C2: Risk Profile

| Metric | Value |
|--------|-------|
| Break rate (stop hit %) | |
| MAE median (ticks) | |
| MAE P95 (ticks) | |
| Comeback rate (after >=20pt MFE) | |
| Zone break velocity (median ticks/sec) | |

### C3: Time Profile

| Metric | Value |
|--------|-------|
| Median time-to-MFE | |
| Median time-to-resolution | |
| % resolved within 30min | |
| % resolved within 1hr | |
| % resolved within 4hr | |

### C4: Trade Series Metrics

For the best-performing sweep combination (highest PF at 1R):

| Metric | Value |
|--------|-------|
| Sharpe ratio (trade-level, not annualized) | |
| Sortino ratio | |
| Calmar ratio (return / max drawdown) | |
| Max drawdown (ticks) | |
| Max consecutive losses | |
| Max consecutive wins | |
| Equity curve slope (linear regression) | |

Compute also for: all sweep combos with PF > 1.0 at 1R.

### C5: Structural Observations

- Zone width breakpoint: at what width does R:R collapse?
- Ray impact: R:R when ray is used as stop vs zone edge as stop
- Nested zone impact: R:R when nested zone available vs not
- Concurrent exposure: typical overlap count
- Tick-based cost vs fixed 3t assumption: how different?

### C6: Sequence Comparison

| Metric | ZTE Seq | Resolved Seq |
|--------|---------|-------------|
| Touch count | | |
| Seq 1 count | | |
| Seq 2+ count | | |
| Seq 1 median R:R | | |
| Seq 2+ median R:R | | |
| Seq 1 PF at 1R | | |
| Seq 2+ PF at 1R | | |

### C7: Session Comparison (RTH vs ETH)

RTH = 09:30 to 15:50 ET. ETH = all other times.

| Metric | RTH | ETH |
|--------|-----|-----|
| Touch count | | |
| % of total touches | | |
| Median MFE (ticks) | | |
| Median risk (ticks) | | |
| Median R:R at MFE | | |
| PF at 1R | | |
| PF at 2R | | |
| Break rate | | |
| Median time-to-resolution | | |
| Comeback rate (after >=20pt MFE) | | |
| Sharpe (trade-level) | | |
| Max drawdown (ticks) | | |

---

## Output Files

All outputs go to `lab/output/`:

| Output | File |
|--------|------|
| Raw trade results (every touch x every sweep combo) | `zone-touch-NQ-baseline-raw.csv` |
| Summary statistics | `zone-touch-NQ-baseline-summary.md` |
| Zone geometry analysis | `zone-touch-NQ-zone-geometry.md` |
| Ray availability analysis | `zone-touch-NQ-ray-analysis.md` |
| Journal entry | Append to `zone-touch-NQ-journal.md` |
| Audit log | Append to `audit/audit_log.md` |

---

## Review Gate

Before proceeding to Prompt 1 (feature screening):

1. Is there a raw edge? (PF > 1.0 at 1R for any sweep combo)
2. What is the baseline PF at the best sweep combo? (number to beat)
3. What zone width is the breakpoint?
4. Do rays improve R:R?
5. What time cap is sufficient? (% resolved)
6. How different is resolved_seq from zte_seq?
7. Is the tick-based cost materially different from 3t?
8. What does the comeback rate imply for breakeven/trail design?
9. Sharpe/Sortino/Calmar — is the equity curve tradeable or too volatile?

Document findings in the journal before moving on.

---

## Constraints

- **No features.** Every touch is treated equally. No scoring, no filtering.
- **No parameter fitting.** The sweep explores structural variations, not
  optimized parameters. The baseline is descriptive, not prescriptive.
- **Constants from registry.** Read tick_size and experiment_cost_ticks
  from `_config/instruments.md`. Never hardcode.
- **All data.** Use both calibration and holdout — no fitting means no
  contamination. Holdout discipline begins at Prompt 1.
- **Journal the outcome.** Append findings to `lab/output/zone-touch-NQ-journal.md`.

---

## Appendix: Column Schemas

### ZTE Schema (52 columns)

| Column | Type | Description |
|--------|------|-------------|
| DateTime | datetime | Touch timestamp |
| BarIndex | int | 250-vol bar index (maps to row in bar data) |
| TouchType | string | DEMAND_EDGE or SUPPLY_EDGE (filter VP_RAY) |
| ApproachDir | int | -1 = from above, +1 = from below |
| TouchPrice | float | Price at zone edge |
| ZoneTop | float | Zone upper boundary |
| ZoneBot | float | Zone lower boundary |
| HasVPRay | int | Always 0 (dead) |
| VPRayPrice | float | Always 0 |
| Reaction | float | Max favorable excursion (ticks, 250-vol bars) |
| Penetration | float | Max adverse excursion (ticks, 250-vol bars) |
| ReactionPeakBar | int | Bar index of max reaction |
| ZoneBroken | int | 1 = zone broke during observation |
| BreakBarIndex | int | Bar index where zone broke (-1 if not) |
| BarsObserved | int | Bars between touch and resolution |
| TouchSequence | int | Nth touch of this zone |
| ZoneAgeBars | int | Bars since zone appeared |
| ApproachVelocity | float | 10-bar lookback price change (ticks) |
| TrendSlope | float | 50-bar lookback price change (ticks) |
| SourceLabel | string | TF label (15m-720m) |
| SourceChart | int | SC chart number (drop during prep) |
| SourceStudyID | int | SC study ID (drop during prep) |
| RxnBar_30..RxnBar_360 | int | First bar reaching reaction threshold (-1 = never) |
| PenBar_30..PenBar_120 | int | First bar reaching penetration threshold (-1 = never) |
| ZoneWidthTicks | int | (ZoneTop - ZoneBot) / tick_size |
| CascadeState | string | PRIOR_HELD, PRIOR_BROKE, NO_PRIOR, UNKNOWN |
| CascadeActive | int | 1 = cascade within lookback |
| TFWeightScore | int | TF weight component |
| TFConfluence | int | # of higher TFs with aligned zones |
| SessionClass | int | 0=Open, 1=MidDay, 2=Afternoon, 3=OffHours |
| DayOfWeek | int | 0=Sun..6=Sat |
| ModeAssignment | string | M1F, M1H, M3, M4, M5, SKIP |
| QualityScore | int | A-Cal quality score |
| ContextScore | int | A-Cal context score |
| TotalScore | int | QualityScore + ContextScore |
| SourceSlot | int | Chart slot index (0-8) |
| ConfirmedBar | int | Bar where signal confirmed |
| HtfConfirmed | int | 1 = HTF confirmation |
| Active | int | 1 = unresolved at export |
| DemandRayPrice | float | Nearest broken demand ray (0 = none) |
| SupplyRayPrice | float | Nearest broken supply ray (0 = none) |
| DemandRayDistTicks | float | Distance to demand ray (ticks) |
| SupplyRayDistTicks | float | Distance to supply ray (ticks) |

### 250-Volume Bar Schema (44 columns)

| Column | Type | Description |
|--------|------|-------------|
| Date, Time | datetime | Bar timestamp |
| Open, High, Low, Last | float | OHLC prices |
| Volume | int | Bar volume (always 250) |
| # of Trades | int | Trade count |
| OHLC Avg, HLC Avg, HL Avg | float | Price averages |
| Bid Volume, Ask Volume | int | Directional volume |
| CPL | float | Close price level |
| TR | float | True range |
| Open.1, High.1, Low.1, Close | int/float | Secondary OHLC |
| M1F Demand/Supply Entry | float | ZTE M1F signals |
| M3 Demand/Supply Entry | float | ZTE M3 signals |
| M4 Demand/Supply Entry | float | ZTE M4 signals |
| Skip Demand/Supply Touch | float | ZTE skip signals |
| M5 Demand/Supply Entry | float | ZTE M5 signals |
| Trend Bar Color | float | Trend direction |
| Trend Slope (Ticks) | float | Trend slope value |
| Demand/Supply Edge Touch | float | Edge touch signals |
| M1H Demand/Supply Entry | float | ZTE M1H signals |
| VP Ray Touch | float | VP ray signal (dead) |
| Reaction Ticks | float | Reaction ticks |
| Penetration Ticks | float | Penetration ticks |
| **Composite Speed** | float | SpeedRead composite score |
| **Price Velocity** | float | Price movement rate |
| **Volume Rate** | float | Volume flow rate |
| **Slow Threshold** | float | SpeedRead slow threshold |
| **Fast Threshold** | float | SpeedRead fast threshold |
| **Roll50** | float | Rolling 50-period metric |

### Ray Context Schema (7 columns, long format)

| Column | Type | Description |
|--------|------|-------------|
| TouchID | string | Composite key: BarIndex_TouchType_SourceLabel |
| RayPrice | float | Broken zone ray price level |
| RaySide | string | DEMAND or SUPPLY |
| RayDirection | string | ABOVE or BELOW relative to touch edge |
| RayDistTicks | float | Distance from touch edge to ray (always positive) |
| RayTF | string | TF of the zone that produced the ray |
| RayAgeBars | int | Bars since ray accumulated |

### Ray Reference Schema (10 columns)

| Column | Type | Description |
|--------|------|-------------|
| BaseBarIndex | int | 250-vol chart bar index |
| DateTime | datetime | Timestamp |
| ChartSlot | int | Source chart slot |
| SourceLabel | string | TF label |
| ChartNumber | int | SC chart number |
| HtfBarIndex | int | Bar index on source HTF chart |
| DemandRayPrice | float | Demand ray price level |
| SupplyRayPrice | float | Supply ray price level |
| DemandBrokenCount | int | Broken demand zones at this ray |
| SupplyBrokenCount | int | Broken supply zones at this ray |
