# BLB Live Consolidation Detection System — Build Plan

## Context

Ji needs a real-time consolidation detection system for ES/NQ futures to feed a rotation martingale strategy. The system must detect consolidation zones, measure rotation size, and signal breakout imminence — all in real-time via shared memory IPC between Sierra Chart (C++) and Python (ML).

No data exists yet. The tick collector must be built and run first.

This system lives in `infra/blb/` — it's infrastructure that strategies consume, not an archetype that flows through lab/bench/deploy.

---

## Phase 0 — Scaffold [DONE]

Create the infrastructure workspace and define the IPC contract.

**Files:**
- `infra/CONTEXT.md` — workspace rules (infra produces signals, never trades)
- `infra/blb/shared_memory_spec.md` — IPC contract (ring buffer layout, signal struct)
- `infra/blb/requirements.txt` — Python deps (pandas, numpy, scikit-learn, matplotlib, joblib)
- Update `CLAUDE.md` folder structure to include `infra/`
- Update `CONTEXT.md` router to add BLB tasks

**Signal struct (not binary — rotation martingale needs these):**
- `consolidation_prob` (float, 0.0-1.0)
- `rotation_size_mean` (float, current rotation size)
- `breakout_score` (float, 0.0-1.0)
- `tick_index` (uint64, staleness detection)

**Self-checks:**
- [ ] All files in manifest exist and are non-empty
- [ ] `shared_memory_spec.md` defines both regions (BLB_TickRing + BLB_Signal) with byte offsets
- [ ] `CLAUDE.md` folder structure includes `infra/`
- [ ] `CONTEXT.md` router includes BLB task row

---

## Phase 1 — Tick Collector (CSV only)

Build `infra/blb/BLB_ConsolidationTickCollector.cpp`. ACSIL study on 1-tick chart that streams 20+ fields per tick to CSV.

**Key patterns (from existing codebase):**
- `sc.GetPersistentPointer()` with magic number for state
- `fopen/fprintf` for CSV (never `sc.OpenFile`)
- DOM via `sc.GetBidMarketDepthEntryAtLevel()` with 10-tick cooldown
- `AutoLoop = 0`, `UpdateAlways = 1`
- Duplicate guard: only write when `sc.Index > LastProcessedIndex`

**Critical fields for rotation martingale:**
- RotationSize (multiple windows) — feeds rotation sizing
- PriceSpeed / PriceDelta — feeds breakout detection
- DOM depth + absorption — feeds consolidation confidence

**Self-checks (before compile):**
- [ ] CSV header row matches exactly the 20+ fields defined in blbML.md lines 31-53
- [ ] `AutoLoop = 0` and `UpdateAlways = 1` are set in SetDefaults
- [ ] Duplicate guard present: only writes when `sc.Index > LastProcessedIndex`
- [ ] DOM cooldown counter present (every Nth tick, not every tick)
- [ ] Absorption tracking: detects level, tracks volume hit, tracks depletion, clears on break
- [ ] Rotation tracking: detects direction flip, records size for multiple windows
- [ ] PriceSpeed computed as `abs(price_change) / time_delta_seconds`
- [ ] PriceDelta_5tick and PriceDelta_20tick use circular buffer, not full array scan
- [ ] File path uses `sc.DataFilesFolder()`, not hardcoded path
- [ ] No `sc.OpenFile()` usage (uses fopen/fprintf per known issues)

**Self-checks (after compile — user runs these):**
- [ ] Compiles with 0 errors, 0 warnings
- [ ] CSV file appears at expected path after loading on 1-tick chart
- [ ] CSV has correct column count (open in spreadsheet, verify headers)
- [ ] TickIndex column increments monotonically (no gaps, no duplicates)
- [ ] TradePrice matches visible chart price at same timestamp
- [ ] TimeSinceLast_ms values are reasonable (50-500ms during RTH, longer during ETH)
- [ ] DOM columns are non-zero when market is open (Bid1Size, Ask1Size, TotalBidDepth)
- [ ] DOMImbalance is not all zeros (confirms DOM reads are working)
- [ ] RotationSize columns show variation (not stuck at 0)
- [ ] File size grows steadily (not stuck, not writing duplicates)
- [ ] Run for 30+ minutes minimum — spot check first 100 rows and last 100 rows

