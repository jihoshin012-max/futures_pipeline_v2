# Range Fade Rotation — Research Findings

Date: 2026-04-01
Data: NQ-250tick-calibration.csv (127,568 bars, 73 trading days)
Holdout: NQ-250tick-holdout.csv (121,595 bars, 63 trading days)

## FINAL VALIDATED CONFIG

| Setting | Value |
|---------|-------|
| Lookback | 100 |
| Inner Mult | 0.75 |
| Outer Mult | 1.75 |
| Max Consecutive Stops | 2 |
| Max Daily Loss | $2,500 |
| Martingale | Off |
| Step-Up | Off |

| Metric | Calibration | Holdout |
|--------|-------------|---------|
| Net PnL (after $4/trade) | $33,043 | $33,545 |
| MaxDD | $15,518 | $14,269 |
| PnL/DD | 2.13 | 2.35 |
| EV/Trade | $12.31 | — |
| Win Day% | — | — |
| Trades/Day | ~45 | ~40 |
| Worst Day | -$2,687 | -$2,901 |
| Days Halted by MDL | 12 (20%) | 12 (19%) |

No new code features required — parameter change only on existing v3 study.

### Features tested and rejected

| Feature | Calibration | Holdout | Verdict |
|---------|-------------|---------|---------|
| Martingale (any config) | Mixed | Failed | Hurts PnL/DD, N=2 limits to 1 step-up |
| Step-up stop (all offsets) | Worse | — | Cuts winners short, increases costs |
| EMA filter (EMA50/thr=0.25) | PnL/DD 4.28 | PnL/DD 1.44 | Overfit — lost 52% of edge |
| Regime switching (rolling WR) | EV $6.55 | Underperformed static | 0.75/2.50 static beats switching |
| Dynamic multipliers | — | — | Not needed — single config works all regimes |
| Sub-band switching | — | — | Not needed — circuit breaker is redundant for alternating strategy |

### Open investigations for future exploration
- User has additional ideas to explore

---

## Research Detail

Initial lookback: 50 bars (fixed)

## 1. Band Touch & Regime Analysis

### StdDev Regime (50-bar stdDev quintiles)
- Higher vol = more inner band touches (Q1: 51.6/100 -> Q5: 65.4/100)
- Higher vol = fewer outer band touches (Q1: 13.4/100 -> Q5: 10.4/100)
- Bands scale with vol but not proportionally to tail behavior

### Time of Day
- Hour 18 (globex open): highest stdDev (30.77), highest outer touch (24.2/100)
- Hour 15 (close): lowest stdDev (11.63), lowest outer touch
- Inner touch rate stable across hours (48-63/100)

### Lookback Sensitivity
- Inner touch rate stable across all lookbacks (~57/100)
- Longer lookback = more stops relative to targets
- Target hit ratio: 20.9% at LB=20 -> 15.2% at LB=100
- Lookback has least impact of the three levers

## 2. Bar Speed Analysis (250-tick bars)

### Duration Distribution
- Median: 13.8s, Mean: 38.6s (heavy right skew)
- 75.7% of bars form in <30s
- Fast (<10s): 39.3%, Moderate (10-30s): 36.4%, Slow (30-120s): 15.7%, Very slow (>120s): 8.6%

### Speed vs Range
- Speed and range are largely independent (can have fast narrow bars or fast wide bars)
- Slow/very-slow bars skew toward wider ranges

### Speed vs Band Behavior
- Fast bars: 77.4% inner touch, 27.6% outer touch
- Slow bars: 69.3% inner touch, 16.1% outer touch
- Correlation between bar speed and stdDev: 0.032 (near zero)
- Speed and stdDev are independent signals

### Speed Transitions as Leading Indicator
- Slow-to-fast transition: 46.4% followed by stdDev expansion within 20 bars
- Band touch lag after speed shift: 0.4 bars (near immediate)
- Bar speed is a potential leading indicator for vol regime change

## 3. Multiplier Grid Search (baseline, 1 contract, no martingale)

