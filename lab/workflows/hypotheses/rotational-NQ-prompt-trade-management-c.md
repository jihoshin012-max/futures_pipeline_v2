# Track C: In-Trade Management Signals — Experiment Prompt

> **Archetype:** rotational
> **Instrument:** NQ
> **Status:** draft
> **Created:** 2026-03-29
> **Parent study:** `rotational-NQ-prompt-scale-detection.md`
> **Depends on:** Track A (`rotational-NQ-prompt-entry-signals.md`) and Track B (`rotational-NQ-prompt-fade-confirmation.md`) — run both first. Update this prompt's baseline and test weeks with the winning entry config from A + B before executing.
> **Data:** `data/NQ-1tick-calibration.csv` (P1), `data/NQ-1tick-holdout.csv` (P2)

---

## Problem

The chop-filtered strategy (82% WR, 18% SR) loses money on hard stops: each stop costs ~$600 (60 ticks × $5 × 2 contracts). With an 18% stop rate across ~10K cycles, stops are the dominant loss source — verify the actual gross loss breakdown in Step 1 before proceeding. If mid-trade signals can identify losing trades before the hard stop hits, early exit at a smaller loss preserves capital.

This prompt focuses on **reducing losses on losing trades** — not extending winners or improving entries. Entry decisions are handled by `rotational-NQ-prompt-entry-signals.md`.

## Prior art

- **Choppiness filter:** Validated entry gate. P1 E[R]=$52.57, P2 E[R]=$55.28. WR=82%, SR=18%.
- **Feature discovery (Steps 5-6):** Choppiness, abs_slope, R2 showed signal at entry. dR2 and dSlope (rate of change during trade) are untested.
- **SC alignment:** Within-bar chop timing differs between Python and SC. Management features that rely on precise bar-level timing may have similar alignment challenges.

## Candidates

### Regime features (evaluated mid-trade, not at entry)

### 1. Signed choppiness mid-trade

```
signed_chop = (close[i] - close[i-lb+1]) / summed_range
```

- If LONG and signed_chop turns strongly negative → displacement building against position
- If SHORT and signed_chop turns strongly positive → same

**Hypothesis:** Signed chop turning against position mid-trade predicts stops.

### 2. dR2/dt (trend formation rate)

```
dr2 = r2[i] - r2[i-1]
```

- Rising R2 mid-trade = trend forming = dangerous for rotation strategy

**Hypothesis:** Rising dR2 mid-trade predicts stops. Could trigger early exit or skip add.

### 3. dSlope/dt (slope acceleration)

```
dslope = abs_slope[i] - abs_slope[i-1]
```

- Rising slope = trend strengthening

**Hypothesis:** Rising dSlope mid-trade confirms trend formation alongside dR2.

### 4. Signed slope mid-trade

```
signed_slope = slope[i]
```

- Direction of regression line relative to position direction

**Hypothesis:** Signed slope strongly against position mid-trade predicts stops.

---

## Trade-Behavior Signals (computed from the trade itself)

### 5. Normalized hold time

```
hold_ratio = bars_held / median_bars_to_reversal
```

