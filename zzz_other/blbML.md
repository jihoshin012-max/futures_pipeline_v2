# BLB Consolidation Detection System

## System Overview

A 3-part pipeline that detects consolidation zones in ES/NQ futures in real-time and outputs a gate signal (`sg_Signal`) for use by an automation study.

**Pipeline flow:**
```
1-tick chart
  → BLB_ConsolidationTickCollector.cpp (exports per-tick order flow to CSV)
  → blb_consolidation_trainer.py (ML analysis, discovers thresholds)
  → BLB_ConsolidationDetector.cpp (live detection using learned thresholds)
```

**Platform:** Sierra Chart (ACSIL C++ studies) + Python ML pipeline  
**Target data feed:** Denali  
**Chart type:** 1-tick  
**Output directory:** `C:\SierraChart\SierraChartInstance_2\Data\`  
**Source directory:** `ACS_Source\`

---

## Part 1 — BLB_ConsolidationTickCollector.cpp

ACSIL C++ study that runs on a 1-tick chart. Streams raw per-tick order flow data to CSV.

### Output File
`C:\SierraChart\SierraChartInstance_2\Data\cons_tick_orderflow.csv`

### CSV Fields (one row per tick)
| Field | Description |
|---|---|
| TradePrice | Last trade price |
| TradeSize | Last trade volume |
| PriceSpeed_pts_sec | Points per second price velocity |
| PriceDelta_5tick | Net price change over 5 ticks |
| PriceDelta_20tick | Net price change over 20 ticks |
| RotationSize_[window] | Rotation size for configurable windows |
| DirReversals_20tick | Direction reversals in last 20 ticks |
| AbsorbLevel | Price level being absorbed (0 = none) |
| AbsorbVolHit | Volume that has hit the absorb level |
| AbsorbOrigSize | Original resting size at absorb level |
| AbsorbCurSize | Current remaining size at absorb level |
| DOMImbalance | Bid-Ask imbalance from DOM |
| TotalBidDepth | Sum of all bid depth |
| TotalAskDepth | Sum of all ask depth |
| Bid1Size | Best bid size |
| Ask1Size | Best ask size |
| Spread | Current bid-ask spread |
| CumDelta | Cumulative delta |
| TimeSinceLast_ms | Milliseconds since previous tick |
| TickIndex | Sequential tick counter |
| DateTime | Timestamp |

### Implementation Requirements
- Must be an ACSIL study (`scsf_` function signature)
- Use `sc.OpenFile()` for CSV writing or manual file I/O with `<fstream>`
- Read DOM via `sc.GetBidMarketDepthEntries()` / `sc.GetAskMarketDepthEntries()`
- Compute PriceSpeed using tick timestamps: `abs(price_change) / time_delta_seconds`
- Track rotation via price direction reversals — flip detection on tick-by-tick price movement
- Absorption detection: monitor a price level being repeatedly hit while resting size depletes
- Write CSV header on first call, append rows on each new tick
- Use `sc.Index` to track tick index
- Guard against writing duplicate rows — only write when `sc.Index` advances

### ACSIL Boilerplate
```cpp
#include "SierraChart.h"
SCDLLName("BLB_ConsolidationTickCollector")

SCSFExport scsf_BLB_ConsolidationTickCollector(SCStudyInterfaceRef sc)
{
    if (sc.SetDefaults)
    {
        sc.GraphName = "BLB Consolidation Tick Collector";
        sc.GraphRegion = 0;
        sc.AutoLoop = 0; // Manual looping for tick-level control
        sc.UpdateAlways = 1;
        return;
    }
    // Implementation here
}
```

---

## Part 2 — blb_consolidation_trainer.py

Offline Python pipeline. Reads the tick CSV, engineers features, auto-labels consolidation, trains a RandomForest, and exports thresholds + diagnostics.

### Dependencies
```
pandas
numpy
scikit-learn
matplotlib
joblib
argparse
```

### CLI Interface
```bash
python blb_consolidation_trainer.py \
    --csv path/to/cons_tick_orderflow.csv \
    --window 50 \
    --label-persist 30 \
    --rot-pctl 30 \
    --speed-pctl 35 \
    --delta-pctl 40
