#include "sierrachart.h"
#include <cstdio>

SCDLLName("Range Fade Rotation Algo V3")

// ── CSV log helper ──
static void LogEvent(
    SCStudyInterfaceRef sc,
    const char* logPath,
    int& headerWritten,
    const char* event,
    int barIndex,
    float high, float low, float close,
    float mean, float innerTop, float innerBot,
    float outerTop, float outerBot,
    int posQty, int lastEntryDir,
    float entryPrice, float stepUpStop,
    int stepUpDone, int stepUpEnabled,
    int consecLong, int consecShort,
    int longBlocked, int shortBlocked,
    float dailyPnL, float tradePnL,
    int qty,
    int buySignal, int sellSignal,
    float maxDailyLoss, int dailyLossHit)
{
    FILE* f = fopen(logPath, "a");
    if (!f) return;

    if (!headerWritten)
    {
        fprintf(f,
            "DateTime,BarIndex,Event,"
            "High,Low,Close,"
            "Mean,InnerTop,InnerBot,OuterTop,OuterBot,"
            "PosQty,LastEntryDir,"
            "EntryPrice,StepUpStop,StepUpDone,StepUpEnabled,"
            "ConsecLongStops,ConsecShortStops,"
            "LongBlocked,ShortBlocked,"
            "DailyPnL,TradePnL,"
            "OrderQty,"
            "BuySignal,SellSignal,"
            "MaxDailyLoss,DailyLossHit\n");
        headerWritten = 1;
    }

    int date = sc.BaseDateTimeIn[barIndex].GetDate();
    int timeVal = sc.BaseDateTimeIn[barIndex].GetTimeInSeconds();
    int yy = date / 10000;
    int mm = (date / 100) % 100;
    int dd = date % 100;
    int hh = timeVal / 3600;
    int mi = (timeVal % 3600) / 60;
    int ss = timeVal % 60;

    fprintf(f,
        "%04d-%02d-%02d %02d:%02d:%02d,%d,%s,"
        "%.2f,%.2f,%.2f,"
        "%.2f,%.2f,%.2f,%.2f,%.2f,"
        "%d,%d,"
        "%.2f,%.2f,%d,%d,"
        "%d,%d,"
        "%d,%d,"
        "%.2f,%.2f,"
        "%d,"
        "%d,%d,"
        "%.2f,%d\n",
        yy, mm, dd, hh, mi, ss, barIndex, event,
        high, low, close,
        mean, innerTop, innerBot, outerTop, outerBot,
        posQty, lastEntryDir,
        entryPrice, stepUpStop, stepUpDone, stepUpEnabled,
        consecLong, consecShort,
        longBlocked, shortBlocked,
        dailyPnL, tradePnL,
        qty,
        buySignal, sellSignal,
        maxDailyLoss, dailyLossHit);

    fclose(f);
}

/*
    Range-Fade Rotation Algo V3
    ===========================
    Price hits bottom band -> BUY, target = top band.
    Price hits top band -> SELL, target = bottom band.
    Stop = outer band.
    Always reversing between bands.
    Martingale: after a stop, increase size on next entry.

    V2 changes:
    - Per-side consecutive stop blocking with alternation.
    - Stop step-up at midline.

    V3 changes:
    - Added sub-band structure (drawn only, no trading logic yet).
      Each zone (inner-to-outer) gets its own replica of the main
      band structure:
        subMid = (inner + outer) / 2  (derived, no input)
        halfWidth = (outer - inner) / 2
        subInner = subMid ± subInnerMult * halfWidth
        subOuter = subMid ± subOuterMult * halfWidth
      At mult=1.0: sub-inner=inner, sub-outer=outer.
    - 2 new inputs: Sub-Inner Band Multiplier, Sub-Outer Band Multiplier.
    - 10 new subgraphs: SubMid Top/Bot, SubInner Top/Bot Upper/Lower,
      SubOuter Top/Bot Upper/Lower.
*/