**[USER ACTION] Collect at least one full RTH session before Phase 2.**

---

## Phase 2 — ML Training

Build `infra/blb/blb_features.py` and `infra/blb/blb_consolidation_trainer.py`.

### What the ML already has to work with

The blbML.md spec defines everything the ML needs — the data fields, the feature engineering, the labeling method, and the model config. The prior training run (34,200 ES ticks, 2026-03-20) produced findings that serve as **benchmarks** the new model should either confirm or challenge:

**Raw data (20 CSV fields per tick — from Part 1):**
These fields already capture the four components directly:

| Component | CSV Fields That Capture It |
|---|---|
| Walls (reload) | Bid1Size, Ask1Size, TotalBidDepth, TotalAskDepth |
| Thick Book (persistence) | TotalBidDepth, TotalAskDepth, DOMImbalance |
| Absorption (edge reversal) | AbsorbLevel, AbsorbVolHit, AbsorbOrigSize, AbsorbCurSize |
| No Aggression (passive flow) | TradeSize, TimeSinceLast_ms |

**Engineered features (25 signals — from Part 2, Step 2, blbML.md lines 130-184):**

Note: the spec text says "21 signals" but the actual feature list totals 25. We use the actual list, not the stated count.

These are specific measurements grouped into 7 categories. Each maps to one or more of the four components:

| Group | Count | Features | Maps To |
|---|---|---|---|
| Speed | 5 | price_speed, speed_norm, speed_accel, decelerating, speed_compression | No Aggression (slow tape) |
| Rotation | 4 | rot_a_norm, rot_b_norm, choppy_norm, tight_rotation | Range structure |
| Volume | 3 | trade_size_norm, small_trade, large_trade | No Aggression (small trades) |
| Absorption | 3 | absorbing, absorb_ratio, absorb_remain | Absorption |
| DOM | 5 | dom_imbal_abs, dom_depth_norm, bid_concentration, ask_concentration, spread_norm | Thick Book + Walls |
| Delta/Flow | 3 | delta_vel, delta_abs_norm, delta_balanced | No Aggression (balanced flow) |
| Tick Rate | 2 | slow_tick, tick_rate_norm | No Aggression (slow tick rate) |
| **Total** | **25** | | |

**Gap features — added to close audit gaps (not in original spec):**

The original C++ detector (blbML.md Part 3) used two signals that have no direct equivalent in the 25 features above. Adding them as features 26-28:

| # | Feature | Derivation | Maps To |
|---|---|---|---|
| 26 | `reload_rate` | DOM depth recoveries within 3 ticks / DOM depth drops, over rolling window | Walls (reload) — benchmark: 91% during consolidation |
| 27 | `depth_churn` | abs(tick-over-tick total DOM depth change) / total depth, rolling mean | Thick Book (persistence) — benchmark: 0.159 during consolidation |
| 28 | `edge_reversal_rate` | Price reversals within N ticks of touching rolling high/low / edge touches | Absorption (edge reversal) — benchmark: 65% during consolidation |

These three features directly measure the components that the prior findings identified as #1 driver (thick book via churn), supporting (walls via reload), and #2 driver (absorption via edge reversal).

**Total: 28 features → ~56 rolled features (mean + std over 50-tick window).**

**Rolled features (~56 — from Step 3):**
Each of the 28 features gets a rolling mean and std over a 50-tick window. These are the model's actual inputs.

**Auto-labeling (from Step 4):**
Self-supervised — no manual labels needed. Consolidation = small rotation + slow speed + balanced delta, all holding for 30+ consecutive ticks. The persistence requirement prevents labeling brief pauses.

**Model config (from Step 5):**
RandomForest, 300 trees, max depth 10, balanced class weights, 80/20 time-ordered split (no shuffle).

### How the ML validates the findings

After training, the model produces:
- `consolidation_why.png` — feature importance chart showing which of the ~56 features the model found most predictive
- `consolidation_thresholds.csv` — mean values during consolidation vs trending for each feature

