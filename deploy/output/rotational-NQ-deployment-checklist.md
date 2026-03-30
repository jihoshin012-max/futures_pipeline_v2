# Deployment Checklist — rotational NQ chop variant

> **Strategy:** ATEAM_ROTATION_V3_CHOP
> **Indicator:** ATEAM_ChoppinessFilter
> **Config:** SD=10 HS=60 depth_1 MCS=2 + choppiness < 0.10 lb=3 on 250-tick bars
> **Verdict:** PASS (2026-03-29)

---

- [x] C++ review: params match frozen config (SD=10, HS=60, depth_1, MCS=2, chop<0.10, lb=3)
- [x] Compilation: no warnings (both strategy and indicator DLLs)
- [x] Chop filter formula verified: `abs(close[i] - close[i-lb+1]) / summed_range`
- [x] Choppiness values match Python 100% on completed RTH bars
- [x] Block/allow decisions match Python 100% on RTH bars
- [x] Replay verification: entries, exits, adds, stops, directions all consistent with strategy logic
- [x] CSV log confirms CHOP_BLOCKED events have chop >= 0.10, SEED events have chop < 0.10
- [x] Audit entries logged
- [x] Verdict: PASS — all hard and soft gates met

**Known deviation:** SC partial-bar chop timing differs from Python complete-bar chop. Entry prices differ by ~1-3 ticks on individual trades. Accepted — statistical properties validated across 10K+ cycles.

**Pending:**
- [ ] Create `deployment-ready-rotational-NQ.flag` (human only)
