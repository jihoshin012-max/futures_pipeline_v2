# Initial Balance Analysis -- Research Findings

Date: 2026-04-03
Data: 18 NQ contracts (H2, H3, H4, H5, H6, M2, M3, M4, M5, U2, U3, U4, U5, Z1, Z2, Z3, Z4, Z5)
Total: 1143 valid trading days (IB1 High > 0 AND IB2 High > 0)
IB1: 08:30-09:30 ET, IB2: 09:30-10:30 ET
Signal: IB1 break outcome only (no slope, no price vs midline)
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

## 4. Primary Signal: IB1 Outcome -> IB2 First Break Direction

The signal is IB1 break outcome alone. No additional filters are
needed when IB1 breaks only one side.

| IB1 Outcome | Days | Predicted IB2 Direction | Accuracy | One-Side Holds |
|-------------|------|------------------------|----------|----------------|
| HIGH only | 214 | HIGH first | 91.6% | 91.1% |
| LOW only | 188 | LOW first | 91.0% | 88.3% |
| BOTH | 699 | near coin flip | 50.4% HIGH / 49.6% LOW | -- |

When IB1 breaks only one side, IB2 breaks in that same direction
first approximately 91% of the time. When IB1 breaks both sides,
IB2 direction is essentially random (50/50).

**Note on slope filters:** Midline slope was tested in two forms:
- 10:00-10:30 slope (midline change during IB2): showed apparent
  signal, but this window falls inside IB2 itself and is not a
  true leading indicator -- it is computed from the same period
  being predicted.
- 08:30-09:30 slope (IB1 period, true leading window): did not
  add predictive value. When IB1 breaks only one side, accuracy
  was 88-95% regardless of slope direction. The slope did not
  meaningfully separate outcomes.

---

## 5. Tested and Rejected Additions

The following filters were tested as potential improvements to the
IB1-only signal. None added meaningful value beyond IB1 outcome alone.

### Midline Slope (10:00-10:30)

- Computed as (midline at 10:30 - midline at 10:00) / 6 bars
- Threshold: +/-0.25 pts/bar (UP/FLAT/DOWN)
- Problem: this slope is computed during IB2 itself (09:30-10:30),
  so it is not available before the period begins. It appeared to
  improve accuracy (e.g., IB1 LOW only + DOWN slope = 95%) but
  this was an artifact of using concurrent data as a predictor.
- When the same slope was measured over the IB1 period (08:30-09:30),
  it did not separate outcomes -- accuracy ranged 88-95% across all
  slope buckets with no consistent pattern.

### Price vs Midline at IB2 End

- Whether close at 10:30 was above/below the 160-bar rolling mean
- Same problem as slope: computed at the end of IB2, not before it
- Standalone signal (73% directional accuracy) is weaker than IB1
  outcome alone (91%) and adds no value as a filter

### Triple Signal (IB1 + Slope + Price vs Midline)

- Combined all three inputs
- Best accuracy cases (95%+) were identical to IB1-only accuracy
  because the IB1 outcome dominated; slope and price position
  were either concurrent data or noise

---

## 6. Per-Contract Consistency (IB1 outcome only)

| Contract | Days | HO Days | HO Acc | LO Days | LO Acc |
|----------|------|---------|--------|---------|--------|
| Z1 | 65 | 11 | 90.9% | 7 | 100.0% |
| H2 | 63 | 6 | 100.0% | 10 | 100.0% |
| M2 | 64 | 6 | 100.0% | 14 | 78.6% |
| U2 | 65 | 14 | 100.0% | 9 | 88.9% |
| Z2 | 64 | 13 | 100.0% | 13 | 92.3% |
| H3 | 62 | 10 | 90.0% | 9 | 88.9% |
| M3 | 63 | 15 | 80.0% | 6 | 83.3% |
| U3 | 63 | 11 | 90.9% | 10 | 90.0% |
| Z3 | 64 | 13 | 100.0% | 10 | 90.0% |
| H4 | 63 | 13 | 76.9% | 9 | 88.9% |
| M4 | 68 | 14 | 100.0% | 14 | 85.7% |
| U4 | 62 | 14 | 85.7% | 7 | 100.0% |
| Z4 | 65 | 12 | 83.3% | 14 | 85.7% |
| H5 | 61 | 11 | 90.9% | 14 | 92.9% |
| M5 | 63 | 17 | 88.2% | 10 | 90.0% |
| U5 | 67 | 12 | 100.0% | 10 | 100.0% |
| Z5 | 60 | 9 | 77.8% | 11 | 90.9% |
| H6 | 61 | 13 | 100.0% | 11 | 100.0% |

