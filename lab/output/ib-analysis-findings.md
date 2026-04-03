# Initial Balance Analysis — Research Findings

Date: 2026-04-03
Data: 18 NQ contracts (Z1 through H6), 5-min bars
Total: 755 valid trading days (4 years, 2021-10 to 2026-03)
IB1: 08:30-09:30 ET, IB2: 09:30-10:30 ET
Midline: 160-bar rolling mean on 5-min chart
Slope: change in midline per bar over 30 min before IB2 end
Slope threshold: +/-0.25 pts/bar (UP/FLAT/DOWN)

---

## IB2 Overall Stats (755 days)

- IB2 one-side holds: 598/755 (79.2%)
- IB2 both broken: 157/755 (20.8%)
- Neither side broken: 0/755 (0.0%)

---

## IB1 Break Stats (755 days)

| IB1 Outcome | Days | % |
|-------------|------|---|
| Both broken | 466 | 61.7% |
| High only | 155 | 20.5% |
| Low only | 131 | 17.3% |
| Neither | 3 | 0.4% |

---

## Combined Signal: IB1 Outcome + Midline Slope -> IB2 First Break

All signals are known by 10:30 (before IB2 breaks).

### When IB1 only breaks ONE side (highest accuracy)

| Signal | Days | IB2 First Break | Accuracy | One-Side Holds |
|--------|------|----------------|----------|---------------|
| IB1 HIGH only + Slope DOWN | 13 | HIGH first | 92% | 92% |
| IB1 HIGH only + Slope FLAT | 33 | HIGH first | 91% | 94% |
| IB1 LOW only + Slope DOWN | 84 | LOW first | 89% | 94% |
| IB1 HIGH only + Slope UP | 109 | HIGH first | 86% | 92% |
| IB1 LOW only + Slope UP | 11 | LOW first | 82% | 82% |
| IB1 LOW only + Slope FLAT | 36 | LOW first | 81% | 86% |

Pattern: When IB1 only breaks one side, IB2 tends to break the
SAME side first (81-92% accuracy). The slope adds marginal
signal but the IB1 one-side outcome is the dominant predictor.

### When IB1 breaks BOTH sides (lower accuracy)

| Signal | Days | IB2 First Break | Accuracy | One-Side Holds |
|--------|------|----------------|----------|---------------|
| IB1 BOTH + UP | 147 | HIGH first | 61% | 70% |
| IB1 BOTH + DOWN | 133 | LOW first | 53% | 76% |
| IB1 BOTH + FLAT | 186 | LOW first | 52% | 69% |

Pattern: When IB1 breaks both sides, directional prediction
drops to near coin-flip (52-61%). One-side hold rate also drops
(69-76% vs 82-94% for one-side IB1).

---

## IB1 -> IB2 Cross Analysis

### IB1 one-side only -> IB2 behavior
- When IB1 only breaks one side: IB2 breaks both only 5% (calibration)
- When IB1 breaks both: IB2 breaks both 30%

### Directional reversal (when IB1 both break)
- IB1 HIGH first, then both break -> IB2 LOW first 65% (calibration)
- IB1 LOW first, then both break -> IB2 HIGH first 65% (calibration)

Note: reversal pattern observed on calibration (60 days). Not
confirmed on full 4-year dataset — needs separate verification.

---

## Midline Slope Analysis

### Slope at IB2 end — directional signal
- Midline UP: IB2 breaks HIGH first 75% (cal), confirmed on holdout
- Midline DOWN: IB2 breaks LOW first 83% (cal), confirmed on holdout
- Midline FLAT: roughly even

### Flat midline — one-side hold rate
- FLAT midline at IB2 end: IB2 one-side holds 95% (calibration)
- UP midline: one-side holds ~60%
- DOWN midline: one-side holds ~80%

### IB1 midline slope
- Always FLAT at IB1 end (09:30) — 160-bar mean hasn't established
  direction by RTH open. Slope is only meaningful by IB2 end (10:30).

---

## Price vs Midline Analysis (760 days)

