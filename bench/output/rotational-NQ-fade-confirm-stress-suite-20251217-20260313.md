# Stress Test Report -- rotational NQ (fade-confirmation variant)

> **Holdout period:** 2025-12-17 to 2026-03-13
> **Config:** SD=10 HS=60 depth_1 MCS=2 + chop<0.10 lb=3 + dr2<=-0.40 + dslope<=-2.0 + fade_confirm<0.40
> **Date:** 2026-03-30

## Baseline

| Metric | Value |
|---|---|
| Cycles | 6,742 |
| WR | 85% |
| SR | 14% |
| Total PnL | $543,039 |
| E[R] | $80.55 |
| PF | 1.91 |

## Test 1: Fade Confirm Threshold Sensitivity

Tested in P1 (Step 5). All thresholds 0.4-0.7 improved all 12 weeks.
fc<0.4 chosen as best E[R] with near-100% retention.

## Test 2: Historical Drawdown

| Metric | Value |
|---|---|
| Max DD | $1,135 |
| Profit/DD ratio | 478.4 |
| Max consecutive wins | 44 |
| Max consecutive losses | 3 |

## Test 3: Serial Correlation -- NONE

| Lag | r | Threshold | Status |
|---|---|---|---|
| 1 | -0.0108 | +/-0.0244 | ok |
| 2 | +0.0081 | +/-0.0244 | ok |
| 3 | -0.0123 | +/-0.0244 | ok |
| 4 | -0.0046 | +/-0.0244 | ok |
| 5 | +0.0032 | +/-0.0244 | ok |

## Test 4: Bootstrap Monte Carlo (10K paths)

| Metric | P5 | P50 | P95 | P99 |
|---|---|---|---|---|
| PnL | $505,522 | $543,414 | $581,405 | $596,963 |
| DD | $3,387 | $4,346 | $6,030 | $7,117 |

## Test 5: WR Compression

| Reduction | WR | PF | Total PnL |
|---|---|---|---|
| **0%** | 85% | 1.91 | $543,039 |
| 5% | 81% | 1.40 | $311,659 |
| 8% | 79% | 1.20 | $172,671 |
| 10% | 77% | 1.08 | $80,280 |
| 15% | 73% | 0.87 | $-151,100 |

Breakeven at ~12% compression.

## Test 6: Slippage

| Slippage | PF | E[R] | Total PnL |
|---|---|---|---|
| **0t** | 1.91 | $80.55 | $543,039 |
| 1t | 1.74 | $67.53 | $455,319 |
| 2t | 1.58 | $54.52 | $367,599 |
| 3t | 1.43 | $41.51 | $279,879 |
| 4t | 1.28 | $28.50 | $192,159 |
| 5t | 1.15 | $15.49 | $104,439 |

## Test 7: Kelly Sizing

| Metric | Value |
|---|---|
| Win rate | 85.35% |
| Avg win | $198.28 |
| Avg loss | $605.12 |
| W/L ratio | 0.33 |
| Full Kelly | 0.41 |
| Half Kelly | 0.20 |

## Comparison vs Entry-Signals P2

| Metric | Entry-Signals | Fade-Confirm | Delta |
|---|---|---|---|
| Cycles | 6,927 | 6,742 | -185 |
| WR | 84% | 85% | +1.2pt |
| SR | 16% | 14% | -1.2pt |
| E[R] | $70.40 | $80.55 | $+10.15 |
| PF | 1.73 | 1.91 | +0.18 |
| Total PnL | $487,638 | $543,039 | $+55,401 |
