# Track C2: Loss Mitigation — Adaptive Stops, Session Context, Mechanical Rules

> **Archetype:** rotational
> **Instrument:** NQ
> **Status:** FAILED (2026-03-30). All 4 approaches dead: HS sweep (HS=60 optimal), session context (no signal), adaptive stop (net negative), partial profit (break-even kills reversals)
> **Created:** 2026-03-30
> **Parent study:** `rotational-NQ-prompt-scale-detection.md`
> **Predecessor:** Track C (`rotational-NQ-prompt-trade-management-c.md`) — FAILED. Mid-trade signals did not differentiate winners from losers in time. This prompt takes a different approach.
> **Depends on:** Track A (PASSED) + Track B (PASSED). Baseline includes both entry filters.
> **Data:** `data/NQ-1tick-calibration.csv` (P1), `data/NQ-1tick-holdout.csv` (P2)

---

## Problem

Same as Track C: the A+B filtered strategy (85% WR, 15% SR, ~6,500 cycles) loses ~$600 per hard stop. Track C attempted mid-trade signal reading to cut losses early — it failed because the 10pt step with 60-tick stop resolves too fast for mid-trade features to diverge before the outcome.

This prompt takes three different approaches:
1. **Adaptive stop from entry conditions** — use what's known AT ENTRY to set per-trade risk (no mid-trade reading needed)
2. **Session context signals** — mathematical derivatives of time-of-day that capture WHY certain conditions produce more stops
3. **Mechanical rules** — structural changes that reduce loss severity without any signal

## Prior art

- **Track A+B baseline:** chop < 0.10 + dR2 <= -0.40 + dSlope <= -2.0 + fade_confirm < 0.40. P1: 6,496 cycles, 85% WR, 15% SR, E[R]=$78.16. P2: E[R]=$80.55, PF=1.91.
- **Track C (FAILED):** Mid-trade signals (signed_chop, dR2, dSlope, signed_slope, hold_ratio, mfe_rate, mae_proximity, range_ratio) did not produce actionable signal. Trajectory divergence was either absent or occurred < 3 bars before outcome.
- **Track B critical finding:** Hypotheses can invert — test both directions for any directional signal.
- **HS selection:** HS=60 (1.5x step) chosen from original 144-config sweep on unfiltered baseline. Not re-optimized for A+B filtered population.

## Why Track C failed — and why C2 is different

Track C tried to READ the trade mid-flight and ACT before the stop. The 10pt/60-tick trade resolves in seconds to minutes — not enough time for regime or trade-behavior features to diverge meaningfully.

Track C2 avoids mid-trade signal reading entirely:
- **Adaptive stop:** decision made AT ENTRY, not mid-trade
- **Session context:** decision made AT ENTRY, based on session state
- **Mechanical rules:** no decision at all — structural PnL change applied to every trade

---

## Part 1: HS Sweep (parameter optimization)

The 60-tick hard stop was optimized for the unfiltered baseline (18K cycles, 76% WR). With A+B filters (6.5K cycles, 85% WR), the optimal HS may have shifted.

**Sweep range:** 40, 45, 50, 55, 60 ticks. No wider — 60 is the maximum.

**Method:** Run A+B filtered sim at each HS level across full P1. Compare E[R], PF, WR, SR, total PnL. Per-week breakdown.

**Success:** If a tighter HS improves E[R] by > 3% over HS=60, adopt it as the new baseline for the rest of Track C2.

**Risk:** Tighter stops increase SR — some trades that would have survived at 60 ticks get stopped at 50. The improvement comes from smaller losses per stop, but only if the increased SR doesn't offset the savings.

---

## Part 2: Session Context Signals (entry gates)

Mathematical derivatives of time-of-day that capture the CONDITIONS producing stops, not the clock time itself. All computed at entry from completed bars — no mid-trade reading, no look-ahead.

### 1. Tick rate ratio (market speed relative to session)

```
tick_rate = 250 / bar_duration[i-1]                    (ticks per second, previous completed bar)
session_avg_tick_rate = mean(tick_rate) from RTH open to bar i-1  (completed bars only, no entry bar)
tick_rate_ratio = tick_rate / session_avg_tick_rate
```

- > 1.0 = market is faster than session average (more active)
- < 1.0 = market is slower than session average (quieter)
- **Warmup:** First 20 completed RTH bars = neutral (tick_rate_ratio = 1.0). Not enough bars to establish a meaningful session average before that.
- High tick rate → 10pt moves happen in seconds (momentum-driven, harder to fade)
- Low tick rate → 10pt moves happen over minutes (more oscillation, better for rotation)

**Hypothesis:** Entries during low tick_rate_ratio outperform entries during high tick_rate_ratio. **Test both directions.**

### 2. Session range consumption

```
session_range = session_high - session_low              (from RTH open to current bar)
session_range_ratio = session_range / ATR_at_open
```

Where ATR_at_open = the engine's rolling ATR value at the first completed RTH bar (computed from overnight bars, already available in the aggregated bar data). This is a multi-bar rolling average, not a single bar's range.