```

| Argument | Default | Description |
|---|---|---|
| `--csv` | required | Path to tick CSV |
| `--window` | 50 | Rolling window size in ticks |
| `--label-persist` | 30 | Consecutive ticks conditions must hold for label=1 |
| `--rot-pctl` | 30 | Rotation percentile cutoff |
| `--speed-pctl` | 35 | Speed percentile cutoff |
| `--delta-pctl` | 40 | Delta balance percentile cutoff |

### Pipeline Steps

#### Step 1 — `load_data(csv_path)`
- Read `cons_tick_orderflow.csv`
- Drop rows with missing/zero `TradePrice` or `TradeSize`
- Deduplicate on `DateTime` (keep last — preserves live DOM updates)
- Sort chronologically
- Return clean DataFrame

#### Step 2 — `engineer_features(df)`
Build 21 signals from raw columns. All normalizations use 200-tick rolling mean unless stated otherwise.

**SPEED GROUP** (how fast price is moving):
| Feature | Derivation |
|---|---|
| `price_speed` | Raw `PriceSpeed_pts_sec` |
| `speed_norm` | `price_speed / rolling_mean(price_speed, 200)` |
| `speed_accel` | `diff(price_speed, 10)` — negative = decelerating |
| `decelerating` | Binary: `speed_accel < 0` |
| `speed_compression` | `PriceDelta_5tick / PriceDelta_20tick` |

**ROTATION / RANGE GROUP** (size of swings):
| Feature | Derivation |
|---|---|
| `rot_a_norm` | `RotationSize_A / rolling_mean(RotationSize_A, 500)` |
| `rot_b_norm` | `RotationSize_B / rolling_mean(RotationSize_B, 500)` |
| `choppy_norm` | `DirReversals_20tick / rolling_mean(DirReversals_20tick, 500)` |
| `tight_rotation` | Binary: small range AND reversals > 8 |

**VOLUME GROUP:**
| Feature | Derivation |
|---|---|
| `trade_size_norm` | `TradeSize / rolling_mean(TradeSize, 200)` |
| `small_trade` | Binary: `trade_size_norm < 0.5` |
| `large_trade` | Binary: `trade_size_norm > 2.0` |

**ABSORPTION GROUP:**
| Feature | Derivation |
|---|---|
| `absorbing` | Binary: `AbsorbLevel > 0` |
| `absorb_ratio` | `AbsorbVolHit / AbsorbOrigSize` |
| `absorb_remain` | `AbsorbCurSize / AbsorbOrigSize` |

**DOM GROUP:**
| Feature | Derivation |
|---|---|
| `dom_imbal_abs` | `abs(DOMImbalance)` |
| `dom_depth_norm` | `(TotalBidDepth + TotalAskDepth) / rolling_mean(total_depth, 200)` |
| `bid_concentration` | `Bid1Size / TotalBidDepth` |
| `ask_concentration` | `Ask1Size / TotalAskDepth` |
| `spread_norm` | `Spread / rolling_median(Spread, 200)` |

**DELTA / FLOW GROUP:**
| Feature | Derivation |
|---|---|
| `delta_vel` | `diff(CumDelta, 10)` — delta velocity |
| `delta_abs_norm` | `abs(delta_vel) / rolling_mean(abs(delta_vel), 100)` |
| `delta_balanced` | Binary: `delta_abs_norm < 0.30` |

**TICK RATE GROUP:**
| Feature | Derivation |
|---|---|
| `slow_tick` | Binary: `TimeSinceLast_ms > 500` |
| `tick_rate_norm` | `TimeSinceLast_ms / rolling_mean(TimeSinceLast_ms, 200)` |

#### Step 3 — `roll_features(df, window=50)`
For each of the 21 features, compute `mean` and `std` over a rolling window of `window` ticks.

Result: ~42 rolled features (e.g. `speed_norm_mean`, `speed_norm_std`). These are the model's input matrix.

Drop rows with NaN from rolling warmup.

#### Step 4 — `make_labels(df, rot_pctl, speed_pctl, delta_pctl, persist)`
Self-supervised auto-labeling. No manual labels needed.

**Conditions for consolidation (label=1):**
| Condition | Threshold |
|---|---|
| Rotation small | `rot_a_norm_mean <= percentile(rot_a_norm_mean, rot_pctl)` |
| Speed slow | `speed_norm_mean <= percentile(speed_norm_mean, speed_pctl)` |
| Delta balanced | `delta_abs_norm_mean <= percentile(delta_abs_norm_mean, delta_pctl)` |

ALL 3 conditions must be true for `persist` consecutive ticks to get `label = 1`.

The persistence requirement prevents labeling brief pauses as consolidation.

Implementation: create a boolean column where all 3 conditions are met, then use a rolling min over `persist` window — if the rolling min is 1, the full window passed.

#### Step 5 — `train_model(X, y)`
| Setting | Value |
|---|---|
| Model | `RandomForestClassifier` |
| Trees (`n_estimators`) | 300 |
| Max depth | 10 |
| Class weight | `balanced` |
| Train/test split | 80/20 time-ordered (NO shuffle — respects sequence) |
| Jobs | -1 (all CPU cores) |

Output: classification report (precision, recall, F1) and confusion matrix printed to console.

#### Step 6 — `save_outputs(model, features, df, thresholds)`
| Output File | Contents |
|---|---|
| `consolidation_why.png` | Top-20 feature importance bar chart (matplotlib) |
| `consolidation_thresholds.csv` | All features ranked by importance, mean values during consolidation vs trending |
| `consolidation_model.pkl` | Trained RandomForest model (joblib) |
| `consolidation_features.pkl` | Feature name list — must match model input order |
| `labeled_data.csv` | All raw ticks with `consolidation_label` column appended |

Also print human-readable console summary: top 10 features with HIGHER/LOWER direction during consolidation vs trending.

---

## Part 3 — BLB_ConsolidationDetector.cpp

ACSIL C++ study that re-implements the ML-discovered thresholds in real-time. Uses a 6-feature simultaneous vote — ALL must pass for `sg_Signal = 1`.

### Persistent State
Use a struct with circular buffers (up to 500 ticks) storing:
- Price, volume, DOM depth changes, reload tracking
- Macro range buffers: high/low per feature bar (200 bars max)
- Running sums for O(1) incremental feature updates
- Cached feature values recomputed every `BarSize` ticks

Store persistent state via `sc.GetPersistentPointer()`.

### The 6 Detection Features

| # | Feature | What It Measures | Consolidation Threshold |
|---|---|---|---|
| 1 | Reload Rate | DOM depth recoveries / drops | >= 0.70 |
| 2 | Macro Range | High-Low price range over N bars | <= 20 pts |
| 3 | Price Efficiency | abs(net move) / total path | <= 0.40 |
| 4 | Delta Balance | abs(buy - sell) / total volume | <= 0.08 |
| 5 | Depth Churn | abs(DOM changes) vs average | Below avg × 1.2 |
| 6 | Reversal | **PLACEHOLDER — always returns true** | Always passes |

### Output Subgraphs
| Subgraph | Purpose |
|---|---|
| `sg_Signal` | Main binary flag — all 6 features pass AND DOM conditions met |
| `sg_SignalNoDOM` | Consolidation without DOM (macro range + efficiency + churn only) |
| `sg_ReloadRate` | Diagnostic: current reload rate value |
| `sg_DeltaBal` | Diagnostic: current delta balance value |
| `sg_MacroRange` | Diagnostic: current macro range value |
| `sg_MacroHigh` | Rolling range upper boundary |
| `sg_MacroLow` | Rolling range lower boundary |

### DOM Optimization
DOM depth queries are expensive. Read DOM only every 10th tick using a cooldown counter (`r_DOMCooldown`). This prevents hammering the API.

### ACSIL Boilerplate
```cpp
#include "SierraChart.h"
SCDLLName("BLB_ConsolidationDetector")

