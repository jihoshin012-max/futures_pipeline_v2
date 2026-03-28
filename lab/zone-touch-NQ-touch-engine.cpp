// STUDY VERSION LOG
// Current: v4.0 (2026-03-23) — ZRA+ZB4 consolidation
// Prior:   ZoneReactionAnalyzer.cpp v3.2, ZoneBounceSignalsV4_aligned.cpp v3.2
// Backup:  ZoneReactionAnalyzer_v31.cpp, ZoneBounceSignalsV4_aligned_v31.cpp
//
// @study: Zone Touch Engine
// @version: 4.0
// @author: ATEAM
// @summary: Consolidated zone touch detection, scoring, measurement, and ray tracking.
//           Merges ZRA (reaction/penetration measurement, VP_RAY detection, multi-bar tracking)
//           with ZB4 (A-Cal scoring, mode routing, cascade awareness, signal drawings).
//           Adds V4 SG 12/13 broken zone ray accumulation.
//           Persistent storage is ZB4-compatible — autotraders read it unchanged.

#include "sierrachart.h"

SCDLLName("Zone Touch Engine")

// ═══════════════════════════════════════════════════════════════════════════
//  CONSTANTS
// ═══════════════════════════════════════════════════════════════════════════

// V4 subgraph indices (all 15 — must match SupplyDemandZonesV4.cpp)
constexpr int V4_SG_DEMAND_SIGNAL      = 0;
constexpr int V4_SG_DEMAND_ZONE_TOP    = 1;
constexpr int V4_SG_DEMAND_ZONE_BOT    = 2;
constexpr int V4_SG_SUPPLY_SIGNAL      = 3;
constexpr int V4_SG_SUPPLY_ZONE_TOP    = 4;
constexpr int V4_SG_SUPPLY_ZONE_BOT    = 5;
constexpr int V4_SG_DEMAND_BROKEN      = 6;
constexpr int V4_SG_SUPPLY_BROKEN      = 7;
constexpr int V4_SG_NEAREST_DEMAND_TOP = 8;
constexpr int V4_SG_NEAREST_DEMAND_BOT = 9;
constexpr int V4_SG_NEAREST_SUPPLY_TOP = 10;
constexpr int V4_SG_NEAREST_SUPPLY_BOT = 11;
constexpr int V4_SG_DEMAND_RAY_PRICE   = 12;
constexpr int V4_SG_SUPPLY_RAY_PRICE   = 13;
constexpr int V4_SG_VP_IMBALANCE_PRICE = 14;

// Persistent storage — ZB4 magic preserved for autotrader compatibility
constexpr uint32_t ZBV4_STORAGE_MAGIC  = 0x5A425634; // "ZBV4"
constexpr uint32_t RXNS_STORAGE_MAGIC  = 0x5A544552; // "ZTER"
constexpr int      MAX_TRACKED_SIGNALS = 5000;
constexpr int      MAX_TRACKED_ZONES   = 10000;
constexpr int      MAX_CHART_SLOTS     = 9;
constexpr int      EVICT_FRACTION      = 2;
constexpr int      MAX_ACCUMULATED_RAYS = 4000;

// Drawing line number ranges (non-overlapping with V4)
constexpr int LN_BASE_STOP   = 84000;
constexpr int LN_BASE_TARGET = 88000;
constexpr int LN_BASE_LABEL  = 92000;

// Detection
constexpr int DEBOUNCE_TICKS       = 3;
constexpr int DEBOUNCE_BAR_WINDOW  = 20;
constexpr int APPROACH_LOOKBACK    = 10;
constexpr int DEFAULT_CASCADE_WINDOW = 50;

// ZRA reaction/penetration threshold tracking
constexpr int   NUM_RXN_THRESHOLDS = 7;
constexpr int   NUM_PEN_THRESHOLDS = 4;
constexpr float RXN_THRESHOLDS[NUM_RXN_THRESHOLDS] = {30, 50, 80, 120, 160, 240, 360};
constexpr float PEN_THRESHOLDS[NUM_PEN_THRESHOLDS]  = {30, 50, 80, 120};

// ═══════════════════════════════════════════════════════════════════════════
//  ENUMS
// ═══════════════════════════════════════════════════════════════════════════

enum EdgeTouchType { kDemandEdge = 0, kSupplyEdge = 1, kVPRay = 2, kTouchTypeCount };
enum TrendContext  { kWithTrend = 0, kCounterTrend = 1, kNeutral = 2 };
enum ModeAssignment { kMode1Full = 0, kMode1Half = 1, kMode3 = 2, kMode4 = 3, kSkip = 4, kMode5 = 5 };
enum SessionClass  { kOpen = 0, kMidDay = 1, kAfternoon = 2, kOffHours = 3 };
enum CascadeState  { kCascadePriorHeld = 0, kCascadeNoPrior = 1, kCascadePriorBroke = 2 };
enum PersistentIdx { kSignalStoragePtr = 0, kReactionStoragePtr = 1 };

// ═══════════════════════════════════════════════════════════════════════════
//  DATA STRUCTURES — SignalRecord & SignalStorage MUST match ZB4 exactly
// ═══════════════════════════════════════════════════════════════════════════

// TrackedZone — from ZB4 (has HtfBar and SlotIdx)
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

// SignalRecord — BYTE-IDENTICAL to ZB4. Autotraders cast this directly.
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
    int   Type;
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
    int   CascadeState;
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

// SignalStorage — BYTE-IDENTICAL to ZB4. Autotraders read this via GetPersistentPointerFromChartStudy.
struct SignalStorage
{
    uint32_t     MagicNumber;
    int          SignalCount;
    int          ZoneCount;
    int          LastBreakBar;
    int          LastHeldBar;
    SignalRecord Signals[MAX_TRACKED_SIGNALS];
    TrackedZone  Zones[MAX_TRACKED_ZONES];
};

// ── ZRA measurement tracking (separate persistent storage) ──

struct ReactionTrack
{
    float Reaction;
    float Penetration;
    int   ReactionPeakBar;
    int   ResolutionBar;
    int   BreakBarIndex;
    int   RxnCrossBar[NUM_RXN_THRESHOLDS];
    int   PenCrossBar[NUM_PEN_THRESHOLDS];
    int   ApproachDir;
    int   SourceSlotIdx;
    int   SourceChart;
    int   SourceStudyID;
    char  SourceLabel[32];
    bool  ZoneBroken;
    bool  Resolved;
};

// VP_RAY touches (not in SignalStorage — autotrader doesn't use them)
struct VPRayTouch
{
    int   BarIndex;
    float TouchPrice;
    float ZoneTop;
    float ZoneBot;
    float VPRayPrice;
    float ApproachVelocity;
    float TrendSlope;
    int   TouchSequence;
    int   ZoneAgeBars;
    int   ApproachDir;
    int   SourceChart;
    int   SourceStudyID;
    int   SourceSlotIdx;
    char  SourceLabel[32];
    float Reaction;
    float Penetration;
    int   ReactionPeakBar;
    int   ResolutionBar;
    int   BreakBarIndex;
    int   RxnCrossBar[NUM_RXN_THRESHOLDS];
    int   PenCrossBar[NUM_PEN_THRESHOLDS];
    bool  HasVPRay;
    bool  ZoneBroken;
    bool  Resolved;
    bool  Active;
};

// Accumulated broken zone ray
struct RayRecord
{
    float Price;
    float ZoneTop;
    float ZoneBot;
    int   BreakBar;
    int   SlotIdx;
    bool  IsDemand;  // true = demand ray (zone broke down), false = supply ray (zone broke up)
};

constexpr int MAX_VP_RAY_TOUCHES = 2000;

struct ReactionStorage
{
    uint32_t      MagicNumber;
    int           TrackCount;       // parallel to SignalStorage.SignalCount
    ReactionTrack Tracks[MAX_TRACKED_SIGNALS];
    int           VPRayCount;
    VPRayTouch    VPRays[MAX_VP_RAY_TOUCHES];
    int           RayCount;
    RayRecord     Rays[MAX_ACCUMULATED_RAYS];
};

// Per-bar scratch data for one chart slot (not persisted)
struct ChartSlotData
{
    int ChartNumber;
    int StudyID;
    SCFloatArray DemandTop, DemandBot, SupplyTop, SupplyBot;
    SCFloatArray VPImbalance, DemandBroken, SupplyBroken;
    SCFloatArray DemandRayPrice, SupplyRayPrice;  // V4 SG 12/13
    // SG 0-5 fetched but stored locally only
    SCFloatArray DemandSignal, DemandZoneTopSG, DemandZoneBotSG;
    SCFloatArray SupplySignal, SupplyZoneTopSG, SupplyZoneBotSG;
    int  V4Size;
    int  V4Idx;
    int  V4Idx1;
    int  V4Idx2;
    int  TFWeightScore;
    bool Valid;
};

// ═══════════════════════════════════════════════════════════════════════════
//  HELPERS
// ═══════════════════════════════════════════════════════════════════════════

static const char* TrendLabel(int ctx)
{
    switch (ctx) {
        case kWithTrend:    return "WT";
        case kCounterTrend: return "CT";
        case kNeutral:      return "NT";
        default:            return "??";
    }
}

static const char* ModeLabel(int mode)
{
    switch (mode) {
        case kMode1Full: return "M1F";
        case kMode1Half: return "M1H";
        case kMode3:     return "M3";
        case kMode4:     return "M4";
        case kMode5:     return "M5";
        default:         return "---";
    }
}

static const char* TouchTypeStr(int type)
{
    switch (type) {
        case kDemandEdge: return "DEMAND_EDGE";
        case kSupplyEdge: return "SUPPLY_EDGE";
        case kVPRay:      return "VP_RAY";
        default:          return "UNKNOWN";
    }
}

static const char* CascadeStr(int cs)
{
    switch (cs) {
        case kCascadePriorHeld:  return "PRIOR_HELD";
        case kCascadeNoPrior:    return "NO_PRIOR";
        case kCascadePriorBroke: return "PRIOR_BROKE";
        default:                 return "UNKNOWN";
    }
}

// ── Persistent storage allocation ──

static SignalStorage* GetOrAllocateSignalStorage(SCStudyInterfaceRef sc)
{
    SignalStorage* p = (SignalStorage*)sc.GetPersistentPointer(kSignalStoragePtr);
    if (p == nullptr || p->MagicNumber != ZBV4_STORAGE_MAGIC)
    {
        if (p != nullptr) sc.FreeMemory(p);
        p = (SignalStorage*)sc.AllocateMemory(sizeof(SignalStorage));
        if (p == nullptr) return nullptr;
        memset(p, 0, sizeof(SignalStorage));
        p->MagicNumber = ZBV4_STORAGE_MAGIC;
        sc.SetPersistentPointer(kSignalStoragePtr, p);
    }
    return p;
}

static ReactionStorage* GetOrAllocateReactionStorage(SCStudyInterfaceRef sc)
{
    ReactionStorage* p = (ReactionStorage*)sc.GetPersistentPointer(kReactionStoragePtr);
    if (p == nullptr || p->MagicNumber != RXNS_STORAGE_MAGIC)
    {
        if (p != nullptr) sc.FreeMemory(p);
        p = (ReactionStorage*)sc.AllocateMemory(sizeof(ReactionStorage));
        if (p == nullptr) return nullptr;
        memset(p, 0, sizeof(ReactionStorage));
        p->MagicNumber = RXNS_STORAGE_MAGIC;
        sc.SetPersistentPointer(kReactionStoragePtr, p);
    }
    return p;
}

// ── Eviction ──

static void DeleteAllSignalDrawings(SCStudyInterfaceRef sc, int maxSlots)
{
    for (int i = 0; i < maxSlots; i++)
    {
        sc.DeleteACSChartDrawing(sc.ChartNumber, TOOL_DELETE_CHARTDRAWING, LN_BASE_STOP   + i);
        sc.DeleteACSChartDrawing(sc.ChartNumber, TOOL_DELETE_CHARTDRAWING, LN_BASE_TARGET + i);
        sc.DeleteACSChartDrawing(sc.ChartNumber, TOOL_DELETE_CHARTDRAWING, LN_BASE_LABEL  + i);
    }
}

static void EvictOldSignals(SCStudyInterfaceRef sc, SignalStorage* p, ReactionStorage* r)
{
    int evictCount = p->SignalCount / EVICT_FRACTION;
    if (evictCount < 1) evictCount = 1;
    for (int i = 0; i < evictCount; i++)
    {
        sc.DeleteACSChartDrawing(sc.ChartNumber, TOOL_DELETE_CHARTDRAWING, LN_BASE_STOP   + i);
        sc.DeleteACSChartDrawing(sc.ChartNumber, TOOL_DELETE_CHARTDRAWING, LN_BASE_TARGET + i);
        sc.DeleteACSChartDrawing(sc.ChartNumber, TOOL_DELETE_CHARTDRAWING, LN_BASE_LABEL  + i);
    }
    int remaining = p->SignalCount - evictCount;
    memmove(&p->Signals[0], &p->Signals[evictCount], remaining * sizeof(SignalRecord));
    for (int i = 0; i < remaining; i++) p->Signals[i].DrawingsPlaced = false;
    memset(&p->Signals[remaining], 0, evictCount * sizeof(SignalRecord));
    p->SignalCount = remaining;
    // Evict parallel reaction tracks
    if (r->TrackCount > evictCount)
    {
        int rRemain = r->TrackCount - evictCount;
        memmove(&r->Tracks[0], &r->Tracks[evictCount], rRemain * sizeof(ReactionTrack));
        memset(&r->Tracks[rRemain], 0, evictCount * sizeof(ReactionTrack));
        r->TrackCount = rRemain;
    }
    else
    {
        r->TrackCount = 0;
    }
}

