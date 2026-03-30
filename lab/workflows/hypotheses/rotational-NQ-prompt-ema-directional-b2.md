# Track B2: EMA Directional Gate — Experiment Prompt

> **Archetype:** rotational
> **Instrument:** NQ
> **Status:** FROZEN — combined config (entry gate C + d2_avg3 hold) ready for bench
> **Created:** 2026-03-30
> **Parent study:** `rotational-NQ-prompt-scale-detection.md`
> **Depends on:** Track A (PASSED) + Track B (PASSED). Baseline includes both entry filters.
> **Next:** Track D (`rotational-NQ-prompt-extended-hold.md`) — depends on this prompt's outcome.
> **Data:** `data/NQ-1tick-calibration.csv` (P1), `data/NQ-1tick-holdout.csv` (P2)

---

## Problem

The A+B filtered strategy takes both LONG and SHORT entries based purely on pullback direction. It has no awareness of the short-term trend context. Preliminary standalone analysis on P1 showed a large E[R] spread between trades aligned with the EMA trend vs against it.

**Note on preliminary numbers:** The standalone analysis was computed using an inline filter implementation that produced ~5,200 cycles — different from the official A+B frozen config (6,496 cycles, E[R]=$78.16). The specific dollar values below are preliminary and must be re-verified in Step 2 on the correct population using the official engine. The directional pattern (with-trend outperforms against-trend) is expected to hold but the magnitudes will differ.

Preliminary findings:
- Trades WITH the EMA trend: E[R]=$73.41, 85% WR (1,246 cycles)
- Trades AGAINST the EMA trend: E[R]=$32.89, 80% WR (3,951 cycles)
- SHORT entries when EMA says UP: E[R]=-$12.49 (1,339 cycles at threshold ±2.0)

A directional gate based on EMA state could block the worst-performing direction while preserving the neutral zone where either direction works.

## Prior art

- **Track A+B baseline:** chop < 0.10 + dR2 <= -0.40 + dSlope <= -2.0 + fade_confirm < 0.40. P1: 6,496 cycles, 85% WR, 15% SR, E[R]=$78.16.
- **Track B critical finding:** Hypotheses can invert — entering when the pullback has maximum momentum is better for mean reversion. **Test both directions.**
- **Standalone EMA analysis (preliminary — from inline filter, re-verify on official population):** EMA 9/21 spread on 250-tick bars. Derivatives showed negligible forward return prediction (<0.3 pts spread). BUT directional alignment with trade direction showed strong preliminary signal (~$40 E[R] spread between with-trend and against-trend). Magnitudes will differ on official population.
- **Track C/C2 (FAILED):** Mid-trade signals and session context did not help. This prompt is an entry gate — same category as A and B.

## Candidates

### 1. Three-state EMA spread gate

```
ema9 = EMA(close, 9) on 250-tick bars
ema21 = EMA(close, 21) on 250-tick bars
ema_spread = ema9 - ema21
```

Three states at entry:
- **ema_spread > threshold:** EMA says UP → allow LONGs, block SHORTs
- **ema_spread < -threshold:** EMA says DOWN → allow SHORTs, block LONGs
- **|ema_spread| <= threshold:** NEUTRAL → allow both directions

**Computed from completed bars prior to entry bar** (same causal rule as all other features).

**Threshold sweep:** 0.5, 1.0, 2.0, 3.0, 5.0 pts. Standalone analysis showed strongest signal at ±2.0.

**Hypothesis:** Blocking trades that oppose the EMA direction improves E[R] by removing the worst-performing direction. The neutral zone preserves entries when EMA is indeterminate. **Test both: block against-trend AND block with-trend (Track B inversion check).**

### 2. d_ema9 slope gate

```
d_ema9 = ema9[i-1] - ema9[i-2]    (previous completed bar's EMA slope)
```

- Positive = short EMA rising
- Negative = short EMA falling

**Preliminary standalone finding (from inline filter — re-verify in Step 2 on official population):**
- LONG when d_ema9 > 0: E[R]=$101.98 (725 cycles)
- LONG when d_ema9 <= 0: E[R]=$56.18 (2,478 cycles)
- SHORT when d_ema9 <= 0: E[R]=$31.94 (284 cycles)
- SHORT when d_ema9 > 0: E[R]=-$0.47 (1,710 cycles — near zero)

