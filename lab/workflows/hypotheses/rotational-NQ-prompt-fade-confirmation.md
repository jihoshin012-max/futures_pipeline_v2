# Track B: Fade Confirmation Signals — Experiment Prompt

> **Archetype:** rotational
> **Instrument:** NQ
> **Status:** frozen
> **Created:** 2026-03-30
> **Parent study:** `rotational-NQ-prompt-scale-detection.md`
> **Depends on:** Track A (`rotational-NQ-prompt-entry-signals.md`) — PASSED. Baseline includes Track A's entry filter.
> **Next:** Track C (`rotational-NQ-prompt-trade-management-c.md`) — depends on this prompt's outcome.
> **Data:** `data/NQ-1tick-calibration.csv` (P1), `data/NQ-1tick-holdout.csv` (P2)

---

## Problem

The rotation strategy fades pullbacks: enters LONG after a 10pt drop, SHORT after a 10pt rise. The chop filter (Track A baseline) ensures the regime is rotational, and Track A's dR2/dSlope filter ensures the regime is improving. But neither assesses the **specific pullback being faded** — is the move exhausting, or does it have more to go?

A fade entered while the pullback still has momentum is more likely to continue against the position and hit the hard stop. A fade entered as the pullback stalls is more likely to reverse.

This prompt tests **move-level confirmation signals** at the entry bar — evaluated after chop and dR2/dSlope pass, as one final check before entering.

## Prior art

- **Chop filter:** chop < 0.10 at lb=3. Validated P1 + P2. Regime-level gate.
- **Track A (entry signals):** dR2 <= -0.40, dSlope <= -2.0. P1: E[R] $53.43 → $63.22 (+18.3%), 64% retention, 9/12 weeks improved. Handed off to bench.
- **Feature discovery (Steps 5-6):** Volume-based features (vol_imbalance, signed_price_vol, cum_delta) showed no signal for cycle outcome prediction. BUT those tests predicted outcomes over the full trade duration. Fade confirmation operates at a shorter horizon (next 10pt move), where microstructure signals may be more relevant.

## Candidates

### 1. Close position (entry price within previous bar's range)

**Important: all features use completed bars PRIOR to the entry bar, not the entry bar itself.** The entry bar is incomplete at entry time — using its OHLC or volume would include data from ticks after entry (look-ahead contamination). This is the same within-bar timing issue found during choppiness calibration (see scale detection journal, "C++ calibration fix" and "SC bar alignment investigation").

```
fade_confirm = (entry_price - prev_bar_low) / (prev_bar_high - prev_bar_low)
```

Where `entry_price` = the tick price at entry (known), `prev_bar` = last completed 250-tick bar.

- Range: typically [0, 1], but can be negative or >1 if entry price is outside prev bar's range
- **Negative fade_confirm** (LONG entry below prev_bar_low) = pullback blew through the entire prior bar's support = strong momentum against the fade = potentially the strongest block signal
- Near 1.0 = entering near top of previous bar's range (for a LONG, buyers were in control)
- Near 0.0 = entering near bottom
- Guard: if prev_bar_high == prev_bar_low (zero-range bar), set to 0.5 (neutral)

**Direction-relative:**
```
fade_confirm = (entry_price - prev_bar_low) / (prev_bar_high - prev_bar_low)    (LONG)
fade_confirm = (prev_bar_high - entry_price) / (prev_bar_high - prev_bar_low)    (SHORT)
```

High fade_confirm = entry price is on the "exhaustion" side of the previous bar = favorable.

**Hypothesis:** Entries with high fade_confirm (>0.7) outperform entries with low fade_confirm (<0.3).

### 2. Range decay (pullback losing momentum)

Computed from completed bars before the entry bar:

```
range_decay = range[i-1] / range[i-2]
```

- < 1.0 = the bar before entry had smaller range than the one before it = momentum fading
- > 1.0 = range expanding = momentum increasing

**Multi-bar version (preferred — less noisy):**
```
avg_range_decay = mean(range[i-1-k] / range[i-2-k] for k in 0..lb-1)
```

All bars in the computation are completed before entry. No look-ahead.

**Hypothesis:** avg_range_decay < 0.8 at entry = pullback momentum fading = fade is more likely to work.

### 3. Volume shift (order flow reversal)

Computed from the completed bar before entry:

```
delta = (ask_vol[i-1] - bid_vol[i-1]) / (ask_vol[i-1] + bid_vol[i-1])
```

