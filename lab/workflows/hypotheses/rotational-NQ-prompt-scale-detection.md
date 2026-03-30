# Rotation Scale Detection Study — Experiment Prompt

> **Archetype:** rotational
> **Instrument:** NQ
> **Status:** completed
> **Created:** 2026-03-28
> **Data:** `data/NQ-1tick-calibration.csv` (P1, 31.8M rows)

---

## Problem

The rotation strategy uses a fixed StepDist (SD). P1 calibration found SD=25 optimal (PropScore 0.0353), but P2 validation showed SD=30/50 outperformed while SD=25 degraded, suggesting the dominant rotation scale may have shifted wider (38% stop rate on W39, -$18K). A fixed SD cannot adapt to regime shifts.

## Goal

Determine whether real-time rotation scale detection can improve strategy performance by:
1. Gating entries when the market isn't rotating at the strategy's SD scale
2. Dynamically adjusting SD to match the current dominant rotation scale
3. Adding quality filters (retracement health, completion asymmetry) to avoid degraded regimes

## Baseline

- **File:** `lab/output/rotational-NQ-results-baseline.csv` (144 configs)
- **Cycles:** `lab/output/rotational-NQ-results-baseline-cycles.csv` (885K cycles)
- **Reference config:** SD=10 HS=60 depth_1 MCS=2 (config_id 8)
  - P1: 18,148 cycles, 76% WR, 24% SR, E[R]=+$3.13, PropScore=0.0074
  - P2: 19,947 cycles, E[R]=+$3.78 (positive both periods)
  - HS ratio is 1.5x (not 1.25x -- note HS=50/1.25x was negative for SD=10)
- **Prior baseline (deprecated):** SD=25 HS=125 depth_1 (config_id 79). Best P1 PropScore but collapsed in P2 ($33.19 -> $0.16). Steps 1-3 used this baseline; results are in journal but should not be used for future decisions.

## Locked Files (DO NOT MODIFY)

- `lab/rotational-NQ-simulator.py` — calibration-locked Python sim
- `lab/rotational-NQ-sweep-baseline.py` — baseline sweep runner

## Study Files

- `lab/rotational-NQ-scale-detection-engine.py` — signal precomputation (zigzag, aggregation, all signal generators)
- `lab/rotational-NQ-scale-detection-sweep.py` — forked sweep with filter injection

## Zigzag Ownership

The zigzag implementation in the signal engine is the authoritative version. It is a 3-state machine (INIT/UP/DOWN) with session boundary resets, validated against the fractal discovery analysis. If this study produces viable results, the C++ port will replicate this exact algorithm — NOT Sierra Chart's built-in Zig Zag (black box).

---

## Test Data

5 representative weeks selected from P1 baseline (SD=10 HS=60 depth_1, config_id 8).

| Week | Dates | Category | Baseline Cycles | Stop Rate | d1 SR | Net PnL | Selection reason |
|---|---|---|---|---|---|---|---|
| W42 | 2025-10-13 to 2025-10-17 | WORST | 1,922 | 26% | 50% | -$17,386 | Worst week by far, most cycles of bad weeks |
| W50 | 2025-12-08 to 2025-12-12 | BAD | 1,296 | 25% | 51% | -$9,640 | Second worst, late in P1 period |
| W45 | 2025-11-03 to 2025-11-07 | AVG | 1,582 | 25% | 48% | +$756 | Near breakeven, middling |
| W46 | 2025-11-10 to 2025-11-14 | GOOD | 1,729 | 23% | 47% | +$24,420 | Best week, high cycles |
| W48 | 2025-11-24 to 2025-11-28 | GOOD | 1,123 | 22% | 49% | +$20,322 | Strong good week, different period |

Source: `lab/output/rotational-NQ-results-baseline-cycles.csv`, grouped by ISO week.

Note: d1 SR (depth-1 stop rate) is remarkably stable at SD=10 (47-51% across all weeks). The PnL difference between good and bad weeks comes from cycle count and d0/d1 split, not from d1 stop rate variation. This is a fundamentally different profile from SD=25 where d1 SR swung from 21-65%.

---

## Test Sequence

**Updated 2026-03-29.** Original Steps 1-3 (MTZZ counting, ZZ median, asymmetry as standalone on/off filters on SD=25) all failed. Step 4 (derived feature correlation on SD=25) showed some signal from R2, choppiness, and vol imbalance. Baseline changed to SD=10 HS=60. Approach shifted from single-signal on/off gating to derived feature analysis for multiple strategy decisions.

