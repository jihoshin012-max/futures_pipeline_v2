// STUDY VERSION LOG
// Base: ATEAM_ROTATION_V3_V2803 (martingale rotation strategy)
// Fork: ATEAM_ROTATION_V3_LP (LucidProp — parameter sweep & prop firm evaluation)
//
// archetype: rotational
// @study: ATEAM Rotation V3 LP
// @version: LP-1.1
// @author: ATEAM
// @type: trading-system + batch-test
// @summary: Forked from V2803 for LucidProp project. Adds CSV test mode for
//           batch simulation over bar data, producing cycle-level P&L output
//           for parameter sweeps and Monte Carlo analysis.
//
// CHANGE LOG
// ----------
// [2026-03-25 LP-1.0] Initial fork from V2803
//   - Copied ATEAM_ROTATION_V3_V2803.cpp verbatim as starting point
//   - Renamed DLL and study function to ATEAM_ROTATION_V3_LP
//   - Added this version header and change log
//
// [2026-03-25 LP-1.1] Add RTH gate + CSV test mode
//   - Input 12: RTH Only toggle (default ON) — restricts live trading to 9:30-15:50 ET
//   - Input 13: unused/reserved
//   - Input 14: CSV Test Mode toggle — batch simulation on bar data CSV
//   - Input 15: CSV Test Path — directory containing calibration bar data CSV
//   - RTH gate: no new entries outside 9:30:00-15:49:50, forced flatten at 15:49:50
//   - RTH gate resets watch state at session open (9:30:00) each day
//   - CSV test mode: loads bar data, runs rotation state machine bar-by-bar,
//     writes cycle-level CSV (cycles.csv) and event-level CSV (events.csv)
//   - Cycle CSV includes: watch phase data, entry/exit, depth, P&L, MFE/MAE
//   - Test mode processes up to 500K bars (sufficient for calibration slice)
//   - Following V32 Zone Touch pattern: runs once on last bar, allocate/free memory
//
// [2026-03-25 LP-1.1 bugfix] Three fixes to test mode output
//   - avg_entry_price: saved before SimFlatten clears it (was always 0.00)
//   - pnl_dollars: corrected to pnlTicks * $5/tick (was double-counting via TickSize * 20)
//   - cycle_id: sequential numbering — incremented in RecordCycle, not StartNewWatch
//   - Also fixed AddEvent avg entry param at SESSION_RESET, HARD_STOP, REVERSAL, EOD_FLATTEN

#include "sierrachart.h"
#include <cstdio>
#include <cmath>
#include <cstring>

SCDLLName("ATEAM_ROTATION_V3_LP")

// =========================================================================
//  Helper: parse "HH:MM:SS" or "H:MM:SS" from time string to seconds since midnight
// =========================================================================
static int TimeToSeconds(int Hour, int Minute, int Second)
{
    return Hour * 3600 + Minute * 60 + Second;
}

// RTH boundaries in seconds since midnight (ET)
static const int RTH_OPEN_SEC  = 9 * 3600 + 30 * 60;       // 09:30:00
static const int RTH_CLOSE_SEC = 15 * 3600 + 49 * 60 + 50;  // 15:49:50
static const int RTH_NOENTRY_SEC = 15 * 3600 + 49 * 60 + 50; // same — no new entries after this

// =========================================================================
//  Live-mode event CSV logger (unchanged from V2803)
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
    double      StepDist,
    int         MaxLevels,
    int         MaxContractSize)
{
    SCString FilePath;
    FilePath.Format("%s\\ATEAM_ROTATION_V3_LP_log.csv", sc.DataFilesFolder().GetChars());

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
            "Level,PnlTicks,StepDist,MaxLevels,MaxContractSize\n");
        *pHeaderWritten = 1;
    }

    int Year, Month, Day, Hour, Minute, Second;
    sc.CurrentSystemDateTime.GetDateTimeYMDHMS(Year, Month, Day,
                                                Hour, Minute, Second);

    fprintf(f,
        "%04d-%02d-%02d %02d:%02d:%02d,%s,%s,%s,"
        "%.2f,%.2f,%d,%d,"
        "%d,%.1f,%.2f,%d,%d\n",
        Year, Month, Day, Hour, Minute, Second,
        sc.GetChartSymbol(sc.ChartNumber).GetChars(),
        Event, Side,
        Price, AvgEntryPrice, PosQty, AddQty,
        Level, PnlTicks, StepDist, MaxLevels, MaxContractSize);

    fclose(f);
}

