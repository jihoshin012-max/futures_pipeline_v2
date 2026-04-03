// STUDY VERSION LOG
// Base: ATEAM_ROTATION_V3_FULL
// Fork: Full study with all validated entry gates + hold mechanic
//
// archetype: rotational
// @study: ATEAM Rotation V3 Full
// @version: FULL-1.0
// @author: ATEAM
// @type: trading-system + batch-test
// @summary: Complete rotational NQ strategy with stacked entry gates
//           and d2_avg3 in-trade hold mechanic.
//
//   Entry gates (stacked, all must pass):
//     1. chop < 0.10           (parent study, inter-study ref)
//     2. dR2 <= -0.40          (Track A, inline linreg lb=3)
//     3. dSlope <= -2.0        (Track A, inline linreg lb=3)
//     4. fade_confirm < 0.40   (Track B, prev completed bar)
//     5. d2_ema9 entry gate    (Track B2, |d2|<=0.5 neutral zone)
//
//   In-trade hold (Track B2):
//     At reversal trigger: if d2_avg3 aligned with direction -> HOLD
//     Exit on d2_avg3 flip (D2_EXIT), hard stop, or EOD
//
//   Frozen config: rotational-NQ-ema-directional-params-frozen.json
//   Validation: P1+P2 PASS, E[R]=$225, PF=4.04, 84% WR, 13/13 weeks
//
// CHANGE LOG
// ----------
// [2026-03-30 FULL-1.0] Fork from CHOP-1.0
//   - Added Inputs 19-27: Track A, B, B2 parameters
//   - Added Subgraphs 1-8: inline feature computation (R2, Slope, dR2,
//     dSlope, EMA9, dEMA9, d2EMA9, d2Avg3)
//   - Track A: dR2/dSlope entry gate (skip when dr2 > -0.40 OR dslope > -2.0)
//   - Track B: fade_confirm entry gate (skip when fc >= 0.40, uses prev bar)
//   - Track B2 entry: d2_ema9 three-state gate (block against-trend outside neutral)
//   - Track B2 hold: d2_avg3 delays reversal when curvature aligned
//   - D2_EXIT: new exit type when d2_avg3 flips against direction during hold
//   - All entry gates applied on both SEED and REVERSAL re-entry
//   - Test mode: extended pass-1 to compute all features per agg bar
//   - Test mode: extended pass-2 with hold state machine
//   - Events CSV: added feature value columns for debugging
//   - Cycles CSV: added hold_count column
//   - Live mode: inline feature computation via subgraphs in autoloop
//   - Live mode: FlattenPending=2 for D2_EXIT (no re-enter)
//   - Renamed DLL and study function to ATEAM_ROTATION_V3_FULL
//
// PRIOR LOG (from CHOP-1.0 -> LP-1.1):
//   See rotational-NQ-study-chop.cpp for full history

#include "sierrachart.h"
#include <cstdio>
#include <cmath>
#include <cstring>

SCDLLName("ATEAM_ROTATION_V3_FULL")

// =========================================================================
//  Helpers
// =========================================================================
static int TimeToSeconds(int Hour, int Minute, int Second)
{
    return Hour * 3600 + Minute * 60 + Second;
}

static const int RTH_OPEN_SEC  = 9 * 3600 + 30 * 60;
static const int RTH_CLOSE_SEC = 15 * 3600 + 49 * 60 + 50;

// Linear regression on 3 equally-spaced points (x=0,1,2)
static void LinReg3(float y0, float y1, float y2, float& outSlope, float& outR2)
{
    float meanY = (y0 + y1 + y2) / 3.0f;
    outSlope = (y2 - y0) / 2.0f;
    float intercept = meanY - outSlope;
    float yh0 = intercept;
    float yh1 = intercept + outSlope;
    float yh2 = intercept + 2.0f * outSlope;
    float ssRes = (y0 - yh0) * (y0 - yh0) + (y1 - yh1) * (y1 - yh1) + (y2 - yh2) * (y2 - yh2);
    float ssTot = (y0 - meanY) * (y0 - meanY) + (y1 - meanY) * (y1 - meanY) + (y2 - meanY) * (y2 - meanY);
    outR2 = (ssTot > 1e-10f) ? (1.0f - ssRes / ssTot) : 1.0f;
}

// =========================================================================
//  Live-mode event CSV logger
// =========================================================================
static void WriteCSV(SCStudyInterfaceRef sc,
    int*        pHeaderWritten,
    const char* Event,
    const char* Side,
    double      Price,
    double      AvgEntryPrice,
    int         PosQty,
    int         AddQty,
    int         Level,
    double      PnlTicks,
    double      ChopValue,
    double      StepDist,
    int         MaxLevels,
    int         MaxContractSize,
    float       DR2Value,
    float       DSlopeValue,
    float       FadeConfValue,
    float       D2EMA9Value,
    float       D2Avg3Value,
    int         HoldState)
{
    SCString FilePath;
    FilePath.Format("%s\\ATEAM_ROTATION_V3_FULL_log.csv", sc.DataFilesFolder().GetChars());

    int NeedHeader = 0;
    if (*pHeaderWritten == 0)
    {
        FILE* fCheck = fopen(FilePath.GetChars(), "r");
        if (fCheck == NULL)
        {
            NeedHeader = 1;
        }
        else
        {
            fseek(fCheck, 0, SEEK_END);
            if (ftell(fCheck) == 0)
                NeedHeader = 1;
            else
                *pHeaderWritten = 1;
            fclose(fCheck);
        }
    }

    FILE* f = fopen(FilePath.GetChars(), "a");
    if (f == NULL)
        return;

    if (NeedHeader)
    {
        fprintf(f,
            "DateTime,Symbol,Event,Side,Price,AvgEntryPrice,PosQty,AddQty,"
            "Level,PnlTicks,ChopValue,StepDist,MaxLevels,MaxContractSize,"
            "DR2,DSlope,FadeConf,D2EMA9,D2Avg3,HoldActive\n");
        *pHeaderWritten = 1;
    }

    int Year, Month, Day, Hour, Minute, Second;
    sc.BaseDateTimeIn[sc.ArraySize - 1].GetDateTimeYMDHMS(Year, Month, Day,
                                                           Hour, Minute, Second);

    fprintf(f,
        "%04d-%02d-%02d %02d:%02d:%02d,%s,%s,%s,"
        "%.2f,%.2f,%d,%d,"
        "%d,%.1f,%.4f,%.2f,%d,%d,"
        "%.4f,%.4f,%.4f,%.4f,%.4f,%d\n",
        Year, Month, Day, Hour, Minute, Second,
        sc.GetChartSymbol(sc.ChartNumber).GetChars(),
        Event, Side,
        Price, AvgEntryPrice, PosQty, AddQty,
        Level, PnlTicks, ChopValue, StepDist, MaxLevels, MaxContractSize,
        DR2Value, DSlopeValue, FadeConfValue, D2EMA9Value, D2Avg3Value, HoldState);

    fclose(f);
}

// =========================================================================
//  CSV TEST MODE — data structures
// =========================================================================
struct TestBar
{
    char  DateTime[32];
    float Open, High, Low, Last;
    int   TimeSec;
    int   DateInt;
};

struct CycleRecord
{
    int   cycleId;
    char  watchStartDT[32];
    float watchPrice;
    float watchHigh;
    float watchLow;
    int   watchBars;
    char  seedDT[32];
    char  exitDT[32];
    char  direction[6];
    float seedPrice;
    float avgEntryPrice;
    float exitPrice;
    char  exitType[16];
    int   depth;
    int   maxPosition;
    float pnlTicks;
    float pnlDollars;
    int   barsHeld;
    float mfeTicks;
    float maeTicks;
    int   holdCount;
};

struct EventRecord
{
    int   cycleId;
    char  datetime[32];
    char  event[20];
    char  side[6];
    float price;
    float avgEntryPrice;
    int   posQty;
    int   addQty;
    int   level;
    float pnlTicks;
    float chopVal;
    float dr2Val;
    float dslopeVal;
    float fcVal;
    float d2Val;
    float d2a3Val;
};