static void EvictOldZones(SignalStorage* p)
{
    int evictCount = p->ZoneCount / EVICT_FRACTION;
    if (evictCount < 1) evictCount = 1;
    int remaining = p->ZoneCount - evictCount;
    memmove(&p->Zones[0], &p->Zones[evictCount], remaining * sizeof(TrackedZone));
    memset(&p->Zones[remaining], 0, evictCount * sizeof(TrackedZone));
    p->ZoneCount = remaining;
}

static void EvictOldRays(ReactionStorage* r)
{
    int evictCount = r->RayCount / EVICT_FRACTION;
    if (evictCount < 1) evictCount = 1;
    int remaining = r->RayCount - evictCount;
    memmove(&r->Rays[0], &r->Rays[evictCount], remaining * sizeof(RayRecord));
    memset(&r->Rays[remaining], 0, evictCount * sizeof(RayRecord));
    r->RayCount = remaining;
}

static void EvictOldVPRays(ReactionStorage* r)
{
    int evictCount = r->VPRayCount / EVICT_FRACTION;
    if (evictCount < 1) evictCount = 1;
    int remaining = r->VPRayCount - evictCount;
    memmove(&r->VPRays[0], &r->VPRays[evictCount], remaining * sizeof(VPRayTouch));
    memset(&r->VPRays[remaining], 0, evictCount * sizeof(VPRayTouch));
    r->VPRayCount = remaining;
}

// ── Zone management ──

static int FindOrCreateZone(SignalStorage* p, float top, float bot,
                            bool isDemand, int slotIdx, int barIndex,
                            int htfBarIdx, float tolerance = 0.01f)
{
    for (int i = 0; i < p->ZoneCount; i++)
    {
        if (p->Zones[i].IsDemand == isDemand &&
            p->Zones[i].SlotIdx == slotIdx &&
            fabs(p->Zones[i].Top - top) < tolerance &&
            fabs(p->Zones[i].Bot - bot) < tolerance)
            return i;
    }
    if (p->ZoneCount >= MAX_TRACKED_ZONES)
        EvictOldZones(p);
    int idx = p->ZoneCount;
    p->Zones[idx].Top             = top;
    p->Zones[idx].Bot             = bot;
    p->Zones[idx].FirstSeenBar    = barIndex;
    p->Zones[idx].FirstSeenHtfBar = htfBarIdx;
    p->Zones[idx].TouchCount      = 0;
    p->Zones[idx].SlotIdx         = slotIdx;
    p->Zones[idx].IsDemand        = isDemand;
    p->ZoneCount++;
    return idx;
}

// ── Debounce ──

static bool IsDebouncedDuplicate(SignalStorage* p, int type, float price,
                                  float tickSize, int currentBar, int sourceSlot)
{
    float threshold = DEBOUNCE_TICKS * tickSize;
    for (int i = p->SignalCount - 1; i >= 0; i--)
    {
        SignalRecord& s = p->Signals[i];
        if (!s.Active) continue;
        if (currentBar - s.BarIndex > DEBOUNCE_BAR_WINDOW) break;
        if (s.SourceSlot == sourceSlot &&
            s.Type == type && fabs(s.TouchPrice - price) < threshold)
            return true;
    }
    return false;
}

static bool IsVPRayDebouncedDuplicate(ReactionStorage* r, float price,
                                       float tickSize, int currentBar, int sourceChart)
{
    float threshold = DEBOUNCE_TICKS * tickSize;
    for (int i = r->VPRayCount - 1; i >= 0; i--)
    {
        VPRayTouch& v = r->VPRays[i];
        if (!v.Active) continue;
        if (currentBar - v.BarIndex > DEBOUNCE_BAR_WINDOW) break;
        if (v.SourceChart == sourceChart && fabs(v.TouchPrice - price) < threshold)
            return true;
    }
    return false;
}

static bool IsBarDuplicate(SignalStorage* p, int barIndex, int touchType,
                           float touchPrice, float tolerance)
{
    for (int i = p->SignalCount - 1; i >= 0; i--)
    {
        SignalRecord& s = p->Signals[i];
        if (s.BarIndex != barIndex) break;
        if (s.Type == touchType && fabs(s.TouchPrice - touchPrice) <= tolerance)
            return true;
    }
    return false;
}

// ── Scoring helpers ──

static float CalcTrendSlope(SCStudyInterfaceRef sc, int barIndex, float tickSize, int lookback)
{
    if (barIndex < lookback) return 0.0f;
    float priceNow  = sc.BaseData[SC_LAST][barIndex];
    float pricePrev = sc.BaseData[SC_LAST][barIndex - lookback];
    return (priceNow - pricePrev) / tickSize;
}

static float CalcApproachVelocity(SCStudyInterfaceRef sc, int barIndex, float tickSize)
{
    if (barIndex < APPROACH_LOOKBACK) return 0.0f;
    float priceNow  = sc.BaseData[SC_LAST][barIndex];
    float pricePrev = sc.BaseData[SC_LAST][barIndex - APPROACH_LOOKBACK];
    return fabs(priceNow - pricePrev) / tickSize;
}

static int ClassifyTrend(int touchType, float trendSlope, float threshold)
{
    if (touchType == kDemandEdge) {
        if (trendSlope > threshold)  return kWithTrend;
        if (trendSlope < -threshold) return kCounterTrend;
        return kNeutral;
    } else {
        if (trendSlope < -threshold) return kWithTrend;
        if (trendSlope > threshold)  return kCounterTrend;
        return kNeutral;
    }
}

static int CountTFConfluence(ChartSlotData slots[], int activeCount,
                             int touchType, float touchPrice, float tolerance,
                             int excludeSlot)
{
    int count = 1;
    for (int s = 0; s < activeCount; s++)
    {
        if (s == excludeSlot || !slots[s].Valid || slots[s].ChartNumber == 0)
            continue;
        float edgePrice = (touchType == kDemandEdge)
            ? slots[s].DemandTop[slots[s].V4Idx]
            : slots[s].SupplyBot[slots[s].V4Idx];
        if (edgePrice > 0 && fabs(edgePrice - touchPrice) <= tolerance)
            count++;
    }
    return count;
}

static int CalcTFWeightScore(SCStudyInterfaceRef sc, int chartNumber)
{
    n_ACSIL::s_BarPeriod bp;
    sc.GetBarPeriodParametersForChart(chartNumber, bp);
    if (bp.ChartDataType != INTRADAY_DATA || bp.IntradayChartBarPeriodType != IBPT_DAYS_MINS_SECS)
        return 0;
    int seconds = bp.IntradayChartBarPeriodParameter1;
    if (seconds >= 14400) return 25;
    if (seconds >= 7200)  return 18;
    if (seconds >= 5400)  return 15;
    if (seconds >= 3600)  return 12;
    if (seconds >= 1800)  return 8;
    if (seconds >= 900)   return 5;
    return 0;
}

static int CalcZoneQualityScore(int tfWeight, float zoneWidthTicks, bool hasVPRay)
{
    int score = tfWeight;
    if (zoneWidthTicks >= 401.0f)      score += 20;
    else if (zoneWidthTicks >= 161.0f) score += 15;
    else if (zoneWidthTicks >= 81.0f)  score += 8;
    if (hasVPRay) score += 20;
    return score;
}

static int ClassifySession(int hour, int minute)
{
    int hhmm = hour * 100 + minute;
    if (hhmm >= 830 && hhmm < 1000)  return kOpen;
    if (hhmm >= 1000 && hhmm < 1400) return kMidDay;
    if (hhmm >= 1400 && hhmm < 1700) return kAfternoon;
    return kOffHours;
}

static int DetermineCascadeState(int evalBar, int lastBreakBar, int lastHeldBar, int cascadeWindow)
{
    bool recentBreak = (lastBreakBar > 0 && (evalBar - lastBreakBar) <= cascadeWindow);
    bool recentHeld  = (lastHeldBar > 0 && (evalBar - lastHeldBar) <= cascadeWindow);
    if (recentBreak) return kCascadePriorBroke;
    if (recentHeld)  return kCascadePriorHeld;
    return kCascadeNoPrior;
}

static int CalcContextScore(int cascadeState, int sessionCls,
                             float approachVel, float penetrationTicks)
{
    int score = 0;
    if (cascadeState == kCascadePriorHeld)   score += 30;
    else if (cascadeState == kCascadeNoPrior) score += 20;
    if (sessionCls == kOpen)           score += 15;
    else if (sessionCls == kMidDay)    score += 12;
    else if (sessionCls == kAfternoon) score += 5;
    if (approachVel >= 101.0f)      score += 10;
    else if (approachVel >= 51.0f)  score += 8;
    else if (approachVel >= 21.0f)  score += 5;
    else                            score += 3;
    if (penetrationTicks < 30.0f)        score += 15;
    else if (penetrationTicks <= 80.0f)  score += 10;
    else if (penetrationTicks <= 120.0f) score += 5;
    return score;
}

static float CalcPenetrationTicks(int touchType, float touchEdge,
                                   float barLow, float barHigh, float tickSize)
{
    float pen;
    if (touchType == kDemandEdge)
        pen = (touchEdge - barLow) / tickSize;
    else
        pen = (barHigh - touchEdge) / tickSize;
    return pen > 0.0f ? pen : 0.0f;
}

// ── Label and drawing helpers ──

static void BuildEdgeLabel(SCString& out, int mode, int touchType, int seq,
                           int trendCtx, int tfCount, bool hasVP, int totalScore,
                           bool gradeOnly)
{
    char typeChar = (touchType == kDemandEdge) ? 'D' : 'S';
    const char* modeStr = ModeLabel(mode);
    if (gradeOnly) { out.Format("%s [%d]", modeStr, totalScore); return; }
    const char* trendStr = TrendLabel(trendCtx);
    const char* vpStr = hasVP ? " +VP" : "";
    out.Format("%s %c%d %s %dTF [%d]%s", modeStr, typeChar, seq, trendStr, tfCount, totalScore, vpStr);
}

static void GetModeStopTarget(int mode, float zoneWidthTicks,
                               int m3Stop, int m3Target, int m4Stop, int m4Target,
                               int m5Stop, int m5Target, int& outStop, int& outTarget)
{
    switch (mode)
    {
        case kMode1Full: case kMode1Half:
        { int s = (int)(zoneWidthTicks * 0.35f); if (s < 80) s = 80; if (s > 200) s = 200;
          outStop = s; outTarget = s * 2; break; }
        case kMode3:  outStop = m3Stop;  outTarget = m3Target;  break;
        case kMode4:  outStop = m4Stop;  outTarget = m4Target;  break;
        case kMode5:  outStop = m5Stop;  outTarget = m5Target;  break;
        default:      outStop = 0;       outTarget = 0;         break;
    }
}