Each step uses the same 5 test weeks (W42, W50, W45, W46, W48).

---

### PHASE 1: Feature Discovery (individual analysis)

Goal: compute derived features from 250-tick bars at each SD=10 cycle entry, and determine which features have a measurable relationship with cycle outcomes.

#### Step 5: Compute derived features on SD=10 baseline

**What:** For each cycle in the 5 test weeks (SD=10 HS=60 depth_1), compute at the entry bar:
- Rolling slope (multiple lookbacks)
- R2 of regression fit
- Choppiness ratio (net_move / summed_range)
- Signed volume delta (ask_vol - bid_vol cumulative)
- Realized volatility
- Bar duration

**Data:** 5 test weeks, ~7,600 total cycles.
**Output:** Tagged cycle CSV with feature values at entry.

#### Step 6: Individual feature-outcome correlation

**What:** For each feature, bucket cycles by feature value and measure:
- Stop rate (SR)
- PnL per cycle
- d1 stop rate
- Win rate

**Pass criteria:** At least one feature shows a meaningful monotonic or threshold relationship with outcomes (e.g., SR varies by 5+ percentage points across buckets with sufficient sample size).
**If fail:** Stop -- derived features don't have predictive value for this strategy at this scale.

#### Step 7: Pairwise feature combinations

**What:** For features that showed signal in Step 6, test pairs: does combining two features produce stronger separation than either alone?
**Pass criteria:** The combination improves on the best single feature.
**If fail:** Use the best single feature only.

---

### PHASE 2: Design the Intervention

Based on Phase 1 findings, design how the features inform the strategy. This is NOT limited to entry on/off gating. Possible interventions:

- **Entry gating:** trade / don't trade (on/off)
- **Add gating:** allow d0 entry but skip the d1 martingale add under certain conditions
- **Adaptive HS:** widen or tighten the hard stop based on feature values
- **Direction bias:** favor long or short based on directional features
- **Position sizing:** scale size by regime confidence

#### Step 8: Targeted intervention test

**What:** Implement the most promising intervention from Phase 1 findings. Run on the 5 test weeks.
**Pass criteria:** Improves PnL on bad weeks without materially hurting good weeks.
**If fail:** Try the next most promising intervention, or stop.

---

### PHASE 3: Validation

#### Step 9: Full P1 run

**What:** If Step 8 passes, run the intervention on ALL of P1 (~18K cycles).
**Pass criteria:** Consistent improvement across the full period, not just the test weeks.
**If fail:** Overfit to test weeks -- stop.

---

### PRIOR WORK (Steps 1-4, completed on SD=25 baseline -- deprecated)

Steps 1-3 tested single-signal on/off filters on SD=25 HS=125. All failed.
Step 4 tested derived feature correlation on SD=25. Showed some signal (R2, choppiness, vol imbalance).
Full results in journal. These results informed the approach shift but should not be used for SD=10 decisions -- the feature-outcome relationships may differ at this scale.

### FUTURE (separate studies)

- **Parent-child stacking (Approach E):** Trade dominant scale AND its child simultaneously.
- **Confidence-based position sizing:** Scale position size by regime confidence.
- **MTZZ elements as composite features:** If derived features show signal, the ZZ median (15pt threshold) and MTZZ completion data could be added as additional features in a composite model -- not as standalone filters.

---

## Known Issues (from initial testing)

1. **Completion count normalization:** Raw counts always favor smallest threshold. Fixed with normalization by baseline rate per threshold.
2. **Count-based vs time-based window:** Count-based (last N swings) makes all thresholds look the same. Time-based (last N agg bars) correctly measures completion density.
3. **Signal noise at short windows:** Window=250 agg bars (~30 min) produced 127 transitions in W39 — too noisy. Needs hysteresis or a different signal mechanism.
4. **ATR on 1-tick bars:** Mostly zero (True Range of a single tick ≈ 0). ATR filter requires computing ATR from aggregated bars, not from the pre-computed column.

## Signal Architecture

- Signals are precomputed on 250-tick aggregated bars (matches live SC chart timeframe)
- Mapped back to 1-tick resolution via forward-fill for the sweep simulator
- Entry decisions happen at tick precision; regime detection operates at 250-tick resolution
- Intended to match live SC behavior: study on 250-tick chart, strategy reads via inter-study reference (verify UpdateAlways setting during C++ port)

## Output

Results go to `lab/output/rotational-NQ-scale-detection/`.
Journal updates go to `lab/output/rotational-NQ-journal-scale-detection.md`.
