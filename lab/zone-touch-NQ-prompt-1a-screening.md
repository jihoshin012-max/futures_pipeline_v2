# Zone Touch NQ — Prompt 1a: Feature Screening

Version: v4.0 (futures_pipeline)
Archetype: zone-touch | Instrument: NQ

## Purpose

Screen candidate features against the baseline established in
Prompt 0. Identify which features meaningfully improve R:R, PF,
and win rate beyond the raw edge. No model building — just
individual feature signal strength.

## Prerequisites

Prompt 0 must be complete. Required outputs:
- `lab/output/zone-touch-NQ-baseline-raw.csv` — per-touch simulation results
- `lab/output/zone-touch-NQ-baseline-summary.md` — baseline PF, R:R, win rates
- Top sweep combinations identified from baseline
- Baseline PF at 1R per combo (the numbers to beat)

## Data

**Use CALIBRATION data only.** Holdout discipline begins here.
Read calibration dates from `_config/period-config.md`
(calibration: 2025-09-21 to 2025-12-14, 3,278 touches).

Filter the baseline raw CSV to `Period == 'calibration'` and to
the three representative sweep combos below.

## Representative Sweep Combos

Screen features against three combos representing different trade
styles. This prevents features that only work with one setup from
passing screening.

| Combo | Offset | Buffer | Cap | Style |
|-------|--------|--------|-----|-------|
| A | 40 | 0 | 240 | Aggressive: deep entry, tight stop, long hold |
| B | 20 | 10 | 240 | Balanced: moderate entry, buffered stop |
| C | 0 | 10 | 120 | Conservative: edge entry, buffered stop, short hold |

**Update these combos** from Prompt 0 final results if the re-run
shifts the rankings. The styles should represent aggressive,
balanced, and conservative trade setups from the top performers.

### Source Files

| File | Purpose |
|------|---------|
| `data/NQ-zte-calibration.csv` | Touch data with ZTE features |
| `data/NQ-250vol-calibration.csv` | 250-vol bar data (44 cols, SpeedRead) |
| `data/NQ-1tick-calibration.csv` | Tick data for simulation |
| `data/NQ-ray-context-calibration.csv` | Filtered ray data |
| `data/NQ-ray-reference-calibration.csv` | Ray reference with BrokenCount |
| `lab/output/zone-touch-NQ-baseline-raw.csv` | Prompt 0 results |

---

## Screening Method

For each candidate feature, **repeat the following for each of the
three representative combos (A, B, C):**

1. **Bin** the calibration touches into terciles (low/mid/high) or
   categories based on the feature's natural distribution
2. **Compute** PF at 1R, win rate at 1R, median R:R, and touch count
   per bin using that combo's tick-level simulation results
3. **Measure signal strength:** the spread between best bin and worst
   bin (R/P spread for continuous features, or category spread for
   categorical features)
4. **Statistical test:** Mann-Whitney U between best and worst bins
   on per-trade PnL. Record p-value.
5. **Replication check:** split calibration at midpoint (Replica-A
   and Replica-B per `_config/period-config.md`). Does the same bin
   rank as best/worst on both halves?

### Pass Criteria (multi-combo)

