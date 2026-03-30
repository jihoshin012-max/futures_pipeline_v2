# Cross-Language Verification Report

> **Archetype:** rotational
> **Instrument:** NQ
> **Variant:** chop (SD=10 HS=60 depth_1 MCS=2 + choppiness < 0.10 lb=3)
> **Date:** 2026-03-29
> **Status:** PASS

---

## Implementations Compared

| | Python | C++ (test mode) | C++ (live mode) |
|---|---|---|---|
| **File** | `rotational-NQ-scale-detection-engine.py` + `rotational-NQ-scale-detection-sweep.py` | `rotational-NQ-study-chop.cpp` (RunTestMode) | `rotational-NQ-study-chop.cpp` (live) + `rotational-NQ-scale-detection-chop.cpp` (indicator) |
| **Data source** | `NQ-1tick-calibration.csv` (P1, 1 day slice) | Same CSV via inline reader | SC native 250-tick chart + 1-tick chart |
| **Aggregation** | `_aggregate_bars()` — pre-computed, mapped to ticks via `tick_to_agg` | Two-pass: pass 1 aggregates + computes choppiness, pass 2 simulates using `tickChop[i]` | SC native 250-tick bar construction; ChoppinessFilter study reads completed bars |

## Calibration Window

- **Date:** 2025-09-22 (single RTH session)
- **Data file:** `NQ_calibration_1day.csv` (334,682 ticks)
- **Params:** SD=10, HS=60, depth_1, MCS=2, chop < 0.10, lb=3, 250-tick bars

## Test Mode Results — PASS

| Metric | Python | C++ (test mode) | Match |
|---|---|---|---|
| Total cycles | 34 | 34 | EXACT |
| Total PnL ticks | 241 | 241 | EXACT |
| Per-cycle PnL | see below | see below | EXACT |

All 34 cycles match on: seed_dt, direction, seed_price, exit_dt, exit_type, depth, pnl_ticks.

Verification tool: `lab/output/rotational-NQ-scale-detection/diff-calibration.py`

### Bugs Found and Fixed

**Bug 1: Choppiness formula off-by-one (both files)**
- C++ used `close[i - lookback]` for net_move
- Python uses `close[i - lookback + 1]`
- C++ spanned one extra bar, producing different choppiness values
- Fixed in both `rotational-NQ-study-chop.cpp` and `rotational-NQ-scale-detection-chop.cpp`

**Bug 2: Timing mismatch (test mode only)**
- C++ single-pass inline aggregation updated choppiness only when a 250-tick bar completed
- Ticks within incomplete bars used the previous bar's choppiness
- Python pre-computes all bars then maps each tick to its own bar's choppiness
- Fixed by replacing single-pass with two-pass approach: pass 1 aggregates + computes, pass 2 simulates

### Choppiness Verification

Confirmed all 34 filtered entries have choppiness < 0.10 at their actual seed tick by chaining through `watch_bars` + `bars_held` to reconstruct exact tick positions (avoids timestamp ambiguity from multiple ticks sharing the same second).

Reference files:
- Python cycles: `calibration-chop-filtered-python.csv` (34 cycles)
- Python baseline: `calibration-baseline-python.csv` (49 cycles)
- Python choppiness: `calibration-choppiness-250tick-python.csv` (1,339 bars)
- C++ output: `ATEAM_LP_TEST_cycles.csv` (34 cycles, post-fix)

## Live Mode Results — PASS

**Root cause of initial mismatch:** Python `_aggregate_bars()` reset tick counting on date change (midnight). SC counts continuously within the session (18:00–17:00). This shifted all bar boundaries after midnight, producing different choppiness values.

**Fix applied:** Removed date-change reset from Python's `_aggregate_bars()`. Reverted C++ strategy to read from external ChoppinessFilter study on 250-tick chart.

**Replay verification:**
- Choppiness values: 100% match across all RTH bars (identical to 6 decimal places)
- Block/allow decisions: 100% match across all RTH bars
- Trade-level prices differ slightly due to bar resolution (250-tick bar close vs tick-precise entry) — expected and validated by slippage stress test

**P2 re-run with SC-aligned aggregation:** 10,892 cycles, 82% WR, 18% SR, E[R]=$55.28 (original: $54.86). Signal intact.

**P2 stress test:** PASS — all 9 tests consistent with P1. See journal.

## Handoff Status

- **Test mode:** PASS
- **Live mode:** PASS
- **P2 holdout:** PASS (re-run with SC-aligned aggregation)
- **P2 stress test:** PASS
- **Ready for bench handoff**
