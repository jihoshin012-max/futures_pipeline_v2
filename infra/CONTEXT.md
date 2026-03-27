# Infrastructure

last_reviewed: 2026-03-27 | review_cadence: quarterly

## What This Workspace Is

Live infrastructure that strategies consume. Produces signals, never
trades. Each subsystem has its own directory with its own lifecycle —
independent of the lab/bench/deploy strategy pipeline.

- Strategies reference infra signals via SC study subgraphs
- Strategies never need to know how infra signals are computed
- Infra never reads from lab/, bench/, or deploy/
- Infra reads shared constants from `_config/`

---

## Hard Rules

1. **Infra produces signals, never trades.** No order placement logic.
2. **No strategy-specific code.** Infra is archetype-agnostic.
3. **Each subsystem is self-contained.** Own docs, own lifecycle.
4. **Signal contract is the interface.** Consuming studies reference
   subgraph indices. Changing subgraph layout is a breaking change.

---

## Subsystems

| Subsystem | Directory | Signal Output | Status |
|-----------|-----------|---------------|--------|
| BLB Consolidation Detection | `blb/` | consolidation_prob, rotation_size, breakout_score | Building |

---

## Where to Go

| You Want To... | Go Here |
|----------------|---------|
| **Build/modify BLB consolidation detector** | `blb/` |
| **Understand BLB spec** | `blb/` + root `blbML.md` |
| **Look up instrument constants** | `_config/instruments.md` |
