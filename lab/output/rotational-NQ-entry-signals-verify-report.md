# Entry Signals Verify Report

> **Archetype:** rotational | **Instrument:** NQ | **Date:** 2026-03-29
> **Prompt:** `rotational-NQ-prompt-entry-signals.md`
> **Parent:** chop filter (v1.0, validated P1+P2)

## Configuration

| Parameter | Value |
|-----------|-------|
| Strategy | SD=10 HS=60 depth_1 MCS=2 |
| Chop filter | chop < 0.10 at lb=3, 250-tick bars |
| dr2 filter | dr2 <= -0.40 (R2 rate of change) |
| dslope filter | dslope <= -2.0 (abs_slope rate of change) |
| Gate behavior | Skip entry when chop >= 0.10 OR dr2 > -0.40 OR dslope > -2.0 |

## Results Summary

| Metric | Chop Only (P1) | + Entry Signals (P1) | Delta |
|--------|----------------|----------------------|-------|
| Cycles | 10,312 | 6,624 (64% retention) | -3,688 |
| Win Rate | 82% | 83% | +1pt |
| Stop Rate | 18% | 17% | -1pt |
| E[R] | $53.43 | $63.22 | **+$9.80 (+18.3%)** |
| Total PnL | $550,920 | $418,785 | -$132,135 |

## Per-Week Breakdown (Full P1)

| Week | bl_ER | ef_ER | dER | Verdict |
|------|-------|-------|-----|---------|
| W39 | $42.60 | $53.81 | +$11.21 | improved |
| W40 | $63.32 | $37.49 | -$25.83 | **degraded** |
| W41 | $54.81 | $55.79 | +$0.98 | improved |
| W42 | $43.88 | $63.30 | +$19.42 | improved |
| W43 | $32.14 | $50.57 | +$18.43 | improved |
| W44 | $50.97 | $69.84 | +$18.87 | improved |
| W45 | $64.16 | $61.57 | -$2.60 | degraded |
| W46 | $54.29 | $60.67 | +$6.38 | improved |
| W47 | $55.05 | $76.56 | +$21.51 | improved |
| W48 | $66.10 | $75.28 | +$9.18 | improved |
| W49 | $57.16 | $54.22 | -$2.94 | degraded |
| W50 | $52.27 | $61.97 | +$9.71 | improved |

**9/12 weeks improved (75%). 3 degraded (W40 is the only material miss).**

## Feature Selection Trail

| Step | Features | Action |
|------|----------|--------|
| Step 2 (kill gate) | signed_chop, signed_slope | Killed: SR spread < 3pt |
| Step 2 (kill gate) | dchop, d2chop, dr2, dslope | Passed: SR spread 4.9-9.5pt |
| Step 3 (redundancy) | dchop (r=0.87 w/ d2chop) | Dropped: weaker of pair |
| Step 3 (redundancy) | d2chop (r=0.71 w/ dslope) | Dropped: weaker of pair |
| Step 3 (survivors) | **dr2** (r=0.52 w/ dslope) | Kept: 9.5pt SR spread, independent |
| Step 3 (survivors) | **dslope** (r=0.52 w/ dr2) | Kept: 5.9pt SR spread, independent |

## Sanity Check

Entry filter E[R]=$63.22 vs 10 random seeds at same retention (64%):
- Random mean: $53.53
- Random max: $54.91
- **Margin: +$8.31 over best random seed**

## Interpretation

The entry filter selects cycles where the market is **actively transitioning into rotational mode** at the moment of entry:
- **dr2 <= -0.40**: R-squared is falling (trend structure dissolving)
- **dslope <= -2.0**: Absolute regression slope is falling (directional momentum fading)

Both signals measure the same phenomenon (regime transition) from independent mathematical perspectives (R2 goodness-of-fit vs slope magnitude), with low pairwise correlation (r=0.52).

## Trade-offs

- **36% of cycles rejected**: Total PnL drops from $551K to $419K despite higher per-cycle quality
- **W40 degradation**: The worst weekly outcome; filter blocked good entries that week
- **Retention vs quality**: Tighter thresholds improve E[R] further but total PnL drops faster

## Bench Requirements

- P2 holdout: ONE SHOT. E[R] must not degrade below $55.28 (chop-only P2 baseline)
- Stress tests: slippage sweep, Monte Carlo, Kelly criterion
- Threshold sensitivity: dr2 threshold +-0.10, dslope threshold +-1.0

## Files

| File | Purpose |
|------|---------|
| `rotational-NQ-entry-signals-params-frozen.json` | Frozen configuration |
| `entry-signals-retroactive.csv` | Step 4 threshold sweep results |
| `entry-signals-step5-livesim.csv` | Step 5 live sim comparison |
| `entry-signals-step6-7-validation.csv` | Steps 6+7 P1 validation + sanity |
| `regime-direction-tagged-cycles.csv` | Tagged cycle data (5 test weeks) |
| `regime-direction-intrade-bars.csv` | In-trade bar snapshots (for Track B) |
