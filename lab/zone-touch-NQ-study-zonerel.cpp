// STUDY VERSION LOG
// Current: v3.0 (2026-03-23) — Zone-relative exits, CT 5t limit entry
// v1.0 (2026-03-22) — Initial build (fixed exits, market-only entries)
// Fresh build — not derived from M1A or M1B

// archetype: zone_touch
// @study: ATEAM Zone Bounce V1
// @version: 3.0
// @author: ATEAM
// @type: trading-system
// @features: inter-study, autoloop, GetPersistentPointerFromChartStudy, 2-leg-exits
// @looping: autoloop
// @complexity: medium
// @inputs: ZBV4StudyID, Slot TF mappings, base qty, auto-trade enable
// @summary: A-Cal scored zone bounce autotrader. Reads zone touch signals from
//           ZoneBounceSignalsV4 via persistent storage. 4-feature scoring model,
//           2-mode routing (CT vs WT/NT), zone-relative 2-leg partial exits.
//           CT mode uses 5-tick limit entry inside zone edge.

#include "sierrachart.h"
#include <cstdio>
#include <cmath>

// =========================================================================
//  P1-Frozen Config (inlined — SC remote build only sends the .cpp file)
//  CONFIG_VERSION = "P1_2026-03-23_v3"
//  Source of truth: scoring_model_acal.json + feature_config.json
//  Do NOT modify without re-running replication gate (Part B).
//  Keep zone_bounce_config_ZONEREL.h in sync for pipeline version tracking.
// =========================================================================
namespace ZoneBounceConfig
{
    static const char* CONFIG_VERSION = "P1_2026-03-23_v3";

    // Instrument
    constexpr float TICK_SIZE     = 0.25f;
    constexpr float TICK_VALUE    = 5.00f;
    constexpr int   BAR_TYPE_VOL  = 250;
    constexpr int   COST_TICKS    = 3;

    // A-Cal Scoring Model (4 features)
    constexpr float SCORE_THRESHOLD = 16.66f;
    constexpr float SCORE_MAX       = 23.80f;

    // F10: Prior Penetration (numeric). Low <= p33 = best.
    constexpr float F10_WEIGHT  = 10.0f;
    constexpr float F10_BIN_P33 = 220.0f;
    constexpr float F10_BIN_P67 = 590.0f;

    // F04: Cascade State (categorical)
    constexpr float F04_WEIGHT          = 5.94f;
    constexpr float F04_PTS_NO_PRIOR    = 5.94f;
    constexpr float F04_PTS_PRIOR_HELD  = 2.97f;
    constexpr float F04_PTS_PRIOR_BROKE = 0.0f;

    // F01: Timeframe (categorical)
    constexpr float F01_WEIGHT      = 3.44f;
    constexpr float F01_PTS_BEST    = 3.44f;   // 30m
    constexpr float F01_PTS_MID     = 1.72f;   // 15m, 60m, 90m, 120m
    constexpr float F01_PTS_WORST   = 0.0f;    // 480m
    constexpr int   F01_BEST_TF_MIN = 30;

    // F21: Zone Age (numeric). Low <= p33 = best.
    constexpr float F21_WEIGHT  = 4.42f;
    constexpr float F21_BIN_P33 = 49.0f;
    constexpr float F21_BIN_P67 = 831.87f;

    // Trend classification (non-direction-aware)
    // slope <= P33 -> CT, slope >= P67 -> WT, else NT
    // Same classification regardless of demand/supply direction.
    // TrendSlope read from ZBV4 SignalRecord.TrendSlope (pre-computed).
    constexpr float TREND_P33 = -0.30755102040803795f;
    constexpr float TREND_P67 =  0.34030804321728640f;

    // =====================================================================
    //  Zone-Relative Exit Parameters (v3.0 — replaces fixed exits)
    //  Targets and stops scale with zone width. Multipliers are P1-frozen.
    // =====================================================================
    constexpr float T1_MULT    = 0.5f;    // leg 1 target = 0.5 x zone_width_ticks
    constexpr float T2_MULT    = 1.0f;    // leg 2 target = 1.0 x zone_width_ticks
    constexpr float STOP_MULT  = 1.5f;    // stop = 1.5 x zone_width_ticks
    constexpr int   STOP_FLOOR = 120;     // min stop = 120 ticks (protects narrow zones)
    constexpr int   TIMECAP    = 160;     // bars (both modes)

    // CT Limit Entry (v3.0)
    constexpr int CT_LIMIT_DEPTH_TICKS = 5;    // 5 ticks inside zone edge
    constexpr int CT_FILL_WINDOW_BARS  = 20;   // cancel after 20 bars

    constexpr float LEG1_WEIGHT = 0.67f;
    constexpr float LEG2_WEIGHT = 0.33f;

    // Filters
    constexpr int TF_MAX_MINUTES = 120;
    constexpr int WTNT_SEQ_MAX   = 5;

    // Kill-Switch (v3.0: increased for wider zone-relative stops)
    constexpr int KILLSWITCH_CONSEC_LOSSES = 3;
    constexpr int KILLSWITCH_DAILY_TICKS   = -600;
    constexpr int KILLSWITCH_WEEKLY_TICKS  = -1200;

    // EOD Flatten
    constexpr int FLATTEN_HOUR   = 16;
    constexpr int FLATTEN_MINUTE = 55;

    // Drawing line number ranges (non-overlapping with ZBV4 84000-92000 and M1B 200000+)
    constexpr int LN_ZB1_ENTRY = 300000;
    constexpr int LN_ZB1_STOP  = 304000;
    constexpr int LN_ZB1_T1    = 308000;
    constexpr int LN_ZB1_T2    = 312000;
    constexpr int LN_ZB1_LABEL = 316000;
    constexpr int MAX_ZB1_DRAWINGS = 4000;

    // ZBV4 storage
    constexpr uint32_t ZBV4_STORAGE_MAGIC = 0x5A425634;
    constexpr int MAX_TRACKED_SIGNALS = 5000;
    constexpr int MAX_TRACKED_ZONES   = 10000;
} // namespace ZoneBounceConfig

SCDLLName("ATEAM_ZONE_BOUNCE_V1")

// =========================================================================
//  V4 Data Interface — struct definitions must match ZBV4_aligned.cpp
//  This is plumbing only. No trading logic from prior autotraders.
// =========================================================================

struct SignalRecord
{
    float TouchPrice;
    float ZoneTop;
    float ZoneBot;
    float TrendSlope;
    float VPRayPrice;
    float ApproachVelocity;
    float ZoneWidthTicks;
    float PenetrationTicks;
    int   BarIndex;
    int   TouchSequence;
    int   Type;           // 0=DEMAND_EDGE, 1=SUPPLY_EDGE
    int   TrendCtx;
    int   ModeAssignment;
    int   QualityScore;
    int   ContextScore;
    int   TotalScore;
    int   TFConfluence;
    int   ZoneAgeBars;
    int   TFWeightScore;
    int   SessionClass;
    int   DayOfWeek;
    int   CascadeState;   // 0=PRIOR_HELD, 1=NO_PRIOR, 2=PRIOR_BROKE
    int   SourceSlot;
    int   SourceHtfBar;
    int   ConfirmedBar;
    float DbgPrevHigh;
    float DbgPrevSBot;
    float DbgEvalHigh;
    float DbgEvalSBot;
    bool  HasVPRay;
    bool  CascadeActive;
    bool  HtfConfirmed;
    bool  DrawingsPlaced;
    bool  RaysResolved;
    bool  Active;
};

struct TrackedZone
{
    float Top;
    float Bot;
    int   TouchCount;
    int   BirthBar;
    int   DeathBar;
    int   SourceSlot;
    bool  Active;
};

struct SignalStorage
{
    uint32_t     MagicNumber;
    int          SignalCount;
    int          ZoneCount;
    int          LastBreakBar;
    int          LastHeldBar;
    SignalRecord Signals[ZoneBounceConfig::MAX_TRACKED_SIGNALS];
    TrackedZone  Zones[ZoneBounceConfig::MAX_TRACKED_ZONES];
};

// =========================================================================
//  Zone Bounce Internal Types
// =========================================================================

enum TrendLabel { TREND_CT = 0, TREND_WT = 1, TREND_NT = 2 };
enum TradeMode  { MODE_CT = 0, MODE_WTNT = 1 };
enum ExitType   { EXIT_NONE = 0, EXIT_TARGET_1, EXIT_TARGET_2, EXIT_STOP,
                  EXIT_TIMECAP, EXIT_FLATTEN_EOD, EXIT_LIMIT_EXPIRED };
enum EntryType  { ENTRY_MARKET = 0, ENTRY_LIMIT_5T = 1 };

static const char* TrendLabelStr[] = { "CT", "WT", "NT" };
static const char* ExitTypeStr[]   = { "NONE", "TARGET_1", "TARGET_2",
                                       "STOP", "TIMECAP", "FLATTEN_EOD",
                                       "LIMIT_EXPIRED" };
static const char* CascadeStr[]    = { "PRIOR_HELD", "NO_PRIOR", "PRIOR_BROKE" };
static const char* EntryTypeStr[]  = { "MARKET", "LIMIT_5T" };

struct LegState
{
    bool  Active;
    int   Contracts;
    float TargetPrice;
    ExitType  ExitResult;
    float ExitPrice;
    int   ExitBar;
    float PnlTicks;    // raw, before cost
};

struct PositionState
{
    bool  InTrade;
    TradeMode Mode;
    EntryType Entry;
    int   Direction;       // +1 LONG, -1 SHORT
    float EntryPrice;
    int   EntryBar;
    float StopPrice;
    float ZoneTop;         // for logging
    float ZoneBot;         // for logging
    int   ZoneWidthTicks;  // for logging
    int   StopTicks;       // for logging
    int   T1Ticks;         // for logging
    int   T2Ticks;         // for logging
    int   LimitDepthTicks; // 5 for CT, 0 for WT/NT
    LegState Leg1;
    LegState Leg2;
    float MFE;             // ticks
    float MAE;             // ticks
    int   SignalIdx;       // index into SignalStorage for logging
    int   DrawIdx;         // drawing index for level lines
};

struct KillSwitchState
{
    int   ConsecLosses;
    float DailyPnl;        // cumulative ticks today
    float WeeklyPnl;       // cumulative ticks this week
    int   LastTradeDay;
    int   LastTradeWeek;
    bool  SessionHalted;
    bool  DailyHalted;
    bool  WeeklyHalted;
};

// CT limit order pending state (separate from market pending)
struct LimitPendingState
{
    bool      Active;
    int       Direction;        // +1 LONG, -1 SHORT
    float     LimitPrice;       // 5t inside zone edge
    int       DeadlineBar;      // cancel after this bar (signal bar + FILL_WINDOW)
    int       SignalIdx;        // index into SignalStorage
    int       ZoneWidthTicks;
    float     ZoneTop;
    float     ZoneBot;
    int       Leg1Qty;
    int       Leg2Qty;
    bool      SingleLeg;
    int       SignalBar;        // bar when signal was detected
};

// WT/NT market entry pending state (fills at next bar open)
struct MarketPendingState
{
    bool      Active;
    TradeMode Mode;
    int       Direction;     // +1 LONG, -1 SHORT
    int       SignalIdx;     // index into SignalStorage
    int       ZoneWidthTicks;
    float     ZoneTop;
    float     ZoneBot;
    int       Leg1Qty;
    int       Leg2Qty;
    bool      SingleLeg;
};

struct StudyState
{
    uint32_t          Magic;
    int               LastProcessedSignalCount;
    PositionState     Position;
    KillSwitchState   KillSwitch;
    MarketPendingState MarketPending;
    LimitPendingState  LimitPending;
    int               TradeLogHeaderWritten;
    int               SignalLogHeaderWritten;
    int               DrawCount;  // monotonic counter for drawing line IDs
};

constexpr uint32_t STUDY_STATE_MAGIC = 0x5A42564E; // "ZBVN"

// =========================================================================
//  Input declarations
// =========================================================================