// =========================================================================
//  CSV TEST MODE — data structures
// =========================================================================
struct TestBar
{
    char DateTime[32];  // "YYYY-MM-DD HH:MM:SS.ffffff"
    float Open, High, Low, Last;
    int TimeSec;        // seconds since midnight (parsed from Time column)
    int DateInt;        // YYYYMMDD as integer for session boundary detection
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
    char  direction[6];     // "LONG" or "SHORT"
    float seedPrice;
    float avgEntryPrice;
    float exitPrice;
    char  exitType[16];     // REVERSAL, HARD_STOP, EOD_FLATTEN
    int   depth;            // number of adds completed
    int   maxPosition;      // peak contracts held
    float pnlTicks;
    float pnlDollars;
    int   barsHeld;
    float mfeTicks;         // max favorable excursion from avg entry
    float maeTicks;         // max adverse excursion from avg entry
};

struct EventRecord
{
    int   cycleId;
    char  datetime[32];
    char  event[20];        // SEED, ADD, REVERSAL, HARD_STOP, EOD_FLATTEN, FADE_BLOCKED
    char  side[6];
    float price;
    float avgEntryPrice;
    int   posQty;
    int   addQty;
    int   level;
    float pnlTicks;
};

// =========================================================================
//  CSV TEST MODE — batch simulation function
// =========================================================================
static void RunTestMode(SCStudyInterfaceRef sc,
    const char* basePath,
    double StepDist, int InitialQty, int MaxLevels, int MaxContractSize,
    double HardStop, int MaxFades, float TickSize)
{
    // ---------- Load bar data ----------
    const int MAX_BARS = 500000;
    TestBar* bars = (TestBar*)sc.AllocateMemory(MAX_BARS * sizeof(TestBar));
    if (!bars)
    {
        sc.AddMessageToLog("CSV TEST MODE LP: Failed to allocate bar data", 1);
        return;
    }

    SCString barPath;
    barPath.Format("%s\\NQ_calibration_1day.csv", basePath);
    FILE* bf = fopen(barPath.GetChars(), "r");
    if (!bf)
    {
        // Try without double backslash
        barPath.Format("%sNQ_calibration_1day.csv", basePath);
        bf = fopen(barPath.GetChars(), "r");
        if (!bf)
        {
            SCString msg;
            msg.Format("CSV TEST MODE LP: Cannot open %s", barPath.GetChars());
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
            // Parse: Date, Time, Open, High, Low, Last, ...
            // Format: "2025-9-21, 18:00:00.000000, 24850.00, ..."
            char dateStr[32], timeStr[32];
            float o, h, l, c;
            if (sscanf(line, " %31[^,], %31[^,], %f, %f, %f, %f",
                        dateStr, timeStr, &o, &h, &l, &c) < 6)
                continue;

            // Parse time to seconds since midnight
            int hr = 0, mn = 0, sec = 0;
            sscanf(timeStr, " %d:%d:%d", &hr, &mn, &sec);
            int timeSec = TimeToSeconds(hr, mn, sec);

            // Parse date to integer YYYYMMDD
            int yr = 0, mo = 0, dy = 0;
            sscanf(dateStr, " %d-%d-%d", &yr, &mo, &dy);
            int dateInt = yr * 10000 + mo * 100 + dy;

            // Build datetime string
            char dtBuf[32];
            snprintf(dtBuf, sizeof(dtBuf), "%04d-%02d-%02d %02d:%02d:%02d",
                     yr, mo, dy, hr, mn, sec);

            TestBar& b = bars[nBars];
            strncpy(b.DateTime, dtBuf, 31); b.DateTime[31] = '\0';
            b.Open = o;
            b.High = h;
            b.Low  = l;
            b.Last = c;
            b.TimeSec = timeSec;
            b.DateInt = dateInt;
            nBars++;
        }
        fclose(bf);
    }

    {
        SCString msg;
        msg.Format("CSV TEST MODE LP: Loaded %d bars", nBars);
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
        sc.AddMessageToLog("CSV TEST MODE LP: Failed to allocate output arrays", 1);
        if (cycles) sc.FreeMemory(cycles);
        if (events) sc.FreeMemory(events);
        sc.FreeMemory(bars);
        return;
    }
    memset(cycles, 0, MAX_CYCLES * sizeof(CycleRecord));
    memset(events, 0, MAX_EVENTS * sizeof(EventRecord));
    int nCycles = 0;
    int nEvents = 0;

    // ---------- Simulation state ----------
    double anchorPrice    = 0.0;
    double watchPrice     = 0.0;
    double watchHigh      = 0.0;
    double watchLow       = 0.0;
    int    direction      = 0;    // 1=long, -1=short
    int    level          = 0;
    int    fadeCountLong  = 0;
    int    fadeCountShort = 0;
    int    posQty         = 0;    // simulated position (signed)
    double avgEntry       = 0.0;  // simulated average entry price
    double totalCost      = 0.0;  // total cost basis (qty * price) for avg calc

    // Per-cycle tracking
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
    int    rthActive       = 0;   // whether we're in an RTH session

    // Helper lambdas
    auto ResetState = [&]()
    {
        anchorPrice = 0.0;
        direction   = 0;
        level       = 0;
        watchPrice  = 0.0;
        watchHigh   = 0.0;
        watchLow    = 0.0;
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
    };

    auto SimEntry = [&](int dir, int qty, float price)
    {
        // Simulate a market fill — update position and average entry
        if (posQty == 0)
        {
            posQty    = dir * qty;
            avgEntry  = price;
            totalCost = price * qty;
        }
        else
        {
            int absBefore = abs(posQty);
            totalCost += price * qty;
            posQty += dir * qty;
            int absAfter = abs(posQty);
            avgEntry = totalCost / absAfter;
        }
    };

    auto SimFlatten = [&](float price) -> float
    {
        // Returns P&L in ticks
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

    // [LP-1.1 bugfix] Capture avg entry before SimFlatten clears it
    float savedAvgEntry = 0.0f;

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
        c.avgEntryPrice = savedAvgEntry;  // [LP-1.1 bugfix] use saved value
        c.exitPrice     = bars[barIdx].Last;
        strncpy(c.exitType, exitType, 15); c.exitType[15] = '\0';
        c.depth         = cycleDepth;
        c.maxPosition   = cyclePeakPos;
        c.pnlTicks      = pnlTicks;
        c.pnlDollars    = pnlTicks * 5.0f;  // [LP-1.1 bugfix] NQ mini: $5 per tick
        c.barsHeld      = barIdx - cycleStartBar;
        c.mfeTicks      = cycleMFE;
        c.maeTicks      = cycleMAE;
        cycleId++;  // [LP-1.1 bugfix] increment after recording, sequential IDs
    };

    auto StartNewWatch = [&](int barIdx)
    {
        // [LP-1.1 bugfix] Only increment cycleId when starting a NEW watch
        // after a completed/stopped cycle, not on session resets
        // cycleId is now incremented in RecordCycle instead
        strncpy(watchStartDT, bars[barIdx].DateTime, 31); watchStartDT[31] = '\0';
        watchStartPrice = bars[barIdx].Last;
        watchStartHigh  = bars[barIdx].Last;
        watchStartLow   = bars[barIdx].Last;
        watchStartBar   = barIdx;
        cycleDepth      = 0;
        cyclePeakPos    = 0;
        cycleMFE        = 0.0f;
        cycleMAE        = 0.0f;
    };

    // ---------- Main simulation loop ----------
    for (int i = 0; i < nBars; i++)
    {
        float price   = bars[i].Last;
        int   timeSec = bars[i].TimeSec;
        int   dateInt = bars[i].DateInt;

        // --- Session boundary detection ---
        int newSession = (dateInt != prevDateInt && timeSec >= RTH_OPEN_SEC);
        if (timeSec >= RTH_OPEN_SEC && timeSec <= RTH_CLOSE_SEC)
        {
            if (!rthActive)
            {
                rthActive = 1;
                // New RTH session — reset all state, flatten if somehow in a position
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
            // Outside RTH — skip
            if (rthActive && timeSec > RTH_CLOSE_SEC)
                rthActive = 0;  // mark RTH ended
            prevDateInt = dateInt;
            continue;
        }
        prevDateInt = dateInt;

        // --- EOD FLATTEN: force close at 15:49:50 ---
        if (timeSec >= RTH_CLOSE_SEC)
        {
            if (posQty != 0)
            {
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
                // Was watching but no trade — don't record cycle, just reset
                ResetState();
            }
            rthActive = 0;
            continue;
        }

        // --- Track MFE/MAE if in position ---
        if (posQty != 0)
        {
            float excursion;
            if (posQty > 0)
                excursion = (price - (float)avgEntry) / TickSize;
            else
                excursion = ((float)avgEntry - price) / TickSize;
            if (excursion > cycleMFE) cycleMFE = excursion;
            if (-excursion > cycleMAE) cycleMAE = -excursion;
            // Also track using High/Low for more accurate MFE/MAE
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

        // --- HARD STOP CHECK ---
        if (posQty != 0 && HardStop > 0.0)
        {
            double unrealPts = (posQty > 0)
                ? ((float)avgEntry - price)
                : (price - (float)avgEntry);
            double unrealTicks = unrealPts / TickSize;

            if (unrealTicks >= HardStop)
            {
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

        // --- WATCHING: flat, looking for seed ---
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
            // Track watch extremes for cycle record
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
                continue; // not enough pullback

            // Check fade filter
            if (FadeBlocked(seedDir))
            {
                seedDir = -seedDir;
                bool otherMoved = (seedDir == 1)
                    ? (pullFromHigh >= StepDist)
                    : (pullFromLow >= StepDist);
                if (!otherMoved || FadeBlocked(seedDir))
                    continue;
            }

            // SEED entry
            SimEntry(seedDir, InitialQty, price);
            direction   = seedDir;
            level       = 0;
            anchorPrice = price;
            watchPrice  = 0.0;
            cycleStartBar = i;
            cycleDepth    = 0;
            cyclePeakPos  = abs(posQty);
            cycleMFE      = 0.0f;
            cycleMAE      = 0.0f;
            UpdateFadeCount(seedDir);

            AddEvent(i, "SEED", seedDir == 1 ? "LONG" : "SHORT",
                     price, price, posQty, InitialQty, 0, 0.0f);
            continue;
        }

        // --- IN POSITION: check reversal or add ---
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

        // REVERSAL: StepDist in favor -> flatten and enter opposite
        if (inFavor)
        {
            savedAvgEntry = (float)avgEntry;
            float pnl = SimFlatten(price);
            const char* side = direction == 1 ? "LONG" : "SHORT";
            AddEvent(i, "REVERSAL", side,
                     price, savedAvgEntry, 0, 0, level, pnl);
            RecordCycle(i, "REVERSAL", pnl);

            // Immediately enter opposite direction (no pending in sim)
            int newDir = -direction;
            if (FadeBlocked(newDir))
            {
                AddEvent(i, "FADE_BLOCKED", newDir == 1 ? "LONG" : "SHORT",
                         price, 0.0f, 0, 0, level, 0.0f);
                ResetState();
                StartNewWatch(i);
                continue;
            }

            SimEntry(newDir, InitialQty, price);
            direction   = newDir;
            level       = 0;
            anchorPrice = price;
            cycleStartBar = i;
            cycleDepth    = 0;
            cyclePeakPos  = abs(posQty);
            cycleMFE      = 0.0f;
            cycleMAE      = 0.0f;
            UpdateFadeCount(newDir);

            // Start new watch tracking for the new cycle
            strncpy(watchStartDT, bars[i].DateTime, 31); watchStartDT[31] = '\0';
            watchStartPrice = price;
            watchStartHigh  = price;
            watchStartLow   = price;
            watchStartBar   = i;

            AddEvent(i, "REVERSAL_ENTRY", newDir == 1 ? "LONG" : "SHORT",
                     price, price, posQty, InitialQty, 0, 0.0f);
            continue;
        }

        // MARTINGALE ADD: StepDist against -> add next doubling level
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
                    continue; // max size — cannot add
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

    // ---------- Handle open position at end of data ----------
    if (posQty != 0 && nBars > 0)
    {
        int lastIdx = nBars - 1;
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
                       "pnl_ticks,pnl_dollars,bars_held,mfe_ticks,mae_ticks\n");
            for (int i = 0; i < nCycles; i++)
            {
                const CycleRecord& c = cycles[i];
                fprintf(f, "%d,%s,%.2f,%.2f,%.2f,%d,%s,%s,%s,%.2f,%.2f,%.2f,"
                           "%s,%d,%d,%.2f,%.2f,%d,%.2f,%.2f\n",
                        c.cycleId, c.watchStartDT, c.watchPrice,
                        c.watchHigh, c.watchLow, c.watchBars,
                        c.seedDT, c.exitDT, c.direction,
                        c.seedPrice, c.avgEntryPrice, c.exitPrice,
                        c.exitType, c.depth, c.maxPosition,
                        c.pnlTicks, c.pnlDollars, c.barsHeld,
                        c.mfeTicks, c.maeTicks);
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
                       "pos_qty,add_qty,level,pnl_ticks\n");
            for (int i = 0; i < nEvents; i++)
            {
                const EventRecord& e = events[i];
                fprintf(f, "%d,%s,%s,%s,%.2f,%.2f,%d,%d,%d,%.2f\n",
                        e.cycleId, e.datetime, e.event, e.side,
                        e.price, e.avgEntryPrice, e.posQty, e.addQty,
                        e.level, e.pnlTicks);
            }
            fclose(f);
        }
    }

    // ---------- Summary ----------
    {
        int wins = 0, losses = 0;
        float totalPnl = 0.0f;
        for (int i = 0; i < nCycles; i++)
        {
            totalPnl += cycles[i].pnlTicks;
            if (cycles[i].pnlTicks >= 0) wins++;
            else losses++;
        }
        SCString msg;
        msg.Format("CSV TEST MODE LP: Complete. %d cycles (%d W / %d L), "
                   "net PnL=%.1f ticks, %d events",
                   nCycles, wins, losses, totalPnl, nEvents);
        sc.AddMessageToLog(msg, 0);
    }

    // ---------- Cleanup ----------
    sc.FreeMemory(events);
    sc.FreeMemory(cycles);
    sc.FreeMemory(bars);
}

// =========================================================================
//  Main study function
// =========================================================================
SCSFExport scsf_ATEAM_ROTATION_V3_LP(SCStudyInterfaceRef sc)
{
    SCSubgraphRef sg_FilterBG = sc.Subgraph[0];

    if (sc.SetDefaults)
    {
        sc.GraphName = "ATEAM Rotation V3 LP";
        sc.AutoLoop = 1;
        sc.UpdateAlways = 1;
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

        // Background highlight subgraph
        sg_FilterBG.Name = "Speed Filter BG";
        sg_FilterBG.DrawStyle = DRAWSTYLE_BACKGROUND;
        sg_FilterBG.PrimaryColor = RGB(80, 80, 0);
        sg_FilterBG.SecondaryColor = RGB(80, 0, 0);
        sg_FilterBG.SecondaryColorUsed = 1;
        sg_FilterBG.DrawZeros = 0;

        sc.Input[0].Name = "Step Dist (pts)";
        sc.Input[0].SetFloat(2.0f);

        sc.Input[1].Name = "Initial Qty";
        sc.Input[1].SetInt(1);

        sc.Input[2].Name = "Max Martingale Levels (1=1, 2=1,2, 3=1,2,4, 4=1,2,4,8)";
        sc.Input[2].SetInt(4);

        sc.Input[3].Name = "Max Contract Size (resets to Initial after hitting this)";
        sc.Input[3].SetInt(8);

        sc.Input[4].Name = "Enable";
        sc.Input[4].SetYesNo(1);

        sc.Input[5].Name = "CSV Log";
        sc.Input[5].SetYesNo(1);

        sc.Input[6].Name = "Hard Stop (ticks, 0=disabled)";
        sc.Input[6].SetFloat(0.0f);

        sc.Input[7].Name = "Max Direction Fades (0=unlimited)";
        sc.Input[7].SetInt(0);

        sc.Input[8].Name = "Enable Speed Filter";
        sc.Input[8].SetYesNo(0);

        sc.Input[9].Name = "SpeedRead Study Ref";
        sc.Input[9].SetStudySubgraphValues(0, 0);

        sc.Input[10].Name = "Speed Slow Threshold (trades ON below)";
        sc.Input[10].SetFloat(30.0f);

        sc.Input[11].Name = "Speed Fast Threshold (stop out above)";
        sc.Input[11].SetFloat(70.0f);

        // [LP-1.1] RTH gate
        sc.Input[12].Name = "RTH Only";
        sc.Input[12].SetYesNo(1);

        // Input 13 reserved

        // [LP-1.1] CSV Test Mode
        sc.Input[14].Name = "CSV Test Mode";
        sc.Input[14].SetYesNo(0);

        sc.Input[15].Name = "CSV Test Path";
        sc.Input[15].SetString(
            "C:\\Projects\\pipeline\\stages\\01-data\\data\\bar_data\\tick\\");

        return;
    }

    sc.SendOrdersToTradeService = 0;

    // =====================================================================
    //  CSV TEST MODE — runs once on last bar, batch simulation, then return
    // =====================================================================
    if (sc.Input[14].GetYesNo())
    {
        if (sc.Index != sc.ArraySize - 1)
            return;

        SCString basePath;
        basePath = sc.Input[15].GetString();
        // Ensure trailing backslash
        if (basePath.GetLength() > 0 &&
            basePath[basePath.GetLength() - 1] != '\\')
            basePath += "\\";

        const double StepDist        = sc.Input[0].GetFloat();
        const int    InitialQty      = sc.Input[1].GetInt();
        const int    MaxLevels       = sc.Input[2].GetInt();
        const int    MaxContractSize = sc.Input[3].GetInt();
        const double HardStop        = sc.Input[6].GetFloat();
        const int    MaxFades        = sc.Input[7].GetInt();

        sc.AddMessageToLog("CSV TEST MODE LP: Starting batch simulation...", 0);

        RunTestMode(sc, basePath.GetChars(),
                    StepDist, InitialQty, MaxLevels, MaxContractSize,
                    HardStop, MaxFades, sc.TickSize);

        return;  // test mode done — do not run live logic
    }

    // =====================================================================
    //  LIVE TRADING MODE (original V2803 logic + RTH gate)
    // =====================================================================
    if (!sc.Input[4].GetYesNo())
        return;

    const double StepDist        = sc.Input[0].GetFloat();
    const int    InitialQty      = sc.Input[1].GetInt();
    const int    MaxLevels       = sc.Input[2].GetInt();
    const int    MaxContractSize = sc.Input[3].GetInt();
    const int    CSVEnabled      = sc.Input[5].GetYesNo();
    const double HardStop        = sc.Input[6].GetFloat();
    const int    MaxFades        = sc.Input[7].GetInt();
    const int    SpeedFilterEnabled = sc.Input[8].GetYesNo();
    const float  SpeedSlowThresh   = sc.Input[10].GetFloat();
    const float  SpeedFastThresh   = sc.Input[11].GetFloat();
    const int    RTHOnly           = sc.Input[12].GetYesNo();

    // persistent state
    double& AnchorPrice    = sc.GetPersistentDouble(0);
    double& WatchPrice     = sc.GetPersistentDouble(1);
    double& WatchHigh      = sc.GetPersistentDouble(2);
    double& WatchLow       = sc.GetPersistentDouble(3);
    int&    Direction      = sc.GetPersistentInt(0);   // 1=long, -1=short
    int&    Level          = sc.GetPersistentInt(1);   // 0..MaxLevels-1
    int&    OrderPending   = sc.GetPersistentInt(2);   // 1=waiting for fill
    int&    FlattenPending = sc.GetPersistentInt(3);   // 1=waiting for flatten
    int&    CSVHeader      = sc.GetPersistentInt(4);   // CSV header written flag
    int&    FadeCountLong  = sc.GetPersistentInt(5);   // consecutive long entries
    int&    FadeCountShort = sc.GetPersistentInt(6);   // consecutive short entries
    int&    SpeedFilterOff = sc.GetPersistentInt(7);   // 0=trades allowed, 1=disabled
    int&    RTHFlatSent    = sc.GetPersistentInt(8);   // 1=EOD flatten already sent today

    // Read SpeedRead study data (only if filter enabled)
    float SpeedVal = 0.0f;
    if (SpeedFilterEnabled)
    {
        SCFloatArray SpeedData;
        sc.GetStudyArrayUsingID(sc.Input[9].GetStudyID(), sc.Input[9].GetSubgraphIndex(), SpeedData);
        SpeedVal = (SpeedData.GetArraySize() > sc.Index) ? SpeedData[sc.Index] : 0.0f;

        if (SpeedVal > 0.0f && SpeedVal <= SpeedSlowThresh)
        {
            sg_FilterBG[sc.Index] = 1.0f;
            sg_FilterBG.DataColor[sc.Index] = sg_FilterBG.PrimaryColor;
        }
        else
        {
            sg_FilterBG[sc.Index] = 0.0f;
        }
    }
    else
    {
        sg_FilterBG[sc.Index] = 0.0f;
    }

    // Only run trade logic on last bar
    if (sc.Index != sc.ArraySize - 1)
        return;

    s_SCPositionData Pos;
    sc.GetTradePosition(Pos);
    int    PosQty = Pos.PositionQuantity;
    double Price  = sc.Close[sc.Index];

    // --- RTH GATE: get current bar time ---
    int BarTimeSec = 0;
    if (RTHOnly)
    {
        SCDateTime BarDT = sc.BaseDateTimeIn[sc.Index];
        int Year, Month, Day, Hour, Minute, Second;
        BarDT.GetDateTimeYMDHMS(Year, Month, Day, Hour, Minute, Second);
        BarTimeSec = TimeToSeconds(Hour, Minute, Second);

        // Reset RTHFlatSent at session open
        if (BarTimeSec >= RTH_OPEN_SEC && BarTimeSec < RTH_OPEN_SEC + 60)
            RTHFlatSent = 0;

        // EOD FLATTEN: force close at 15:49:50
        if (BarTimeSec >= RTH_CLOSE_SEC && !RTHFlatSent)
        {
            if (PosQty != 0)
            {
                sc.AddMessageToTradeServiceLog("*** RTH EOD FLATTEN ***", 1);

                if (CSVEnabled)
                {
                    double AvgEntry = Pos.AveragePrice;
                    double PnlTicks = (PosQty > 0)
                        ? (Price - AvgEntry) / sc.TickSize
                        : (AvgEntry - Price) / sc.TickSize;
                    WriteCSV(sc, &CSVHeader, "EOD_FLATTEN",
                        Direction == 1 ? "LONG" : "SHORT",
                        Price, AvgEntry, PosQty, 0, Level, PnlTicks * abs(PosQty),
                        StepDist, MaxLevels, MaxContractSize);
                }

                sc.FlattenAndCancelAllOrders();
                AnchorPrice    = 0.0;
                Direction      = 0;
                Level          = 0;
                OrderPending   = 0;
                FlattenPending = 0;
                WatchPrice     = 0.0;
                WatchHigh      = 0.0;
                WatchLow       = 0.0;
            }
            RTHFlatSent = 1;
            return;
        }

        // Outside RTH window — no trading
        if (BarTimeSec < RTH_OPEN_SEC || BarTimeSec >= RTH_CLOSE_SEC)
            return;
    }

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
        AnchorPrice    = 0.0;
        Direction      = 0;
        Level          = 0;
        OrderPending   = 0;
        FlattenPending = 0;
        WatchPrice     = 0.0;
        WatchHigh      = 0.0;
        WatchLow       = 0.0;
    };

    // DEBUG
    {
        SCString msg;
        msg.Format("Dir:%d Level:%d Pos:%d Anchor:%.2f Price:%.2f Pend:%d Flat:%d FadesL:%d FadesS:%d Speed:%.1f FilterOff:%d",
                   Direction, Level, PosQty, AnchorPrice, Price, OrderPending, FlattenPending,
                   FadeCountLong, FadeCountShort, SpeedVal, SpeedFilterOff);
        sc.AddMessageToTradeServiceLog(msg, 0);
    }

    // HARD STOP CHECK
    if (PosQty != 0 && HardStop > 0.0 && !FlattenPending)
    {
        double AvgEntry = Pos.AveragePrice;
        double UnrealizedPts = (PosQty > 0)
            ? (AvgEntry - Price)
            : (Price - AvgEntry);
        double UnrealizedTicks = UnrealizedPts / sc.TickSize;

        if (UnrealizedTicks >= HardStop)
        {
            SCString msg;
            msg.Format("*** HARD STOP HIT (%.0f ticks against) - FLATTENING ***", UnrealizedTicks);
            sc.AddMessageToTradeServiceLog(msg, 1);

            if (CSVEnabled)
            {
                WriteCSV(sc, &CSVHeader, "HARD_STOP",
                    Direction == 1 ? "LONG" : "SHORT",
                    Price, AvgEntry, PosQty, 0, Level, -UnrealizedTicks,
                    StepDist, MaxLevels, MaxContractSize);
            }

            sc.FlattenAndCancelAllOrders();
            ResetToWatching();
            return;
        }
    }

    // SPEED FILTER
    if (SpeedFilterEnabled)
    {
        if (SpeedFilterOff == 0 && SpeedVal >= SpeedFastThresh)
        {
            SpeedFilterOff = 1;
            sc.AddMessageToTradeServiceLog("*** SPEED FILTER OFF - FAST TAPE - STOPPING ***", 1);

            if (CSVEnabled)
                WriteCSV(sc, &CSVHeader, "SPEED_FILTER_OFF", "NONE",
                    Price, 0.0, PosQty, 0, Level, 0.0,
                    StepDist, MaxLevels, MaxContractSize);

            if (PosQty != 0)
            {
                sc.FlattenAndCancelAllOrders();
                ResetToWatching();
            }
            else
            {
                ResetToWatching();
            }
            return;
        }

        if (SpeedFilterOff == 1)
        {
            if (SpeedVal <= SpeedSlowThresh)
            {
                SpeedFilterOff = 0;
                sc.AddMessageToTradeServiceLog("*** SPEED FILTER ON - SLOW TAPE - TRADING RESUMED ***", 1);

                if (CSVEnabled)
                    WriteCSV(sc, &CSVHeader, "SPEED_FILTER_ON", "NONE",
                        Price, 0.0, 0, 0, 0, 0.0,
                        StepDist, MaxLevels, MaxContractSize);

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

    // Handle flatten pending
    if (FlattenPending)
    {
        if (PosQty != 0)
            return;

        FlattenPending = 0;
        int NewDir = -Direction;

        if (FadeBlocked(NewDir))
        {
            SCString msg;
            msg.Format("*** FADE LIMIT REACHED (%s blocked) - GOING TO WATCH ***",
                       NewDir == 1 ? "LONG" : "SHORT");
            sc.AddMessageToTradeServiceLog(msg, 1);

            if (CSVEnabled)
                WriteCSV(sc, &CSVHeader, "FADE_BLOCKED",
                    NewDir == 1 ? "LONG" : "SHORT",
                    Price, 0.0, 0, 0, Level, 0.0,
                    StepDist, MaxLevels, MaxContractSize);

            ResetToWatching();
            return;
        }

        if (Market(NewDir, InitialQty))
        {
            Direction    = NewDir;
            AnchorPrice  = Price;
            Level        = 0;
            OrderPending = 1;
            UpdateFadeCount(NewDir);
            sc.AddMessageToTradeServiceLog("*** REVERSAL ENTRY SENT ***", 1);

            if (CSVEnabled)
                WriteCSV(sc, &CSVHeader, "REVERSAL_ENTRY",
                    NewDir == 1 ? "LONG" : "SHORT",
                    Price, Price, InitialQty, InitialQty, 0, 0.0,
                    StepDist, MaxLevels, MaxContractSize);
        }
        return;
    }

    // Handle order pending
    if (OrderPending)
    {
        if (PosQty == 0)
            return;
        OrderPending = 0;
    }

    // SEED
    if (PosQty == 0 && AnchorPrice == 0.0)
    {
        if (WatchPrice == 0.0)
        {
            WatchPrice = Price;
            WatchHigh  = Price;
            WatchLow   = Price;
            sc.AddMessageToTradeServiceLog("WATCHING: recording reference price", 1);
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
            SCString msg;
            msg.Format("*** FADE LIMIT - %s BLOCKED, TRYING OPPOSITE ***",
                       SeedDir == 1 ? "LONG" : "SHORT");
            sc.AddMessageToTradeServiceLog(msg, 1);

            SeedDir = -SeedDir;
            bool otherMoved = (SeedDir == 1)
                ? (pullFromHigh >= StepDist)
                : (pullFromLow >= StepDist);

            if (!otherMoved || FadeBlocked(SeedDir))
                return;
        }

        if (Market(SeedDir, InitialQty))
        {
            Direction    = SeedDir;
            Level        = 0;
            AnchorPrice  = Price;
            WatchPrice   = 0.0;
            OrderPending = 1;
            UpdateFadeCount(SeedDir);

            const char* Side = SeedDir == 1 ? "LONG" : "SHORT";
            SCString msg;
            msg.Format("SEED: %s base size", Side);
            sc.AddMessageToTradeServiceLog(msg, 1);

            if (CSVEnabled)
                WriteCSV(sc, &CSVHeader, "SEED", Side,
                    Price, Price, InitialQty, InitialQty, 0, 0.0,
                    StepDist, MaxLevels, MaxContractSize);
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

    // REVERSAL
    if (inFavor)
    {
        double AvgEntry = Pos.AveragePrice;
        double PnlTicks = (Direction == 1)
            ? (Price - AvgEntry) / sc.TickSize
            : (AvgEntry - Price) / sc.TickSize;

        sc.AddMessageToTradeServiceLog("*** REVERSAL - FLATTENING ***", 1);

        if (CSVEnabled)
            WriteCSV(sc, &CSVHeader, "REVERSAL",
                Direction == 1 ? "LONG" : "SHORT",
                Price, AvgEntry, PosQty, 0, Level, PnlTicks,
                StepDist, MaxLevels, MaxContractSize);

        sc.FlattenAndCancelAllOrders();
        FlattenPending = 1;
        return;
    }

    // MARTINGALE ADD
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
                sc.AddMessageToTradeServiceLog("*** MAX SIZE HIT - CANNOT ADD ***", 1);
                return;
            }
            addQty = room;
            Level = 0;

            SCString msg2;
            msg2.Format("*** MAX SIZE CAP - ADDING %d TO FILL TO %d ***", addQty, MaxContractSize);
            sc.AddMessageToTradeServiceLog(msg2, 1);
        }

        SCString msg;
        msg.Format("*** MARTINGALE ADD qty=%d (Level=%d) ***", addQty, useLevel);
        sc.AddMessageToTradeServiceLog(msg, 1);

        if (Market(Direction, addQty))
        {
            Level++;
            if (Level >= MaxLevels)
                Level = 0;
            AnchorPrice  = Price;
            OrderPending = 1;

            if (CSVEnabled)
                WriteCSV(sc, &CSVHeader, "ADD",
                    Direction == 1 ? "LONG" : "SHORT",
                    Price, Pos.AveragePrice,
                    PosQty + (Direction > 0 ? addQty : -addQty),
                    addQty, Level, 0.0,
                    StepDist, MaxLevels, MaxContractSize);
        }
        return;
    }
}