- Range: [-1, 1]
- Positive = buyers dominant in previous bar, negative = sellers dominant

**Direction-relative formula:**
```
flow_confirm = delta      (for LONG entries — buyers returning)
flow_confirm = -delta     (for SHORT entries — sellers returning)
```

High flow_confirm = volume in the previous completed bar was shifting in the fade direction = exhaustion.

**Note:** Volume features showed no signal in Steps 5-6 for cycle outcomes. This tests them at a different prediction horizon (next 10pt move vs full trade). If they still show no signal here, volume is conclusively not useful for this strategy at any timescale.

### 4. Consecutive bar direction (micro-momentum shift)

Computed from the 3 completed bars before the entry bar:

```
direction_bars = count of bars [i-3, i-2, i-1] where close[j] > close[j-1]  (for LONG)
direction_bars = count of bars [i-3, i-2, i-1] where close[j] < close[j-1]  (for SHORT)
```

- Range: 0 to 3
- High count = price has already started reversing toward your fade direction in completed bars
- 0 count = price was still moving against your intended entry up to the bar before entry

**Hypothesis:** Entries where 2+ of the last 3 completed bars moved in the fade direction outperform entries where 0 did.

### 5. Directional speed (time + price momentum)

Computed from the completed bar before entry. Uses bar duration — the time dimension that no other feature captures.

```
bar_duration = time_last_tick[i-1] - time_first_tick[i-1]    (seconds)
directional_speed = (close[i-1] - open[i-1]) / bar_duration  (points per second, signed)
```

**Direction-relative:**
```
fade_speed = directional_speed     (LONG — positive = bar went up = in fade direction = favorable)
fade_speed = -directional_speed    (SHORT — negated so positive = bar went down = in fade direction = favorable)
```

- High fade_speed (positive) = previous bar moved in your fade direction (up for LONG, down for SHORT) = pullback reversing = favorable
- Low fade_speed (negative) = previous bar moved against your fade direction = pullback continuing with momentum = unfavorable
- Guard: if bar_duration = 0 (all ticks at same timestamp — rare), set to 0 (neutral)

**Note:** bar_duration alone showed no signal in Steps 5-6 for entry gating. But that tested duration in isolation, not combined with direction and range. The signed speed (displacement per second) is a different signal — it captures HOW FAST price is displacing in a direction, not just how long the bar took.

**Hypothesis:** Entries with high fade_speed (pullback reversing in the previous bar) outperform entries with low fade_speed (pullback continuing with momentum).

---

## Application

Entry gate only — evaluated after chop and Track A filters pass. One final check: "is this specific pullback exhausting?"

```
Pullback reaches 10pts
  → chop < 0.10?          (regime: rotational)
  → dR2/dSlope pass?      (regime: improving)
  → fade confirmed?       (move: exhausting)    ← THIS PROMPT
  → ENTER
```

No in-trade management. No simulator fork needed.

---

## Baseline

Track A's winning config (P2 PASS — confirmed 2026-03-29):
- **Config:** SD=10 HS=60 depth_1 MCS=2 + chop < 0.10 + dR2 <= -0.40 + dSlope <= -2.0
- **P1:** 6,624 cycles, 83% WR, 17% SR, E[R]=$63.22
- **P2:** 6,927 cycles, 84% WR, 16% SR, E[R]=$70.40, PF=1.731 — improved over P1

## Locked files (DO NOT MODIFY)

