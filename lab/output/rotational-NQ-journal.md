# Journal — rotational / NQ

Append-only. Records decisions, state changes, and reasoning.
Read this first when resuming work on rotational-NQ.

---

## 2026-03-27 — Initial onboarding

**C++ study:** `lab/rotational-NQ-study.cpp` onboarded.
- Base: ATEAM_ROTATION_V3_V2803 (martingale rotation strategy)
- Fork: ATEAM_ROTATION_V3_LP (LP-1.1) — adds RTH gate + CSV test mode
- CSV test mode produces cycle-level and event-level output for
  parameter sweeps and Monte Carlo analysis
- Study changelog goes back to 2026-03-25

**SpeedRead study:** Relegated to ACSIL workspace (`C:\Projects\sierrachart`).
Not a strategy — it's a general-purpose indicator that produces data.
Pipeline keeps its output as `data/NQ-speedread-calibration.csv`.

**Data onboarded:** 18 files copied from old pipeline, renamed to
`[instrument]-[source]-[role].csv` convention. See `data/README.md`
for full inventory.

**Python pipeline:** Not yet started. No feature engine, simulator,
evaluator, or hypothesis configs exist.

**Current state:** C++ study exists, data is in place. Ready for
either Python pipeline development (01-features) or continued
C++ iteration.