Similar pattern to ema_spread but using rate of change rather than level. Dollar values are preliminary — see Problem section note.

**Hypothesis:** Block SHORTs when d_ema9 > 0, block LONGs when d_ema9 < 0. Or the inverse. Test both.

### 3. d_spread (trend strengthening/weakening)

```
d_spread = ema_spread[i-1] - ema_spread[i-2]
```

- Positive = spread widening (trend strengthening)
- Negative = spread narrowing (trend weakening)

**Preliminary standalone finding (re-verify in Step 2):** Weaker differentiation than ema_spread or d_ema9. LONG with d_spread > 0: E[R]=$85.31 vs d_spread <= 0: $54.40. SHORT showed no differentiation. Dollar values are preliminary.

**Hypothesis:** May add marginal value on top of ema_spread. Test as secondary candidate.

### 4. d2_ema9 (EMA curvature / trend acceleration)

```
d2_ema9 = d_ema9[i-1] - d_ema9[i-2]    (previous completed bars)
```

- Positive = EMA9 slope is increasing (trend accelerating)
- Negative = EMA9 slope is decreasing (trend decelerating)

**Standalone price prediction:** No signal (-0.14 to -0.26 pts spread, sub-tick). BUT: not yet tested as a direction × trade outcome differentiator.

**Hypothesis:** When d_ema9 is positive (trend up), positive d2_ema9 (accelerating) may make LONGs even stronger than when d2_ema9 is negative (decelerating). The curvature adds a second dimension to the velocity signal — not just "which direction is the trend" but "is the trend gaining or losing steam." Test as a refinement on top of d_ema9. **Test both directions.**

---

## Application

Entry gate — evaluated after chop, Track A, and Track B filters pass. Adds directional awareness.

```
Pullback reaches 10pts → direction determined (LONG or SHORT)
  → chop < 0.10?            (regime: rotational)
  → dR2/dSlope pass?        (regime: improving)
  → fade_confirm < 0.40?    (move: has momentum)
  → EMA directional gate?   (direction: aligned with trend?)   ← THIS PROMPT
  → ENTER
```

Three outcomes:
- **EMA supports the direction:** enter
- **EMA opposes the direction:** block
- **EMA neutral:** enter (no directional information)

No in-trade management. No simulator fork needed. Uses existing filter injection.

---

## Baseline

Track A+B combined (from official frozen params):
- **Config:** SD=10 HS=60 depth_1 MCS=2 + chop < 0.10 + dR2 <= -0.40 + dSlope <= -2.0 + fade_confirm < 0.40
- **P1:** 6,496 cycles, 85% WR, 15% SR, E[R]=$78.16
- **P2:** E[R]=$80.55, PF=1.91
- **Frozen params:** `lab/output/rotational-NQ-fade-confirm-params-frozen.json`

## Locked files (DO NOT MODIFY)

