# Regime, Direction & Trade-Behavior Signals — Index

> **Status:** Split into four tracks on 2026-03-30

## Execution Order

**Run in sequence: A → B → C → C2 → B2 → D.** Each track's winner becomes the next track's baseline. C and C2 failed. B2 is a new entry gate.

- A and B are **entry decisions** (before trade)
- C is **loss management** (during trade, reduce losses)
- D is **win extension** (during trade, increase wins)

## Track A: Entry Regime Signals — PASSED

**Prompt:** `rotational-NQ-prompt-entry-signals.md`
**Status:** frozen, P2 PASS

**Scope:** dR2, dSlope — regime transition rate at entry. Skip entries when regime isn't improving.

**Winner:** dR2 <= -0.40 + dSlope <= -2.0. P1 E[R] $53.43 → $63.22 (+18.3%), 64% retention.

## Track B: Fade Confirmation — PASSED

**Prompt:** `rotational-NQ-prompt-fade-confirmation.md`
**Status:** frozen, P2 PASS

**Scope:** Close position, range decay, volume shift, consecutive bar direction, directional speed — assessed at entry to confirm the pullback is exhausting before fading.

**Winner:** fade_confirm < 0.40. P1 E[R] $63.22 -> $78.16 (+24%), 98% retention, 12/12 weeks. All hypotheses inverted.

## Track B2: EMA Directional Gate + Hold — PASSED

**Prompt:** `rotational-NQ-prompt-ema-directional-b2.md`
**Status:** frozen, P2 PASS (H2+H5 overridden)

**Scope:** d2_ema9 (EMA9 curvature) as entry gate + d2_avg3 in-trade hold (delay reversal when curvature aligned).

**Winner:** Entry gate (|d2|<=0.5 neutral zone) + d2_avg3 hold. Combined P1 E[R]=$201 (+158%), P2 E[R]=$225, PF=4.04, 13/13 weeks.

## Track C: In-Trade Loss Management — FAILED

**Prompt:** `rotational-NQ-prompt-trade-management-c.md`
**Status:** FAILED — mid-trade signals did not differentiate winners from losers in time.

**Scope:** 8 candidates (4 regime + 4 trade-behavior) evaluated mid-trade for early exit, skip add, break-even stop, tighten stop.

## Track C2: Loss Mitigation (revised approach)

**Prompt:** `rotational-NQ-prompt-loss-mitigation-c2.md`
**Status:** FAILED

**Scope:** Avoids mid-trade signal reading. Instead: HS sweep (40-60), session context signals (tick rate ratio, session range, price displacement, SR acceleration), mechanical rules (partial profit, adaptive stop from fade_confirm).

## Track D: Extended Hold — superseded by B2

**Prompt:** `rotational-NQ-prompt-extended-hold.md`
**Status:** superseded — B2 hold mechanic achieved this objective via d2_avg3

**Scope:** At the 10pt reversal point, evaluate whether to hold for a larger target. B2's d2_avg3 hold delivered +128% E[R] improvement using EMA curvature as the hold signal.

## Shared Data

Steps 0-1 from Track A produced shared output files. Subsequent tracks generate their own tagged cycles from whatever baseline survives.

**Shared output:**
- `lab/output/rotational-NQ-scale-detection/regime-direction-tagged-cycles.csv`
- `lab/output/rotational-NQ-scale-detection/regime-direction-intrade-bars.csv`

## Layers (completed)

| Layer | Track | Question | Timescale | Result |
|---|---|---|---|---|
| Regime | A (chop + dR2/dSlope) | Is the market rotational and improving? | Last 3 agg bars | PASSED |
| Move | B (fade confirmation) | Is this pullback exhausting? | Entry tick + last 2-3 completed bars | PASSED |
| Direction + Hold | B2 (d2_ema9 + d2_avg3) | Aligned with trend? Hold when curvature supports. | EMA 9/21 at entry + mid-trade | PASSED |
| Defense | ~~C~~ (mid-trade signals) | ~~Is this trade going badly?~~ | ~~During the trade~~ | FAILED |
| Defense | ~~C2~~ (adaptive stops + session) | ~~Can we reduce loss severity?~~ | ~~At entry + session~~ | FAILED |
| Offense | ~~D~~ (extended hold) | ~~Should we hold past the reversal?~~ | ~~At 10pt reversal point~~ | Superseded by B2 |

## Open Investigations

| # | Dimension | Status | Notes |
|---|---|---|---|
| 1 | **Loss mitigation** | Unsolved | C and C2 both FAILED. 100% of losses from depth_1 stops. Previous approaches exhausted — new methods needed. |
| 2 | **Position sizing** | Unexplored | Fixed 1+1 add. Could scale based on entry quality / signal confidence. |
