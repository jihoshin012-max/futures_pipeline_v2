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

---

## 2026-03-28 — Prompt 0 Baseline

Calibration: 2025-09-21 to 2025-12-14


### Key Numbers

- Median R:R at MFE: 1.12
- Win rate at 1R: 53.5%
- Win rate at 2R: 30.7%
- Break rate: 22.5%
- Comeback rate (≥20pt): 52.8%
- Median time-to-MFE: 27.8min

### Review Gate Answers

1. Raw edge? -> (check median R:R > 1.0 at any sweep)
2. Width breakpoint? -> (see C4 width vs R:R)
3. Ray improvement? -> (see C4 ray impact)
4. Time cap sufficient? -> 100.0% resolved by 4hr
5. Resolved vs ZTE seq? -> (see C5)
6. Tick cost vs 3t? -> (see C4 cost)
7. Comeback rate? -> 52.8% after ≥20pt MFE

Full results: `lab/output/zone-touch-NQ-baseline-summary.md`
Raw data: `lab/output/zone-touch-NQ-baseline-raw.csv`