SCSFExport scsf_RangeFadeRotationV3(SCStudyInterfaceRef sc)
{
    // Subgraphs
    SCSubgraphRef Subgraph_InnerTop      = sc.Subgraph[0];
    SCSubgraphRef Subgraph_InnerBot      = sc.Subgraph[1];
    SCSubgraphRef Subgraph_OuterTop      = sc.Subgraph[2];
    SCSubgraphRef Subgraph_OuterBot      = sc.Subgraph[3];
    SCSubgraphRef Subgraph_BuyArrow      = sc.Subgraph[4];
    SCSubgraphRef Subgraph_SellArrow     = sc.Subgraph[5];
    SCSubgraphRef Subgraph_ExitMarker    = sc.Subgraph[6];
    SCSubgraphRef Subgraph_Midline       = sc.Subgraph[7];
    // Sub-bands: top zone (between Inner Top and Outer Top)
    SCSubgraphRef Subgraph_SubMidTop         = sc.Subgraph[8];
    SCSubgraphRef Subgraph_SubInnerTopUpper  = sc.Subgraph[9];
    SCSubgraphRef Subgraph_SubInnerTopLower  = sc.Subgraph[10];
    SCSubgraphRef Subgraph_SubOuterTopUpper  = sc.Subgraph[11];
    SCSubgraphRef Subgraph_SubOuterTopLower  = sc.Subgraph[12];
    // Sub-bands: bottom zone (between Inner Bot and Outer Bot)
    SCSubgraphRef Subgraph_SubMidBot         = sc.Subgraph[13];
    SCSubgraphRef Subgraph_SubInnerBotUpper  = sc.Subgraph[14];
    SCSubgraphRef Subgraph_SubInnerBotLower  = sc.Subgraph[15];
    SCSubgraphRef Subgraph_SubOuterBotUpper  = sc.Subgraph[16];
    SCSubgraphRef Subgraph_SubOuterBotLower  = sc.Subgraph[17];

    // Inputs
    SCInputRef Input_Enabled           = sc.Input[0];
    SCInputRef Input_Period            = sc.Input[1];
    SCInputRef Input_InnerMult         = sc.Input[2];
    SCInputRef Input_OuterMult         = sc.Input[3];
    SCInputRef Input_BaseQty           = sc.Input[4];
    SCInputRef Input_StartTime         = sc.Input[5];
    SCInputRef Input_EndTime           = sc.Input[6];
    SCInputRef Input_FlattenTime       = sc.Input[7];
    SCInputRef Input_UseTimeFilter     = sc.Input[8];
    SCInputRef Input_Martingale        = sc.Input[9];
    SCInputRef Input_MartingaleMult    = sc.Input[10];
    SCInputRef Input_MartingaleMax     = sc.Input[11];
    SCInputRef Input_MaxDailyLoss      = sc.Input[12];
    SCInputRef Input_MaxConsecStops    = sc.Input[13];
    SCInputRef Input_StopStepUp        = sc.Input[14];
    SCInputRef Input_StopStepUpTicks   = sc.Input[15];
    SCInputRef Input_SubInnerMult      = sc.Input[16];
    SCInputRef Input_SubOuterMult      = sc.Input[17];
    SCInputRef Input_EnableLog         = sc.Input[18];
    SCInputRef Input_LogPath           = sc.Input[19];

    // Persistent state
    int&   r_ConsecLongStops  = sc.GetPersistentInt(0);
    int&   r_LastTradeDate    = sc.GetPersistentInt(1);
    int&   r_DailyLossHit    = sc.GetPersistentInt(2);
    int&   r_ConsecShortStops = sc.GetPersistentInt(3);
    int&   r_LastEntryDir     = sc.GetPersistentInt(4);  // 1=long, -1=short
    int&   r_StepUpDone       = sc.GetPersistentInt(5);  // 1=step-up stop active
    int&   r_HideSubMenuID    = sc.GetPersistentInt(6);
    int&   r_SubBandsHidden   = sc.GetPersistentInt(7);
    int&   r_HideMainMenuID   = sc.GetPersistentInt(8);
    int&   r_MainBandsHidden  = sc.GetPersistentInt(9);
    int&   r_LogHeaderWritten = sc.GetPersistentInt(10);
    int&   r_AttachedLogged   = sc.GetPersistentInt(11);  // 1=already logged flat state
    float& r_DailyPnL        = sc.GetPersistentFloat(0);
    float& r_EntryPrice       = sc.GetPersistentFloat(1);

    if (sc.SetDefaults)
    {
        sc.GraphName = "Range Fade Rotation Algo V3";
        sc.AutoLoop = 1;
        sc.UpdateStartIndex = 0;
        sc.GraphRegion = 0;

        Subgraph_InnerTop.Name = "Inner Top (Sell)";
        Subgraph_InnerTop.DrawStyle = DRAWSTYLE_LINE;
        Subgraph_InnerTop.PrimaryColor = RGB(255, 105, 180);
        Subgraph_InnerTop.LineWidth = 2;

        Subgraph_InnerBot.Name = "Inner Bottom (Buy)";
        Subgraph_InnerBot.DrawStyle = DRAWSTYLE_LINE;
        Subgraph_InnerBot.PrimaryColor = RGB(255, 105, 180);
        Subgraph_InnerBot.LineWidth = 2;

        Subgraph_OuterTop.Name = "Outer Top (Stop)";
        Subgraph_OuterTop.DrawStyle = DRAWSTYLE_LINE;
        Subgraph_OuterTop.PrimaryColor = RGB(255, 0, 102);
        Subgraph_OuterTop.LineWidth = 2;
        Subgraph_OuterTop.LineStyle = LINESTYLE_DASH;

        Subgraph_OuterBot.Name = "Outer Bottom (Stop)";
        Subgraph_OuterBot.DrawStyle = DRAWSTYLE_LINE;
        Subgraph_OuterBot.PrimaryColor = RGB(255, 0, 102);
        Subgraph_OuterBot.LineWidth = 2;
        Subgraph_OuterBot.LineStyle = LINESTYLE_DASH;

        Subgraph_BuyArrow.Name = "Buy";
        Subgraph_BuyArrow.DrawStyle = DRAWSTYLE_ARROWUP;
        Subgraph_BuyArrow.PrimaryColor = RGB(0, 255, 136);
        Subgraph_BuyArrow.LineWidth = 3;

        Subgraph_SellArrow.Name = "Sell";
        Subgraph_SellArrow.DrawStyle = DRAWSTYLE_ARROWDOWN;
        Subgraph_SellArrow.PrimaryColor = RGB(255, 136, 0);
        Subgraph_SellArrow.LineWidth = 3;

        Subgraph_ExitMarker.Name = "Exit";
        Subgraph_ExitMarker.DrawStyle = DRAWSTYLE_DIAMOND;
        Subgraph_ExitMarker.PrimaryColor = RGB(255, 255, 255);
        Subgraph_ExitMarker.LineWidth = 2;

        Subgraph_Midline.Name = "Midline";
        Subgraph_Midline.DrawStyle = DRAWSTYLE_LINE;
        Subgraph_Midline.PrimaryColor = RGB(128, 128, 128);
        Subgraph_Midline.LineWidth = 1;
        Subgraph_Midline.LineStyle = LINESTYLE_DOT;

        // Sub-bands: top zone
        Subgraph_SubMidTop.Name = "Sub Mid Top";
        Subgraph_SubMidTop.DrawStyle = DRAWSTYLE_LINE;
        Subgraph_SubMidTop.PrimaryColor = RGB(128, 128, 128);
        Subgraph_SubMidTop.LineWidth = 1;
        Subgraph_SubMidTop.LineStyle = LINESTYLE_DOT;

        Subgraph_SubInnerTopUpper.Name = "Sub Inner Top Upper";
        Subgraph_SubInnerTopUpper.DrawStyle = DRAWSTYLE_LINE;
        Subgraph_SubInnerTopUpper.PrimaryColor = RGB(255, 170, 0);
        Subgraph_SubInnerTopUpper.LineWidth = 1;
        Subgraph_SubInnerTopUpper.LineStyle = LINESTYLE_DASH;

        Subgraph_SubInnerTopLower.Name = "Sub Inner Top Lower";
        Subgraph_SubInnerTopLower.DrawStyle = DRAWSTYLE_LINE;
        Subgraph_SubInnerTopLower.PrimaryColor = RGB(255, 170, 0);
        Subgraph_SubInnerTopLower.LineWidth = 1;
        Subgraph_SubInnerTopLower.LineStyle = LINESTYLE_DASH;

        Subgraph_SubOuterTopUpper.Name = "Sub Outer Top Upper";
        Subgraph_SubOuterTopUpper.DrawStyle = DRAWSTYLE_LINE;
        Subgraph_SubOuterTopUpper.PrimaryColor = RGB(255, 170, 0);
        Subgraph_SubOuterTopUpper.LineWidth = 1;

        Subgraph_SubOuterTopLower.Name = "Sub Outer Top Lower";
        Subgraph_SubOuterTopLower.DrawStyle = DRAWSTYLE_LINE;
        Subgraph_SubOuterTopLower.PrimaryColor = RGB(255, 170, 0);
        Subgraph_SubOuterTopLower.LineWidth = 1;

        // Sub-bands: bottom zone
        Subgraph_SubMidBot.Name = "Sub Mid Bot";
        Subgraph_SubMidBot.DrawStyle = DRAWSTYLE_LINE;
        Subgraph_SubMidBot.PrimaryColor = RGB(128, 128, 128);
        Subgraph_SubMidBot.LineWidth = 1;
        Subgraph_SubMidBot.LineStyle = LINESTYLE_DOT;

        Subgraph_SubInnerBotUpper.Name = "Sub Inner Bot Upper";
        Subgraph_SubInnerBotUpper.DrawStyle = DRAWSTYLE_LINE;
        Subgraph_SubInnerBotUpper.PrimaryColor = RGB(255, 170, 0);
        Subgraph_SubInnerBotUpper.LineWidth = 1;
        Subgraph_SubInnerBotUpper.LineStyle = LINESTYLE_DASH;

        Subgraph_SubInnerBotLower.Name = "Sub Inner Bot Lower";
        Subgraph_SubInnerBotLower.DrawStyle = DRAWSTYLE_LINE;
        Subgraph_SubInnerBotLower.PrimaryColor = RGB(255, 170, 0);
        Subgraph_SubInnerBotLower.LineWidth = 1;
        Subgraph_SubInnerBotLower.LineStyle = LINESTYLE_DASH;

        Subgraph_SubOuterBotUpper.Name = "Sub Outer Bot Upper";
        Subgraph_SubOuterBotUpper.DrawStyle = DRAWSTYLE_LINE;
        Subgraph_SubOuterBotUpper.PrimaryColor = RGB(255, 170, 0);
        Subgraph_SubOuterBotUpper.LineWidth = 1;

        Subgraph_SubOuterBotLower.Name = "Sub Outer Bot Lower";
        Subgraph_SubOuterBotLower.DrawStyle = DRAWSTYLE_LINE;
        Subgraph_SubOuterBotLower.PrimaryColor = RGB(255, 170, 0);
        Subgraph_SubOuterBotLower.LineWidth = 1;

        Input_Enabled.Name = "Algo Enabled";
        Input_Enabled.SetYesNo(0);

        Input_Period.Name = "Lookback Period (bars)";
        Input_Period.SetInt(50);
        Input_Period.SetIntLimits(5, 1000);

        Input_InnerMult.Name = "Inner Band Multiplier";
        Input_InnerMult.SetFloat(1.0f);
        Input_InnerMult.SetFloatLimits(0.1f, 5.0f);

        Input_OuterMult.Name = "Outer Band Multiplier (Stop)";
        Input_OuterMult.SetFloat(2.0f);
        Input_OuterMult.SetFloatLimits(0.2f, 6.0f);

        Input_BaseQty.Name = "Base Quantity";
        Input_BaseQty.SetInt(1);
        Input_BaseQty.SetIntLimits(1, 100);

        Input_StartTime.Name = "Start Time";
        Input_StartTime.SetTime(HMS_TIME(9, 30, 0));

        Input_EndTime.Name = "End Time (no new entries)";
        Input_EndTime.SetTime(HMS_TIME(15, 45, 0));

        Input_FlattenTime.Name = "Flatten Time";
        Input_FlattenTime.SetTime(HMS_TIME(15, 55, 0));

        Input_UseTimeFilter.Name = "Use Time Filter";
        Input_UseTimeFilter.SetYesNo(1);

        Input_Martingale.Name = "Martingale Enabled";
        Input_Martingale.SetYesNo(1);

        Input_MartingaleMult.Name = "Martingale Multiplier";
        Input_MartingaleMult.SetFloat(1.5f);
        Input_MartingaleMult.SetFloatLimits(1.5f, 5.0f);

        Input_MartingaleMax.Name = "Martingale Max Contracts";
        Input_MartingaleMax.SetInt(2);
        Input_MartingaleMax.SetIntLimits(1, 50);

        Input_MaxDailyLoss.Name = "Max Daily Loss $ (0=off)";
        Input_MaxDailyLoss.SetFloat(0.0f);
        Input_MaxDailyLoss.SetFloatLimits(0.0f, 100000.0f);

        Input_MaxConsecStops.Name = "Max Consecutive Stops (0=off)";
        Input_MaxConsecStops.SetInt(3);
        Input_MaxConsecStops.SetIntLimits(0, 50);

        Input_StopStepUp.Name = "Stop Step-Up at Midline";
        Input_StopStepUp.SetYesNo(0);

        Input_StopStepUpTicks.Name = "Step-Up Offset (ticks from entry)";
        Input_StopStepUpTicks.SetInt(-40);
        Input_StopStepUpTicks.SetIntLimits(-500, 500);

        Input_SubInnerMult.Name = "Sub-Inner Band Multiplier";
        Input_SubInnerMult.SetFloat(1.0f);
        Input_SubInnerMult.SetFloatLimits(0.0f, 5.0f);

        Input_SubOuterMult.Name = "Sub-Outer Band Multiplier";
        Input_SubOuterMult.SetFloat(1.25f);
        Input_SubOuterMult.SetFloatLimits(0.0f, 5.0f);

        Input_EnableLog.Name = "Enable CSV Log";
        Input_EnableLog.SetYesNo(0);

        Input_LogPath.Name = "Log File Path";
        Input_LogPath.SetPathAndFileName("C:\\SierraChart\\Data\\rangefade-v3-log.csv");

        sc.AllowMultipleEntriesInSameDirection = 0;
        sc.MaximumPositionAllowed = 50;
        sc.SupportReversals = 1;
        sc.AllowOppositeEntryWithOpposingPositionOrOrders = 1;
        sc.SupportAttachedOrdersForTrading = 0;
        sc.CancelAllOrdersOnEntriesAndReversals = 1;
        sc.AllowOnlyOneTradePerBar = 1;
        sc.MaintainTradeStatisticsAndTradesData = 1;

        return;
    }

    // -- Cleanup on study removal --
    if (sc.LastCallToFunction)
    {
        if (r_HideMainMenuID != 0)
            sc.RemoveACSChartShortcutMenuItem(sc.ChartNumber, r_HideMainMenuID);
        if (r_HideSubMenuID != 0)
            sc.RemoveACSChartShortcutMenuItem(sc.ChartNumber, r_HideSubMenuID);
        return;
    }

    // -- Register right-click menus on first run --
    if (r_HideMainMenuID == 0)
    {
        r_HideMainMenuID = sc.AddACSChartShortcutMenuItem(sc.ChartNumber, "Hide Main Bands");
        r_MainBandsHidden = 0;
    }
    if (r_HideSubMenuID == 0)
    {
        r_HideSubMenuID = sc.AddACSChartShortcutMenuItem(sc.ChartNumber, "Hide Sub-Bands");
        r_SubBandsHidden = 0;
    }

    // -- Handle menu events --
    if (sc.MenuEventID != 0 && sc.MenuEventID == r_HideMainMenuID)
    {
        r_MainBandsHidden = !r_MainBandsHidden;

        if (r_MainBandsHidden)
        {
            Subgraph_InnerTop.DrawStyle  = DRAWSTYLE_HIDDEN;
            Subgraph_InnerBot.DrawStyle  = DRAWSTYLE_HIDDEN;
            Subgraph_OuterTop.DrawStyle  = DRAWSTYLE_HIDDEN;
            Subgraph_OuterBot.DrawStyle  = DRAWSTYLE_HIDDEN;
            Subgraph_Midline.DrawStyle   = DRAWSTYLE_HIDDEN;
            sc.ChangeACSChartShortcutMenuItemText(sc.ChartNumber, r_HideMainMenuID, "Show Main Bands");
        }
        else
        {
            Subgraph_InnerTop.DrawStyle  = DRAWSTYLE_LINE;
            Subgraph_InnerBot.DrawStyle  = DRAWSTYLE_LINE;
            Subgraph_OuterTop.DrawStyle  = DRAWSTYLE_LINE;
            Subgraph_OuterBot.DrawStyle  = DRAWSTYLE_LINE;
            Subgraph_Midline.DrawStyle   = DRAWSTYLE_LINE;
            sc.ChangeACSChartShortcutMenuItemText(sc.ChartNumber, r_HideMainMenuID, "Hide Main Bands");
        }
    }

    if (sc.MenuEventID != 0 && sc.MenuEventID == r_HideSubMenuID)
    {
        r_SubBandsHidden = !r_SubBandsHidden;

        if (r_SubBandsHidden)
        {
            Subgraph_SubMidTop.DrawStyle        = DRAWSTYLE_HIDDEN;
            Subgraph_SubInnerTopUpper.DrawStyle  = DRAWSTYLE_HIDDEN;
            Subgraph_SubInnerTopLower.DrawStyle  = DRAWSTYLE_HIDDEN;
            Subgraph_SubOuterTopUpper.DrawStyle  = DRAWSTYLE_HIDDEN;
            Subgraph_SubOuterTopLower.DrawStyle  = DRAWSTYLE_HIDDEN;
            Subgraph_SubMidBot.DrawStyle         = DRAWSTYLE_HIDDEN;
            Subgraph_SubInnerBotUpper.DrawStyle  = DRAWSTYLE_HIDDEN;
            Subgraph_SubInnerBotLower.DrawStyle  = DRAWSTYLE_HIDDEN;
            Subgraph_SubOuterBotUpper.DrawStyle  = DRAWSTYLE_HIDDEN;
            Subgraph_SubOuterBotLower.DrawStyle  = DRAWSTYLE_HIDDEN;
            sc.ChangeACSChartShortcutMenuItemText(sc.ChartNumber, r_HideSubMenuID, "Show Sub-Bands");
        }
        else
        {
            Subgraph_SubMidTop.DrawStyle         = DRAWSTYLE_LINE;
            Subgraph_SubInnerTopUpper.DrawStyle   = DRAWSTYLE_LINE;
            Subgraph_SubInnerTopLower.DrawStyle   = DRAWSTYLE_LINE;
            Subgraph_SubOuterTopUpper.DrawStyle   = DRAWSTYLE_LINE;
            Subgraph_SubOuterTopLower.DrawStyle   = DRAWSTYLE_LINE;
            Subgraph_SubMidBot.DrawStyle          = DRAWSTYLE_LINE;
            Subgraph_SubInnerBotUpper.DrawStyle   = DRAWSTYLE_LINE;
            Subgraph_SubInnerBotLower.DrawStyle   = DRAWSTYLE_LINE;
            Subgraph_SubOuterBotUpper.DrawStyle   = DRAWSTYLE_LINE;
            Subgraph_SubOuterBotLower.DrawStyle   = DRAWSTYLE_LINE;
            sc.ChangeACSChartShortcutMenuItemText(sc.ChartNumber, r_HideSubMenuID, "Hide Sub-Bands");
        }
    }

    // Declare variables early so LOG_EVENT macro can reference them
    float mean = 0, innerTop = 0, innerBot = 0, outerTop = 0, outerBot = 0;
    int posQty = 0;
    bool longBlocked = false, shortBlocked = false;
    float stepUpStopLog = 0;
    int maxConsecStops = 0;

    // -- Compute bands --
    int period = Input_Period.GetInt();
    if (sc.Index < period)
        return;

    // Rolling mean and std dev of Close (internal only, not drawn)
    float sum = 0.0f;
    for (int j = sc.Index - period + 1; j <= sc.Index; j++)
        sum += sc.Close[j];
    mean = sum / period;

    float sumSq = 0.0f;
    for (int j = sc.Index - period + 1; j <= sc.Index; j++)
    {
        float diff = sc.Close[j] - mean;
        sumSq += diff * diff;
    }
    float stdDev = sqrtf(sumSq / period);

    if (stdDev < sc.TickSize)
        return;

    float innerMult    = Input_InnerMult.GetFloat();
    float outerMult    = Input_OuterMult.GetFloat();
    float subInnerMult = Input_SubInnerMult.GetFloat();
    float subOuterMult = Input_SubOuterMult.GetFloat();

    innerTop    = mean + innerMult * stdDev;
    innerBot    = mean - innerMult * stdDev;
    outerTop    = mean + outerMult * stdDev;
    outerBot    = mean - outerMult * stdDev;

    // Sub-midline = midpoint of inner and outer (derived, no input)
    float halfWidthTop = (outerTop - innerTop) / 2.0f;
    float halfWidthBot = (innerBot - outerBot) / 2.0f;
    float subMidTop    = innerTop + halfWidthTop;
    float subMidBot    = innerBot - halfWidthBot;

    // Sub-bands: 4 lines per zone, mirroring main band structure
    // At mult=1.0: sub-inner matches inner/outer, sub-outer matches inner/outer
    // Top zone
    float subInnerTopUpper = subMidTop + subInnerMult * halfWidthTop;
    float subInnerTopLower = subMidTop - subInnerMult * halfWidthTop;
    float subOuterTopUpper = subMidTop + subOuterMult * halfWidthTop;
    float subOuterTopLower = subMidTop - subOuterMult * halfWidthTop;
    // Bottom zone
    float subInnerBotUpper = subMidBot + subInnerMult * halfWidthBot;
    float subInnerBotLower = subMidBot - subInnerMult * halfWidthBot;
    float subOuterBotUpper = subMidBot + subOuterMult * halfWidthBot;
    float subOuterBotLower = subMidBot - subOuterMult * halfWidthBot;

    Subgraph_InnerTop[sc.Index]    = innerTop;
    Subgraph_InnerBot[sc.Index]    = innerBot;
    Subgraph_OuterTop[sc.Index]    = outerTop;
    Subgraph_OuterBot[sc.Index]    = outerBot;
    Subgraph_Midline[sc.Index]     = mean;
    // Sub-bands: always compute values, toggle visibility via DrawStyle
    Subgraph_SubMidTop[sc.Index]        = subMidTop;
    Subgraph_SubInnerTopUpper[sc.Index] = subInnerTopUpper;
    Subgraph_SubInnerTopLower[sc.Index] = subInnerTopLower;
    Subgraph_SubOuterTopUpper[sc.Index] = subOuterTopUpper;
    Subgraph_SubOuterTopLower[sc.Index] = subOuterTopLower;
    Subgraph_SubMidBot[sc.Index]        = subMidBot;
    Subgraph_SubInnerBotUpper[sc.Index] = subInnerBotUpper;
    Subgraph_SubInnerBotLower[sc.Index] = subInnerBotLower;
    Subgraph_SubOuterBotUpper[sc.Index] = subOuterBotUpper;
    Subgraph_SubOuterBotLower[sc.Index] = subOuterBotLower;

    // Clear signals
    Subgraph_BuyArrow[sc.Index] = 0;
    Subgraph_SellArrow[sc.Index] = 0;
    Subgraph_ExitMarker[sc.Index] = 0;

    if (Input_Enabled.GetYesNo() == 0)
        return;

    // -- Logging setup (before bar-close gate so step-up can log) --
    bool doLog = (Input_EnableLog.GetYesNo() != 0);
    const char* logPath = Input_LogPath.GetPathAndFileName();
    float _logHigh  = sc.High[sc.Index];
    float _logLow   = sc.Low[sc.Index];
    float _logClose = sc.Close[sc.Index];

    #define LOG_EVENT(evt, pnlVal, qtyVal, buySig, sellSig) \
        if (doLog) LogEvent(sc, logPath, r_LogHeaderWritten, evt, \
            sc.Index, _logHigh, _logLow, _logClose, \
            mean, innerTop, innerBot, outerTop, outerBot, \
            posQty, r_LastEntryDir, \
            r_EntryPrice, stepUpStopLog, r_StepUpDone, \
            Input_StopStepUp.GetYesNo(), \
            r_ConsecLongStops, r_ConsecShortStops, \
            (int)longBlocked, (int)shortBlocked, \
            r_DailyPnL, pnlVal, qtyVal, buySig, sellSig, \
            Input_MaxDailyLoss.GetFloat(), r_DailyLossHit)

    // -- Position (read before bar-close gate for real-time step-up) --
    {
        s_SCPositionData EarlyPosData;
        sc.GetTradePosition(EarlyPosData);
        posQty = EarlyPosData.PositionQuantity;
    }

    // -- Real-time stop step-up at midline (runs on every tick) --
    // When price reaches midline, modify the working attached stop order
    // to the step-up level. SC's order system handles execution from there.
    if (Input_StopStepUp.GetYesNo() != 0 && posQty != 0 && !r_StepUpDone)
    {
        float rtHigh = sc.High[sc.Index];
        float rtLow  = sc.Low[sc.Index];
        bool midlineTouched = (posQty > 0 && rtHigh >= mean)
                           || (posQty < 0 && rtLow <= mean);

        if (midlineTouched)
        {
            float stepUpStop;
            if (posQty > 0)
                stepUpStop = r_EntryPrice + Input_StopStepUpTicks.GetInt() * sc.TickSize;
            else
                stepUpStop = r_EntryPrice - Input_StopStepUpTicks.GetInt() * sc.TickSize;

            // Find and modify the working attached stop order
            int orderIdx = 0;
            s_SCTradeOrder orderInfo;
            while (sc.GetOrderByIndex(orderIdx, orderInfo) != SCTRADING_ORDER_ERROR)
            {
                orderIdx++;
                if (!IsWorkingOrderStatus(orderInfo.OrderStatusCode))
                    continue;
                if (orderInfo.ParentInternalOrderID == 0)
                    continue;

                // Only modify stop orders, not target (limit) orders.
                // Both stop and target are attached orders on the same side,
                // so we must check the order type to distinguish them.
                if (orderInfo.OrderTypeAsInt != SCT_ORDERTYPE_STOP)
                    continue;

                s_SCNewOrder modOrder;
                modOrder.InternalOrderID = orderInfo.InternalOrderID;
                modOrder.Price1 = stepUpStop;
                sc.ModifyOrder(modOrder);
                break;
            }

            r_StepUpDone = 1;
            stepUpStopLog = stepUpStop;
            LOG_EVENT("STEPUP_ARMED", 0.0f, 0, 0, 0);
        }
    }

    // -- Bar close gate: everything below only runs on closed bars --
    if (sc.GetBarHasClosedStatus() == BHCS_BAR_HAS_NOT_CLOSED)
        return;

    // -- Daily reset --
    int today = sc.BaseDateTimeIn[sc.Index].GetDate();
    if (today != r_LastTradeDate)
    {
        r_LastTradeDate = today;
        r_DailyPnL = 0.0f;
        r_DailyLossHit = 0;
        r_ConsecLongStops = 0;
        r_ConsecShortStops = 0;
        r_LastEntryDir = 0;
        r_StepUpDone = 0;
        r_EntryPrice = 0.0f;
        r_AttachedLogged = 1;  // prevent stale LastTradeProfitLoss from previous day
    }

    // Daily loss limit
    float maxLoss = Input_MaxDailyLoss.GetFloat();
    if (maxLoss > 0.0f && r_DailyPnL <= -maxLoss)
    {
        if (!r_DailyLossHit)
        {
            r_DailyLossHit = 1;
            sc.FlattenAndCancelAllOrders();
            LOG_EVENT("DAILY_LOSS_HIT", 0.0f, 0, 0, 0);
        }
        return;
    }

    // Per-side consecutive stop limit
    maxConsecStops = Input_MaxConsecStops.GetInt();
    longBlocked  = (maxConsecStops > 0 && r_ConsecLongStops >= maxConsecStops);
    shortBlocked = (maxConsecStops > 0 && r_ConsecShortStops >= maxConsecStops);

    // -- Time filter --
    int barTime = sc.BaseDateTimeIn[sc.Index].GetTimeInSeconds();
    int startTime = Input_StartTime.GetTime();
    int endTime = Input_EndTime.GetTime();
    int flattenTime = Input_FlattenTime.GetTime();
    bool useTime = (Input_UseTimeFilter.GetYesNo() != 0);

    bool canTrade = true;
    bool mustFlatten = false;

    if (useTime)
    {
        if (startTime < endTime)
            canTrade = (barTime >= startTime && barTime <= endTime);
        else
            canTrade = (barTime >= startTime || barTime <= endTime);

        mustFlatten = (barTime >= flattenTime && barTime < flattenTime + 600);
    }

    // -- Re-read position for bar-close logic --
    s_SCPositionData PosData;
    sc.GetTradePosition(PosData);
    posQty = PosData.PositionQuantity;

    // Flatten at flatten time
    if (mustFlatten && posQty != 0)
    {
        LOG_EVENT("EXIT_FLATTEN", 0.0f, 0, 0, 0);
        sc.FlattenAndCancelAllOrders();
        Subgraph_ExitMarker[sc.Index] = sc.Close[sc.Index];
        return;
    }

    if (!canTrade)
        return;

    // -- Martingale qty --
    int baseQty = Input_BaseQty.GetInt();
    int qty = baseQty;

    // Martingale uses the stop count for the side about to be entered
    // (applied per-signal below, not here)

    // -- Track attached order results BEFORE signal logic --
    // This ensures stops are counted before a new entry can fire on the same bar.
    float high  = sc.High[sc.Index];
    float low   = sc.Low[sc.Index];
    float close = sc.Close[sc.Index];

    if (posQty == 0 && PosData.LastTradeProfitLoss < 0 && !r_AttachedLogged)
    {
        r_AttachedLogged = 1;
        r_DailyPnL += PosData.LastTradeProfitLoss;
        LOG_EVENT("ATTACHED_STOP", PosData.LastTradeProfitLoss, 0, 0, 0);
        if (r_LastEntryDir == 1)       r_ConsecLongStops++;
        else if (r_LastEntryDir == -1) r_ConsecShortStops++;
        LOG_EVENT("SIDE_STATE", 0.0f, 0, 0, 0);
    }
    else if (posQty == 0 && PosData.LastTradeProfitLoss > 0 && !r_AttachedLogged)
    {
        r_AttachedLogged = 1;
        r_DailyPnL += PosData.LastTradeProfitLoss;
        LOG_EVENT("ATTACHED_TARGET", PosData.LastTradeProfitLoss, 0, 0, 0);
        if (r_LastEntryDir == 1)       r_ConsecLongStops = 0;
        else if (r_LastEntryDir == -1) r_ConsecShortStops = 0;
    }

    // Recompute blocked flags after attached order tracking
    longBlocked  = (maxConsecStops > 0 && r_ConsecLongStops >= maxConsecStops);
    shortBlocked = (maxConsecStops > 0 && r_ConsecShortStops >= maxConsecStops);

    // -- Signals --
    bool buySignal  = (low <= innerBot && low > outerBot);
    bool sellSignal = (high >= innerTop && high < outerTop);

    // BUY at bottom band, target = top band, stop = outer bottom
    if (buySignal && posQty <= 0 && !longBlocked)
    {
        if (posQty < 0)
        {
            float pnl = (float)(-posQty) * (PosData.AveragePrice - close)
                        * sc.CurrencyValuePerTick / sc.TickSize;
            LOG_EVENT("EXIT_REVERSAL_SHORT", pnl, 0, (int)buySignal, (int)sellSignal);
            sc.FlattenAndCancelAllOrders();
            Subgraph_ExitMarker[sc.Index] = close;
            r_DailyPnL += pnl;
            if (pnl < 0) r_ConsecShortStops++;
            else r_ConsecShortStops = 0;
        }

        // Martingale for long side
        if (Input_Martingale.GetYesNo() != 0 && r_ConsecLongStops > 0)
        {
            float mult = Input_MartingaleMult.GetFloat();
            int maxQty = Input_MartingaleMax.GetInt();
            qty = (int)(baseQty * powf(mult, (float)r_ConsecLongStops));
            if (qty > maxQty) qty = maxQty;
            if (qty < 1) qty = 1;
        }

        s_SCNewOrder Order;
        Order.OrderQuantity = qty;
        Order.OrderType = SCT_ORDERTYPE_MARKET;
        Order.TimeInForce = SCT_TIF_GTC;

        // Target = top inner band, Stop = outer bottom band
        Order.AttachedOrderTarget1Type = SCT_ORDERTYPE_LIMIT;
        Order.Target1Offset = innerTop - innerBot;
        Order.AttachedOrderStop1Type = SCT_ORDERTYPE_STOP;
        Order.Stop1Offset = innerBot - outerBot;

        int result = static_cast<int>(sc.BuyEntry(Order));
        if (result > 0)
        {
            Subgraph_BuyArrow[sc.Index] = low - stdDev * 0.2f;
            r_LastEntryDir = 1;
            r_EntryPrice = close;
            r_StepUpDone = 0;
            r_AttachedLogged = 0;

            // If shorts were blocked, this long is the forced opposite-side trade.
            // Reset both counters — circuit breaker complete.
            if (shortBlocked)
            {
                r_ConsecLongStops = 0;
                r_ConsecShortStops = 0;
            }
            LOG_EVENT("ENTRY_LONG", 0.0f, qty, (int)buySignal, (int)sellSignal);
        }
    }
    // SELL at top band, target = bottom band, stop = outer top
    else if (sellSignal && posQty >= 0 && !shortBlocked)
    {
        if (posQty > 0)
        {
            float pnl = (float)posQty * (close - PosData.AveragePrice)
                        * sc.CurrencyValuePerTick / sc.TickSize;
            LOG_EVENT("EXIT_REVERSAL_LONG", pnl, 0, (int)buySignal, (int)sellSignal);
            sc.FlattenAndCancelAllOrders();
            Subgraph_ExitMarker[sc.Index] = close;
            r_DailyPnL += pnl;
            if (pnl < 0) r_ConsecLongStops++;
            else r_ConsecLongStops = 0;
        }

        // Martingale for short side
        if (Input_Martingale.GetYesNo() != 0 && r_ConsecShortStops > 0)
        {
            float mult = Input_MartingaleMult.GetFloat();
            int maxQty = Input_MartingaleMax.GetInt();
            qty = (int)(baseQty * powf(mult, (float)r_ConsecShortStops));
            if (qty > maxQty) qty = maxQty;
            if (qty < 1) qty = 1;
        }

        s_SCNewOrder Order;
        Order.OrderQuantity = qty;
        Order.OrderType = SCT_ORDERTYPE_MARKET;
        Order.TimeInForce = SCT_TIF_GTC;

        // Target = bottom inner band, Stop = outer top band
        Order.AttachedOrderTarget1Type = SCT_ORDERTYPE_LIMIT;
        Order.Target1Offset = innerTop - innerBot;
        Order.AttachedOrderStop1Type = SCT_ORDERTYPE_STOP;
        Order.Stop1Offset = outerTop - innerTop;

        int result = static_cast<int>(sc.SellEntry(Order));
        if (result > 0)
        {
            Subgraph_SellArrow[sc.Index] = high + stdDev * 0.2f;
            r_LastEntryDir = -1;
            r_EntryPrice = close;
            r_StepUpDone = 0;
            r_AttachedLogged = 0;

            // If longs were blocked, this short is the forced opposite-side trade.
            // Reset both counters — circuit breaker complete.
            if (longBlocked)
            {
                r_ConsecLongStops = 0;
                r_ConsecShortStops = 0;
            }
            LOG_EVENT("ENTRY_SHORT", 0.0f, qty, (int)buySignal, (int)sellSignal);
        }
    }

    #undef LOG_EVENT
}
