# Track D: Extended Hold Signals — Experiment Prompt

> **Archetype:** rotational
> **Instrument:** NQ
> **Status:** draft
> **Created:** 2026-03-30
> **Parent study:** `rotational-NQ-prompt-scale-detection.md`
> **Depends on:** Track A (PASSED), Track B (pending), Track C (pending). Update baseline with whatever survives A + B + C before executing.
> **Data:** `data/NQ-1tick-calibration.csv` (P1), `data/NQ-1tick-holdout.csv` (P2)

---

## Problem

The rotation strategy exits at a fixed 10pt reversal. When conditions are strongly favorable mid-trade (pullback exhausted, regime rotational, trade moving well), the 10pt exit may leave profit on the table. The trade could have continued for 15, 20, or more points.

This prompt tests whether mid-trade signals can identify trades where holding past the 10pt reversal point produces a better outcome than the fixed exit.

**This is about increasing wins, not reducing losses.** Loss reduction is Track C's scope. Track D only modifies the exit on trades that reach the reversal target — it never changes the hard stop, add logic, or entry decisions.

## Prior art

- **Chop filter + Track A:** Validated entry gates. Filters already break the pure reversal chain — most reversals are blocked and the strategy enters a fresh watch phase. Holding longer doesn't disrupt the sequence any further than the filters already do.
- **Track A (entry signals):** dR2 <= -0.40, dSlope <= -2.0. P2 PASS (E[R]=$70.40, PF=1.73).
- **Track B (fade confirmation):** fade_confirm < 0.40. P2 PASS (E[R]=$80.55, PF=1.91, 98% retention).
- **Track C (management):** Tests loss reduction mid-trade. Track D uses the same signal categories but for the opposite purpose (extend winners vs cut losers).

### Track B critical finding — hypothesis inversion

**All Track B entry hypotheses were inverted.** The strategy profits from mean reversion: entering when the pullback has MAXIMUM momentum gives the most room for reversion. Entering during exhaustion is worse.

**Implication for Track D:** At the 10pt reversal point, the same inversion may apply. The intuitive hypothesis is "hold when conditions are favorable (calm, rotational)." But if the strategy profits from strong momentum, the hold decision might work differently:

- **Trades that entered with strong pullback momentum** (low fade_confirm) may have stronger mean reversion — meaning the 10pt reversal captures most of the move, and extending adds little.
- **Trades that entered with weaker momentum** (higher fade_confirm, still below 0.40) may have more residual reversion — price hasn't fully snapped back yet, and holding could capture more.

[SPECULATION] This is unverified — Step 1's reversal characterization will show whether entry momentum strength correlates with post-10pt continuation. Test both directions for every signal.

## Mechanical verification

The extended hold does NOT break the strategy's step-based sequence. Verified in the simulator code:

1. After any exit (normal reversal, extended hold exit, hard stop): `reset_state()` clears all state, `start_watch()` begins fresh watch
2. Reversal re-entry is already blocked ~79% of the time by filters → most cycles end with watch → fresh seed
3. Hard stop remains at 60 ticks from `avg_entry` during extended hold — unchanged
4. Add check remains at 10pts against from `anchor` (entry price) — unchanged
5. No state leaks between cycles — fade counts update at entry, not exit

The extended hold changes one cycle's duration and PnL. The next cycle starts identically from watch.

---

## Exit Rules (candidates)

### Rule 1: Conditional reversal delay (one extra step)

When the 10pt reversal point is reached, check mid-trade signals. If conditions are strongly favorable, skip this reversal and hold for the next step (20pts total). If not, take the normal 10pt exit.

```
At 10pt reversal point:
  → check mid-trade signals
  → if favorable: hold, set new target at 20pts from entry, trail stop to entry (break-even)
  → if not favorable: normal 10pt exit
```

- Maximum extension: one extra 10pt step (20pts total). NOT unlimited holding.
- Break-even stop activates when holding past 10pts — the 10pts of favorable movement are protected.
- If price reaches 20pts: exit (reversal at 20pts instead of 10pts)
- If price reverses back to entry after holding past 10pts: exit at break-even (0 PnL instead of +10pt win)

