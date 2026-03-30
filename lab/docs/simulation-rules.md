# Simulation Rules

<!--
Layer 3 reference. Loaded by BUILD agents during hypothesis testing
and parameter optimization. This is the "how strategies work" reference.
-->

## Rotational Archetype (NQ)

### Entry Mechanics

**Seed detection:** Watch phase tracks high/low of recent prices. When pull-from-high
or pull-from-low exceeds StepDist, a seed entry fires in the pullback direction.
If both pullbacks exceed StepDist simultaneously, the larger pullback wins.

**Fade filter:** Tracks consecutive same-direction entries. If MaxFades > 0, blocks
the Nth consecutive entry in the same direction.

**Choppiness filter (variant: chop):** Reads choppiness ratio from a 250-tick bar study.
Blocks entry when choppiness >= threshold (default 0.10). Choppiness = |net_move| / summed_range
over lookback bars (default 3). Low choppiness = rotational = trade. High = trending = skip.

**Entry time:** All features must be computable at the entry bar. The choppiness value
is from the last completed 250-tick bar (forward-filled to tick resolution).

### Exit Mechanics

**Reversal:** When price moves StepDist in favor of the position, flatten and enter
opposite direction (subject to fade filter and choppiness filter).

**Hard stop:** When unrealized loss exceeds HardStop ticks, flatten immediately.
Reset to watch phase.

**EOD flatten:** If RTH gate enabled, forced flatten at 15:49:50 ET. No new entries
outside 09:30:00 - 15:49:50.

**Martingale add:** When price moves StepDist against the position, add contracts
at the next doubling level (1, 2, 4, 8...) up to MaxContractSize. After max level
reached, level resets to 0 (next add is base size again).

### Cost Model

- Commission: $3.50 per round-turn per mini contract
- Tick value: $5.00 per tick (NQ mini, tick size 0.25)
- Cost_ticks from instruments.md: read, not hardcoded

### Trail Mechanics

Not used in the rotation strategy. Exit is by reversal, hard stop, or EOD only.

### Session Boundaries

- RTH: 09:30:00 - 15:49:50 ET
- Session open: reset watch state, reset fade counts
- EOD: forced flatten at 15:49:50 (10 seconds before 15:50)
- No overnight carry

### Frozen Parameters (chop variant)

| Parameter | Value | Source |
|-----------|-------|--------|
| StepDist | 10.0 pts | P1+P2 cross-period validation |
| HardStop | 60 ticks (15.0 pts) | Best HS ratio at SD=10 (1.5x) |
| MaxLevels | 1 | Depth 1 (one martingale add) |
| MaxContractSize | 2 | 1 initial + 1 add |
| MaxFades | 0 | Unlimited (not used) |
| Choppiness threshold | 0.10 | P1 feature analysis + P2 validation |
| Choppiness lookback | 3 bars | Lookback sensitivity test (lb=3 best) |
| Choppiness bar size | 250 ticks | Matches live SC chart timeframe |

## Zone Touch Archetype (NQ)

<!-- To be populated when zone touch study is built -->