SCSFExport scsf_ATEAM_ZONE_BOUNCE_V1(SCStudyInterfaceRef sc)
{
    // --- Inputs ---
    SCInputRef Input_ZBV4StudyID   = sc.Input[0];
    SCInputRef Input_Enabled       = sc.Input[1];   // master on/off for all logic
    SCInputRef Input_SendOrders    = sc.Input[2];   // submit orders to exchange
    SCInputRef Input_BaseQty       = sc.Input[3];
    SCInputRef Input_Slot0TF       = sc.Input[4];
    SCInputRef Input_Slot1TF       = sc.Input[5];
    SCInputRef Input_Slot2TF       = sc.Input[6];
    SCInputRef Input_Slot3TF       = sc.Input[7];
    SCInputRef Input_Slot4TF       = sc.Input[8];
    SCInputRef Input_Slot5TF       = sc.Input[9];
    SCInputRef Input_Slot6TF       = sc.Input[10];
    SCInputRef Input_Slot7TF       = sc.Input[11];
    SCInputRef Input_Slot8TF       = sc.Input[12];
    SCInputRef Input_CSVLogging    = sc.Input[13];
    SCInputRef Input_CSVTestMode   = sc.Input[14];
    SCInputRef Input_CSVTestPath   = sc.Input[15];

    // --- Subgraphs (visual indicators) ---
    SCSubgraphRef SG_Signal     = sc.Subgraph[0];
    SCSubgraphRef SG_Score      = sc.Subgraph[1];
    SCSubgraphRef SG_EntryPrice = sc.Subgraph[2];

    // =================================================================
    //  Defaults (SetDefaults block)
    // =================================================================
    if (sc.SetDefaults)
    {
        sc.GraphName = "ATEAM Zone Bounce V1 [v3.0]";
        sc.StudyDescription = "A-Cal zone bounce autotrader (P1_2026-03-23_v3). "
            "Zone-relative exits, CT 5t limit entry.";
        sc.AutoLoop = 1;
        sc.GraphRegion = 0;
        sc.CalculationPrecedence = VERY_LOW_PREC_LEVEL;

        Input_ZBV4StudyID.Name = "ZBV4 Study ID";
        Input_ZBV4StudyID.SetInt(1);
        Input_ZBV4StudyID.SetIntLimits(1, 500);

        Input_Enabled.Name = "Enable Trading Logic";
        Input_Enabled.SetYesNo(0);

        Input_SendOrders.Name = "Send Live Orders";
        Input_SendOrders.SetYesNo(0);

        Input_BaseQty.Name = "Base Quantity (contracts)";
        Input_BaseQty.SetInt(3);
        Input_BaseQty.SetIntLimits(1, 30);

        // Slot-to-TF mapping (minutes). Set to 0 for unused slots.
        Input_Slot0TF.Name  = "Slot 0 TF (minutes)";  Input_Slot0TF.SetInt(15);
        Input_Slot1TF.Name  = "Slot 1 TF (minutes)";  Input_Slot1TF.SetInt(30);
        Input_Slot2TF.Name  = "Slot 2 TF (minutes)";  Input_Slot2TF.SetInt(60);
        Input_Slot3TF.Name  = "Slot 3 TF (minutes)";  Input_Slot3TF.SetInt(90);
        Input_Slot4TF.Name  = "Slot 4 TF (minutes)";  Input_Slot4TF.SetInt(120);
        Input_Slot5TF.Name  = "Slot 5 TF (minutes)";  Input_Slot5TF.SetInt(0);
        Input_Slot6TF.Name  = "Slot 6 TF (minutes)";  Input_Slot6TF.SetInt(0);
        Input_Slot7TF.Name  = "Slot 7 TF (minutes)";  Input_Slot7TF.SetInt(0);
        Input_Slot8TF.Name  = "Slot 8 TF (minutes)";  Input_Slot8TF.SetInt(0);

        Input_CSVLogging.Name = "CSV Logging Enabled";
        Input_CSVLogging.SetYesNo(1);

        Input_CSVTestMode.Name = "CSV Test Mode";
        Input_CSVTestMode.SetYesNo(0);

        Input_CSVTestPath.Name = "CSV Test Path";
        Input_CSVTestPath.SetString(
            "C:\\Projects\\pipeline\\stages\\01-data\\output\\zone_prep\\");

        SG_Signal.Name     = "Signal";
        SG_Signal.DrawStyle = DRAWSTYLE_ARROW_UP;
        SG_Signal.PrimaryColor = RGB(0, 200, 0);

        SG_Score.Name      = "A-Cal Score";
        SG_Score.DrawStyle = DRAWSTYLE_IGNORE;

        SG_EntryPrice.Name = "Entry Price";
        SG_EntryPrice.DrawStyle = DRAWSTYLE_IGNORE;

        sc.AllowMultipleEntriesInSameDirection = 0;
        sc.SupportReversals = 0;
        sc.AllowOppositeEntryWithOpposingPositionOrOrders = 0;
        sc.SupportAttachedOrdersForTrading = 1;
        sc.CancelAllOrdersOnEntriesAndReversals = 1;
        sc.AllowOnlyOneTradePerBar = 1;
        sc.MaximumPositionAllowed = 10;
        sc.SendOrdersToTradeService = 0;
        sc.MaintainTradeStatisticsAndTradesData = 1;
        sc.AllowEntryWithWorkingOrders = 0;
        sc.CancelAllWorkingOrdersOnExit = 1;

        return;
    }

    // SendOrdersToTradeService stays 0 — SC's internal sim handles
    // attached orders (OCO groups) in simulation mode. Set to 1 only
    // for live broker routing (not replay).
    sc.SendOrdersToTradeService = 0;

    // =================================================================
    //  Enable Trading Logic gate
    // =================================================================
    if (!Input_Enabled.GetBoolean())
    {
        if (Input_SendOrders.GetBoolean() && sc.Index == sc.ArraySize - 1)
        {
            sc.AddMessageToLog(
                "ATEAM_ZONE_BOUNCE_V1: Send Live Orders is ON but "
                "Enable Trading Logic is OFF. No orders will be sent.", 1);
        }
        return;
    }

    // =================================================================
    //  CSV TEST MODE — standalone replication gate (batch simulation)
    //  Reads merged CSVs + bar data, runs full scoring + entry + exit
    //  pipeline without V4/ZBV4 SignalRecords. Same logic, CSV input.
    //  Runs ONCE on the last bar, writes output files, then returns.
    // =================================================================
    if (Input_CSVTestMode.GetBoolean())
    {
        // Only run on last bar
        if (sc.Index != sc.ArraySize - 1)
            return;

        SCString basePath;
        basePath = Input_CSVTestPath.GetString();
        // Ensure trailing backslash
        if (basePath.GetLength() > 0 &&
            basePath[basePath.GetLength() - 1] != '\\')
            basePath += "\\";

        sc.AddMessageToLog("CSV TEST MODE: Starting batch replication...", 0);

        // ---------- Load bar data ----------
        struct BarRow { float Open, High, Low, Last; };
        const int MAX_BARS = 200000;
        BarRow* barData = (BarRow*)sc.AllocateMemory(MAX_BARS * sizeof(BarRow));
        if (!barData)
        {
            sc.AddMessageToLog("CSV TEST MODE: Failed to allocate bar data", 1);
            return;
        }
        int nBars = 0;
        {
            SCString barPath;
            barPath.Format("%sNQ_bardata_P1.csv", basePath.GetChars());
            FILE* bf = fopen(barPath.GetChars(), "r");
            if (!bf)
            {
                sc.AddMessageToLog("CSV TEST MODE: Cannot open bar data CSV", 1);
                sc.FreeMemory(barData);
                return;
            }
            char line[2048];
            fgets(line, sizeof(line), bf); // skip header
            while (fgets(line, sizeof(line), bf) && nBars < MAX_BARS)
            {
                // Parse: Date,Time,Open,High,Low,Last,...
                char dateStr[64], timeStr[64];
                float o, h, l, c;
                if (sscanf(line, "%[^,],%[^,],%f,%f,%f,%f",
                           dateStr, timeStr, &o, &h, &l, &c) >= 6)
                {
                    barData[nBars].Open = o;
                    barData[nBars].High = h;
                    barData[nBars].Low  = l;
                    barData[nBars].Last = c;
                    nBars++;
                }
            }
            fclose(bf);
        }
        {
            SCString msg;
            msg.Format("CSV TEST MODE: Loaded %d bars", nBars);
            sc.AddMessageToLog(msg, 0);
        }

        // ---------- Load touches ----------
        struct TouchRow
        {
            char  DateTime[32];
            int   BarIndex;
            int   TouchType;    // 0=DEMAND, 1=SUPPLY
            float TouchPrice;
            float ZoneTop, ZoneBot;
            float Penetration;
            int   TouchSequence;
            int   ZoneAgeBars;
            float TrendSlope;
            char  SourceLabel[16];
            int   CascadeState;  // 0=PRIOR_HELD, 1=NO_PRIOR, 2=PRIOR_BROKE
            int   RotBarIndex;
            float ZoneWidthTicks;
            char  SBBLabel[16];
        };
        const int MAX_TOUCHES = 10000;
        TouchRow* touches = (TouchRow*)sc.AllocateMemory(MAX_TOUCHES * sizeof(TouchRow));
        if (!touches)
        {
            sc.FreeMemory(barData);
            return;
        }
        int nTouches = 0;

        auto ParseCascade = [](const char* s) -> int
        {
            if (strstr(s, "NO_PRIOR"))    return 1;
            if (strstr(s, "PRIOR_HELD"))  return 0;
            if (strstr(s, "PRIOR_BROKE")) return 2;
            if (strstr(s, "UNKNOWN"))     return 1; // treat as NO_PRIOR
            return 1;
        };

        auto LoadMergedCSV = [&](const char* fname)
        {
            SCString fullPath;
            fullPath.Format("%s%s", basePath.GetChars(), fname);
            FILE* f = fopen(fullPath.GetChars(), "r");
            if (!f) return;
            char line[4096];
            fgets(line, sizeof(line), f); // skip header
            // Find column indices from header
            // Columns: DateTime,BarIndex,TouchType,...,ZoneTop,ZoneBot,...,
            //          Penetration,...,TouchSequence,ZoneAgeBars,...,TrendSlope,
            //          SourceLabel,...,ZoneWidthTicks,CascadeState,...,SBB_Label,
            //          RotBarIndex,...
            while (fgets(line, sizeof(line), f) && nTouches < MAX_TOUCHES)
            {
                // Parse CSV by splitting on commas
                char fields[40][128];
                int nFields = 0;
                {
                    char* p = line;
                    while (*p && nFields < 40)
                    {
                        char* start = p;
                        while (*p && *p != ',' && *p != '\n' && *p != '\r') p++;
                        int len = (int)(p - start);
                        if (len >= 128) len = 127;
                        memcpy(fields[nFields], start, len);
                        fields[nFields][len] = '\0';
                        nFields++;
                        if (*p == ',') p++;
                    }
                }
                if (nFields < 36) continue;  // need all columns

                // Merged CSV column order (from zone_prep):
                // 0:DateTime, 1:BarIndex, 2:TouchType, 3:ApproachDir,
                // 4:TouchPrice, 5:ZoneTop, 6:ZoneBot, 7:HasVPRay,
                // 8:VPRayPrice, 9:Reaction, 10:Penetration,
                // 11:ReactionPeakBar, 12:ZoneBroken, 13:BreakBarIndex,
                // 14:BarsObserved, 15:TouchSequence, 16:ZoneAgeBars,
                // 17:ApproachVelocity, 18:TrendSlope, 19:SourceLabel,
                // 20:RxnBar_120, 21:RxnBar_160, 22:RxnBar_240,
                // 23:RxnBar_30, 24:RxnBar_360, 25:RxnBar_50,
                // 26:RxnBar_80, 27:PenBar_120, 28:PenBar_30,
                // 29:PenBar_50, 30:PenBar_80, 31:ZoneWidthTicks,
                // 32:CascadeState, 33:TFConfluence, 34:SBB_Label,
                // 35:RotBarIndex, 36:Period

                TouchRow& t = touches[nTouches];
                strncpy(t.DateTime, fields[0], 31);
                t.DateTime[31] = '\0';
                t.BarIndex = atoi(fields[1]);
                t.TouchType = (strstr(fields[2], "DEMAND") != nullptr) ? 0 : 1;
                t.TouchPrice = (float)atof(fields[4]);
                t.ZoneTop = (float)atof(fields[5]);
                t.ZoneBot = (float)atof(fields[6]);
                t.Penetration = (float)atof(fields[10]);
                t.TouchSequence = atoi(fields[15]);
                t.ZoneAgeBars = atoi(fields[16]);
                t.TrendSlope = (float)atof(fields[18]);
                strncpy(t.SourceLabel, fields[19], 15);
                t.SourceLabel[15] = '\0';
                t.ZoneWidthTicks = (float)atof(fields[31]);
                t.CascadeState = ParseCascade(fields[32]);
                strncpy(t.SBBLabel, fields[34], 15);
                t.SBBLabel[15] = '\0';
                t.RotBarIndex = atoi(fields[35]);

                if (t.RotBarIndex >= 0)
                    nTouches++;
            }
            fclose(f);
        };

        LoadMergedCSV("NQ_merged_P1a.csv");
        LoadMergedCSV("NQ_merged_P1b.csv");

        // Sort touches by RotBarIndex (bubble sort — n is small)
        for (int i = 0; i < nTouches - 1; i++)
            for (int j = i + 1; j < nTouches; j++)
                if (touches[j].RotBarIndex < touches[i].RotBarIndex)
                {
                    TouchRow tmp = touches[i];
                    touches[i] = touches[j];
                    touches[j] = tmp;
                }

        {
            SCString msg;
            msg.Format("CSV TEST MODE: Loaded %d touches (RotBarIndex >= 0)", nTouches);
            sc.AddMessageToLog(msg, 0);
        }

        // ---------- Build zone history for F10 ----------
        // For each touch, find the prior touch on the same zone
        // Key: (ZoneTop, ZoneBot, SourceLabel)
        auto FindPriorPenCSV = [&](int touchIdx) -> float
        {
            const TouchRow& cur = touches[touchIdx];
            if (cur.TouchSequence <= 1) return -1.0f;

            for (int i = touchIdx - 1; i >= 0; i--)
            {
                const TouchRow& prev = touches[i];
                if (prev.TouchType != cur.TouchType) continue;
                if (fabs(prev.ZoneTop - cur.ZoneTop) > 0.01f) continue;
                if (fabs(prev.ZoneBot - cur.ZoneBot) > 0.01f) continue;
                if (strcmp(prev.SourceLabel, cur.SourceLabel) != 0) continue;
                if (prev.TouchSequence == cur.TouchSequence - 1)
                    return prev.Penetration;
            }
            return -1.0f;
        };

        // ---------- TF minutes from SourceLabel ----------
        auto CSVGetTFMin = [](const char* label) -> int
        {
            if (strcmp(label, "15m")  == 0) return 15;
            if (strcmp(label, "30m")  == 0) return 30;
            if (strcmp(label, "60m")  == 0) return 60;
            if (strcmp(label, "90m")  == 0) return 90;
            if (strcmp(label, "120m") == 0) return 120;
            if (strcmp(label, "240m") == 0) return 240;
            if (strcmp(label, "360m") == 0) return 360;
            if (strcmp(label, "480m") == 0) return 480;
            if (strcmp(label, "720m") == 0) return 720;
            return 0;
        };

        // ---------- Scoring helpers (reuse from main logic) ----------
        auto BinNum = [](float val, float p33, float p67, float w, bool nan) -> float
        {
            if (nan) return 0.0f;
            if (val <= p33) return w;
            if (val >= p67) return 0.0f;
            return w / 2.0f;
        };

        auto ScF04 = [](int cs) -> float
        {
            switch (cs)
            {
                case 1:  return ZoneBounceConfig::F04_PTS_NO_PRIOR;
                case 0:  return ZoneBounceConfig::F04_PTS_PRIOR_HELD;
                case 2:  return ZoneBounceConfig::F04_PTS_PRIOR_BROKE;
                default: return 0.0f;
            }
        };

        auto ScF01 = [](int tfMin) -> float
        {
            if (tfMin == 30)  return ZoneBounceConfig::F01_PTS_BEST;
            if (tfMin == 480) return ZoneBounceConfig::F01_PTS_WORST;
            if (tfMin > 0)    return ZoneBounceConfig::F01_PTS_MID;
            return 0.0f;
        };

        auto ClsTrend = [](float slope) -> TrendLabel
        {
            if (slope <= ZoneBounceConfig::TREND_P33) return TREND_CT;
            if (slope >= ZoneBounceConfig::TREND_P67) return TREND_WT;
            return TREND_NT;
        };

        auto ComputeExits = [](int zw, int* t1, int* t2, int* st)
        {
            *t1 = (int)(ZoneBounceConfig::T1_MULT * zw + 0.5f);
            *t2 = (int)(ZoneBounceConfig::T2_MULT * zw + 0.5f);
            int raw = (int)(ZoneBounceConfig::STOP_MULT * zw + 0.5f);
            *st = (raw > ZoneBounceConfig::STOP_FLOOR) ? raw : ZoneBounceConfig::STOP_FLOOR;
        };

        // ---------- 2-leg exit simulator (matches Python sim_2leg_zr) ----------
        struct SimResult
        {
            float entryPrice, stopPrice, t1Target, t2Target;
            int   stopTicks, t1Ticks, t2Ticks;
            int   leg1Exit, leg2Exit;  // ExitType enum
            float leg1Pnl, leg2Pnl;
            int   leg1ExitBar, leg2ExitBar;
            float mfe, mae;
            int   barsHeld;
            float weightedPnl;
        };

        auto SimTwoLeg = [&](int entryBar, float entryPrice, int direction,
                              int zoneWidthTicks) -> bool
        {
            // Output stored in a shared SimResult
            return true;  // placeholder
        };

        // Actual simulation
        struct TradeOut
        {
            char  tradeId[16];
            char  mode[8];
            char  datetime[32];
            char  direction[8];
            char  touchType[16];
            char  sourceLabel[16];
            float zoneTop, zoneBot;
            int   zoneWidthTicks;
            char  entryType[16];
            float entryPrice;
            int   stopTicks, t1Ticks, t2Ticks;
            float stopPrice, t1Target, t2Target;
            int   leg1Exit, leg2Exit;
            float leg1Pnl, leg2Pnl;
            float weightedPnl;
            int   barsHeld;
            float mfe, mae;
            float acalScore;
        };

        const int MAX_TRADES = 500;
        TradeOut* tradeLog = (TradeOut*)sc.AllocateMemory(MAX_TRADES * sizeof(TradeOut));
        int nTrades = 0;

        struct SkipOut
        {
            char datetime[32];
            char touchType[16];
            char sourceLabel[16];
            float acalScore;
            char trendLabel[4];
            char skipReason[24];
        };
        const int MAX_SKIPS = 5000;
        SkipOut* skipLog = (SkipOut*)sc.AllocateMemory(MAX_SKIPS * sizeof(SkipOut));
        int nSkips = 0;

        if (!tradeLog || !skipLog)
        {
            sc.FreeMemory(barData);
            sc.FreeMemory(touches);
            if (tradeLog) sc.FreeMemory(tradeLog);
            if (skipLog) sc.FreeMemory(skipLog);
            return;
        }

        // ---------- Simulation state ----------
        int  inTradeUntil = -1;     // bar index when current trade exits
        bool limitPending = false;
        int  limitExpiresAt = -1;

        // Kill-switch
        int   ksConsec = 0;
        float ksDailyPnl = 0.0f;
        float ksWeeklyPnl = 0.0f;
        bool  ksSessionHalt = false;
        bool  ksDailyHalt = false;
        bool  ksWeeklyHalt = false;
        int   ksLastDay = 0;

        // CT limit tracking
        int ctSignals = 0, ctFills = 0, ctExpired = 0;

        int tradeCounter = 0;
        const float TS = ZoneBounceConfig::TICK_SIZE;

        // ---------- Main simulation loop ----------
        for (int ti = 0; ti < nTouches; ti++)
        {
            const TouchRow& t = touches[ti];
            int touchBar = t.RotBarIndex;
            int wtEntryBar = touchBar + 1;
            if (wtEntryBar >= nBars) continue;

            int direction = (t.TouchType == 0) ? 1 : -1;
            int tfMin = CSVGetTFMin(t.SourceLabel);
            int seq = t.TouchSequence;
            int zw = (int)(t.ZoneWidthTicks + 0.5f);

            // Score
            float priorPen = FindPriorPenCSV(ti);
            bool f10NaN = (priorPen < 0.0f);
            float f10Raw = f10NaN ? 0.0f : priorPen;
            float f10Pts = BinNum(f10Raw, ZoneBounceConfig::F10_BIN_P33,
                ZoneBounceConfig::F10_BIN_P67, ZoneBounceConfig::F10_WEIGHT, f10NaN);
            float f04Pts = ScF04(t.CascadeState);
            float f01Pts = ScF01(tfMin);
            float f21Pts = BinNum((float)t.ZoneAgeBars, ZoneBounceConfig::F21_BIN_P33,
                ZoneBounceConfig::F21_BIN_P67, ZoneBounceConfig::F21_WEIGHT, false);
            float score = f10Pts + f04Pts + f01Pts + f21Pts;

            TrendLabel trend = ClsTrend(t.TrendSlope);

            // Check limit expiry
            if (limitPending && touchBar >= limitExpiresAt)
                limitPending = false;

            // --- Determine action ---
            const char* skipReason = nullptr;
            TradeMode mode = MODE_CT;

            // Kill-switch day reset (simplified: use touchBar distance)
            // Use actual bar datetime not available, approximate with bar index gaps
            // Actually we can parse the DateTime from touch CSV
            {
                int dayVal = 0;
                // Parse YYYY-MM-DD from t.DateTime
                int yy=0, mm=0, dd=0;
                sscanf(t.DateTime, "%d-%d-%d", &yy, &mm, &dd);
                dayVal = yy * 10000 + mm * 100 + dd;
                if (dayVal > 0 && dayVal != ksLastDay)
                {
                    ksDailyPnl = 0.0f;
                    ksSessionHalt = false;
                    ksDailyHalt = false;
                    ksConsec = 0;
                    ksLastDay = dayVal;
                }
            }

            // Score gate
            if (score < ZoneBounceConfig::SCORE_THRESHOLD)
                skipReason = "BELOW_THRESHOLD";
            else if (tfMin <= 0 || tfMin > ZoneBounceConfig::TF_MAX_MINUTES)
                skipReason = "TF_FILTER";
            else
            {
                if (trend == TREND_CT)
                    mode = MODE_CT;
                else
                {
                    if (seq > ZoneBounceConfig::WTNT_SEQ_MAX)
                        skipReason = "SEQ_FILTER";
                    else
                        mode = MODE_WTNT;
                }
            }

            // No-overlap
            if (!skipReason && wtEntryBar <= inTradeUntil)
                skipReason = "IN_POSITION";

            // Limit pending
            if (!skipReason && limitPending)
                skipReason = "LIMIT_PENDING";

            // Kill-switch
            if (!skipReason && (ksSessionHalt || ksDailyHalt || ksWeeklyHalt))
                skipReason = "KILL_SWITCH";

            if (skipReason)
            {
                if (nSkips < MAX_SKIPS)
                {
                    SkipOut& sk = skipLog[nSkips++];
                    strncpy(sk.datetime, t.DateTime, 31);
                    sk.datetime[31] = '\0';
                    strncpy(sk.touchType,
                        (t.TouchType == 0) ? "DEMAND_EDGE" : "SUPPLY_EDGE", 15);
                    sk.touchType[15] = '\0';
                    strncpy(sk.sourceLabel, t.SourceLabel, 15);
                    sk.sourceLabel[15] = '\0';
                    sk.acalScore = score;
                    strncpy(sk.trendLabel, TrendLabelStr[trend], 3);
                    sk.trendLabel[3] = '\0';
                    strncpy(sk.skipReason, skipReason, 23);
                    sk.skipReason[23] = '\0';
                }
                continue;
            }

            // --- Execute trade ---
            int entryBar = -1;
            float entryPrice = 0.0f;
            EntryType entryType = ENTRY_MARKET;

            if (mode == MODE_CT)
            {
                ctSignals++;
                // Place limit: 5t inside zone edge
                limitPending = true;
                limitExpiresAt = touchBar + ZoneBounceConfig::CT_FILL_WINDOW_BARS;

                float limitPrice;
                if (direction == 1)
                    limitPrice = t.ZoneTop - ZoneBounceConfig::CT_LIMIT_DEPTH_TICKS * TS;
                else
                    limitPrice = t.ZoneBot + ZoneBounceConfig::CT_LIMIT_DEPTH_TICKS * TS;

                // Scan bars 1..20 for fill
                bool filled = false;
                for (int off = 1; off <= ZoneBounceConfig::CT_FILL_WINDOW_BARS; off++)
                {
                    int bi = touchBar + off;
                    if (bi >= nBars) break;
                    if (direction == 1)
                    {
                        if (barData[bi].Low <= limitPrice)
                        {
                            entryPrice = (barData[bi].Open < limitPrice)
                                ? barData[bi].Open : limitPrice;
                            entryBar = bi;
                            filled = true;
                            break;
                        }
                    }
                    else
                    {
                        if (barData[bi].High >= limitPrice)
                        {
                            entryPrice = (barData[bi].Open > limitPrice)
                                ? barData[bi].Open : limitPrice;
                            entryBar = bi;
                            filled = true;
                            break;
                        }
                    }
                }

                if (!filled)
                {
                    ctExpired++;
                    if (nSkips < MAX_SKIPS)
                    {
                        SkipOut& sk = skipLog[nSkips++];
                        strncpy(sk.datetime, t.DateTime, 31);
                        sk.datetime[31] = '\0';
                        strncpy(sk.touchType,
                            (t.TouchType == 0) ? "DEMAND_EDGE" : "SUPPLY_EDGE", 15);
                        sk.touchType[15] = '\0';
                        strncpy(sk.sourceLabel, t.SourceLabel, 15);
                        sk.sourceLabel[15] = '\0';
                        sk.acalScore = score;
                        strncpy(sk.trendLabel, TrendLabelStr[trend], 3);
                        sk.trendLabel[3] = '\0';
                        strncpy(sk.skipReason, "LIMIT_EXPIRED", 23);
                        sk.skipReason[23] = '\0';
                    }
                    // Leave limitPending = true for signals during the window
                    continue;
                }

                ctFills++;
                limitPending = false;  // filled, in_trade_until takes over
                entryType = ENTRY_LIMIT_5T;
            }
            else
            {
                // WT/NT: market at next bar open
                entryBar = wtEntryBar;
                entryPrice = barData[entryBar].Open;
                entryType = ENTRY_MARKET;
            }

            // --- Compute exits ---
            int t1Ticks, t2Ticks, stopTicks;
            ComputeExits(zw, &t1Ticks, &t2Ticks, &stopTicks);

            float stopPx, t1Px, t2Px;
            if (direction == 1)
            {
                stopPx = entryPrice - stopTicks * TS;
                t1Px   = entryPrice + t1Ticks * TS;
                t2Px   = entryPrice + t2Ticks * TS;
            }
            else
            {
                stopPx = entryPrice + stopTicks * TS;
                t1Px   = entryPrice - t1Ticks * TS;
                t2Px   = entryPrice - t2Ticks * TS;
            }

            // --- Simulate 2-leg exit ---
            bool leg1Open = true, leg2Open = true;
            float leg1Pnl = 0, leg2Pnl = 0;
            int leg1Exit = EXIT_NONE, leg2Exit = EXIT_NONE;
            int leg1Bar = -1, leg2Bar = -1;
            float simMFE = 0, simMAE = 0;
            int lastBar = entryBar;

            for (int bi = entryBar; bi < nBars; bi++)
            {
                float bH = barData[bi].High;
                float bL = barData[bi].Low;
                float bC = barData[bi].Last;
                int barsHeld = bi - entryBar + 1;
                lastBar = bi;

                // MFE/MAE
                float bmfe = (direction == 1) ? (bH - entryPrice) / TS
                                              : (entryPrice - bL) / TS;
                float bmae = (direction == 1) ? (entryPrice - bL) / TS
                                              : (bH - entryPrice) / TS;
                if (bmfe > simMFE) simMFE = bmfe;
                if (bmae > simMAE) simMAE = bmae;

                // Time cap
                if (barsHeld >= ZoneBounceConfig::TIMECAP)
                {
                    float pnl = (direction == 1) ? (bC - entryPrice) / TS
                                                 : (entryPrice - bC) / TS;
                    if (leg1Open)
                    { leg1Pnl = pnl; leg1Exit = EXIT_TIMECAP; leg1Bar = bi; leg1Open = false; }
                    if (leg2Open)
                    { leg2Pnl = pnl; leg2Exit = EXIT_TIMECAP; leg2Bar = bi; leg2Open = false; }
                    break;
                }

                // Stop-first
                bool stopHit = (direction == 1) ? (bL <= stopPx) : (bH >= stopPx);
                if (stopHit)
                {
                    float pnl = (direction == 1) ? (stopPx - entryPrice) / TS
                                                 : (entryPrice - stopPx) / TS;
                    if (leg1Open)
                    { leg1Pnl = pnl; leg1Exit = EXIT_STOP; leg1Bar = bi; leg1Open = false; }
                    if (leg2Open)
                    { leg2Pnl = pnl; leg2Exit = EXIT_STOP; leg2Bar = bi; leg2Open = false; }
                    break;
                }

                // T1
                if (leg1Open)
                {
                    bool hit = (direction == 1) ? (bH >= t1Px) : (bL <= t1Px);
                    if (hit)
                    {
                        leg1Pnl = (float)t1Ticks;
                        leg1Exit = EXIT_TARGET_1;
                        leg1Bar = bi;
                        leg1Open = false;
                    }
                }

                // T2
                if (leg2Open)
                {
                    bool hit = (direction == 1) ? (bH >= t2Px) : (bL <= t2Px);
                    if (hit)
                    {
                        leg2Pnl = (float)t2Ticks;
                        leg2Exit = EXIT_TARGET_2;
                        leg2Bar = bi;
                        leg2Open = false;
                    }
                }

                if (!leg1Open && !leg2Open)
                    break;
            }

            // End of data
            if (leg1Open || leg2Open)
            {
                float lastC = barData[(lastBar < nBars) ? lastBar : nBars - 1].Last;
                float pnl = (direction == 1) ? (lastC - entryPrice) / TS
                                             : (entryPrice - lastC) / TS;
                if (leg1Open) { leg1Pnl = pnl; leg1Exit = EXIT_TIMECAP; leg1Bar = lastBar; }
                if (leg2Open) { leg2Pnl = pnl; leg2Exit = EXIT_TIMECAP; leg2Bar = lastBar; }
            }

            int finalExitBar = (leg1Bar > leg2Bar) ? leg1Bar : leg2Bar;
            int barsHeld = finalExitBar - entryBar + 1;
            float wPnl = ZoneBounceConfig::LEG1_WEIGHT * leg1Pnl
                       + ZoneBounceConfig::LEG2_WEIGHT * leg2Pnl
                       - (float)ZoneBounceConfig::COST_TICKS;

            // Update position state
            inTradeUntil = finalExitBar;

            // Kill-switch
            ksDailyPnl += wPnl;
            ksWeeklyPnl += wPnl;
            if (wPnl < 0) ksConsec++;
            else ksConsec = 0;
            if (ksConsec >= ZoneBounceConfig::KILLSWITCH_CONSEC_LOSSES)
                ksSessionHalt = true;
            if (ksDailyPnl <= (float)ZoneBounceConfig::KILLSWITCH_DAILY_TICKS)
                ksDailyHalt = true;
            if (ksWeeklyPnl <= (float)ZoneBounceConfig::KILLSWITCH_WEEKLY_TICKS)
                ksWeeklyHalt = true;

            // Store trade
            if (nTrades < MAX_TRADES)
            {
                tradeCounter++;
                TradeOut& tr = tradeLog[nTrades++];
                snprintf(tr.tradeId, sizeof(tr.tradeId), "ZB_%04d", tradeCounter);
                strncpy(tr.mode, (mode == MODE_CT) ? "CT" : "WTNT", 7);
                tr.mode[7] = '\0';
                strncpy(tr.datetime, t.DateTime, 31);
                tr.datetime[31] = '\0';
                strncpy(tr.direction, (direction == 1) ? "LONG" : "SHORT", 7);
                tr.direction[7] = '\0';
                strncpy(tr.touchType,
                    (t.TouchType == 0) ? "DEMAND_EDGE" : "SUPPLY_EDGE", 15);
                tr.touchType[15] = '\0';
                strncpy(tr.sourceLabel, t.SourceLabel, 15);
                tr.sourceLabel[15] = '\0';
                tr.zoneTop = t.ZoneTop;
                tr.zoneBot = t.ZoneBot;
                tr.zoneWidthTicks = zw;
                strncpy(tr.entryType, EntryTypeStr[entryType], 15);
                tr.entryType[15] = '\0';
                tr.entryPrice = entryPrice;
                tr.stopTicks = stopTicks;
                tr.t1Ticks = t1Ticks;
                tr.t2Ticks = t2Ticks;
                tr.stopPrice = stopPx;
                tr.t1Target = t1Px;
                tr.t2Target = t2Px;
                tr.leg1Exit = leg1Exit;
                tr.leg2Exit = leg2Exit;
                tr.leg1Pnl = leg1Pnl;
                tr.leg2Pnl = leg2Pnl;
                tr.weightedPnl = wPnl;
                tr.barsHeld = barsHeld;
                tr.mfe = simMFE;
                tr.mae = simMAE;
                tr.acalScore = score;
            }
        }

        // ---------- Write output CSVs ----------
        {
            SCString tradePath;
            tradePath.Format("%s\\ATEAM_CSV_TEST_ZONEREL_trades.csv",
                             sc.DataFilesFolder().GetChars());
            FILE* f = fopen(tradePath.GetChars(), "w");
            if (f)
            {
                fprintf(f,
                    "trade_id,mode,datetime,direction,touch_type,source_label,"
                    "zone_top,zone_bot,zone_width_ticks,"
                    "entry_type,entry_price,"
                    "stop_ticks,t1_ticks,t2_ticks,"
                    "stop_price,t1_target_price,t2_target_price,"
                    "leg1_exit_type,leg1_pnl_ticks,"
                    "leg2_exit_type,leg2_pnl_ticks,"
                    "weighted_pnl,bars_held,mfe_ticks,mae_ticks\n");
                for (int i = 0; i < nTrades; i++)
                {
                    const TradeOut& tr = tradeLog[i];
                    fprintf(f,
                        "%s,%s,%s,%s,%s,%s,"
                        "%.2f,%.2f,%d,"
                        "%s,%.2f,"
                        "%d,%d,%d,"
                        "%.2f,%.2f,%.2f,"
                        "%s,%.2f,"
                        "%s,%.2f,"
                        "%.4f,%d,%.2f,%.2f\n",
                        tr.tradeId, tr.mode, tr.datetime, tr.direction,
                        tr.touchType, tr.sourceLabel,
                        tr.zoneTop, tr.zoneBot, tr.zoneWidthTicks,
                        tr.entryType, tr.entryPrice,
                        tr.stopTicks, tr.t1Ticks, tr.t2Ticks,
                        tr.stopPrice, tr.t1Target, tr.t2Target,
                        ExitTypeStr[tr.leg1Exit], tr.leg1Pnl,
                        ExitTypeStr[tr.leg2Exit], tr.leg2Pnl,
                        tr.weightedPnl, tr.barsHeld, tr.mfe, tr.mae);
                }
                fclose(f);
            }
        }
        {
            SCString skipPath;
            skipPath.Format("%s\\ATEAM_CSV_TEST_ZONEREL_skipped.csv",
                             sc.DataFilesFolder().GetChars());
            FILE* f = fopen(skipPath.GetChars(), "w");
            if (f)
            {
                fprintf(f, "datetime,touch_type,source_label,acal_score,"
                           "trend_label,skip_reason\n");
                for (int i = 0; i < nSkips; i++)
                {
                    const SkipOut& sk = skipLog[i];
                    fprintf(f, "%s,%s,%s,%.4f,%s,%s\n",
                            sk.datetime, sk.touchType, sk.sourceLabel,
                            sk.acalScore, sk.trendLabel, sk.skipReason);
                }
                fclose(f);
            }
        }

        // ---------- Comparison against Python answer key ----------
        {
            SCString akPath;
            akPath.Format("%s..\\..\\..\\04-backtest\\zone_touch\\output\\"
                          "p1_twoleg_answer_key_zr.csv",
                          basePath.GetChars());
            FILE* ak = fopen(akPath.GetChars(), "r");

            SCString rptPath;
            rptPath.Format("%s\\ATEAM_CSV_TEST_ZONEREL_report.txt",
                           sc.DataFilesFolder().GetChars());
            FILE* rpt = fopen(rptPath.GetChars(), "w");

            if (rpt)
            {
                fprintf(rpt, "CSV TEST MODE — Replication Gate Report\n");
                fprintf(rpt, "========================================\n\n");

                // Aggregate stats
                int ctCount = 0, wtCount = 0;
                int wins = 0;
                float grossWin = 0, grossLoss = 0, totalPnl = 0;
                for (int i = 0; i < nTrades; i++)
                {
                    if (strcmp(tradeLog[i].mode, "CT") == 0) ctCount++;
                    else wtCount++;
                    if (tradeLog[i].weightedPnl > 0)
                    { wins++; grossWin += tradeLog[i].weightedPnl; }
                    else
                        grossLoss += fabs(tradeLog[i].weightedPnl);
                    totalPnl += tradeLog[i].weightedPnl;
                }
                float wr = (nTrades > 0) ? 100.0f * wins / nTrades : 0;
                float pf = (grossLoss > 0.001f) ? grossWin / grossLoss : 9999.0f;

                // Count skips
                int skBT = 0, skIP = 0, skLP = 0;
                for (int i = 0; i < nSkips; i++)
                {
                    if (strcmp(skipLog[i].skipReason, "BELOW_THRESHOLD") == 0) skBT++;
                    else if (strcmp(skipLog[i].skipReason, "IN_POSITION") == 0) skIP++;
                    else if (strcmp(skipLog[i].skipReason, "LIMIT_PENDING") == 0) skLP++;
                }

                fprintf(rpt, "Total trades:      %d  (expected: 77)\n", nTrades);
                fprintf(rpt, "CT trades:         %d  (expected: 39)\n", ctCount);
                fprintf(rpt, "WT trades:         %d  (expected: 38)\n", wtCount);
                fprintf(rpt, "CT LIMIT_EXPIRED:  %d  (expected: 2)\n", ctExpired);
                fprintf(rpt, "LIMIT_PENDING:     %d  (expected: 0)\n", skLP);
                fprintf(rpt, "IN_POSITION:       %d  (expected: 44)\n", skIP);
                fprintf(rpt, "WR:                %.1f%%  (expected: 90.9%%)\n", wr);
                fprintf(rpt, "PF:                %.2f  (expected: 10.61)\n", pf);
                fprintf(rpt, "Total PnL:         %.1ft\n\n", totalPnl);

                // Per-trade comparison
                if (ak)
                {
                    char hdr[4096];
                    fgets(hdr, sizeof(hdr), ak); // skip header

                    int matched = 0, mismatched = 0;
                    int pyIdx = 0;
                    char akLine[4096];
                    while (fgets(akLine, sizeof(akLine), ak) && pyIdx < nTrades)
                    {
                        // Parse Python answer key row
                        char pyFields[30][128];
                        int nPyF = 0;
                        {
                            char* p = akLine;
                            while (*p && nPyF < 30)
                            {
                                char* s = p;
                                while (*p && *p != ',' && *p != '\n' && *p != '\r') p++;
                                int ln = (int)(p - s);
                                if (ln >= 128) ln = 127;
                                memcpy(pyFields[nPyF], s, ln);
                                pyFields[nPyF][ln] = '\0';
                                nPyF++;
                                if (*p == ',') p++;
                            }
                        }
                        if (nPyF < 16) { pyIdx++; continue; }

                        // Answer key columns (Python replication harness format):
                        // 0:rbi, 1:mode, 2:direction, 3:zone_width,
                        // 4:entry_price, 5:weighted_pnl, 6:bars_held,
                        // 7:leg1_exit, 8:leg1_pnl, 9:leg2_exit, 10:leg2_pnl,
                        // 11:mfe, 12:mae, 13:t1_ticks, 14:t2_ticks,
                        // 15:stop_ticks, 16:entry_bar, 17:exit_bar,
                        // 18:score, 19:trend, 20:tf

                        const TradeOut& ct = tradeLog[pyIdx];
                        float pyEp = (float)atof(pyFields[4]);
                        float pyWpnl = (float)atof(pyFields[5]);
                        int pyZw = atoi(pyFields[3]);
                        int pySt = atoi(pyFields[15]);
                        int pyT1 = atoi(pyFields[13]);
                        int pyT2 = atoi(pyFields[14]);

                        bool epOk = fabs(ct.entryPrice - pyEp) < 0.02f;
                        bool zwOk = ct.zoneWidthTicks == pyZw;
                        bool stOk = ct.stopTicks == pySt;
                        bool t1Ok = ct.t1Ticks == pyT1;
                        bool t2Ok = ct.t2Ticks == pyT2;
                        bool l1Ok = (strcmp(ExitTypeStr[ct.leg1Exit], pyFields[7]) == 0);
                        bool l2Ok = (strcmp(ExitTypeStr[ct.leg2Exit], pyFields[9]) == 0);
                        bool pnlOk = fabs(ct.weightedPnl - pyWpnl) < 1.0f;

                        if (epOk && zwOk && stOk && t1Ok && t2Ok && l1Ok && l2Ok && pnlOk)
                            matched++;
                        else
                        {
                            mismatched++;
                            if (mismatched <= 5)
                            {
                                fprintf(rpt, "MISMATCH trade %d (%s):\n", pyIdx + 1, ct.tradeId);
                                if (!epOk) fprintf(rpt, "  entry_price: C++=%10.2f  Py=%10.2f\n",
                                                   ct.entryPrice, pyEp);
                                if (!zwOk) fprintf(rpt, "  zone_width:  C++=%d  Py=%d\n",
                                                   ct.zoneWidthTicks, pyZw);
                                if (!stOk) fprintf(rpt, "  stop_ticks:  C++=%d  Py=%d\n",
                                                   ct.stopTicks, pySt);
                                if (!t1Ok) fprintf(rpt, "  t1_ticks:    C++=%d  Py=%d\n",
                                                   ct.t1Ticks, pyT1);
                                if (!t2Ok) fprintf(rpt, "  t2_ticks:    C++=%d  Py=%d\n",
                                                   ct.t2Ticks, pyT2);
                                if (!l1Ok) fprintf(rpt, "  leg1_exit:   C++=%s  Py=%s\n",
                                                   ExitTypeStr[ct.leg1Exit], pyFields[7]);
                                if (!l2Ok) fprintf(rpt, "  leg2_exit:   C++=%s  Py=%s\n",
                                                   ExitTypeStr[ct.leg2Exit], pyFields[9]);
                                if (!pnlOk) fprintf(rpt, "  weighted_pnl: C++=%.4f  Py=%.4f\n",
                                                    ct.weightedPnl, pyWpnl);
                                fprintf(rpt, "\n");
                            }
                        }
                        pyIdx++;
                    }

                    fprintf(rpt, "Per-trade comparison: %d/%d matched, %d mismatched\n\n",
                            matched, nTrades, mismatched);

                    if (mismatched == 0 && matched == nTrades && nTrades == 77)
                        fprintf(rpt, "VERDICT: PASS (77/77 trades matched)\n");
                    else
                        fprintf(rpt, "VERDICT: FAIL (%d matched, %d mismatched, %d total)\n",
                                matched, mismatched, nTrades);

                    fclose(ak);
                }
                else
                {
                    fprintf(rpt, "Answer key not found — skipping per-trade comparison.\n");
                    fprintf(rpt, "VERDICT: MANUAL CHECK REQUIRED\n");
                }

                fclose(rpt);
            }
        }

        // Cleanup
        sc.FreeMemory(barData);
        sc.FreeMemory(touches);
        sc.FreeMemory(tradeLog);
        sc.FreeMemory(skipLog);

        {
            SCString msg;
            msg.Format("CSV TEST MODE: Complete. %d trades, %d skips. "
                       "See ATEAM_CSV_TEST_ZONEREL_report.txt", nTrades, nSkips);
            sc.AddMessageToLog(msg, 0);
        }
        return;  // CSV test mode done — skip normal operation
    }

    // =================================================================
    //  Early-out: not enough bars
    // =================================================================
    if (sc.Index < 2)
        return;

    // =================================================================
    //  Replay-start safe boundary (M1B pattern)
    //  Orders are only submitted for bars at or after the replay start.
    //  Prevents SC from rejecting historical-bar orders during recalc.
    // =================================================================
    int& safeBar = sc.GetPersistentInt(1);

    if (sc.IsFullRecalculation && sc.Index == 0)
        safeBar = -1;

    if (safeBar < 0 && sc.UpdateStartIndex > 0)
        safeBar = sc.UpdateStartIndex;

    // =================================================================
    //  Get or initialize persistent state
    // =================================================================
    StudyState* pState = (StudyState*)sc.GetPersistentPointer(0);
    if (pState == nullptr)
    {
        pState = (StudyState*)sc.AllocateMemory(sizeof(StudyState));
        if (pState == nullptr) return;
        memset(pState, 0, sizeof(StudyState));
        pState->Magic = STUDY_STATE_MAGIC;
        sc.SetPersistentPointer(0, pState);
    }
    if (pState->Magic != STUDY_STATE_MAGIC)
    {
        memset(pState, 0, sizeof(StudyState));
        pState->Magic = STUDY_STATE_MAGIC;
    }

    // =================================================================
    //  Build slot-to-TF lookup
    // =================================================================
    int slotTF[9];
    slotTF[0] = Input_Slot0TF.GetInt();
    slotTF[1] = Input_Slot1TF.GetInt();
    slotTF[2] = Input_Slot2TF.GetInt();
    slotTF[3] = Input_Slot3TF.GetInt();
    slotTF[4] = Input_Slot4TF.GetInt();
    slotTF[5] = Input_Slot5TF.GetInt();
    slotTF[6] = Input_Slot6TF.GetInt();
    slotTF[7] = Input_Slot7TF.GetInt();
    slotTF[8] = Input_Slot8TF.GetInt();

    // =================================================================
    //  Read ZBV4 SignalStorage
    // =================================================================
    int zbv4ID = Input_ZBV4StudyID.GetInt();
    void* rawPtr = sc.GetPersistentPointerFromChartStudy(
        sc.ChartNumber, zbv4ID, 0);

    // --- DIAGNOSTIC: write to file on last bar only ---
    if (sc.Index == sc.ArraySize - 1)
    {
        SCString diagPath;
        diagPath.Format("%s\\ATEAM_DIAGNOSTIC.txt",
                         sc.DataFilesFolder().GetChars());
        FILE* diag = fopen(diagPath.GetChars(), "w");
        if (diag)
        {
            fprintf(diag, "ATEAM_ZONE_BOUNCE_V1 v3.0 Diagnostic\n");
            fprintf(diag, "ChartNumber: %d\n", sc.ChartNumber);
            fprintf(diag, "zbv4ID (Input[0]): %d\n", zbv4ID);
            fprintf(diag, "rawPtr: %p\n", rawPtr);
            if (rawPtr != nullptr)
            {
                SignalStorage* tmp = (SignalStorage*)rawPtr;
                fprintf(diag, "MagicNumber: 0x%08X (expected 0x%08X)\n",
                        tmp->MagicNumber, ZoneBounceConfig::ZBV4_STORAGE_MAGIC);
                fprintf(diag, "SignalCount: %d\n", tmp->SignalCount);
                fprintf(diag, "ZoneCount: %d\n", tmp->ZoneCount);
            }
            fprintf(diag, "LastProcessedSignalCount: %d\n",
                    pState->LastProcessedSignalCount);
            fprintf(diag, "sc.ArraySize: %d\n", sc.ArraySize);
            fprintf(diag, "Input_Enabled: %d\n", Input_Enabled.GetBoolean());
            fprintf(diag, "Input_CSVLogging: %d\n", Input_CSVLogging.GetBoolean());
            fclose(diag);
        }
    }

    if (rawPtr == nullptr) return;

    SignalStorage* storage = (SignalStorage*)rawPtr;
    if (storage->MagicNumber != ZoneBounceConfig::ZBV4_STORAGE_MAGIC)
        return;

    // =================================================================
    //  Helper: Resolve TF minutes from SourceSlot
    // =================================================================
    auto GetTFMinutes = [&](int sourceSlot) -> int
    {
        if (sourceSlot < 0 || sourceSlot > 8) return 0;
        return slotTF[sourceSlot];
    };

    auto GetTFLabel = [&](int tfMinutes) -> const char*
    {
        switch (tfMinutes)
        {
            case 15:  return "15m";
            case 30:  return "30m";
            case 60:  return "60m";
            case 90:  return "90m";
            case 120: return "120m";
            case 240: return "240m";
            case 360: return "360m";
            case 480: return "480m";
            default:  return "UNK";
        }
    };

    // =================================================================
    //  Helper: Numeric bin scoring (A-Cal)
    //  Convention: Low <= p33 -> full weight, Mid -> half, High >= p67 -> 0
    // =================================================================
    auto BinNumeric = [](float value, float p33, float p67, float weight,
                         bool isNaN) -> float
    {
        if (isNaN) return 0.0f;
        if (value <= p33) return weight;           // best bin
        if (value >= p67) return 0.0f;             // worst bin
        return weight / 2.0f;                      // mid bin
    };

    auto BinIndex = [](float value, float p33, float p67, bool isNaN) -> int
    {
        if (isNaN) return -1;      // null bin
        if (value <= p33) return 0; // low
        if (value >= p67) return 2; // high
        return 1;                   // mid
    };

    // =================================================================
    //  Helper: F04 Cascade State scoring
    //  CascadeState enum: 0=PRIOR_HELD, 1=NO_PRIOR, 2=PRIOR_BROKE
    // =================================================================
    auto ScoreF04 = [](int cascadeState) -> float
    {
        switch (cascadeState)
        {
            case 1:  return ZoneBounceConfig::F04_PTS_NO_PRIOR;    // best
            case 0:  return ZoneBounceConfig::F04_PTS_PRIOR_HELD;  // mid
            case 2:  return ZoneBounceConfig::F04_PTS_PRIOR_BROKE; // worst
            default: return 0.0f;
        }
    };

    auto BinF04 = [](int cascadeState) -> int
    {
        switch (cascadeState)
        {
            case 1:  return 2;  // NO_PRIOR = best = "high bin index"
            case 0:  return 1;  // PRIOR_HELD = mid
            case 2:  return 0;  // PRIOR_BROKE = worst
            default: return -1;
        }
    };

    // =================================================================
    //  Helper: F01 Timeframe scoring
    // =================================================================
    auto ScoreF01 = [](int tfMinutes) -> float
    {
        if (tfMinutes == ZoneBounceConfig::F01_BEST_TF_MIN)
            return ZoneBounceConfig::F01_PTS_BEST;    // 30m = 3.44
        if (tfMinutes == 480)
            return ZoneBounceConfig::F01_PTS_WORST;   // 480m = 0
        if (tfMinutes > 0)
            return ZoneBounceConfig::F01_PTS_MID;     // others = 1.72
        return 0.0f;                                  // unknown
    };

    auto BinF01 = [](int tfMinutes) -> int
    {
        if (tfMinutes == 30)  return 2;  // best
        if (tfMinutes == 480) return 0;  // worst
        if (tfMinutes > 0)    return 1;  // mid
        return -1;
    };

    // =================================================================
    //  Helper: Classify trend label (non-direction-aware)
    //  slope <= P33 -> CT, slope >= P67 -> WT, else NT
    // =================================================================
    auto ClassifyTrend = [](float slope, int /*touchType*/) -> TrendLabel
    {
        if (slope <= ZoneBounceConfig::TREND_P33) return TREND_CT;
        if (slope >= ZoneBounceConfig::TREND_P67) return TREND_WT;
        return TREND_NT;
    };

    // =================================================================
    //  Helper: Find prior touch signal for F10
    //  Searches backward in SignalStorage for same zone, seq-1
    // =================================================================
    auto FindPriorPenetration = [&](const SignalRecord& sig,
                                     int sigIdx) -> float
    {
        if (sig.TouchSequence <= 1) return -1.0f;  // no prior, return NaN sentinel

        // Match zone by ZoneTop/ZoneBot (within tolerance)
        const float tol = 0.01f;
        for (int i = sigIdx - 1; i >= 0; i--)
        {
            const SignalRecord& prev = storage->Signals[i];
            if (!prev.Active) continue;
            if (prev.Type != sig.Type) continue;
            if (fabs(prev.ZoneTop - sig.ZoneTop) > tol) continue;
            if (fabs(prev.ZoneBot - sig.ZoneBot) > tol) continue;
            if (prev.TouchSequence == sig.TouchSequence - 1)
            {
                return prev.PenetrationTicks;
            }
        }
        return -1.0f;  // not found, treat as NaN
    };

    // =================================================================
    //  Helper: Detect SBB (same-bar-break)
    // =================================================================
    auto IsSBB = [&](const SignalRecord& sig) -> bool
    {
        // Check if zone died on the same bar it was touched
        for (int z = 0; z < storage->ZoneCount; z++)
        {
            const TrackedZone& zone = storage->Zones[z];
            if (fabs(zone.Top - sig.ZoneTop) < 0.01f &&
                fabs(zone.Bot - sig.ZoneBot) < 0.01f)
            {
                return (zone.DeathBar == sig.BarIndex && zone.DeathBar > 0);
            }
        }
        return false;
    };

    // =================================================================
    //  Helper: Detect session (RTH vs ETH)
    // =================================================================
    auto GetSession = [&](int barIndex) -> const char*
    {
        SCDateTime barDT = sc.BaseDateTimeIn[barIndex];
        int hour, minute, second;
        barDT.GetTimeHMS(hour, minute, second);
        int hhmm = hour * 100 + minute;
        // RTH: 09:30 - 16:15 ET
        if (hhmm >= 930 && hhmm < 1615) return "RTH";
        return "ETH";
    };

    // =================================================================
    //  Helper: Compute zone-relative exit parameters
    // =================================================================
    auto ComputeZoneRelativeExits = [](int zoneWidthTicks,
                                       int* outT1, int* outT2, int* outStop)
    {
        *outT1   = (int)(ZoneBounceConfig::T1_MULT * zoneWidthTicks + 0.5f);
        *outT2   = (int)(ZoneBounceConfig::T2_MULT * zoneWidthTicks + 0.5f);
        int rawStop = (int)(ZoneBounceConfig::STOP_MULT * zoneWidthTicks + 0.5f);
        *outStop = (rawStop > ZoneBounceConfig::STOP_FLOOR)
                   ? rawStop : ZoneBounceConfig::STOP_FLOOR;
    };

    // =================================================================
    //  CSV Logging helpers
    // =================================================================

    auto OpenTradeLog = [&]() -> FILE*
    {
        if (!Input_CSVLogging.GetBoolean()) return nullptr;
        SCString path;
        path.Format("%s\\ATEAM_ZONE_BOUNCE_V1_trades.csv",
                     sc.DataFilesFolder().GetChars());

        int needHeader = 0;
        if (pState->TradeLogHeaderWritten == 0)
        {
            FILE* fCheck = fopen(path.GetChars(), "r");
            if (fCheck == nullptr) { needHeader = 1; }
            else
            {
                fseek(fCheck, 0, SEEK_END);
                if (ftell(fCheck) == 0) needHeader = 1;
                else pState->TradeLogHeaderWritten = 1;
                fclose(fCheck);
            }
        }
        FILE* f = fopen(path.GetChars(), "a");
        if (f == nullptr) return nullptr;
        if (needHeader)
        {
            fprintf(f,
                "trade_id,mode,datetime,direction,touch_type,source_label,"
                "touch_sequence,"
                "zone_top,zone_bot,zone_width_ticks,"
                "F10_raw,F04_raw,F01_raw,F21_raw,"
                "F10_bin,F04_bin,F01_bin,F21_bin,"
                "F10_points,F04_points,F01_points,F21_points,"
                "acal_score,score_margin,trend_slope,trend_label,"
                "sbb_label,session,"
                "entry_type,limit_depth_ticks,"
                "entry_bar_index,entry_price,stop_price,"
                "stop_ticks,t1_ticks,t2_ticks,"
                "t1_target_price,t2_target_price,"
                "leg1_exit_type,leg1_exit_price,leg1_exit_bar,leg1_pnl_ticks,"
                "leg2_exit_type,leg2_exit_price,leg2_exit_bar,leg2_pnl_ticks,"
                "weighted_pnl,bars_held,mfe_ticks,mae_ticks,"
                "slippage_ticks,latency_ms\n");
            pState->TradeLogHeaderWritten = 1;
        }
        return f;
    };

    auto OpenSignalLog = [&]() -> FILE*
    {
        if (!Input_CSVLogging.GetBoolean()) return nullptr;
        SCString path;
        path.Format("%s\\ATEAM_ZONE_BOUNCE_V1_signals.csv",
                     sc.DataFilesFolder().GetChars());

        int needHeader = 0;
        if (pState->SignalLogHeaderWritten == 0)
        {
            FILE* fCheck = fopen(path.GetChars(), "r");
            if (fCheck == nullptr) { needHeader = 1; }
            else
            {
                fseek(fCheck, 0, SEEK_END);
                if (ftell(fCheck) == 0) needHeader = 1;
                else pState->SignalLogHeaderWritten = 1;
                fclose(fCheck);
            }
        }
        FILE* f = fopen(path.GetChars(), "a");
        if (f == nullptr) return nullptr;
        if (needHeader)
        {
            fprintf(f,
                "datetime,touch_type,source_label,"
                "zone_width_ticks,touch_sequence,"
                "acal_score,score_margin,"
                "trend_label,sbb_label,action,skip_reason,"
                "current_position_pnl\n");
            pState->SignalLogHeaderWritten = 1;
        }
        return f;
    };

    auto FormatDateTime = [&](int barIndex, char* buf, int bufSize)
    {
        SCDateTime dt = sc.BaseDateTimeIn[barIndex];
        int y, mo, d, h, mi, s;
        dt.GetDateTimeYMDHMS(y, mo, d, h, mi, s);
        snprintf(buf, bufSize, "%04d-%02d-%02d %02d:%02d:%02d",
                 y, mo, d, h, mi, s);
    };

    // =================================================================
    //  Helper: Draw level lines for a trade
    // =================================================================
    auto DrawLevelLines = [&](int drawIdx, float entryPrice,
                               float stopPrice, float t1Price, float t2Price,
                               bool singleLeg, int entryBar)
    {
        int endBar = entryBar + ZoneBounceConfig::TIMECAP;

        auto DrawLevel = [&](int lineBase, float price, COLORREF color)
        {
            s_UseTool tool;
            tool.Clear();
            tool.ChartNumber = sc.ChartNumber;
            tool.DrawingType = DRAWING_LINE;
            tool.AddMethod   = UTAM_ADD_OR_ADJUST;
            tool.LineNumber  = lineBase + drawIdx;
            tool.Region      = 0;
            tool.BeginIndex  = entryBar;
            tool.BeginValue  = price;
            tool.EndIndex    = endBar;
            tool.EndValue    = price;
            tool.Color       = color;
            tool.LineStyle   = LINESTYLE_DASH;
            tool.LineWidth   = 1;
            tool.AddAsUserDrawnDrawing = 0;
            sc.UseTool(tool);
        };

        DrawLevel(ZoneBounceConfig::LN_ZB1_ENTRY, entryPrice, RGB(255, 255, 255));
        DrawLevel(ZoneBounceConfig::LN_ZB1_STOP,  stopPrice, RGB(200, 0, 0));
        DrawLevel(ZoneBounceConfig::LN_ZB1_T1,    t1Price, RGB(0, 180, 0));
        if (!singleLeg)
            DrawLevel(ZoneBounceConfig::LN_ZB1_T2, t2Price, RGB(0, 120, 255));
    };

    // =================================================================
    //  Helper: Delete level lines for a trade
    // =================================================================
    auto DeleteLevelLines = [&](int drawIdx)
    {
        sc.DeleteACSChartDrawing(sc.ChartNumber, TOOL_DELETE_CHARTDRAWING,
            ZoneBounceConfig::LN_ZB1_ENTRY + drawIdx);
        sc.DeleteACSChartDrawing(sc.ChartNumber, TOOL_DELETE_CHARTDRAWING,
            ZoneBounceConfig::LN_ZB1_STOP + drawIdx);
        sc.DeleteACSChartDrawing(sc.ChartNumber, TOOL_DELETE_CHARTDRAWING,
            ZoneBounceConfig::LN_ZB1_T1 + drawIdx);
        sc.DeleteACSChartDrawing(sc.ChartNumber, TOOL_DELETE_CHARTDRAWING,
            ZoneBounceConfig::LN_ZB1_T2 + drawIdx);
    };

    // =================================================================
    //  Helper: Establish position from entry price (shared by market + limit)
    // =================================================================
    auto EstablishPosition = [&](float entryPrice, int direction,
                                  EntryType entryType, TradeMode mode,
                                  int signalIdx, int zoneWidthTicks,
                                  float zoneTop, float zoneBot,
                                  int leg1Qty, int leg2Qty, bool singleLeg,
                                  int limitDepthTicks)
    {
        const float tickSize = ZoneBounceConfig::TICK_SIZE;
        PositionState& pos = pState->Position;

        int t1Ticks, t2Ticks, stopTicks;
        ComputeZoneRelativeExits(zoneWidthTicks, &t1Ticks, &t2Ticks, &stopTicks);

        float t1Price, t2Price, stopPrice;
        if (direction == 1)  // LONG
        {
            t1Price   = entryPrice + t1Ticks * tickSize;
            t2Price   = entryPrice + t2Ticks * tickSize;
            stopPrice = entryPrice - stopTicks * tickSize;
        }
        else  // SHORT
        {
            t1Price   = entryPrice - t1Ticks * tickSize;
            t2Price   = entryPrice - t2Ticks * tickSize;
            stopPrice = entryPrice + stopTicks * tickSize;
        }

        pos.InTrade        = true;
        pos.Mode           = mode;
        pos.Entry          = entryType;
        pos.Direction      = direction;
        pos.EntryPrice     = entryPrice;
        pos.EntryBar       = sc.Index;
        pos.StopPrice      = stopPrice;
        pos.ZoneTop        = zoneTop;
        pos.ZoneBot        = zoneBot;
        pos.ZoneWidthTicks = zoneWidthTicks;
        pos.StopTicks      = stopTicks;
        pos.T1Ticks        = t1Ticks;
        pos.T2Ticks        = t2Ticks;
        pos.LimitDepthTicks = limitDepthTicks;
        pos.MFE            = 0.0f;
        pos.MAE            = 0.0f;
        pos.SignalIdx      = signalIdx;

        pos.Leg1.Active      = true;
        pos.Leg1.Contracts   = leg1Qty;
        pos.Leg1.TargetPrice = t1Price;
        pos.Leg1.ExitResult  = EXIT_NONE;
        pos.Leg1.PnlTicks   = 0.0f;

        if (!singleLeg)
        {
            pos.Leg2.Active      = true;
            pos.Leg2.Contracts   = leg2Qty;
            pos.Leg2.TargetPrice = t2Price;
            pos.Leg2.ExitResult  = EXIT_NONE;
            pos.Leg2.PnlTicks   = 0.0f;
        }
        else
        {
            memset(&pos.Leg2, 0, sizeof(LegState));
        }

        // Submit orders to SC trade service with OCO groups
        {
            s_SCNewOrder entryOrder;
            entryOrder.OrderType = SCT_ORDERTYPE_MARKET;
            entryOrder.TimeInForce = SCT_TIF_GTC;
            entryOrder.OrderQuantity = leg1Qty + leg2Qty;

            entryOrder.OCOGroup1Quantity = leg1Qty;
            entryOrder.Target1Offset     = t1Ticks * tickSize;
            entryOrder.Stop1Offset       = stopTicks * tickSize;

            if (!singleLeg)
            {
                entryOrder.OCOGroup2Quantity = leg2Qty;
                entryOrder.Target2Offset     = t2Ticks * tickSize;
                entryOrder.Stop2Offset       = stopTicks * tickSize;
            }

            int result;
            if (direction == 1)
                result = (int)sc.BuyEntry(entryOrder);
            else
                result = (int)sc.SellEntry(entryOrder);

            // Diagnostic
            SCString diagPath;
            diagPath.Format("%s\\ATEAM_ORDER_LOG.txt",
                             sc.DataFilesFolder().GetChars());
            FILE* diag = fopen(diagPath.GetChars(), "a");
            if (diag)
            {
                char dtBuf[32];
                FormatDateTime(sc.Index, dtBuf, sizeof(dtBuf));
                fprintf(diag,
                    "%s dir=%d qty=%d result=%d entry=%s zw=%d "
                    "t1=%d t2=%d stop=%d\n",
                    dtBuf, direction, leg1Qty + leg2Qty, result,
                    EntryTypeStr[entryType], zoneWidthTicks,
                    t1Ticks, t2Ticks, stopTicks);
                fclose(diag);
            }
        }

        // Visual: signal arrow
        if (signalIdx >= 0 && signalIdx < storage->SignalCount)
        {
            const SignalRecord& sig = storage->Signals[signalIdx];
            SG_Signal[sig.BarIndex] = sig.TouchPrice;
            SG_Signal.DataColor[sig.BarIndex] =
                (mode == MODE_CT) ? RGB(0, 200, 0) : RGB(0, 128, 255);
            SG_Signal.DrawStyle = (direction == 1)
                ? DRAWSTYLE_ARROW_UP : DRAWSTYLE_ARROW_DOWN;
            SG_EntryPrice[sc.Index] = entryPrice;

            // Mode + score label
            {
                const char* modeStr = (mode == MODE_CT) ? "CT" : "WT";
                float acalScore = SG_Score[sig.BarIndex];
                SCString text;
                text.Format("%s %.1f", modeStr, acalScore);

                bool isDemand = (direction == 1);
                float barLow  = sc.BaseData[SC_LOW][sig.BarIndex];
                float barHigh = sc.BaseData[SC_HIGH][sig.BarIndex];

                s_UseTool tool;
                tool.Clear();
                tool.ChartNumber = sc.ChartNumber;
                tool.DrawingType = DRAWING_TEXT;
                tool.AddMethod   = UTAM_ADD_OR_ADJUST;
                tool.LineNumber  = ZoneBounceConfig::LN_ZB1_LABEL + pState->DrawCount;
                tool.Region      = 0;
                tool.BeginIndex  = sig.BarIndex;
                tool.BeginValue  = isDemand
                    ? barLow - (60 * tickSize)
                    : barHigh + (60 * tickSize);
                tool.Text        = text;
                tool.Color       = (mode == MODE_CT)
                    ? RGB(0, 200, 0) : RGB(0, 128, 255);
                tool.FontSize    = 9;
                tool.FontBold    = 1;
                tool.TextAlignment = DT_CENTER | (isDemand ? DT_TOP : DT_BOTTOM);
                tool.TransparentLabelBackground = 1;
                tool.AddAsUserDrawnDrawing = 0;
                sc.UseTool(tool);
            }
        }

        // Draw level lines
        {
            int drawIdx = pState->DrawCount++;
            pos.DrawIdx = drawIdx;
            DrawLevelLines(drawIdx, entryPrice, stopPrice, t1Price, t2Price,
                          singleLeg, sc.Index);
        }
    };

    // =================================================================
    //  Resolve WT/NT market pending entry: establish position at bar open
    // =================================================================
    PositionState& pos = pState->Position;
    const float tickSize = ZoneBounceConfig::TICK_SIZE;

    if (pState->MarketPending.Active && !pos.InTrade)
    {
        MarketPendingState& mp = pState->MarketPending;
        float entryPrice = sc.BaseData[SC_OPEN][sc.Index];

        EstablishPosition(entryPrice, mp.Direction, ENTRY_MARKET, MODE_WTNT,
                          mp.SignalIdx, mp.ZoneWidthTicks,
                          mp.ZoneTop, mp.ZoneBot,
                          mp.Leg1Qty, mp.Leg2Qty, mp.SingleLeg, 0);

        mp.Active = false;
    }

    // =================================================================
    //  CT Limit order management
    //  Check if pending limit fills this bar, or if deadline expired.
    // =================================================================
    if (pState->LimitPending.Active && !pos.InTrade)
    {
        LimitPendingState& lp = pState->LimitPending;

        // Check 16:55 flatten — cancel pending limit
        {
            SCDateTime barDT = sc.BaseDateTimeIn[sc.Index];
            int hour, minute, second;
            barDT.GetTimeHMS(hour, minute, second);
            int hhmm = hour * 100 + minute;
            if (hhmm >= (ZoneBounceConfig::FLATTEN_HOUR * 100 +
                         ZoneBounceConfig::FLATTEN_MINUTE))
            {
                // Log as LIMIT_EXPIRED (EOD)
                if (Input_CSVLogging.GetBoolean() &&
                    lp.SignalIdx >= 0 && lp.SignalIdx < storage->SignalCount)
                {
                    const SignalRecord& sig = storage->Signals[lp.SignalIdx];
                    FILE* f = OpenSignalLog();
                    if (f)
                    {
                        char dtBuf[32];
                        FormatDateTime(sig.BarIndex, dtBuf, sizeof(dtBuf));
                        int tfMin = GetTFMinutes(sig.SourceSlot);
                        fprintf(f,
                            "%s,%s,%s,%d,%d,%.4f,%.4f,%s,%s,%s,%s,%.2f\n",
                            dtBuf,
                            (sig.Type == 0) ? "DEMAND_EDGE" : "SUPPLY_EDGE",
                            GetTFLabel(tfMin),
                            lp.ZoneWidthTicks, sig.TouchSequence,
                            0.0f, 0.0f,
                            "CT", "NORMAL", "SKIP", "LIMIT_EXPIRED", 0.0f);
                        fclose(f);
                    }
                }
                lp.Active = false;
                goto done_limit_check;
            }
        }

        // Check deadline expiry
        if (sc.Index > lp.DeadlineBar)
        {
            // Log LIMIT_EXPIRED
            if (Input_CSVLogging.GetBoolean() &&
                lp.SignalIdx >= 0 && lp.SignalIdx < storage->SignalCount)
            {
                const SignalRecord& sig = storage->Signals[lp.SignalIdx];
                FILE* f = OpenSignalLog();
                if (f)
                {
                    char dtBuf[32];
                    FormatDateTime(sig.BarIndex, dtBuf, sizeof(dtBuf));
                    int tfMin = GetTFMinutes(sig.SourceSlot);
                    float priorPen = FindPriorPenetration(sig, lp.SignalIdx);
                    bool f10IsNaN = (priorPen < 0.0f);
                    float f10Raw = f10IsNaN ? 0.0f : priorPen;
                    float f10Pts = BinNumeric(f10Raw, ZoneBounceConfig::F10_BIN_P33,
                        ZoneBounceConfig::F10_BIN_P67, ZoneBounceConfig::F10_WEIGHT,
                        f10IsNaN);
                    float f04Pts = ScoreF04(sig.CascadeState);
                    float f01Pts = ScoreF01(tfMin);
                    float f21Pts = BinNumeric((float)sig.ZoneAgeBars,
                        ZoneBounceConfig::F21_BIN_P33, ZoneBounceConfig::F21_BIN_P67,
                        ZoneBounceConfig::F21_WEIGHT, false);
                    float score = f10Pts + f04Pts + f01Pts + f21Pts;

                    fprintf(f,
                        "%s,%s,%s,%d,%d,%.4f,%.4f,%s,%s,%s,%s,%.2f\n",
                        dtBuf,
                        (sig.Type == 0) ? "DEMAND_EDGE" : "SUPPLY_EDGE",
                        GetTFLabel(tfMin),
                        lp.ZoneWidthTicks, sig.TouchSequence,
                        score, score - ZoneBounceConfig::SCORE_THRESHOLD,
                        "CT",
                        IsSBB(sig) ? "SBB" : "NORMAL",
                        "SKIP", "LIMIT_EXPIRED", 0.0f);
                    fclose(f);
                }
            }
            lp.Active = false;
            goto done_limit_check;
        }

        // Check if limit fills this bar
        // For LONG (demand): bar low <= limit price -> filled
        // For SHORT (supply): bar high >= limit price -> filled
        {
            float high = sc.BaseData[SC_HIGH][sc.Index];
            float low  = sc.BaseData[SC_LOW][sc.Index];
            bool filled = false;

            if (lp.Direction == 1)  // LONG
                filled = (low <= lp.LimitPrice);
            else                    // SHORT
                filled = (high >= lp.LimitPrice);

            if (filled)
            {
                EstablishPosition(lp.LimitPrice, lp.Direction,
                                  ENTRY_LIMIT_5T, MODE_CT,
                                  lp.SignalIdx, lp.ZoneWidthTicks,
                                  lp.ZoneTop, lp.ZoneBot,
                                  lp.Leg1Qty, lp.Leg2Qty, lp.SingleLeg,
                                  ZoneBounceConfig::CT_LIMIT_DEPTH_TICKS);
                lp.Active = false;
            }
        }
    }
done_limit_check:

    // =================================================================
    //  Position management: check exits on current bar
    // =================================================================
    if (pos.InTrade)
    {
        float high = sc.BaseData[SC_HIGH][sc.Index];
        float low  = sc.BaseData[SC_LOW][sc.Index];
        float last = sc.BaseData[SC_LAST][sc.Index];

        // Track MFE/MAE
        float favorableExcursion = 0.0f;
        float adverseExcursion   = 0.0f;
        if (pos.Direction == 1) // LONG
        {
            favorableExcursion = (high - pos.EntryPrice) / tickSize;
            adverseExcursion   = (pos.EntryPrice - low) / tickSize;
        }
        else // SHORT
        {
            favorableExcursion = (pos.EntryPrice - low) / tickSize;
            adverseExcursion   = (high - pos.EntryPrice) / tickSize;
        }
        if (favorableExcursion > pos.MFE) pos.MFE = favorableExcursion;
        if (adverseExcursion > pos.MAE)   pos.MAE = adverseExcursion;

        // --- Check 16:55 ET flatten ---
        {
            SCDateTime barDT = sc.BaseDateTimeIn[sc.Index];
            int hour, minute, second;
            barDT.GetTimeHMS(hour, minute, second);
            int hhmm = hour * 100 + minute;
            if (hhmm >= (ZoneBounceConfig::FLATTEN_HOUR * 100 +
                         ZoneBounceConfig::FLATTEN_MINUTE))
            {
                if (pos.Leg1.Active)
                {
                    pos.Leg1.Active = false;
                    pos.Leg1.ExitResult = EXIT_FLATTEN_EOD;
                    pos.Leg1.ExitPrice = last;
                    pos.Leg1.ExitBar = sc.Index;
                    float raw = (pos.Direction == 1)
                        ? (last - pos.EntryPrice) / tickSize
                        : (pos.EntryPrice - last) / tickSize;
                    pos.Leg1.PnlTicks = raw;
                }
                if (pos.Leg2.Active)
                {
                    pos.Leg2.Active = false;
                    pos.Leg2.ExitResult = EXIT_FLATTEN_EOD;
                    pos.Leg2.ExitPrice = last;
                    pos.Leg2.ExitBar = sc.Index;
                    float raw = (pos.Direction == 1)
                        ? (last - pos.EntryPrice) / tickSize
                        : (pos.EntryPrice - last) / tickSize;
                    pos.Leg2.PnlTicks = raw;
                }
                goto position_closed;
            }
        }

        // --- Check time cap ---
        {
            int barsHeld = sc.Index - pos.EntryBar;
            if (barsHeld >= ZoneBounceConfig::TIMECAP)
            {
                if (pos.Leg1.Active)
                {
                    pos.Leg1.Active = false;
                    pos.Leg1.ExitResult = EXIT_TIMECAP;
                    pos.Leg1.ExitPrice = last;
                    pos.Leg1.ExitBar = sc.Index;
                    float raw = (pos.Direction == 1)
                        ? (last - pos.EntryPrice) / tickSize
                        : (pos.EntryPrice - last) / tickSize;
                    pos.Leg1.PnlTicks = raw;
                }
                if (pos.Leg2.Active)
                {
                    pos.Leg2.Active = false;
                    pos.Leg2.ExitResult = EXIT_TIMECAP;
                    pos.Leg2.ExitPrice = last;
                    pos.Leg2.ExitBar = sc.Index;
                    float raw = (pos.Direction == 1)
                        ? (last - pos.EntryPrice) / tickSize
                        : (pos.EntryPrice - last) / tickSize;
                    pos.Leg2.PnlTicks = raw;
                }
                goto position_closed;
            }
        }

        // --- Check stop (STOP-FIRST rule: check stop before targets) ---
        {
            bool stopHit = false;
            if (pos.Direction == 1) // LONG: stop if low <= stop
                stopHit = (low <= pos.StopPrice);
            else                    // SHORT: stop if high >= stop
                stopHit = (high >= pos.StopPrice);

            if (stopHit)
            {
                if (pos.Leg1.Active)
                {
                    pos.Leg1.Active = false;
                    pos.Leg1.ExitResult = EXIT_STOP;
                    pos.Leg1.ExitPrice = pos.StopPrice;
                    pos.Leg1.ExitBar = sc.Index;
                    float raw = (pos.Direction == 1)
                        ? (pos.StopPrice - pos.EntryPrice) / tickSize
                        : (pos.EntryPrice - pos.StopPrice) / tickSize;
                    pos.Leg1.PnlTicks = raw;
                }
                if (pos.Leg2.Active)
                {
                    pos.Leg2.Active = false;
                    pos.Leg2.ExitResult = EXIT_STOP;
                    pos.Leg2.ExitPrice = pos.StopPrice;
                    pos.Leg2.ExitBar = sc.Index;
                    float raw = (pos.Direction == 1)
                        ? (pos.StopPrice - pos.EntryPrice) / tickSize
                        : (pos.EntryPrice - pos.StopPrice) / tickSize;
                    pos.Leg2.PnlTicks = raw;
                }
                goto position_closed;
            }
        }

        // --- Check targets (only if stop didn't hit this bar) ---
        // Leg 1 target
        if (pos.Leg1.Active)
        {
            bool t1Hit = false;
            if (pos.Direction == 1)
                t1Hit = (high >= pos.Leg1.TargetPrice);
            else
                t1Hit = (low <= pos.Leg1.TargetPrice);

            if (t1Hit)
            {
                pos.Leg1.Active = false;
                pos.Leg1.ExitResult = EXIT_TARGET_1;
                pos.Leg1.ExitPrice = pos.Leg1.TargetPrice;
                pos.Leg1.ExitBar = sc.Index;
                float raw = (pos.Direction == 1)
                    ? (pos.Leg1.TargetPrice - pos.EntryPrice) / tickSize
                    : (pos.EntryPrice - pos.Leg1.TargetPrice) / tickSize;
                pos.Leg1.PnlTicks = raw;
                // Remove T1 line on hit
                sc.DeleteACSChartDrawing(sc.ChartNumber, TOOL_DELETE_CHARTDRAWING,
                    ZoneBounceConfig::LN_ZB1_T1 + pos.DrawIdx);
            }
        }

        // Leg 2 target
        if (pos.Leg2.Active)
        {
            bool t2Hit = false;
            if (pos.Direction == 1)
                t2Hit = (high >= pos.Leg2.TargetPrice);
            else
                t2Hit = (low <= pos.Leg2.TargetPrice);

            if (t2Hit)
            {
                pos.Leg2.Active = false;
                pos.Leg2.ExitResult = EXIT_TARGET_2;
                pos.Leg2.ExitPrice = pos.Leg2.TargetPrice;
                pos.Leg2.ExitBar = sc.Index;
                float raw = (pos.Direction == 1)
                    ? (pos.Leg2.TargetPrice - pos.EntryPrice) / tickSize
                    : (pos.EntryPrice - pos.Leg2.TargetPrice) / tickSize;
                pos.Leg2.PnlTicks = raw;
            }
        }

        // If both legs closed, trade is done
        if (!pos.Leg1.Active && !pos.Leg2.Active)
            goto position_closed;

        // Trade still open — skip to signal processing
        goto done_exit_check;

    position_closed:
        {
            // Delete level lines
            DeleteLevelLines(pos.DrawIdx);

            // Compute weighted PnL: (0.67 * leg1 + 0.33 * leg2) - 3t
            float weightedPnl =
                ZoneBounceConfig::LEG1_WEIGHT * pos.Leg1.PnlTicks +
                ZoneBounceConfig::LEG2_WEIGHT * pos.Leg2.PnlTicks -
                (float)ZoneBounceConfig::COST_TICKS;

            int barsHeld = sc.Index - pos.EntryBar;

            // Update kill-switch
            KillSwitchState& ks = pState->KillSwitch;

            // Check day/week rollover
            SCDateTime barDT = sc.BaseDateTimeIn[sc.Index];
            int y, mo, d, h, mi, s;
            barDT.GetDateTimeYMDHMS(y, mo, d, h, mi, s);
            int dayNum = y * 10000 + mo * 100 + d;
            int dow = barDT.GetDayOfWeek();
            int weekNum = dayNum - dow;

            if (dayNum != ks.LastTradeDay)
            {
                ks.DailyPnl = 0.0f;
                ks.SessionHalted = false;
                ks.DailyHalted = false;
                ks.ConsecLosses = 0;
                ks.LastTradeDay = dayNum;
            }
            if (weekNum != ks.LastTradeWeek)
            {
                ks.WeeklyPnl = 0.0f;
                ks.WeeklyHalted = false;
                ks.LastTradeWeek = weekNum;
            }

            ks.DailyPnl += weightedPnl;
            ks.WeeklyPnl += weightedPnl;

            if (weightedPnl < 0.0f)
                ks.ConsecLosses++;
            else
                ks.ConsecLosses = 0;

            if (ks.ConsecLosses >= ZoneBounceConfig::KILLSWITCH_CONSEC_LOSSES)
                ks.SessionHalted = true;
            if (ks.DailyPnl <= (float)ZoneBounceConfig::KILLSWITCH_DAILY_TICKS)
                ks.DailyHalted = true;
            if (ks.WeeklyPnl <= (float)ZoneBounceConfig::KILLSWITCH_WEEKLY_TICKS)
                ks.WeeklyHalted = true;

            // Exit remaining position via SC trade service
            sc.FlattenAndCancelAllOrders();

            // --- Write trade_log.csv ---
            if (Input_CSVLogging.GetBoolean() && pos.SignalIdx >= 0 &&
                pos.SignalIdx < storage->SignalCount)
            {
                const SignalRecord& sig = storage->Signals[pos.SignalIdx];
                int tfMin = GetTFMinutes(sig.SourceSlot);
                const char* tfLabel = GetTFLabel(tfMin);
                float priorPen = FindPriorPenetration(sig, pos.SignalIdx);
                bool f10IsNaN = (priorPen < 0.0f);
                float f10Raw = f10IsNaN ? 0.0f : priorPen;

                float f10Pts = BinNumeric(f10Raw, ZoneBounceConfig::F10_BIN_P33,
                    ZoneBounceConfig::F10_BIN_P67, ZoneBounceConfig::F10_WEIGHT,
                    f10IsNaN);
                float f04Pts = ScoreF04(sig.CascadeState);
                float f01Pts = ScoreF01(tfMin);
                float f21Pts = BinNumeric((float)sig.ZoneAgeBars,
                    ZoneBounceConfig::F21_BIN_P33, ZoneBounceConfig::F21_BIN_P67,
                    ZoneBounceConfig::F21_WEIGHT, false);
                float score = f10Pts + f04Pts + f01Pts + f21Pts;
                float trendSlope = sig.TrendSlope;
                TrendLabel trend = ClassifyTrend(trendSlope, sig.Type);
                bool sbb = IsSBB(sig);

                FILE* f = OpenTradeLog();
                if (f)
                {
                    char dtBuf[32];
                    FormatDateTime(sig.BarIndex, dtBuf, sizeof(dtBuf));

                    // Generate trade ID
                    static int tradeCounter = 0;
                    tradeCounter++;
                    char tradeId[64];
                    snprintf(tradeId, sizeof(tradeId), "ZB_%s_%04d",
                             dtBuf, tradeCounter);
                    for (char* p = tradeId; *p; p++)
                    {
                        if (*p == ' ' || *p == ':') *p = '_';
                        if (*p == '-') *p = '_';
                    }

                    const char* cascadeLabel =
                        (sig.CascadeState >= 0 && sig.CascadeState <= 2)
                        ? CascadeStr[sig.CascadeState] : "UNKNOWN";

                    fprintf(f,
                        "%s,%s,%s,%s,%s,%s,"              // id,mode,dt,dir,touchtype,srclabel
                        "%d,"                              // seq
                        "%.2f,%.2f,%d,"                    // zone_top,zone_bot,zone_width_ticks
                        "%.4f,%s,%s,%.4f,"                 // F10raw,F04raw,F01raw,F21raw
                        "%d,%d,%d,%d,"                     // F10bin,F04bin,F01bin,F21bin
                        "%.4f,%.4f,%.4f,%.4f,"             // F10pts,F04pts,F01pts,F21pts
                        "%.4f,%.4f,%.6f,%s,"               // score,margin,slope,trend
                        "%s,%s,"                           // sbb,session
                        "%s,%d,"                           // entry_type,limit_depth
                        "%d,%.2f,%.2f,"                    // entrybar,entrypx,stoppx
                        "%d,%d,%d,"                        // stop_ticks,t1_ticks,t2_ticks
                        "%.2f,%.2f,"                       // t1target,t2target
                        "%s,%.2f,%d,%.2f,"                 // leg1exit,leg1px,leg1bar,leg1pnl
                        "%s,%.2f,%d,%.2f,"                 // leg2exit,leg2px,leg2bar,leg2pnl
                        "%.4f,%d,%.2f,%.2f,"               // wpnl,bars,mfe,mae
                        "%.1f,%d\n",                       // slip,latency
                        tradeId,
                        (pos.Mode == MODE_CT) ? "CT" : "WTNT",
                        dtBuf,
                        (pos.Direction == 1) ? "LONG" : "SHORT",
                        (sig.Type == 0) ? "DEMAND_EDGE" : "SUPPLY_EDGE",
                        tfLabel,
                        sig.TouchSequence,
                        pos.ZoneTop, pos.ZoneBot, pos.ZoneWidthTicks,
                        f10Raw, cascadeLabel, tfLabel, (float)sig.ZoneAgeBars,
                        BinIndex(f10Raw, ZoneBounceConfig::F10_BIN_P33,
                            ZoneBounceConfig::F10_BIN_P67, f10IsNaN),
                        BinF04(sig.CascadeState),
                        BinF01(tfMin),
                        BinIndex((float)sig.ZoneAgeBars,
                            ZoneBounceConfig::F21_BIN_P33,
                            ZoneBounceConfig::F21_BIN_P67, false),
                        f10Pts, f04Pts, f01Pts, f21Pts,
                        score, score - ZoneBounceConfig::SCORE_THRESHOLD,
                        trendSlope, TrendLabelStr[trend],
                        sbb ? "SBB" : "NORMAL",
                        GetSession(sig.BarIndex),
                        EntryTypeStr[pos.Entry], pos.LimitDepthTicks,
                        pos.EntryBar, pos.EntryPrice, pos.StopPrice,
                        pos.StopTicks, pos.T1Ticks, pos.T2Ticks,
                        pos.Leg1.TargetPrice, pos.Leg2.TargetPrice,
                        ExitTypeStr[pos.Leg1.ExitResult],
                        pos.Leg1.ExitPrice, pos.Leg1.ExitBar, pos.Leg1.PnlTicks,
                        ExitTypeStr[pos.Leg2.ExitResult],
                        pos.Leg2.ExitPrice, pos.Leg2.ExitBar, pos.Leg2.PnlTicks,
                        weightedPnl, barsHeld, pos.MFE, pos.MAE,
                        0.0f, 0);  // slippage=0, latency=0 in replay

                    fclose(f);
                }
            }

            // Reset position
            memset(&pos, 0, sizeof(PositionState));
        }
    }
done_exit_check:

    // =================================================================
    //  Process new signals from ZBV4
    // =================================================================
    int sigCount = storage->SignalCount;

    // ZBV4 evicts old signals, which reduces SignalCount. If our
    // high-water mark exceeds the current count, reset to re-sync.
    if (pState->LastProcessedSignalCount > sigCount)
        pState->LastProcessedSignalCount = sigCount;

    if (sigCount <= pState->LastProcessedSignalCount)
        goto end_of_study;

    // Process all new signals
    for (int i = pState->LastProcessedSignalCount; i < sigCount; i++)
    {
        const SignalRecord& sig = storage->Signals[i];
        if (!sig.Active) continue;

        // Only process signals on current bar
        if (sig.BarIndex != sc.Index) continue;

        // Must be edge touch
        if (sig.Type != 0 && sig.Type != 1) continue;

        // --- Step 3: Direction ---
        int direction = (sig.Type == 0) ? 1 : -1;  // DEMAND->LONG, SUPPLY->SHORT

        // --- Step 4: Compute features ---
        int tfMin = GetTFMinutes(sig.SourceSlot);
        const char* tfLabel = GetTFLabel(tfMin);

        float priorPen = FindPriorPenetration(sig, i);
        bool f10IsNaN = (priorPen < 0.0f);
        float f10Raw = f10IsNaN ? 0.0f : priorPen;

        // --- Step 5: Zone width ---
        int zoneWidthTicks = (int)((sig.ZoneTop - sig.ZoneBot) / tickSize + 0.5f);

        // --- Step 6: Score ---
        float f10Pts = BinNumeric(f10Raw, ZoneBounceConfig::F10_BIN_P33,
            ZoneBounceConfig::F10_BIN_P67, ZoneBounceConfig::F10_WEIGHT,
            f10IsNaN);
        float f04Pts = ScoreF04(sig.CascadeState);
        float f01Pts = ScoreF01(tfMin);
        float f21Pts = BinNumeric((float)sig.ZoneAgeBars,
            ZoneBounceConfig::F21_BIN_P33, ZoneBounceConfig::F21_BIN_P67,
            ZoneBounceConfig::F21_WEIGHT, false);

        float acalScore = f10Pts + f04Pts + f01Pts + f21Pts;
        float scoreMargin = acalScore - ZoneBounceConfig::SCORE_THRESHOLD;

        // --- Compute trend ---
        float trendSlope = sig.TrendSlope;
        TrendLabel trend = ClassifyTrend(trendSlope, sig.Type);
        bool sbb = IsSBB(sig);

        // --- Determine action and skip reason ---
        const char* skipReason = nullptr;
        TradeMode mode = MODE_CT;

        // Step 6: Score gate
        if (acalScore < ZoneBounceConfig::SCORE_THRESHOLD)
        {
            skipReason = "BELOW_THRESHOLD";
            goto log_signal;
        }

        // Step 7: TF filter
        if (tfMin <= 0 || tfMin > ZoneBounceConfig::TF_MAX_MINUTES)
        {
            skipReason = "TF_FILTER";
            goto log_signal;
        }

        // Step 10: Mode routing
        if (trend == TREND_CT)
        {
            mode = MODE_CT;
        }
        else
        {
            // WT/NT: seq gate
            if (sig.TouchSequence > ZoneBounceConfig::WTNT_SEQ_MAX)
            {
                skipReason = "SEQ_FILTER";
                goto log_signal;
            }
            mode = MODE_WTNT;
        }

        // Step 11: No-overlap check (includes pending entries AND pending limits)
        if (pos.InTrade || pState->MarketPending.Active || pState->LimitPending.Active)
        {
            if (pos.InTrade)
            {
                if (pos.Mode == mode)
                    skipReason = "IN_POSITION";
                else
                    skipReason = "CROSS_MODE_OVERLAP";
            }
            else if (pState->LimitPending.Active)
            {
                skipReason = "LIMIT_PENDING";
            }
            else
            {
                skipReason = "IN_POSITION";  // market pending counts as in-position
            }
            goto log_signal;
        }

        // Kill-switch check
        {
            KillSwitchState& ks = pState->KillSwitch;
            if (ks.SessionHalted || ks.DailyHalted || ks.WeeklyHalted)
            {
                skipReason = "KILL_SWITCH";
                goto log_signal;
            }
        }

        // Safe bar check — skip signals from before replay start
        if (safeBar > 0 && sig.BarIndex < safeBar)
        {
            skipReason = "PRE_REPLAY";
            goto log_signal;
        }

        // --- QUEUE ENTRY ---
        {
            int baseQty = Input_BaseQty.GetInt();
            int leg1Qty, leg2Qty;
            bool singleLeg = false;

            if (baseQty >= 3)
            {
                leg1Qty = (int)(baseQty * 0.67f + 0.5f);
                leg2Qty = baseQty - leg1Qty;
                if (leg2Qty < 1) { leg2Qty = 1; leg1Qty = baseQty - 1; }
            }
            else if (baseQty == 2)
            {
                leg1Qty = 1;
                leg2Qty = 1;
            }
            else
            {
                singleLeg = true;
                leg1Qty = 1;
                leg2Qty = 0;
            }

            // Score subgraph on signal bar
            SG_Score[sig.BarIndex] = acalScore;

            if (mode == MODE_CT)
            {
                // CT: place 5t limit order inside zone edge
                LimitPendingState& lp = pState->LimitPending;
                lp.Active         = true;
                lp.Direction      = direction;
                lp.SignalIdx      = i;
                lp.ZoneWidthTicks = zoneWidthTicks;
                lp.ZoneTop        = sig.ZoneTop;
                lp.ZoneBot        = sig.ZoneBot;
                lp.Leg1Qty        = leg1Qty;
                lp.Leg2Qty        = leg2Qty;
                lp.SingleLeg      = singleLeg;
                lp.SignalBar      = sig.BarIndex;
                lp.DeadlineBar    = sig.BarIndex + ZoneBounceConfig::CT_FILL_WINDOW_BARS;

                // Limit price: 5t inside zone edge
                if (direction == 1)  // DEMAND_EDGE -> LONG: buy below ZoneTop
                    lp.LimitPrice = sig.ZoneTop -
                        ZoneBounceConfig::CT_LIMIT_DEPTH_TICKS * tickSize;
                else                 // SUPPLY_EDGE -> SHORT: sell above ZoneBot
                    lp.LimitPrice = sig.ZoneBot +
                        ZoneBounceConfig::CT_LIMIT_DEPTH_TICKS * tickSize;
            }
            else
            {
                // WT/NT: market entry at next bar open
                MarketPendingState& mp = pState->MarketPending;
                mp.Active         = true;
                mp.Mode           = MODE_WTNT;
                mp.Direction      = direction;
                mp.SignalIdx      = i;
                mp.ZoneWidthTicks = zoneWidthTicks;
                mp.ZoneTop        = sig.ZoneTop;
                mp.ZoneBot        = sig.ZoneBot;
                mp.Leg1Qty        = leg1Qty;
                mp.Leg2Qty        = leg2Qty;
                mp.SingleLeg      = singleLeg;
            }

            skipReason = nullptr;  // traded
        }

    log_signal:
        // --- Write signal_log.csv ---
        if (Input_CSVLogging.GetBoolean())
        {
            FILE* f = OpenSignalLog();
            if (f)
            {
                char dtBuf[32];
                FormatDateTime(sig.BarIndex, dtBuf, sizeof(dtBuf));

                float currentPosPnl = 0.0f;
                if (skipReason != nullptr && pos.InTrade)
                {
                    float last = sc.BaseData[SC_LAST][sc.Index];
                    currentPosPnl = (pos.Direction == 1)
                        ? (last - pos.EntryPrice) / tickSize
                        : (pos.EntryPrice - last) / tickSize;
                }

                fprintf(f,
                    "%s,%s,%s,%d,%d,%.4f,%.4f,%s,%s,%s,%s,%.2f\n",
                    dtBuf,
                    (sig.Type == 0) ? "DEMAND_EDGE" : "SUPPLY_EDGE",
                    tfLabel,
                    zoneWidthTicks, sig.TouchSequence,
                    acalScore, scoreMargin,
                    TrendLabelStr[trend],
                    sbb ? "SBB" : "NORMAL",
                    (skipReason == nullptr) ? "TRADE" : "SKIP",
                    (skipReason != nullptr) ? skipReason : "",
                    currentPosPnl);

                fclose(f);
            }
        }

        // --- Write microstructure_log.csv (per zone touch, all touches) ---
        if (Input_CSVLogging.GetBoolean())
        {
            SCString mPath;
            mPath.Format("%s\\ATEAM_ZONE_BOUNCE_V1_microstructure.csv",
                         sc.DataFilesFolder().GetChars());
            static int microHeaderWritten = 0;
            FILE* mf = fopen(mPath.GetChars(), "a");
            if (mf)
            {
                if (microHeaderWritten == 0)
                {
                    fseek(mf, 0, SEEK_END);
                    if (ftell(mf) == 0)
                        fprintf(mf, "datetime,touch_type,source_label,"
                                "bid_ask_spread,volume,ask_volume,bid_volume,"
                                "delta,num_trades\n");
                    microHeaderWritten = 1;
                }
                char dtBuf[32];
                FormatDateTime(sig.BarIndex, dtBuf, sizeof(dtBuf));

                float askVol = sc.BaseData[SC_ASKVOL][sig.BarIndex];
                float bidVol = sc.BaseData[SC_BIDVOL][sig.BarIndex];
                float volume = sc.BaseData[SC_VOLUME][sig.BarIndex];
                float numTrades = sc.BaseData[SC_NUM_TRADES][sig.BarIndex];
                float delta = askVol - bidVol;
                float spread = sc.BaseData[SC_HIGH][sig.BarIndex] -
                               sc.BaseData[SC_LOW][sig.BarIndex];

                fprintf(mf, "%s,%s,%s,%.2f,%.0f,%.0f,%.0f,%.0f,%.0f\n",
                        dtBuf,
                        (sig.Type == 0) ? "DEMAND_EDGE" : "SUPPLY_EDGE",
                        tfLabel,
                        spread, volume, askVol, bidVol, delta, numTrades);
                fclose(mf);
            }
        }
    } // end signal loop

    pState->LastProcessedSignalCount = sigCount;

end_of_study:
    return;
}