**Risk:** A trade that would have been a +10pt win becomes a 0 PnL (break-even) if the extension fails. The cost is the foregone 10pt win.

### Rule 2: Stepped trailing stop

As price moves each additional 10pts in favor beyond the first 10pts, trail the stop up by 10pts. Preserves the step grid.

```
Entry at 100 (LONG example):
  → 10pts in favor (110): check signals → if hold: stop at 100 (break-even)
  → 20pts in favor (120): stop trails to 110 (+10pt locked in)
  → 30pts in favor (130): stop trails to 120 (+20pt locked in)
  → Stop hit at any step: exit at stop level
```

- No maximum hold — but each step must pass signal checks to continue
- Minimum locked-in profit increases with each step
- More complex than Rule 1 but captures larger moves

### Rule 3: ATR-scaled conditional target

Instead of a fixed extra step, scale the extension by current ATR. When ATR is low (calm/rotational), the extension is small. When ATR is expanding, no extension (conditions deteriorating).

```
At 10pt reversal point:
  → if ATR_ratio < 0.8 (volatility contracting): extend target to 10 + (10 * (1 - ATR_ratio)) pts
  → if ATR_ratio >= 1.0: normal 10pt exit
  → break-even stop at entry
```

- Adapts to conditions — extends more in calm markets, less in volatile
- More conservative than fixed extra step

---

## Signal candidates for the hold decision

Evaluated at the moment the 10pt reversal would normally trigger. All from completed bars.

### From Track C's signal set (reuse)
- **dR2:** Is R2 falling? (regime becoming more rotational = favorable for hold)
- **dSlope:** Is slope decreasing? (trend weakening = favorable for rotation to continue)
- **Signed chop relative to position:** Is displacement aligned with position direction?
- **range_ratio:** Is volatility contracting? (favorable for rotation)

### From Track B's signal set (reuse)
- **fade_speed of the current move:** Is the move in favor accelerating or stalling?
- **direction_bars:** Are recent bars continuing in the favorable direction?

### Trade-behavior at reversal point
- **mfe_rate at 10pts:** How quickly did we reach the reversal target? Fast = momentum in favor. Slow = grinding — extension may not work.
- **mae_proximity at reversal:** How close to the stop did we get during this trade? If we nearly stopped out before reaching 10pts, the trade was messy — don't extend.

---

## Baseline

Use whatever config survives Tracks A + B + C. Update before executing.

**Track A winner:** chop < 0.10 + dR2 <= -0.40 + dSlope <= -2.0. P1: E[R]=$63.22. P2: E[R]=$70.40.

**Track B winner:** + fade_confirm < 0.40. P1: E[R]=$78.16, 85% WR, 15% SR, 98% retention. P2: E[R]=$80.55, PF=1.91.

**Track C:** TBD — update baseline after Track C completes. If Track C fails, use Track A + B numbers above.

## Locked files (DO NOT MODIFY)