**If the model agrees with the prior findings:**
- DOM depth features (thick book) rank highest in importance
- Absorption features rank second
- Speed/tick rate features (no aggression) rank as supporting
- The thresholds align with the known benchmarks (churn ~0.159, reversal ~65%, etc.)

**If the model disagrees:**
- Different features rank high → the data may have different characteristics than the original 34,200-tick sample
- This is valuable information, not a failure — it means the model found something the original study didn't

### What we build

**blb_features.py** — shared feature engineering (used by trainer and live scorer):
- Implements all 28 features (25 from blbML.md lines 130-184 + 3 gap features above)
- Rolling window computation (mean + std over 50 ticks) → ~56 model inputs
- Exact feature names and derivations from spec

**blb_consolidation_trainer.py** — offline pipeline following blbML.md Part 2 exactly:
- Step 1: load_data (clean CSV)
- Step 2: engineer_features (28 signals via blb_features.py)
- Step 3: roll_features (50-tick window → ~56 inputs)
- Step 4: make_labels (auto-label via percentile thresholds + persistence)
- Step 5: train_model (RandomForest per spec)
- Step 6: save_outputs (model.pkl, features.pkl, thresholds.csv, why.png, labeled_data.csv)

**CLI interface** (from blbML.md lines 102-119):
```
python blb_consolidation_trainer.py \
    --csv path/to/cons_tick_orderflow.csv \
    --window 50 \
    --label-persist 30 \
    --rot-pctl 30 \
    --speed-pctl 35 \
    --delta-pctl 40
```
All args have defaults from spec. `--csv` is the only required arg.

