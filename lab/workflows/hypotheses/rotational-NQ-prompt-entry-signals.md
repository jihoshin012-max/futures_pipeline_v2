# Entry Regime & Direction Signals — Experiment Prompt

> **Archetype:** rotational
> **Instrument:** NQ
> **Status:** draft
> **Created:** 2026-03-29
> **Parent study:** `rotational-NQ-prompt-scale-detection.md`
> **Related:** `rotational-NQ-prompt-trade-management.md` (shares Steps 0-1)
> **Data:** `data/NQ-1tick-calibration.csv` (P1), `data/NQ-1tick-holdout.csv` (P2)

---

## Problem

The choppiness filter (chop < 0.10 at lb=3 on 250-tick bars) is a validated binary entry gate. It tells us *whether* the market is rotational but not *which direction* displacement is building or *how fast* the regime is changing. These dimensions may improve entry quality — selecting better entries and biasing entry direction.

## Prior art

- **Choppiness ratio:** `|close[i] - close[i-lb+1]| / summed_range` — validated P1→P2 (E[R] $2.21→$52.57 filtered P1, held at $55.28 P2, SC-aligned aggregation). Verdict: PASS.
- **Feature discovery (Steps 5-6):** Choppiness, abs_slope, and R2 were the only 3 of 11 features showing signal at SD=10. All three are price-path-based at lb=3. Volume-based features showed no signal.
- **Pairwise combinations (Step 7):** R2 adds nothing when choppiness is already in the filter. Slope adds marginally. Choppiness alone is the primary driver.

## Candidates

### 1. Signed choppiness (directional displacement)

```
signed_chop = (close[i] - close[i-lb+1]) / summed_range
```

- Range: [-1, 1]
- Positive = upward displacement, negative = downward, near zero = rotational
- Computation: identical to choppiness, remove `fabs`

**Mathematical constraint:** The chop filter requires `chop < 0.10` for entry, and `chop = |signed_chop|`. This constrains signed_chop to **[-0.10, +0.10]** at every entry point. The directional signal is compressed to near-zero values by construction.

**Hypothesis (weak claim):** Within the narrow [-0.10, +0.10] range, cycles where signed_chop aligns with trade direction (LONG + positive, SHORT + negative) may outperform those where it opposes. The effect size, if it exists, will be small. Step 2 will determine if there's any signal here — this candidate is the most likely to be killed at the kill gate.

### 2. dChop/dt (regime transition rate)

```
dchop = chop[i] - chop[i-1]
```

- Negative = market becoming more rotational
- Positive = market becoming more trending

**Hypothesis:** Entries where dchop is negative (market transitioning *into* rotational mode) may outperform entries where dchop is positive (rotational mode is fading).

### 3. d²Chop/dt² (transition acceleration)

```
d2chop = dchop[i] - dchop[i-1]
```

- Detects inflection points — sustained vs transient regime transitions

**Hypothesis:** Entries during accelerating transitions into rotational mode may be higher quality.

### 4. Signed slope (directional trend from regression)

```
signed_slope = slope[i]  (already computed in engine, currently used as abs_slope)
```

- Positive = price path trending up, negative = down, near zero = flat
- Independent from signed chop: regression-based vs displacement/range-based

**Hypothesis:** A second directional signal independent of signed chop. Entering LONG when signed_slope is positive may outperform entering against the regression direction.

---

## Application

Entry decisions only — no in-trade management in this prompt.

- **Direction preference:** Signed chop + signed slope at entry → prefer entries aligned with displacement/regression direction. Note: signed chop is constrained to [-0.10, 0.10] at entry by the chop filter — its directional contribution may be negligible. Signed slope is the stronger directional candidate.
- **Entry quality:** dChop/dt at entry → prefer entries during improving conditions (negative dchop)
- **Entry filter:** Block entries where directional signals strongly oppose trade direction

---

## Baseline

Same as parent study (SC-aligned aggregation):
- **Config:** SD=10 HS=60 depth_1 MCS=2 + choppiness < 0.10 at lb=3
- **P1 filtered:** 10,312 cycles, 82% WR, 18% SR, E[R]=$52.57
- **P2 filtered:** 10,892 cycles, 82% WR, 18% SR, E[R]=$55.28

## Locked files (DO NOT MODIFY)