Entry: bar close (matching v3 behavior)
Target: innerTop - innerBot offset from entry
Stop: innerBot - outerBot (long) or outerTop - innerTop (short) offset from entry
StdDev: ddof=0 (population, matching v3 C++ code)
Trade sequencing: same-bar re-entry after resolution (matching v3 reversals)
Reversal logic: opposite-side signal exits current position at close, enters opposite
RTH filter: entries only between 09:30-15:45 ET (matching v3 time filter)
Validation: 87.7% of v3 trades matched by time+direction+price on 10/10/2025

### Overall (RTH only, with reversals)
- 24 of 27 combinations are positive EV
- Top combinations by EV per trade:

| Inner | Outer | Win Rate | EV/Trade | PF   | R:R   | Trades/Day | Max Consec Loss |
|-------|-------|----------|----------|------|-------|-----------|----------------|
| 1.50  | 2.00  | 18.8%    | +$4.73   | 1.04 | 6:1   | 115       | 35             |
| 0.50  | 2.00  | 62.3%    | +$4.30   | 1.04 | 0.67:1| 147       | 8              |
| 1.50  | 3.00  | 40.4%    | +$3.56   | 1.02 | 2:1   | 61        | 17             |
| 0.75  | 2.50  | 57.7%    | +$3.47   | 1.02 | 0.86:1| 97        | 8              |
| 0.50  | 1.75  | 58.4%    | +$3.46   | 1.03 | 0.8:1 | 150       | 11             |

Current v3 settings (1.00/2.00): +$1.74 EV — positive but mid-pack.
Reversal exits: 18.4% of all trades across all combinations.

### By Volatility Regime
- Low vol: 25/27 positive EV, avg EV = +$3.42 (most favorable)
- Medium vol: 16/27 positive EV, avg EV = +$0.94
- High vol: 10/27 positive EV, avg EV = +$0.07 (weakest)

### Top 5 Regime Breakdown
| Combo     | Low Vol EV | Med Vol EV | High Vol EV |
|-----------|-----------|-----------|-------------|
| 1.50/2.00 | -$1.21    | +$17.90   | -$2.83      |
| 0.50/2.00 | +$6.98    | +$1.79    | +$1.17      |
| 1.50/3.00 | +$2.33    | +$13.05   | -$9.13      |
| 0.75/2.50 | +$6.33    | -$5.26    | +$11.42     |
| 0.50/1.75 | +$4.88    | +$1.39    | +$3.15      |

Key: 0.50/2.00 and 0.50/1.75 are positive across ALL three regimes.
Note: 1.50/2.00 is negative in low vol and high vol — only strong in medium vol.

## 4. Martingale Overlay (RTH-filtered, validated against v3)

Martingale logic validated against v3 10/10/2025 replay — qty, counters,
per-side tracking, max cap all match. Python/v3 match rate: 87.7%.

### Best configs by PnL/DD ratio

| Combo     | Config      | Total PnL | Max DD  | PnL/DD | Notes                    |
|-----------|-------------|-----------|---------|--------|--------------------------|
| 1.50/2.00 | no mart     | +$39.7K   | $15.4K  | 2.57   | Baseline                 |
| 1.50/2.00 | m1.5 max3   | +$128.4K  | $40.5K  | 3.17   | Best risk-adjusted       |
| 0.50/1.75 | m2.0 max4   | +$108.1K  | —       | 2.85   | All-regime, mart helps   |
| 0.50/2.00 | no mart     | +$46.2K   | —       | 2.05   | Mart degrades this combo |
| 1.00/2.00 | m2.0 max4   | +$89.3K   | $62.2K  | 1.43   | Current v3, helps but weak |
| 1.50/3.00 | m1.5 max2   | -$696     | —       | —      | Destroyed by mart        |

### Martingale interaction rules

| Combo     | Win Rate | Mart Effect | Why                                         |
|-----------|----------|-------------|---------------------------------------------|
| 1.50/2.00 | 18.8%    | Amplifies   | Tight stops, small losses, safe to scale    |
| 0.50/1.75 | 58.4%    | Helps       | Small losses despite higher WR              |
| 0.50/2.00 | 62.3%    | Hurts       | High WR = few consec losses, mart adds DD   |
| 1.50/3.00 | 40.4%    | Destroys    | Wide stops = large losses compound          |
| 1.00/2.00 | 37.8%    | Moderate    | Edge too thin, mart helps but high DD       |

