# Regime, Direction & Trade-Behavior Signals — Index

> **Status:** Split into four tracks on 2026-03-30

## Execution Order

**Run in sequence: A → B → C → D.** Each track's winner becomes the next track's baseline.

- A and B are **entry decisions** (before trade)
- C is **loss management** (during trade, reduce losses)
- D is **win extension** (during trade, increase wins)

## Track A: Entry Regime Signals — PASSED

**Prompt:** `rotational-NQ-prompt-entry-signals.md`
**Status:** frozen, P2 PASS

**Scope:** dR2, dSlope — regime transition rate at entry. Skip entries when regime isn't improving.

**Winner:** dR2 <= -0.40 + dSlope <= -2.0. P1 E[R] $53.43 → $63.22 (+18.3%), 64% retention.

## Track B: Fade Confirmation

**Prompt:** `rotational-NQ-prompt-fade-confirmation.md`
**Status:** draft

**Scope:** Close position, range decay, volume shift, consecutive bar direction, directional speed — assessed at entry to confirm the pullback is exhausting before fading.

**Question:** Is this specific pullback done, or does it have more to go?

## Track C: In-Trade Loss Management

**Prompt:** `rotational-NQ-prompt-trade-management-c.md`
**Status:** draft (depends on A + B)

**Scope:** 8 candidates (4 regime + 4 trade-behavior) evaluated mid-trade for early exit, skip add, break-even stop, tighten stop.

**Question:** Once in a trade, is it going badly enough to cut early?

## Track D: Extended Hold

**Prompt:** `rotational-NQ-prompt-extended-hold.md`
**Status:** draft (depends on A + B + C)

**Scope:** At the 10pt reversal point, evaluate whether to hold for a larger target. Three exit rules (conditional delay, stepped trailing, ATR-scaled). Reuses signals from Tracks B and C.

**Question:** This trade reached the reversal target — should we hold for more?

## Shared Data

Steps 0-1 from Track A produced shared output files. Subsequent tracks generate their own tagged cycles from whatever baseline survives.

**Shared output:**
- `lab/output/rotational-NQ-scale-detection/regime-direction-tagged-cycles.csv`
- `lab/output/rotational-NQ-scale-detection/regime-direction-intrade-bars.csv`

## Four Layers

| Layer | Track | Question | Timescale | Focus |
|---|---|---|---|---|
| Regime | A (chop + dR2/dSlope) | Is the market rotational and improving? | Last 3 agg bars | Entry gate |
| Move | B (fade confirmation) | Is this pullback exhausting? | Entry tick + last 2-3 completed bars | Entry gate |
| Defense | C (loss management) | Is this trade going badly? | During the trade | Reduce losses |
| Offense | D (extended hold) | Should we hold past the reversal? | At 10pt reversal point | Increase wins |