**Enhancement over original spec:** 3-class labeling instead of binary:
- Class 0: trending
- Class 1: consolidation (per spec's auto-labeling)
- Class 2: breakout-imminent (the N ticks before a consolidation→trending transition)

This gives the model `predict_proba` output for both consolidation_prob (class 1) and breakout_score (class 2).

### Self-checks

**blb_features.py:**
- [ ] All 25 features from blbML.md (lines 133-184) have matching functions
- [ ] 3 gap features implemented: reload_rate, depth_churn, edge_reversal_rate
- [ ] Total feature count = 28
- [ ] Feature names match spec exactly (speed_norm, rot_a_norm, delta_balanced, etc.)
- [ ] Rolling normalizations use correct window sizes (200-tick for speed/volume, 500-tick for rotation)
- [ ] No NaN propagation: rolling warmup rows are handled (dropped or filled)
- [ ] Binary features (decelerating, tight_rotation, small_trade, etc.) output 0 or 1 only
- [ ] Feature count after rolling = ~56 (28 features × 2 stats each)

**blb_consolidation_trainer.py:**
- [ ] CLI args work: --csv, --window (default 50), --label-persist (default 30), --rot-pctl (default 30), --speed-pctl (default 35), --delta-pctl (default 40)
- [ ] `--csv` is required; all others optional with spec defaults
- [ ] n_jobs=-1 (use all CPU cores)
- [ ] Train/test split is time-ordered (NO shuffle) — `shuffle=False` in train_test_split
- [ ] Class weights set to `balanced`
- [ ] Persistence labeling: rolling min window applied (not just single-tick threshold)
- [ ] Label distribution printed: class 0, 1, 2 counts should all be non-trivial (>5% each)
- [ ] Classification report printed: precision, recall, F1 per class
- [ ] F1 for consolidation class (1) > 0.60 — if below, flag for investigation
- [ ] Feature importance chart saved and reviewed — compare against prior findings:
  - DOM/depth features (incl. depth_churn) should rank high (thick book was #1 driver at 84.4% consistency)
  - Absorption features (incl. edge_reversal_rate) should rank high (#2 driver at 75% consistency)
  - reload_rate should rank as supporting (91% reload was a supporting finding)
  - If they don't, document what ranked higher and why
- [ ] `consolidation_features.pkl` feature count matches model's `n_features_in_` (should be ~56)
- [ ] `consolidation_model.pkl` loads successfully with `joblib.load()` and predicts on a sample row
- [ ] Feature hash saved alongside model (for scorer verification)

---

## Phase 3 — Shared Memory IPC

Add shared memory write to the tick collector. Build Python IPC wrapper.

**Files:**
- Modify `BLB_ConsolidationTickCollector.cpp` — add ring buffer write alongside CSV
- `infra/blb/shared_memory.py` — Python wrapper (TickRingReader, SignalWriter)
- `infra/blb/test_shm_read.py` — proof-of-concept test

**IPC layout:**
- `BLB_TickRing`: 1024-slot ring buffer, atomic write_index, C++ writes / Python reads
- `BLB_Signal`: single struct, Python writes / C++ reads

**Risk:** "Global\\" prefix may need admin. Fallback: "Local\\" prefix or file-backed mmap.

**Self-checks (C++ side):**
- [ ] `CreateFileMappingA` called with correct size matching `shared_memory_spec.md`
- [ ] `MapViewOfFile` returns non-NULL (log error to SC message log if NULL)
- [ ] Memory fence before `InterlockedIncrement64` on write_index
- [ ] Handles cleaned up on `sc.LastCallToFunction` (UnmapViewOfFile + CloseHandle)
- [ ] CSV output still works identically (shared memory is additive, not a replacement)
- [ ] Compile succeeds with shared memory additions

**Self-checks (Python side — shared_memory.py):**
- [ ] `TickRingReader` opens named shared memory and reads write_index
- [ ] Slot struct size matches spec (184 bytes)
- [ ] Field unpacking order matches C++ write order exactly
- [ ] Falling-behind warning triggers when `write_index - read_index > 900`
- [ ] Reset logic triggers when `write_index - read_index > 1024`

**Self-checks (test_shm_read.py — proof-of-concept):**
- [ ] Connects to `BLB_TickRing` successfully (prints "Connected")
- [ ] Reads and prints latest 5 ticks with all fields
- [ ] TradePrice from shared memory matches last 5 rows of CSV file (±0.01)
- [ ] TickIndex from shared memory matches CSV TickIndex column
- [ ] If shared memory doesn't exist, prints clear error (not a crash)

---

## Phase 4 — Live Scorer

Build `infra/blb/blb_live_scorer.py` and `infra/blb/start_scorer.bat`.

**Architecture:** Single-threaded poll loop (5ms sleep), reads ticks from ring buffer, engineers features (reusing blb_features.py), runs model.predict_proba(), writes signal to BLB_Signal.

**Key details:**
- Warm-up: needs ~500 ticks before scoring is valid (writes neutral signal during warm-up)
- Incremental feature computation (don't recompute full window each tick)
- Feature hash verification at startup (ensures scorer matches trainer)
- Logs PID to file for liveness checking

**Self-checks (startup):**
- [ ] Loads model.pkl successfully — prints model type and n_features_in_
- [ ] Loads features.pkl — prints feature count
- [ ] Feature hash from features.pkl matches hash stored during training
- [ ] Connects to BLB_TickRing — prints "Connected to tick ring"
- [ ] Connects to BLB_Signal — prints "Connected to signal block"
- [ ] If either shared memory region missing, prints clear error and exits (not crash)

**Self-checks (warm-up):**
- [ ] Prints warm-up progress every 100 ticks ("Warm-up: 100/500 ticks...")
- [ ] During warm-up, writes `ready = 0` to signal block
- [ ] During warm-up, `consolidation_prob = 0.5` (neutral, not 0 or 1)

**Self-checks (scoring):**
- [ ] Prints "Scoring active" when warm-up completes
- [ ] Sets `ready = 1` in signal block
- [ ] consolidation_prob is always in [0.0, 1.0] range
- [ ] breakout_score is always in [0.0, 1.0] range
- [ ] rotation_size_mean is always >= 0
- [ ] tick_index in signal block matches latest processed tick
- [ ] Logs scoring latency every 1000 ticks (time from tick read to signal write)
- [ ] Warns if latency exceeds 50ms
- [ ] Warns if falling behind ring buffer (ticks accumulating faster than scoring)

**Self-checks (resilience):**
- [ ] If ring buffer has no new ticks for 5 seconds, logs warning (market may be closed)
- [ ] If model.predict raises exception, logs error and writes last-known-good signal (not crash)
- [ ] Ctrl+C cleanly shuts down (prints "Scorer stopped", unmaps shared memory)

---

## Phase 5 — Signal Reader Study

Build `infra/blb/BLB_ConsolidationDetector.cpp`. Simplified ACSIL study — only reads from shared memory and exposes subgraphs.

**Subgraphs:**
- sg_Signal (Subgraph[0]): consolidation_prob
- sg_RotationSize (Subgraph[1]): rotation_size_mean
- sg_BreakoutScore (Subgraph[2]): breakout_score
- sg_Stale (Subgraph[3]): 1.0 if Python hasn't updated in >500ms

**Self-checks (before compile):**
- [ ] Opens `BLB_Signal` shared memory (logs error if not found, doesn't crash)
- [ ] Reads signal struct with correct field offsets matching `shared_memory_spec.md`
- [ ] Staleness check: compares signal tick_index against sc.Index
- [ ] Time staleness: compares signal timestamp_ms against current time
- [ ] When stale: sg_Stale = 1.0, other subgraphs hold last known values (not zeroed)
- [ ] Handles cleaned up on `sc.LastCallToFunction`

**Self-checks (after compile — user runs these):**
- [ ] Compiles with 0 errors, 0 warnings
- [ ] Loads on same chart as collector without SC crash
- [ ] With scorer running: sg_Signal shows values between 0.0-1.0 in Values window
- [ ] With scorer stopped: sg_Stale goes to 1.0 within a few seconds
- [ ] With scorer restarted: sg_Stale returns to 0.0 after warm-up
- [ ] Subgraph values update on each tick (not frozen)
- [ ] Visual sanity: during obvious consolidation on chart, sg_Signal should be > 0.5

---

## Future Phases (after system is operational)

### Phase 6 — ML Strategy Learning

Requires live-collected data with the full signal pipeline running. Historical data won't work because DOM data is only available in real-time from the Denali feed.

The ML uses the rich signal output (consolidation_prob, rotation_size, breakout_score) + a Python rotation simulator to learn:
- Dynamic StepDist from rotation size
- When to trade vs skip based on consolidation quality
- When to reduce exposure based on breakout imminence
- Optimal parameter settings for profitability and risk management

**Prerequisites:**
- Phases 1-5 operational and producing live signals
- Python rotation simulator calibrated against C++ (`lab/rotational-NQ-study.cpp`)
- Sufficient live signal data collected (weeks/months, not hours)

**Reference files:**
- `lab/rotational-NQ-study.cpp` (ATEAM_ROTATION_V3_LP) — the C++ autotrader
- `lab/rotational-NQ-speedread.cpp` (SpeedRead_V2) — earlier experiment, onboarded for reference only

### Phase 7 — Deploy

Once the ML has learned optimal dynamic settings:
- Option A: Update C++ study with ML-learned parameters, execute from C++
- Option B: Execute directly from Python if more efficient
- If using both, calibrate Python and C++ to ensure they behave identically

---

## Build Order & Dependencies

```
Phase 0 (scaffold)           → no deps           [DONE]
Phase 1 (collector CSV)      → Phase 0
   [DATA GAP — user collects 1+ sessions]
Phase 2 (ML training)        → collected data
Phase 3 (shared memory)      → Phase 1
Phase 4 (scorer)             → Phase 2 + Phase 3
Phase 5 (signal reader)      → Phase 3
   [SYSTEM OPERATIONAL — producing live signals]
Phase 6 (ML strategy learning) → live signal data (future)
Phase 7 (deploy)               → Phase 6 (future)
```

Phase 3 is independent of Phase 2 — can run in parallel.
Phases 4 and 5 can run in parallel (share only the SHM spec from Phase 0).
Phases 6 and 7 require live-collected data (DOM not available in historical) — future work after system is operational.

---

## File Manifest

| File | Phase | Purpose |
|------|-------|---------|
| `infra/CONTEXT.md` | 0 | Infrastructure workspace context |
| `infra/blb/shared_memory_spec.md` | 0 | IPC contract |
| `infra/blb/requirements.txt` | 0 | Python deps |
| `infra/blb/BLB_ConsolidationTickCollector.cpp` | 1, 3 | Tick data + SHM writer |
| `infra/blb/blb_features.py` | 2 | Feature engineering (25 from blbML.md spec + 3 gap features = 28 total) |
| `infra/blb/blb_consolidation_trainer.py` | 2 | ML pipeline (train, validate against prior findings, export model) |
| `infra/blb/shared_memory.py` | 3 | Python SHM wrapper |
| `infra/blb/test_shm_read.py` | 3 | SHM proof-of-concept |
| `infra/blb/blb_live_scorer.py` | 4 | Always-on scorer |
| `infra/blb/start_scorer.bat` | 4 | Launcher |
| `infra/blb/BLB_ConsolidationDetector.cpp` | 5 | Signal reader study |

---

## Design Considerations

### Scale problem at tick level
Consolidation ranges are tiny relative to surrounding moves. On a 1-tick chart, consolidation zones visually collapse to a flat line. This means:
- **Price-based features alone won't catch it** — the range is noise at chart scale
- **DOM features are the primary detection layer** — thick book, reload rate, absorption reveal structure *within* the tiny range
- **All price/rotation features must be self-normalizing** — the spec uses rolling mean denominators, but the normalization window size is critical. A small rotation means different things depending on recent context.
- **DOM sampling rate (10-tick cooldown) may need tuning** — if DOM is the primary signal, more frequent reads may be worth the SC performance cost. Test after Phase 1.

---

## Risks & Mitigations

### Data Collection (Phase 1)

| Risk | Impact | Mitigation |
|------|--------|------------|
| DOM reads return stale/zero data | Model trains on garbage DOM features → thick book and wall detection fail | Self-check: verify Bid1Size and TotalBidDepth are non-zero during RTH. If all zeros, DOM feed may not be enabled on the chart — check SC data feed settings |
| Absorption tracking misses events | AbsorbLevel stays at 0 → absorption features are useless | Self-check: verify AbsorbLevel is non-zero at least sometimes during consolidation. If always 0, the detection logic threshold may be too high — tune the "large resting order" definition |
| DOM cooldown (every 10th tick) misses fast changes | DOM features have 10-tick lag, may miss rapid wall breaks | Start with 10-tick cooldown. If model performance is poor on DOM features, reduce to every 5th or every tick and measure SC performance impact |
| CSV grows too large | Multi-session collection fills disk | Each tick is ~200 bytes. One RTH session ≈ 10-20MB. Not a concern for weeks of data. Only flag if approaching GB scale |

### ML Training (Phase 2)

| Risk | Impact | Mitigation |
|------|--------|------------|
| Insufficient data | Model overfits to a single session's market character | Collect at least 3-5 RTH sessions across different market conditions before training. One session is enough to test the pipeline end-to-end but not enough to trust the model |
| Label imbalance | Consolidation may be rare or dominant depending on the sessions collected → model biased | The spec uses `class_weight='balanced'` which compensates. Self-check: print label distribution. If any class <5%, collect more diverse data |
| Model disagrees with prior findings | Feature importance doesn't match thick book #1, absorption #2 | This is information, not failure. Document what ranked higher. Possible causes: different instrument (ES vs NQ), different market regime, different time of day. Consider retraining on data that matches the original study conditions |
| Auto-labeling doesn't match visible consolidation on chart | Percentile thresholds (rot_pctl=30, speed_pctl=35, delta_pctl=40) may not suit the new data | The trainer has CLI args to tune these. Start with spec defaults, visually compare labeled_data.csv against chart, adjust if labels don't match obvious consolidation zones |
| Feature drift between trainer and scorer | Scorer computes features differently → model predictions meaningless | Both use the same `blb_features.py`. Feature hash saved during training, verified at scorer startup. If hash mismatch, scorer refuses to start |
| Breakout-imminent class (class 2) has too few samples | The N ticks before each transition may not be enough training data | Start with N=30 (same as persist window). If class 2 has <100 samples, increase N or collect more sessions with transitions |

### Shared Memory IPC (Phase 3)

| Risk | Impact | Mitigation |
|------|--------|------------|
| ACSIL blocks CreateFileMappingA | Shared memory doesn't work → can't bridge C++ and Python | Test with a minimal proof-of-concept study first (Phase 3 self-check). If blocked: fall back to file-backed mmap — same layout, same protocols, backed by a file instead of pagefile. Spec already defines this fallback in `shared_memory_spec.md` |
| "Global\\" prefix needs admin rights | Named shared memory not visible between processes | Use "Local\\" prefix instead (scoped to user session). Both SC and Python run under the same user, so Local is sufficient |
| Python can't open C++-created shared memory | `mmap` module on Windows uses different naming conventions | Use `ctypes` to call `OpenFileMappingW` directly, then `MapViewOfFile`, then wrap result. The `shared_memory.py` module handles this |
| Ring buffer overrun | Python scorer falls behind, C++ overwrites unread ticks | Ring buffer is 1024 slots. At 100 ticks/sec, Python has 10 seconds of runway. Self-check: scorer warns if `write_index - read_index > 900`. If it happens often, increase ring size or optimize scorer |

### Live Scoring (Phase 4)

| Risk | Impact | Mitigation |
|------|--------|------------|
| Scorer latency exceeds tick rate | Signal falls behind, becomes stale | Log latency every 1000 ticks. RandomForest predict on ~56 features [SPECULATION] should be <2ms. The bottleneck is feature rolling — use incremental computation, not full-window recompute. If still slow, reduce model trees (300→100) and benchmark |
| Scorer crashes or hangs | No signal → sg_Stale = 1.0, strategy gets no guidance | Staleness detection in Phase 5 detector catches this. Strategy should fail-open (trade as if no filter). Scorer logs PID for manual restart. [SUGGESTION] Add a watchdog script that restarts scorer if PID disappears |
| Scorer warm-up during live trading | First ~500 ticks have no valid signal | Scorer writes `ready=0` and `consolidation_prob=0.5` (neutral) during warm-up. Strategy should check `ready` flag and ignore signal until warm-up completes |
| Model file gets corrupted or deleted | Scorer can't start | Scorer checks for model file at startup and prints clear error. Keep a backup copy of model.pkl |

### Signal Reader (Phase 5)

| Risk | Impact | Mitigation |
|------|--------|------------|
| Two ACSIL studies on same chart cause performance issues | Chart freezes or lags | Both studies use `AutoLoop=0` + `UpdateAlways=1`. The detector is thin (just reads shared memory, no computation). If performance degrades, profile which study is the bottleneck |
| Subgraph values frozen/not updating | Strategy reads stale data | sg_Stale subgraph exists specifically for this. Strategy checks sg_Stale before using other subgraph values |

### Strategy Integration (Phase 6 — future)

| Risk | Impact | Mitigation |
|------|--------|------------|
| Too few cycles to train strategy model | Overfits to small sample | Need at least 50 matched cycles. If the autotrader CSV test mode on collected data produces fewer, collect more sessions or use wider parameter ranges to generate more cycles |
| Time alignment between tick data and bar data is off | Cycle features don't match actual market state at entry | Tick timestamps and bar timestamps must be from the same data source. Use nearest-tick matching with a tolerance (1 second max). Self-check: verify prices match at alignment points |
| Strategy model learns session-specific patterns | Works on training data, fails on new sessions | Time-ordered train/test split protects against this. Additionally, check that out-of-sample win prediction > 55%. If not, the model may be overfitting to market conditions rather than consolidation structure |

### Operational

| Risk | Impact | Mitigation |
|------|--------|------------|
| Python process not started before trading | No signal available, strategy trades blind | Strategy fail-open design handles this safely. [SUGGESTION] Add a pre-trading checklist: (1) start scorer, (2) verify "Scoring active" log, (3) check sg_Stale = 0.0 in SC |
| Model becomes stale over time | Market microstructure evolves, model loses accuracy | Monitor feature importance drift. Periodically retrain on recent data (monthly or quarterly). Compare new model's feature rankings against prior — if they shift significantly, investigate what changed |
| SC instance restart clears shared memory | Scorer writes to old region, new detector reads new region | Scorer detects stale tick_index (no new ticks arriving) and reconnects. Detector creates fresh shared memory on startup, scorer finds the new region |
