# Regime, Direction & Trade-Behavior Signals — Index

> **Status:** Split into two prompts on 2026-03-29

This prompt was split into two independent tracks that share Steps 0-1 (instrumentation + tagging) but diverge completely afterward:

## Track A: Entry Signals

**Prompt:** `rotational-NQ-prompt-entry-signals.md`

**Scope:** Signed choppiness, dChop/dt, d²Chop/dt², signed slope — evaluated at entry to gate or bias direction.

**Approach:** Retroactive filter → live sim → P1 → sanity → bench handoff. Uses existing sweep (no fork).

## Track B: In-Trade Management

**Prompt:** `rotational-NQ-prompt-trade-management.md`

**Scope:** 8 candidates (4 regime + 4 trade-behavior) evaluated mid-trade for early exit, skip add, break-even stop, tighten stop.

**Approach:** Loss replay analysis → simulator fork → isolation testing per action → combine winners → P1 → sanity → bench handoff.

## Shared Steps

Steps 0 (instrument simulator) and 1 (compute and tag) produce output files used by both prompts. Run once, both prompts consume the results.

**Shared output:**
- `lab/output/rotational-NQ-scale-detection/regime-direction-tagged-cycles.csv`
- `lab/output/rotational-NQ-scale-detection/regime-direction-intrade-bars.csv`

## Execution Order

Either prompt can be run first. If both succeed, the trade-management prompt's Step 7 tests the combination.