SCSFExport scsf_BLB_ConsolidationDetector(SCStudyInterfaceRef sc)
{
    SCSubgraphRef sg_Signal = sc.Subgraph[0];
    SCSubgraphRef sg_SignalNoDOM = sc.Subgraph[1];
    SCSubgraphRef sg_ReloadRate = sc.Subgraph[2];
    SCSubgraphRef sg_DeltaBal = sc.Subgraph[3];
    SCSubgraphRef sg_MacroRange = sc.Subgraph[4];
    SCSubgraphRef sg_MacroHigh = sc.Subgraph[5];
    SCSubgraphRef sg_MacroLow = sc.Subgraph[6];

    if (sc.SetDefaults)
    {
        sc.GraphName = "BLB Consolidation Detector";
        sc.GraphRegion = 1;
        sc.AutoLoop = 0;
        sc.UpdateAlways = 1;

        sg_Signal.Name = "Consolidation Signal";
        sg_Signal.DrawStyle = DRAWSTYLE_LINE;
        sg_Signal.PrimaryColor = RGB(0, 255, 0);

        sg_SignalNoDOM.Name = "Signal No DOM";
        sg_SignalNoDOM.DrawStyle = DRAWSTYLE_LINE;

        sg_ReloadRate.Name = "Reload Rate";
        sg_ReloadRate.DrawStyle = DRAWSTYLE_LINE;

        sg_DeltaBal.Name = "Delta Balance";
        sg_DeltaBal.DrawStyle = DRAWSTYLE_LINE;

        sg_MacroRange.Name = "Macro Range";
        sg_MacroRange.DrawStyle = DRAWSTYLE_LINE;

        sg_MacroHigh.Name = "Macro High";
        sg_MacroHigh.DrawStyle = DRAWSTYLE_LINE;

        sg_MacroLow.Name = "Macro Low";
        sg_MacroLow.DrawStyle = DRAWSTYLE_LINE;

        return;
    }
    // Implementation here — use sc.GetPersistentPointer() for state
}
```

---

## ML Findings Reference

Training run: 34,200 ES ticks | Live DOM | 2026-03-20

### #1 Driver — Thick Book (DOM Depth Stays)
- 412 resting contracts average
- Churn: 0.159 (low = orders stay put)
- Moving market churn: 0.201 (+26% more pulling)
- **84.4% consistent** — strongest signal across all time windows

### #2 Driver — Absorption (Price Reverses at Edges)
- 65% price reversal rate at range edges
- Top edge: 64% reversal | Bottom edge: 65%
- 108/167 edge tests reversed
- **75% consistent**

### Supporting — Walls (Reload)
- 91% reload within 3 ticks after being hit
- 64% instant refill
- ASK wall depth surges to 232 | BID total depth: 461

### Supporting — No Aggression
- 0.33% aggression ratio
- 1.29 avg lots vs 412 resting contracts
- 88% of trades are 1-lots
- Only 6.1% are 3+ lot trades
- 237ms between ticks vs 104ms in moving market (2.3× slower)

### What Breaks Consolidation
- Reload rate drops (from 91% baseline)
- Depth depletes faster than refill
- Large aggression appears (3+ lots)
- One side pulls their orders

---

## Known Incomplete Items

1. **Reversal feature in ConsolidationDetector.cpp** — scaffolding exists but always returns true. Was intended to detect micro-reversal patterns inside the range.
2. **Edge reversal detection** — code comments show it was being designed but disabled.
3. **Automation hook** — ConsolidationDetector produces `sg_Signal`, but an automation study needs to read it as an input. The two studies need to be connected via Sierra Chart study references.

---

## File Manifest

| File | Location | Purpose |
|---|---|---|
| `BLB_ConsolidationDetector.cpp` | `ACS_Source\` | Live detection study |
| `blb_consolidation_trainer.py` | `ACS_Source\` | ML training pipeline |
| `BLB_ConsolidationTickCollector.cpp` | `ACS_Source\` | Data collection study |
| `cons_tick_orderflow.csv` | `Data\` | Raw tick export |
| `consolidation_thresholds.csv` | `Data\` | ML-discovered thresholds |
| `consolidation_why.png` | `Data\` | Feature importance chart |
| `consolidation_model.pkl` | `Data\` | Trained model |
| `consolidation_features.pkl` | `Data\` | Feature name list |
| `labeled_data.csv` | `Data\` | Ticks with labels |

---

## ACSIL Development Notes

- All studies use `AutoLoop = 0` (manual looping) for tick-level control
- Use `sc.UpdateAlways = 1` to process every tick in real-time
- Persistent state via `sc.GetPersistentPointer()` — allocate on first run, cast on subsequent
- DOM access: `sc.GetBidMarketDepthEntries()` / `sc.GetAskMarketDepthEntries()`
- File I/O: use `<fstream>` or `sc.OpenFile()` — write to `Data\` subdirectory
- Subgraph values set via `sg_Signal[sc.Index] = value`
- Study inputs for cross-study references: `sc.Input[N].SetStudySubgraphValues()`
- Compile target: Sierra Chart's built-in ACSIL compiler (MSVC-compatible C++)