A feature passes screening if **at least 2 of 3 combos** meet ALL of:
- Spread between best and worst bin PF > [that combo's baseline PF × 0.2]
- MWU p-value < 0.05
- Best bin is the same on both Replica-A and Replica-B
- Best bin has >= 100 touches

A feature that passes on all 3 combos is **STRONG**.
A feature that passes on 2 of 3 is **PASS**.
A feature that passes on only 1 is **WEAK**.
A feature that passes on 0 is **FAIL**.

---

## Candidate Features

### From ZTE (already in touch data)

These were features in v3.2. Re-screen on tick-level simulation
results — rankings may change with structural stops.

| # | Feature | Column | Type | v3.2 Status |
|---|---------|--------|------|-------------|
| F01 | Timeframe | SourceLabel | categorical | Was #2 (weight 4.91) |
| F04 | Cascade State | CascadeState | categorical | Was #7 (weight 1.93) |
| F05 | Session | DateTime → RTH/ETH | categorical | Was #3 (weight 4.54) |
| F09 | ZW/ATR Ratio | ZoneWidthTicks × tick_size / ATR | continuous | Was #4 (weight 2.98) |
| F10 | Prior Penetration | Penetration (prior touch) | continuous | Was #1 (weight 10.0) |
| F13 | Close Position | (Close-Low)/(High-Low) or (High-Close)/(High-Low) | continuous | Was #6 (weight 2.82) |
| F16 | Touch Sequence (ZTE) | TouchSequence | ordinal | Was screened, mixed |
| F17 | Approach Velocity | ApproachVelocity | continuous | Was screened |
| F18 | Trend Slope | TrendSlope | continuous | Was screened |
| F21 | Zone Age | ZoneAgeBars | continuous | Was #5 (weight 2.95) |
| F22 | Zone Width | ZoneWidthTicks | continuous | Was screened |
| F23 | TF Confluence | TFConfluence | ordinal | Was screened |
| F24 | HTF Confirmed | HtfConfirmed | binary | Was screened |
| F25 | Cascade Active | CascadeActive | binary | Was screened |

### New: From 250-vol Bar Data (SpeedRead)

Not available in v3.2. These measure market speed/momentum
at the moment of the zone touch.

| # | Feature | Column | Type | Hypothesis |
|---|---------|--------|------|------------|
| F30 | Composite Speed | Composite Speed | continuous | Fast market at touch → weaker bounce (momentum carries through) |
| F31 | Price Velocity | Price Velocity | continuous | High velocity approach → zone more likely to break |
| F32 | Volume Rate | Volume Rate | continuous | High volume at touch → stronger conviction in direction |
| F33 | Roll50 | Roll50 | continuous | Rolling context for speed normalization |

**Entry-time safety:** These columns are on the touch bar itself
(BarIndex maps directly). They are computable at entry time.

### New: From 250-vol Bar Data (Other)

| # | Feature | Column | Type | Hypothesis |
|---|---------|--------|------|------------|
| F34 | Bid/Ask Ratio | Bid Volume / Ask Volume | continuous | Directional volume imbalance at touch bar |
| F35 | True Range | TR | continuous | Volatility at touch — wide range bars may signal breakout |

### New: From Baseline Results (Prompt 0 Derived)

| # | Feature | Column | Type | Hypothesis |
|---|---------|--------|------|------------|
| F40 | Resolved Sequence | resolved_seq (from B4) | ordinal | Resolved seq may predict better than ZTE seq |
| F41 | Zone Comeback History | comeback_rate for this zone | continuous | Zones with high comeback rates → weaker future bounces |

### New: From Filtered Ray Data

| # | Feature | Column | Type | Hypothesis |
|---|---------|--------|------|------------|
| F50 | Valid Ray Count | count of filtered rays per touch | ordinal | More structural levels nearby → better defined risk |
| F51 | Nearest Ray Distance | min RayDistTicks from filtered rays | continuous | Closer ray → tighter stop → better R:R |
| F52 | Ray Broken Count | DemandBrokenCount or SupplyBrokenCount | ordinal | More broken zones stacked → stronger structural level |
| F53 | Ray Side Match | ray same side as touch type | binary | Demand touch with demand ray below → structural support |

---

## Screening Output Per Feature

For each candidate feature, produce **per combo (A, B, C):**

| Output | Content |
|--------|---------|
| Bin definitions | Tercile boundaries (continuous) or category list |
| Per-bin table | Touch count, PF at 1R, win rate at 1R, median R:R, median MFE |
| Signal spread | Best bin PF - worst bin PF |
| MWU p-value | Between best and worst bin PnL distributions |
| Replica stability | Best bin same on Replica-A and Replica-B? |
| RTH vs ETH split | Does the feature work in both sessions or only one? |

**Then aggregate across combos:**

| Output | Content |
|--------|---------|
| Combos passed | How many of A/B/C meet all criteria |
| Consistency | Is the best bin the same across combos? |
| Verdict | STRONG / PASS / WEAK / FAIL |

### Verdict Criteria

| Verdict | Criteria |
|---------|---------|
| STRONG | All 3 combos pass (spread > threshold, p < 0.05, replica stable, n >= 100) |
| PASS | 2 of 3 combos pass |
| WEAK | 1 of 3 combos passes, OR 2 pass but best bin differs across combos |
| FAIL | 0 combos pass |

---

## Part A: Individual Feature Screening

Screen all candidate features (F01-F53) using the method above.
Process each feature independently — no interactions yet.

**Output per feature:** One section in the screening report with
the bin table, spread, p-value, replica check, session split,
and verdict.

**Order:** Screen ZTE features first (F01-F25), then SpeedRead
(F30-F33), then bar data (F34-F35), then derived (F40-F41),
then ray (F50-F53).

---

## Part B: Feature Ranking

After all features are screened individually:

1. **Rank by signal spread** (descending) — only PASS and WEAK features
2. **Compare to v3.2 ranking** — which features changed rank?
   Did any v3.2 top features drop? Did any new features enter?
3. **Flag new entrants** — SpeedRead features, ray features, or
   resolved sequence that weren't available in v3.2
4. **Session dependency** — which features only work in RTH?
   Which work in both? This informs whether ETH trading is viable.

### Ranking Table

| Rank | Feature | Combos Passed | Avg Spread | Best Bin Consistent? | RTH | ETH | v3.2 Rank | Verdict |
|------|---------|--------------|-----------|---------------------|-----|-----|-----------|---------|
| 1 | | /3 | | | | | | |
| 2 | | /3 | | | | | | |
| ... | | | | | | | | |

**Avg Spread** = average of best-worst bin PF spread across the combos that passed.
**Best Bin Consistent** = same best bin across all passing combos (Yes/No).

---

## Part C: Feature Interactions (Preliminary)

For the top 5 PASS features, check pairwise:
- Are they correlated? (Spearman rho between feature values)
- If two features are highly correlated (|rho| > 0.7), one may
  be redundant — note for Prompt 1b model building

Do NOT build a multi-feature model here. Just flag correlations.

---

## Output Files

All outputs go to `lab/output/`:

| Output | File |
|--------|------|
| Per-feature screening details | `zone-touch-NQ-screening-details.md` |
| Feature ranking table | `zone-touch-NQ-screening-ranking.md` |
| Correlation matrix (top features) | `zone-touch-NQ-screening-correlations.md` |
| Journal entry | Append to `zone-touch-NQ-journal.md` |
| Audit log | Append to `audit/audit_log.md` |

---

## Review Gate

Before proceeding to Prompt 1b (model building):

1. How many features passed? (need >= 3 for a viable model)
2. Did the v3.2 top features survive on tick-level data?
3. Did any SpeedRead features pass? (new signal source)
4. Did ray features pass? (structural data now included)
5. Does resolved_seq outperform zte_seq?
6. Are there session-dependent features that limit ETH trading?
7. Any high correlations among top features? (redundancy risk)

Document findings in the journal before moving on.

---

## Constraints

- **Calibration only.** Do not use holdout data. Holdout discipline
  is active from this prompt forward.
- **Individual features only.** No multi-feature models. No scoring.
  Each feature screened independently against baseline.
- **Locked sweep combo.** Use the best entry offset, stop buffer,
  and time cap from Prompt 0. Do not re-optimize.
- **Entry-time safety.** All features must be computable at entry
  time. No future data. Verify each new feature (F30-F53) is
  available at BarIndex without lookahead.
- **Constants from registry.** Read from `_config/instruments.md`.
- **Journal the outcome.** Append findings to journal.