static void PlaceSignalDrawings(SCStudyInterfaceRef sc, SignalRecord& sig,
                                int slotIdx, float tickSize,
                                int mode, int totalScore,
                                int m3Stop, int m3Target, int m4Stop, int m4Target,
                                int m5Stop, int m5Target,
                                bool showLines, bool showLabels, bool gradeOnly)
{
    bool isDemand = (sig.Type == kDemandEdge);
    int drawBar = sig.BarIndex;
    if (showLabels)
    {
        SCString labelText;
        BuildEdgeLabel(labelText, mode, sig.Type, sig.TouchSequence,
                       sig.TrendCtx, sig.TFConfluence, sig.HasVPRay, totalScore, gradeOnly);
        COLORREF labelColor;
        switch (mode) {
            case kMode1Full: case kMode1Half:
                labelColor = isDemand ? RGB(0,120,255) : RGB(200,0,0); break;
            case kMode3: labelColor = isDemand ? RGB(255,140,0) : RGB(200,0,200); break;
            case kMode4: labelColor = isDemand ? RGB(0,200,200) : RGB(255,100,150); break;
            case kMode5: labelColor = isDemand ? RGB(0,180,80) : RGB(180,200,0); break;
            default:     labelColor = RGB(128,128,128); break;
        }
        s_UseTool LabelTool;
        LabelTool.Clear();
        LabelTool.ChartNumber = sc.ChartNumber;
        LabelTool.DrawingType = DRAWING_TEXT;
        LabelTool.AddMethod   = UTAM_ADD_OR_ADJUST;
        LabelTool.LineNumber  = LN_BASE_LABEL + slotIdx;
        LabelTool.Region      = 0;
        LabelTool.BeginIndex  = drawBar;
        LabelTool.BeginValue  = isDemand ? sc.Low[drawBar] - (50*tickSize) : sc.High[drawBar] + (50*tickSize);
        LabelTool.Text        = labelText;
        LabelTool.Color       = labelColor;
        LabelTool.FontSize    = 8;
        LabelTool.FontBold    = (mode == kMode1Full || mode == kMode1Half) ? 1 : 0;
        LabelTool.TextAlignment = DT_CENTER | (isDemand ? DT_TOP : DT_BOTTOM);
        LabelTool.TransparentLabelBackground = 1;
        LabelTool.AddAsUserDrawnDrawing = 0;
        sc.UseTool(LabelTool);
    }
    if (showLines && mode != kSkip)
    {
        int sigStop, sigTarget;
        GetModeStopTarget(mode, sig.ZoneWidthTicks, m3Stop, m3Target, m4Stop, m4Target,
                          m5Stop, m5Target, sigStop, sigTarget);
        if (sigStop > 0)
        {
            float stopOff = sigStop * tickSize, targetOff = sigTarget * tickSize;
            float stopP, targetP;
            if (isDemand) { stopP = sig.TouchPrice - stopOff; targetP = sig.TouchPrice + targetOff; }
            else          { stopP = sig.TouchPrice + stopOff; targetP = sig.TouchPrice - targetOff; }
            s_UseTool R; R.Clear();
            R.ChartNumber = sc.ChartNumber; R.DrawingType = DRAWING_HORIZONTAL_RAY;
            R.AddMethod = UTAM_ADD_OR_ADJUST; R.Region = 0;
            R.AddAsUserDrawnDrawing = 0; R.LineStyle = LINESTYLE_DASH;
            R.LineWidth = 1; R.DisplayHorizontalLineValue = 1;
            R.LineNumber = LN_BASE_STOP + slotIdx; R.BeginIndex = drawBar;
            R.BeginValue = stopP; R.Color = RGB(200,0,0); sc.UseTool(R);
            R.LineNumber = LN_BASE_TARGET + slotIdx;
            R.BeginValue = targetP; R.Color = RGB(0,120,255); sc.UseTool(R);
        }
    }
    sig.DrawingsPlaced = true;
}

static void PlayModeAlert(SCStudyInterfaceRef sc, int mode, int touchType,
                           int seq, int totalScore,
                           unsigned int m1s, unsigned int m3s,
                           unsigned int m4s, unsigned int m5s)
{
    unsigned int sn = 0;
    switch (mode) {
        case kMode1Full: case kMode1Half: sn = m1s; break;
        case kMode3: sn = m3s; break; case kMode4: sn = m4s; break;
        case kMode5: sn = m5s; break; default: return;
    }
    if (sn < 2) return;
    SCString msg; msg.Format("%s %s S%d [%d]", ModeLabel(mode),
        (touchType == kDemandEdge) ? "Demand" : "Supply", seq, totalScore);
    sc.PlaySound(sn, msg, 0);
}

// ── Chart slot fetch (all 15 V4 subgraphs) ──

static void FetchChartSlot(SCStudyInterfaceRef sc, ChartSlotData& slot, int index)
{
    slot.Valid = false;
    slot.TFWeightScore = 0;
    if (slot.ChartNumber == 0) return;

    // Fetch all 15 subgraphs
    sc.GetStudyArrayFromChartUsingID(slot.ChartNumber, slot.StudyID, V4_SG_DEMAND_SIGNAL,      slot.DemandSignal);
    sc.GetStudyArrayFromChartUsingID(slot.ChartNumber, slot.StudyID, V4_SG_DEMAND_ZONE_TOP,    slot.DemandZoneTopSG);
    sc.GetStudyArrayFromChartUsingID(slot.ChartNumber, slot.StudyID, V4_SG_DEMAND_ZONE_BOT,    slot.DemandZoneBotSG);
    sc.GetStudyArrayFromChartUsingID(slot.ChartNumber, slot.StudyID, V4_SG_SUPPLY_SIGNAL,      slot.SupplySignal);
    sc.GetStudyArrayFromChartUsingID(slot.ChartNumber, slot.StudyID, V4_SG_SUPPLY_ZONE_TOP,    slot.SupplyZoneTopSG);
    sc.GetStudyArrayFromChartUsingID(slot.ChartNumber, slot.StudyID, V4_SG_SUPPLY_ZONE_BOT,    slot.SupplyZoneBotSG);
    sc.GetStudyArrayFromChartUsingID(slot.ChartNumber, slot.StudyID, V4_SG_DEMAND_BROKEN,      slot.DemandBroken);
    sc.GetStudyArrayFromChartUsingID(slot.ChartNumber, slot.StudyID, V4_SG_SUPPLY_BROKEN,      slot.SupplyBroken);
    sc.GetStudyArrayFromChartUsingID(slot.ChartNumber, slot.StudyID, V4_SG_NEAREST_DEMAND_TOP, slot.DemandTop);
    sc.GetStudyArrayFromChartUsingID(slot.ChartNumber, slot.StudyID, V4_SG_NEAREST_DEMAND_BOT, slot.DemandBot);
    sc.GetStudyArrayFromChartUsingID(slot.ChartNumber, slot.StudyID, V4_SG_NEAREST_SUPPLY_TOP, slot.SupplyTop);
    sc.GetStudyArrayFromChartUsingID(slot.ChartNumber, slot.StudyID, V4_SG_NEAREST_SUPPLY_BOT, slot.SupplyBot);
    sc.GetStudyArrayFromChartUsingID(slot.ChartNumber, slot.StudyID, V4_SG_DEMAND_RAY_PRICE,   slot.DemandRayPrice);
    sc.GetStudyArrayFromChartUsingID(slot.ChartNumber, slot.StudyID, V4_SG_SUPPLY_RAY_PRICE,   slot.SupplyRayPrice);
    sc.GetStudyArrayFromChartUsingID(slot.ChartNumber, slot.StudyID, V4_SG_VP_IMBALANCE_PRICE, slot.VPImbalance);

    if (slot.DemandTop.GetArraySize() == 0 || slot.SupplyTop.GetArraySize() == 0)
        return;

    slot.V4Size = slot.DemandTop.GetArraySize();
    slot.V4Idx  = sc.GetNearestMatchForDateTimeIndex(slot.ChartNumber, index);
    slot.V4Idx1 = sc.GetNearestMatchForDateTimeIndex(slot.ChartNumber, index - 1);
    slot.V4Idx2 = (index >= 2)
                  ? sc.GetNearestMatchForDateTimeIndex(slot.ChartNumber, index - 2) : -1;

    if (slot.V4Idx < 0 || slot.V4Idx >= slot.V4Size ||
        slot.V4Idx1 < 0 || slot.V4Idx1 >= slot.V4Size)
        return;
    if (slot.V4Idx2 < 0 || slot.V4Idx2 >= slot.V4Size)
        slot.V4Idx2 = slot.V4Idx1;

    slot.Valid = true;
    slot.TFWeightScore = CalcTFWeightScore(sc, slot.ChartNumber);
}

// ── Source label from chart bar period ──

static const char* GetSourceLabel(SCStudyInterfaceRef sc, int chartNumber)
{
    n_ACSIL::s_BarPeriod bp;
    sc.GetBarPeriodParametersForChart(chartNumber, bp);
    int mins = bp.IntradayChartBarPeriodParameter1 / 60;
    if (mins >= 720) return "720m"; if (mins >= 480) return "480m";
    if (mins >= 360) return "360m"; if (mins >= 240) return "240m";
    if (mins >= 120) return "120m"; if (mins >= 90)  return "90m";
    if (mins >= 60)  return "60m";  if (mins >= 30)  return "30m";
    if (mins >= 15)  return "15m";  return "??m";
}

// ── Ray accumulation: find nearest ray to a price ──

static float FindNearestRay(ReactionStorage* r, bool wantDemand, float touchPrice,
                             float* outDist, float tickSize)
{
    float bestPrice = 0.0f;
    float bestDist = 1e30f;
    for (int i = 0; i < r->RayCount; i++)
    {
        if (r->Rays[i].IsDemand != wantDemand) continue;
        float d = fabs(r->Rays[i].Price - touchPrice);
        if (d < bestDist) { bestDist = d; bestPrice = r->Rays[i].Price; }
    }
    if (bestPrice != 0.0f && outDist != nullptr)
        *outDist = bestDist / tickSize;
    return bestPrice;
}

// ═══════════════════════════════════════════════════════════════════════════
//  CSV EXPORT — Unified raw CSV (52 columns) + ray_context.csv
// ═══════════════════════════════════════════════════════════════════════════

static void WriteRawCSV(SCStudyInterfaceRef sc, SignalStorage* sig, ReactionStorage* rxn,
                        const char* csvPath, float tickSize)
{
    FILE* fp = fopen(csvPath, "w");
    if (fp == nullptr) return;

    fprintf(fp, "DateTime,BarIndex,TouchType,ApproachDir,TouchPrice,ZoneTop,ZoneBot,"
                "HasVPRay,VPRayPrice,Reaction,Penetration,ReactionPeakBar,"
                "ZoneBroken,BreakBarIndex,BarsObserved,TouchSequence,ZoneAgeBars,ApproachVelocity,"
                "TrendSlope,SourceLabel,SourceChart,SourceStudyID,"
                "RxnBar_30,RxnBar_50,RxnBar_80,RxnBar_120,RxnBar_160,RxnBar_240,RxnBar_360,"
                "PenBar_30,PenBar_50,PenBar_80,PenBar_120,"
                "ZoneWidthTicks,CascadeState,CascadeActive,"
                "TFWeightScore,TFConfluence,SessionClass,DayOfWeek,"
                "ModeAssignment,QualityScore,ContextScore,TotalScore,"
                "SourceSlot,ConfirmedBar,HtfConfirmed,Active,"
                "DemandRayPrice,SupplyRayPrice,DemandRayDistTicks,SupplyRayDistTicks\n");

    // Write DEMAND_EDGE and SUPPLY_EDGE touches (from SignalStorage + ReactionStorage)
    int count = sig->SignalCount < rxn->TrackCount ? sig->SignalCount : rxn->TrackCount;
    for (int i = 0; i < sig->SignalCount; i++)
    {
        SignalRecord& s = sig->Signals[i];
        if (!s.Active) continue;

        // Get parallel reaction track (if available)
        ReactionTrack* rt = (i < rxn->TrackCount) ? &rxn->Tracks[i] : nullptr;

        SCDateTime dt = sc.BaseDateTimeIn[s.BarIndex];
        int yr, mo, dy, hr, mn, sc2;
        dt.GetDateTimeYMDHMS(yr, mo, dy, hr, mn, sc2);

        const char* srcLabel = rt ? rt->SourceLabel : "??m";
        int srcChart = rt ? rt->SourceChart : 0;
        int srcStudyID = rt ? rt->SourceStudyID : 0;

        float reaction = rt ? rt->Reaction : 0.0f;
        float penetration = rt ? rt->Penetration : 0.0f;
        int rxnPeakBar = rt ? rt->ReactionPeakBar : s.BarIndex;
        bool zoneBroken = rt ? rt->ZoneBroken : false;
        int breakBar = rt ? rt->BreakBarIndex : -1;
        int barsObs = (rt && rt->Resolved) ? (rt->ResolutionBar - s.BarIndex) : -1;

        // Find nearest rays
        float demRayDist = 0, supRayDist = 0;
        float demRay = FindNearestRay(rxn, true, s.TouchPrice, &demRayDist, tickSize);
        float supRay = FindNearestRay(rxn, false, s.TouchPrice, &supRayDist, tickSize);

        fprintf(fp, "%04d-%02d-%02d %02d:%02d:%02d,%d,%s,%d,"
                    "%.2f,%.2f,%.2f,%d,%.2f,"
                    "%.1f,%.1f,%d,%d,%d,%d,%d,%d,"
                    "%.1f,%.4f,%s,%d,%d,",
                yr, mo, dy, hr, mn, sc2,
                s.BarIndex, TouchTypeStr(s.Type),
                (s.Type == kDemandEdge) ? -1 : 1,
                s.TouchPrice, s.ZoneTop, s.ZoneBot,
                s.HasVPRay ? 1 : 0, s.VPRayPrice,
                reaction, penetration, rxnPeakBar,
                zoneBroken ? 1 : 0, breakBar, barsObs,
                s.TouchSequence, s.ZoneAgeBars,
                s.ApproachVelocity, s.TrendSlope,
                srcLabel, srcChart, srcStudyID);

        // RxnBar and PenBar threshold crossings
        if (rt) {
            fprintf(fp, "%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,",
                    rt->RxnCrossBar[0], rt->RxnCrossBar[1], rt->RxnCrossBar[2],
                    rt->RxnCrossBar[3], rt->RxnCrossBar[4], rt->RxnCrossBar[5],
                    rt->RxnCrossBar[6],
                    rt->PenCrossBar[0], rt->PenCrossBar[1], rt->PenCrossBar[2],
                    rt->PenCrossBar[3]);
        } else {
            fprintf(fp, "-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,-1,");
        }

        // ZB4 scoring columns
        fprintf(fp, "%.0f,%s,%d,%d,%d,%d,%d,%s,%d,%d,%d,%d,%d,%d,%d,"
                    "%.2f,%.2f,%.1f,%.1f\n",
                s.ZoneWidthTicks, CascadeStr(s.CascadeState),
                s.CascadeActive ? 1 : 0,
                s.TFWeightScore, s.TFConfluence,
                s.SessionClass, s.DayOfWeek,
                ModeLabel(s.ModeAssignment), s.QualityScore, s.ContextScore, s.TotalScore,
                s.SourceSlot, s.ConfirmedBar, s.HtfConfirmed ? 1 : 0, 1,
                demRay, supRay, demRayDist, supRayDist);
    }

    // Write VP_RAY touches (from ReactionStorage only — no scoring)
    for (int i = 0; i < rxn->VPRayCount; i++)
    {
        VPRayTouch& v = rxn->VPRays[i];
        if (!v.Active) continue;

        SCDateTime dt = sc.BaseDateTimeIn[v.BarIndex];
        int yr, mo, dy, hr, mn, sc2;
        dt.GetDateTimeYMDHMS(yr, mo, dy, hr, mn, sc2);

        int barsObs = v.Resolved ? (v.ResolutionBar - v.BarIndex) : -1;

        float demRayDist = 0, supRayDist = 0;
        float demRay = FindNearestRay(rxn, true, v.TouchPrice, &demRayDist, tickSize);
        float supRay = FindNearestRay(rxn, false, v.TouchPrice, &supRayDist, tickSize);

        fprintf(fp, "%04d-%02d-%02d %02d:%02d:%02d,%d,%s,%d,"
                    "%.2f,%.2f,%.2f,%d,%.2f,"
                    "%.1f,%.1f,%d,%d,%d,%d,%d,%d,"
                    "%.1f,%.4f,%s,%d,%d,",
                yr, mo, dy, hr, mn, sc2,
                v.BarIndex, "VP_RAY", v.ApproachDir,
                v.TouchPrice, v.ZoneTop, v.ZoneBot,
                1, v.VPRayPrice,
                v.Reaction, v.Penetration, v.ReactionPeakBar,
                v.ZoneBroken ? 1 : 0, v.BreakBarIndex, barsObs,
                v.TouchSequence, v.ZoneAgeBars,
                v.ApproachVelocity, v.TrendSlope,
                v.SourceLabel, v.SourceChart, v.SourceStudyID);

        fprintf(fp, "%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,",
                v.RxnCrossBar[0], v.RxnCrossBar[1], v.RxnCrossBar[2],
                v.RxnCrossBar[3], v.RxnCrossBar[4], v.RxnCrossBar[5],
                v.RxnCrossBar[6],
                v.PenCrossBar[0], v.PenCrossBar[1], v.PenCrossBar[2],
                v.PenCrossBar[3]);

        // VP_RAY has no scoring — write zeros/defaults
        fprintf(fp, "0,NO_PRIOR,0,0,0,0,0,---,0,0,0,%d,%d,0,1,"
                    "%.2f,%.2f,%.1f,%.1f\n",
                v.SourceSlotIdx, v.BarIndex,
                demRay, supRay, demRayDist, supRayDist);
    }

    fclose(fp);

    SCString msg;
    msg.Format("ZTE: Raw CSV written — %d signals + %d VP_RAY",
               sig->SignalCount, rxn->VPRayCount);
    sc.AddMessageToLog(msg, 0);
}