// =========================================================================
//  CSV TEST MODE — batch simulation function
// =========================================================================
static void RunTestMode(SCStudyInterfaceRef sc,
    const char* basePath,
    double StepDist, int InitialQty, int MaxLevels, int MaxContractSize,
    double HardStop, int MaxFades, float TickSize,
    int ChopFilterOn, float ChopThresh, int ChopLookback,
    int EntrySignalOn, float DR2Thresh, float DSlopeThresh,
    int FadeConfirmOn, float FadeConfirmThresh,
    int D2EntryOn, float D2NeutralThresh,
    int D2HoldOn, int EMAPeriod)
{
    // ---------- Load bar data ----------
    const int MAX_BARS = 500000;
    TestBar* bars = (TestBar*)sc.AllocateMemory(MAX_BARS * sizeof(TestBar));
    if (!bars)
    {
        sc.AddMessageToLog("CSV TEST: Failed to allocate bar data", 1);
        return;
    }

    SCString barPath;
    barPath.Format("%s\\NQ-1tick-calibration-1day.csv", basePath);
    FILE* bf = fopen(barPath.GetChars(), "r");
    if (!bf)
    {
        barPath.Format("%sNQ-1tick-calibration-1day.csv", basePath);
        bf = fopen(barPath.GetChars(), "r");
        if (!bf)
        {
            SCString msg;
            msg.Format("CSV TEST: Cannot open %s", barPath.GetChars());
            sc.AddMessageToLog(msg, 1);
            sc.FreeMemory(bars);
            return;
        }
    }

    int nBars = 0;
    {
        char line[4096];
        fgets(line, sizeof(line), bf); // skip header
        while (fgets(line, sizeof(line), bf) && nBars < MAX_BARS)
        {
            char dateStr[32], timeStr[32];
            float o, h, l, c;
            if (sscanf(line, " %31[^,], %31[^,], %f, %f, %f, %f",
                        dateStr, timeStr, &o, &h, &l, &c) < 6)
                continue;

            int hr = 0, mn = 0, sec = 0;
            sscanf(timeStr, " %d:%d:%d", &hr, &mn, &sec);
            int timeSec = TimeToSeconds(hr, mn, sec);

            int yr = 0, mo = 0, dy = 0;
            sscanf(dateStr, " %d-%d-%d", &yr, &mo, &dy);
            int dateInt = yr * 10000 + mo * 100 + dy;

            char dtBuf[32];
            snprintf(dtBuf, sizeof(dtBuf), "%04d-%02d-%02d %02d:%02d:%02d",
                     yr, mo, dy, hr, mn, sec);

            TestBar& b = bars[nBars];
            strncpy(b.DateTime, dtBuf, 31); b.DateTime[31] = '\0';
            b.Open = o; b.High = h; b.Low = l; b.Last = c;
            b.TimeSec = timeSec;
            b.DateInt = dateInt;
            nBars++;
        }
        fclose(bf);
    }

    {
        SCString msg;
        msg.Format("CSV TEST: Loaded %d bars", nBars);
        sc.AddMessageToLog(msg, 0);
    }

    if (nBars == 0)
    {
        sc.FreeMemory(bars);
        return;
    }

    // ---------- Allocate output arrays ----------
    const int MAX_CYCLES = 50000;
    const int MAX_EVENTS = 200000;
    CycleRecord* cycles = (CycleRecord*)sc.AllocateMemory(MAX_CYCLES * sizeof(CycleRecord));
    EventRecord* events = (EventRecord*)sc.AllocateMemory(MAX_EVENTS * sizeof(EventRecord));
    if (!cycles || !events)
    {
        sc.AddMessageToLog("CSV TEST: Failed to allocate output arrays", 1);
        if (cycles) sc.FreeMemory(cycles);
        if (events) sc.FreeMemory(events);
        sc.FreeMemory(bars);
        return;
    }
    memset(cycles, 0, MAX_CYCLES * sizeof(CycleRecord));
    memset(events, 0, MAX_EVENTS * sizeof(EventRecord));
    int nCycles = 0;
    int nEvents = 0;

    // ---------- Pass 1: Aggregate ticks -> bars, compute all features ----------
    const int AGG_SIZE = 250;
    const int MAX_AGG_BARS = 5000;

    float* aggCloseArr  = (float*)sc.AllocateMemory(MAX_AGG_BARS * sizeof(float));
    float* aggRangeArr  = (float*)sc.AllocateMemory(MAX_AGG_BARS * sizeof(float));
    float* aggHighArr   = (float*)sc.AllocateMemory(MAX_AGG_BARS * sizeof(float));
    float* aggLowArr    = (float*)sc.AllocateMemory(MAX_AGG_BARS * sizeof(float));
    float* aggChopArr   = (float*)sc.AllocateMemory(MAX_AGG_BARS * sizeof(float));
    float* aggR2Arr     = (float*)sc.AllocateMemory(MAX_AGG_BARS * sizeof(float));
    float* aggSlopeArr  = (float*)sc.AllocateMemory(MAX_AGG_BARS * sizeof(float));
    float* aggDR2Arr    = (float*)sc.AllocateMemory(MAX_AGG_BARS * sizeof(float));
    float* aggDSlopeArr = (float*)sc.AllocateMemory(MAX_AGG_BARS * sizeof(float));
    float* aggEMA9Arr   = (float*)sc.AllocateMemory(MAX_AGG_BARS * sizeof(float));
    float* aggDEMA9Arr  = (float*)sc.AllocateMemory(MAX_AGG_BARS * sizeof(float));
    float* aggD2EMA9Arr = (float*)sc.AllocateMemory(MAX_AGG_BARS * sizeof(float));
    float* aggD2Avg3Arr = (float*)sc.AllocateMemory(MAX_AGG_BARS * sizeof(float));
    int*   tickToAgg    = (int*)sc.AllocateMemory(nBars * sizeof(int));
    float* tickChop     = (float*)sc.AllocateMemory(nBars * sizeof(float));
    float* tickDR2      = (float*)sc.AllocateMemory(nBars * sizeof(float));
    float* tickDSlope   = (float*)sc.AllocateMemory(nBars * sizeof(float));
    float* tickD2EMA9   = (float*)sc.AllocateMemory(nBars * sizeof(float));
    float* tickD2Avg3   = (float*)sc.AllocateMemory(nBars * sizeof(float));

    if (!aggCloseArr || !aggRangeArr || !aggHighArr || !aggLowArr ||
        !aggChopArr || !aggR2Arr || !aggSlopeArr || !aggDR2Arr || !aggDSlopeArr ||
        !aggEMA9Arr || !aggDEMA9Arr || !aggD2EMA9Arr || !aggD2Avg3Arr ||
        !tickToAgg || !tickChop || !tickDR2 || !tickDSlope || !tickD2EMA9 || !tickD2Avg3)
    {
        sc.AddMessageToLog("CSV TEST: Failed to allocate feature arrays", 1);
        if (aggCloseArr)  sc.FreeMemory(aggCloseArr);
        if (aggRangeArr)  sc.FreeMemory(aggRangeArr);
        if (aggHighArr)   sc.FreeMemory(aggHighArr);
        if (aggLowArr)    sc.FreeMemory(aggLowArr);
        if (aggChopArr)   sc.FreeMemory(aggChopArr);
        if (aggR2Arr)     sc.FreeMemory(aggR2Arr);
        if (aggSlopeArr)  sc.FreeMemory(aggSlopeArr);
        if (aggDR2Arr)    sc.FreeMemory(aggDR2Arr);
        if (aggDSlopeArr) sc.FreeMemory(aggDSlopeArr);
        if (aggEMA9Arr)   sc.FreeMemory(aggEMA9Arr);
        if (aggDEMA9Arr)  sc.FreeMemory(aggDEMA9Arr);
        if (aggD2EMA9Arr) sc.FreeMemory(aggD2EMA9Arr);
        if (aggD2Avg3Arr) sc.FreeMemory(aggD2Avg3Arr);
        if (tickToAgg)    sc.FreeMemory(tickToAgg);
        if (tickChop)     sc.FreeMemory(tickChop);
        if (tickDR2)      sc.FreeMemory(tickDR2);
        if (tickDSlope)   sc.FreeMemory(tickDSlope);
        if (tickD2EMA9)   sc.FreeMemory(tickD2EMA9);
        if (tickD2Avg3)   sc.FreeMemory(tickD2Avg3);
        sc.FreeMemory(events);
        sc.FreeMemory(cycles);
        sc.FreeMemory(bars);
        return;
    }

    // --- Aggregate ticks into bars (mirrors Python _aggregate_bars) ---
    int   aggIdx = 0;
    int   tickCount = 0;
    int   prevDate = -1;
    float curBarHigh = -1e9f, curBarLow = 1e9f, curClose = 0.0f;

    for (int i = 0; i < nBars; i++)
    {
        // No date-change reset — SC counts 250-tick bars continuously
        // from session open (18:00) through next day's close.
        // Removing this reset aligns bar boundaries with SC's native bars.

        if (tickCount == 0)
        {
            curBarHigh = bars[i].High;
            curBarLow  = bars[i].Low;
            curClose   = bars[i].Last;
        }
        else
        {
            if (bars[i].High > curBarHigh) curBarHigh = bars[i].High;
            if (bars[i].Low  < curBarLow)  curBarLow  = bars[i].Low;
            curClose = bars[i].Last;
        }
        tickToAgg[i] = aggIdx;
        tickCount++;

        if (tickCount >= AGG_SIZE)
        {
            aggCloseArr[aggIdx] = curClose;
            aggRangeArr[aggIdx] = curBarHigh - curBarLow;
            aggHighArr[aggIdx]  = curBarHigh;
            aggLowArr[aggIdx]   = curBarLow;
            aggIdx++;
            tickCount = 0;
            curBarHigh = -1e9f;
            curBarLow  = 1e9f;
        }
    }
    int nAgg = aggIdx;
    if (tickCount > 0)
    {
        aggCloseArr[aggIdx] = curClose;
        aggRangeArr[aggIdx] = curBarHigh - curBarLow;
        aggHighArr[aggIdx]  = curBarHigh;
        aggLowArr[aggIdx]   = curBarLow;
        nAgg = aggIdx + 1;
    }

    // --- Compute choppiness per agg bar ---
    for (int a = 0; a < nAgg; a++)
    {
        if (a < ChopLookback - 1)
        {
            aggChopArr[a] = -1.0f;
            continue;
        }
        float netMove = fabsf(aggCloseArr[a] - aggCloseArr[a - ChopLookback + 1]);
        float sumRange = 0.0f;
        for (int j = a - ChopLookback + 1; j <= a; j++)
            sumRange += aggRangeArr[j];
        aggChopArr[a] = (sumRange > 0.0001f) ? (netMove / sumRange) : 0.0f;
    }

    // --- Compute R2, Slope, dR2, dSlope per agg bar (linreg lb=3) ---
    for (int a = 0; a < nAgg; a++)
    {
        if (a < 2)
        {
            aggR2Arr[a] = 1.0f;
            aggSlopeArr[a] = 0.0f;
            aggDR2Arr[a] = 0.0f;
            aggDSlopeArr[a] = 0.0f;
            continue;
        }
        float slope, r2;
        LinReg3(aggCloseArr[a - 2], aggCloseArr[a - 1], aggCloseArr[a], slope, r2);
        aggR2Arr[a] = r2;
        aggSlopeArr[a] = slope;

        if (a >= 3)
        {
            float prevSlope, prevR2;
            LinReg3(aggCloseArr[a - 3], aggCloseArr[a - 2], aggCloseArr[a - 1], prevSlope, prevR2);
            aggDR2Arr[a] = r2 - prevR2;
            aggDSlopeArr[a] = fabsf(slope) - fabsf(prevSlope);
        }
        else
        {
            aggDR2Arr[a] = 0.0f;
            aggDSlopeArr[a] = 0.0f;
        }
    }

    // --- Compute EMA, dEMA, d2EMA, d2Avg3 per agg bar ---
    float emaMult = 2.0f / (EMAPeriod + 1);
    for (int a = 0; a < nAgg; a++)
    {
        if (a == 0)
            aggEMA9Arr[a] = aggCloseArr[a];
        else
            aggEMA9Arr[a] = aggCloseArr[a] * emaMult + aggEMA9Arr[a - 1] * (1.0f - emaMult);

        aggDEMA9Arr[a] = (a >= 1) ? aggEMA9Arr[a] - aggEMA9Arr[a - 1] : 0.0f;
        aggD2EMA9Arr[a] = (a >= 2) ? aggDEMA9Arr[a] - aggDEMA9Arr[a - 1] : 0.0f;
        aggD2Avg3Arr[a] = (a >= 4)
            ? (aggD2EMA9Arr[a] + aggD2EMA9Arr[a - 1] + aggD2EMA9Arr[a - 2]) / 3.0f
            : 0.0f;
    }

    // --- Map gate features to tick resolution using PREVIOUS completed bar ---
    // Each tick sees the feature values from the bar BEFORE its own bar.
    // This matches the live mode (ci-1) behavior: gate decisions use only
    // completed bar data, no look-ahead within the current bar.
    for (int i = 0; i < nBars; i++)
    {
        int a = tickToAgg[i];
        int prevA = (a >= 1) ? a - 1 : 0;
        tickChop[i]    = aggChopArr[prevA];
        tickDR2[i]     = aggDR2Arr[prevA];
        tickDSlope[i]  = aggDSlopeArr[prevA];
        tickD2EMA9[i]  = aggD2EMA9Arr[prevA];
        tickD2Avg3[i]  = aggD2Avg3Arr[prevA];
    }

    {
        SCString msg;
        msg.Format("CSV TEST: Features computed — %d agg bars, %d ticks", nAgg, nBars);
        sc.AddMessageToLog(msg, 0);
    }

    // Free agg-only arrays (keep aggHighArr, aggLowArr, tickToAgg for fade_confirm)
    sc.FreeMemory(aggCloseArr);
    sc.FreeMemory(aggRangeArr);
    sc.FreeMemory(aggChopArr);
    sc.FreeMemory(aggR2Arr);
    sc.FreeMemory(aggSlopeArr);
    sc.FreeMemory(aggDR2Arr);
    sc.FreeMemory(aggDSlopeArr);
    sc.FreeMemory(aggEMA9Arr);
    sc.FreeMemory(aggDEMA9Arr);
    sc.FreeMemory(aggD2EMA9Arr);
    sc.FreeMemory(aggD2Avg3Arr);

    // ---------- Simulation state ----------
    double anchorPrice    = 0.0;
    double watchPrice     = 0.0;
    double watchHigh      = 0.0;
    double watchLow       = 0.0;
    int    direction      = 0;
    int    level          = 0;
    int    fadeCountLong  = 0;
    int    fadeCountShort = 0;
    int    posQty         = 0;
    double avgEntry       = 0.0;
    double totalCost      = 0.0;
    int    holdActive     = 0;
    int    holdCount      = 0;

    int    cycleId        = 0;
    char   watchStartDT[32] = "";
    float  watchStartPrice = 0.0f;
    float  watchStartHigh  = 0.0f;
    float  watchStartLow   = 0.0f;
    int    watchStartBar   = 0;
    int    cycleStartBar   = 0;
    int    cycleDepth      = 0;
    int    cyclePeakPos    = 0;
    float  cycleMFE        = 0.0f;
    float  cycleMAE        = 0.0f;
    int    prevDateInt     = 0;
    int    rthActive       = 0;
    float  savedAvgEntry   = 0.0f;

    auto ResetState = [&]()
    {
        anchorPrice = 0.0;
        direction   = 0;
        level       = 0;
        watchPrice  = 0.0;
        watchHigh   = 0.0;
        watchLow    = 0.0;
        holdActive  = 0;
    };

    auto FadeBlocked = [&](int dir) -> bool
    {
        if (MaxFades <= 0) return false;
        if (dir == 1  && fadeCountLong  >= MaxFades) return true;
        if (dir == -1 && fadeCountShort >= MaxFades) return true;
        return false;
    };

    auto UpdateFadeCount = [&](int dir)
    {
        if (dir == 1)  { fadeCountLong++;  fadeCountShort = 0; }
        else           { fadeCountShort++; fadeCountLong  = 0; }
    };

    auto AddEvent = [&](int barIdx, const char* evt, const char* side,
                        float price, float avg, int pq, int aq, int lv, float pnl)
    {
        if (nEvents >= MAX_EVENTS) return;
        EventRecord& e = events[nEvents++];
        e.cycleId = cycleId;
        strncpy(e.datetime, bars[barIdx].DateTime, 31); e.datetime[31] = '\0';
        strncpy(e.event, evt, 19); e.event[19] = '\0';
        strncpy(e.side, side, 5); e.side[5] = '\0';
        e.price = price;
        e.avgEntryPrice = avg;
        e.posQty = pq;
        e.addQty = aq;
        e.level = lv;
        e.pnlTicks = pnl;
        e.chopVal   = tickChop[barIdx];
        e.dr2Val    = tickDR2[barIdx];
        e.dslopeVal = tickDSlope[barIdx];
        e.fcVal     = 0.0f;
        e.d2Val     = tickD2EMA9[barIdx];
        e.d2a3Val   = tickD2Avg3[barIdx];
    };

    auto SetLastEventFC = [&](float fc)
    {
        if (nEvents > 0)
            events[nEvents - 1].fcVal = fc;
    };

    auto SimEntry = [&](int dir, int qty, float price)
    {
        if (posQty == 0)
        {
            posQty    = dir * qty;
            avgEntry  = price;
            totalCost = price * qty;
        }
        else
        {
            totalCost += price * qty;
            posQty += dir * qty;
            int absAfter = abs(posQty);
            avgEntry = totalCost / absAfter;
        }
    };

    auto SimFlatten = [&](float price) -> float
    {
        float pnl = 0.0f;
        if (posQty != 0)
        {
            if (posQty > 0)
                pnl = (price - (float)avgEntry) / TickSize * abs(posQty);
            else
                pnl = ((float)avgEntry - price) / TickSize * abs(posQty);
        }
        posQty    = 0;
        avgEntry  = 0.0;
        totalCost = 0.0;
        return pnl;
    };

    auto RecordCycle = [&](int barIdx, const char* exitType, float pnlTicks)
    {
        if (nCycles >= MAX_CYCLES) return;
        CycleRecord& c = cycles[nCycles++];
        c.cycleId = cycleId;
        strncpy(c.watchStartDT, watchStartDT, 31); c.watchStartDT[31] = '\0';
        c.watchPrice = watchStartPrice;
        c.watchHigh  = watchStartHigh;
        c.watchLow   = watchStartLow;
        c.watchBars  = (cycleStartBar > watchStartBar) ? cycleStartBar - watchStartBar : 0;
        strncpy(c.seedDT, bars[cycleStartBar].DateTime, 31); c.seedDT[31] = '\0';
        strncpy(c.exitDT, bars[barIdx].DateTime, 31); c.exitDT[31] = '\0';
        strncpy(c.direction, direction == 1 ? "LONG" : "SHORT", 5); c.direction[5] = '\0';
        c.seedPrice     = bars[cycleStartBar].Last;
        c.avgEntryPrice = savedAvgEntry;
        c.exitPrice     = bars[barIdx].Last;
        strncpy(c.exitType, exitType, 15); c.exitType[15] = '\0';
        c.depth         = cycleDepth;
        c.maxPosition   = cyclePeakPos;
        c.pnlTicks      = pnlTicks;
        c.pnlDollars    = pnlTicks * 5.0f;
        c.barsHeld      = barIdx - cycleStartBar;
        c.mfeTicks      = cycleMFE;
        c.maeTicks      = cycleMAE;
        c.holdCount     = holdCount;
        cycleId++;
    };

    auto StartNewWatch = [&](int barIdx)
    {
        strncpy(watchStartDT, bars[barIdx].DateTime, 31); watchStartDT[31] = '\0';
        watchStartPrice = bars[barIdx].Last;
        watchStartHigh  = bars[barIdx].Last;
        watchStartLow   = bars[barIdx].Last;
        watchStartBar   = barIdx;
        cycleDepth      = 0;
        cyclePeakPos    = 0;
        cycleMFE        = 0.0f;
        cycleMAE        = 0.0f;
        holdActive      = 0;
        holdCount       = 0;
    };

    // --- Entry gate check: returns 1 if allowed, <0 if blocked ---
    auto CheckEntryGates = [&](int barIdx, int dir, float price, float& outFC) -> int
    {
        if (ChopFilterOn && tickChop[barIdx] >= 0.0f && tickChop[barIdx] >= ChopThresh)
            return -1;

        if (EntrySignalOn)
        {
            if (tickDR2[barIdx] > DR2Thresh || tickDSlope[barIdx] > DSlopeThresh)
                return -2;
        }

        outFC = -1.0f;
        if (FadeConfirmOn)
        {
            int prevAgg = tickToAgg[barIdx] - 1;
            if (prevAgg >= 0)
            {
                float prevH = aggHighArr[prevAgg];
                float prevL = aggLowArr[prevAgg];
                float range = prevH - prevL;
                float fc;
                if (range < 0.0001f)
                    fc = 0.5f;
                else if (dir == 1)
                    fc = (price - prevL) / range;
                else
                    fc = (prevH - price) / range;
                outFC = fc;
                if (fc >= FadeConfirmThresh)
                    return -3;
            }
        }

        if (D2EntryOn)
        {
            float d2 = tickD2EMA9[barIdx];
            if (d2 > D2NeutralThresh && dir == -1)
                return -4;
            if (d2 < -D2NeutralThresh && dir == 1)
                return -4;
        }

        return 1;
    };

    auto BlockEventName = [](int code) -> const char*
    {
        switch (code)
        {
            case -1: return "CHOP_BLOCKED";
            case -2: return "DR2_BLOCKED";
            case -3: return "FADECONF_BLK";
            case -4: return "D2_BLOCKED";
            default: return "GATE_BLOCKED";
        }
    };

    // ---------- Main simulation loop (pass 2) ----------
    for (int i = 0; i < nBars; i++)
    {
        float price   = bars[i].Last;
        int   timeSec = bars[i].TimeSec;
        int   dateInt = bars[i].DateInt;

        // --- Session boundary ---
        if (timeSec >= RTH_OPEN_SEC && timeSec <= RTH_CLOSE_SEC)
        {
            if (!rthActive)
            {
                rthActive = 1;
                if (posQty != 0)
                {
                    savedAvgEntry = (float)avgEntry;
                    float pnl = SimFlatten(price);
                    AddEvent(i, "SESSION_RESET", direction == 1 ? "LONG" : "SHORT",
                             price, savedAvgEntry, 0, 0, level, pnl);
                }
                ResetState();
                fadeCountLong  = 0;
                fadeCountShort = 0;
                StartNewWatch(i);
            }
        }
        else
        {
            if (rthActive && timeSec > RTH_CLOSE_SEC)
                rthActive = 0;
            prevDateInt = dateInt;
            continue;
        }
        prevDateInt = dateInt;

        // --- EOD FLATTEN ---
        if (timeSec >= RTH_CLOSE_SEC)
        {
            if (posQty != 0)
            {
                holdActive = 0;
                savedAvgEntry = (float)avgEntry;
                float pnl = SimFlatten(price);
                const char* side = direction == 1 ? "LONG" : "SHORT";
                AddEvent(i, "EOD_FLATTEN", side,
                         price, savedAvgEntry, 0, 0, level, pnl);
                RecordCycle(i, "EOD_FLATTEN", pnl);
                ResetState();
            }
            else if (watchPrice != 0.0)
            {
                ResetState();
            }
            rthActive = 0;
            continue;
        }

        // --- MFE/MAE tracking ---
        if (posQty != 0)
        {
            float excursion;
            if (posQty > 0)
                excursion = (price - (float)avgEntry) / TickSize;
            else
                excursion = ((float)avgEntry - price) / TickSize;
            if (excursion > cycleMFE) cycleMFE = excursion;
            if (-excursion > cycleMAE) cycleMAE = -excursion;
            float hiExc, loExc;
            if (posQty > 0)
            {
                hiExc = (bars[i].High - (float)avgEntry) / TickSize;
                loExc = (bars[i].Low  - (float)avgEntry) / TickSize;
            }
            else
            {
                hiExc = ((float)avgEntry - bars[i].Low)  / TickSize;
                loExc = ((float)avgEntry - bars[i].High) / TickSize;
            }
            if (hiExc > cycleMFE) cycleMFE = hiExc;
            if (-loExc > cycleMAE) cycleMAE = -loExc;
        }

        // --- HARD STOP ---
        if (posQty != 0 && HardStop > 0.0)
        {
            double unrealPts = (posQty > 0)
                ? ((float)avgEntry - price)
                : (price - (float)avgEntry);
            double unrealTicks = unrealPts / TickSize;

            if (unrealTicks >= HardStop)
            {
                holdActive = 0;
                savedAvgEntry = (float)avgEntry;
                float pnl = SimFlatten(price);
                const char* side = direction == 1 ? "LONG" : "SHORT";
                AddEvent(i, "HARD_STOP", side,
                         price, savedAvgEntry, 0, 0, level, pnl);
                RecordCycle(i, "HARD_STOP", pnl);
                ResetState();
                StartNewWatch(i);
                continue;
            }
        }

        // --- WATCHING ---
        if (posQty == 0 && anchorPrice == 0.0)
        {
            if (watchPrice == 0.0)
            {
                watchPrice = price;
                watchHigh  = price;
                watchLow   = price;
                if (watchStartDT[0] == '\0')
                    StartNewWatch(i);
                continue;
            }

            if (price > watchHigh) watchHigh = price;
            if (price < watchLow)  watchLow  = price;
            if (price > watchStartHigh) watchStartHigh = price;
            if (price < watchStartLow)  watchStartLow  = price;

            double pullFromHigh = watchHigh - price;
            double pullFromLow  = price - watchLow;

            int seedDir = 0;
            if (pullFromHigh >= StepDist && pullFromLow >= StepDist)
                seedDir = (pullFromHigh >= pullFromLow) ? 1 : -1;
            else if (pullFromHigh >= StepDist)
                seedDir = 1;
            else if (pullFromLow >= StepDist)
                seedDir = -1;
            else
                continue;

            if (FadeBlocked(seedDir))
            {
                seedDir = -seedDir;
                bool otherMoved = (seedDir == 1)
                    ? (pullFromHigh >= StepDist)
                    : (pullFromLow >= StepDist);
                if (!otherMoved || FadeBlocked(seedDir))
                    continue;
            }

            float fc = -1.0f;
            int gateResult = CheckEntryGates(i, seedDir, price, fc);
            if (gateResult < 0)
                continue;

            SimEntry(seedDir, InitialQty, price);
            direction     = seedDir;
            level         = 0;
            anchorPrice   = price;
            watchPrice    = 0.0;
            cycleStartBar = i;
            cycleDepth    = 0;
            cyclePeakPos  = abs(posQty);
            cycleMFE      = 0.0f;
            cycleMAE      = 0.0f;
            holdActive    = 0;
            holdCount     = 0;
            UpdateFadeCount(seedDir);

            AddEvent(i, "SEED", seedDir == 1 ? "LONG" : "SHORT",
                     price, price, posQty, InitialQty, 0, 0.0f);
            if (fc >= 0.0f) SetLastEventFC(fc);
            continue;
        }

        // --- IN POSITION ---
        if (posQty == 0)
        {
            ResetState();
            StartNewWatch(i);
            continue;
        }

        double upMove   = price - anchorPrice;
        double downMove = anchorPrice - price;
        bool inFavor = (direction == 1 ? upMove >= StepDist : downMove >= StepDist);
        bool against = (direction == 1 ? downMove >= StepDist : upMove >= StepDist);

        // REVERSAL TRIGGER (takes priority over D2 exit)
        if (inFavor)
        {
            // d2_avg3 hold check
            if (D2HoldOn)
            {
                float d2avg3 = tickD2Avg3[i];
                bool aligned = (direction == 1) ? (d2avg3 > 0.0f) : (d2avg3 <= 0.0f);
                if (aligned)
                {
                    anchorPrice = price;
                    holdActive = 1;
                    holdCount++;
                    AddEvent(i, "HOLD", direction == 1 ? "LONG" : "SHORT",
                             price, (float)avgEntry, posQty, 0, level, 0.0f);
                    continue;
                }
            }

            // Normal REVERSAL
            holdActive = 0;
            savedAvgEntry = (float)avgEntry;
            float pnl = SimFlatten(price);
            const char* side = direction == 1 ? "LONG" : "SHORT";
            AddEvent(i, "REVERSAL", side,
                     price, savedAvgEntry, 0, 0, level, pnl);
            RecordCycle(i, "REVERSAL", pnl);

            int newDir = -direction;

            if (FadeBlocked(newDir))
            {
                AddEvent(i, "FADE_BLOCKED", newDir == 1 ? "LONG" : "SHORT",
                         price, 0.0f, 0, 0, level, 0.0f);
                ResetState();
                StartNewWatch(i);
                continue;
            }

            float fc = -1.0f;
            int gateResult = CheckEntryGates(i, newDir, price, fc);
            if (gateResult < 0)
            {
                AddEvent(i, BlockEventName(gateResult), newDir == 1 ? "LONG" : "SHORT",
                         price, 0.0f, 0, 0, 0, 0.0f);
                ResetState();
                StartNewWatch(i);
                continue;
            }

            SimEntry(newDir, InitialQty, price);
            direction     = newDir;
            level         = 0;
            anchorPrice   = price;
            cycleStartBar = i;
            cycleDepth    = 0;
            cyclePeakPos  = abs(posQty);
            cycleMFE      = 0.0f;
            cycleMAE      = 0.0f;
            holdActive    = 0;
            holdCount     = 0;
            UpdateFadeCount(newDir);

            strncpy(watchStartDT, bars[i].DateTime, 31); watchStartDT[31] = '\0';
            watchStartPrice = price;
            watchStartHigh  = price;
            watchStartLow   = price;
            watchStartBar   = i;

            AddEvent(i, "REVERSAL_ENTRY", newDir == 1 ? "LONG" : "SHORT",
                     price, price, posQty, InitialQty, 0, 0.0f);
            if (fc >= 0.0f) SetLastEventFC(fc);
            continue;
        }

        // D2 EXIT (only between reversal triggers, during hold)
        if (holdActive && D2HoldOn)
        {
            float d2avg3 = tickD2Avg3[i];
            bool flipped = (direction == 1) ? (d2avg3 <= 0.0f) : (d2avg3 > 0.0f);
            if (flipped)
            {
                holdActive = 0;
                savedAvgEntry = (float)avgEntry;
                float pnl = SimFlatten(price);
                const char* side = direction == 1 ? "LONG" : "SHORT";
                AddEvent(i, "D2_EXIT", side,
                         price, savedAvgEntry, 0, 0, level, pnl);
                RecordCycle(i, "D2_EXIT", pnl);
                ResetState();
                StartNewWatch(i);
                continue;
            }
        }

        // MARTINGALE ADD
        if (against)
        {
            int useLevel = level;
            if (useLevel >= MaxLevels)
                useLevel = 0;

            int addQty = (int)(InitialQty * pow(2.0, useLevel) + 0.5);
            int absPos = abs(posQty);

            if (absPos + addQty > MaxContractSize)
            {
                int room = MaxContractSize - absPos;
                if (room <= 0)
                    continue;
                addQty = room;
                level = 0;
            }

            SimEntry(direction, addQty, price);
            level++;
            if (level >= MaxLevels)
                level = 0;
            anchorPrice = price;
            cycleDepth++;
            if (abs(posQty) > cyclePeakPos)
                cyclePeakPos = abs(posQty);

            AddEvent(i, "ADD", direction == 1 ? "LONG" : "SHORT",
                     price, (float)avgEntry, posQty, addQty, level, 0.0f);
            continue;
        }
    }

    // ---------- End of data ----------
    if (posQty != 0 && nBars > 0)
    {
        int lastIdx = nBars - 1;
        holdActive = 0;
        savedAvgEntry = (float)avgEntry;
        float pnl = SimFlatten(bars[lastIdx].Last);
        const char* side = direction == 1 ? "LONG" : "SHORT";
        AddEvent(lastIdx, "DATA_END", side,
                 bars[lastIdx].Last, savedAvgEntry, 0, 0, level, pnl);
        RecordCycle(lastIdx, "DATA_END", pnl);
    }

    // ---------- Write cycles CSV ----------
    {
        SCString outPath;
        outPath.Format("%s\\ATEAM_LP_TEST_cycles.csv", basePath);
        FILE* f = fopen(outPath.GetChars(), "w");
        if (f)
        {
            fprintf(f, "cycle_id,watch_start_dt,watch_price,watch_high,watch_low,"
                       "watch_bars,seed_dt,exit_dt,direction,seed_price,"
                       "avg_entry_price,exit_price,exit_type,depth,max_position,"
                       "pnl_ticks,pnl_dollars,bars_held,mfe_ticks,mae_ticks,hold_count\n");
            for (int ci = 0; ci < nCycles; ci++)
            {
                const CycleRecord& c = cycles[ci];
                fprintf(f, "%d,%s,%.2f,%.2f,%.2f,%d,%s,%s,%s,%.2f,%.2f,%.2f,"
                           "%s,%d,%d,%.2f,%.2f,%d,%.2f,%.2f,%d\n",
                        c.cycleId, c.watchStartDT, c.watchPrice,
                        c.watchHigh, c.watchLow, c.watchBars,
                        c.seedDT, c.exitDT, c.direction,
                        c.seedPrice, c.avgEntryPrice, c.exitPrice,
                        c.exitType, c.depth, c.maxPosition,
                        c.pnlTicks, c.pnlDollars, c.barsHeld,
                        c.mfeTicks, c.maeTicks, c.holdCount);
            }
            fclose(f);
        }
    }

    // ---------- Write events CSV ----------
    {
        SCString outPath;
        outPath.Format("%s\\ATEAM_LP_TEST_events.csv", basePath);
        FILE* f = fopen(outPath.GetChars(), "w");
        if (f)
        {
            fprintf(f, "cycle_id,datetime,event,side,price,avg_entry_price,"
                       "pos_qty,add_qty,level,pnl_ticks,"
                       "chop,dr2,dslope,fade_conf,d2_ema9,d2_avg3\n");
            for (int ei = 0; ei < nEvents; ei++)
            {
                const EventRecord& e = events[ei];
                fprintf(f, "%d,%s,%s,%s,%.2f,%.2f,%d,%d,%d,%.2f,"
                           "%.4f,%.4f,%.4f,%.4f,%.4f,%.4f\n",
                        e.cycleId, e.datetime, e.event, e.side,
                        e.price, e.avgEntryPrice, e.posQty, e.addQty,
                        e.level, e.pnlTicks,
                        e.chopVal, e.dr2Val, e.dslopeVal, e.fcVal,
                        e.d2Val, e.d2a3Val);
            }
            fclose(f);
        }
    }

    // ---------- Summary ----------
    {
        int wins = 0, losses = 0, holds = 0, d2exits = 0;
        float totalPnl = 0.0f;
        for (int ci = 0; ci < nCycles; ci++)
        {
            totalPnl += cycles[ci].pnlTicks;
            if (cycles[ci].pnlTicks >= 0) wins++;
            else losses++;
            holds += cycles[ci].holdCount;
            if (strcmp(cycles[ci].exitType, "D2_EXIT") == 0) d2exits++;
        }
        SCString msg;
        msg.Format("CSV TEST: %d cycles (%dW/%dL), PnL=%.1ft, %d events, %d holds, %d D2exits",
                   nCycles, wins, losses, totalPnl, nEvents, holds, d2exits);
        sc.AddMessageToLog(msg, 0);
    }

    // ---------- Cleanup ----------
    sc.FreeMemory(aggHighArr);
    sc.FreeMemory(aggLowArr);
    sc.FreeMemory(tickToAgg);
    sc.FreeMemory(tickChop);
    sc.FreeMemory(tickDR2);
    sc.FreeMemory(tickDSlope);
    sc.FreeMemory(tickD2EMA9);
    sc.FreeMemory(tickD2Avg3);
    sc.FreeMemory(events);
    sc.FreeMemory(cycles);
    sc.FreeMemory(bars);
}