- `lab/rotational-NQ-simulator.py`
- `lab/rotational-NQ-sweep-baseline.py`
- `lab/rotational-NQ-scale-detection-sweep.py` (use existing filter injection)
- `lab/rotational-NQ-scale-detection-engine.py` (extend, don't modify existing functions)

## Study files

- Engine extension: add `compute_fade_confirmation()` to engine
- Analysis scripts: `lab/rotational-NQ-scale-detection-step*.py` (new step numbers)

---

## Test Data

Select 5 representative weeks from P1 **Track A filtered** performance. The Track A filter changes the cycle population — re-rank weeks by Track A filtered PnL and select WEAKEST/LOW/MID/GOOD/BEST. This is done in Step 0 below.

**Note:** Same W47 weighting concern applies. Per-week breakdown is the primary view.

---

## Test Sequence

### Step 0: Select test weeks

Run Track A's winning config across all P1 weeks. Rank by filtered PnL. Select WEAKEST/LOW/MID/GOOD/BEST. Record the table here before proceeding.

### Step 1: Compute and tag

Run Track A's winning config on the 5 test weeks. At each entry bar, compute all 5 fade confirmation features. Tag each cycle with: fade_confirm, range_decay (and avg_range_decay), flow_confirm, direction_bars, fade_speed.

These features use the entry tick price and the previous 2-3 completed bars' OHLC/volume — no data from the incomplete entry bar. No per-bar snapshots during the trade needed — this is entry-only.

**Output:** `lab/output/rotational-NQ-scale-detection/fade-confirm-tagged-cycles.csv`

### Step 2: Entry correlation analysis

For each feature, bucket cycles by feature value at entry and compute SR, WR, avg $/cyc per bucket. Per-week breakdown.

- Does fade_confirm > 0.7 outperform fade_confirm < 0.3?
- Does avg_range_decay < 0.8 outperform > 1.2?
- Does flow_confirm show any signal? (This is the final test of volume at this strategy's timescale.)
- Does direction_bars = 2-3 outperform 0?
- Does high fade_speed (slow/stalling pullback) outperform low fade_speed (fast/accelerating)?

Key question: **Do these features add information beyond chop + dR2/dSlope?**

### Kill gate

**Dies if:** No feature shows SR spread > 3pt. Record findings and stop. Volume conclusively dead for this strategy if flow_confirm shows nothing here.

### Step 3: Redundancy check

Check correlation between candidates:
- fade_confirm and direction_bars (both capture "price moving in fade direction" — may be redundant)
- fade_speed and range_decay (both capture momentum fading — speed uses time, range_decay uses range ratios)
- fade_speed and fade_confirm (speed captures how fast, position captures where — likely independent)

Keep the stronger of redundant pairs or combine if independent.

### Step 4: Retroactive filter (5 test weeks)

Compute PnL for subsets passing various thresholds. No sim re-run. Per-week breakdown — must improve ALL weeks.

**Output:** `lab/output/rotational-NQ-scale-detection/fade-confirm-retroactive.csv`

### Step 5: Live sim (5 test weeks)

Wire winning feature(s) into the existing sweep as an additional entry gate — same injection point, stacked after chop + dR2/dSlope. Compare against Track A baseline. Per-week breakdown.

### Step 6: Full P1 validation

Run on full P1. Per-week breakdown required.

### Step 7: Sanity check

Random filter at matching retention rate (10 seeds). Must outperform all seeds.

### Step 8: Handoff to bench

Freeze configuration. Write frozen params. Create verify report. Route to bench.

Bench runs: stress tests, P2 holdout (ONE SHOT), verdict.

---

## Success criteria

- At least one fade confirmation feature adds SR spread > 5pt within the Track A allowed window
- Improvement survives full P1 per-week
- P2 E[R] does not degrade below Track A baseline — tested in bench

**Kill criteria:**
- After Step 2: no signal → stop
- After Step 4/5: improvement only in pooled stats → stop
- After Step 6: P1 improvement < 5% E[R] over Track A baseline → marginal, stop

## Failure modes

- Close_position and direction_bars may be redundant (both capture price direction at bar level)
- Range_decay is noisy on a single bar ratio — avg over lb bars may be required
- Range_decay can't distinguish pullback exhaustion from low-activity periods (e.g., lunch hour). Contracting ranges could mean "pullback fading" or "market going quiet." If range_decay shows signal, verify it holds across different times of day.
- Volume (flow_confirm) has failed twice already (Steps 5-6 and this would be the third test). If it fails again, stop testing volume for this strategy.
- bar_duration alone showed no signal in Steps 5-6. fade_speed combines duration with direction — a different signal. But if fade_speed also fails, the time dimension adds no value for this strategy.
- **Cycle count constraint:** Track A cut to 6,624 cycles (64% retention). The H2 statistical gate requires >= 5,000 cycles. Track B must target HIGH retention (>80%) to stay above 5,000. This means looser thresholds — use fade confirmation to improve entry quality slightly rather than aggressively filtering. If the best threshold drops below 5,000 cycles, the feature is statistically infeasible regardless of signal strength.
- The more entry gates stacked, the more the strategy depends on specific conditions aligning — fragile in changing market regimes

## Pipeline boundary

Steps 0-7 execute in lab. Step 8 hands off to bench. Do not run stress tests or P2 in lab.