static void WriteRayContextCSV(SCStudyInterfaceRef sc, SignalStorage* sig, ReactionStorage* rxn,
                                const char* csvPath, float tickSize)
{
    FILE* fp = fopen(csvPath, "w");
    if (fp == nullptr) return;

    fprintf(fp, "TouchID,RayPrice,RaySide,RayDirection,RayDistTicks,RayTF,RayAgeBars\n");

    int rowCount = 0;
    for (int i = 0; i < sig->SignalCount; i++)
    {
        SignalRecord& s = sig->Signals[i];
        if (!s.Active) continue;

        // Determine max zone width for proximity filter
        float maxZW = s.ZoneWidthTicks * tickSize;
        if (maxZW < 1.0f) maxZW = 100.0f * tickSize; // fallback
        float proximityLimit = 2.0f * maxZW;

        // Build touch ID
        const char* srcLabel = (i < rxn->TrackCount) ? rxn->Tracks[i].SourceLabel : "??m";
        char touchID[64];
        snprintf(touchID, sizeof(touchID), "%d_%s_%s",
                 s.BarIndex, TouchTypeStr(s.Type), srcLabel);

        for (int r = 0; r < rxn->RayCount; r++)
        {
            RayRecord& ray = rxn->Rays[r];
            float dist = fabs(ray.Price - s.TouchPrice);
            if (dist > proximityLimit) continue;
            if (ray.BreakBar > s.BarIndex) continue; // ray didn't exist at touch time

            const char* raySide = ray.IsDemand ? "DEMAND" : "SUPPLY";
            const char* rayDir = (ray.Price > s.TouchPrice) ? "ABOVE" : "BELOW";
            float distTicks = dist / tickSize;
            int rayAge = s.BarIndex - ray.BreakBar;

            // Infer TF from slot (best effort)
            const char* rayTF = "??m";
            if (ray.SlotIdx >= 0 && ray.SlotIdx < MAX_CHART_SLOTS)
            {
                // We can't call GetBarPeriodParametersForChart here without sc reference in the ray
                // Use a simple mapping based on slot index (matches default chart slot order)
                static const char* slotTFLabels[] = {"15m","30m","60m","90m","120m","240m","360m","480m","720m"};
                rayTF = slotTFLabels[ray.SlotIdx];
            }

            fprintf(fp, "%s,%.2f,%s,%s,%.1f,%s,%d\n",
                    touchID, ray.Price, raySide, rayDir, distTicks, rayTF, rayAge);
            rowCount++;
        }
    }

    fclose(fp);

    SCString msg;
    msg.Format("ZTE: Ray context CSV written — %d ray-touch pairs", rowCount);
    sc.AddMessageToLog(msg, 0);
}


// ═══════════════════════════════════════════════════════════════════════════
//  MAIN STUDY FUNCTION
// ═══════════════════════════════════════════════════════════════════════════

