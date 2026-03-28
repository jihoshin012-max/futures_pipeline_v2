// STUDY VERSION LOG
// Current: v3.2 (2026-03-24) — Frozen dual-model autotrader
// Architecture: A-Eq (Mode 1) + B-ZScore (Mode 2) waterfall
//
// archetype: zone_touch
// @study: ATEAM Zone Touch V3.2
// @version: 3.2
// @author: ATEAM
// @type: trading-system
// @features: inter-study, autoloop, GetPersistentPointerFromChartStudy, dual-model, partial-exits
// @looping: autoloop
// @complexity: high
// @summary: Dual-model zone touch autotrader. A-Eq bin-scored (M1) with partial exits
//           and B-ZScore linear (M2) with zone-relative sizing. Priority waterfall,
//           circuit breakers, CSV logging. Reads ZTE SignalStorage via persistent pointer.

#include "sierrachart.h"
#include <cstdio>
#include <cmath>

SCDLLName("ATEAM_ZONE_TOUCH_V32")

// =========================================================================
//  Frozen Config v3.2 (inlined — SC remote build only sends the .cpp file)
//  CONFIG_VERSION = "V32_2026-03-24"
//  Do NOT modify without full pipeline re-validation.
// =========================================================================
namespace V32Config
{
    static const char* CONFIG_VERSION = "V32_2026-03-24";

    // Instrument
    constexpr float TICK_SIZE  = 0.25f;
    constexpr float TICK_VALUE = 5.00f;
    constexpr int   COST_TICKS = 3;
    constexpr int   ATR_PERIOD = 14;

    // A-Eq Model (Mode 1) — 7 features, bin-based, max 70
    constexpr float AEQ_THRESHOLD = 45.5f;

    // F10 Prior Penetration
    constexpr float F10_BIN_LO = 155.0f;
    constexpr float F10_BIN_HI = 473.07f;
    constexpr int   F10_PTS_LO = 10;
    constexpr int   F10_PTS_MD = 5;
    constexpr int   F10_PTS_HI = 0;
    constexpr int   F10_PTS_NA = 5;

    // F01 Timeframe (categorical, minutes -> points)
    // 120m=5, 90m=10, 15m=5, 30m=5, 60m=5, 240m=5, 360m=5, 720m=5, 480m=0
    constexpr int F01_PTS_15  = 5;
    constexpr int F01_PTS_30  = 5;
    constexpr int F01_PTS_60  = 5;
    constexpr int F01_PTS_90  = 10;
    constexpr int F01_PTS_120 = 5;
    constexpr int F01_PTS_240 = 5;
    constexpr int F01_PTS_360 = 5;
    constexpr int F01_PTS_480 = 0;
    constexpr int F01_PTS_720 = 5;

    // F05 Session (categorical)
    // Overnight=0, PreRTH=10, Midday=5, Close=5, OpeningDrive=5
    constexpr int F05_PTS_OVERNIGHT    = 0;
    constexpr int F05_PTS_PRERTH       = 10;
    constexpr int F05_PTS_OPENINGDRIVE = 5;
    constexpr int F05_PTS_MIDDAY       = 5;
    constexpr int F05_PTS_CLOSE        = 5;

    // F09 ZW/ATR Ratio
    constexpr float F09_BIN_LO = 3.706f;
    constexpr float F09_BIN_HI = 9.415f;
    constexpr int   F09_PTS_LO = 10;
    constexpr int   F09_PTS_MD = 5;
    constexpr int   F09_PTS_HI = 0;

    // F21 Zone Age
    constexpr float F21_BIN_LO = 110.224f;
    constexpr float F21_BIN_HI = 1136.328f;
    constexpr int   F21_PTS_LO = 10;
    constexpr int   F21_PTS_MD = 5;
    constexpr int   F21_PTS_HI = 0;

    // F13 Close Position
    constexpr float F13_BIN_LO = 0.03226f;
    constexpr float F13_BIN_HI = 0.24039f;
    constexpr int   F13_PTS_LO = 10;
    constexpr int   F13_PTS_MD = 5;
    constexpr int   F13_PTS_HI = 0;

    // F04 Cascade State
    constexpr int F04_PTS_PRIOR_BROKE = 0;
    constexpr int F04_PTS_PRIOR_HELD  = 5;
    constexpr int F04_PTS_NO_PRIOR    = 10;

    // B-ZScore Model (Mode 2) — 18 features, linear
    constexpr float BZSCORE_THRESHOLD = 0.50f;  // probability after sigmoid; Python M2_THRESHOLD=0.50
    constexpr int   BZSCORE_N_FEATURES = 18;

    // Feature order: F10, F01_15m, F01_240m, F01_30m, F01_360m, F01_480m,
    //   F01_60m, F01_720m, F01_90m, F05_Midday, F05_OpeningDrive, F05_Overnight,
    //   F05_PreRTH, F09, F21, F13, F04_PRIOR_BROKE, F04_PRIOR_HELD
    constexpr float BZ_COEF[18] = {
        -1.0944957441109158f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f,
        0.0f, 0.0f, 0.0f, 0.0f, 0.03606010615121733f,
        -0.0656867026804309f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f, 0.0f
    };
    constexpr float BZ_INTERCEPT = 0.13950092965883598f;

    constexpr float BZ_MEAN[18] = {
        440.95186f, 0.29652f, 0.05522f, 0.22148f, 0.04576f, 0.03813f,
        0.12386f, 0.02715f, 0.10098f, 0.20104f, 0.14948f, 0.30598f,
        0.14094f, 9.46885f, 4336.96339f, 0.21711f, 0.78462f, 0.16473f
    };
    constexpr float BZ_STD[18] = {
        393.44480f, 0.45672f, 0.22840f, 0.41524f, 0.20896f, 0.19152f,
        0.32942f, 0.16252f, 0.30130f, 0.40078f, 0.35656f, 0.46082f,
        0.34796f, 10.46645f, 12374.68474f, 0.25883f, 0.41108f, 0.37094f
    };

    // M1 Exit Config (entry-relative)
    constexpr int M1_STOP_TICKS      = 190;
    constexpr int M1_T1_TICKS        = 60;
    constexpr int M1_T2_TICKS        = 120;
    constexpr int M1_T1_CONTRACTS    = 1;
    constexpr int M1_T2_CONTRACTS    = 2;
    constexpr int M1_TOTAL_CONTRACTS = 3;
    constexpr int M1_TIMECAP         = 120;

    // M2 Exit Config (zone-relative)
    constexpr float M2_STOP_MULT   = 1.3f;
    constexpr int   M2_STOP_FLOOR  = 100;
    constexpr float M2_TARGET_MULT = 1.0f;
    constexpr int   M2_TIMECAP     = 80;

    // M2 Position Sizing
    constexpr int M2_SIZE_NARROW     = 3;
    constexpr int M2_SIZE_MID        = 2;
    constexpr int M2_SIZE_WIDE       = 1;
    constexpr int M2_SIZE_THRESHOLD1 = 150;
    constexpr int M2_SIZE_THRESHOLD2 = 250;

    // Circuit Breakers
    constexpr int   CB_DAILY_LOSS      = 700;
    constexpr int   CB_MAX_CONSEC      = 5;
    constexpr int   CB_MAX_DRAWDOWN    = 1541;
    constexpr int   CB_ROLLING_WINDOW  = 30;
    constexpr float CB_ROLLING_PF_FLOOR = 1.0f;

    // EOD
    constexpr int EOD_CLOSE_HHMM   = 1550;
    constexpr int EOD_BLACKOUT_HHMM = 1530;

    // ZTE Storage
    constexpr uint32_t ZTE_STORAGE_MAGIC = 0x5A425634;
    constexpr int MAX_TRACKED_SIGNALS    = 5000;
    constexpr int MAX_TRACKED_ZONES      = 10000;

    // Drawing line number ranges (non-overlapping with ZTE 84000-92000)
    constexpr int LN_ENTRY  = 400000;
    constexpr int LN_STOP   = 404000;
    constexpr int LN_T1     = 408000;
    constexpr int LN_T2     = 412000;
    constexpr int LN_BE     = 416000;
    constexpr int LN_LABEL  = 420000;
    constexpr int MAX_DRAWINGS = 4000;
}

// =========================================================================
//  V4 Data Interface — struct definitions BYTE-IDENTICAL to ZTE
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
    int   FirstSeenBar;
    int   FirstSeenHtfBar;
    int   TouchCount;
    int   SlotIdx;
    bool  IsDemand;
};

struct SignalStorage
{
    uint32_t     MagicNumber;
    int          SignalCount;
    int          ZoneCount;
    int          LastBreakBar;
    int          LastHeldBar;
    SignalRecord Signals[V32Config::MAX_TRACKED_SIGNALS];
    TrackedZone  Zones[V32Config::MAX_TRACKED_ZONES];
};

// =========================================================================
//  V32 Internal Types
// =========================================================================

enum ExitType   { EXIT_NONE = 0, EXIT_T1, EXIT_T2, EXIT_STOP, EXIT_TIMECAP,
                  EXIT_EOD, EXIT_PREEMPT };
enum TradeMode  { MODE_M1 = 1, MODE_M2 = 2 };

static const char* ExitTypeStr[] = { "NONE", "T1", "T2", "STOP", "TIMECAP",
                                     "EOD", "PREEMPT" };
static const char* CascadeStr[]  = { "PRIOR_HELD", "NO_PRIOR", "PRIOR_BROKE" };

enum SessionName { SESSION_OVERNIGHT = 0, SESSION_PRERTH, SESSION_OPENINGDRIVE,
                   SESSION_MIDDAY, SESSION_CLOSE };

struct PartialState
{
    bool  T1Hit;
    bool  T2Hit;
    float T1Price;
    float T2Price;
    int   T1Contracts;
    int   T2Contracts;
};

struct PositionState
{
    bool      InTrade;
    TradeMode Mode;
    int       Direction;       // +1 LONG, -1 SHORT
    float     EntryPrice;
    int       EntryBar;
    float     StopPrice;
    float     TargetPrice;     // M2 single target, or M1 T2 (final)
    int       TimeCap;
    int       TotalContracts;
    int       RemainingContracts;
    PartialState Partial;      // M1 only
    bool      BEActive;        // breakeven stop active
    float     OriginalStopPrice; // pre-BE stop for finding attached orders
    float     PartialPnlTicks; // accumulated PnL from partial exits (T1)
    float     MFE;
    float     MAE;
    int       SignalIdx;
    int       DrawIdx;
    float     ZoneWidthTicks;  // stored for M2 sizing log
};

constexpr int CB_RING_SIZE = 30;

struct CircuitBreakerState
{
    float DailyPnl;
    int   ConsecLosses;
    float EquityHWM;
    float CurrentEquity;
    float TradeRing[CB_RING_SIZE];  // last N trade PnLs (ticks)
    int   RingHead;
    int   RingCount;
    int   LastSessionDay;
    bool  CB_Daily;
    bool  CB_Consec;
    bool  CB_Drawdown;
    bool  CB_RollingPF;
};

struct PendingEntryState
{
    bool      HasPending;
    TradeMode Mode;
    int       Direction;
    int       SignalIdx;
    int       TotalContracts;
    float     LimitPrice;
    int       TimeoutBar;         // cancel after this bar
    float     StopTicks;
    float     T1Ticks;
    float     T2Ticks;
    int       T1Contracts;
    int       T2Contracts;
    int       TimeCap;
    float     TargetTicks;        // M2
    float     ZoneWidthTicks;
    float     AeqScore;
    float     BzScore;
};

constexpr uint32_t V32_STATE_MAGIC = 0x56333200; // "V32\0"

struct StudyState
{
    uint32_t            Magic;
    int                 LastProcessedSignalCount;
    PositionState       Position;
    CircuitBreakerState CB;
    PendingEntryState   Pending;
    int                 DecisionLogHeaderWritten;
    int                 TradeLogHeaderWritten;
    int                 DrawCount;
};

// =========================================================================
//  Main Study Function
// =========================================================================