- `lab/rotational-NQ-simulator.py`
- `lab/rotational-NQ-sweep-baseline.py`
- `lab/rotational-NQ-scale-detection-sweep.py` (use existing filter injection)
- `lab/rotational-NQ-scale-detection-engine.py` (extend, don't modify existing functions)

## Study files

- Engine extension: add `compute_ema_directional()` to engine
- Analysis scripts: `lab/rotational-NQ-scale-detection-step*.py` (new step numbers)

---

## Test Data

Select 5 representative weeks from P1 **Track A+B filtered** performance. This is done in Step 0.

**Note:** Per-week breakdown is the primary view.

---

## Test Sequence

### Step 0: Select test weeks

Run A+B config across all P1 weeks. Rank by filtered PnL. Select WEAKEST/LOW/MID/GOOD/BEST. Record table before proceeding.

### Step 1: Compute and tag

Run A+B config on 5 test weeks using the official frozen params (`lab/output/rotational-NQ-fade-confirm-params-frozen.json`) and the engine's filter implementation — do NOT reimplement the filter inline. At each entry bar, compute: ema_spread, d_ema9, d_spread, d2_ema9 (all from completed bars prior to entry).

Tag each cycle with: trade direction, ema_spread, d_ema9, d_spread, d2_ema9.

**Output:** `lab/output/rotational-NQ-scale-detection/b2-ema-directional-tagged-cycles.csv`

### Step 2: Direction × EMA correlation analysis

For each EMA feature, split by trade direction and compute SR, WR, E[R]. Per-week breakdown. **Test both: block against-trend AND block with-trend.**

Specific questions:
- Does blocking SHORTs when ema_spread > threshold improve E[R]?
- Does blocking LONGs when ema_spread < -threshold improve E[R]?
- Does the INVERSE work? (Track B inversion check)
- Does the neutral zone (|spread| <= threshold) perform equally for both directions?

### Kill gate

**Dies if:** No direction × EMA combination shows E[R] spread > $10 between aligned and opposed. Record findings and stop.

### Step 3: Threshold optimization + redundancy check

Sweep thresholds for ema_spread (0.5, 1.0, 2.0, 3.0, 5.0). Find the threshold that maximizes E[R] while retaining > 77% of cycles (to stay above H2 minimum 5,000 cycle gate).

Check redundancy between ema_spread, d_ema9, and d2_ema9 — if any pair correlated > 0.7, keep the stronger. d2_ema9 is a refinement of d_ema9 — test whether adding curvature on top of slope improves differentiation or just adds noise.

### Step 4: Retroactive filter (5 test weeks)

Apply winning EMA gate retroactively. Compute PnL with blocked cycles removed. Per-week breakdown — must improve ALL weeks.

**Output:** `lab/output/rotational-NQ-scale-detection/b2-ema-directional-retroactive.csv`

### Step 5: Live sim (5 test weeks)

Wire EMA gate into existing sweep as direction-aware entry filter. The filter receives the trade direction and EMA state, and returns True/False.

Compare against A+B baseline. Per-week breakdown.

### Step 6: Full P1 validation

Run on full P1. Per-week breakdown required.

### Step 7: Sanity check

Random directional blocking at matching retention rate (10 seeds). Randomly block the same percentage of LONGs and SHORTs as the real gate. Must outperform all seeds.

### Step 8: Handoff to bench

Freeze configuration. Write frozen params. Verify report. Route to bench.

Bench runs: stress tests, P2 holdout (ONE SHOT), verdict.

---

## Success criteria

- E[R] improvement > 10% over A+B baseline
- Improvement survives full P1 per-week (ALL weeks positive, majority improved)
- Retention > 77% (minimum to stay above H2 gate of 5,000 cycles from 6,496 baseline)
- P2 E[R] does not degrade below A+B baseline ($80.55) — tested in bench

**Kill criteria:**
- After Step 2: no direction × EMA spread > $10 → stop
- After Step 4/5: improvement only in pooled stats, not per-week → stop
- After Step 6: P1 improvement < 10% E[R] → marginal for directional gate complexity, stop

## Failure modes

- The standalone analysis showed signal on the FULL A+B population. The retroactive filter (Step 4) and live sim (Step 5) may differ because blocking entries changes the watch/seed sequence.
- ema_spread is computed on 250-tick bars — same SC within-bar timing concern as choppiness. EMA values on incomplete bars differ from completed bars. Use completed bars only.
- Blocking SHORT entries when EMA is up could remove ~1,600 cycles (~25% of 6,496). If these include some winners, the net benefit may be smaller than the preliminary analysis suggests.
- EMA periods (9, 21) are common defaults — not optimized for this strategy or timeframe. Could test other periods but adds overfitting risk.
- Track B inversion: the analysis showed with-trend > against-trend. But this is the INTUITIVE direction (unlike Track B where it inverted). The inversion check in Step 2 confirms this isn't accidental.
- Adding a 4th entry gate (chop → dR2/dSlope → fade_confirm → EMA direction) further restricts entries. Combined retention across all gates must stay above 5,000 cycles.

## Pipeline boundary

Steps 0-7 execute in lab. Step 8 hands off to bench. Do not run stress tests or P2 in lab.
