# Stress Test Report — rotational NQ (chop variant)

> **Holdout period:** 2025-12-17 to 2026-03-13
> **Config:** SD=10 HS=60 depth_1 MCS=2 + choppiness < 0.10 lb=3 250tick
> **Aggregation:** SC-aligned (continuous counting, no date-change reset)
> **Date:** 2026-03-29

---

## Baseline

| Metric | Value |
|---|---|
| Cycles | 10,892 |
| WR | 82% |
| SR | 18% |
| Total PnL | $602,127 |
| E[R] | $55.28 |
| Negative weeks | 0 |

---

## Test 1: Threshold Sensitivity — STABLE

| Threshold | Cycles | WR | Total PnL | E[R] |
|---|---|---|---|---|
| 0.05 | 7,003 | 82% | $370,572 | $52.92 |
| 0.07 | 8,776 | 82% | $473,201 | $53.92 |
| 0.08 | 9,529 | 82% | $528,827 | $55.50 |
| 0.09 | 10,182 | 82% | $564,527 | $55.44 |
| **0.10** | **10,892** | **82%** | **$602,127** | **$55.28** |
| 0.11 | 11,591 | 82% | $660,628 | $56.99 |
| 0.12 | 12,192 | 83% | $722,756 | $59.28 |
| 0.13 | 12,784 | 83% | $749,749 | $58.65 |
| 0.15 | 13,760 | 82% | $782,476 | $56.87 |
| 0.20 | 15,649 | 82% | $845,984 | $54.06 |

No cliff edge. Every threshold from 0.05 to 0.20 strongly positive. Peak at 0.12.

## Test 2: Lookback Sensitivity — lb=3 best

| Lookback | Cycles | WR | Total PnL | E[R] |
|---|---|---|---|---|
| 2 | 9,171 | 80% | $312,246 | $34.05 |
| **3** | **10,892** | **82%** | **$602,127** | **$55.28** |
| 4 | 11,964 | 81% | $553,274 | $46.24 |
| 5 | 12,465 | 80% | $475,456 | $38.14 |
| 8 | 12,732 | 78% | $229,011 | $17.99 |

Clear optimum at lb=3. Same as P1.

## Test 3: Historical Drawdown

| Metric | P2 | P1 |
|---|---|---|
| Max DD | $4,742 | $6,749 |
| Profit/DD ratio | 127.0 | 85.9 |
| Max consecutive wins | 35 | 37 |
| Max consecutive losses | 5 | 5 |

## Test 4: Serial Correlation — NONE

| Lag | r | Threshold | Status |
|---|---|---|---|
| 1 | -0.0048 | +/-0.0192 | ok |
| 2 | -0.0096 | +/-0.0192 | ok |
| 3 | +0.0187 | +/-0.0192 | ok |
| 4 | +0.0035 | +/-0.0192 | ok |
| 5 | +0.0096 | +/-0.0192 | ok |

## Test 5: Bootstrap Monte Carlo (10K paths)

| Metric | P5 | P50 | P95 | P99 |
|---|---|---|---|---|
| PnL | $548,969 | $602,545 | $654,104 | $676,782 |
| DD | $5,070 | $6,513 | $9,141 | $10,849 |

Worst 5% of paths still strongly profitable ($549K).

## Test 6: Reshuffling Monte Carlo

Historical DD $4,742 at 2nd percentile of reshuffled paths. Not outlier lucky.

## Test 7: WR Compression

| Reduction | WR | PF | Total PnL |
|---|---|---|---|
| 0% | 82% | 1.51 | $602,127 |
| 5% | 78% | 1.17 | $240,967 |
| 8% | 76% | 1.02 | $25,397 |
| 10% | 74% | 0.93 | -$119,388 |
| 15% | 70% | 0.76 | -$479,744 |

Breakeven at ~76% WR (8% compression). Same as P1.

## Test 8: Slippage

| Slippage | PF | E[R] | Total PnL |
|---|---|---|---|
| 0t | 1.51 | $55.28 | $602,127 |
| 1t | 1.37 | $41.56 | $452,677 |
| 2t | 1.24 | $27.84 | $303,227 |
| 3t | 1.12 | $14.12 | $153,777 |
| 4t | 1.00 | $0.40 | $4,327 |
| 5t | 0.89 | -$13.32 | -$145,123 |

Breakeven at 4t. Tighter than P1 (6t).

## Test 9: Kelly Sizing

| Metric | Value |
|---|---|
| Win rate | 82.27% |
| Avg win | $197.88 |
| Avg loss | $606.48 |
| W/L ratio | 0.33 |
| Full Kelly | 0.28 |
| Half Kelly | 0.14 |

Identical to P1.

---

## Overall Verdict: PASS

P2 stress profile matches or improves on P1 across every test except slippage tolerance (4t vs 6t breakeven). WR vulnerability threshold is identical (76%). Kelly sizing identical. No serial dependency.
