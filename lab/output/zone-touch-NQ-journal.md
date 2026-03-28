# Journal — zone-touch / NQ

Append-only. Records decisions, state changes, and reasoning.
Read this first when resuming work on zone-touch-NQ.

---

## 2026-03-27 — Initial onboarding

**Study chain tooling onboarded:**
- `zone-touch-NQ-zone-detector.cpp` — SupplyDemandZonesV4, detects and draws supply/demand zones
- `zone-touch-NQ-zone-detector-history.cpp` — batch exports all historical zones (including ephemeral same-bar-break zones)
- `zone-touch-NQ-touch-engine.cpp` — ZoneTouchEngine v4.0, consolidated touch detection + feature computation + scoring + CSV export

**Strategy studies onboarded (all three active, none superseded):**
- `zone-touch-NQ-study-fixed.cpp` — fixed exit version (stop/target in ticks), config: `study-fixed-config.h`
- `zone-touch-NQ-study-zonerel.cpp` — zone-relative exit version (scales with zone width), config: `study-zonerel-config.h`
- `zone-touch-NQ-study-v32.cpp` — unified v3.2 autotrader with A-Eq + B-ZScore dual-mode waterfall, config: `study-v32-config.txt`. Currently frozen, pending replication gate then paper trading. Not yet confirmed as the winner — all three variants remain active.

**Data already in place:**
- `data/NQ-zte-{calibration,holdout}.csv` — ZTE touch events
- `data/NQ-ray-context-{calibration,holdout}.csv` — ray-touch pairs
- `data/NQ-ray-reference-{calibration,holdout}.csv` — RayValidator ground truth

**Python pipeline:** Not yet started. No feature engine, simulator,
evaluator, or hypothesis configs exist for zone-touch.

**Current state:** Full C++ study chain and all three strategy
variants are in place with data. Ready for Python pipeline
development or continued C++ iteration.
