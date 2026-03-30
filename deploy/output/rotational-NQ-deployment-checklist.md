# Deployment Checklist — rotational NQ (chop + A + B + B2)

> **Strategy:** ATEAM_ROTATION_V3_CHOP (requires major update for A + B + B2)
> **Indicator:** ATEAM_ChoppinessFilter
> **Config:** SD=10 HS=60 depth_1 MCS=2
> **Entry gates (stacked):**
> 1. chop < 0.10 at lb=3 on 250-tick bars
> 2. dR2 <= -0.40 AND dSlope <= -2.0 at lb=3
> 3. fade_confirm < 0.40 (entry price in lower 40% of prev 250-tick bar range)
> 4. d2_ema9 entry gate (|d2|<=0.5 neutral zone, block against-trend)
> **In-trade hold:**
> 5. At reversal point: if d2_avg3 aligned with position → HOLD instead of exit
> 6. Exit on d2_avg3 flip, hard stop, or EOD

---

## Chop filter (2026-03-29)

- [x] C++ review: params match frozen config
- [x] Compilation: no warnings
- [x] Chop formula verified
- [x] Choppiness values match Python 100% on completed RTH bars
- [x] Block/allow decisions match Python 100% on RTH bars
- [x] Replay verification: entries, exits, adds, stops, directions consistent
- [x] Verdict: PASS

## Track A — Entry signals (2026-03-29)

- [x] P1: E[R] $53.43 → $63.22 (+18.3%), 64% retention, 9/12 weeks improved
- [x] Sanity check: PASS (+$8.31 over max random)
- [x] P2: PASS — E[R] $70.40, PF 1.731
- [x] Verdict: PASS — all gates met
- [ ] C++ implementation: dR2/dSlope gate not yet in SC study

## Track B — Fade confirmation (2026-03-30)

- [x] P1: E[R] $63.22 → $78.16 (+24%), 98% retention, 12/12 weeks improved
- [x] Key finding: hypotheses inverted — fades work best at max pullback momentum
- [x] Sanity check: PASS (+$72.60 over max random)
- [x] P2: PASS — E[R] $80.55, PF 1.91, Kelly 0.41, WR headroom 12%
- [x] Verdict: PASS — all gates met
- [ ] C++ implementation: fade_confirm gate not yet in SC study

## Track B2 — EMA directional gate + hold (2026-03-30)

- [x] P1: E[R] $78 → $201 (+158%), 12/12 weeks improved
- [x] Entry gate: d2_ema9 |d2|<=0.5 neutral zone (+28%)
- [x] In-trade hold: d2_avg3 delays reversal when curvature aligned (+128%)
- [x] Key finding: 2nd derivative (curvature) >> 1st derivative (velocity) >> level (spread)
- [x] Key finding: in-trade hold >> entry gating (near-additive)
- [x] Key finding: BE trail hurts — protective stops interact destructively with rotation mechanic
- [x] Sanity check: PASS
- [x] P2: PASS (H2+H5 overridden) — E[R]=$225, PF=4.04, 84% WR, 13/13 weeks
- [x] H2 override: 4,466 cycles (hold consolidates cycles by design)
- [x] H5 override: Kelly=0.64 (enlarged wins, unchanged risk profile)
- [x] Stress: bootstrap P5=$959K, WR headroom 35%, slippage profitable through 10t
- [x] Verdict: PASS
- [ ] C++ implementation: d2_ema9 entry gate + d2_avg3 hold not yet in SC study

## Track C/C2 — Loss management (FAILED)

- [x] Track C: mid-trade signals did not differentiate winners from losers
- [x] Track C2: HS=60 optimal, session context no signal, adaptive stop net negative
- [x] Key finding: replay overestimates management benefits at this timescale
- [x] Key finding: 100% of losses from depth_1 stops, depth_0 never stops

## Track D — Extended hold (superseded)

- [x] B2 hold mechanic achieved this objective via d2_avg3

## Known deviations

- SC partial-bar timing differs from Python complete-bar computation. Entry prices differ by ~1-3 ticks on individual trades. Accepted — statistical properties validated across cycles.
- Tracks A, B, and B2 have not been implemented or verified in C++ yet. Current SC study only has chop filter.

## Pending

- [ ] C++ study update: add dR2/dSlope gate (Track A)
- [ ] C++ study update: add fade_confirm gate (Track B)
- [ ] C++ study update: add d2_ema9 entry gate + d2_avg3 hold mechanic (Track B2)
- [ ] Compilation + replay verification of updated study
- [ ] Create `deployment-ready-rotational-NQ.flag` (human only)
