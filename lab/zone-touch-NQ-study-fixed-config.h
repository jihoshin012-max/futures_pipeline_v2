// STUDY VERSION LOG
// Current: v1.0 (2026-03-22) — P1-frozen config
// Source: scoring_model_acal.json + feature_config.json

// zone_bounce_config_FIXED.h — P1-frozen configuration for ATEAM_ZONE_BOUNCE_FIXED
// CONFIG_VERSION = "P1_2026-03-22"
//
// Source of truth: scoring_model_acal.json + feature_config.json
// Do NOT modify without re-running replication gate (Part B).
// ---------------------------------------------------------------
#pragma once

namespace ZoneBounceConfig
{
    // ---- Version tag ----
    static const char* CONFIG_VERSION = "P1_2026-03-22";

    // ---- Instrument ----
    constexpr float TICK_SIZE     = 0.25f;   // NQ E-mini
    constexpr float TICK_VALUE    = 5.00f;   // $5/tick/contract
    constexpr int   BAR_TYPE_VOL  = 250;     // 250-volume bars

    // ---- Cost model ----
    // 3 ticks per TRADE ENTRY (not per contract, not per leg).
    // Deducted once from the combined weighted PnL.
    constexpr int COST_TICKS = 3;

    // =====================================================================
    //  A-Cal Scoring Model (4 features, P1-frozen)
    // =====================================================================

    constexpr float SCORE_THRESHOLD = 16.66f;
    constexpr float SCORE_MAX       = 23.80f;

    // ---- F10: Prior Penetration (numeric, 3 bins) ----
    // Lower penetration = better (zone held well on prior touch)
    // Bin: Low <= p33, Mid = (p33, p67), High >= p67
    constexpr float F10_WEIGHT   = 10.0f;
    constexpr float F10_BIN_P33  = 220.0f;
    constexpr float F10_BIN_P67  = 590.0f;
    // Points: Low = 10.0, Mid = 5.0, High = 0.0, NaN/seq1 = 0.0

    // ---- F04: Cascade State (categorical, 3 values) ----
    // NO_PRIOR = best, PRIOR_BROKE = worst
    constexpr float F04_WEIGHT            = 5.94f;
    constexpr float F04_PTS_NO_PRIOR      = 5.94f;   // best
    constexpr float F04_PTS_PRIOR_HELD    = 2.97f;   // mid (weight / 2)
    constexpr float F04_PTS_PRIOR_BROKE   = 0.0f;    // worst

    // ---- F01: Timeframe (categorical) ----
    // 30m = best, 480m = worst, all others = mid
    // Only 15m/30m/60m/90m/120m pass TF filter
    constexpr float F01_WEIGHT       = 3.44f;
    constexpr float F01_PTS_BEST     = 3.44f;   // 30m
    constexpr float F01_PTS_MID      = 1.72f;   // 15m, 60m, 90m, 120m
    constexpr float F01_PTS_WORST    = 0.0f;    // 480m (never reached post-filter)
    constexpr int   F01_BEST_TF_MIN  = 30;      // best TF in minutes

    // ---- F21: Zone Age (numeric, 3 bins) ----
    // Younger = better (fresh zones bounce more reliably)
    constexpr float F21_WEIGHT   = 4.42f;
    constexpr float F21_BIN_P33  = 49.0f;
    constexpr float F21_BIN_P67  = 831.87f;
    // Points: Low = 4.42, Mid = 2.21, High = 0.0, NaN = 0.0

    // =====================================================================
    //  Bin boundary convention (matches Python: <= and >=)
    // =====================================================================
    // Low:  value <= p33         -> full weight
    // Mid:  p33 < value < p67    -> weight / 2
    // High: value >= p67         -> 0

    // =====================================================================
    //  Segmentation: Seg3 (Score + Trend Context)
    // =====================================================================

    // TrendSlope: linear regression slope of Last price over trailing 50 bars
    // y = mx + b, TrendSlope = m. Equivalent to np.polyfit(x, y, 1)[0].
    constexpr int   TREND_LOOKBACK   = 50;
    constexpr float TREND_P33        = -0.30755102040803795f;
    constexpr float TREND_P67        =  0.34030804321728640f;

    // Direction-aware trend labels:
    //   Demand CT = slope < P33 (falling into demand)
    //   Supply CT = slope > P67 (rising into supply)
    //   Demand WT = slope > P67 (rising, with-trend for demand)
    //   Supply WT = slope < P33 (falling, with-trend for supply)
    //   Everything else = NT

    // =====================================================================
    //  Exit Parameters (from exit sweep — supersedes pipeline defaults)
    // =====================================================================

    // CT Mode (ModeB): counter-trend, highest conviction
    constexpr int CT_T1_TICKS  = 40;      // leg 1 target
    constexpr int CT_T2_TICKS  = 80;      // leg 2 target
    constexpr int CT_STOP_TICKS = 190;    // shared stop
    constexpr int CT_TIMECAP   = 160;     // bars

    // WT/NT Mode (ModeA): with-trend or neutral
    constexpr int WTNT_T1_TICKS  = 60;    // leg 1 target
    constexpr int WTNT_T2_TICKS  = 80;    // leg 2 target
    constexpr int WTNT_STOP_TICKS = 240;  // shared stop
    constexpr int WTNT_TIMECAP   = 160;   // bars

    // Position sizing: 67% leg1, 33% leg2
    // Base = 3 contracts: leg1 = 2ct, leg2 = 1ct
    constexpr float LEG1_WEIGHT = 0.67f;
    constexpr float LEG2_WEIGHT = 0.33f;

    // CT limit entry (5t inside zone edge, 20-bar fill window)
    constexpr int CT_LIMIT_DEPTH_TICKS = 5;    // 5 ticks inside zone edge
    constexpr int CT_FILL_WINDOW_BARS  = 20;   // cancel after 20 bars

    // No BE trigger, no trail trigger (sweep found no improvement)

    // =====================================================================
    //  Filters
    // =====================================================================

    constexpr int   TF_MAX_MINUTES   = 120;   // reject zones from TF > 120m
    constexpr int   WTNT_SEQ_MAX     = 5;     // WT/NT seq gate
    // CT mode: no seq gate

    // =====================================================================
    //  Kill-Switch
    // =====================================================================

    constexpr int KILLSWITCH_CONSEC_LOSSES = 3;     // halt for session
    constexpr int KILLSWITCH_DAILY_TICKS   = -400;  // halt for day
    constexpr int KILLSWITCH_WEEKLY_TICKS  = -800;  // halt for week

    // =====================================================================
    //  EOD Flatten
    // =====================================================================

    constexpr int FLATTEN_HOUR   = 16;
    constexpr int FLATTEN_MINUTE = 55;  // 16:55 ET

    // =====================================================================
    //  Storage Magic
    // =====================================================================

    // ZBV4 SignalStorage magic (must match ZoneBounceSignalsV4_aligned.cpp)
    constexpr uint32_t ZBV4_STORAGE_MAGIC = 0x5A425634;  // "ZBV4"

    // Max signals in ZBV4 storage (must match)
    constexpr int MAX_TRACKED_SIGNALS = 5000;
    constexpr int MAX_TRACKED_ZONES   = 10000;

} // namespace ZoneBounceConfig