- Low ratio (< 0.5) = session range barely established, lots of room for rotation
- High ratio (> 1.5) = session has already moved significantly, range is extended

**Hypothesis:** Entries when session_range_ratio is low (early range, room to rotate) outperform entries when high (extended range, more likely trending). **Test both directions.**

### 3. Price displacement from session midpoint

```
session_mid = (session_high + session_low) / 2
price_displacement = (entry_price - session_mid) / ATR_at_open
```

- Near 0 = entry at session midpoint (symmetric reversion potential)
- Strongly positive/negative = entry at session extreme (pullback may be start of range expansion)

**Hypothesis:** Entries near the midpoint (low absolute displacement) outperform entries at extremes (high absolute displacement). **Test both directions.**

### 4. Stop rate acceleration (trailing, lagging)

```
recent_sr = stop_rate over last N completed cycles
session_sr = stop_rate since session open
sr_acceleration = recent_sr - session_sr
```

- Positive = stop rate increasing (conditions deteriorating)
- Negative = stop rate decreasing (conditions improving)

**Lagging indicator warning:** Requires completed cycles to compute. With ~100 cycles/day, trailing windows of 10-20 cycles cover 10-20% of the day. By the time 3 stops appear in 10 cycles, ~$1,800 is already lost. This signal detects what happened, not what's about to happen. Included for completeness but likely too slow for this strategy's timescale.

**Hypothesis:** Entries when sr_acceleration is negative (conditions improving) outperform entries when positive (deteriorating). Expect weak signal due to lag.

---

## Part 3: Mechanical Rules (no signals)

### 5. Partial profit at intermediate level

At N pts in favor, take half the position off. Remaining half runs with break-even stop.

**Eligibility:** Only applies to **depth_1 trades** (2 contracts — the add triggered at 10pts against, then price recovered). Depth_0 trades (1 contract, no add) cannot partial. Verify what percentage of trades are depth_1 before relying on this mechanic — if most trades are depth_0, the eligible population may be too small to matter.

```
If depth_1 trade AND MFE reaches N pts from avg_entry:
  → close 1 of 2 contracts (lock in N pts profit on that contract)
  → move stop on remaining contract to avg_entry (break-even)
  → remaining contract exits at 10pt reversal or break-even stop
```

**Outcomes (depth_1 only):**
- If trade reverses fully (10pts from anchor): profit = N pts (partial) + reversal PnL (remainder)
- If trade stalls and stops at break-even: profit = N pts (partial) + 0 (remainder) = small win
- If trade never reaches N pts and stops: loss unchanged from baseline (partial never fired)

**Sweep N:** 3, 4, 5, 6, 7 pts. Find the level where net PnL improves.

**Key math:** This only helps on depth_1 trades that reach N pts in favor then reverse back. If N is too low, you lock in tiny profit and sacrifice upside. If N is too high, few trades reach it and the rule rarely fires.

### 6. Adaptive stop from entry quality (no new signal)

Use fade_confirm (already computed, already passed) to scale the hard stop:

```
raw_hs = base_hs - (fade_confirm / 0.40) * (base_hs - min_hs)
adaptive_hs = clamp(raw_hs, min_hs, base_hs)
```

