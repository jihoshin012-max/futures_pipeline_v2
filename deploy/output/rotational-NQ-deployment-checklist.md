# Deployment Checklist — rotational NQ (chop + entry signals + fade confirm)

> **Strategy:** ATEAM_ROTATION_V3_CHOP (requires update for entry signals + fade confirm)
> **Indicator:** ATEAM_ChoppinessFilter
> **Config:** SD=10 HS=60 depth_1 MCS=2
> **Entry gates (stacked):**
> 1. chop < 0.10 at lb=3 on 250-tick bars
> 2. dR2 <= -0.40 AND dSlope <= -2.0 at lb=3
> 3. fade_confirm < 0.40 (entry price in lower 40% of prev 250-tick bar range)

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

## Known deviations

- SC partial-bar chop timing differs from Python complete-bar chop. Entry prices differ by ~1-3 ticks on individual trades. Accepted — statistical properties validated across 10K+ cycles.
- dR2/dSlope and fade_confirm have not been implemented or verified in C++ yet.

## Pending

- [ ] C++ study update: add dR2/dSlope gate (Track A)
- [ ] C++ study update: add fade_confirm gate (Track B)
- [ ] Compilation + replay verification of updated study
- [ ] Create `deployment-ready-rotational-NQ.flag` (human only)