SCSFExport scsf_ATEAM_ZONE_TOUCH_V32(SCStudyInterfaceRef sc)
{
    // --- Inputs ---
    SCInputRef Input_ZTEStudyID       = sc.Input[0];
    SCInputRef Input_Enabled          = sc.Input[1];
    SCInputRef Input_SendOrders       = sc.Input[2];
    SCInputRef Input_M1_Threshold     = sc.Input[3];
    SCInputRef Input_M2_Threshold     = sc.Input[4];
    SCInputRef Input_M2_MaxSeq        = sc.Input[5];
    SCInputRef Input_M2_MaxTF         = sc.Input[6];
    SCInputRef Input_M2_RTHOnly       = sc.Input[7];
    SCInputRef Input_Preemption       = sc.Input[8];
    SCInputRef Input_EntryOffset      = sc.Input[9];
    SCInputRef Input_EntryTimeout     = sc.Input[10];
    SCInputRef Input_M1_StopTicks     = sc.Input[11];
    SCInputRef Input_M1_T1_Ticks      = sc.Input[12];
    SCInputRef Input_M1_T2_Ticks      = sc.Input[13];
    SCInputRef Input_M1_T1_Contracts  = sc.Input[14];
    SCInputRef Input_M1_T2_Contracts  = sc.Input[15];
    SCInputRef Input_M1_BE_After_T1   = sc.Input[16];
    SCInputRef Input_M1_TimeCap       = sc.Input[17];
    SCInputRef Input_M1_TotalContracts = sc.Input[18];
    SCInputRef Input_M2_StopMult      = sc.Input[19];
    SCInputRef Input_M2_StopFloor     = sc.Input[20];
    SCInputRef Input_M2_TargetMult    = sc.Input[21];
    SCInputRef Input_M2_TimeCap       = sc.Input[22];
    SCInputRef Input_M2_Size_Narrow   = sc.Input[23];
    SCInputRef Input_M2_Size_Mid      = sc.Input[24];
    SCInputRef Input_M2_Size_Wide     = sc.Input[25];
    SCInputRef Input_M2_SizeT1        = sc.Input[26];
    SCInputRef Input_M2_SizeT2        = sc.Input[27];
    SCInputRef Input_CB_DailyLoss     = sc.Input[28];
    SCInputRef Input_CB_MaxConsec     = sc.Input[29];
    SCInputRef Input_CB_MaxDrawdown   = sc.Input[30];
    SCInputRef Input_CB_RollingWindow = sc.Input[31];
    SCInputRef Input_CB_RollingPF     = sc.Input[32];
    SCInputRef Input_CB_Enabled       = sc.Input[33];
    SCInputRef Input_CB_Reset         = sc.Input[34];
    SCInputRef Input_LogEnabled       = sc.Input[35];
    SCInputRef Input_Slot0TF          = sc.Input[36];
    SCInputRef Input_Slot1TF          = sc.Input[37];
    SCInputRef Input_Slot2TF          = sc.Input[38];
    SCInputRef Input_Slot3TF          = sc.Input[39];
    SCInputRef Input_Slot4TF          = sc.Input[40];
    SCInputRef Input_Slot5TF          = sc.Input[41];
    SCInputRef Input_Slot6TF          = sc.Input[42];
    SCInputRef Input_Slot7TF          = sc.Input[43];
    SCInputRef Input_Slot8TF          = sc.Input[44];
    SCInputRef Input_EOD_CloseHHMM    = sc.Input[45];
    SCInputRef Input_EOD_BlackoutHHMM = sc.Input[46];
    SCInputRef Input_CB_DailyInclOpen  = sc.Input[47];
    SCInputRef Input_CSVTestMode      = sc.Input[48];
    SCInputRef Input_CSVTestPath      = sc.Input[49];

    // --- Subgraphs (separate up/down — SC DrawStyle applies to all bars) ---
    SCSubgraphRef SG_M1Long       = sc.Subgraph[0];  // demand entry (arrow up)
    SCSubgraphRef SG_M1Short      = sc.Subgraph[1];  // supply entry (arrow down)
    SCSubgraphRef SG_M2Long       = sc.Subgraph[2];  // demand entry (arrow up)
    SCSubgraphRef SG_M2Short      = sc.Subgraph[3];  // supply entry (arrow down)
    SCSubgraphRef SG_M1SkipLong   = sc.Subgraph[4];  // skipped M1 demand (arrow up)
    SCSubgraphRef SG_M1SkipShort  = sc.Subgraph[5];  // skipped M1 supply (arrow down)
    SCSubgraphRef SG_M2SkipLong   = sc.Subgraph[6];  // skipped M2 demand (arrow up)
    SCSubgraphRef SG_M2SkipShort  = sc.Subgraph[7];  // skipped M2 supply (arrow down)

    // =================================================================
    //  SetDefaults
    // =================================================================
    if (sc.SetDefaults)
    {
        sc.GraphName = "ATEAM Zone Touch V3.2";
        sc.StudyDescription = "Dual-model zone touch autotrader (V32_2026-03-24)";
        sc.AutoLoop = 1;
        sc.GraphRegion = 0;
        sc.CalculationPrecedence = VERY_LOW_PREC_LEVEL;

        Input_ZTEStudyID.Name = "ZTE Study ID";
        Input_ZTEStudyID.SetInt(1);
        Input_ZTEStudyID.SetIntLimits(1, 500);

        Input_Enabled.Name = "Enable Trading Logic";
        Input_Enabled.SetYesNo(0);

        Input_SendOrders.Name = "Send Live Orders";
        Input_SendOrders.SetYesNo(0);

        Input_M1_Threshold.Name = "M1 A-Eq Threshold";
        Input_M1_Threshold.SetFloat(V32Config::AEQ_THRESHOLD);

        Input_M2_Threshold.Name = "M2 B-ZScore Threshold";
        Input_M2_Threshold.SetFloat(V32Config::BZSCORE_THRESHOLD);

        Input_M2_MaxSeq.Name = "M2 Max Touch Sequence";
        Input_M2_MaxSeq.SetInt(2);
        Input_M2_MaxSeq.SetIntLimits(1, 20);

        Input_M2_MaxTF.Name = "M2 Max Timeframe (min)";
        Input_M2_MaxTF.SetInt(120);
        Input_M2_MaxTF.SetIntLimits(1, 1440);

        Input_M2_RTHOnly.Name = "M2 RTH Only";
        Input_M2_RTHOnly.SetYesNo(1);

        Input_Preemption.Name = "Preemption Enabled";
        Input_Preemption.SetYesNo(0);

        Input_EntryOffset.Name = "Entry Offset (ticks)";
        Input_EntryOffset.SetInt(0);
        Input_EntryOffset.SetIntLimits(0, 50);

        Input_EntryTimeout.Name = "Entry Timeout (bars)";
        Input_EntryTimeout.SetInt(3);
        Input_EntryTimeout.SetIntLimits(1, 50);

        Input_M1_StopTicks.Name = "M1 Stop (ticks)";
        Input_M1_StopTicks.SetInt(V32Config::M1_STOP_TICKS);

        Input_M1_T1_Ticks.Name = "M1 T1 (ticks)";
        Input_M1_T1_Ticks.SetInt(V32Config::M1_T1_TICKS);

        Input_M1_T2_Ticks.Name = "M1 T2 (ticks)";
        Input_M1_T2_Ticks.SetInt(V32Config::M1_T2_TICKS);

        Input_M1_T1_Contracts.Name = "M1 T1 Contracts";
        Input_M1_T1_Contracts.SetInt(V32Config::M1_T1_CONTRACTS);

        Input_M1_T2_Contracts.Name = "M1 T2 Contracts";
        Input_M1_T2_Contracts.SetInt(V32Config::M1_T2_CONTRACTS);

        Input_M1_BE_After_T1.Name = "M1 BE After T1";
        Input_M1_BE_After_T1.SetYesNo(1);

        Input_M1_TimeCap.Name = "M1 TimeCap (bars)";
        Input_M1_TimeCap.SetInt(V32Config::M1_TIMECAP);

        Input_M1_TotalContracts.Name = "M1 Total Contracts";
        Input_M1_TotalContracts.SetInt(V32Config::M1_TOTAL_CONTRACTS);

        Input_M2_StopMult.Name = "M2 Stop Multiplier";
        Input_M2_StopMult.SetFloat(V32Config::M2_STOP_MULT);

        Input_M2_StopFloor.Name = "M2 Stop Floor (ticks)";
        Input_M2_StopFloor.SetInt(V32Config::M2_STOP_FLOOR);

        Input_M2_TargetMult.Name = "M2 Target Multiplier";
        Input_M2_TargetMult.SetFloat(V32Config::M2_TARGET_MULT);

        Input_M2_TimeCap.Name = "M2 TimeCap (bars)";
        Input_M2_TimeCap.SetInt(V32Config::M2_TIMECAP);

        Input_M2_Size_Narrow.Name = "M2 Size Narrow (<T1)";
        Input_M2_Size_Narrow.SetInt(V32Config::M2_SIZE_NARROW);

        Input_M2_Size_Mid.Name = "M2 Size Mid (T1-T2)";
        Input_M2_Size_Mid.SetInt(V32Config::M2_SIZE_MID);

        Input_M2_Size_Wide.Name = "M2 Size Wide (>=T2)";
        Input_M2_Size_Wide.SetInt(V32Config::M2_SIZE_WIDE);

        Input_M2_SizeT1.Name = "M2 Size Threshold 1 (ticks)";
        Input_M2_SizeT1.SetInt(V32Config::M2_SIZE_THRESHOLD1);

        Input_M2_SizeT2.Name = "M2 Size Threshold 2 (ticks)";
        Input_M2_SizeT2.SetInt(V32Config::M2_SIZE_THRESHOLD2);

        Input_CB_DailyLoss.Name = "CB Daily Loss Limit (ticks)";
        Input_CB_DailyLoss.SetInt(V32Config::CB_DAILY_LOSS);

        Input_CB_MaxConsec.Name = "CB Max Consecutive Losses";
        Input_CB_MaxConsec.SetInt(V32Config::CB_MAX_CONSEC);

        Input_CB_MaxDrawdown.Name = "CB Max Drawdown (ticks)";
        Input_CB_MaxDrawdown.SetInt(V32Config::CB_MAX_DRAWDOWN);

        Input_CB_RollingWindow.Name = "CB Rolling PF Window";
        Input_CB_RollingWindow.SetInt(V32Config::CB_ROLLING_WINDOW);

        Input_CB_RollingPF.Name = "CB Rolling PF Floor";
        Input_CB_RollingPF.SetFloat(V32Config::CB_ROLLING_PF_FLOOR);

        Input_CB_Enabled.Name = "Circuit Breakers Enabled";
        Input_CB_Enabled.SetYesNo(1);

        Input_CB_Reset.Name = "CB Reset (set 1 to reset)";
        Input_CB_Reset.SetInt(0);

        Input_CB_DailyInclOpen.Name = "CB Daily Loss Incl Open P&L";
        Input_CB_DailyInclOpen.SetYesNo(0);  // default: realized only

        Input_LogEnabled.Name = "CSV Logging Enabled";
        Input_LogEnabled.SetYesNo(1);

        Input_Slot0TF.Name = "Slot 0 TF (min)"; Input_Slot0TF.SetInt(15);
        Input_Slot1TF.Name = "Slot 1 TF (min)"; Input_Slot1TF.SetInt(30);
        Input_Slot2TF.Name = "Slot 2 TF (min)"; Input_Slot2TF.SetInt(60);
        Input_Slot3TF.Name = "Slot 3 TF (min)"; Input_Slot3TF.SetInt(90);
        Input_Slot4TF.Name = "Slot 4 TF (min)"; Input_Slot4TF.SetInt(120);
        Input_Slot5TF.Name = "Slot 5 TF (min)"; Input_Slot5TF.SetInt(0);
        Input_Slot6TF.Name = "Slot 6 TF (min)"; Input_Slot6TF.SetInt(0);
        Input_Slot7TF.Name = "Slot 7 TF (min)"; Input_Slot7TF.SetInt(0);
        Input_Slot8TF.Name = "Slot 8 TF (min)"; Input_Slot8TF.SetInt(0);

        Input_EOD_CloseHHMM.Name = "EOD Close (HHMM)";
        Input_EOD_CloseHHMM.SetInt(V32Config::EOD_CLOSE_HHMM);

        Input_EOD_BlackoutHHMM.Name = "EOD Blackout (HHMM)";
        Input_EOD_BlackoutHHMM.SetInt(V32Config::EOD_BLACKOUT_HHMM);

        Input_CSVTestMode.Name = "CSV Test Mode";
        Input_CSVTestMode.SetYesNo(0);

        Input_CSVTestPath.Name = "CSV Test Path";
        Input_CSVTestPath.SetString(
            "C:\\Projects\\pipeline\\stages\\01-data\\output\\zone_prep\\");

        SG_M1Long.Name = "M1 Long";
        SG_M1Long.DrawStyle = DRAWSTYLE_ARROW_UP;
        SG_M1Long.PrimaryColor = RGB(0, 200, 0);
        SG_M1Long.LineWidth = 4;

        SG_M1Short.Name = "M1 Short";
        SG_M1Short.DrawStyle = DRAWSTYLE_ARROW_DOWN;
        SG_M1Short.PrimaryColor = RGB(0, 200, 0);
        SG_M1Short.LineWidth = 4;

        SG_M2Long.Name = "M2 Long";
        SG_M2Long.DrawStyle = DRAWSTYLE_ARROW_UP;
        SG_M2Long.PrimaryColor = RGB(0, 128, 255);
        SG_M2Long.LineWidth = 4;

        SG_M2Short.Name = "M2 Short";
        SG_M2Short.DrawStyle = DRAWSTYLE_ARROW_DOWN;
        SG_M2Short.PrimaryColor = RGB(0, 128, 255);
        SG_M2Short.LineWidth = 4;

        SG_M1SkipLong.Name = "M1 Skip Long";
        SG_M1SkipLong.DrawStyle = DRAWSTYLE_ARROW_UP;
        SG_M1SkipLong.PrimaryColor = RGB(80, 80, 80);
        SG_M1SkipLong.LineWidth = 3;

        SG_M1SkipShort.Name = "M1 Skip Short";
        SG_M1SkipShort.DrawStyle = DRAWSTYLE_ARROW_DOWN;
        SG_M1SkipShort.PrimaryColor = RGB(80, 80, 80);
        SG_M1SkipShort.LineWidth = 3;

        SG_M2SkipLong.Name = "M2 Skip Long";
        SG_M2SkipLong.DrawStyle = DRAWSTYLE_ARROW_UP;
        SG_M2SkipLong.PrimaryColor = RGB(80, 80, 80);
        SG_M2SkipLong.LineWidth = 3;

        SG_M2SkipShort.Name = "M2 Skip Short";
        SG_M2SkipShort.DrawStyle = DRAWSTYLE_ARROW_DOWN;
        SG_M2SkipShort.PrimaryColor = RGB(80, 80, 80);
        SG_M2SkipShort.LineWidth = 3;

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

    // =================================================================
    //  CSV TEST MODE — standalone replication gate (batch simulation)
    //  Reads merged CSVs + bar data, runs full dual-model scoring +
    //  waterfall + simulation without V4/ZTE SignalRecords.
    //  Runs ONCE on the last bar, writes output files, then returns.
    // =================================================================
    if (Input_CSVTestMode.GetBoolean())
    {
        if (sc.Index != sc.ArraySize - 1)
            return;

        SCString basePath;
        basePath = Input_CSVTestPath.GetString();
        if (basePath.GetLength() > 0 &&
            basePath[basePath.GetLength() - 1] != '\\')
            basePath += "\\";

        const float TS = V32Config::TICK_SIZE;

        sc.AddMessageToLog("CSV TEST MODE V32: Starting batch replication...", 0);

        // ---------- Load bar data (with ATR) ----------
        struct BarRow { float Open, High, Low, Last, ATR; };
        const int MAX_BARS = 200000;
        BarRow* barData = (BarRow*)sc.AllocateMemory(MAX_BARS * sizeof(BarRow));
        if (!barData)
        {
            sc.AddMessageToLog("CSV TEST MODE V32: Failed to allocate bar data", 1);
            return;
        }
        int nBars = 0;
        {
            SCString barPath;
            barPath.Format("%sNQ_bardata_P1.csv", basePath.GetChars());
            FILE* bf = fopen(barPath.GetChars(), "r");
            if (!bf)
            {
                sc.AddMessageToLog("CSV TEST MODE V32: Cannot open bar data CSV", 1);
                sc.FreeMemory(barData);
                return;
            }
            char line[4096];
            fgets(line, sizeof(line), bf); // skip header
            while (fgets(line, sizeof(line), bf) && nBars < MAX_BARS)
            {
                // Split all fields by comma to reach ATR (last column, idx 34)
                char fields[40][128];
                int nf = 0;
                char* p = line;
                while (*p && nf < 40)
                {
                    char* start = p;
                    while (*p && *p != ',' && *p != '\n' && *p != '\r') p++;
                    int len = (int)(p - start);
                    if (len >= 128) len = 127;
                    memcpy(fields[nf], start, len);
                    fields[nf][len] = '\0';
                    nf++;
                    if (*p == ',') p++;
                }
                if (nf >= 6)
                {
                    barData[nBars].Open = (float)atof(fields[2]);
                    barData[nBars].High = (float)atof(fields[3]);
                    barData[nBars].Low  = (float)atof(fields[4]);
                    barData[nBars].Last = (float)atof(fields[5]);
                    barData[nBars].ATR  = (nf > 34) ? (float)atof(fields[34]) : 0.0f;
                    nBars++;
                }
            }
            fclose(bf);
        }
        {
            SCString msg;
            msg.Format("CSV TEST MODE V32: Loaded %d bars", nBars);
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
            double PrescoredBZ;  // from p1_scored_touches_bzscore_v32.csv (double to avoid float32 rounding at 0.5 boundary)
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
            if (strstr(s, "UNKNOWN"))     return 1;
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
            while (fgets(line, sizeof(line), f) && nTouches < MAX_TOUCHES)
            {
                char fields[40][128];
                int nf = 0;
                char* p = line;
                while (*p && nf < 40)
                {
                    char* start = p;
                    while (*p && *p != ',' && *p != '\n' && *p != '\r') p++;
                    int len = (int)(p - start);
                    if (len >= 128) len = 127;
                    memcpy(fields[nf], start, len);
                    fields[nf][len] = '\0';
                    nf++;
                    if (*p == ',') p++;
                }
                if (nf < 36) continue;

                // Column order (from zone_prep merged CSV):
                // 0:DateTime, 1:BarIndex, 2:TouchType, 3:ApproachDir,
                // 4:TouchPrice, 5:ZoneTop, 6:ZoneBot, ..., 10:Penetration,
                // 15:TouchSequence, 16:ZoneAgeBars, 18:TrendSlope,
                // 19:SourceLabel, 31:ZoneWidthTicks, 32:CascadeState,
                // 34:SBB_Label, 35:RotBarIndex
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
                t.PrescoredBZ = -1.0;  // filled later from scored CSV

                if (t.RotBarIndex >= 0)
                    nTouches++;
            }
            fclose(f);
        };

        LoadMergedCSV("NQ_merged_P1a.csv");
        LoadMergedCSV("NQ_merged_P1b.csv");

        // Sort touches by RotBarIndex
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
            msg.Format("CSV TEST MODE V32: Loaded %d touches (RotBarIndex >= 0)", nTouches);
            sc.AddMessageToLog(msg, 0);
        }

        // ---------- Load pre-scored B-ZScore from CSV ----------
        // Python stress_test uses pre-scored values (expanding-window z-score +
        // predict_proba). The JSON model params use fixed standardization which
        // gives different scores. For replication, read the same pre-scored CSV.
        {
            SCString bzPath;
            bzPath.Format("%s..\\..\\..\\..\\shared\\archetypes\\zone_touch\\output\\"
                          "p1_scored_touches_bzscore_v32.csv", basePath.GetChars());
            FILE* bf = fopen(bzPath.GetChars(), "r");
            if (!bf)
            {
                // Try absolute fallback
                bf = fopen("C:\\Projects\\pipeline\\shared\\archetypes\\zone_touch\\"
                           "output\\p1_scored_touches_bzscore_v32.csv", "r");
            }
            int nMatched = 0;
            if (bf)
            {
                char line[16384];
                fgets(line, sizeof(line), bf); // skip header
                while (fgets(line, sizeof(line), bf))
                {
                    // Parse fields: col1=BarIndex, col2=TouchType,
                    //               col19=SourceLabel, col64=Score_BZScore
                    char fields[66][128];
                    int nf = 0;
                    char* p = line;
                    while (*p && nf < 66)
                    {
                        char* start = p;
                        while (*p && *p != ',' && *p != '\n' && *p != '\r') p++;
                        int len = (int)(p - start);
                        if (len >= 128) len = 127;
                        memcpy(fields[nf], start, len);
                        fields[nf][len] = '\0';
                        nf++;
                        if (*p == ',') p++;
                    }
                    if (nf < 65) continue;

                    int barIdx = atoi(fields[1]);
                    bool isDemand = (strstr(fields[2], "DEMAND") != nullptr);
                    int touchType = isDemand ? 0 : 1;
                    const char* srcLabel = fields[19];
                    double scoreBZ = atof(fields[64]);

                    // Match against loaded touches by BarIndex+TouchType+SourceLabel
                    for (int ti = 0; ti < nTouches; ti++)
                    {
                        if (touches[ti].BarIndex == barIdx &&
                            touches[ti].TouchType == touchType &&
                            strcmp(touches[ti].SourceLabel, srcLabel) == 0 &&
                            touches[ti].PrescoredBZ < 0.0)
                        {
                            touches[ti].PrescoredBZ = scoreBZ;
                            nMatched++;
                            break;
                        }
                    }
                }
                fclose(bf);
            }
            {
                SCString msg;
                msg.Format("CSV TEST MODE V32: Matched %d/%d pre-scored B-ZScore values",
                           nMatched, nTouches);
                sc.AddMessageToLog(msg, 0);
            }
        }

        // ---------- Helpers ----------
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

        auto CSVClassifySession = [](const char* dtStr) -> SessionName
        {
            // Parse HH:MM from "YYYY-MM-DD HH:MM:SS"
            int hh = 0, mm = 0;
            const char* space = strchr(dtStr, ' ');
            if (space)
                sscanf(space + 1, "%d:%d", &hh, &mm);
            int mins = hh * 60 + mm;
            if (mins < 360)                             return SESSION_OVERNIGHT;
            if (mins >= 360  && mins < 570)             return SESSION_PRERTH;
            if (mins >= 570  && mins < 660)             return SESSION_OPENINGDRIVE;
            if (mins >= 660  && mins < 840)             return SESSION_MIDDAY;
            if (mins >= 840  && mins < 1020)            return SESSION_CLOSE;
            return SESSION_OVERNIGHT;
        };

        auto CSVComputeF13 = [&](int barIdx, bool isDemand) -> float
        {
            if (barIdx < 0 || barIdx >= nBars) return 0.5f;
            float h = barData[barIdx].High;
            float l = barData[barIdx].Low;
            float c = barData[barIdx].Last;
            if (h == l) return 0.5f;
            if (isDemand) return (c - l) / (h - l);
            return (h - c) / (h - l);
        };

        // A-Eq scoring helpers (same constants as main path)
        auto CSVScoreF10 = [](float priorPen) -> float {
            if (priorPen < 0.0f) return (float)V32Config::F10_PTS_NA;
            if (priorPen <= V32Config::F10_BIN_LO) return (float)V32Config::F10_PTS_LO;
            if (priorPen >= V32Config::F10_BIN_HI) return (float)V32Config::F10_PTS_HI;
            return (float)V32Config::F10_PTS_MD;
        };

        auto CSVScoreF01 = [](int tfMin) -> float {
            switch (tfMin) {
                case 15:  return (float)V32Config::F01_PTS_15;
                case 30:  return (float)V32Config::F01_PTS_30;
                case 60:  return (float)V32Config::F01_PTS_60;
                case 90:  return (float)V32Config::F01_PTS_90;
                case 120: return (float)V32Config::F01_PTS_120;
                case 240: return (float)V32Config::F01_PTS_240;
                case 360: return (float)V32Config::F01_PTS_360;
                case 480: return (float)V32Config::F01_PTS_480;
                case 720: return (float)V32Config::F01_PTS_720;
                default:  return 0.0f;
            }
        };

        auto CSVScoreF05 = [](SessionName sess) -> float {
            switch (sess) {
                case SESSION_OVERNIGHT:    return (float)V32Config::F05_PTS_OVERNIGHT;
                case SESSION_PRERTH:       return (float)V32Config::F05_PTS_PRERTH;
                case SESSION_OPENINGDRIVE: return (float)V32Config::F05_PTS_OPENINGDRIVE;
                case SESSION_MIDDAY:       return (float)V32Config::F05_PTS_MIDDAY;
                case SESSION_CLOSE:        return (float)V32Config::F05_PTS_CLOSE;
                default:                   return 0.0f;
            }
        };

        auto CSVScoreF09 = [](float zwAtrRatio) -> float {
            if (zwAtrRatio <= V32Config::F09_BIN_LO) return (float)V32Config::F09_PTS_LO;
            if (zwAtrRatio >= V32Config::F09_BIN_HI) return (float)V32Config::F09_PTS_HI;
            return (float)V32Config::F09_PTS_MD;
        };

        auto CSVScoreF21 = [](float zoneAge) -> float {
            if (zoneAge <= V32Config::F21_BIN_LO) return (float)V32Config::F21_PTS_LO;
            if (zoneAge >= V32Config::F21_BIN_HI) return (float)V32Config::F21_PTS_HI;
            return (float)V32Config::F21_PTS_MD;
        };

        auto CSVScoreF13 = [](float closePos) -> float {
            if (closePos <= V32Config::F13_BIN_LO) return (float)V32Config::F13_PTS_LO;
            if (closePos >= V32Config::F13_BIN_HI) return (float)V32Config::F13_PTS_HI;
            return (float)V32Config::F13_PTS_MD;
        };

        auto CSVScoreF04 = [](int cascade) -> float {
            switch (cascade) {
                case 2:  return (float)V32Config::F04_PTS_PRIOR_BROKE;
                case 0:  return (float)V32Config::F04_PTS_PRIOR_HELD;
                case 1:  return (float)V32Config::F04_PTS_NO_PRIOR;
                default: return 0.0f;
            }
        };

        auto CSVComputeBZScore = [](float priorPen, int tfMin, SessionName sess,
                                     float zwAtrRatio, float zoneAge, float closePos,
                                     int cascade) -> float
        {
            float raw[18];
            memset(raw, 0, sizeof(raw));
            raw[0] = (priorPen >= 0.0f) ? priorPen : 0.0f;
            if (tfMin == 15)       raw[1] = 1.0f;
            else if (tfMin == 240) raw[2] = 1.0f;
            else if (tfMin == 30)  raw[3] = 1.0f;
            else if (tfMin == 360) raw[4] = 1.0f;
            else if (tfMin == 480) raw[5] = 1.0f;
            else if (tfMin == 60)  raw[6] = 1.0f;
            else if (tfMin == 720) raw[7] = 1.0f;
            else if (tfMin == 90)  raw[8] = 1.0f;
            if (sess == SESSION_MIDDAY)            raw[9]  = 1.0f;
            else if (sess == SESSION_OPENINGDRIVE) raw[10] = 1.0f;
            else if (sess == SESSION_OVERNIGHT)    raw[11] = 1.0f;
            else if (sess == SESSION_PRERTH)       raw[12] = 1.0f;
            raw[13] = zwAtrRatio;
            raw[14] = zoneAge;
            raw[15] = closePos;
            if (cascade == 2) raw[16] = 1.0f;
            else if (cascade == 0) raw[17] = 1.0f;

            float z = V32Config::BZ_INTERCEPT;
            for (int i = 0; i < 18; i++)
            {
                float stdVal = V32Config::BZ_STD[i];
                if (stdVal < 1e-8f) stdVal = 1.0f;
                float scaled = (raw[i] - V32Config::BZ_MEAN[i]) / stdVal;
                z += scaled * V32Config::BZ_COEF[i];
            }
            // Sigmoid — convert raw linear to probability (logistic regression)
            return 1.0f / (1.0f + expf(-z));
        };

        auto CSVIsRTH = [](SessionName sess) -> bool {
            return (sess == SESSION_OPENINGDRIVE ||
                    sess == SESSION_MIDDAY ||
                    sess == SESSION_CLOSE);
        };

        // ---------- Output structures ----------
        struct TradeOut
        {
            char  datetime[32];
            int   rotBarIndex;
            int   mode;         // 1=M1, 2=M2
            int   direction;    // 1=LONG, -1=SHORT
            char  zoneType[8];  // DEMAND/SUPPLY
            char  zoneTF[16];
            int   seqCount;
            int   zoneWidthTicks;
            float entryPrice;
            float exitPrice;
            char  exitType[16];
            int   contracts;
            float pnlTicks;     // net (after cost)
            float pnlTotal;
            int   barsHeld;
            float aeqScore;
            float bzScore;
            float mfe, mae;
        };

        struct SkipOut
        {
            char  datetime[32];
            int   rotBarIndex;
            int   mode;
            int   direction;
            char  zoneType[8];
            char  zoneTF[16];
            int   seqCount;
            int   zoneWidthTicks;
            float aeqScore;
            float bzScore;
            char  skipReason[32];
            int   blockingTradeRBI;
        };

        // Decision log — records EVERY touch (entries + skips)
        struct DecisionOut
        {
            char  datetime[32];
            int   rotBarIndex;
            char  zoneType[8];
            float zoneEdge;
            float zoneWidth;
            int   zoneTFMin;
            int   seq;
            float aeqScore;
            float bzScore;
            int   mode;         // 0=none, 1=M1, 2=M2
            char  skipReason[32];
            float entryPrice;
            int   contracts;
            float stopPrice;
            float targetPrice;
        };

        const int MAX_TRADES = 500;
        const int MAX_SKIPS = 5000;
        const int MAX_DECISIONS = 10000;
        TradeOut* tradeLog = (TradeOut*)sc.AllocateMemory(MAX_TRADES * sizeof(TradeOut));
        SkipOut* skipLog = (SkipOut*)sc.AllocateMemory(MAX_SKIPS * sizeof(SkipOut));
        DecisionOut* decLog = (DecisionOut*)sc.AllocateMemory(MAX_DECISIONS * sizeof(DecisionOut));
        int nTrades = 0, nSkips = 0, nDecisions = 0;

        if (!tradeLog || !skipLog || !decLog)
        {
            sc.FreeMemory(barData);
            sc.FreeMemory(touches);
            if (tradeLog) sc.FreeMemory(tradeLog);
            if (skipLog) sc.FreeMemory(skipLog);
            if (decLog) sc.FreeMemory(decLog);
            return;
        }

        // ---------- Simulation state ----------
        int inTradeUntil = -1;
        int blockingTradeRBI = -1;

        auto AddSkip = [&](const TouchRow& t, int tfMin, float aeqScore, float bzScore,
                            int mode, int direction, const char* reason)
        {
            if (nSkips >= MAX_SKIPS) return;
            SkipOut& sk = skipLog[nSkips++];
            strncpy(sk.datetime, t.DateTime, 31); sk.datetime[31] = '\0';
            sk.rotBarIndex = t.RotBarIndex;
            sk.mode = mode;
            sk.direction = direction;
            strncpy(sk.zoneType, (direction == 1) ? "DEMAND" : "SUPPLY", 7);
            sk.zoneType[7] = '\0';
            strncpy(sk.zoneTF, t.SourceLabel, 15); sk.zoneTF[15] = '\0';
            sk.seqCount = t.TouchSequence;
            sk.zoneWidthTicks = (int)(t.ZoneWidthTicks + 0.5f);
            sk.aeqScore = aeqScore;
            sk.bzScore = bzScore;
            strncpy(sk.skipReason, reason, 31); sk.skipReason[31] = '\0';
            sk.blockingTradeRBI = blockingTradeRBI;
        };

        auto AddDecision = [&](const TouchRow& t, int tfMin, float aeqScore,
                                float bzScore, int mode, const char* skipReason,
                                float entryPrice, int contracts,
                                float stopPrice, float targetPrice)
        {
            if (nDecisions >= MAX_DECISIONS) return;
            DecisionOut& d = decLog[nDecisions++];
            strncpy(d.datetime, t.DateTime, 31); d.datetime[31] = '\0';
            d.rotBarIndex = t.RotBarIndex;
            bool isDemand = (t.TouchType == 0);
            strncpy(d.zoneType, isDemand ? "DEMAND" : "SUPPLY", 7);
            d.zoneType[7] = '\0';
            d.zoneEdge = isDemand ? t.ZoneBot : t.ZoneTop;
            d.zoneWidth = t.ZoneWidthTicks;
            d.zoneTFMin = tfMin;
            d.seq = t.TouchSequence;
            d.aeqScore = aeqScore;
            d.bzScore = bzScore;
            d.mode = mode;
            strncpy(d.skipReason, skipReason ? skipReason : "", 31);
            d.skipReason[31] = '\0';
            d.entryPrice = entryPrice;
            d.contracts = contracts;
            d.stopPrice = stopPrice;
            d.targetPrice = targetPrice;
        };

        // ---------- Main simulation loop ----------
        for (int ti = 0; ti < nTouches; ti++)
        {
            const TouchRow& t = touches[ti];
            int rbi = t.RotBarIndex;
            int entryBar = rbi + 1;
            if (entryBar >= nBars) continue;

            bool isDemand = (t.TouchType == 0);
            int direction = isDemand ? 1 : -1;
            int tfMin = CSVGetTFMin(t.SourceLabel);
            int seq = t.TouchSequence;
            int zw = (int)(t.ZoneWidthTicks + 0.5f);

            // --- Compute features ---
            float priorPen = FindPriorPenCSV(ti);
            SessionName sess = CSVClassifySession(t.DateTime);
            float atrVal = (rbi >= 0 && rbi < nBars) ? barData[rbi].ATR : 0.0f;
            float zwAtrRatio = (atrVal > 0.0001f)
                ? (t.ZoneWidthTicks * TS) / atrVal : 0.0f;
            float zoneAge = (float)t.ZoneAgeBars;
            float closePos = CSVComputeF13(rbi, isDemand);

            // --- Scoring ---
            float aeqScore = CSVScoreF10(priorPen) + CSVScoreF01(tfMin) +
                CSVScoreF05(sess) + CSVScoreF09(zwAtrRatio) +
                CSVScoreF21(zoneAge) + CSVScoreF13(closePos) +
                CSVScoreF04(t.CascadeState);

            // Use pre-scored B-ZScore if available (matches Python's pre-scored CSV);
            // fall back to computed score if no pre-scored value was loaded.
            // Keep as double to preserve precision at the 0.50 boundary.
            double bzScoreD = (t.PrescoredBZ >= 0.0)
                ? t.PrescoredBZ
                : (double)CSVComputeBZScore(priorPen, tfMin, sess, zwAtrRatio,
                                            zoneAge, closePos, t.CascadeState);
            float bzScore = (float)bzScoreD;  // for logging

            // --- Waterfall ---
            int selectedMode = 0;
            const char* skipReason = nullptr;

            bool m1Candidate = (aeqScore >= V32Config::AEQ_THRESHOLD);
            bool m2Candidate = false;

            if (!m1Candidate)
            {
                bool passThreshold = (bzScoreD >= (double)V32Config::BZSCORE_THRESHOLD);
                bool passRTH = CSVIsRTH(sess);
                bool passSeq = (seq <= 2);  // frozen M2 filter
                bool passTF  = (tfMin > 0 && tfMin <= 120);

                if (passThreshold && passRTH && passSeq && passTF)
                    m2Candidate = true;
                else if (passThreshold)
                {
                    if (!passRTH) skipReason = "M2_NOT_RTH";
                    else if (!passSeq) skipReason = "M2_SEQ_EXCEEDED";
                    else if (!passTF) skipReason = "M2_TF_EXCEEDED";
                }
            }

            if (m1Candidate)
                selectedMode = 1;
            else if (m2Candidate)
                selectedMode = 2;

            if (selectedMode == 0)
            {
                if (!skipReason)
                    skipReason = "BELOW_THRESHOLD";
                AddSkip(t, tfMin, aeqScore, bzScore, 0, direction, skipReason);
                AddDecision(t, tfMin, aeqScore, bzScore, 0, skipReason,
                            0, 0, 0, 0);
                continue;
            }

            // --- Position overlap ---
            if (entryBar <= inTradeUntil)
            {
                AddSkip(t, tfMin, aeqScore, bzScore, selectedMode, direction,
                         "POSITION_OPEN");
                AddDecision(t, tfMin, aeqScore, bzScore, selectedMode,
                            "POSITION_OPEN", 0, 0, 0, 0);
                continue;
            }

            // --- Simulate trade ---
            float entryPrice = barData[entryBar].Open;
            int barsHeld = 0;
            float exitPrice = 0.0f;
            const char* exitType = "";
            int contracts = 0;
            float pnlTicks = 0.0f;
            float mfe = 0.0f, mae = 0.0f;

            if (selectedMode == 1)
            {
                // M1: 2-leg partial exit (T1=60t@1ct, T2=120t@2ct, BE after T1)
                contracts = V32Config::M1_TOTAL_CONTRACTS;
                float stopPx, t1Px, t2Px;
                if (direction == 1)
                {
                    stopPx = entryPrice - V32Config::M1_STOP_TICKS * TS;
                    t1Px   = entryPrice + V32Config::M1_T1_TICKS * TS;
                    t2Px   = entryPrice + V32Config::M1_T2_TICKS * TS;
                }
                else
                {
                    stopPx = entryPrice + V32Config::M1_STOP_TICKS * TS;
                    t1Px   = entryPrice - V32Config::M1_T1_TICKS * TS;
                    t2Px   = entryPrice - V32Config::M1_T2_TICKS * TS;
                }

                bool leg1Open = true, leg2Open = true;
                float leg1Pnl = 0, leg2Pnl = 0;
                const char* leg1Exit = "";
                const char* leg2Exit = "";
                float maxMFE = 0;

                int end = entryBar + V32Config::M1_TIMECAP;
                if (end > nBars) end = nBars;

                for (int bi = entryBar; bi < end; bi++)
                {
                    float bH = barData[bi].High;
                    float bL = barData[bi].Low;
                    float bC = barData[bi].Last;
                    barsHeld = bi - entryBar + 1;

                    float bmfe = (direction == 1) ? (bH - entryPrice) / TS
                                                  : (entryPrice - bL) / TS;
                    float bmae = (direction == 1) ? (entryPrice - bL) / TS
                                                  : (bH - entryPrice) / TS;
                    if (bmfe > mfe) mfe = bmfe;
                    if (bmae > mae) mae = bmae;
                    if (bmfe > maxMFE) maxMFE = bmfe;

                    // Stop check (all open legs)
                    bool stopHit = (direction == 1) ? (bL <= stopPx)
                                                    : (bH >= stopPx);
                    if (stopHit)
                    {
                        float sPnl = (direction == 1)
                            ? (stopPx - entryPrice) / TS
                            : (entryPrice - stopPx) / TS;
                        if (leg1Open) { leg1Pnl = sPnl; leg1Exit = "stop"; leg1Open = false; }
                        if (leg2Open) { leg2Pnl = sPnl; leg2Exit = "stop"; leg2Open = false; }
                        exitPrice = stopPx;
                        exitType = "stop";
                        break;
                    }

                    // T1 check (leg 1)
                    if (leg1Open)
                    {
                        bool t1Hit = (direction == 1) ? (bH >= t1Px)
                                                      : (bL <= t1Px);
                        if (t1Hit)
                        {
                            leg1Pnl = (float)V32Config::M1_T1_TICKS;
                            leg1Exit = "target_1";
                            leg1Open = false;
                            // Move stop to BE after T1
                            stopPx = entryPrice;
                        }
                    }

                    // T2 check (leg 2)
                    if (leg2Open && !leg1Open)
                    {
                        bool t2Hit = (direction == 1) ? (bH >= t2Px)
                                                      : (bL <= t2Px);
                        if (t2Hit)
                        {
                            leg2Pnl = (float)V32Config::M1_T2_TICKS;
                            leg2Exit = "target_2";
                            leg2Open = false;
                            exitPrice = t2Px;
                            exitType = "target_2";
                            break;
                        }
                    }

                    if (!leg1Open && !leg2Open) break;

                    // Time cap
                    if (barsHeld >= V32Config::M1_TIMECAP)
                    {
                        float tcPnl = (direction == 1)
                            ? (bC - entryPrice) / TS
                            : (entryPrice - bC) / TS;
                        if (leg1Open) { leg1Pnl = tcPnl; leg1Exit = "time_cap"; leg1Open = false; }
                        if (leg2Open) { leg2Pnl = tcPnl; leg2Exit = "time_cap"; leg2Open = false; }
                        exitPrice = bC;
                        exitType = "time_cap";
                        break;
                    }
                }

                // If ran out of bars before exit
                if (leg1Open || leg2Open)
                {
                    int lastIdx = (end > entryBar) ? end - 1 : entryBar;
                    float lastPx = barData[lastIdx].Last;
                    float tcPnl = (direction == 1)
                        ? (lastPx - entryPrice) / TS
                        : (entryPrice - lastPx) / TS;
                    if (leg1Open) { leg1Pnl = tcPnl; leg1Open = false; }
                    if (leg2Open) { leg2Pnl = tcPnl; leg2Open = false; }
                    exitPrice = lastPx;
                    if (strlen(exitType) == 0) exitType = "time_cap";
                    barsHeld = lastIdx - entryBar + 1;
                }

                // Weighted PnL: 1/3 leg1 + 2/3 leg2 (matches Python)
                float wPnl = (1.0f / 3.0f) * leg1Pnl + (2.0f / 3.0f) * leg2Pnl;
                pnlTicks = wPnl * (float)contracts
                    - (float)(V32Config::COST_TICKS * contracts);

                // exitType = last leg's exit reason
                exitType = leg2Exit;
            }
            else
            {
                // M2: stop=max(1.3*ZW,100), target=1.0*ZW, TC=80
                int stopT = (int)(V32Config::M2_STOP_MULT * zw + 0.5f);
                if (stopT < V32Config::M2_STOP_FLOOR) stopT = V32Config::M2_STOP_FLOOR;
                int targetT = (int)(V32Config::M2_TARGET_MULT * zw + 0.5f);
                if (targetT < 1) targetT = 1;

                // Position sizing
                if (zw < V32Config::M2_SIZE_THRESHOLD1)
                    contracts = V32Config::M2_SIZE_NARROW;
                else if (zw < V32Config::M2_SIZE_THRESHOLD2)
                    contracts = V32Config::M2_SIZE_MID;
                else
                    contracts = V32Config::M2_SIZE_WIDE;

                float stopPx, targetPx;
                if (direction == 1)
                {
                    stopPx   = entryPrice - stopT * TS;
                    targetPx = entryPrice + targetT * TS;
                }
                else
                {
                    stopPx   = entryPrice + stopT * TS;
                    targetPx = entryPrice - targetT * TS;
                }

                int end = entryBar + V32Config::M2_TIMECAP;
                if (end > nBars) end = nBars;

                bool exited = false;
                for (int bi = entryBar; bi < end; bi++)
                {
                    float bH = barData[bi].High;
                    float bL = barData[bi].Low;
                    float bC = barData[bi].Last;
                    barsHeld = bi - entryBar + 1;

                    float bmfe = (direction == 1) ? (bH - entryPrice) / TS
                                                  : (entryPrice - bL) / TS;
                    float bmae = (direction == 1) ? (entryPrice - bL) / TS
                                                  : (bH - entryPrice) / TS;
                    if (bmfe > mfe) mfe = bmfe;
                    if (bmae > mae) mae = bmae;

                    // Stop first (same-bar precedence)
                    bool stopHit = (direction == 1) ? (bL <= stopPx)
                                                    : (bH >= stopPx);
                    if (stopHit)
                    {
                        float sPnl = (direction == 1)
                            ? (stopPx - entryPrice) / TS
                            : (entryPrice - stopPx) / TS;
                        pnlTicks = sPnl * (float)contracts
                            - (float)(V32Config::COST_TICKS * contracts);
                        exitPrice = stopPx;
                        exitType = "STOP";
                        exited = true;
                        break;
                    }

                    // Target
                    bool targetHit = (direction == 1) ? (bH >= targetPx)
                                                      : (bL <= targetPx);
                    if (targetHit)
                    {
                        pnlTicks = (float)targetT * (float)contracts
                            - (float)(V32Config::COST_TICKS * contracts);
                        exitPrice = targetPx;
                        exitType = "TARGET";
                        exited = true;
                        break;
                    }

                    // Time cap
                    if (barsHeld >= V32Config::M2_TIMECAP)
                    {
                        float tcPnl = (direction == 1)
                            ? (bC - entryPrice) / TS
                            : (entryPrice - bC) / TS;
                        pnlTicks = tcPnl * (float)contracts
                            - (float)(V32Config::COST_TICKS * contracts);
                        exitPrice = bC;
                        exitType = "TIMECAP";
                        exited = true;
                        break;
                    }
                }

                if (!exited)
                {
                    // Ran out of bars
                    int lastIdx = (end > entryBar) ? end - 1 : entryBar;
                    float bC = barData[lastIdx].Last;
                    float tcPnl = (direction == 1)
                        ? (bC - entryPrice) / TS
                        : (entryPrice - bC) / TS;
                    pnlTicks = tcPnl * (float)contracts
                        - (float)(V32Config::COST_TICKS * contracts);
                    exitPrice = bC;
                    exitType = "TIMECAP";
                    barsHeld = lastIdx - entryBar + 1;
                }
            }

            // --- Record trade ---
            if (nTrades < MAX_TRADES)
            {
                TradeOut& tr = tradeLog[nTrades++];
                strncpy(tr.datetime, t.DateTime, 31); tr.datetime[31] = '\0';
                tr.rotBarIndex = rbi;
                tr.mode = selectedMode;
                tr.direction = direction;
                strncpy(tr.zoneType, isDemand ? "DEMAND" : "SUPPLY", 7);
                tr.zoneType[7] = '\0';
                strncpy(tr.zoneTF, t.SourceLabel, 15); tr.zoneTF[15] = '\0';
                tr.seqCount = seq;
                tr.zoneWidthTicks = zw;
                tr.entryPrice = entryPrice;
                tr.exitPrice = exitPrice;
                strncpy(tr.exitType, exitType, 15); tr.exitType[15] = '\0';
                tr.contracts = contracts;
                tr.pnlTicks = pnlTicks;
                tr.pnlTotal = pnlTicks * V32Config::TICK_VALUE;
                tr.barsHeld = barsHeld;
                tr.aeqScore = aeqScore;
                tr.bzScore = bzScore;
                tr.mfe = mfe;
                tr.mae = mae;
            }

            // Log ENTRY decision (compute stop/target for log)
            {
                float logStop = 0, logTarget = 0;
                if (selectedMode == 1)
                {
                    logStop = (float)V32Config::M1_STOP_TICKS;
                    logTarget = (float)V32Config::M1_T2_TICKS;
                }
                else
                {
                    int st = (int)(V32Config::M2_STOP_MULT * zw + 0.5f);
                    if (st < V32Config::M2_STOP_FLOOR) st = V32Config::M2_STOP_FLOOR;
                    logStop = (float)st;
                    logTarget = V32Config::M2_TARGET_MULT * zw;
                }
                float sPx = (direction == 1)
                    ? entryPrice - logStop * TS
                    : entryPrice + logStop * TS;
                float tPx = (direction == 1)
                    ? entryPrice + logTarget * TS
                    : entryPrice - logTarget * TS;
                AddDecision(t, tfMin, aeqScore, bzScore, selectedMode, "ENTRY",
                            entryPrice, contracts, sPx, tPx);
            }

            blockingTradeRBI = rbi;
            inTradeUntil = entryBar + barsHeld - 1;
        }

        // ---------- Write output CSVs ----------
        {
            SCString tradePath;
            tradePath.Format("%s\\ATEAM_CSV_TEST_V32_trades.csv",
                             basePath.GetChars());
            FILE* f = fopen(tradePath.GetChars(), "w");
            if (f)
            {
                fprintf(f, "datetime,RotBarIndex,mode,direction,zone_type,zone_tf,"
                        "seq_count,zone_width_ticks,entry_price,exit_price,"
                        "exit_type,contracts,pnl_ticks,pnl_total,bars_held,"
                        "score_aeq,score_bz,mfe,mae\n");
                for (int i = 0; i < nTrades; i++)
                {
                    const TradeOut& tr = tradeLog[i];
                    fprintf(f, "%s,%d,M%d,%s,%s,%s,%d,%d,%.2f,%.2f,%s,%d,"
                            "%.4f,%.2f,%d,%.4f,%.4f,%.1f,%.1f\n",
                            tr.datetime, tr.rotBarIndex, tr.mode,
                            (tr.direction == 1) ? "LONG" : "SHORT",
                            tr.zoneType, tr.zoneTF, tr.seqCount,
                            tr.zoneWidthTicks,
                            tr.entryPrice, tr.exitPrice, tr.exitType,
                            tr.contracts, tr.pnlTicks, tr.pnlTotal,
                            tr.barsHeld, tr.aeqScore, tr.bzScore,
                            tr.mfe, tr.mae);
                }
                fclose(f);
            }
        }
        {
            SCString skipPath;
            skipPath.Format("%s\\ATEAM_CSV_TEST_V32_skipped.csv",
                             basePath.GetChars());
            FILE* f = fopen(skipPath.GetChars(), "w");
            if (f)
            {
                fprintf(f, "datetime,RotBarIndex,mode,direction,zone_type,zone_tf,"
                        "seq_count,zone_width_ticks,score_aeq,score_bz,"
                        "skip_reason,blocking_trade_rbi\n");
                for (int i = 0; i < nSkips; i++)
                {
                    const SkipOut& sk = skipLog[i];
                    fprintf(f, "%s,%d,%s,%s,%s,%s,%d,%d,%.4f,%.4f,%s,%d\n",
                            sk.datetime, sk.rotBarIndex,
                            (sk.mode == 1) ? "M1" : ((sk.mode == 2) ? "M2" : "NONE"),
                            (sk.direction == 1) ? "LONG" : "SHORT",
                            sk.zoneType, sk.zoneTF, sk.seqCount,
                            sk.zoneWidthTicks, sk.aeqScore, sk.bzScore,
                            sk.skipReason, sk.blockingTradeRBI);
                }
                fclose(f);
            }
        }
        // Decision log — every touch scored (entries + skips)
        {
            SCString decPath;
            decPath.Format("%s\\ATEAM_CSV_TEST_V32_decisions.csv",
                            basePath.GetChars());
            FILE* f = fopen(decPath.GetChars(), "w");
            if (f)
            {
                fprintf(f, "datetime,touch_id,zone_type,zone_edge,zone_width,"
                        "zone_tf,seq,aeq_score,bzscore,mode,skip_reason,"
                        "entry_price,contracts,stop_price,target_price\n");
                for (int i = 0; i < nDecisions; i++)
                {
                    const DecisionOut& d = decLog[i];
                    fprintf(f, "%s,%d,%s,%.2f,%.1f,%d,%d,%.2f,%.4f,%d,%s,"
                            "%.2f,%d,%.2f,%.2f\n",
                            d.datetime, d.rotBarIndex, d.zoneType,
                            d.zoneEdge, d.zoneWidth, d.zoneTFMin, d.seq,
                            d.aeqScore, d.bzScore, d.mode,
                            d.skipReason, d.entryPrice, d.contracts,
                            d.stopPrice, d.targetPrice);
                }
                fclose(f);
            }
        }

        // ---------- Summary ----------
        {
            int m1Count = 0, m2Count = 0;
            float totalPnl = 0;
            for (int i = 0; i < nTrades; i++)
            {
                if (tradeLog[i].mode == 1) m1Count++;
                else m2Count++;
                totalPnl += tradeLog[i].pnlTicks;
            }
            SCString msg;
            msg.Format("CSV TEST MODE V32: Complete. %d trades (M1=%d, M2=%d), "
                       "%d skips, net PnL=%.1f ticks",
                       nTrades, m1Count, m2Count, nSkips, totalPnl);
            sc.AddMessageToLog(msg, 0);
        }

        sc.FreeMemory(barData);
        sc.FreeMemory(touches);
        sc.FreeMemory(tradeLog);
        sc.FreeMemory(skipLog);
        sc.FreeMemory(decLog);
        return;  // CSV test mode done
    }

    sc.SendOrdersToTradeService = Input_SendOrders.GetBoolean() ? 1 : 0;

    // =================================================================
    //  Enable gate
    // =================================================================
    if (!Input_Enabled.GetBoolean())
    {
        if (Input_SendOrders.GetBoolean() && sc.Index == sc.ArraySize - 1)
        {
            sc.AddMessageToLog(
                "ATEAM_ZONE_TOUCH_V32: Send Live Orders is ON but "
                "Enable Trading Logic is OFF. No orders will be sent.", 1);
        }
        return;
    }

    // =================================================================
    //  Safe boundary — skip historical bars before data is stable
    // =================================================================
    int& safeBar = sc.GetPersistentInt(1);
    if (sc.IsFullRecalculation && sc.Index == 0)
        safeBar = -1;
    if (safeBar < 0 && sc.UpdateStartIndex > 0)
        safeBar = sc.UpdateStartIndex;
    if (safeBar < 0 || sc.Index < safeBar)
        return;

    // =================================================================
    //  Persistent state init
    // =================================================================
    StudyState* pState = (StudyState*)sc.GetPersistentPointer(0);
    if (pState == nullptr || pState->Magic != V32_STATE_MAGIC)
    {
        if (pState == nullptr)
        {
            pState = (StudyState*)sc.AllocateMemory(sizeof(StudyState));
            if (pState == nullptr)
                return;
            sc.SetPersistentPointer(0, pState);
        }
        memset(pState, 0, sizeof(StudyState));
        pState->Magic = V32_STATE_MAGIC;
    }

    // Full recalc reset
    if (sc.IsFullRecalculation && sc.Index == safeBar)
    {
        pState->LastProcessedSignalCount = 0;
        memset(&pState->Position, 0, sizeof(PositionState));
        memset(&pState->CB, 0, sizeof(CircuitBreakerState));
        memset(&pState->Pending, 0, sizeof(PendingEntryState));
        pState->DecisionLogHeaderWritten = 0;
        pState->TradeLogHeaderWritten = 0;
        pState->DrawCount = 0;
    }

    // =================================================================
    //  CB manual reset
    // =================================================================
    if (Input_CB_Reset.GetInt() == 1)
    {
        pState->CB.CB_Consec = false;
        pState->CB.CB_Drawdown = false;
        pState->CB.CB_RollingPF = false;
        pState->CB.ConsecLosses = 0;
        Input_CB_Reset.SetInt(0);
        sc.AddMessageToLog("V32: Circuit breakers manually reset.", 0);
    }

    // =================================================================
    //  Slot-to-TF mapping
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

    auto GetTFMinutes = [&](int sourceSlot) -> int {
        if (sourceSlot >= 0 && sourceSlot < 9)
            return slotTF[sourceSlot];
        return 0;
    };

    // =================================================================
    //  ZTE storage access
    // =================================================================
    int zteID = Input_ZTEStudyID.GetInt();
    void* rawPtr = sc.GetPersistentPointerFromChartStudy(sc.ChartNumber, zteID, 0);
    if (rawPtr == nullptr)
        return;
    SignalStorage* storage = (SignalStorage*)rawPtr;
    if (storage->MagicNumber != V32Config::ZTE_STORAGE_MAGIC)
        return;

    // =================================================================
    //  Local references
    // =================================================================
    PositionState&       pos     = pState->Position;
    CircuitBreakerState& cb      = pState->CB;
    PendingEntryState&   pending = pState->Pending;

    const float tickSize  = V32Config::TICK_SIZE;
    const float tickValue = V32Config::TICK_VALUE;

    // =================================================================
    //  Helper: DateTime utilities
    // =================================================================
    SCDateTime barDT = sc.BaseDateTimeIn[sc.Index];
    int barHour, barMinute, barSecond;
    barDT.GetTimeHMS(barHour, barMinute, barSecond);
    int barHHMM = barHour * 100 + barMinute;
    int barMinutesSinceMidnight = barHour * 60 + barMinute;

    int barDay = 0;
    {
        int y, m, d;
        barDT.GetDateYMD(y, m, d);
        barDay = y * 10000 + m * 100 + d;
    }

    // =================================================================
    //  Position reconciliation — catch desync during fast replay
    //  If study thinks flat but SC still has contracts, force flatten.
    // =================================================================
    if (!pos.InTrade && !pending.HasPending)
    {
        s_SCPositionData scPos;
        sc.GetTradePosition(scPos);
        if (scPos.PositionQuantity != 0)
        {
            sc.FlattenAndCancelAllOrders();
            SCString msg;
            msg.Format("V32 RECONCILE: study flat but SC has %d contracts — "
                       "forced flatten at bar %d (%d:%02d)",
                       scPos.PositionQuantity, sc.Index, barHour, barMinute);
            sc.AddMessageToLog(msg, 1);
        }
    }

    // =================================================================
    //  Helper: Session classification (F05)
    // =================================================================
    auto ClassifySession = [](int minSinceMidnight) -> SessionName {
        if (minSinceMidnight < 360)                            return SESSION_OVERNIGHT;
        if (minSinceMidnight >= 360 && minSinceMidnight < 570) return SESSION_PRERTH;
        if (minSinceMidnight >= 570 && minSinceMidnight < 660) return SESSION_OPENINGDRIVE;
        if (minSinceMidnight >= 660 && minSinceMidnight < 840) return SESSION_MIDDAY;
        if (minSinceMidnight >= 840 && minSinceMidnight < 1020) return SESSION_CLOSE;
        return SESSION_OVERNIGHT; // >= 1020
    };

    auto IsRTH = [](int minSinceMidnight) -> bool {
        return (minSinceMidnight >= 570 && minSinceMidnight < 960); // 9:30 - 16:00
    };

    // =================================================================
    //  Helper: ATR computation (14-period EMA of true range)
    // =================================================================
    auto ComputeATR = [&](int barIdx) -> float {
        int period = V32Config::ATR_PERIOD;
        if (barIdx < period)
            return 0.0f;

        // Seed with SMA
        float sum = 0.0f;
        for (int i = barIdx - period + 1; i <= barIdx - period + period; i++)
        {
            if (i < 1) continue;
            float h = sc.BaseData[SC_HIGH][i];
            float l = sc.BaseData[SC_LOW][i];
            float pc = sc.BaseData[SC_LAST][i - 1];
            float tr = h - l;
            float d1 = (float)fabs(h - pc);
            float d2 = (float)fabs(l - pc);
            if (d1 > tr) tr = d1;
            if (d2 > tr) tr = d2;
            sum += tr;
        }
        float atr = sum / (float)period;

        // EMA forward from seed point
        float mult = 2.0f / ((float)period + 1.0f);
        for (int i = barIdx - period + period + 1; i <= barIdx; i++)
        {
            if (i < 1) continue;
            float h = sc.BaseData[SC_HIGH][i];
            float l = sc.BaseData[SC_LOW][i];
            float pc = sc.BaseData[SC_LAST][i - 1];
            float tr = h - l;
            float d1 = (float)fabs(h - pc);
            float d2 = (float)fabs(l - pc);
            if (d1 > tr) tr = d1;
            if (d2 > tr) tr = d2;
            atr = (tr - atr) * mult + atr;
        }
        return atr;
    };

    // =================================================================
    //  Helper: A-Eq scoring (Mode 1)
    // =================================================================
    auto ScoreF10 = [](float priorPen) -> float {
        if (priorPen < 0.0f) return (float)V32Config::F10_PTS_NA;
        if (priorPen <= V32Config::F10_BIN_LO) return (float)V32Config::F10_PTS_LO;
        if (priorPen >= V32Config::F10_BIN_HI) return (float)V32Config::F10_PTS_HI;
        return (float)V32Config::F10_PTS_MD;
    };

    auto ScoreF01 = [&](int tfMin) -> float {
        switch (tfMin)
        {
            case 15:  return (float)V32Config::F01_PTS_15;
            case 30:  return (float)V32Config::F01_PTS_30;
            case 60:  return (float)V32Config::F01_PTS_60;
            case 90:  return (float)V32Config::F01_PTS_90;
            case 120: return (float)V32Config::F01_PTS_120;
            case 240: return (float)V32Config::F01_PTS_240;
            case 360: return (float)V32Config::F01_PTS_360;
            case 480: return (float)V32Config::F01_PTS_480;
            case 720: return (float)V32Config::F01_PTS_720;
            default:  return 0.0f;
        }
    };

    auto ScoreF05 = [](SessionName sess) -> float {
        switch (sess)
        {
            case SESSION_OVERNIGHT:    return (float)V32Config::F05_PTS_OVERNIGHT;
            case SESSION_PRERTH:       return (float)V32Config::F05_PTS_PRERTH;
            case SESSION_OPENINGDRIVE: return (float)V32Config::F05_PTS_OPENINGDRIVE;
            case SESSION_MIDDAY:       return (float)V32Config::F05_PTS_MIDDAY;
            case SESSION_CLOSE:        return (float)V32Config::F05_PTS_CLOSE;
            default:                   return 0.0f;
        }
    };

    auto ScoreF09 = [](float zwAtrRatio) -> float {
        if (zwAtrRatio <= V32Config::F09_BIN_LO) return (float)V32Config::F09_PTS_LO;
        if (zwAtrRatio >= V32Config::F09_BIN_HI) return (float)V32Config::F09_PTS_HI;
        return (float)V32Config::F09_PTS_MD;
    };

    auto ScoreF21 = [](float zoneAge) -> float {
        if (zoneAge <= V32Config::F21_BIN_LO) return (float)V32Config::F21_PTS_LO;
        if (zoneAge >= V32Config::F21_BIN_HI) return (float)V32Config::F21_PTS_HI;
        return (float)V32Config::F21_PTS_MD;
    };

    auto ScoreF13 = [](float closePos) -> float {
        if (closePos <= V32Config::F13_BIN_LO) return (float)V32Config::F13_PTS_LO;
        if (closePos >= V32Config::F13_BIN_HI) return (float)V32Config::F13_PTS_HI;
        return (float)V32Config::F13_PTS_MD;
    };

    auto ScoreF04 = [](int cascade) -> float {
        switch (cascade)
        {
            case 2:  return (float)V32Config::F04_PTS_PRIOR_BROKE;
            case 0:  return (float)V32Config::F04_PTS_PRIOR_HELD;
            case 1:  return (float)V32Config::F04_PTS_NO_PRIOR;
            default: return 0.0f;
        }
    };

    // =================================================================
    //  Helper: B-ZScore computation (Mode 2)
    // =================================================================
    auto ComputeBZScore = [&](float priorPen, int tfMin, SessionName sess,
                              float zwAtrRatio, float zoneAge, float closePos,
                              int cascade) -> float
    {
        // Build raw feature vector (18 features)
        float raw[18];
        memset(raw, 0, sizeof(raw));

        // F10
        raw[0] = (priorPen >= 0.0f) ? priorPen : 0.0f;

        // F01 one-hot: indices 1-8 = 15m, 240m, 30m, 360m, 480m, 60m, 720m, 90m
        if (tfMin == 15)       raw[1] = 1.0f;
        else if (tfMin == 240) raw[2] = 1.0f;
        else if (tfMin == 30)  raw[3] = 1.0f;
        else if (tfMin == 360) raw[4] = 1.0f;
        else if (tfMin == 480) raw[5] = 1.0f;
        else if (tfMin == 60)  raw[6] = 1.0f;
        else if (tfMin == 720) raw[7] = 1.0f;
        else if (tfMin == 90)  raw[8] = 1.0f;

        // F05 one-hot: indices 9-12 = Midday, OpeningDrive, Overnight, PreRTH
        if (sess == SESSION_MIDDAY)            raw[9]  = 1.0f;
        else if (sess == SESSION_OPENINGDRIVE) raw[10] = 1.0f;
        else if (sess == SESSION_OVERNIGHT)    raw[11] = 1.0f;
        else if (sess == SESSION_PRERTH)       raw[12] = 1.0f;
        // Close is the reference category (all zeros)

        // F09, F21, F13
        raw[13] = zwAtrRatio;
        raw[14] = zoneAge;
        raw[15] = closePos;

        // F04 one-hot: indices 16-17 = PRIOR_BROKE, PRIOR_HELD
        if (cascade == 2) raw[16] = 1.0f;      // PRIOR_BROKE
        else if (cascade == 0) raw[17] = 1.0f;  // PRIOR_HELD
        // NO_PRIOR is the reference category

        // StandardScaler + linear model
        float score = V32Config::BZ_INTERCEPT;
        for (int i = 0; i < 18; i++)
        {
            float stdVal = V32Config::BZ_STD[i];
            if (stdVal < 1e-8f) stdVal = 1.0f;
            float scaled = (raw[i] - V32Config::BZ_MEAN[i]) / stdVal;
            score += scaled * V32Config::BZ_COEF[i];
        }
        // Sigmoid — convert raw linear to probability (logistic regression)
        return 1.0f / (1.0f + expf(-score));
    };

    // =================================================================
    //  Helper: Find prior penetration for F10
    // =================================================================
    auto FindPriorPenetration = [&](int sigIdx) -> float {
        const SignalRecord& cur = storage->Signals[sigIdx];
        if (cur.TouchSequence <= 1)
            return -1.0f;

        for (int i = sigIdx - 1; i >= 0; i--)
        {
            const SignalRecord& prev = storage->Signals[i];
            if (prev.Type != cur.Type) continue;
            if (fabs(prev.ZoneTop - cur.ZoneTop) > 0.01f) continue;
            if (fabs(prev.ZoneBot - cur.ZoneBot) > 0.01f) continue;
            if (prev.SourceSlot != cur.SourceSlot) continue;
            if (prev.TouchSequence == cur.TouchSequence - 1)
                return prev.PenetrationTicks;
        }
        return -1.0f;
    };

    // =================================================================
    //  Helper: Compute F13 (close position within bar)
    // =================================================================
    auto ComputeF13 = [&](int barIdx, bool isDemand) -> float {
        float h = sc.BaseData[SC_HIGH][barIdx];
        float l = sc.BaseData[SC_LOW][barIdx];
        float c = sc.BaseData[SC_LAST][barIdx];
        if (h == l) return 0.5f;
        if (isDemand)
            return (c - l) / (h - l);
        else
            return (h - c) / (h - l);
    };

    // =================================================================
    //  Helper: M2 position sizing
    // =================================================================
    auto M2Size = [&](float zoneWidthTicks) -> int {
        int t1 = Input_M2_SizeT1.GetInt();
        int t2 = Input_M2_SizeT2.GetInt();
        if (zoneWidthTicks < (float)t1) return Input_M2_Size_Narrow.GetInt();
        if (zoneWidthTicks < (float)t2) return Input_M2_Size_Mid.GetInt();
        return Input_M2_Size_Wide.GetInt();
    };

    // =================================================================
    //  Helper: Drawing management
    // =================================================================
    auto DrawLine = [&](int lineBase, int drawIdx, int startBar, int endBar,
                        float price, COLORREF color)
    {
        s_UseTool tool;
        tool.Clear();
        tool.ChartNumber = sc.ChartNumber;
        tool.DrawingType = DRAWING_LINE;
        tool.AddMethod = UTAM_ADD_OR_ADJUST;
        tool.LineNumber = lineBase + drawIdx;
        tool.Region = 0;
        tool.BeginIndex = startBar;
        tool.BeginValue = price;
        tool.EndIndex = endBar;
        tool.EndValue = price;
        tool.Color = color;
        tool.LineStyle = LINESTYLE_DASH;
        tool.LineWidth = 1;
        tool.AddAsUserDrawnDrawing = 0;
        sc.UseTool(tool);
    };

    auto DrawLabel = [&](int drawIdx, int barIndex, float yValue, const char* text,
                         COLORREF color, bool isDemand)
    {
        s_UseTool tool;
        tool.Clear();
        tool.ChartNumber = sc.ChartNumber;
        tool.DrawingType = DRAWING_TEXT;
        tool.AddMethod = UTAM_ADD_OR_ADJUST;
        tool.LineNumber = V32Config::LN_LABEL + drawIdx;
        tool.Region = 0;
        tool.BeginIndex = barIndex;
        tool.BeginValue = yValue;
        tool.Text.Format("%s", text);
        tool.Color = color;
        tool.FontSize = 9;
        tool.FontBold = 1;
        tool.TextAlignment = DT_CENTER | (isDemand ? DT_TOP : DT_BOTTOM);
        tool.TransparentLabelBackground = 1;
        tool.AddAsUserDrawnDrawing = 0;
        sc.UseTool(tool);
    };

    auto RemoveDrawings = [&](int drawIdx)
    {
        // Remove stop/target/BE lines on position close.
        // Keep entry arrow and mode+score label on chart permanently.
        sc.DeleteACSChartDrawing(sc.ChartNumber, TOOL_DELETE_CHARTDRAWING,
                                 V32Config::LN_STOP + drawIdx);
        sc.DeleteACSChartDrawing(sc.ChartNumber, TOOL_DELETE_CHARTDRAWING,
                                 V32Config::LN_T1 + drawIdx);
        sc.DeleteACSChartDrawing(sc.ChartNumber, TOOL_DELETE_CHARTDRAWING,
                                 V32Config::LN_T2 + drawIdx);
        sc.DeleteACSChartDrawing(sc.ChartNumber, TOOL_DELETE_CHARTDRAWING,
                                 V32Config::LN_BE + drawIdx);
    };

    // =================================================================
    //  Helper: Circuit breaker check
    // =================================================================
    auto CBTriggered = [&]() -> bool {
        if (!Input_CB_Enabled.GetBoolean()) return false;

        // Daily loss: optionally include unrealized P&L from open position
        bool dailyHit = cb.CB_Daily;
        if (!dailyHit && Input_CB_DailyInclOpen.GetBoolean() && pos.InTrade)
        {
            float curPrice = sc.Close[sc.Index];
            float openTicks = (curPrice - pos.EntryPrice) / tickSize * (float)pos.Direction;
            float openPnl = pos.PartialPnlTicks + openTicks * (float)pos.RemainingContracts;
            float totalDaily = cb.DailyPnl + openPnl;
            if (totalDaily <= -(float)Input_CB_DailyLoss.GetInt())
                dailyHit = true;
        }

        return dailyHit || cb.CB_Consec || cb.CB_Drawdown || cb.CB_RollingPF;
    };

    auto CBStateStr = [&](char* buf, int bufLen) {
        snprintf(buf, bufLen, "D%d|C%d|DD%d|PF%d",
                 cb.CB_Daily ? 1 : 0, cb.CB_Consec ? 1 : 0,
                 cb.CB_Drawdown ? 1 : 0, cb.CB_RollingPF ? 1 : 0);
    };

    // =================================================================
    //  Helper: CSV logging
    // =================================================================
    auto LogDecision = [&](const SignalRecord& sig, int tfMin, float aeqScore,
                           float bzScore, int mode, const char* skipReason,
                           float entryPrice, int contracts, float stopPrice,
                           float targetPrice)
    {
        if (!Input_LogEnabled.GetBoolean()) return;

        SCString path;
        path.Format("%sATEAM_ZONE_TOUCH_V32_decisions.csv",
                    sc.DataFilesFolder().GetChars());

        FILE* f = nullptr;
        if (!pState->DecisionLogHeaderWritten)
        {
            f = fopen(path.GetChars(), "w");
            if (f)
            {
                fprintf(f, "datetime,touch_id,zone_type,zone_edge,zone_width,"
                        "zone_tf,seq,aeq_score,bzscore,mode,skip_reason,"
                        "entry_price,contracts,stop_price,target_price,cb_state\n");
                pState->DecisionLogHeaderWritten = 1;
            }
        }
        else
        {
            f = fopen(path.GetChars(), "a");
        }
        if (!f) return;

        int h, m, s;
        SCDateTime dt = sc.BaseDateTimeIn[sig.BarIndex < sc.ArraySize ? sig.BarIndex : sc.Index];
        dt.GetTimeHMS(h, m, s);
        int yr, mo, dy;
        dt.GetDateYMD(yr, mo, dy);

        char cbBuf[32];
        CBStateStr(cbBuf, sizeof(cbBuf));

        fprintf(f, "%04d-%02d-%02d %02d:%02d:%02d,%d,%s,%.2f,%.1f,%d,%d,"
                "%.2f,%.4f,%d,%s,%.2f,%d,%.2f,%.2f,%s\n",
                yr, mo, dy, h, m, s,
                sig.BarIndex,
                sig.Type == 0 ? "DEMAND" : "SUPPLY",
                sig.Type == 0 ? sig.ZoneBot : sig.ZoneTop,
                sig.ZoneWidthTicks,
                tfMin,
                sig.TouchSequence,
                aeqScore, bzScore,
                mode,
                skipReason,
                entryPrice, contracts, stopPrice, targetPrice,
                cbBuf);
        fclose(f);
    };

    auto LogTrade = [&](const PositionState& p, float exitPrice, ExitType exitType,
                        float pnlTicks, int barsHeld)
    {
        if (!Input_LogEnabled.GetBoolean()) return;

        SCString path;
        path.Format("%sATEAM_ZONE_TOUCH_V32_trades.csv",
                    sc.DataFilesFolder().GetChars());

        FILE* f = nullptr;
        if (!pState->TradeLogHeaderWritten)
        {
            f = fopen(path.GetChars(), "w");
            if (f)
            {
                fprintf(f, "datetime,mode,direction,entry_price,exit_price,"
                        "exit_type,contracts,pnl_ticks,pnl_total,bars_held,"
                        "mfe,mae,signal_idx,zone_width\n");
                pState->TradeLogHeaderWritten = 1;
            }
        }
        else
        {
            f = fopen(path.GetChars(), "a");
        }
        if (!f) return;

        int h, m, s;
        SCDateTime dt = sc.BaseDateTimeIn[sc.Index];
        dt.GetTimeHMS(h, m, s);
        int yr, mo, dy;
        dt.GetDateYMD(yr, mo, dy);

        float pnlTotal = pnlTicks * tickValue;

        fprintf(f, "%04d-%02d-%02d %02d:%02d:%02d,M%d,%s,%.2f,%.2f,%s,%d,"
                "%.1f,%.2f,%d,%.1f,%.1f,%d,%.1f\n",
                yr, mo, dy, h, m, s,
                (int)p.Mode,
                p.Direction == 1 ? "LONG" : "SHORT",
                p.EntryPrice, exitPrice,
                ExitTypeStr[exitType],
                p.TotalContracts,
                pnlTicks, pnlTotal, barsHeld,
                p.MFE, p.MAE, p.SignalIdx, p.ZoneWidthTicks);
        fclose(f);
    };

    // =================================================================
    //  Helper: Close position and update circuit breakers
    // =================================================================
    auto ClosePosition = [&](float exitPrice, ExitType exitType)
    {
        if (!pos.InTrade) return;

        int barsHeld = sc.Index - pos.EntryBar;
        // PnL for remaining contracts at exit price
        float exitTicks = (exitPrice - pos.EntryPrice) / tickSize * (float)pos.Direction;
        float remainingPnl = exitTicks * (float)pos.RemainingContracts;
        // Total PnL = partial exits already realized + remaining - total cost
        float pnlTicks = pos.PartialPnlTicks + remainingPnl
                         - (float)(V32Config::COST_TICKS * pos.TotalContracts);

        // Log trade
        LogTrade(pos, exitPrice, exitType, pnlTicks, barsHeld);

        // Update circuit breaker state
        cb.DailyPnl += pnlTicks;
        cb.CurrentEquity += pnlTicks;
        if (cb.CurrentEquity > cb.EquityHWM)
            cb.EquityHWM = cb.CurrentEquity;

        // Ring buffer for rolling PF
        cb.TradeRing[cb.RingHead] = pnlTicks;
        cb.RingHead = (cb.RingHead + 1) % CB_RING_SIZE;
        if (cb.RingCount < CB_RING_SIZE) cb.RingCount++;

        // Consecutive losses
        if (pnlTicks < 0.0f)
            cb.ConsecLosses++;
        else
            cb.ConsecLosses = 0;

        // Check breakers
        if (Input_CB_Enabled.GetBoolean())
        {
            // Daily loss
            if (cb.DailyPnl <= -(float)Input_CB_DailyLoss.GetInt())
                cb.CB_Daily = true;

            // Consecutive losses
            if (cb.ConsecLosses >= Input_CB_MaxConsec.GetInt())
                cb.CB_Consec = true;

            // Max drawdown from HWM
            float dd = cb.EquityHWM - cb.CurrentEquity;
            if (dd >= (float)Input_CB_MaxDrawdown.GetInt())
                cb.CB_Drawdown = true;

            // Rolling PF (only after full window)
            int window = Input_CB_RollingWindow.GetInt();
            if (cb.RingCount >= window)
            {
                float wins = 0.0f, losses = 0.0f;
                for (int i = 0; i < window; i++)
                {
                    int idx = (cb.RingHead - window + i + CB_RING_SIZE) % CB_RING_SIZE;
                    if (cb.TradeRing[idx] > 0.0f)
                        wins += cb.TradeRing[idx];
                    else
                        losses += (float)fabs(cb.TradeRing[idx]);
                }
                if (losses > 0.0f)
                {
                    float pf = wins / losses;
                    if (pf < Input_CB_RollingPF.GetFloat())
                        cb.CB_RollingPF = true;
                }
            }
        }

        // Remove drawings
        RemoveDrawings(pos.DrawIdx);

        // Clear position
        memset(&pos, 0, sizeof(PositionState));
    };

    // =================================================================
    //  Session reset — daily loss breaker auto-resets
    // =================================================================
    if (barDay != cb.LastSessionDay && cb.LastSessionDay != 0)
    {
        cb.DailyPnl = 0.0f;
        cb.CB_Daily = false;
    }
    cb.LastSessionDay = barDay;

    // =================================================================
    //  Pending entry resolution
    // =================================================================
    if (pending.HasPending && !pos.InTrade)
    {
        bool filled = false;
        float fillPrice = 0.0f;

        if (Input_EntryOffset.GetInt() == 0)
        {
            // Market entry — fill on this bar's open
            fillPrice = sc.BaseData[SC_OPEN][sc.Index];
            filled = true;
        }
        else
        {
            // Limit entry — check if price touched limit
            float limitPrice = pending.LimitPrice;
            if (pending.Direction == 1) // LONG: limit below
            {
                if (sc.BaseData[SC_LOW][sc.Index] <= limitPrice)
                {
                    fillPrice = limitPrice;
                    filled = true;
                }
            }
            else // SHORT: limit above
            {
                if (sc.BaseData[SC_HIGH][sc.Index] >= limitPrice)
                {
                    fillPrice = limitPrice;
                    filled = true;
                }
            }

            // Timeout check
            if (!filled && sc.Index >= pending.TimeoutBar)
            {
                // Cancel pending
                if (Input_LogEnabled.GetBoolean() && pending.SignalIdx >= 0 &&
                    pending.SignalIdx < storage->SignalCount)
                {
                    LogDecision(storage->Signals[pending.SignalIdx],
                                GetTFMinutes(storage->Signals[pending.SignalIdx].SourceSlot),
                                pending.AeqScore, pending.BzScore,
                                (int)pending.Mode, "LIMIT_TIMEOUT",
                                0, 0, 0, 0);
                }
                memset(&pending, 0, sizeof(PendingEntryState));
                return;
            }
        }

        if (filled)
        {
            // Submit order to SC with OCO attached stop/target groups
            s_SCNewOrder entryOrder;
            entryOrder.OrderType = SCT_ORDERTYPE_MARKET;
            entryOrder.TimeInForce = SCT_TIF_GTC;
            entryOrder.OrderQuantity = pending.TotalContracts;

            if (pending.Mode == MODE_M1)
            {
                // M1: 2 OCO groups — Group1 = T1 contracts, Group2 = T2 contracts
                entryOrder.OCOGroup1Quantity = pending.T1Contracts;
                entryOrder.Target1Offset     = pending.T1Ticks * tickSize;
                entryOrder.Stop1Offset       = pending.StopTicks * tickSize;

                if (pending.T2Contracts > 0)
                {
                    entryOrder.OCOGroup2Quantity = pending.T2Contracts;
                    entryOrder.Target2Offset     = pending.T2Ticks * tickSize;
                    entryOrder.Stop2Offset       = pending.StopTicks * tickSize;
                }
            }
            else
            {
                // M2: 1 OCO group — all contracts at single target/stop
                entryOrder.OCOGroup1Quantity = pending.TotalContracts;
                entryOrder.Target1Offset     = pending.TargetTicks * tickSize;
                entryOrder.Stop1Offset       = pending.StopTicks * tickSize;
            }

            int result = 0;
            if (pending.Direction == 1)
                result = (int)sc.BuyEntry(entryOrder);
            else
                result = (int)sc.SellEntry(entryOrder);

            if (result > 0)
            {
                // Populate position state
                pos.InTrade = true;
                pos.Mode = pending.Mode;
                pos.Direction = pending.Direction;
                pos.EntryPrice = fillPrice;
                pos.EntryBar = sc.Index;
                pos.TotalContracts = pending.TotalContracts;
                pos.RemainingContracts = pending.TotalContracts;
                pos.TimeCap = pending.TimeCap;
                pos.SignalIdx = pending.SignalIdx;
                pos.ZoneWidthTicks = pending.ZoneWidthTicks;
                pos.MFE = 0.0f;
                pos.MAE = 0.0f;
                pos.BEActive = false;

                // Assign draw index
                pState->DrawCount++;
                if (pState->DrawCount >= V32Config::MAX_DRAWINGS)
                    pState->DrawCount = 1;
                pos.DrawIdx = pState->DrawCount;

                // Compute stop and targets
                if (pending.Mode == MODE_M1)
                {
                    float stopOff = pending.StopTicks * tickSize;
                    float t1Off   = pending.T1Ticks * tickSize;
                    float t2Off   = pending.T2Ticks * tickSize;

                    if (pending.Direction == 1)
                    {
                        pos.StopPrice   = fillPrice - stopOff;
                        pos.Partial.T1Price = fillPrice + t1Off;
                        pos.Partial.T2Price = fillPrice + t2Off;
                    }
                    else
                    {
                        pos.StopPrice   = fillPrice + stopOff;
                        pos.Partial.T1Price = fillPrice - t1Off;
                        pos.Partial.T2Price = fillPrice - t2Off;
                    }
                    pos.Partial.T1Contracts = pending.T1Contracts;
                    pos.Partial.T2Contracts = pending.T2Contracts;
                    pos.Partial.T1Hit = false;
                    pos.Partial.T2Hit = false;
                    pos.TargetPrice = pos.Partial.T2Price;
                    pos.OriginalStopPrice = pos.StopPrice;

                    // Draw: entry, stop, T1, T2
                    int endBar = sc.Index + pos.TimeCap;
                    DrawLine(V32Config::LN_STOP, pos.DrawIdx, sc.Index, endBar,
                             pos.StopPrice, RGB(200, 0, 0));
                    DrawLine(V32Config::LN_T1, pos.DrawIdx, sc.Index, endBar,
                             pos.Partial.T1Price, RGB(0, 180, 0));
                    DrawLine(V32Config::LN_T2, pos.DrawIdx, sc.Index, endBar,
                             pos.Partial.T2Price, RGB(0, 120, 255));
                }
                else // MODE_M2
                {
                    float stopOff   = pending.StopTicks * tickSize;
                    float targetOff = pending.TargetTicks * tickSize;

                    if (pending.Direction == 1)
                    {
                        pos.StopPrice   = fillPrice - stopOff;
                        pos.TargetPrice = fillPrice + targetOff;
                    }
                    else
                    {
                        pos.StopPrice   = fillPrice + stopOff;
                        pos.TargetPrice = fillPrice - targetOff;
                    }

                    int endBar = sc.Index + pos.TimeCap;
                    DrawLine(V32Config::LN_STOP, pos.DrawIdx, sc.Index, endBar,
                             pos.StopPrice, RGB(200, 0, 0));
                    DrawLine(V32Config::LN_T1, pos.DrawIdx, sc.Index, endBar,
                             pos.TargetPrice, RGB(0, 180, 0));
                }

                // Entry arrow subgraph (separate up/down subgraphs)
                bool isDemand = (pending.Direction == 1);
                if (pending.Mode == MODE_M1)
                {
                    if (isDemand)
                        SG_M1Long[sc.Index] = sc.BaseData[SC_LOW][sc.Index] - 30.0f * tickSize;
                    else
                        SG_M1Short[sc.Index] = sc.BaseData[SC_HIGH][sc.Index] + 30.0f * tickSize;
                }
                else
                {
                    if (isDemand)
                        SG_M2Long[sc.Index] = sc.BaseData[SC_LOW][sc.Index] - 30.0f * tickSize;
                    else
                        SG_M2Short[sc.Index] = sc.BaseData[SC_HIGH][sc.Index] + 30.0f * tickSize;
                }

                // Entry label
                COLORREF labelColor = (pending.Mode == MODE_M1)
                    ? RGB(0, 200, 0) : RGB(0, 128, 255);
                char labelBuf[32];
                if (pending.Mode == MODE_M1)
                    snprintf(labelBuf, sizeof(labelBuf), "M1 %.1f", pending.AeqScore);
                else
                    snprintf(labelBuf, sizeof(labelBuf), "M2 %.2f", pending.BzScore);

                float labelY = isDemand
                    ? sc.BaseData[SC_LOW][sc.Index] - 50.0f * tickSize
                    : sc.BaseData[SC_HIGH][sc.Index] + 50.0f * tickSize;
                DrawLabel(pos.DrawIdx, sc.Index, labelY, labelBuf, labelColor, isDemand);
            }

            // Clear pending regardless of fill success
            memset(&pending, 0, sizeof(PendingEntryState));
        }
    }

    // =================================================================
    //  Position exit management (priority: EOD > Stop > Target > TimeCap)
    // =================================================================
    if (pos.InTrade)
    {
        float barHigh = sc.BaseData[SC_HIGH][sc.Index];
        float barLow  = sc.BaseData[SC_LOW][sc.Index];
        float barClose = sc.BaseData[SC_LAST][sc.Index];

        // Update MFE/MAE
        float curExcursion = (barClose - pos.EntryPrice) / tickSize * (float)pos.Direction;
        if (curExcursion > pos.MFE) pos.MFE = curExcursion;
        if (curExcursion < pos.MAE) pos.MAE = curExcursion;

        float highExcursion = (pos.Direction == 1)
            ? (barHigh - pos.EntryPrice) / tickSize
            : (pos.EntryPrice - barLow) / tickSize;
        float lowExcursion = (pos.Direction == 1)
            ? (barLow - pos.EntryPrice) / tickSize
            : (pos.EntryPrice - barHigh) / tickSize;
        if (highExcursion > pos.MFE) pos.MFE = highExcursion;
        if (lowExcursion < pos.MAE) pos.MAE = lowExcursion;

        // --- 1. EOD check ---
        int eodClose = Input_EOD_CloseHHMM.GetInt();
        if (barHHMM >= eodClose)
        {
            // Flatten at market
            s_SCNewOrder exitOrder;
            exitOrder.OrderType = SCT_ORDERTYPE_MARKET;
            exitOrder.TimeInForce = SCT_TIF_GTC;
            exitOrder.OrderQuantity = pos.RemainingContracts;
            if (pos.Direction == 1)
                sc.SellExit(exitOrder);
            else
                sc.BuyExit(exitOrder);

            ClosePosition(barClose, EXIT_EOD);
            goto doneExitCheck;
        }

        // --- 2. Stop check (checked BEFORE target — conservative) ---
        {
            bool stopHit = false;
            if (pos.Direction == 1 && barLow <= pos.StopPrice)
                stopHit = true;
            else if (pos.Direction == -1 && barHigh >= pos.StopPrice)
                stopHit = true;

            if (stopHit)
            {
                s_SCNewOrder exitOrder;
                exitOrder.OrderType = SCT_ORDERTYPE_MARKET;
                exitOrder.TimeInForce = SCT_TIF_GTC;
                exitOrder.OrderQuantity = pos.RemainingContracts;
                if (pos.Direction == 1)
                    sc.SellExit(exitOrder);
                else
                    sc.BuyExit(exitOrder);

                ClosePosition(pos.StopPrice, EXIT_STOP);
                goto doneExitCheck;
            }
        }

        // --- 3. Target check ---
        if (pos.Mode == MODE_M1)
        {
            // M1 partial exits
            // T1 check
            if (!pos.Partial.T1Hit)
            {
                bool t1Hit = false;
                if (pos.Direction == 1 && barHigh >= pos.Partial.T1Price)
                    t1Hit = true;
                else if (pos.Direction == -1 && barLow <= pos.Partial.T1Price)
                    t1Hit = true;

                if (t1Hit)
                {
                    pos.Partial.T1Hit = true;
                    pos.RemainingContracts -= pos.Partial.T1Contracts;
                    // Accumulate realized PnL for the T1 partial
                    float t1Ticks = (pos.Partial.T1Price - pos.EntryPrice) / tickSize
                                    * (float)pos.Direction;
                    pos.PartialPnlTicks += t1Ticks * (float)pos.Partial.T1Contracts;

                    // Submit partial exit
                    s_SCNewOrder exitOrder;
                    exitOrder.OrderType = SCT_ORDERTYPE_MARKET;
                    exitOrder.TimeInForce = SCT_TIF_GTC;
                    exitOrder.OrderQuantity = pos.Partial.T1Contracts;
                    if (pos.Direction == 1)
                        sc.SellExit(exitOrder);
                    else
                        sc.BuyExit(exitOrder);

                    // Move stop to breakeven
                    if (Input_M1_BE_After_T1.GetBoolean())
                    {
                        pos.StopPrice = pos.EntryPrice;
                        pos.BEActive = true;

                        // Modify the remaining attached stop order (Group 2)
                        // to breakeven price so SC enforces it tick-by-tick
                        {
                            s_SCTradeOrder orderInfo;
                            int oi = 0;
                            while (sc.GetOrderByIndex(oi, orderInfo)
                                   != SCTRADING_ORDER_ERROR)
                            {
                                // Find working stop orders near our original stop
                                bool isWorking =
                                    (orderInfo.OrderStatusCode == SCT_OSC_OPEN);
                                bool isStop =
                                    (orderInfo.OrderTypeAsInt == SCT_ORDERTYPE_STOP);
                                bool priceMatch = (float)fabs(
                                    orderInfo.Price1 - pos.OriginalStopPrice) < 0.50f;

                                if (isWorking && isStop && priceMatch)
                                {
                                    s_SCNewOrder modOrder;
                                    modOrder.InternalOrderID =
                                        orderInfo.InternalOrderID;
                                    modOrder.Price1 = pos.EntryPrice;
                                    sc.ModifyOrder(modOrder);
                                }
                                oi++;
                            }
                        }

                        // Update stop drawing, add BE line
                        int endBar = pos.EntryBar + pos.TimeCap;
                        DrawLine(V32Config::LN_STOP, pos.DrawIdx, sc.Index, endBar,
                                 pos.StopPrice, RGB(200, 0, 0));
                        DrawLine(V32Config::LN_BE, pos.DrawIdx, sc.Index, endBar,
                                 pos.EntryPrice, RGB(255, 255, 0));
                    }

                    // Remove T1 line
                    sc.DeleteACSChartDrawing(sc.ChartNumber, TOOL_DELETE_CHARTDRAWING,
                                             V32Config::LN_T1 + pos.DrawIdx);

                    if (pos.RemainingContracts <= 0)
                    {
                        ClosePosition(pos.Partial.T1Price, EXIT_T1);
                        goto doneExitCheck;
                    }
                }
            }

            // T2 check (only after T1 hit)
            if (pos.Partial.T1Hit && !pos.Partial.T2Hit)
            {
                bool t2Hit = false;
                if (pos.Direction == 1 && barHigh >= pos.Partial.T2Price)
                    t2Hit = true;
                else if (pos.Direction == -1 && barLow <= pos.Partial.T2Price)
                    t2Hit = true;

                if (t2Hit)
                {
                    pos.Partial.T2Hit = true;
                    pos.RemainingContracts -= pos.Partial.T2Contracts;

                    s_SCNewOrder exitOrder;
                    exitOrder.OrderType = SCT_ORDERTYPE_MARKET;
                    exitOrder.TimeInForce = SCT_TIF_GTC;
                    exitOrder.OrderQuantity = pos.Partial.T2Contracts;
                    if (pos.Direction == 1)
                        sc.SellExit(exitOrder);
                    else
                        sc.BuyExit(exitOrder);

                    // Remove T2 line
                    sc.DeleteACSChartDrawing(sc.ChartNumber, TOOL_DELETE_CHARTDRAWING,
                                             V32Config::LN_T2 + pos.DrawIdx);

                    ClosePosition(pos.Partial.T2Price, EXIT_T2);
                    goto doneExitCheck;
                }
            }
        }
        else // MODE_M2
        {
            bool targetHit = false;
            if (pos.Direction == 1 && barHigh >= pos.TargetPrice)
                targetHit = true;
            else if (pos.Direction == -1 && barLow <= pos.TargetPrice)
                targetHit = true;

            if (targetHit)
            {
                s_SCNewOrder exitOrder;
                exitOrder.OrderType = SCT_ORDERTYPE_MARKET;
                exitOrder.TimeInForce = SCT_TIF_GTC;
                exitOrder.OrderQuantity = pos.RemainingContracts;
                if (pos.Direction == 1)
                    sc.SellExit(exitOrder);
                else
                    sc.BuyExit(exitOrder);

                ClosePosition(pos.TargetPrice, EXIT_T1);
                goto doneExitCheck;
            }
        }

        // --- 4. TimeCap check ---
        {
            int barsHeld = sc.Index - pos.EntryBar;
            if (barsHeld >= pos.TimeCap)
            {
                s_SCNewOrder exitOrder;
                exitOrder.OrderType = SCT_ORDERTYPE_MARKET;
                exitOrder.TimeInForce = SCT_TIF_GTC;
                exitOrder.OrderQuantity = pos.RemainingContracts;
                if (pos.Direction == 1)
                    sc.SellExit(exitOrder);
                else
                    sc.BuyExit(exitOrder);

                ClosePosition(barClose, EXIT_TIMECAP);
            }
        }
    }
    doneExitCheck:

    // =================================================================
    //  Cancel pending limit orders at EOD
    // =================================================================
    if (pending.HasPending && barHHMM >= Input_EOD_CloseHHMM.GetInt())
    {
        if (Input_LogEnabled.GetBoolean() && pending.SignalIdx >= 0 &&
            pending.SignalIdx < storage->SignalCount)
        {
            LogDecision(storage->Signals[pending.SignalIdx],
                        GetTFMinutes(storage->Signals[pending.SignalIdx].SourceSlot),
                        pending.AeqScore, pending.BzScore,
                        (int)pending.Mode, "EOD_CANCEL",
                        0, 0, 0, 0);
        }
        memset(&pending, 0, sizeof(PendingEntryState));
    }

    // =================================================================
    //  New signal processing
    // =================================================================
    int sigCount = storage->SignalCount;
    if (sigCount <= pState->LastProcessedSignalCount)
        goto doneSignalProcessing;

    for (int si = pState->LastProcessedSignalCount; si < sigCount; si++)
    {
        const SignalRecord& sig = storage->Signals[si];

        // Only process signals on current bar
        if (sig.BarIndex != sc.Index)
            continue;

        // --- Compute features ---
        int tfMin = GetTFMinutes(sig.SourceSlot);
        bool isDemand = (sig.Type == 0);
        int direction = isDemand ? 1 : -1;

        // F10: Prior penetration
        float priorPen = FindPriorPenetration(si);

        // F05: Session
        SessionName sess = ClassifySession(barMinutesSinceMidnight);

        // F09: ZW/ATR ratio
        float atr = ComputeATR(sc.Index);
        float zwAtrRatio = 0.0f;
        if (atr > 0.0001f)
            zwAtrRatio = (sig.ZoneWidthTicks * tickSize) / atr;

        // F21: Zone age
        float zoneAge = (float)sig.ZoneAgeBars;

        // F13: Close position
        float closePos = ComputeF13(sc.Index, isDemand);

        // --- Scoring ---
        float aeqScore = ScoreF10(priorPen) + ScoreF01(tfMin) + ScoreF05(sess) +
                         ScoreF09(zwAtrRatio) + ScoreF21(zoneAge) +
                         ScoreF13(closePos) + ScoreF04(sig.CascadeState);

        float bzScore = ComputeBZScore(priorPen, tfMin, sess, zwAtrRatio,
                                       zoneAge, closePos, sig.CascadeState);

        // --- Circuit breaker check ---
        if (CBTriggered())
        {
            LogDecision(sig, tfMin, aeqScore, bzScore, 0, "CB_TRIGGERED",
                        0, 0, 0, 0);
            continue;
        }

        // --- EOD blackout check ---
        int blackoutHHMM = Input_EOD_BlackoutHHMM.GetInt();
        if (barHHMM >= blackoutHHMM)
        {
            LogDecision(sig, tfMin, aeqScore, bzScore, 0, "EOD_BLACKOUT",
                        0, 0, 0, 0);
            continue;
        }

        // --- Waterfall ---
        int selectedMode = 0;
        const char* skipReason = "";

        // Step 1: Check M1 (A-Eq)
        bool m1Candidate = (aeqScore >= Input_M1_Threshold.GetFloat());

        // Step 2: If not M1, check M2 (B-ZScore) with filters
        bool m2Candidate = false;
        if (!m1Candidate)
        {
            bool passThreshold = (bzScore >= Input_M2_Threshold.GetFloat());
            bool passRTH = (!Input_M2_RTHOnly.GetBoolean()) ||
                           IsRTH(barMinutesSinceMidnight);
            bool passSeq = (sig.TouchSequence <= Input_M2_MaxSeq.GetInt());
            bool passTF  = (tfMin > 0 && tfMin <= Input_M2_MaxTF.GetInt());

            if (passThreshold && passRTH && passSeq && passTF)
                m2Candidate = true;
            else if (passThreshold)
            {
                if (!passRTH) skipReason = "M2_NOT_RTH";
                else if (!passSeq) skipReason = "M2_SEQ_EXCEEDED";
                else if (!passTF) skipReason = "M2_TF_EXCEEDED";
            }
        }

        if (m1Candidate)
            selectedMode = 1;
        else if (m2Candidate)
            selectedMode = 2;

        // --- Position check ---
        if (selectedMode > 0 && pos.InTrade)
        {
            // Preemption: M1 candidate can close M2 position
            if (selectedMode == 1 && pos.Mode == MODE_M2 &&
                Input_Preemption.GetBoolean())
            {
                // Close M2
                float exitPrice = sc.BaseData[SC_LAST][sc.Index];
                s_SCNewOrder exitOrder;
                exitOrder.OrderType = SCT_ORDERTYPE_MARKET;
                exitOrder.TimeInForce = SCT_TIF_GTC;
                exitOrder.OrderQuantity = pos.RemainingContracts;
                if (pos.Direction == 1)
                    sc.SellExit(exitOrder);
                else
                    sc.BuyExit(exitOrder);
                ClosePosition(exitPrice, EXIT_PREEMPT);
                // Fall through to enter M1
            }
            else
            {
                // Already in position, skip — but draw skipped arrow + label
                {
                    bool skDemand = (direction == 1);
                    // Pick the correct directional subgraph
                    SCSubgraphRef& skSG =
                        (selectedMode == 1)
                            ? (skDemand ? SG_M1SkipLong : SG_M1SkipShort)
                            : (skDemand ? SG_M2SkipLong : SG_M2SkipShort);
                    float skY = skDemand
                        ? sc.BaseData[SC_LOW][sc.Index] - 30.0f * tickSize
                        : sc.BaseData[SC_HIGH][sc.Index] + 30.0f * tickSize;
                    skSG[sc.Index] = skY;

                    // Draw label for skipped signal
                    pState->DrawCount++;
                    if (pState->DrawCount >= V32Config::MAX_DRAWINGS)
                        pState->DrawCount = 1;
                    COLORREF skColor = RGB(80, 80, 80);
                    char skLabel[32];
                    if (selectedMode == 1)
                        snprintf(skLabel, sizeof(skLabel), "M1 %.1f", aeqScore);
                    else
                        snprintf(skLabel, sizeof(skLabel), "M2 %.2f", bzScore);
                    float skLabelY = skDemand
                        ? sc.BaseData[SC_LOW][sc.Index] - 50.0f * tickSize
                        : sc.BaseData[SC_HIGH][sc.Index] + 50.0f * tickSize;
                    DrawLabel(pState->DrawCount, sig.BarIndex, skLabelY,
                              skLabel, skColor, skDemand);
                }
                LogDecision(sig, tfMin, aeqScore, bzScore, selectedMode,
                            "POSITION_OPEN", 0, 0, 0, 0);
                continue;
            }
        }

        if (selectedMode == 0)
        {
            if (strlen(skipReason) == 0)
                skipReason = (aeqScore < Input_M1_Threshold.GetFloat() &&
                              bzScore < Input_M2_Threshold.GetFloat())
                    ? "BELOW_THRESHOLD" : "FILTER_REJECT";
            LogDecision(sig, tfMin, aeqScore, bzScore, 0, skipReason,
                        0, 0, 0, 0);
            continue;
        }

        // --- Pending entry already exists? ---
        if (pending.HasPending)
        {
            // Draw skipped arrow + label
            {
                bool skDemand = (direction == 1);
                SCSubgraphRef& skSG =
                    (selectedMode == 1)
                        ? (skDemand ? SG_M1SkipLong : SG_M1SkipShort)
                        : (skDemand ? SG_M2SkipLong : SG_M2SkipShort);
                float skY = skDemand
                    ? sc.BaseData[SC_LOW][sc.Index] - 30.0f * tickSize
                    : sc.BaseData[SC_HIGH][sc.Index] + 30.0f * tickSize;
                skSG[sc.Index] = skY;

                pState->DrawCount++;
                if (pState->DrawCount >= V32Config::MAX_DRAWINGS)
                    pState->DrawCount = 1;
                COLORREF skColor = (selectedMode == 1)
                    ? RGB(0, 120, 0) : RGB(0, 80, 160);
                char skLabel[32];
                if (selectedMode == 1)
                    snprintf(skLabel, sizeof(skLabel), "M1 %.1f", aeqScore);
                else
                    snprintf(skLabel, sizeof(skLabel), "M2 %.2f", bzScore);
                float skLabelY = skDemand
                    ? sc.BaseData[SC_LOW][sc.Index] - 50.0f * tickSize
                    : sc.BaseData[SC_HIGH][sc.Index] + 50.0f * tickSize;
                DrawLabel(pState->DrawCount, sig.BarIndex, skLabelY,
                          skLabel, skColor, skDemand);
            }
            LogDecision(sig, tfMin, aeqScore, bzScore, selectedMode,
                        "PENDING_EXISTS", 0, 0, 0, 0);
            continue;
        }

        // --- Build pending entry ---
        pending.HasPending = true;
        pending.Mode = (selectedMode == 1) ? MODE_M1 : MODE_M2;
        pending.Direction = direction;
        pending.SignalIdx = si;
        pending.AeqScore = aeqScore;
        pending.BzScore = bzScore;
        pending.ZoneWidthTicks = sig.ZoneWidthTicks;

        int entryOffset = Input_EntryOffset.GetInt();
        pending.TimeoutBar = sc.Index + Input_EntryTimeout.GetInt();

        if (entryOffset > 0)
        {
            // Limit entry: offset ticks deeper into zone from touch edge
            // Demand: touch at ZoneTop, deeper = lower → ZoneTop - offset
            // Supply: touch at ZoneBot, deeper = higher → ZoneBot + offset
            if (isDemand)
                pending.LimitPrice = sig.ZoneTop - (float)entryOffset * tickSize;
            else
                pending.LimitPrice = sig.ZoneBot + (float)entryOffset * tickSize;
        }
        else
        {
            pending.LimitPrice = 0.0f; // market
        }

        if (selectedMode == 1) // M1
        {
            pending.TotalContracts = Input_M1_TotalContracts.GetInt();
            pending.StopTicks  = (float)Input_M1_StopTicks.GetInt();
            pending.T1Ticks    = (float)Input_M1_T1_Ticks.GetInt();
            pending.T2Ticks    = (float)Input_M1_T2_Ticks.GetInt();
            pending.T1Contracts = Input_M1_T1_Contracts.GetInt();
            pending.T2Contracts = Input_M1_T2_Contracts.GetInt();
            pending.TimeCap    = Input_M1_TimeCap.GetInt();
            pending.TargetTicks = 0.0f;
        }
        else // M2
        {
            pending.TotalContracts = M2Size(sig.ZoneWidthTicks);
            float stopTicks = Input_M2_StopMult.GetFloat() * sig.ZoneWidthTicks;
            if (stopTicks < (float)Input_M2_StopFloor.GetInt())
                stopTicks = (float)Input_M2_StopFloor.GetInt();
            pending.StopTicks   = stopTicks;
            pending.TargetTicks = Input_M2_TargetMult.GetFloat() * sig.ZoneWidthTicks;
            pending.T1Ticks     = 0.0f;
            pending.T2Ticks     = 0.0f;
            pending.T1Contracts = 0;
            pending.T2Contracts = 0;
            pending.TimeCap     = Input_M2_TimeCap.GetInt();
        }

        // Log the entry decision
        float expectedEntry = (entryOffset > 0) ? pending.LimitPrice
            : sc.BaseData[SC_LAST][sc.Index];
        float expectedStop = (direction == 1)
            ? expectedEntry - pending.StopTicks * tickSize
            : expectedEntry + pending.StopTicks * tickSize;
        float expectedTarget = 0.0f;
        if (selectedMode == 1)
        {
            expectedTarget = (direction == 1)
                ? expectedEntry + pending.T2Ticks * tickSize
                : expectedEntry - pending.T2Ticks * tickSize;
        }
        else
        {
            expectedTarget = (direction == 1)
                ? expectedEntry + pending.TargetTicks * tickSize
                : expectedEntry - pending.TargetTicks * tickSize;
        }

        LogDecision(sig, tfMin, aeqScore, bzScore, selectedMode, "ENTRY",
                     expectedEntry, pending.TotalContracts,
                     expectedStop, expectedTarget);
    }

    pState->LastProcessedSignalCount = sigCount;

    doneSignalProcessing:
    (void)0; // label requires a statement
}