- `lab/rotational-NQ-simulator.py`
- `lab/rotational-NQ-sweep-baseline.py`
- `lab/rotational-NQ-scale-detection-sweep.py` (fork for this track)
- `lab/rotational-NQ-scale-detection-engine.py` (extend, don't modify existing functions)

## Study files

- **Forked simulator:** `lab/rotational-NQ-scale-detection-sweep-extended.py` — fork of sweep with conditional hold logic at the reversal point. Must reproduce identical results when hold is disabled.
- Engine: reuse `compute_intrade_signals()` from Track C if available, or add `compute_hold_signals()`
- Analysis scripts: `lab/rotational-NQ-scale-detection-step*.py` (new step numbers)

---

## Test Data

Select 5 representative weeks from the final baseline (post A + B + C). Re-rank by filtered PnL, select WEAKEST/LOW/MID/GOOD/BEST. This is done in Step 0.

**Note:** Per-week breakdown is the primary view.

---

## Test Sequence

### Step 0: Select test weeks

Run final baseline config across all P1 weeks. Rank by filtered PnL. Select 5 weeks.

### Step 1: Characterize reversal cycles

Before testing extensions, understand the current reversal population:
- How many bars does the average reversal cycle take to reach 10pts?
- After reaching 10pts, what does price do next? Compute MFE beyond 10pts for all reversal cycles (how much further did price go in favor before coming back?).
- What percentage of reversals would have reached 15pts? 20pts? 25pts?
- What percentage would have come back to entry (break-even) or worse after reaching 10pts?

This establishes the theoretical ceiling for extended holds and the cost distribution (how many 10pt wins would become break-evens or losses).

**Output:** `lab/output/rotational-NQ-scale-detection/extended-hold-reversal-analysis.csv`

### Step 2: Signal correlation at reversal point

At the moment each reversal fires, compute all signal candidates. Compare cycles where price continued past 15pts vs cycles where price reversed back:
- Which signals differentiate "continuation" from "reversal back"?
- How much lead time do they provide?

Per-week breakdown required.

### Kill gate

**Dies if:** No signal differentiates continuation from reversal-back at the 10pt point. If you can't predict whether price will continue, holding is gambling. Record findings and stop.

### Step 3: Replay analysis

For each exit rule (conditional delay, stepped trailing, ATR-scaled):
- Replay every reversal cycle with the extension rule applied
- Compare extended PnL vs normal 10pt PnL per cycle
- **Cost of extension:** How many +10pt wins become break-even or losses?
- **Benefit of extension:** How much additional profit from cycles that continued?
- Net benefit = (additional profit from extensions) - (lost profit from failed extensions)
- **Break-even conversion rate:** Must be < 30%. If more than 30% of extended trades give back the 10pt win, the rule is too aggressive.

Per-week breakdown. Net benefit positive on ALL test weeks.

**Output:** `lab/output/rotational-NQ-scale-detection/extended-hold-replay.csv`

### Step 4: Fork and verify simulator

Fork sweep. Add conditional hold logic at the reversal point. Verify fork produces identical results with hold disabled.

### Step 5: Live sim (5 test weeks)

Test each exit rule in isolation. Compare against baseline. Per-week breakdown.

Then test winning rule with best signal combination.

### Step 6: Full P1 validation

Run on full P1. Per-week breakdown required.

### Step 7: Sanity check

Randomize the hold decision: at each reversal point, randomly decide to hold or exit at the same frequency as the real signal. If random holds produce similar improvement, the signal isn't predicting — the benefit is just from holding longer on average.

### Step 8: Handoff to bench

Freeze configuration. Write frozen params. Create verify report. Route to bench.

Bench runs: stress tests, P2 holdout (ONE SHOT), verdict.

---

## Success criteria

- Net benefit (additional profit from extensions - lost profit from failed extensions) positive across ALL test weeks
- Break-even conversion rate < 30% (at most 30% of extended trades give back the 10pt win)
- Improvement survives full P1 per-week
- P2 E[R] does not degrade below pre-extension baseline — tested in bench

**Kill criteria:**
- After Step 2: no signal differentiates continuation from reversal-back → stop
- After Step 3: break-even conversion rate > 30% → too many wins lost → stop
- After Step 3: net benefit negative on any test week → stop
- After Step 6: P1 improvement < 5% E[R] → marginal, stop

## Failure modes

- **Asymmetric risk:** A +10pt win becoming a 0 PnL (break-even) is a -$200 swing (for 1 contract). The extension needs to produce enough +20pt wins to offset this. With 82% WR, most cycles are reversals — even a small break-even conversion rate affects many trades.
- Signals at the 10pt point may look identical for continuation and reversal-back — the market hasn't decided yet, and no feature can predict what happens next.
- Rule 2 (stepped trailing) adds complexity — each step needs its own signal check, making the simulator fork significantly more complex to implement and verify in C++.
- ATR-scaled targets (Rule 3) depend on ATR accuracy at the 250-tick bar level, which may be noisy.
- SC implementation: conditional hold at reversal requires the C++ study to evaluate signals at the reversal point and decide whether to flatten or hold — a more complex code path than entry gating.

## Pipeline boundary

Steps 0-7 execute in lab. Step 8 hands off to bench. Do not run stress tests or P2 in lab.