- Median computed from trailing N **reversal** cycles only (stops don't represent "normal" rotation time — including them inflates the median)
- Test sensitivity of N (e.g., last 20, 50, 100 reversal cycles)
- **Fallback:** If fewer than N reversal cycles available (session start), use P1 all-time median as default until enough trailing data accumulates
- hold_ratio > 1.5 = trade taking unusually long

**Hypothesis:** Trades exceeding 1.5-2x median hold time are more likely to stop out.

### 6. MFE efficiency and retracement

```
mfe_rate = mfe_ticks / bars_held
mfe_retracement = (mfe_ticks - current_favor_ticks) / mfe_ticks  (only when mfe_ticks > 0)
```

- **mfe_rate:** Low = stalling. High = healthy. At bars_held=0, undefined — compute from bar 1 onward.
- **mfe_retracement:** High = gave back favorable move. When mfe_ticks = 0 (never moved in favor), set to 0 (no retracement of nothing). Can exceed 1.0 when current_favor_ticks is negative (price past entry against you) — this is valid and means "gave back all MFE plus now losing."

**Hypothesis:** Low mfe_rate or high mfe_retracement (>0.5 after >5pts MFE) predicts stops.

### 7. MAE increment and proximity

```
mae_increment = mae_ticks[bar] - mae_ticks[bar-1]    (always >= 0, since MAE is a running max)
mae_proximity = mae_ticks / hard_stop_ticks
```

- **mae_increment:** How many new adverse ticks this bar set beyond the previous worst. 0 = no new worst this bar (price moved favorably or held). Positive = new worst set. This is NOT a velocity in the usual sense — MAE only increases, so the "velocity" is really "did we hit a new low, and by how much?"
- **mae_proximity:** Continuous measure of how close to stop (0.0 = entry, 1.0 = stopped).

**Hypothesis:** High mae_increment early in trade (new worst being set rapidly) predicts stops. mae_proximity > 0.60 combined with regime deterioration is a strong cut signal.

### 8. Relative range expansion

```
range_ratio = avg_range_recent / avg_range_at_entry
d_range_ratio = range_ratio[i] - range_ratio[i-1]
```

- avg_range_recent = mean range of the last lb bars (smoothed — a single anomalous bar won't spike the ratio)
- avg_range_at_entry = mean range of the lb bars at entry time
- range_ratio > 2.0 = volatility doubled since entry
- d_range_ratio positive = ranges expanding

**Hypothesis:** Expanding ranges mean the 10pt step distance is small relative to current volatility → rotation more likely to overshoot.

---

## Management Actions

### Feature-triggered actions (require signal evaluation)

| Action | Mechanic | Trigger candidates |
|---|---|---|
| **Early exit** | Flatten at current price before hard stop. Accept smaller loss. | signed_chop/slope against position, dR2 rising, hold_ratio > 1.5x, mae_proximity > 0.60 |
| **Skip add** | When 10pts against, do NOT add martingale contract. Stay at 1. | dR2 positive, mae_increment high, range_ratio > 1.5 |
| **Tighten stop** | Reduce hard stop from 60 ticks to smaller value (e.g., 40). | dslope (test both abs and signed versions), range_ratio expanding |

Note on dSlope: `abs_slope[i] - abs_slope[i-1]` loses directionality — steepening in your favor is different from steepening against you. However, for a rotation strategy, any steepening (either direction) indicates trending, which is bad for the rotation mechanic. Test both `d(abs_slope)` and `d(signed_slope)` to see which is more predictive.

### Mechanical rule (no signal needed)

| Action | Mechanic | Trigger |
|---|---|---|
| **Break-even stop** | Once MFE exceeds N ticks, move stop from -60 to 0 (entry price). | mfe_ticks > threshold (e.g., 20 ticks / 5pts) |

Break-even stop is a fixed rule, not a feature-triggered action. It doesn't depend on any of the 8 candidate signals — just on whether the trade has moved enough in favor. Test it independently as a standalone strategy modification in Step 6, separate from the feature-triggered actions.

### Not tested

**"Let winners run" is NOT tested.** Extending beyond 10pt reversal exit changes the strategy's fundamental mechanic and requires separate investigation.

---

## Baseline

Use the winning config from Track A + Track B (whatever survived). Update these numbers with the final entry-filtered P1 results before proceeding. The entry filters change the cycle population — different cycles, different WR/SR, different E[R]. Re-select test weeks from the new filtered distribution (rank weeks by filtered PnL, pick WEAKEST/LOW/MID/GOOD/BEST).

**Track A winner (known):** chop < 0.10 + dR2 <= -0.40 + dSlope <= -2.0. P1: 6,624 cycles, 83% WR, 17% SR, E[R]=$63.22.

**Track B winner:** TBD — update baseline after Track B completes. If Track B fails, use Track A numbers above.

## Locked files (DO NOT MODIFY)

- `lab/rotational-NQ-simulator.py`
- `lab/rotational-NQ-sweep-baseline.py`
- `lab/rotational-NQ-scale-detection-sweep.py` (fork, don't edit original)
- `lab/rotational-NQ-scale-detection-engine.py` (extend, don't modify existing functions)

## Study files

- Engine extension: add `compute_intrade_signals()` to engine
- **Forked simulator:** `lab/rotational-NQ-scale-detection-sweep-managed.py` — fork of sweep with management hooks. Must reproduce identical results when all management disabled.
- Analysis scripts: `lab/rotational-NQ-scale-detection-step*.py`

---

## Test Data

Same 5 weeks as entry-signals prompt (chop-filtered P1 distribution):

| Week | Dates | Category | Filtered Cycles | Filtered PnL | E[R] |
|---|---|---|---|---|---|
| W43 | 2025-10-20 to 2025-10-24 | WEAKEST | 557 | $17,423 | $31.28 |
| W39 | 2025-09-22 to 2025-09-26 | LOW | 438 | $18,285 | $41.75 |
| W50 | 2025-12-08 to 2025-12-12 | MID | 803 | $41,286 | $51.41 |
| W46 | 2025-11-10 to 2025-11-14 | GOOD | 1,022 | $54,599 | $53.42 |
| W47 | 2025-11-17 to 2025-11-21 | BEST | 2,227 | $120,697 | $54.20 |

**Note:** W47 has 4x more cycles than W43. Per-week breakdown is the primary view.

---

## Test Sequence

### Step 0: Instrument the simulator (shared with entry-signals prompt)

Add optional `on_bar_in_trade` callback to `run_sim_filtered` (same pattern as existing `on_cycle_exit`). Records per-bar snapshots during each trade. Default None, no behavior change.

**Verify:** Cycle outputs identical with callback=None vs callback=recording_fn.

If already completed for entry-signals prompt, skip.

### Step 1: Compute and tag (shared with entry-signals prompt)

Run instrumented sweep on 5 test weeks. Record per-bar snapshots with: price, current_pnl_ticks, mfe_ticks, mae_ticks, bar_offset, all regime features (signed_chop, dchop, d2chop, signed_slope, dr2, dslope).

**What's NOT pre-computed in Step 1:** Trade-behavior features (hold_ratio, mfe_rate, mfe_retracement, mae_increment, mae_proximity, range_ratio, d_range_ratio) are derived from the raw snapshot values during analysis (Steps 2-4). hold_ratio in particular depends on median_bars_to_reversal which requires a trailing window size N — a parameter not determined until Step 3. Store the raw inputs (bar_offset, mfe_ticks, mae_ticks, bar ranges); compute derived ratios during analysis.

**Resolution:** Regime features computed on 250-tick aggregated bars. Raw trade data (price, mfe, mae, ranges) recorded per 250-tick bar during the trade.

**Output:**
- `lab/output/rotational-NQ-scale-detection/regime-direction-tagged-cycles.csv` (per-cycle)
- `lab/output/rotational-NQ-scale-detection/regime-direction-intrade-bars.csv` (per-bar snapshots with price)

If Track A already ran Steps 0-1, reuse output only if Track A FAILED (baseline unchanged). If Track A SUCCEEDED, re-run Step 1 with the new entry filter active to generate snapshots of the updated cycle population.

### Step 2: In-trade correlation analysis

For cycles that ended in HARD_STOP vs REVERSAL, compare feature trajectories:
- **Regime signals:** Do losing trades show rising dR2, rising dSlope, signed signals turning against position?
- **Trade-behavior signals:** Do losing trades show low mfe_rate, high hold_ratio, high mae_proximity, expanding range_ratio?
- Do winning trades show stable/favorable trajectories?
- **Lead time:** At what point do signals diverge? How many bars before the outcome? Compute the median divergence point for each feature.

Per-week breakdown required.

### Kill gate

**Dies if either condition is true:**
1. HARD_STOP and REVERSAL trades show no distinguishable feature trajectories (signals don't diverge before outcome). If you can't tell a loser from a winner mid-trade, management won't help.
2. Signals diverge but median lead time is < 3 bars before outcome. At ~10-30 seconds per 250-tick bar, fewer than 3 bars provides insufficient time to evaluate and act in live trading — especially given SC's within-bar timing constraints.

Record findings and stop.

### Step 3: Application mapping + redundancy check

Map features to management actions. Check redundancy — a stalling trade has low mfe_rate, high hold_ratio, AND rising mae_proximity simultaneously. If these are >0.8 correlated, keep only the strongest.

### Step 4: Loss replay analysis (5 test weeks)

Replay feature trajectories for ALL cycles (HARD_STOP and REVERSAL). Evaluate each management action separately — different actions have different cost structures.

**Early exit replay:**
- For every HARD_STOP cycle: if we had exited when the signal fired, what would the loss have been vs -$600?
- For every REVERSAL cycle: did the signal fire? If so, what was the exit price at that point — the trade would have been cut at a smaller win or small loss instead of the full +$200 reversal.
- Net benefit = (savings on stops) - (cost on prematurely cut winners)
- **Winner-touch rate:** Must fire on < 20% of REVERSAL cycles.

**Skip-add replay:**
- For every cycle where the add triggered: would skip-add signal have fired at the add point?
- For HARD_STOP cycles with add: savings = avoided doubling into a loser (loss with 1 contract vs 2 contracts)
- For REVERSAL cycles with add: cost = foregone incremental profit (won with 1 contract instead of 2)
- Net benefit = (savings on stopped-with-add trades) - (foregone profit on reversed-with-add trades)

**Tighten-stop replay:**
- Sweep tighten levels: 30, 35, 40, 45, 50 ticks. For each level:
- For every cycle: if we had tightened stop from 60 to [level] ticks when the signal fired, would the trade have been stopped at the tighter level?
- For HARD_STOP cycles: savings = stopped at [level] instead of 60 ticks (smaller loss)
- For REVERSAL cycles that would have been stopped at [level]: cost = turned a winner into a loss (trade survived at 60 but would be clipped at [level])
- Net benefit = (savings from earlier stops) - (cost of clipping would-be winners)
- Select the level with the best net benefit across all test weeks. If no level is positive on all weeks, tighten-stop fails.

Per-week breakdown. Net benefit must be positive on ALL test weeks for each action independently.

**Output:** `lab/output/rotational-NQ-scale-detection/trade-management-loss-replay.csv`

### Step 5: Fork and verify simulator

Fork sweep into `rotational-NQ-scale-detection-sweep-managed.py`. Add management hooks.

**Verify first:** Run fork with all management disabled. Output must be identical to original sweep. Only then enable management features.

### Step 6: Live sim with management (5 test weeks)

Test each winning management action in isolation first:
- Run managed sweep with ONLY early exit enabled (if it passed Step 4). All other actions disabled.
- Run managed sweep with ONLY skip add enabled. All other actions disabled.
- Run managed sweep with ONLY tighten stop enabled. All other actions disabled.
- Run managed sweep with ONLY break-even stop enabled (mechanical rule — test standalone).

Compare each against chop-only baseline. Per-week breakdown is primary view.

Then combine the actions that individually improved PnL and test together. If the combined result is worse than the best individual action, the actions interfere — use the best individual only.

### Step 7: Combine with entry signals (if entry-signals prompt also succeeded)

If `rotational-NQ-prompt-entry-signals.md` produced a winning entry feature, test both together: entry filter narrows entries, management modifies surviving trades.

**Interaction effect:** Entry filter changes which trades exist for management. Report all three: entry-only, management-only, combined. Combined must outperform the better individual to justify complexity.

Skip if entry-signals prompt failed or hasn't been run.

### Step 8: Full P1 validation

Run on full P1. Per-week breakdown required.

### Step 9: Sanity check

Randomize each feature-triggered action independently:
- **Early exit:** Random exit timing at matching frequency (10 seeds).
- **Skip add:** Random skip at matching frequency.
- **Tighten stop:** Apply tightened stop at random bars matching signal frequency.

**Break-even stop is excluded from sanity check** — it's a mechanical rule (MFE > threshold), not a feature-triggered action. There's no "random" version that makes sense. Its value is tested directly by comparing strategy PnL with and without the rule.

Key question for feature-triggered actions: does the specific signal outperform random application of the same action at the same frequency?

### Step 10: Handoff to bench

Freeze configuration. Write frozen params. Create verify report.

**SC implementation feasibility assessment (required before handoff):**
- Which management actions require C++ changes vs existing SC features
- Estimated complexity per action
- Whether Python ↔ SC within-bar timing affects management decisions
- If complexity is too high relative to improvement, note as deployment risk

Bench runs: stress tests, P2 holdout (ONE SHOT), verdict.

---

## Success criteria

- Average loss per stopped cycle decreases without proportional decrease in average win
- Net benefit positive across ALL test weeks (not just pooled)
- Winner-touch rate < 20% for early exit signals (does not apply to skip add, break-even, tighten)
- Improvement survives full P1 per-week
- P2 E[R] does not degrade below $55.28 — tested in bench

**Kill criteria:**
- After Step 2: no trajectory divergence → stop
- After Step 4: net benefit negative on any test week → stop
- After Step 4: early exit winner-touch rate > 20% → signal too aggressive, stop
- After Step 6: improvement only in pooled stats → likely W47-driven, stop
- After Step 8: P1 improvement < 5% E[R] → marginal, not worth C++ complexity

## Failure modes

- Trade-behavior features (hold_ratio, mfe, mae) likely correlated — check redundancy before combining
- median_bars_to_reversal for hold_ratio needs trailing window — test sensitivity
- Simulator fork adds complexity — verify identity before any testing
- Early exit could reduce winners if signal is noisy — must show net benefit
- SC implementation of dynamic stops and early exit is substantially harder than entry gating
- Within-bar timing differences between Python and SC may affect management decisions differently than entry decisions

## Pipeline boundary

Steps 0-9 execute in lab. Step 10 hands off to bench. Do not run stress tests or P2 in lab.