- `lab/rotational-NQ-simulator.py`
- `lab/rotational-NQ-sweep-baseline.py`
- `lab/rotational-NQ-scale-detection-sweep.py` (use existing filter injection, don't edit)
- `lab/rotational-NQ-scale-detection-engine.py` (extend, don't modify existing functions)

## Study files

- Engine extension: add `compute_entry_signals()` to `rotational-NQ-scale-detection-engine.py`
- Analysis scripts: `lab/rotational-NQ-scale-detection-step*.py` (new step numbers)

---

## Test Data

5 representative weeks selected from P1 **chop-filtered** performance.

| Week | Dates | Category | Filtered Cycles | Filtered PnL | E[R] | Selection reason |
|---|---|---|---|---|---|---|
| W43 | 2025-10-20 to 2025-10-24 | WEAKEST | 557 | $17,423 | $31.28 | Lowest filtered PnL |
| W39 | 2025-09-22 to 2025-09-26 | LOW | 438 | $18,285 | $41.75 | Second lowest |
| W50 | 2025-12-08 to 2025-12-12 | MID | 803 | $41,286 | $51.41 | Middle of range |
| W46 | 2025-11-10 to 2025-11-14 | GOOD | 1,022 | $54,599 | $53.42 | Upper range |
| W47 | 2025-11-17 to 2025-11-21 | BEST | 2,227 | $120,697 | $54.20 | Highest PnL, most cycles |

**Note:** W47 has 4x more cycles than W43. All analysis must report per-week breakdowns as the primary view. Pooled statistics are secondary.

---

## Test Sequence

### Step 0: Instrument the simulator (shared with trade-management prompt)

The existing sweep already supports callback injection (`on_cycle_exit`). Add a second optional callback (`on_bar_in_trade`) following the same pattern — `None` by default, no behavior change.

The callback records at each 250-tick bar while in position: cycle_id, bar_offset, price, direction, current_pnl_ticks, mfe_ticks, mae_ticks, and regime feature values.

**Verify:** Cycle outputs identical with callback=None vs callback=recording_fn.

### Step 1: Compute and tag (shared with trade-management prompt)

Run the instrumented sweep on the 5 test weeks with the chop filter active. Compute all 6 regime features at entry bar: signed_chop, dchop, d2chop, signed_slope, dr2, dslope. Track A uses the first 4; dr2 and dslope are computed here for Track B's shared data.

**Output:**
- `lab/output/rotational-NQ-scale-detection/regime-direction-tagged-cycles.csv` (per-cycle, all 6 regime features at entry)
- `lab/output/rotational-NQ-scale-detection/regime-direction-intrade-bars.csv` (per-bar snapshots — Track A doesn't use these, but they're generated here for Track B)

### Step 2: Entry correlation analysis

For each feature, bucket cycles by feature value at entry and compute SR, WR, avg $/cyc per bucket. Per-week breakdown for each.

- Monotonic relationships
- Separation between weak and strong weeks
- **Directional features:** Does the sign relative to trade direction predict outcome?

Key question: **Do these features add information beyond what choppiness already captures?**

### Kill gate

**Dies if:** No feature shows SR spread > 3pt at entry. Record findings and stop.

### Step 3: Redundancy check

Check correlation between candidates. If signed_chop and signed_slope are highly correlated, keep only the stronger one. If dchop and d2chop are redundant, keep dchop (simpler).

### Step 4: Retroactive filter (5 test weeks)

Tag cycles with entry feature values and compute PnL for subsets passing various thresholds. No sim re-run — retroactive only. Per-week breakdown required.

**Output:** `lab/output/rotational-NQ-scale-detection/entry-signals-retroactive.csv`

### Step 5: Live sim (5 test weeks)

Wire winning feature(s) into the existing sweep as entry gate or direction bias — same injection point as the chop filter. Compare against chop-only baseline. Per-week breakdown.

### Step 6: Full P1 validation

Run on full P1. Per-week breakdown required — improvement must not be concentrated in high-cycle weeks.

### Step 7: Sanity check

Random filter at matching retention rate (10 seeds). Entry feature must outperform all random seeds.

### Step 8: Handoff to bench

Freeze configuration. Write frozen params. Create verify report. Route to bench.

Bench runs: stress test suite, P2 holdout (ONE SHOT), formal verdict.

---

## Success criteria

- At least one entry feature adds SR spread > 5pt within the chop < 0.10 window
- Improvement survives full P1 per-week
- P2 E[R] does not degrade below $55.28 — tested in bench

**Kill criteria:**
- After Step 2: no signal → stop
- After Step 4/5: improvement only in pooled stats, not per-week → stop
- After Step 6: P1 improvement < 5% E[R] over baseline → marginal, stop

## Failure modes

- **Signed chop is constrained to [-0.10, 0.10] at entry** by the chop filter. The directional signal is mathematically compressed — most likely candidate to show no signal
- dChop/dt may be noise — choppiness jumps 0.02→0.37 bar-to-bar
- d²Chop/dt² needs 3 completed bars of history — noisy at lb=3
- Signed slope may also be near-zero at entry when chop is low (low displacement generally means low slope), though they are mathematically independent — verify empirically
- Overfitting risk with 4 features on the same data

## Pipeline boundary

Steps 0-7 execute in lab. Step 8 hands off to bench. Do not run stress tests or P2 in lab.
