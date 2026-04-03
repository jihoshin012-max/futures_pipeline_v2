# Initial Balance Analysis -- Research Findings

Date: 2026-04-03
Data: 18 NQ contracts (H2, H3, H4, H5, H6, M2, M3, M4, M5, U2, U3, U4, U5, Z1, Z2, Z3, Z4, Z5)
Total: 1143 valid trading days (IB1 High > 0 AND IB2 High > 0)
IB1: 08:30-09:30 ET, IB2: 09:30-10:30 ET
Midline: 160-bar rolling mean on 5-min chart
Slope: (midline at 10:30 - midline at 10:00) / 6 bars
Slope threshold: +/-0.25 pts/bar (UP/FLAT/DOWN)
RTH: 09:30-16:00

**Disclaimer:** All percentages reported are observed frequencies from
historical data, not statistical probabilities. Past frequencies do not
guarantee future outcomes.

---

## 1. IB2 Overall Stats (1143 valid days)

- IB2 one-side holds: 871/1143 (76.2%)
- IB2 both broken: 236/1143 (20.6%)
- IB2 neither broken: 36/1143 (3.1%)

---

## 2. IB1 Break Stats (1143 days)

| IB1 Outcome | Days | % |
|-------------|------|---|
| Both broken | 713 | 62.4% |
| High only | 221 | 19.3% |
| Low only | 203 | 17.8% |
| Neither | 6 | 0.5% |

---

## 3. IB1 -> IB2 Cross Analysis

### When IB1 breaks BOTH sides (713 days)

| IB2 Outcome | Days | % |
|-------------|------|---|
| Both broken | 195 | 27.3% |
| One-side holds | 504 | 70.7% |
| Neither broken | 14 | 2.0% |

### When IB1 breaks ONE side only (424 days)

| IB2 Outcome | Days | % |
|-------------|------|---|
| Both broken | 41 | 9.7% |
| One-side holds | 361 | 85.1% |
| Neither broken | 22 | 5.2% |

---

## 4. Combined Signal: IB1 + Midline Slope -> IB2 First Break

Midline slope = (midline at 10:30 - midline at 10:00) / 6 bars.
Slope threshold: +/-0.25 pts/bar (UP/FLAT/DOWN).
All signals known by 10:30 (before IB2 breaks).

### When IB1 breaks ONE side only (highest accuracy)

| Signal | Days | IB2 First Break | Accuracy | One-Side Holds |
|--------|------|----------------|----------|---------------|
| IB1 LOW only + DOWN | 125 | LOW first | 95% | 93% |
| IB1 HIGH only + FLAT | 38 | HIGH first | 92% | 95% |
| IB1 HIGH only + UP | 154 | HIGH first | 92% | 90% |
| IB1 HIGH only + DOWN | 22 | HIGH first | 91% | 91% |
| IB1 LOW only + UP | 16 | LOW first | 88% | 81% |
| IB1 LOW only + FLAT | 47 | LOW first | 81% | 79% |

### When IB1 breaks BOTH sides

| Signal | Days | IB2 First Break | Accuracy | One-Side Holds |
|--------|------|----------------|----------|---------------|
| IB1 BOTH + UP | 240 | HIGH first | 60% | 72% |
| IB1 BOTH + DOWN | 241 | LOW first | 58% | 72% |
| IB1 BOTH + FLAT | 218 | LOW first | 51% | 72% |

---

## 5. Price vs Midline Analysis

### Standalone Signal

| Price Position at IB2 End | Days | IB2 Same Direction | One-Side Holds |
|--------------------------|------|-------------------|---------------|
| ABOVE midline | 559 | 73.5% HIGH first | 78.2% |
| BELOW midline | 548 | 70.8% LOW first | 79.2% |

### Triple Signal: IB1 + Slope + Price vs Midline -> IB2 First Break

Top triple signals (min 5 days, sorted by accuracy):

| Signal | Days | Direction | Accuracy | One-Side |
|--------|------|-----------|----------|----------|
| IB1 LOW only + DOWN + BELOW | 125 | LOW | 95% | 93% |
| IB1 HIGH only + FLAT + ABOVE | 31 | HIGH | 94% | 97% |
| IB1 HIGH only + UP + ABOVE | 153 | HIGH | 92% | 91% |
| IB1 HIGH only + DOWN + BELOW | 18 | HIGH | 89% | 89% |
| IB1 LOW only + FLAT + BELOW | 41 | LOW | 88% | 83% |
| IB1 BOTH + DOWN + ABOVE | 24 | HIGH | 88% | 62% |
| IB1 HIGH only + FLAT + BELOW | 7 | HIGH | 86% | 86% |
| IB1 LOW only + UP + ABOVE | 12 | LOW | 83% | 83% |
| IB1 BOTH + UP + BELOW | 17 | LOW | 82% | 59% |
| IB1 BOTH + FLAT + BELOW | 113 | LOW | 66% | 76% |
| IB1 BOTH + FLAT + ABOVE | 105 | HIGH | 66% | 68% |
| IB1 BOTH + UP + ABOVE | 223 | HIGH | 63% | 74% |
| IB1 BOTH + DOWN + BELOW | 217 | LOW | 63% | 73% |
| IB1 LOW only + FLAT + ABOVE | 6 | LOW | 33% | 50% |

