# Stage 02: Hypothesis Testing

## Inputs

- **Layer 3 (internalize):** `lab/docs/simulation-rules.md`, `_config/period-config.md`
- **Layer 4 (process):** `lab/output/[arch]-[inst]-features-frozen.json`

## Process

Edit `lab/[arch]-[inst]-hypothesis-configs.py`. One structural
parameter change per experiment (stop_ticks, leg_targets,
trail_steps, routing).

Run: `python harness/hypothesis_generator.py --archetype [arch]`

Metric: calibration PF at `experiment_cost_ticks` from `_config/instruments.md`,
minimum `min_trades` from same file.
Keep rule: PF improves by > 0.1 → keep. Else revert.
Budget: 200 experiments per archetype per calibration period.

**Replication gate:** Every hypothesis must pass on both replica
halves (A and B) of the calibration windows. Replica-B failure →
revert (hard mode) or flag as weak (review mode).
Gate mode read from `_config/period-config.md`.

## Outputs

- Append row to `lab/output/[arch]-[inst]-results.tsv`
- Append entry to `lab/output/[arch]-[inst]-journal.md` with reasoning
- When human approves: `lab/output/[arch]-[inst]-hypothesis-frozen.json`
- Promoted hypothesis flows to Stage 03 as Layer 4 input

Human approval = the frozen file exists. If no frozen file, the
output is still draft.

---

## Rotational NQ — Study Map

### Execution: A → B → C → C2 → B2 → D (sequential, each winner becomes next baseline)

```
Parent Study (completed)
│  rotational-NQ-prompt-scale-detection.md
│  Outcome: chop < 0.10 at lb=3 on 250-tick bars
│
├─ Track A: Entry Regime Signals (PASSED)
│  rotational-NQ-prompt-entry-signals.md
│  Outcome: + dR2 <= -0.40, dSlope <= -2.0
│
├─ Track B: Fade Confirmation (PASSED)
│  rotational-NQ-prompt-fade-confirmation.md
│  Outcome: + fade_confirm < 0.40
│
├─ Track C: In-Trade Loss Management (FAILED)
│  rotational-NQ-prompt-trade-management-c.md
│  Mid-trade signals did not differentiate winners from losers in time
│
├─ Track C2: Loss Mitigation — revised approach (FAILED)
│  rotational-NQ-prompt-loss-mitigation-c2.md
│  HS sweep: HS=60 optimal. Session context: no signal. Adaptive stop: net negative.
│  Partial profit: break-even mechanism kills reversals (same root cause as Track C).
│
├─ Track B2: EMA Directional Gate (PASSED + bench PASS)
│  rotational-NQ-prompt-ema-directional-b2.md
│  Entry: d2_ema9 curvature gate (|d2|<=0.5 neutral zone)
│  Hold: d2_avg3 delays reversal exit when curvature aligned
│  Combined P1: E[R]=$201 (+158%), P2: E[R]=$225, PF=4.04, 13/13 weeks
│
└─ Track D: Extended Hold (superseded by B2 hold mechanic)
   rotational-NQ-prompt-extended-hold.md
   B2 hold mechanic achieved the extended hold objective via d2_avg3
```

### Index

`rotational-NQ-prompt-chop-momentum.md` — master index with five-layer summary table

### Current Stacked Entry Gates + Hold (after A + B + B2)

```
Pullback reaches 10pts
  → chop < 0.10?            (parent study)
  → dR2 <= -0.40?           (Track A)
  → dSlope <= -2.0?         (Track A)
  → fade_confirm < 0.40?    (Track B)
  → d2_ema9 entry gate?     (Track B2: |d2|<=0.5 neutral, block against-trend)
  → ENTER
  → at reversal: d2_avg3 aligned? HOLD : EXIT+re-enter  (Track B2 hold)
  → exit on d2_avg3 flip, hard stop, or EOD
```

### Frozen Params

| Variant | File | Status |
|---|---|---|
| Chop only | `lab/output/rotational-NQ-params-frozen.json` | deployed |
| + Entry signals | `lab/output/rotational-NQ-entry-signals-params-frozen.json` | bench PASS |
| + Fade confirm | `lab/output/rotational-NQ-fade-confirm-params-frozen.json` | bench PASS |
| + EMA directional | `lab/output/rotational-NQ-ema-directional-params-frozen.json` | bench PASS |

### Key Findings

1. Track B hypotheses were ALL inverted. Fades work best when pullback has MAXIMUM momentum, not during exhaustion.
2. **Replay analysis overestimates management benefits.** Confirmed across Track C (skip-add, break-even) and Track C2 (partial profit). Break-even stops interact destructively with the reversal mechanic at this timescale. Do NOT develop management rules from replay without live sim verification.
3. 100% of gross losses come from depth_1 HARD_STOPs. depth_0 trades never stop at SD=10 HS=60.
4. HS=60 is globally optimal for the A+B filtered population.
5. **d2_ema9 (EMA9 curvature/acceleration) is the strongest directional signal.** Not ema_spread (level) or d_ema9 (velocity). The second derivative captures trend acceleration which predicts mean-reversion direction. No inversion (unlike Track B).
6. **In-trade d2 monitoring >> entry gating.** Entry gate: +28%. In-trade hold: +128%. Combined: +158%. The hold mechanic lets winners run when curvature stays aligned — trades exit on d2_avg3 flip instead of fixed reversal distance. Near-additive with entry gate.
7. **BE trail hurts.** Break-even trailing on extended holds exits trades that would recover. Same root cause as Track C/C2: protective stops interact destructively with the rotation mechanic.
