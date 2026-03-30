# Zone Touch NQ — Prompt 0 Baseline Summary

Generated: 2026-03-28 13:05

Calibration: 2025-09-21 to 2025-12-14
Holdout: 2025-12-17 to 2026-03-13


## A1: Zone Width Distribution

| TF | Count | Median (t) | Median (pts) | P90 (t) | Max (t) |
|---|---|---|---|---|---|
| 15m | 2094 | 110 | 27.5 | 313 | 654 |
| 30m | 1489 | 176 | 44.0 | 436 | 871 |
| 60m | 947 | 263 | 65.8 | 656 | 899 |
| 90m | 749 | 388 | 97.0 | 733 | 1283 |
| 120m | 640 | 386 | 96.5 | 678 | 1137 |
| 240m | 433 | 502 | 125.5 | 1168 | 1283 |
| 360m | 368 | 470 | 117.5 | 1461 | 1605 |
| 480m | 378 | 655 | 163.8 | 1273 | 1461 |
| 720m | 282 | 608 | 151.9 | 1132 | 1350 |
| ALL | 7380 | 236 | 59.0 | 706 | 1605 |

## A2: Ray Availability

- Rate: 97.9% (7228/7380)
- Ray position median: -0.26 (0=touch edge, 1=opposite)

## A3: Nested Zone Frequency

- Overall rate (TF>=60m): 95.6%

## C1: MFE Distribution

| Group | N | Median MFE (t) | Median R:R | Win@1R | Win@1.5R | Win@2R | Win@3R | PF@1R | PF@1.5R | PF@2R |
|---|---|---|---|---|---|---|---|---|---|---|
| ALL | 417,828 | 194 | 0.85 | 45.0% | 31.2% | 22.3% | 12.9% | 0.39 | 0.29 | 0.23 |
| DEMAND | 221,604 | 224 | 0.87 | 45.5% | 31.0% | 21.8% | 12.2% | 0.39 | 0.28 | 0.21 |
| SUPPLY | 196,224 | 165 | 0.83 | 44.5% | 31.4% | 22.9% | 13.6% | 0.39 | 0.31 | 0.26 |
| RTH | 222,876 | 270 | 0.93 | 47.7% | 33.2% | 23.4% | 13.1% | 0.47 | 0.34 | 0.27 |
| ETH | 194,952 | 132 | 0.77 | 41.8% | 28.8% | 21.1% | 12.6% | 0.29 | 0.22 | 0.18 |
| CALIBRATION | 184,016 | 167 | 0.88 | 46.2% | 33.1% | 24.4% | 14.7% | 0.38 | 0.32 | 0.26 |
| HOLDOUT | 233,812 | 220 | 0.83 | 44.0% | 29.6% | 20.7% | 11.5% | 0.40 | 0.28 | 0.21 |

### Best Sweep Combos (by median R:R)

| Offset | Buffer | Cap | N | R:R | Win@1R | Break% |
|---|---|---|---|---|---|---|
| 0 | 0 | 240 | 7352 | 1.24 | 58.3% | 37.4% |
| 10 | 0 | 240 | 7072 | 1.23 | 57.8% | 38.8% |
| 20 | 0 | 240 | 6754 | 1.21 | 57.1% | 40.3% |
| 10 | 5 | 240 | 7072 | 1.21 | 57.3% | 38.1% |
| 0 | 5 | 240 | 7352 | 1.20 | 57.7% | 36.7% |
| 20 | 5 | 240 | 6754 | 1.19 | 56.4% | 39.6% |
| 40 | 0 | 240 | 6134 | 1.19 | 55.7% | 41.8% |
| 10 | 10 | 240 | 7072 | 1.18 | 56.4% | 37.3% |
| 0 | 10 | 240 | 7352 | 1.17 | 56.7% | 35.9% |
| 20 | 10 | 240 | 6754 | 1.16 | 55.6% | 38.7% |

## C2: Risk Profile

| Metric | Value |
|---|---|
| Break rate | 22.4% |
| MAE median | 88t |
| MAE P95 | 419t |
| Comeback rate (>=20pt MFE) | 53.2% |
| Zone break velocity | 1.2 t/s |

## C3: Time Profile

| Metric | Value |
|---|---|
| Median time-to-MFE | 27.0min |
| Median time-to-resolution | 59.9min |
| Resolved within 30min | 32.4% |
| Resolved within 1hr | 58.8% |
| Resolved within 4hr | 100.0% |

## C4: Trade Series Metrics

Best sweep (by PF@1R): best(o=0,b=0,c=240)

| Metric | Value |
|---|---|
| PF at 1R | 0.73 |
| Sharpe (trade-level) | -0.117 |
| Sortino | -0.155 |
| Calmar | -0.965 |
| Max drawdown | 389021t |
| Max consecutive wins | 37 |
| Max consecutive losses | 41 |
| Equity curve slope | -57.52 |

Profitable combos (PF>1.0 at 1R): 0

## C5: Structural Observations

See console output for zone width breakpoint, ray impact, concurrent exposure, and cost proxy analysis.

## C6: Sequence Comparison

| Metric | ZTE Seq | Resolved Seq |
|---|---|---|
| Touch count | 7352 | 7352 |
| Seq 1 count | 2365 | 2382 |
| Seq 2+ count | 4987 | 4970 |
| Seq 1 R:R | 1.43 | 1.19 |
| Seq 2+ R:R | 0.79 | 0.89 |
| Seq 1 PF@1R | 1.04 | 0.64 |
| Seq 2+ PF@1R | 0.40 | 0.44 |

## C7: Session Comparison (RTH vs ETH)

| Metric | RTH | ETH |
|---|---|---|
| Touch count | 222876 | 194952 |
| % of total | 53.3% | 46.7% |
| Median MFE (t) | 270 | 132 |
| Median risk (t) | 310 | 175 |
| Median R:R | 0.93 | 0.77 |
| PF@1R | 0.47 | 0.29 |
| PF@2R | 0.27 | 0.18 |
| Break rate | 17.8% | 27.7% |
| Median time-to-res | 60.0min | 59.6min |
| Comeback rate (>=20pt) | 62.2% | 42.9% |
| Sharpe | -0.301 | -0.423 |
| Max drawdown (t) | 30836166 | 30329015 |