SCSFExport scsf_ZoneTouchEngine(SCStudyInterfaceRef sc)
{
    // --- Input references (ZB4-compatible layout) ---
    SCInputRef InputActiveCount    = sc.Input[0];
    // Input[1-18]: 9 chart slots × 2 (ChartNum, StudyID)
    SCInputRef InputM1FullThreshold = sc.Input[19];
    SCInputRef InputM1HalfThreshold = sc.Input[20];
    SCInputRef InputMaxTouchSeq     = sc.Input[21];
    SCInputRef InputCascadeWindow   = sc.Input[22];
    SCInputRef InputConfluenceTol   = sc.Input[23];
    SCInputRef InputTrendMethod     = sc.Input[24];
    SCInputRef InputTrendThreshold  = sc.Input[25];
    SCInputRef InputSlopeLookback   = sc.Input[26];
    SCInputRef InputFastEMAPeriod   = sc.Input[27];
    SCInputRef InputSlowEMAPeriod   = sc.Input[28];
    SCInputRef InputM3StopTicks     = sc.Input[29];
    SCInputRef InputM3TargetTicks   = sc.Input[30];
    SCInputRef InputM4StopTicks     = sc.Input[31];
    SCInputRef InputM4TargetTicks   = sc.Input[32];
    SCInputRef InputM5StopTicks     = sc.Input[33];
    SCInputRef InputM5TargetTicks   = sc.Input[34];
    SCInputRef InputShowLines       = sc.Input[35];
    SCInputRef InputShowSkipped     = sc.Input[36];
    SCInputRef InputMaxVisibleRays  = sc.Input[37];
    SCInputRef InputLabelDetail     = sc.Input[38];
    SCInputRef InputSkipBars        = sc.Input[39];
    SCInputRef InputEnableAlerts    = sc.Input[40];
    SCInputRef InputM1AlertSound    = sc.Input[41];
    SCInputRef InputM3AlertSound    = sc.Input[42];
    SCInputRef InputM4AlertSound    = sc.Input[43];
    SCInputRef InputM5AlertSound    = sc.Input[44];
    // 45 unused
    SCInputRef InputCSVPath         = sc.Input[46];
    SCInputRef InputExportCSV       = sc.Input[47];
    SCInputRef InputSuppressLabels  = sc.Input[48];
    SCInputRef InputObsMinutes      = sc.Input[49];
    SCInputRef InputVPThreshold     = sc.Input[50];
    SCInputRef InputRayCSVPath      = sc.Input[51];
    SCInputRef InputExportRayCSV    = sc.Input[52];

    // --- Subgraph references ---
    // ZB4-compatible (indices 0-11, 13-14 preserved)
    SCSubgraphRef SG_M1Demand   = sc.Subgraph[0];
    SCSubgraphRef SG_M1Supply   = sc.Subgraph[1];
    SCSubgraphRef SG_M3Demand   = sc.Subgraph[2];
    SCSubgraphRef SG_M3Supply   = sc.Subgraph[3];
    SCSubgraphRef SG_M4Demand   = sc.Subgraph[4];
    SCSubgraphRef SG_M4Supply   = sc.Subgraph[5];
    SCSubgraphRef SG_SkipDemand = sc.Subgraph[6];
    SCSubgraphRef SG_SkipSupply = sc.Subgraph[7];
    SCSubgraphRef SG_M5Demand   = sc.Subgraph[8];
    SCSubgraphRef SG_M5Supply   = sc.Subgraph[9];
    SCSubgraphRef SG_TrendSlope = sc.Subgraph[10];
    SCSubgraphRef SG_TrendZero  = sc.Subgraph[11];
    // [12] = Demand Edge (from ZRA)
    SCSubgraphRef SG_DemandEdge = sc.Subgraph[12];
    SCSubgraphRef SG_M1HDemand  = sc.Subgraph[13];
    SCSubgraphRef SG_M1HSupply  = sc.Subgraph[14];
    SCSubgraphRef SG_SupplyEdge = sc.Subgraph[15];
    SCSubgraphRef SG_VPRayTouch = sc.Subgraph[16];
    SCSubgraphRef SG_Reaction   = sc.Subgraph[17];
    SCSubgraphRef SG_Penetration = sc.Subgraph[18];

    // ══════════════════════════════════════════════════════════════════
    //  SET DEFAULTS
    // ══════════════════════════════════════════════════════════════════

    if (sc.SetDefaults)
    {
        sc.GraphName = "Zone Touch Engine [v4.0]";
        sc.StudyDescription = "Consolidated zone touch detection, A-Cal scoring, reaction measurement, and ray tracking";
        sc.AutoLoop = 1;
        sc.GraphRegion = 0;
        sc.FreeDLL = 0;
        sc.CalculationPrecedence = LOW_PREC_LEVEL;

        InputActiveCount.Name = "Active Chart Count";
        InputActiveCount.SetInt(9); InputActiveCount.SetIntLimits(1, 9);

        // Chart slots (groups of 2)
        static const int chartDefaults[9] = {3,4,5,6,7,2,8,14,9};
        static const int studyDefaults[9] = {1,3,2,3,1,4,3,2,2};
        for (int s = 0; s < 9; s++)
        {
            int base = 1 + s * 2;
            SCString numName, idName;
            numName.Format("Chart %d Number (0=off)", s + 1);
            idName.Format("Chart %d V4 Study ID", s + 1);
            sc.Input[base].Name = numName;
            sc.Input[base].SetInt(chartDefaults[s]);
            sc.Input[base].SetIntLimits(0, 500);
            sc.Input[base + 1].Name = idName;
            sc.Input[base + 1].SetInt(studyDefaults[s]);
            sc.Input[base + 1].SetIntLimits(1, 500);
        }

        InputM1FullThreshold.Name = "M1 Full Score Threshold";
        InputM1FullThreshold.SetInt(90); InputM1FullThreshold.SetIntLimits(0, 155);
        InputM1HalfThreshold.Name = "M1 Half Score Threshold";
        InputM1HalfThreshold.SetInt(60); InputM1HalfThreshold.SetIntLimits(0, 155);
        InputMaxTouchSeq.Name = "Max Touch Sequence";
        InputMaxTouchSeq.SetInt(2); InputMaxTouchSeq.SetIntLimits(1, 10);
        InputCascadeWindow.Name = "Cascade Window (Bars)";
        InputCascadeWindow.SetInt(DEFAULT_CASCADE_WINDOW); InputCascadeWindow.SetIntLimits(1, 500);
        InputConfluenceTol.Name = "TF Confluence Tolerance (Ticks)";
        InputConfluenceTol.SetInt(2); InputConfluenceTol.SetIntLimits(0, 50);

        InputTrendMethod.Name = "Trend Method";
        InputTrendMethod.SetCustomInputStrings("50-bar Slope;EMA Crossover");
        InputTrendMethod.SetCustomInputIndex(0);
        InputTrendThreshold.Name = "Trend Threshold (Ticks)";
        InputTrendThreshold.SetInt(10); InputTrendThreshold.SetIntLimits(0, 500);
        InputSlopeLookback.Name = "Slope Lookback (Bars)";
        InputSlopeLookback.SetInt(50); InputSlopeLookback.SetIntLimits(5, 500);
        InputFastEMAPeriod.Name = "Fast EMA Period";
        InputFastEMAPeriod.SetInt(20); InputFastEMAPeriod.SetIntLimits(2, 200);
        InputSlowEMAPeriod.Name = "Slow EMA Period";
        InputSlowEMAPeriod.SetInt(50); InputSlowEMAPeriod.SetIntLimits(2, 500);

        InputM3StopTicks.Name = "M3 Stop Ticks";    InputM3StopTicks.SetInt(30);    InputM3StopTicks.SetIntLimits(1, 500);
        InputM3TargetTicks.Name = "M3 Target Ticks"; InputM3TargetTicks.SetInt(240); InputM3TargetTicks.SetIntLimits(1, 2000);
        InputM4StopTicks.Name = "M4 Stop Ticks";    InputM4StopTicks.SetInt(80);    InputM4StopTicks.SetIntLimits(1, 500);
        InputM4TargetTicks.Name = "M4 Target Ticks"; InputM4TargetTicks.SetInt(40);  InputM4TargetTicks.SetIntLimits(1, 2000);
        InputM5StopTicks.Name = "M5 Stop Ticks";    InputM5StopTicks.SetInt(50);    InputM5StopTicks.SetIntLimits(1, 500);
        InputM5TargetTicks.Name = "M5 Target Ticks"; InputM5TargetTicks.SetInt(120); InputM5TargetTicks.SetIntLimits(1, 2000);

        InputShowLines.Name = "Show Stop/Target Lines"; InputShowLines.SetYesNo(1);
        InputShowSkipped.Name = "Show Skipped Signals";  InputShowSkipped.SetYesNo(1);
        InputMaxVisibleRays.Name = "Max Signal Rays";     InputMaxVisibleRays.SetInt(3); InputMaxVisibleRays.SetIntLimits(0, 50);
        InputLabelDetail.Name = "Label Detail";
        InputLabelDetail.SetCustomInputStrings("Full;Score Only"); InputLabelDetail.SetCustomInputIndex(0);
        InputSkipBars.Name = "Skip First N Bars (0=process all)";
        InputSkipBars.SetInt(0); InputSkipBars.SetIntLimits(0, 500000);

        InputEnableAlerts.Name = "Enable Alert Sounds"; InputEnableAlerts.SetYesNo(0);
        InputM1AlertSound.Name = "M1 Alert Sound"; InputM1AlertSound.SetAlertSoundNumber(0);
        InputM3AlertSound.Name = "M3 Alert Sound"; InputM3AlertSound.SetAlertSoundNumber(0);
        InputM4AlertSound.Name = "M4 Alert Sound"; InputM4AlertSound.SetAlertSoundNumber(0);
        InputM5AlertSound.Name = "M5 Alert Sound"; InputM5AlertSound.SetAlertSoundNumber(0);

        InputCSVPath.Name = "Raw CSV Export Path";
        InputCSVPath.SetPathAndFileName("C:\\Projects\\sierrachart\\analysis\\analyzer_zonereaction\\ZTE_raw.csv");
        InputExportCSV.Name = "Export Raw CSV"; InputExportCSV.SetYesNo(1);
        InputSuppressLabels.Name = "Suppress All Labels"; InputSuppressLabels.SetYesNo(1);

        InputObsMinutes.Name = "Observation Window (Minutes)";
        InputObsMinutes.SetInt(720); InputObsMinutes.SetIntLimits(1, 1440);
        InputVPThreshold.Name = "VP Ray Threshold (Ticks)";
        InputVPThreshold.SetInt(0); InputVPThreshold.SetIntLimits(0, 200);
        InputRayCSVPath.Name = "Ray Context CSV Path";
        InputRayCSVPath.SetPathAndFileName("C:\\Projects\\sierrachart\\analysis\\analyzer_zonereaction\\ray_context.csv");
        InputExportRayCSV.Name = "Export Ray Context CSV"; InputExportRayCSV.SetYesNo(1);

        // Subgraphs (ZB4-compatible indices 0-11, 13-14)
        SG_M1Demand.Name = "M1F Demand Entry"; SG_M1Demand.DrawStyle = DRAWSTYLE_ARROW_UP;
        SG_M1Demand.PrimaryColor = RGB(0,120,255); SG_M1Demand.LineWidth = 5; SG_M1Demand.DrawZeros = false;
        SG_M1Supply.Name = "M1F Supply Entry"; SG_M1Supply.DrawStyle = DRAWSTYLE_ARROW_DOWN;
        SG_M1Supply.PrimaryColor = RGB(200,0,0); SG_M1Supply.LineWidth = 5; SG_M1Supply.DrawZeros = false;
        SG_M3Demand.Name = "M3 Demand Entry"; SG_M3Demand.DrawStyle = DRAWSTYLE_ARROW_UP;
        SG_M3Demand.PrimaryColor = RGB(255,140,0); SG_M3Demand.LineWidth = 5; SG_M3Demand.DrawZeros = false;
        SG_M3Supply.Name = "M3 Supply Entry"; SG_M3Supply.DrawStyle = DRAWSTYLE_ARROW_DOWN;
        SG_M3Supply.PrimaryColor = RGB(200,0,200); SG_M3Supply.LineWidth = 5; SG_M3Supply.DrawZeros = false;
        SG_M4Demand.Name = "M4 Demand Entry"; SG_M4Demand.DrawStyle = DRAWSTYLE_ARROW_UP;
        SG_M4Demand.PrimaryColor = RGB(128,128,128); SG_M4Demand.LineWidth = 4; SG_M4Demand.DrawZeros = false;
        SG_M4Supply.Name = "M4 Supply Entry"; SG_M4Supply.DrawStyle = DRAWSTYLE_ARROW_DOWN;
        SG_M4Supply.PrimaryColor = RGB(128,128,128); SG_M4Supply.LineWidth = 4; SG_M4Supply.DrawZeros = false;
        SG_SkipDemand.Name = "Skip Demand Touch"; SG_SkipDemand.DrawStyle = DRAWSTYLE_ARROW_UP;
        SG_SkipDemand.PrimaryColor = RGB(128,128,128); SG_SkipDemand.LineWidth = 3; SG_SkipDemand.DrawZeros = false;
        SG_SkipSupply.Name = "Skip Supply Touch"; SG_SkipSupply.DrawStyle = DRAWSTYLE_ARROW_DOWN;
        SG_SkipSupply.PrimaryColor = RGB(128,128,128); SG_SkipSupply.LineWidth = 3; SG_SkipSupply.DrawZeros = false;
        SG_M5Demand.Name = "M5 Demand Entry"; SG_M5Demand.DrawStyle = DRAWSTYLE_ARROW_UP;
        SG_M5Demand.PrimaryColor = RGB(0,180,80); SG_M5Demand.LineWidth = 4; SG_M5Demand.DrawZeros = false;
        SG_M5Supply.Name = "M5 Supply Entry"; SG_M5Supply.DrawStyle = DRAWSTYLE_ARROW_DOWN;
        SG_M5Supply.PrimaryColor = RGB(180,200,0); SG_M5Supply.LineWidth = 4; SG_M5Supply.DrawZeros = false;
        SG_TrendSlope.Name = "Trend Bar Color"; SG_TrendSlope.DrawStyle = DRAWSTYLE_COLOR_BAR;
        SG_TrendSlope.PrimaryColor = RGB(0,120,255); SG_TrendSlope.SecondaryColor = RGB(200,0,0);
        SG_TrendSlope.SecondaryColorUsed = 1; SG_TrendSlope.DrawZeros = false;
        SG_TrendZero.Name = "Trend Slope (Ticks)"; SG_TrendZero.DrawStyle = DRAWSTYLE_IGNORE; SG_TrendZero.DrawZeros = false;
        SG_DemandEdge.Name = "Demand Edge Touch"; SG_DemandEdge.DrawStyle = DRAWSTYLE_ARROW_UP;
        SG_DemandEdge.PrimaryColor = RGB(0,120,255); SG_DemandEdge.LineWidth = 6; SG_DemandEdge.DrawZeros = false;
        SG_M1HDemand.Name = "M1H Demand Entry"; SG_M1HDemand.DrawStyle = DRAWSTYLE_ARROW_UP;
        SG_M1HDemand.PrimaryColor = RGB(100,160,255); SG_M1HDemand.LineWidth = 4; SG_M1HDemand.DrawZeros = false;
        SG_M1HSupply.Name = "M1H Supply Entry"; SG_M1HSupply.DrawStyle = DRAWSTYLE_ARROW_DOWN;
        SG_M1HSupply.PrimaryColor = RGB(255,80,80); SG_M1HSupply.LineWidth = 4; SG_M1HSupply.DrawZeros = false;
        SG_SupplyEdge.Name = "Supply Edge Touch"; SG_SupplyEdge.DrawStyle = DRAWSTYLE_ARROW_DOWN;
        SG_SupplyEdge.PrimaryColor = RGB(200,0,0); SG_SupplyEdge.LineWidth = 6; SG_SupplyEdge.DrawZeros = false;
        SG_VPRayTouch.Name = "VP Ray Touch"; SG_VPRayTouch.DrawStyle = DRAWSTYLE_DIAMOND;
        SG_VPRayTouch.PrimaryColor = RGB(255,255,0); SG_VPRayTouch.LineWidth = 5; SG_VPRayTouch.DrawZeros = false;
        SG_Reaction.Name = "Reaction Ticks"; SG_Reaction.DrawStyle = DRAWSTYLE_IGNORE; SG_Reaction.DrawZeros = false;
        SG_Penetration.Name = "Penetration Ticks"; SG_Penetration.DrawStyle = DRAWSTYLE_IGNORE; SG_Penetration.DrawZeros = false;

        return;
    }

    // ══════════════════════════════════════════════════════════════════
    //  LAST CALL CLEANUP
    // ══════════════════════════════════════════════════════════════════

    if (sc.LastCallToFunction)
    {
        SignalStorage* p = (SignalStorage*)sc.GetPersistentPointer(kSignalStoragePtr);
        if (p != nullptr) { DeleteAllSignalDrawings(sc, MAX_TRACKED_SIGNALS); sc.FreeMemory(p); sc.SetPersistentPointer(kSignalStoragePtr, nullptr); }
        ReactionStorage* r = (ReactionStorage*)sc.GetPersistentPointer(kReactionStoragePtr);
        if (r != nullptr) { sc.FreeMemory(r); sc.SetPersistentPointer(kReactionStoragePtr, nullptr); }
        return;
    }

    // ══════════════════════════════════════════════════════════════════
    //  PERSISTENT STORAGE
    // ══════════════════════════════════════════════════════════════════

    SignalStorage* pSig = GetOrAllocateSignalStorage(sc);
    ReactionStorage* pRxn = GetOrAllocateReactionStorage(sc);
    if (pSig == nullptr || pRxn == nullptr) return;

    int index = sc.Index;

    // ══════════════════════════════════════════════════════════════════
    //  FULL RECALC RESET
    // ══════════════════════════════════════════════════════════════════

    if (sc.UpdateStartIndex == 0 && index == 0)
    {
        DeleteAllSignalDrawings(sc, MAX_TRACKED_SIGNALS);
        pSig->SignalCount = 0; pSig->ZoneCount = 0;
        pSig->LastBreakBar = 0; pSig->LastHeldBar = 0;
        memset(pSig->Signals, 0, sizeof(pSig->Signals));
        memset(pSig->Zones, 0, sizeof(pSig->Zones));
        pRxn->TrackCount = 0; pRxn->VPRayCount = 0; pRxn->RayCount = 0;
        memset(pRxn->Tracks, 0, sizeof(pRxn->Tracks));
        memset(pRxn->VPRays, 0, sizeof(pRxn->VPRays));
        memset(pRxn->Rays, 0, sizeof(pRxn->Rays));
    }

    if (index < 1) return;

    bool isLiveBar = (index == sc.ArraySize - 1);

    // ══════════════════════════════════════════════════════════════════
    //  READ INPUTS
    // ══════════════════════════════════════════════════════════════════

    int activeCount = InputActiveCount.GetInt();
    if (activeCount < 1) activeCount = 1;
    if (activeCount > MAX_CHART_SLOTS) activeCount = MAX_CHART_SLOTS;

    int m1FullThreshold = InputM1FullThreshold.GetInt();
    int m1HalfThreshold = InputM1HalfThreshold.GetInt();
    int maxSeq          = InputMaxTouchSeq.GetInt();
    float trendThr      = (float)InputTrendThreshold.GetInt();
    float confluenceTol = InputConfluenceTol.GetInt() * sc.TickSize;
    bool showLines      = InputShowLines.GetYesNo() != 0;
    int  maxVisibleRays = InputMaxVisibleRays.GetInt();
    int  skipBars       = InputSkipBars.GetInt();
    bool gradeOnly      = (InputLabelDetail.GetIndex() == 1);
    int  fastPeriod     = InputFastEMAPeriod.GetInt();
    int  slowPeriod     = InputSlowEMAPeriod.GetInt();
    bool useEMA         = (InputTrendMethod.GetIndex() == 1);
    int  slopeLookback  = InputSlopeLookback.GetInt();
    int  m3Stop = InputM3StopTicks.GetInt(), m3Target = InputM3TargetTicks.GetInt();
    int  m4Stop = InputM4StopTicks.GetInt(), m4Target = InputM4TargetTicks.GetInt();
    int  m5Stop = InputM5StopTicks.GetInt(), m5Target = InputM5TargetTicks.GetInt();
    int  cascadeWindow  = InputCascadeWindow.GetInt();
    bool showSkipped    = InputShowSkipped.GetYesNo() != 0;
    bool exportCSV      = InputExportCSV.GetYesNo() != 0;
    bool exportRayCSV   = InputExportRayCSV.GetYesNo() != 0;
    bool suppressLabels = InputSuppressLabels.GetYesNo() != 0;
    bool alertsEnabled  = InputEnableAlerts.GetYesNo() != 0;
    unsigned int m1Sound = InputM1AlertSound.GetAlertSoundNumber();
    unsigned int m3Sound = InputM3AlertSound.GetAlertSoundNumber();
    unsigned int m4Sound = InputM4AlertSound.GetAlertSoundNumber();
    unsigned int m5Sound = InputM5AlertSound.GetAlertSoundNumber();
    float tickSize       = sc.TickSize;
    int   obsMinutes     = InputObsMinutes.GetInt();
    float vpThreshold    = InputVPThreshold.GetInt() * tickSize;

    if (skipBars > 0 && index < skipBars) return;

    // ══════════════════════════════════════════════════════════════════
    //  BUILD CHART SLOT TABLE AND FETCH V4 ARRAYS
    // ══════════════════════════════════════════════════════════════════

    ChartSlotData slots[MAX_CHART_SLOTS];
    memset(slots, 0, sizeof(slots));
    for (int s = 0; s < activeCount; s++)
    {
        int inputBase = 1 + s * 2;
        slots[s].ChartNumber = sc.Input[inputBase].GetInt();
        slots[s].StudyID     = sc.Input[inputBase + 1].GetInt();
        FetchChartSlot(sc, slots[s], index);
    }

    // ══════════════════════════════════════════════════════════════════
    //  CLEAR SUBGRAPHS
    // ══════════════════════════════════════════════════════════════════

    SG_M1Demand[index] = 0; SG_M1Supply[index] = 0;
    SG_M1HDemand[index] = 0; SG_M1HSupply[index] = 0;
    SG_M3Demand[index] = 0; SG_M3Supply[index] = 0;
    SG_M4Demand[index] = 0; SG_M4Supply[index] = 0;
    SG_M5Demand[index] = 0; SG_M5Supply[index] = 0;
    SG_SkipDemand[index] = 0; SG_SkipSupply[index] = 0;
    SG_DemandEdge[index] = 0; SG_SupplyEdge[index] = 0;
    SG_VPRayTouch[index] = 0;
    SG_Reaction[index] = 0; SG_Penetration[index] = 0;

    // ══════════════════════════════════════════════════════════════════
    //  TREND OUTPUT (from ZB4)
    // ══════════════════════════════════════════════════════════════════

    SCFloatArrayRef fastEMA = SG_TrendSlope.Arrays[0];
    SCFloatArrayRef slowEMA = SG_TrendSlope.Arrays[1];
    float trendValue;
    if (useEMA) {
        sc.ExponentialMovAvg(sc.BaseData[SC_LAST], fastEMA, index, fastPeriod);
        sc.ExponentialMovAvg(sc.BaseData[SC_LAST], slowEMA, index, slowPeriod);
        trendValue = (fastEMA[index] - slowEMA[index]) / tickSize;
    } else {
        trendValue = CalcTrendSlope(sc, index, tickSize, slopeLookback);
    }
    SG_TrendZero[index] = trendValue;
    if (trendValue > trendThr)      { SG_TrendSlope[index] = 1; SG_TrendSlope.DataColor[index] = RGB(0,120,255); }
    else if (trendValue < -trendThr){ SG_TrendSlope[index] = 1; SG_TrendSlope.DataColor[index] = RGB(200,0,0); }
    else                            { SG_TrendSlope[index] = 0; }

    // ══════════════════════════════════════════════════════════════════
    //  CASCADE TRACKING + RAY ACCUMULATION
    // ══════════════════════════════════════════════════════════════════

    for (int s = 0; s < activeCount; s++)
    {
        if (!slots[s].Valid) continue;
        int htfIdx = slots[s].V4Idx;

        // Cascade tracking (from ZB4)
        if (slots[s].DemandBroken.GetArraySize() > htfIdx && htfIdx >= 0)
            if (slots[s].DemandBroken[htfIdx] > 0) pSig->LastBreakBar = index;
        if (slots[s].SupplyBroken.GetArraySize() > htfIdx && htfIdx >= 0)
            if (slots[s].SupplyBroken[htfIdx] > 0) pSig->LastBreakBar = index;

        // Ray accumulation (NEW — V4 SG 12/13)
        // Scan ALL HTF bars between V4Idx1 and V4Idx (inclusive of V4Idx,
        // exclusive of V4Idx1 which was already processed on the previous base bar).
        // SG 12/13 fire only on the break bar — multiple HTF bars can elapse
        // between consecutive base bars, so we must check the full range.
        {
            int htfStart = slots[s].V4Idx1;  // include V4Idx1 — dedup prevents double inserts
            int htfEnd   = slots[s].V4Idx;   // current HTF bar (inclusive)
            if (htfStart < 0) htfStart = 0;
            int demArrSize = slots[s].DemandRayPrice.GetArraySize();
            int supArrSize = slots[s].SupplyRayPrice.GetArraySize();

            for (int hi = htfStart; hi <= htfEnd; hi++)
            {
                // Demand rays
                if (demArrSize > hi)
                {
                    float demRay = slots[s].DemandRayPrice[hi];
                    if (demRay != 0.0f)
                    {
                        // Dedup: check full accumulator for this price+slot+side
                        bool dup = false;
                        for (int r = 0; r < pRxn->RayCount; r++) {
                            if (pRxn->Rays[r].IsDemand && pRxn->Rays[r].SlotIdx == s && fabs(pRxn->Rays[r].Price - demRay) < 0.01f) { dup = true; break; }
                        }
                        if (!dup) {
                            if (pRxn->RayCount >= MAX_ACCUMULATED_RAYS) EvictOldRays(pRxn);
                            RayRecord& ray = pRxn->Rays[pRxn->RayCount];
                            ray.Price = demRay; ray.IsDemand = true; ray.BreakBar = index;
                            ray.SlotIdx = s; ray.ZoneTop = 0; ray.ZoneBot = 0;
                            pRxn->RayCount++;
                        }
                    }
                }
                // Supply rays
                if (supArrSize > hi)
                {
                    float supRay = slots[s].SupplyRayPrice[hi];
                    if (supRay != 0.0f)
                    {
                        bool dup = false;
                        for (int r = 0; r < pRxn->RayCount; r++) {
                            if (!pRxn->Rays[r].IsDemand && pRxn->Rays[r].SlotIdx == s && fabs(pRxn->Rays[r].Price - supRay) < 0.01f) { dup = true; break; }
                        }
                        if (!dup) {
                            if (pRxn->RayCount >= MAX_ACCUMULATED_RAYS) EvictOldRays(pRxn);
                            RayRecord& ray = pRxn->Rays[pRxn->RayCount];
                            ray.Price = supRay; ray.IsDemand = false; ray.BreakBar = index;
                            ray.SlotIdx = s; ray.ZoneTop = 0; ray.ZoneBot = 0;
                            pRxn->RayCount++;
                        }
                    }
                }
            }
        }
    }

    // ══════════════════════════════════════════════════════════════════
    //  ZONE DISCOVERY (from ZB4 — register zones before touch detection)
    // ══════════════════════════════════════════════════════════════════

    if (index >= 1 && !isLiveBar)
    for (int s = 0; s < activeCount; s++)
    {
        if (!slots[s].Valid) continue;
        float dTop = slots[s].DemandTop[slots[s].V4Idx];
        float dBot = slots[s].DemandBot[slots[s].V4Idx];
        float sTop = slots[s].SupplyTop[slots[s].V4Idx];
        float sBot = slots[s].SupplyBot[slots[s].V4Idx];
        if (dTop > 0 && dBot > 0) FindOrCreateZone(pSig, dTop, dBot, true, s, index, slots[s].V4Idx, 0.01f);
        if (sTop > 0 && sBot > 0) FindOrCreateZone(pSig, sTop, sBot, false, s, index, slots[s].V4Idx, 0.01f);
    }

    // ══════════════════════════════════════════════════════════════════
    //  TOUCH DETECTION + SCORING (per chart slot)
    // ══════════════════════════════════════════════════════════════════

    if (index >= 1 && !isLiveBar)
    for (int s = 0; s < activeCount; s++)
    {
        if (!slots[s].Valid) continue;

        int evalBar = index, prevBar = index - 1;
        float low = sc.Low[evalBar], high = sc.High[evalBar];
        float low1 = sc.Low[prevBar], high1 = sc.High[prevBar];

        float dTop = slots[s].DemandTop[slots[s].V4Idx];
        float dBot = slots[s].DemandBot[slots[s].V4Idx];
        float sTop = slots[s].SupplyTop[slots[s].V4Idx];
        float sBot = slots[s].SupplyBot[slots[s].V4Idx];
        float vpRay = (slots[s].VPImbalance.GetArraySize() > 0) ? slots[s].VPImbalance[slots[s].V4Idx] : 0.0f;
        float dTop1 = slots[s].DemandTop[slots[s].V4Idx1];
        float sBot1 = slots[s].SupplyBot[slots[s].V4Idx1];

        // --- DEMAND_EDGE ---
        if (dTop > 0 && dBot > 0)
        {
            bool zoneConsistent = (dTop1 > 0 && fabs(dTop - dTop1) < tickSize * 2);
            bool touchNow       = (low <= dTop);
            bool notTouchBefore = (low1 > dTop1);

            if (zoneConsistent && touchNow && notTouchBefore)
            {
                int zoneIdx = FindOrCreateZone(pSig, dTop, dBot, true, s, evalBar, slots[s].V4Idx, 0.01f);
                if (!IsDebouncedDuplicate(pSig, kDemandEdge, dTop, tickSize, evalBar, s))
                {
                    int seq = 1, ageBars = 0;
                    if (zoneIdx >= 0) { pSig->Zones[zoneIdx].TouchCount++; seq = pSig->Zones[zoneIdx].TouchCount; ageBars = evalBar - pSig->Zones[zoneIdx].FirstSeenBar; }

                    float slope = useEMA ? (fastEMA[evalBar] - slowEMA[evalBar]) / tickSize : CalcTrendSlope(sc, evalBar, tickSize, slopeLookback);
                    int trendCtx = ClassifyTrend(kDemandEdge, slope, trendThr);
                    int tfCount = CountTFConfluence(slots, activeCount, kDemandEdge, dTop, confluenceTol, s);
                    float approachVel = CalcApproachVelocity(sc, evalBar, tickSize);
                    float zoneWidth = fabs(dTop - dBot) / tickSize;
                    float penetration = CalcPenetrationTicks(kDemandEdge, dTop, low, high, tickSize);
                    int sessionCls = ClassifySession(sc.BaseDateTimeIn[evalBar].GetHour(), sc.BaseDateTimeIn[evalBar].GetMinute());
                    int dow = sc.BaseDateTimeIn[evalBar].GetDayOfWeek();
                    int cascState = DetermineCascadeState(evalBar, pSig->LastBreakBar, pSig->LastHeldBar, cascadeWindow);

                    bool vpNearZone = (vpRay > 0.0f) && (fabs(vpRay - dTop) < fabs(dTop - dBot) * 3.0f);

                    int qualityScore = CalcZoneQualityScore(slots[s].TFWeightScore, zoneWidth, vpNearZone);
                    int contextScore = CalcContextScore(cascState, sessionCls, approachVel, penetration);
                    int totalScore = qualityScore + contextScore;

                    bool seqOk = (seq <= maxSeq), trendOk = (trendCtx == kWithTrend);
                    int mode;
                    if (seqOk && trendOk && totalScore >= m1FullThreshold) mode = kMode1Full;
                    else if (seqOk && trendOk && totalScore >= m1HalfThreshold) mode = kMode1Half;
                    else if (seqOk && !trendOk) mode = kMode3;
                    else if (sessionCls == kAfternoon) mode = kMode4;
                    else mode = kMode5;

                    if (pSig->SignalCount >= MAX_TRACKED_SIGNALS) EvictOldSignals(sc, pSig, pRxn);
                    if (pSig->SignalCount < MAX_TRACKED_SIGNALS)
                    {
                        SignalRecord& sig = pSig->Signals[pSig->SignalCount];
                        sig.TouchPrice = dTop; sig.ZoneTop = dTop; sig.ZoneBot = dBot;
                        sig.TrendSlope = slope; sig.VPRayPrice = vpNearZone ? vpRay : 0.0f;
                        sig.BarIndex = evalBar; sig.TouchSequence = seq; sig.Type = kDemandEdge;
                        sig.TrendCtx = trendCtx; sig.ModeAssignment = mode;
                        sig.QualityScore = qualityScore; sig.ContextScore = contextScore; sig.TotalScore = totalScore;
                        sig.TFConfluence = tfCount; sig.ApproachVelocity = approachVel;
                        sig.ZoneWidthTicks = zoneWidth; sig.ZoneAgeBars = ageBars;
                        sig.PenetrationTicks = penetration; sig.TFWeightScore = slots[s].TFWeightScore;
                        sig.SessionClass = sessionCls; sig.DayOfWeek = dow; sig.CascadeState = cascState;
                        sig.SourceSlot = s; sig.SourceHtfBar = slots[s].V4Idx; sig.ConfirmedBar = evalBar;
                        sig.DbgPrevHigh = low1; sig.DbgPrevSBot = dTop1;
                        sig.DbgEvalHigh = low; sig.DbgEvalSBot = dTop;
                        sig.HasVPRay = vpNearZone; sig.CascadeActive = (cascState == kCascadePriorBroke);
                        sig.HtfConfirmed = true; sig.DrawingsPlaced = false;
                        sig.RaysResolved = false; sig.Active = true;

                        // Parallel reaction track
                        ReactionTrack& rt = pRxn->Tracks[pSig->SignalCount];
                        rt.Reaction = 0; rt.Penetration = 0; rt.ReactionPeakBar = evalBar;
                        rt.ResolutionBar = -1; rt.BreakBarIndex = -1; rt.ApproachDir = -1;
                        rt.SourceSlotIdx = s; rt.SourceChart = slots[s].ChartNumber;
                        rt.SourceStudyID = slots[s].StudyID;
                        const char* sl = GetSourceLabel(sc, slots[s].ChartNumber);
                        strncpy(rt.SourceLabel, sl, 31); rt.SourceLabel[31] = '\0';
                        rt.ZoneBroken = false; rt.Resolved = false;
                        for (int th = 0; th < NUM_RXN_THRESHOLDS; th++) rt.RxnCrossBar[th] = -1;
                        for (int th = 0; th < NUM_PEN_THRESHOLDS; th++) rt.PenCrossBar[th] = -1;

                        pSig->SignalCount++;
                        pRxn->TrackCount = pSig->SignalCount;

                        if (mode != kSkip) pSig->LastHeldBar = evalBar;

                        SG_DemandEdge[evalBar] = low - (5 * tickSize);
                        if (!suppressLabels) {
                            float arrowY = sc.Low[evalBar] - (15 * tickSize);
                            switch (mode) {
                                case kMode1Full: SG_M1Demand[evalBar] = arrowY; break;
                                case kMode1Half: SG_M1HDemand[evalBar] = arrowY; break;
                                case kMode3: SG_M3Demand[evalBar] = arrowY; break;
                                case kMode4: SG_M4Demand[evalBar] = arrowY; break;
                                case kMode5: SG_M5Demand[evalBar] = arrowY; break;
                                case kSkip: if (showSkipped) SG_SkipDemand[evalBar] = arrowY; break;
                            }
                        }
                        if (isLiveBar && alertsEnabled)
                            PlayModeAlert(sc, mode, kDemandEdge, seq, totalScore, m1Sound, m3Sound, m4Sound, m5Sound);
                    }
                }
            }
        }

        // --- SUPPLY_EDGE ---
        if (sTop > 0 && sBot > 0)
        {
            bool zoneConsistent = (sBot1 > 0 && fabs(sBot - sBot1) < tickSize * 2);
            bool touchNow       = (high >= sBot);
            bool notTouchBefore = (high1 < sBot1);

            if (zoneConsistent && touchNow && notTouchBefore)
            {
                int zoneIdx = FindOrCreateZone(pSig, sTop, sBot, false, s, evalBar, slots[s].V4Idx, 0.01f);
                if (!IsDebouncedDuplicate(pSig, kSupplyEdge, sBot, tickSize, evalBar, s))
                {
                    int seq = 1, ageBars = 0;
                    if (zoneIdx >= 0) { pSig->Zones[zoneIdx].TouchCount++; seq = pSig->Zones[zoneIdx].TouchCount; ageBars = evalBar - pSig->Zones[zoneIdx].FirstSeenBar; }

                    float slope = useEMA ? (fastEMA[evalBar] - slowEMA[evalBar]) / tickSize : CalcTrendSlope(sc, evalBar, tickSize, slopeLookback);
                    int trendCtx = ClassifyTrend(kSupplyEdge, slope, trendThr);
                    int tfCount = CountTFConfluence(slots, activeCount, kSupplyEdge, sBot, confluenceTol, s);
                    float approachVel = CalcApproachVelocity(sc, evalBar, tickSize);
                    float zoneWidth = fabs(sTop - sBot) / tickSize;
                    float penetration = CalcPenetrationTicks(kSupplyEdge, sBot, low, high, tickSize);
                    int sessionCls = ClassifySession(sc.BaseDateTimeIn[evalBar].GetHour(), sc.BaseDateTimeIn[evalBar].GetMinute());
                    int dow = sc.BaseDateTimeIn[evalBar].GetDayOfWeek();
                    int cascState = DetermineCascadeState(evalBar, pSig->LastBreakBar, pSig->LastHeldBar, cascadeWindow);

                    bool vpNearZone = (vpRay > 0.0f) && (fabs(vpRay - sBot) < fabs(sTop - sBot) * 3.0f);

                    int qualityScore = CalcZoneQualityScore(slots[s].TFWeightScore, zoneWidth, vpNearZone);
                    int contextScore = CalcContextScore(cascState, sessionCls, approachVel, penetration);
                    int totalScore = qualityScore + contextScore;

                    bool seqOk = (seq <= maxSeq), trendOk = (trendCtx == kWithTrend);
                    int mode;
                    if (seqOk && trendOk && totalScore >= m1FullThreshold) mode = kMode1Full;
                    else if (seqOk && trendOk && totalScore >= m1HalfThreshold) mode = kMode1Half;
                    else if (seqOk && !trendOk) mode = kMode3;
                    else if (sessionCls == kAfternoon) mode = kMode4;
                    else mode = kMode5;

                    if (pSig->SignalCount >= MAX_TRACKED_SIGNALS) EvictOldSignals(sc, pSig, pRxn);
                    if (pSig->SignalCount < MAX_TRACKED_SIGNALS)
                    {
                        SignalRecord& sig = pSig->Signals[pSig->SignalCount];
                        sig.TouchPrice = sBot; sig.ZoneTop = sTop; sig.ZoneBot = sBot;
                        sig.TrendSlope = slope; sig.VPRayPrice = vpNearZone ? vpRay : 0.0f;
                        sig.BarIndex = evalBar; sig.TouchSequence = seq; sig.Type = kSupplyEdge;
                        sig.TrendCtx = trendCtx; sig.ModeAssignment = mode;
                        sig.QualityScore = qualityScore; sig.ContextScore = contextScore; sig.TotalScore = totalScore;
                        sig.TFConfluence = tfCount; sig.ApproachVelocity = approachVel;
                        sig.ZoneWidthTicks = zoneWidth; sig.ZoneAgeBars = ageBars;
                        sig.PenetrationTicks = penetration; sig.TFWeightScore = slots[s].TFWeightScore;
                        sig.SessionClass = sessionCls; sig.DayOfWeek = dow; sig.CascadeState = cascState;
                        sig.SourceSlot = s; sig.SourceHtfBar = slots[s].V4Idx; sig.ConfirmedBar = evalBar;
                        sig.DbgPrevHigh = high1; sig.DbgPrevSBot = sBot1;
                        sig.DbgEvalHigh = high; sig.DbgEvalSBot = sBot;
                        sig.HasVPRay = vpNearZone; sig.CascadeActive = (cascState == kCascadePriorBroke);
                        sig.HtfConfirmed = true; sig.DrawingsPlaced = false;
                        sig.RaysResolved = false; sig.Active = true;

                        ReactionTrack& rt = pRxn->Tracks[pSig->SignalCount];
                        rt.Reaction = 0; rt.Penetration = 0; rt.ReactionPeakBar = evalBar;
                        rt.ResolutionBar = -1; rt.BreakBarIndex = -1; rt.ApproachDir = +1;
                        rt.SourceSlotIdx = s; rt.SourceChart = slots[s].ChartNumber;
                        rt.SourceStudyID = slots[s].StudyID;
                        const char* sl = GetSourceLabel(sc, slots[s].ChartNumber);
                        strncpy(rt.SourceLabel, sl, 31); rt.SourceLabel[31] = '\0';
                        rt.ZoneBroken = false; rt.Resolved = false;
                        for (int th = 0; th < NUM_RXN_THRESHOLDS; th++) rt.RxnCrossBar[th] = -1;
                        for (int th = 0; th < NUM_PEN_THRESHOLDS; th++) rt.PenCrossBar[th] = -1;

                        pSig->SignalCount++;
                        pRxn->TrackCount = pSig->SignalCount;

                        if (mode != kSkip) pSig->LastHeldBar = evalBar;

                        SG_SupplyEdge[evalBar] = high + (5 * tickSize);
                        if (!suppressLabels) {
                            float arrowY = sc.High[evalBar] + (15 * tickSize);
                            switch (mode) {
                                case kMode1Full: SG_M1Supply[evalBar] = arrowY; break;
                                case kMode1Half: SG_M1HSupply[evalBar] = arrowY; break;
                                case kMode3: SG_M3Supply[evalBar] = arrowY; break;
                                case kMode4: SG_M4Supply[evalBar] = arrowY; break;
                                case kMode5: SG_M5Supply[evalBar] = arrowY; break;
                                case kSkip: if (showSkipped) SG_SkipSupply[evalBar] = arrowY; break;
                            }
                        }
                        if (isLiveBar && alertsEnabled)
                            PlayModeAlert(sc, mode, kSupplyEdge, seq, totalScore, m1Sound, m3Sound, m4Sound, m5Sound);
                    }
                }
            }
        }

        // --- VP_RAY (from ZRA — goes to ReactionStorage only) ---
        if (vpRay > 0)
        {
            bool rayTouchNow  = (low <= vpRay + vpThreshold) && (high >= vpRay - vpThreshold);
            float vpRay1 = (slots[s].VPImbalance.GetArraySize() > 0) ? slots[s].VPImbalance[slots[s].V4Idx1] : 0.0f;
            bool rayNotBefore = (vpRay1 == 0) || (low1 > vpRay1 + vpThreshold) || (high1 < vpRay1 - vpThreshold);

            if (rayTouchNow && rayNotBefore)
            {
                if (!IsVPRayDebouncedDuplicate(pRxn, vpRay, tickSize, evalBar, slots[s].ChartNumber))
                {
                    float prevMid = (high1 + low1) * 0.5f;
                    int approachDir = (prevMid > vpRay) ? -1 : +1;
                    float zTop = 0, zBot = 0; bool vpIsDemand = false;
                    if (dTop > 0 && dBot > 0) { zTop = dTop; zBot = dBot; vpIsDemand = true; }
                    else if (sTop > 0 && sBot > 0) { zTop = sTop; zBot = sBot; }

                    int touchSeq = 0, ageBars = 0;
                    if (zTop > 0 && zBot > 0) {
                        int zIdx = FindOrCreateZone(pSig, zTop, zBot, vpIsDemand, s, evalBar, slots[s].V4Idx, 0.01f);
                        if (zIdx >= 0) { pSig->Zones[zIdx].TouchCount++; touchSeq = pSig->Zones[zIdx].TouchCount; ageBars = evalBar - pSig->Zones[zIdx].FirstSeenBar; }
                    }
                    float velocity = CalcApproachVelocity(sc, evalBar, tickSize);
                    float trend = useEMA ? (fastEMA[evalBar] - slowEMA[evalBar]) / tickSize : CalcTrendSlope(sc, evalBar, tickSize, slopeLookback);

                    if (pRxn->VPRayCount >= MAX_VP_RAY_TOUCHES) EvictOldVPRays(pRxn);
                    if (pRxn->VPRayCount < MAX_VP_RAY_TOUCHES)
                    {
                        VPRayTouch& v = pRxn->VPRays[pRxn->VPRayCount];
                        v.BarIndex = evalBar; v.TouchPrice = vpRay;
                        v.ZoneTop = zTop; v.ZoneBot = zBot; v.VPRayPrice = vpRay;
                        v.ApproachVelocity = velocity; v.TrendSlope = trend;
                        v.TouchSequence = touchSeq; v.ZoneAgeBars = ageBars;
                        v.ApproachDir = approachDir; v.SourceChart = slots[s].ChartNumber;
                        v.SourceStudyID = slots[s].StudyID; v.SourceSlotIdx = s;
                        const char* sl = GetSourceLabel(sc, slots[s].ChartNumber);
                        strncpy(v.SourceLabel, sl, 31); v.SourceLabel[31] = '\0';
                        v.Reaction = 0; v.Penetration = 0; v.ReactionPeakBar = evalBar;
                        v.ResolutionBar = -1; v.BreakBarIndex = -1;
                        v.HasVPRay = true; v.ZoneBroken = false; v.Resolved = false; v.Active = true;
                        for (int th = 0; th < NUM_RXN_THRESHOLDS; th++) v.RxnCrossBar[th] = -1;
                        for (int th = 0; th < NUM_PEN_THRESHOLDS; th++) v.PenCrossBar[th] = -1;
                        pRxn->VPRayCount++;

                        SG_VPRayTouch[evalBar] = vpRay;
                    }
                }
            }
        }
    }

    // ══════════════════════════════════════════════════════════════════
    //  REACTION/PENETRATION TRACKING (from ZRA)
    // ══════════════════════════════════════════════════════════════════

    double observationDays = obsMinutes / 1440.0;
    float high_cur = sc.High[index], low_cur = sc.Low[index];

    // Track edge touches (DEMAND_EDGE / SUPPLY_EDGE)
    for (int i = 0; i < pSig->SignalCount && i < pRxn->TrackCount; i++)
    {
        SignalRecord& sig = pSig->Signals[i];
        ReactionTrack& rt = pRxn->Tracks[i];
        if (!sig.Active || rt.Resolved) continue;
        if (index <= sig.BarIndex) continue;

        float reactionVal, penetrationVal;
        if (rt.ApproachDir == -1) { reactionVal = (high_cur - sig.TouchPrice) / tickSize; penetrationVal = (sig.TouchPrice - low_cur) / tickSize; }
        else                      { reactionVal = (sig.TouchPrice - low_cur) / tickSize; penetrationVal = (high_cur - sig.TouchPrice) / tickSize; }
        if (reactionVal < 0) reactionVal = 0;
        if (penetrationVal < 0) penetrationVal = 0;

        if (reactionVal > rt.Reaction) { rt.Reaction = reactionVal; rt.ReactionPeakBar = index; }
        if (penetrationVal > rt.Penetration) rt.Penetration = penetrationVal;

        for (int th = 0; th < NUM_RXN_THRESHOLDS; th++)
            if (rt.RxnCrossBar[th] == -1 && reactionVal >= RXN_THRESHOLDS[th]) rt.RxnCrossBar[th] = index;
        for (int th = 0; th < NUM_PEN_THRESHOLDS; th++)
            if (rt.PenCrossBar[th] == -1 && penetrationVal >= PEN_THRESHOLDS[th]) rt.PenCrossBar[th] = index;

        // Zone break detection
        int si = rt.SourceSlotIdx;
        if (!rt.ZoneBroken && si >= 0 && si < activeCount && slots[si].Valid) {
            int v4i = slots[si].V4Idx;
            if (sig.Type == kDemandEdge) {
                if ((slots[si].DemandBroken.GetArraySize() > v4i && v4i >= 0 && slots[si].DemandBroken[v4i] != 0) ||
                    (slots[si].DemandTop[v4i] == 0 && slots[si].DemandBot[v4i] == 0))
                { rt.ZoneBroken = true; rt.BreakBarIndex = index; }
            } else if (sig.Type == kSupplyEdge) {
                if ((slots[si].SupplyBroken.GetArraySize() > v4i && v4i >= 0 && slots[si].SupplyBroken[v4i] != 0) ||
                    (slots[si].SupplyTop[v4i] == 0 && slots[si].SupplyBot[v4i] == 0))
                { rt.ZoneBroken = true; rt.BreakBarIndex = index; }
            }
        }

        // Time-based resolution
        double elapsed = sc.BaseDateTimeIn[index].GetAsDouble() - sc.BaseDateTimeIn[sig.BarIndex].GetAsDouble();
        if (elapsed >= observationDays) {
            rt.Resolved = true; rt.ResolutionBar = index;
            SG_Reaction[sig.BarIndex] = rt.Reaction;
            SG_Penetration[sig.BarIndex] = rt.Penetration;
        }
    }

    // Track VP_RAY touches
    for (int i = 0; i < pRxn->VPRayCount; i++)
    {
        VPRayTouch& v = pRxn->VPRays[i];
        if (!v.Active || v.Resolved) continue;
        if (index <= v.BarIndex) continue;

        float reactionVal, penetrationVal;
        if (v.ApproachDir == -1) { reactionVal = (high_cur - v.TouchPrice) / tickSize; penetrationVal = (v.TouchPrice - low_cur) / tickSize; }
        else                     { reactionVal = (v.TouchPrice - low_cur) / tickSize; penetrationVal = (high_cur - v.TouchPrice) / tickSize; }
        if (reactionVal < 0) reactionVal = 0;
        if (penetrationVal < 0) penetrationVal = 0;

        if (reactionVal > v.Reaction) { v.Reaction = reactionVal; v.ReactionPeakBar = index; }
        if (penetrationVal > v.Penetration) v.Penetration = penetrationVal;

        for (int th = 0; th < NUM_RXN_THRESHOLDS; th++)
            if (v.RxnCrossBar[th] == -1 && reactionVal >= RXN_THRESHOLDS[th]) v.RxnCrossBar[th] = index;
        for (int th = 0; th < NUM_PEN_THRESHOLDS; th++)
            if (v.PenCrossBar[th] == -1 && penetrationVal >= PEN_THRESHOLDS[th]) v.PenCrossBar[th] = index;

        double elapsed = sc.BaseDateTimeIn[index].GetAsDouble() - sc.BaseDateTimeIn[v.BarIndex].GetAsDouble();
        if (elapsed >= observationDays) { v.Resolved = true; v.ResolutionBar = index; }
    }

    // ══════════════════════════════════════════════════════════════════
    //  SUBGRAPH RESTORATION PASS (from ZB4)
    // ══════════════════════════════════════════════════════════════════

    if (!suppressLabels)
    for (int i = 0; i < pSig->SignalCount; i++)
    {
        SignalRecord& sig = pSig->Signals[i];
        if (!sig.Active) continue;
        if (sig.BarIndex != index && sig.BarIndex != index - 1) continue;
        int sigBar = sig.BarIndex;
        bool isDemand = (sig.Type == kDemandEdge);
        float arrowY = isDemand ? (sc.Low[sigBar] - (15 * tickSize)) : (sc.High[sigBar] + (15 * tickSize));
        switch (sig.ModeAssignment) {
            case kMode1Full: if (isDemand) SG_M1Demand[sigBar] = arrowY; else SG_M1Supply[sigBar] = arrowY; break;
            case kMode1Half: if (isDemand) SG_M1HDemand[sigBar] = arrowY; else SG_M1HSupply[sigBar] = arrowY; break;
            case kMode3: if (isDemand) SG_M3Demand[sigBar] = arrowY; else SG_M3Supply[sigBar] = arrowY; break;
            case kMode4: if (isDemand) SG_M4Demand[sigBar] = arrowY; else SG_M4Supply[sigBar] = arrowY; break;
            case kMode5: if (isDemand) SG_M5Demand[sigBar] = arrowY; else SG_M5Supply[sigBar] = arrowY; break;
            case kSkip: if (showSkipped) { if (isDemand) SG_SkipDemand[sigBar] = arrowY; else SG_SkipSupply[sigBar] = arrowY; } break;
        }
    }

    // ══════════════════════════════════════════════════════════════════
    //  DRAWING PLACEMENT PASS (from ZB4)
    // ══════════════════════════════════════════════════════════════════

    if (!suppressLabels)
    for (int i = 0; i < pSig->SignalCount; i++)
    {
        SignalRecord& sig = pSig->Signals[i];
        if (!sig.Active || sig.DrawingsPlaced) continue;
        if (sig.BarIndex > index) continue;
        bool shouldDraw = (sig.ModeAssignment != kSkip) || (sig.ModeAssignment == kSkip && showSkipped);
        if (!shouldDraw) { sig.DrawingsPlaced = true; continue; }
        PlaceSignalDrawings(sc, sig, i, tickSize, sig.ModeAssignment, sig.TotalScore,
                            m3Stop, m3Target, m4Stop, m4Target, m5Stop, m5Target,
                            showLines, true, gradeOnly);
    }

    // ══════════════════════════════════════════════════════════════════
    //  RAY RESOLUTION PASS (from ZB4)
    // ══════════════════════════════════════════════════════════════════

    if (showLines && sc.UpdateStartIndex > 0)
    for (int i = 0; i < pSig->SignalCount; i++)
    {
        SignalRecord& sig = pSig->Signals[i];
        if (!sig.Active || !sig.DrawingsPlaced || sig.RaysResolved) continue;
        if (sig.ModeAssignment == kSkip || sig.BarIndex >= index) continue;
        int sigStop, sigTarget;
        GetModeStopTarget(sig.ModeAssignment, sig.ZoneWidthTicks, m3Stop, m3Target, m4Stop, m4Target, m5Stop, m5Target, sigStop, sigTarget);
        if (sigStop == 0) continue;
        float stopOff = sigStop * tickSize, targetOff = sigTarget * tickSize;
        bool isDemand = (sig.Type == kDemandEdge);
        float stopP = isDemand ? sig.TouchPrice - stopOff : sig.TouchPrice + stopOff;
        float targetP = isDemand ? sig.TouchPrice + targetOff : sig.TouchPrice - targetOff;
        bool resolved = false;
        for (int b = sig.BarIndex + 1; b <= index; b++) {
            if (isDemand) { if (sc.BaseData[SC_LOW][b] <= stopP || sc.BaseData[SC_HIGH][b] >= targetP) { resolved = true; break; } }
            else          { if (sc.BaseData[SC_HIGH][b] >= stopP || sc.BaseData[SC_LOW][b] <= targetP) { resolved = true; break; } }
        }
        if (resolved) {
            sc.DeleteACSChartDrawing(sc.ChartNumber, TOOL_DELETE_CHARTDRAWING, LN_BASE_STOP + i);
            sc.DeleteACSChartDrawing(sc.ChartNumber, TOOL_DELETE_CHARTDRAWING, LN_BASE_TARGET + i);
            sig.RaysResolved = true;
        }
    }

    // Ray limit cleanup
    if (showLines && maxVisibleRays > 0) {
        int activeRayCount = 0;
        for (int i = pSig->SignalCount - 1; i >= 0; i--)
            if (pSig->Signals[i].Active && pSig->Signals[i].DrawingsPlaced && !pSig->Signals[i].RaysResolved && pSig->Signals[i].ModeAssignment != kSkip)
                activeRayCount++;
        if (activeRayCount > maxVisibleRays) {
            int toRemove = activeRayCount - maxVisibleRays;
            for (int i = 0; i < pSig->SignalCount && toRemove > 0; i++) {
                SignalRecord& sig = pSig->Signals[i];
                if (sig.Active && sig.DrawingsPlaced && !sig.RaysResolved && sig.ModeAssignment != kSkip) {
                    sc.DeleteACSChartDrawing(sc.ChartNumber, TOOL_DELETE_CHARTDRAWING, LN_BASE_STOP + i);
                    sc.DeleteACSChartDrawing(sc.ChartNumber, TOOL_DELETE_CHARTDRAWING, LN_BASE_TARGET + i);
                    sig.RaysResolved = true; toRemove--;
                }
            }
        }
    }

    // ══════════════════════════════════════════════════════════════════
    //  CSV EXPORT (last bar only)
    // ══════════════════════════════════════════════════════════════════

    if (index == sc.ArraySize - 1)
    {
        if (exportCSV)
            WriteRawCSV(sc, pSig, pRxn, InputCSVPath.GetPathAndFileName(), tickSize);
        if (exportRayCSV)
            WriteRayContextCSV(sc, pSig, pRxn, InputRayCSVPath.GetPathAndFileName(), tickSize);

        SCString msg;
        msg.Format("ZTE v4.0: %d signals, %d VP_RAY, %d zones, %d rays accumulated",
                   pSig->SignalCount, pRxn->VPRayCount, pSig->ZoneCount, pRxn->RayCount);
        sc.AddMessageToLog(msg, 0);
    }
}
