# Zone Touch NQ — Prompt 0 Baseline Summary

Generated: 2026-03-28 07:43

Calibration: 2025-09-21 to 2025-12-14


## A1: Zone Width Distribution

| TF | Count | Median (t) | Median (pts) | P90 (t) | Max (t) |
|---|---|---|---|---|---|
| 15m | 972 | 100 | 24.9 | 264 | 579 |
| 30m | 726 | 144 | 36.1 | 390 | 871 |
| 60m | 406 | 216 | 53.9 | 567 | 899 |
| 90m | 331 | 367 | 91.8 | 606 | 1105 |
| 120m | 298 | 355 | 88.8 | 606 | 1137 |
| 240m | 181 | 499 | 124.8 | 1146 | 1236 |
| 360m | 150 | 516 | 129.0 | 1482 | 1605 |
| 480m | 125 | 582 | 145.5 | 1089 | 1174 |
| 720m | 89 | 649 | 162.2 | 894 | 1116 |
| ALL | 3278 | 193 | 48.2 | 606 | 1605 |

## A2: Ray Availability

- Rate: 95.5% (3129/3278)
- Ray position median: -0.10 (0=touch edge, 1=opposite)

## A3: Nested Zone Frequency

- Overall rate (TF>=60m): 91.8%

## C1: MFE Distribution

| Group | N | Median MFE (t) | Median R:R | Win@1R | Win@2R | Win@3R |
|---|---|---|---|---|---|---|
| ALL | 206,880 | 190 | 1.12 | 53.5% | 30.7% | 19.5% |
| DEMAND | 108,976 | 221 | 1.13 | 54.1% | 30.0% | 17.9% |
| SUPPLY | 97,904 | 162 | 1.10 | 52.9% | 31.4% | 21.3% |

### Best Sweep Combos (by median R:R)

| Offset | Buffer | Cap | N | R:R | Win@1R | Break% |
|---|---|---|---|---|---|---|
| 40 | 0 | 240 | 3124 | 2.02 | 70.8% | 39.9% |
| 40 | 5 | 240 | 3124 | 1.99 | 70.1% | 38.9% |
| 40 | 10 | 240 | 3124 | 1.93 | 69.7% | 38.5% |
| 40 | 0 | 120 | 3124 | 1.79 | 66.1% | 28.7% |
| 40 | 20 | 240 | 3124 | 1.77 | 68.3% | 37.3% |
| 40 | 5 | 120 | 3124 | 1.74 | 65.4% | 27.9% |
| 20 | 0 | 240 | 3253 | 1.68 | 66.2% | 41.3% |
| 40 | 10 | 120 | 3124 | 1.68 | 64.8% | 27.0% |
| 20 | 5 | 240 | 3253 | 1.62 | 65.1% | 40.2% |
| 40 | 20 | 120 | 3124 | 1.55 | 63.0% | 25.5% |

## C2: Risk Profile

| Metric | Value |
|---|---|
| Break rate | 22.5% |
| MAE median | 66t |
| MAE P95 | 412t |
| Comeback rate (≥20pt MFE) | 52.8% |
| Zone break velocity | 1.1 t/s |

## C3: Time Profile

| Metric | Value |
|---|---|
| Median time-to-MFE | 27.8min |
| Median time-to-resolution | 60.0min |
| Resolved within 30min | 30.3% |
| Resolved within 1hr | 58.3% |
| Resolved within 4hr | 100.0% |

## C5: Sequence Comparison

| Metric | ZTE Seq | Resolved Seq |
|---|---|---|
| Touch count | 3278 | 3278 |
| Seq 1 count | 1164 | 1098 |
| Seq 2+ count | 2114 | 2180 |
| Seq 1 R:R | 1.41 | 1.41 |
| Seq 2+ R:R | 0.80 | 0.81 |