HO = IB1 HIGH only, LO = IB1 LOW only.
Acc = % where IB2 broke in the predicted direction first.
Signal is consistent across all 18 contracts with no contract
showing accuracy below 77%.

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

### Simplified Signal (IB1 outcome only)

| Signal | Cal Days | Cal Acc | HO Days | HO Acc |
|--------|----------|---------|---------|--------|
| IB1 HIGH only -> HIGH | 199 | 91.0% | 13 | 100.0% |
| IB1 LOW only -> LOW | 176 | 90.3% | 11 | 100.0% |
| IB1 BOTH (best dir) | 655 | 50.5% | 32 | 56.2% |

Holdout confirms calibration: one-side IB1 outcomes predict IB2
direction at 100% in the 61-day holdout (24 qualifying days).
IB1 BOTH remains near coin flip in holdout (56%).

---

## 9. Gap Analysis (1,143 days)

### Gap 1: Extension Beyond Broken IB2 Level
On 833 one-side IB2 days:
- Median extension: 92 pts (65% of IB2 range)
- 27% of days extend > 100% of IB2 range
- LOW breaks extend slightly further than HIGH (88% vs 75% of IB2 range avg)
- IB1 outcome does not differentiate extension size

### Gap 2: Time to First IB2 Break (one-side days)
On 334 concordant days (IB1 one-side, IB2 same direction):
- Median first break: 10:40 (10 min after IB2 closes)
- 64% of breaks occur in the 10:00 hour
- HIGH and LOW timing nearly identical (median 10:40 vs 10:45)

### Gap 3: IB1 BOTH Days — Secondary Signal
On IB1 BOTH days (62% of all days):
- IB1 first-break direction is anti-predictive of IB2 (33.6% concordance)
- No tested filter exceeded 55% directional accuracy
- IB2/IB1 size ratio > median yields 70.6% one-side rate (vs 61.0% below)
- Midline position at 09:30: 57-58% contrarian signal — not actionable
- IB1 slope at 09:30: no meaningful differentiation
- Verdict: no reliable directional signal found for BOTH days

### Gap 4: Failure Analysis
31 of 393 one-side days (7.9%) were failures (IB2 broke opposite direction):
- Failures have: larger IB1 range (med 101.5 vs 71.0), smaller IB2 range
  (med 112.5 vs 145.5), lower IB2/IB1 ratio (med 1.08 vs 2.04)
- **IB2/IB1 ratio > 2.0 drops failure rate to 2.1%** — strongest pre-trade filter
- Failures have later break times (med 10:55 vs 10:40)
- Failures have steeper IB1 slopes (med 0.180 vs 0.048) — overlaps with
  the ratio finding (volatile IB1 = less reliable)
- No contract or year clustering

### Gap 5: IB2 Unbroken Level Duration
On one-side days:
- Median closest approach to unbroken level: 56 pts (41% of IB2 range)
- 31% of days price comes within 25% of IB2 range of the unbroken level
- When IB2 eventually breaks both sides (289 days): second break median 14:00
- Median gap between first and second break: 160 minutes
- 18% of second breaks occur overnight in globex

### Midline Analysis on Gaps
Tested midline position and slope at 09:30 (leading indicator) across all gaps:
- Failures vs successes: midline position near even (53/47 vs 61/39) — not helpful
- IB1 slope steeper on failures (0.180 vs 0.048) but overlaps with IB2/IB1 ratio
- IB1 BOTH days: 57-58% contrarian signal — not actionable
- Extension distance: no meaningful difference by midline position
- Verdict: midline does not add value to any gap finding

---

## Data Files

IB data per contract: data/NQ-ib-5min-[contract].csv
IB study: lab/utility-NQ-study-ib-box.cpp
Period config: _config/period-config.md
Gap analysis: lab/output/ib-gap-analysis.py, ib-gap-analysis-summary.txt

---

## Open Questions

- Entry/stop/target mechanics for the IB directional signal
- IB2/IB1 ratio > 2.0 as a quality filter — needs holdout validation
- Is there value in an IB3 period (10:30-11:30)?
- Can the 10:40 median break time inform entry timing?
