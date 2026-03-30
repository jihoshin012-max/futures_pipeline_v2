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
either Python pipeline development (features) or continued
C++ iteration.

---

## 2026-03-28 — Rangefade variant onboarded

**New variant:** `lab/rotational-NQ-study-rangefade.cpp`
- Bollinger-style band-fading rotation: rolling mean ± σ bands,
  buy at inner bottom, sell at inner top, outer bands as stops
- Differs from base study (StepDist/price-action) by using
  statistical bands that adapt to volatility
- Simpler position management — no multi-level doubling ladder.
  Optional martingale scales base qty geometrically after
  consecutive stops.
- Exits via SC attached orders, not custom state machine
- Includes time filter, max daily loss shutoff, daily state reset
- Renamed from `RangeFadeRotation.cpp` to pipeline naming convention

---

### 2026-03-29 — Study Build (strategy)

- Intent: compile existing
- Study: `rotational-NQ-study-chop.cpp`
- SC name: ATEAM_ROTATION_V3_CHOP
- Params source: `lab/output/rotational-NQ-params-frozen.json` (SD=10, HS=60, depth_1, MCS=2, chop<0.10 lb=3)
- Compile: PASS (0 warnings)
- Forked from LP-1.1, adds choppiness entry gate (Inputs 16-18) alongside SpeedRead (Inputs 8-11)
- Inline choppiness computation in CSV test mode for calibration
- DLL: `studies/compiled/ATEAM_ROTATION_V3_CHOP.dll`