---

## 6. Per-Contract Consistency (top 2 signals)

| Contract | Days | LO+DN Days | LO+DN Acc | HO+UP Days | HO+UP Acc |
|----------|------|-----------|-----------|-----------|-----------|
| H2 | 63 | 8 | 100.0% | 4 | 100.0% |
| H3 | 62 | 6 | 100.0% | 8 | 87.5% |
| H4 | 63 | 5 | 80.0% | 9 | 88.9% |
| H5 | 61 | 7 | 100.0% | 9 | 88.9% |
| H6 | 61 | 9 | 100.0% | 11 | 100.0% |
| M2 | 64 | 9 | 77.8% | 5 | 100.0% |
| M3 | 63 | 3 | 100.0% | 10 | 80.0% |
| M4 | 68 | 7 | 85.7% | 9 | 100.0% |
| M5 | 63 | 7 | 100.0% | 11 | 90.9% |
| U2 | 65 | 8 | 100.0% | 10 | 100.0% |
| U3 | 63 | 7 | 85.7% | 8 | 87.5% |
| U4 | 62 | 5 | 100.0% | 8 | 87.5% |
| U5 | 67 | 6 | 100.0% | 11 | 100.0% |
| Z1 | 65 | 4 | 100.0% | 7 | 85.7% |
| Z2 | 64 | 9 | 100.0% | 9 | 100.0% |
| Z3 | 64 | 8 | 100.0% | 9 | 100.0% |
| Z4 | 65 | 9 | 88.9% | 8 | 75.0% |
| Z5 | 60 | 8 | 100.0% | 8 | 75.0% |

---

## 7. IB2 Break Timing

### When IB2 both sides break (236 days), second break time distribution

| Hour | Days | % |
|------|------|---|
| 10:00-10:59 | 3 | 1.3% |
| 11:00-11:59 | 40 | 16.9% |
| 12:00-12:59 | 39 | 16.5% |
| 13:00-13:59 | 52 | 22.0% |
| 14:00-14:59 | 62 | 26.3% |
| 15:00-15:59 | 40 | 16.9% |

- Median IB2 both-broken time: 13:35
- Median IB1 both-broken time: 09:55

---

## 8. Holdout Validation

Calibration: 2021-10-01 to 2025-12-14 (1067 days)
Holdout: 2025-12-17 to 2026-03-13 (61 days)

### Double Signals (IB1 + Slope)

| Signal | Cal Days | Cal Acc | Cal 1-Side | HO Days | HO Acc | HO 1-Side |
|--------|----------|---------|-----------|---------|--------|-----------|
| IB1 LOW only + DOWN | 115 | 95% | 92% | 9 | 100% | 100% |
| IB1 HIGH only + UP | 142 | 91% | 89% | 11 | 100% | 100% |
| IB1 HIGH only + FLAT | 37 | 92% | 95% | 1 | 100% | 100% |
| IB1 LOW only + FLAT | 45 | 80% | 78% | 2 | 100% | 100% |
| IB1 BOTH + UP | 227 | 59% | 71% | 11 | 73% | 100% |
| IB1 BOTH + DOWN | 226 | 58% | 72% | 9 | 44% | 56% |
| IB1 BOTH + FLAT | 202 | 50% | 70% | 12 | 58% | 92% |

### Triple Signals (IB1 + Slope + Price vs Midline)

| Signal | Cal Days | Cal Acc | Cal 1-Side | HO Days | HO Acc | HO 1-Side |
|--------|----------|---------|-----------|---------|--------|-----------|
| IB1 HIGH only + UP + ABOVE | 141 | 91% | 90% | 11 | 100% | 100% |
| IB1 LOW only + DOWN + BELOW | 115 | 95% | 92% | 9 | 100% | 100% |
| IB1 BOTH + FLAT + BELOW | 100 | 64% | 74% | 9 | 78% | 89% |
| IB1 BOTH + FLAT + ABOVE | 102 | 65% | 67% | 3 | 100% | 100% |

---

## Data Files

IB data per contract: data/NQ-ib-5min-[contract].csv
IB study: lab/utility-NQ-study-ib-box.cpp
Period config: _config/period-config.md

---

## Open Questions

- Can these signals be integrated with the rangefade rotation strategy?
- Should IB break direction influence the rangefade's directional bias?
- Is there value in an IB3 period (10:30-11:30)?
- Can the midline slope threshold be optimized further?