// =========================================================================
//  Main study function
// =========================================================================
SCSFExport scsf_ATEAM_ROTATION_V3_FULL(SCStudyInterfaceRef sc)
{
    SCSubgraphRef sg_FilterBG = sc.Subgraph[0];
    SCSubgraphRef sg_R2       = sc.Subgraph[1];
    SCSubgraphRef sg_Slope    = sc.Subgraph[2];
    SCSubgraphRef sg_dR2      = sc.Subgraph[3];
    SCSubgraphRef sg_dSlope   = sc.Subgraph[4];
    SCSubgraphRef sg_EMA9     = sc.Subgraph[5];
    SCSubgraphRef sg_dEMA9    = sc.Subgraph[6];
    SCSubgraphRef sg_d2EMA9   = sc.Subgraph[7];
    SCSubgraphRef sg_d2Avg3   = sc.Subgraph[8];

    if (sc.SetDefaults)
    {
        sc.GraphName = "ATEAM Rotation V3 Full";
        sc.AutoLoop = 1;
        sc.UpdateAlways = 1;
        sc.UpdateStartIndex = 0;  // full recalc — EMA subgraphs are path-dependent
        sc.GraphRegion = 0;

        sc.AllowMultipleEntriesInSameDirection = 1;
        sc.MaximumPositionAllowed = 100;
        sc.SupportReversals = 0;
        sc.SendOrdersToTradeService = 0;
        sc.AllowOppositeEntryWithOpposingPositionOrOrders = 1;
        sc.SupportAttachedOrdersForTrading = 0;
        sc.CancelAllOrdersOnEntriesAndReversals = 0;
        sc.AllowEntryWithWorkingOrders = 1;
        sc.CancelAllWorkingOrdersOnExit = 1;
        sc.AllowOnlyOneTradePerBar = 0;
        sc.MaintainTradeStatisticsAndTradesData = 1;

        sg_FilterBG.Name = "Filter BG";
        sg_FilterBG.DrawStyle = DRAWSTYLE_BACKGROUND;
        sg_FilterBG.PrimaryColor = RGB(80, 80, 0);
        sg_FilterBG.SecondaryColor = RGB(80, 0, 0);
        sg_FilterBG.SecondaryColorUsed = 1;
        sg_FilterBG.DrawZeros = 0;

        sg_R2.Name = "R2";         sg_R2.DrawStyle = DRAWSTYLE_HIDDEN;
        sg_Slope.Name = "Slope";   sg_Slope.DrawStyle = DRAWSTYLE_HIDDEN;
        sg_dR2.Name = "dR2";       sg_dR2.DrawStyle = DRAWSTYLE_HIDDEN;
        sg_dSlope.Name = "dSlope"; sg_dSlope.DrawStyle = DRAWSTYLE_HIDDEN;
        sg_EMA9.Name = "EMA9";     sg_EMA9.DrawStyle = DRAWSTYLE_HIDDEN;
        sg_dEMA9.Name = "dEMA9";   sg_dEMA9.DrawStyle = DRAWSTYLE_HIDDEN;
        sg_d2EMA9.Name = "d2EMA9"; sg_d2EMA9.DrawStyle = DRAWSTYLE_HIDDEN;
        sg_d2Avg3.Name = "d2Avg3"; sg_d2Avg3.DrawStyle = DRAWSTYLE_HIDDEN;

        sc.Input[0].Name = "Step Dist (pts)";
        sc.Input[0].SetFloat(10.0f);
        sc.Input[1].Name = "Initial Qty";
        sc.Input[1].SetInt(1);
        sc.Input[2].Name = "Max Martingale Levels";
        sc.Input[2].SetInt(1);
        sc.Input[3].Name = "Max Contract Size";
        sc.Input[3].SetInt(2);
        sc.Input[4].Name = "Enable";
        sc.Input[4].SetYesNo(0);
        sc.Input[5].Name = "CSV Log";
        sc.Input[5].SetYesNo(0);
        sc.Input[6].Name = "Hard Stop (ticks, 0=disabled)";
        sc.Input[6].SetFloat(60.0f);
        sc.Input[7].Name = "Max Direction Fades (0=unlimited)";
        sc.Input[7].SetInt(0);

        sc.Input[8].Name = "Enable Speed Filter";
        sc.Input[8].SetYesNo(0);
        sc.Input[9].Name = "SpeedRead Study Ref";
        sc.Input[9].SetStudySubgraphValues(0, 0);
        sc.Input[10].Name = "Speed Slow Threshold";
        sc.Input[10].SetFloat(30.0f);
        sc.Input[11].Name = "Speed Fast Threshold";
        sc.Input[11].SetFloat(70.0f);

        sc.Input[12].Name = "RTH Only";
        sc.Input[12].SetYesNo(1);
        // Input 13: reserved

        sc.Input[14].Name = "CSV Test Mode";
        sc.Input[14].SetYesNo(0);
        sc.Input[15].Name = "CSV Test Path";
        sc.Input[15].SetString(
            "C:\\Projects\\futures_pipeline\\data\\");

        sc.Input[16].Name = "Enable Choppiness Filter";
        sc.Input[16].SetYesNo(1);
        sc.Input[17].Name = "Choppiness Study Ref";
        sc.Input[17].SetStudySubgraphValues(0, 0);
        sc.Input[18].Name = "Choppiness Threshold";
        sc.Input[18].SetFloat(0.10f);

        sc.Input[19].Name = "Enable Entry Signal Filter (dR2/dSlope)";
        sc.Input[19].SetYesNo(1);
        sc.Input[20].Name = "dR2 Threshold (skip when dr2 > this)";
        sc.Input[20].SetFloat(-0.40f);
        sc.Input[21].Name = "dSlope Threshold (skip when dslope > this)";
        sc.Input[21].SetFloat(-2.0f);

        sc.Input[22].Name = "Enable Fade Confirm Filter";
        sc.Input[22].SetYesNo(1);
        sc.Input[23].Name = "Fade Confirm Threshold (skip when fc >= this)";
        sc.Input[23].SetFloat(0.40f);

        sc.Input[24].Name = "Enable EMA Directional Entry Gate";
        sc.Input[24].SetYesNo(1);
        sc.Input[25].Name = "d2_ema9 Neutral Threshold";
        sc.Input[25].SetFloat(0.5f);

        sc.Input[26].Name = "Enable EMA Directional Hold";
        sc.Input[26].SetYesNo(1);
        sc.Input[27].Name = "EMA Period";
        sc.Input[27].SetInt(9);
        sc.Input[27].SetIntLimits(2, 50);

        return;
    }

    sc.SendOrdersToTradeService = 0;

    const double StepDist          = sc.Input[0].GetFloat();
    const int    InitialQty        = sc.Input[1].GetInt();
    const int    MaxLevels         = sc.Input[2].GetInt();
    const int    MaxContractSize   = sc.Input[3].GetInt();
    const int    CSVEnabled        = sc.Input[5].GetYesNo();
    const double HardStop          = sc.Input[6].GetFloat();
    const int    MaxFades          = sc.Input[7].GetInt();
    const int    SpeedFilterEnabled = sc.Input[8].GetYesNo();
    const float  SpeedSlowThresh   = sc.Input[10].GetFloat();
    const float  SpeedFastThresh   = sc.Input[11].GetFloat();
    const int    RTHOnly           = sc.Input[12].GetYesNo();
    const int    ChopFilterEnabled = sc.Input[16].GetYesNo();
    const float  ChopThreshold     = sc.Input[18].GetFloat();
    const int    EntrySignalEnabled = sc.Input[19].GetYesNo();
    const float  DR2Threshold      = sc.Input[20].GetFloat();
    const float  DSlopeThreshold   = sc.Input[21].GetFloat();
    const int    FadeConfirmEnabled = sc.Input[22].GetYesNo();
    const float  FadeConfirmThresh = sc.Input[23].GetFloat();
    const int    D2EntryEnabled    = sc.Input[24].GetYesNo();
    const float  D2NeutralThresh   = sc.Input[25].GetFloat();
    const int    D2HoldEnabled     = sc.Input[26].GetYesNo();
    const int    EMAPeriod         = sc.Input[27].GetInt();
    const float  EMAMult           = 2.0f / (EMAPeriod + 1);

    int ci = sc.Index;

    // =====================================================================
    //  AUTOLOOP: Compute inline features on every 250-tick bar
    // =====================================================================

    // Linear regression R2/Slope (lb=3)
    if (ci >= 2)
    {
        float slope, r2;
        LinReg3(sc.Close[ci - 2], sc.Close[ci - 1], sc.Close[ci], slope, r2);
        sg_R2[ci] = r2;
        sg_Slope[ci] = slope;
    }
    else
    {
        sg_R2[ci] = 1.0f;
        sg_Slope[ci] = 0.0f;
    }

    if (ci >= 3)
    {
        sg_dR2[ci] = sg_R2[ci] - sg_R2[ci - 1];
        sg_dSlope[ci] = fabsf(sg_Slope[ci]) - fabsf(sg_Slope[ci - 1]);
    }
    else
    {
        sg_dR2[ci] = 0.0f;
        sg_dSlope[ci] = 0.0f;
    }

    // EMA
    if (ci == 0)
        sg_EMA9[ci] = sc.Close[ci];
    else
        sg_EMA9[ci] = sc.Close[ci] * EMAMult + sg_EMA9[ci - 1] * (1.0f - EMAMult);

    sg_dEMA9[ci] = (ci >= 1) ? sg_EMA9[ci] - sg_EMA9[ci - 1] : 0.0f;
    sg_d2EMA9[ci] = (ci >= 2) ? sg_dEMA9[ci] - sg_dEMA9[ci - 1] : 0.0f;
    sg_d2Avg3[ci] = (ci >= 4)
        ? (sg_d2EMA9[ci] + sg_d2EMA9[ci - 1] + sg_d2EMA9[ci - 2]) / 3.0f
        : 0.0f;

    // Chop from external study (read on every bar for visual; gate decision
    // uses prevBar in live section below)
    float ChopVal = 0.0f;
    int   ChopEntryAllowed = 1;
    if (ChopFilterEnabled)
    {
        SCFloatArray ChopData;
        sc.GetStudyArrayUsingID(sc.Input[17].GetStudyID(), sc.Input[17].GetSubgraphIndex(), ChopData);
        ChopVal = (ChopData.GetArraySize() > ci) ? ChopData[ci] : 0.0f;
        ChopEntryAllowed = (ChopVal < ChopThreshold) ? 1 : 0;
    }

    // Speed filter visual
    float SpeedVal = 0.0f;
    if (SpeedFilterEnabled)
    {
        SCFloatArray SpeedData;
        sc.GetStudyArrayUsingID(sc.Input[9].GetStudyID(), sc.Input[9].GetSubgraphIndex(), SpeedData);
        SpeedVal = (SpeedData.GetArraySize() > ci) ? SpeedData[ci] : 0.0f;
        if (SpeedVal > 0.0f && SpeedVal <= SpeedSlowThresh)
        {
            sg_FilterBG[ci] = 1.0f;
            sg_FilterBG.DataColor[ci] = sg_FilterBG.PrimaryColor;
        }
        else
            sg_FilterBG[ci] = 0.0f;
    }
    else
        sg_FilterBG[ci] = 0.0f;

    // =====================================================================
    //  CSV TEST MODE
    // =====================================================================
    if (sc.Input[14].GetYesNo())
    {
        if (ci != sc.ArraySize - 1)
            return;

        SCString basePath;
        basePath = sc.Input[15].GetString();
        if (basePath.GetLength() > 0 &&
            basePath[basePath.GetLength() - 1] != '\\')
            basePath += "\\";

        sc.AddMessageToLog("CSV TEST: Starting...", 0);

        RunTestMode(sc, basePath.GetChars(),
                    StepDist, InitialQty, MaxLevels, MaxContractSize,
                    HardStop, MaxFades, sc.TickSize,
                    sc.Input[16].GetYesNo(), sc.Input[18].GetFloat(), 3,
                    sc.Input[19].GetYesNo(), sc.Input[20].GetFloat(), sc.Input[21].GetFloat(),
                    sc.Input[22].GetYesNo(), sc.Input[23].GetFloat(),
                    sc.Input[24].GetYesNo(), sc.Input[25].GetFloat(),
                    sc.Input[26].GetYesNo(), sc.Input[27].GetInt());
        return;
    }

    // =====================================================================
    //  LIVE TRADING MODE
    // =====================================================================
    if (!sc.Input[4].GetYesNo())
        return;

    if (ci != sc.ArraySize - 1)
        return;

    // Read features from PREVIOUS COMPLETED bar (ci-1), not the current
    // incomplete bar. The current bar has partial data — EMA derivatives
    // on partial bars produce noise, not signal. This matches the test mode
    // where features are computed on completed agg bars.
    // All gate features use the PREVIOUS COMPLETED bar (ci-1).
    // The current bar (ci) is incomplete — EMA derivatives on partial
    // bar data produce noise. Completed-bar values match what
    // the Python validation computed.
    int prevBar = (ci >= 1) ? ci - 1 : 0;
    float dR2Val     = sg_dR2[prevBar];
    float dSlopeVal  = sg_dSlope[prevBar];
    float d2Ema9Val  = sg_d2EMA9[prevBar];
    float d2Avg3Val  = sg_d2Avg3[prevBar];

    // Re-read chop from previous completed bar (overrides autoloop value)
    if (ChopFilterEnabled)
    {
        SCFloatArray ChopData;
        sc.GetStudyArrayUsingID(sc.Input[17].GetStudyID(), sc.Input[17].GetSubgraphIndex(), ChopData);
        ChopVal = (ChopData.GetArraySize() > prevBar) ? ChopData[prevBar] : 0.0f;
        ChopEntryAllowed = (ChopVal < ChopThreshold) ? 1 : 0;
    }

    double& AnchorPrice    = sc.GetPersistentDouble(0);
    double& WatchPrice     = sc.GetPersistentDouble(1);
    double& WatchHigh      = sc.GetPersistentDouble(2);
    double& WatchLow       = sc.GetPersistentDouble(3);
    int&    Direction      = sc.GetPersistentInt(0);
    int&    Level          = sc.GetPersistentInt(1);
    int&    OrderPending   = sc.GetPersistentInt(2);
    int&    FlattenPending = sc.GetPersistentInt(3);
    int&    CSVHeader      = sc.GetPersistentInt(4);
    int&    FadeCountLong  = sc.GetPersistentInt(5);
    int&    FadeCountShort = sc.GetPersistentInt(6);
    int&    SpeedFilterOff = sc.GetPersistentInt(7);
    int&    RTHFlatSent    = sc.GetPersistentInt(8);
    int&    GateBlockLogged = sc.GetPersistentInt(9);
    int&    HoldActive     = sc.GetPersistentInt(10);

    s_SCPositionData Pos;
    sc.GetTradePosition(Pos);
    int    PosQty = Pos.PositionQuantity;
    double Price  = sc.Close[ci];

    auto LogCSV = [&](const char* evt, const char* side, double price, double avg,
                      int posQ, int addQ, int lv, double pnl, float fcVal)
    {
        if (CSVEnabled)
            WriteCSV(sc, &CSVHeader, evt, side, price, avg, posQ, addQ, lv, pnl,
                     ChopVal, StepDist, MaxLevels, MaxContractSize,
                     dR2Val, dSlopeVal, fcVal, d2Ema9Val, d2Avg3Val, HoldActive);
    };

    auto Market = [&](int side, int qty) -> bool
    {
        s_SCNewOrder O;
        O.OrderQuantity = qty;
        O.OrderType     = SCT_ORDERTYPE_MARKET;
        O.TimeInForce   = SCT_TIF_GTC;
        int r = side > 0 ? sc.BuyEntry(O) : sc.SellEntry(O);
        return r > 0;
    };

    auto FadeBlocked = [&](int dir) -> bool
    {
        if (MaxFades <= 0) return false;
        if (dir == 1  && FadeCountLong  >= MaxFades) return true;
        if (dir == -1 && FadeCountShort >= MaxFades) return true;
        return false;
    };

    auto UpdateFadeCount = [&](int dir)
    {
        if (dir == 1)  { FadeCountLong++;  FadeCountShort = 0; }
        else           { FadeCountShort++; FadeCountLong  = 0; }
    };

    auto ResetToWatching = [&]()
    {
        AnchorPrice     = 0.0;
        Direction       = 0;
        Level           = 0;
        OrderPending    = 0;
        FlattenPending  = 0;
        WatchPrice      = 0.0;
        WatchHigh       = 0.0;
        WatchLow        = 0.0;
        GateBlockLogged = 0;
        HoldActive      = 0;
    };

    // Entry gate check: returns 1 if allowed, <0 if blocked
    auto LiveCheckGates = [&](int dir, double price, float& fcOut) -> int
    {
        if (ChopFilterEnabled && !ChopEntryAllowed)
            return -1;
        if (EntrySignalEnabled)
        {
            if (dR2Val > DR2Threshold || dSlopeVal > DSlopeThreshold)
                return -2;
        }
        fcOut = -1.0f;
        if (FadeConfirmEnabled && ci >= 1)
        {
            float prevH = sc.High[ci - 1];
            float prevL = sc.Low[ci - 1];
            float range = prevH - prevL;
            float fc;
            if (range < 0.0001f)
                fc = 0.5f;
            else if (dir == 1)
                fc = ((float)price - prevL) / range;
            else
                fc = (prevH - (float)price) / range;
            fcOut = fc;
            if (fc >= FadeConfirmThresh)
                return -3;
        }
        if (D2EntryEnabled)
        {
            if (d2Ema9Val > D2NeutralThresh && dir == -1)
                return -4;
            if (d2Ema9Val < -D2NeutralThresh && dir == 1)
                return -4;
        }
        return 1;
    };

    // --- RTH GATE ---
    int BarTimeSec = 0;
    if (RTHOnly)
    {
        SCDateTime BarDT = sc.BaseDateTimeIn[ci];
        int Year, Month, Day, Hour, Minute, Second;
        BarDT.GetDateTimeYMDHMS(Year, Month, Day, Hour, Minute, Second);
        BarTimeSec = TimeToSeconds(Hour, Minute, Second);

        if (BarTimeSec >= RTH_OPEN_SEC && BarTimeSec < RTH_OPEN_SEC + 60)
            RTHFlatSent = 0;

        if (BarTimeSec >= RTH_CLOSE_SEC && !RTHFlatSent)
        {
            if (PosQty != 0)
            {
                sc.AddMessageToTradeServiceLog("*** RTH EOD FLATTEN ***", 1);
                double AvgEntry = Pos.AveragePrice;
                double PnlTicks = (PosQty > 0)
                    ? (Price - AvgEntry) / sc.TickSize
                    : (AvgEntry - Price) / sc.TickSize;
                LogCSV("EOD_FLATTEN", Direction == 1 ? "LONG" : "SHORT",
                       Price, AvgEntry, PosQty, 0, Level, PnlTicks * abs(PosQty), -1.0f);
                sc.FlattenAndCancelAllOrders();
                ResetToWatching();
            }
            RTHFlatSent = 1;
            return;
        }

        if (BarTimeSec < RTH_OPEN_SEC || BarTimeSec >= RTH_CLOSE_SEC)
            return;
    }

    // DEBUG
    {
        SCString msg;
        msg.Format("Dir:%d Lv:%d Pos:%d Anc:%.2f P:%.2f Pend:%d Flat:%d Hold:%d "
                   "Chop:%.3f dR2:%.3f dSl:%.3f d2:%.3f d2a3:%.3f",
                   Direction, Level, PosQty, AnchorPrice, Price,
                   OrderPending, FlattenPending, HoldActive,
                   ChopVal, dR2Val, dSlopeVal, d2Ema9Val, d2Avg3Val);
        sc.AddMessageToTradeServiceLog(msg, 0);
    }

    // ====================== D2 EXIT during hold =========================
    if (HoldActive && PosQty != 0 && D2HoldEnabled && FlattenPending == 0)
    {
        bool flipped = (Direction == 1) ? (d2Avg3Val <= 0.0f) : (d2Avg3Val > 0.0f);
        if (flipped)
        {
            sc.AddMessageToTradeServiceLog("*** D2 EXIT ***", 1);
            double AvgEntry = Pos.AveragePrice;
            double PnlTicks = (Direction == 1)
                ? (Price - AvgEntry) / sc.TickSize
                : (AvgEntry - Price) / sc.TickSize;
            LogCSV("D2_EXIT", Direction == 1 ? "LONG" : "SHORT",
                   Price, AvgEntry, PosQty, 0, Level, PnlTicks * abs(PosQty), -1.0f);
            sc.FlattenAndCancelAllOrders();
            FlattenPending = 2;
            return;
        }
    }

    // ====================== HARD STOP ===================================
    if (PosQty != 0 && HardStop > 0.0 && !FlattenPending)
    {
        double AvgEntry = Pos.AveragePrice;
        double UnrealizedPts = (PosQty > 0)
            ? (AvgEntry - Price) : (Price - AvgEntry);
        double UnrealizedTicks = UnrealizedPts / sc.TickSize;

        if (UnrealizedTicks >= HardStop)
        {
            SCString msg;
            msg.Format("*** HARD STOP (%.0f ticks) ***", UnrealizedTicks);
            sc.AddMessageToTradeServiceLog(msg, 1);
            LogCSV("HARD_STOP", Direction == 1 ? "LONG" : "SHORT",
                   Price, AvgEntry, PosQty, 0, Level, -UnrealizedTicks, -1.0f);
            sc.FlattenAndCancelAllOrders();
            ResetToWatching();
            return;
        }
    }

    // ====================== SPEED FILTER ================================
    if (SpeedFilterEnabled)
    {
        if (SpeedFilterOff == 0 && SpeedVal >= SpeedFastThresh)
        {
            SpeedFilterOff = 1;
            sc.AddMessageToTradeServiceLog("*** SPEED OFF ***", 1);
            LogCSV("SPEED_OFF", "NONE", Price, 0.0, PosQty, 0, Level, 0.0, -1.0f);
            if (PosQty != 0)
                sc.FlattenAndCancelAllOrders();
            ResetToWatching();
            return;
        }
        if (SpeedFilterOff == 1)
        {
            if (SpeedVal <= SpeedSlowThresh)
            {
                SpeedFilterOff = 0;
                sc.AddMessageToTradeServiceLog("*** SPEED ON ***", 1);
                LogCSV("SPEED_ON", "NONE", Price, 0.0, 0, 0, 0, 0.0, -1.0f);
                ResetToWatching();
            }
            else
            {
                if (PosQty != 0)
                {
                    sc.FlattenAndCancelAllOrders();
                    ResetToWatching();
                }
                return;
            }
        }
    }

    // ====================== FLATTEN PENDING =============================
    if (FlattenPending)
    {
        if (PosQty != 0)
            return;

        if (FlattenPending == 2)
        {
            FlattenPending = 0;
            ResetToWatching();
            return;
        }

        FlattenPending = 0;
        int NewDir = -Direction;

        if (FadeBlocked(NewDir))
        {
            SCString msg;
            msg.Format("*** FADE LIMIT (%s blocked) ***", NewDir == 1 ? "LONG" : "SHORT");
            sc.AddMessageToTradeServiceLog(msg, 1);
            LogCSV("FADE_BLOCKED", NewDir == 1 ? "LONG" : "SHORT",
                   Price, 0.0, 0, 0, Level, 0.0, -1.0f);
            ResetToWatching();
            return;
        }

        float fcVal = -1.0f;
        int gateResult = LiveCheckGates(NewDir, Price, fcVal);
        if (gateResult < 0)
        {
            const char* bn[] = { "", "CHOP_BLOCKED", "DR2_BLOCKED", "FADECONF_BLK", "D2_BLOCKED" };
            int idx = -gateResult;
            if (idx > 4) idx = 0;
            if (!GateBlockLogged)
            {
                LogCSV(bn[idx], NewDir == 1 ? "LONG" : "SHORT",
                       Price, 0.0, 0, 0, Level, 0.0, fcVal);
                GateBlockLogged = 1;
            }
            ResetToWatching();
            return;
        }

        if (Market(NewDir, InitialQty))
        {
            Direction    = NewDir;
            AnchorPrice  = Price;
            Level        = 0;
            OrderPending = 1;
            HoldActive   = 0;
            UpdateFadeCount(NewDir);
            sc.AddMessageToTradeServiceLog("*** REVERSAL ENTRY ***", 1);
            LogCSV("REVERSAL_ENTRY", NewDir == 1 ? "LONG" : "SHORT",
                   Price, Price, InitialQty, InitialQty, 0, 0.0, fcVal);
        }
        return;
    }

    if (OrderPending)
    {
        if (PosQty == 0) return;
        OrderPending = 0;
    }

    // ====================== SEED ========================================
    if (PosQty == 0 && AnchorPrice == 0.0)
    {
        if (WatchPrice == 0.0)
        {
            WatchPrice = Price;
            WatchHigh  = Price;
            WatchLow   = Price;
            return;
        }

        if (Price > WatchHigh) WatchHigh = Price;
        if (Price < WatchLow)  WatchLow  = Price;

        double pullFromHigh = WatchHigh - Price;
        double pullFromLow  = Price - WatchLow;

        int SeedDir = 0;
        if (pullFromHigh >= StepDist && pullFromLow >= StepDist)
            SeedDir = (pullFromHigh >= pullFromLow) ? 1 : -1;
        else if (pullFromHigh >= StepDist)
            SeedDir = 1;
        else if (pullFromLow >= StepDist)
            SeedDir = -1;
        else
            return;

        if (FadeBlocked(SeedDir))
        {
            SeedDir = -SeedDir;
            bool otherMoved = (SeedDir == 1)
                ? (pullFromHigh >= StepDist)
                : (pullFromLow >= StepDist);
            if (!otherMoved || FadeBlocked(SeedDir))
                return;
        }

        float fcVal = -1.0f;
        int gateResult = LiveCheckGates(SeedDir, Price, fcVal);
        if (gateResult < 0)
        {
            if (!GateBlockLogged)
            {
                const char* bn[] = { "", "CHOP_BLOCKED", "DR2_BLOCKED", "FADECONF_BLK", "D2_BLOCKED" };
                int idx = -gateResult;
                if (idx > 4) idx = 0;
                LogCSV(bn[idx], SeedDir == 1 ? "LONG" : "SHORT",
                       Price, 0.0, 0, 0, 0, 0.0, fcVal);
                GateBlockLogged = 1;
            }
            return;
        }

        if (Market(SeedDir, InitialQty))
        {
            Direction    = SeedDir;
            Level        = 0;
            AnchorPrice  = Price;
            WatchPrice   = 0.0;
            OrderPending = 1;
            HoldActive   = 0;
            UpdateFadeCount(SeedDir);

            SCString msg;
            msg.Format("SEED: %s", SeedDir == 1 ? "LONG" : "SHORT");
            sc.AddMessageToTradeServiceLog(msg, 1);
            LogCSV("SEED", SeedDir == 1 ? "LONG" : "SHORT",
                   Price, Price, InitialQty, InitialQty, 0, 0.0, fcVal);
        }
        return;
    }

    if (PosQty == 0)
    {
        ResetToWatching();
        return;
    }

    int PosSide = (PosQty > 0 ? 1 : -1);
    if (Direction == 0 || Direction != PosSide)
    {
        Direction   = PosSide;
        AnchorPrice = Price;
        Level       = 0;
    }

    double upMove   = Price - AnchorPrice;
    double downMove = AnchorPrice - Price;
    bool inFavor = (Direction == 1 ? upMove >= StepDist : downMove >= StepDist);
    bool against = (Direction == 1 ? downMove >= StepDist : upMove >= StepDist);

    // ====================== REVERSAL (with hold) ========================
    if (inFavor)
    {
        if (D2HoldEnabled)
        {
            bool aligned = (Direction == 1) ? (d2Avg3Val > 0.0f) : (d2Avg3Val <= 0.0f);
            if (aligned)
            {
                AnchorPrice = Price;
                HoldActive = 1;
                sc.AddMessageToTradeServiceLog("*** HOLD ***", 1);
                LogCSV("HOLD", Direction == 1 ? "LONG" : "SHORT",
                       Price, Pos.AveragePrice, PosQty, 0, Level, 0.0, -1.0f);
                return;
            }
        }

        HoldActive = 0;
        double AvgEntry = Pos.AveragePrice;
        double PnlTicks = (Direction == 1)
            ? (Price - AvgEntry) / sc.TickSize
            : (AvgEntry - Price) / sc.TickSize;

        sc.AddMessageToTradeServiceLog("*** REVERSAL ***", 1);
        LogCSV("REVERSAL", Direction == 1 ? "LONG" : "SHORT",
               Price, AvgEntry, PosQty, 0, Level, PnlTicks, -1.0f);

        sc.FlattenAndCancelAllOrders();
        FlattenPending = 1;
        return;
    }

    // ====================== MARTINGALE ADD ==============================
    if (against)
    {
        int useLevel = Level;
        if (useLevel >= MaxLevels)
            useLevel = 0;

        int addQty = (int)(InitialQty * pow(2.0, useLevel) + 0.5);
        int absPos = abs(PosQty);

        if (absPos + addQty > MaxContractSize)
        {
            int room = MaxContractSize - absPos;
            if (room <= 0)
            {
                sc.AddMessageToTradeServiceLog("*** MAX SIZE ***", 1);
                return;
            }
            addQty = room;
            Level = 0;
        }

        SCString msg;
        msg.Format("*** ADD qty=%d (Lv=%d) ***", addQty, useLevel);
        sc.AddMessageToTradeServiceLog(msg, 1);

        if (Market(Direction, addQty))
        {
            Level++;
            if (Level >= MaxLevels)
                Level = 0;
            AnchorPrice  = Price;
            OrderPending = 1;

            LogCSV("ADD", Direction == 1 ? "LONG" : "SHORT",
                   Price, Pos.AveragePrice,
                   PosQty + (Direction > 0 ? addQty : -addQty),
                   addQty, Level, 0.0, -1.0f);
        }
        return;
    }
}