### Standalone signal
| Price Position at IB2 End | Days | IB2 Breaks Same Direction | One-Side Holds |
|--------------------------|------|--------------------------|---------------|
| ABOVE midline | 392 | 73% HIGH first | 78% |
| BELOW midline | 368 | 70% LOW first | 79% |

### Triple Signal: IB1 + Slope + Price vs Midline

Top signals (all three confirming, min 5 days):

| Signal | Days | Direction | Accuracy | One-Side |
|--------|------|-----------|----------|----------|
| IB1 HIGH only + DOWN + ABOVE | 9 | HIGH | 100% | 100% |
| IB1 HIGH only + FLAT + ABOVE | 24 | HIGH | 92% | 96% |
| IB1 LOW only + FLAT + BELOW | 27 | LOW | 89% | 96% |
| IB1 LOW only + DOWN + BELOW | 82 | LOW | 88% | 93% |
| IB1 LOW only + UP + ABOVE | 8 | LOW | 88% | 75% |
| IB1 HIGH only + UP + ABOVE | 108 | HIGH | 86% | 91% |
| IB1 HIGH only + FLAT + BELOW | 7 | HIGH | 86% | 86% |
| IB1 HIGH only + DOWN + BELOW | 7 | HIGH | 86% | 86% |

Largest confirming buckets:
- IB1 HIGH only + UP + ABOVE: 108 days, 86% accuracy
- IB1 LOW only + DOWN + BELOW: 82 days, 88% accuracy

### IB1 BOTH improvement with price vs midline

When IB1 breaks both sides, adding price vs midline improves accuracy:

| Signal | Days | Direction | Accuracy | One-Side |
|--------|------|-----------|----------|----------|
| IB1 BOTH + UP + BELOW | 14 | LOW | 79% | 57% |
| IB1 BOTH + DOWN + ABOVE | 17 | HIGH | 76% | 59% |
| IB1 BOTH + FLAT + BELOW | 101 | LOW | 73% | 68% |
| IB1 BOTH + FLAT + ABOVE | 79 | HIGH | 71% | 70% |
| IB1 BOTH + UP + ABOVE | 140 | HIGH | 66% | 72% |
| IB1 BOTH + DOWN + BELOW | 116 | LOW | 57% | 78% |

Compared to IB1 BOTH without price filter (52-61%), adding
price vs midline improves directional accuracy to 57-79%.

Note: All percentages are observed frequencies from historical data,
not probabilities from a statistical model.

---

## Holdout Validation (61 days, H6 contract)

Top signals calibration vs holdout:

| Signal | Cal Acc | HO Acc | Cal 1-Side | HO 1-Side |
|--------|---------|--------|-----------|-----------|
| IB1 LOW only + DOWN | 100% | 89% | 100% | 100% |
| IB1 HIGH only + UP | 71% | 90% | 86% | 100% |
| IB1 BOTH + FLAT | 70% | 57% | 90% | 93% |
| IB1 BOTH + UP | 64% | 80% | 50% | 100% |

Top two signals held on holdout. IB1 BOTH signals degraded.

---

## Per-Contract Consistency (top 2 signals)

| Contract | Days | LO+DN Days | LO+DN Acc | HO+UP Days | HO+UP Acc |
|----------|------|-----------|-----------|-----------|-----------|
| H4 | 67 | 7 | 71% | 8 | 88% |
| H5 | 64 | 9 | 89% | 9 | 89% |
| H6 | 61 | 9 | 89% | 10 | 90% |
| M3 | 67 | 3 | 100% | 11 | 73% |
| M4 | 72 | 8 | 88% | 9 | 89% |
| M5 | 64 | 7 | 100% | 12 | 92% |
| U3 | 67 | 7 | 86% | 9 | 78% |
| U4 | 65 | 6 | 83% | 8 | 88% |
| U5 | 67 | 7 | 86% | 11 | 100% |
| Z3 | 68 | 8 | 100% | 11 | 100% |
| Z4 | 68 | 9 | 89% | 9 | 78% |
| Z5 | 60 | 7 | 100% | 7 | 71% |

No contract below 71% for either signal.

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

Note: All percentages are observed frequencies from historical data,
not probabilities from a statistical model.