Where base_hs = 60 ticks (or whatever Part 1 finds), min_hs = 40 ticks. Clamp prevents exceeding base_hs when fade_confirm is negative (entry price outside prev bar's range).

- fade_confirm = 0.01 (very strong entry) → HS ≈ 59 ticks (near full stop)
- fade_confirm = 0.20 (moderate entry) → HS ≈ 50 ticks
- fade_confirm = 0.39 (borderline entry) → HS ≈ 41 ticks (tight stop)
- fade_confirm = -0.10 (extreme momentum) → raw = 65, clamped to 60 (base_hs)

**Rationale from Track B:** Low fade_confirm = entry at max pullback momentum = more room for reversion = deserves wider stop. High fade_confirm = borderline entry = less reversion room = tighter stop appropriate.

**Note:** This uses the OPPOSITE intuition from what you'd expect — low fade_confirm gets more room, not less. Aligned with Track B's inversion finding.

---

## Baseline

Track A+B combined:
- **Config:** SD=10 HS=60 depth_1 MCS=2 + chop < 0.10 + dR2 <= -0.40 + dSlope <= -2.0 + fade_confirm < 0.40
- **P1:** 6,496 cycles, 85% WR, 15% SR, E[R]=$78.16
- **P2:** E[R]=$80.55, PF=1.91

## Locked files (DO NOT MODIFY)

- `lab/rotational-NQ-simulator.py`
- `lab/rotational-NQ-sweep-baseline.py`
- `lab/rotational-NQ-scale-detection-sweep.py` (fork for mechanical rules)
- `lab/rotational-NQ-scale-detection-engine.py` (extend for session context signals)

## Study files

- Engine extension: add `compute_session_context()` to engine
- Forked simulator for mechanical rules (partial profit, adaptive stop): `lab/rotational-NQ-scale-detection-sweep-c2.py`
- Analysis scripts: `lab/rotational-NQ-scale-detection-step*.py`

---

## Test Data

Select 5 representative weeks from P1 **Track A+B filtered** performance. This is done in Step 0.

**Note:** Per-week breakdown is the primary view.

---

## Test Sequence

### Step 0: Select test weeks + HS sweep

**Week selection:** Run A+B config across all P1 weeks. Rank by filtered PnL. Select WEAKEST/LOW/MID/GOOD/BEST.

**HS sweep:** Run A+B config at HS = 40, 45, 50, 55, 60 across full P1. Per-week breakdown. If a tighter HS improves E[R] by > 3%, adopt it as the new baseline for remaining steps. If not, keep HS=60.

### Step 1: Compute session context features

Run A+B config on the 5 test weeks. At each entry, compute: tick_rate_ratio, session_range_ratio, price_displacement, sr_acceleration.

All features use completed bars and session state at the moment of entry — no look-ahead, no mid-trade reading.

**Output:** `lab/output/rotational-NQ-scale-detection/c2-session-context-tagged-cycles.csv`

### Step 2: Entry correlation analysis

For each session context feature, bucket cycles and compute SR, WR, avg $/cyc. Per-week breakdown. **Test both directions** for all features.

Key questions:
- Do entries during fast markets (high tick_rate_ratio) stop more?
- Do entries in extended sessions (high session_range_ratio) stop more?
- Do entries at session extremes (high price_displacement) stop more?
- Does sr_acceleration show any signal despite lag?

### Kill gate (session context)

**Dies if:** No session context feature shows SR spread > 3pt. Record findings. Session context signals proceed to Step 3 only if they pass. Mechanical rules (Steps 4-5) proceed regardless — they don't need signal validation.

### Step 3: Session context retroactive filter (5 test weeks)

For features that showed signal in Step 2, compute PnL at various thresholds. Per-week breakdown. Must improve ALL weeks.

### Step 4: Mechanical rules replay (5 test weeks)

**Partial profit replay:** For each N (3, 4, 5, 6, 7 pts):
- For each cycle, check: did MFE reach N pts?
- If yes: compute PnL with partial profit (1 contract closed at N pts, remainder at reversal or break-even)
- If no: PnL unchanged
- Net benefit vs baseline per week

**Adaptive stop replay:** For each cycle, compute adaptive_hs from fade_confirm. Re-evaluate: would the trade have stopped at the tighter level?
- For HARD_STOP cycles: savings from earlier stop (smaller loss)
- For REVERSAL cycles that would have been stopped at the tighter level: cost (turned winner into loss)
- Net benefit vs baseline per week

### Step 5: Fork and verify simulator

Fork sweep for mechanical rules (partial profit + adaptive stop). Verify identity with rules disabled.

### Step 6: Live sim (5 test weeks)

Test each winning item in isolation:
- Session context entry gate (if it passed kill gate)
- Partial profit at best N
- Adaptive stop
- HS from Step 0 (if different from 60)

Compare each against baseline. Per-week breakdown. Then combine winners.

### Step 7: Full P1 validation

Run on full P1. Per-week breakdown required.

### Step 8: Sanity check

- **Session context features:** Random filter at matching retention (10 seeds).
- **Adaptive stop:** Compare varying HS (based on fade_confirm) against a fixed HS at the average adaptive level. If the fixed version performs the same, the adaptation isn't adding value — just use the fixed tighter HS from the sweep.
- **Partial profit:** No sanity check needed — it's a mechanical rule with no signal component. Its value is tested directly in Step 6 (PnL with vs without).

### Step 9: Handoff to bench

Freeze configuration. Write frozen params. Verify report. Route to bench.

Bench runs: stress tests, P2 holdout (ONE SHOT), verdict.

---

## Success criteria

- At least one approach (HS sweep, session context, partial profit, or adaptive stop) improves E[R] by > 3% over A+B baseline
- Improvement survives full P1 per-week
- P2 E[R] does not degrade below A+B baseline ($80.55) — tested in bench

**Kill criteria:**
- After Step 0: no HS improvement > 3% → keep HS=60, continue with other approaches
- After Step 2: no session context signal → skip Steps 3, proceed to Step 4 (mechanical rules)
- After Step 4: no mechanical rule shows per-week improvement → study ends
- After Step 7: P1 improvement < 3% E[R] → marginal, stop

## Failure modes

- HS sweep may show HS=60 is already optimal — tighter stops increase SR more than they reduce per-stop losses
- Tick rate ratio may be redundant with choppiness (both capture market activity level)
- Session range ratio and price displacement may be too noisy intraday — the session range grows monotonically, making early-session values systematically different from late-session
- sr_acceleration is lagging — flagged, likely to fail but included for completeness
- Partial profit reduces upside on winners (locking in N pts means the partial contract misses the full 10pt move) — must verify net benefit
- Adaptive stop using fade_confirm might not add value over a fixed tighter HS — the sanity check tests this directly

## Pipeline boundary

Steps 0-8 execute in lab. Step 9 hands off to bench. Do not run stress tests or P2 in lab.