### Pattern
- Martingale benefit depends on LOSS SIZE, not win rate
- Tight stops (small losses) + martingale = safe scaling
- Wide stops (large losses) + martingale = destructive compounding
- High win rate doesn't need martingale (few consecutive losses to recover from)

## 5. Design Implications for V4

### Regime Detection (computable in real time)
1. StdDev ratio: 50-bar stdDev / 200-bar stdDev
   - > 1.5 = expanding (high vol)
   - < 0.7 = contracting (low vol)
   - Between = normal
2. Bar speed: rolling 10-bar average duration (leading indicator)
   - Slow-to-fast transition precedes vol expansion 46.4% of the time

### Proposed V4 Adaptive Logic (final, RTH-filtered + martingale validated)

| Condition | Combo | Martingale | PnL/DD | Rationale |
|-----------|-------|-----------|--------|-----------|
| Default / Medium vol | 1.50/2.00 | m1.5 max3 | 3.17 | Best risk-adjusted, +$17.90 EV in med vol |
| Conservative / All-regime | 0.50/2.00 | OFF | 2.05 | Positive all regimes, high WR, low max consec loss |
| High vol fallback | 0.50/1.75 | m2.0 max4 | 2.85 | Positive in high vol (+$3.15), mart helps |

### Regime detection (real-time)
1. StdDev ratio: 50-bar stdDev / 200-bar stdDev
   - > 1.5 = expanding (high vol) → switch to high vol config
   - < 0.7 = contracting (low vol) → use conservative config
   - Between = normal → use default config
2. Bar speed: rolling 10-bar avg duration (leading indicator)
   - Slow-to-fast transition precedes vol expansion 46.4% of the time

### Sub-band role
- Sub-bands can serve as regime-adaptive stop/entry levels
- Default (1.50/2.00): trade on main bands with tight stops
- High vol: sub-outer band provides wider stop level
- The 0.50 inner mult configs effectively trade closer to midline —
  sub-inner bands could serve this role
- Sub-bands already drawn in v3, just need trading logic wired

### What NOT to change
- Lookback: minimal impact, keep at 50
- Entry logic: bar close entry is correct
- RTH filter: 09:30-15:45 only

### Open questions for V4
- Should inner mult adapt by regime or stay fixed?
- The 0.50 inner mult combos trade very frequently (150/day)
  — need to assess feasibility with real-world execution
- Should regime switching happen mid-day or only at day boundaries?
- How to handle transition: if in a position when regime changes,
  keep current trade or flatten?

### Corrections applied to analysis (vs initial run)
1. StdDev ddof=0 (population) — matches C++ sqrtf(sumSq/period)
2. Entry price = bar close — matches v3 market order fill
3. Same-bar re-entry — matches v3 reversal behavior
4. Reversal logic — opposite-side signal exits + enters on same bar
5. RTH time filter — entries only 09:30-15:45 ET

### Validation
- 10/10/2025 single-day comparison: 87.7% of v3 trades matched by time+dir+price
- Remaining 12.3% differ due to same-bar target/stop ambiguity (250-tick resolution)
- Martingale qty, counters, per-side tracking, max cap all validated against v3 replay
- No systematic logic discrepancies found

## 6. Analysis Files

All in lab/output/rotational-NQ-scale-detection/:
- stddev-regime-analysis.csv
- time-of-day-analysis.csv
- lookback-sensitivity.csv
- volatility-expansion-contraction.csv
- speed-bucket-stats.csv
- speed-range-crosstab-count.csv
- speed-range-crosstab-pct.csv
- speed-band-behavior.csv
- speed-intraday-profile.csv
- multiplier-grid-baseline.csv
- multiplier-grid-by-regime.csv
- martingale-overlay.csv
- martingale-equity-curves.csv
- summary.txt
- speed-analysis-summary.txt
- multiplier-grid-summary.txt
- martingale-summary.txt
