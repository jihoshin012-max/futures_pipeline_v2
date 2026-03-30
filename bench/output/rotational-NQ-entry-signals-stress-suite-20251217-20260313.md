# Stress Test Report -- rotational NQ (entry-signals variant)

> **Holdout period:** 2025-12-17 to 2026-03-13
> **Config:** SD=10 HS=60 depth_1 MCS=2 + chop<0.10 lb=3 + dr2<=-0.40 + dslope<=-2.0
> **Date:** 2026-03-29

## Baseline

| Metric | Value |
|---|---|
| Cycles | 6,927 |
| WR | 84% |
| SR | 16% |
| Total PnL | $487,638 |
| E[R] | $70.40 |

## Test 1: DR2 Threshold Sensitivity

| DR2 Threshold | Cycles | WR | SR | E[R] |
|---|---|---|---|---|
| **-0.40 (frozen)** | **6,927** | **84%** | **16%** | **$70.40** |

Single frozen threshold. Sensitivity tested in P1 (Step 4).

## Test 2: Historical Drawdown

| Metric | Value |
|---|---|
| Max DD | $525 |
| Profit/DD ratio | 928.7 |
| Max consecutive wins | 46 |
| Max consecutive losses | 4 |

## Test 3: Serial Correlation -- NONE

| Lag | r | Threshold | Status |
|---|---|---|---|
| 1 | -0.0140 | +/-0.0240 | ok |
| 2 | -0.0121 | +/-0.0240 | ok |
| 3 | -0.0014 | +/-0.0240 | ok |
| 4 | -0.0055 | +/-0.0240 | ok |
| 5 | +0.0092 | +/-0.0240 | ok |

## Test 4: Bootstrap Monte Carlo (10K paths)

| Metric | P5 | P50 | P95 | P99 |
|---|---|---|---|---|
| PnL | $447,182 | $488,257 | $528,268 | $543,842 |
| DD | $3,786 | $4,934 | $6,932 | $8,233 |

## Test 5: WR Compression

| Reduction | WR | PF | Total PnL |
|---|---|---|---|
| **0%** | 84% | 1.73 | $487,638 |
| 5% | 80% | 1.30 | $252,702 |
| 8% | 77% | 1.12 | $111,901 |
| 10% | 76% | 1.02 | $18,570 |
| 15% | 72% | 0.82 | $-216,366 |

Breakeven at ~11% compression.

## Test 6: Slippage

| Slippage | PF | E[R] | Total PnL |
|---|---|---|---|
| **0t** | 1.73 | $70.40 | $487,638 |
| 1t | 1.57 | $57.01 | $394,878 |
| 2t | 1.42 | $43.61 | $302,118 |
| 3t | 1.29 | $30.22 | $209,358 |
| 4t | 1.15 | $16.83 | $116,598 |
| 5t | 1.03 | $3.44 | $23,838 |

## Test 7: Kelly Sizing

| Metric | Value |
|---|---|
| Win rate | 84.12% |
| Avg win | $198.16 |
| Avg loss | $606.41 |
| W/L ratio | 0.33 |
| Full Kelly | 0.36 |
| Half Kelly | 0.18 |

## Comparison vs Chop-Only P2

| Metric | Chop-Only | Entry-Signals | Delta |
|---|---|---|---|
| Cycles | 10,892 | 6,927 | -3,965 |
| WR | 82% | 84% | +1.9pt |
| SR | 18% | 16% | -1.8pt |
| E[R] | $55.28 | $70.40 | $+15.12 |
| PF | 1.51 | 1.73 | +0.22 |
| Total PnL | $602,127 | $487,638 | $-114,489 |
