#include "sierrachart.h"

SCDLLName("Range Fade Rotation Algo V2")

/*
    Range-Fade Rotation Algo V2
    ===========================
    Price hits bottom band -> BUY, target = top band.
    Price hits top band -> SELL, target = bottom band.
    Stop = outer band.
    Always reversing between bands.
    Martingale: after a stop, increase size on next entry.

    V2 changes:
    - Added "Max Consecutive Stops" input (per-side). When
      consecutive stop-outs on one side (long or short) reach
      this threshold, that side is blocked for the rest of the
      day. The opposite side remains open.
      Example: 3 long stops in a row -> longs blocked, shorts
      still allowed. A winning long resets the long counter.
      Set to 0 to disable.
*/

SCSFExport scsf_RangeFadeRotationV2(SCStudyInterfaceRef sc)
{
    // Subgraphs
    SCSubgraphRef Subgraph_InnerTop   = sc.Subgraph[0];
    SCSubgraphRef Subgraph_InnerBot   = sc.Subgraph[1];
    SCSubgraphRef Subgraph_OuterTop   = sc.Subgraph[2];
    SCSubgraphRef Subgraph_OuterBot   = sc.Subgraph[3];
    SCSubgraphRef Subgraph_BuyArrow   = sc.Subgraph[4];
    SCSubgraphRef Subgraph_SellArrow  = sc.Subgraph[5];
    SCSubgraphRef Subgraph_ExitMarker = sc.Subgraph[6];
    SCSubgraphRef Subgraph_Midline    = sc.Subgraph[7];

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
    SCInputRef Input_StopStepUp       = sc.Input[14];
    SCInputRef Input_StopStepUpTicks  = sc.Input[15];

    // Persistent state
    int&   r_ConsecLongStops  = sc.GetPersistentInt(0);
    int&   r_LastTradeDate    = sc.GetPersistentInt(1);
    int&   r_DailyLossHit    = sc.GetPersistentInt(2);
    int&   r_ConsecShortStops = sc.GetPersistentInt(3);
    int&   r_LastEntryDir     = sc.GetPersistentInt(4);  // 1=long, -1=short
    int&   r_StepUpDone       = sc.GetPersistentInt(5);  // 1=step-up stop active
    float& r_DailyPnL        = sc.GetPersistentFloat(0);
    float& r_EntryPrice       = sc.GetPersistentFloat(1);

    if (sc.SetDefaults)
    {
        sc.GraphName = "Range Fade Rotation Algo V2";
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

    // -- Compute bands --
    int period = Input_Period.GetInt();
    if (sc.Index < period)
        return;

    // Rolling mean and std dev of Close (internal only, not drawn)
    float sum = 0.0f;
    for (int j = sc.Index - period + 1; j <= sc.Index; j++)
        sum += sc.Close[j];
    float mean = sum / period;

    float sumSq = 0.0f;
    for (int j = sc.Index - period + 1; j <= sc.Index; j++)
    {
        float diff = sc.Close[j] - mean;
        sumSq += diff * diff;
    }
    float stdDev = sqrtf(sumSq / period);

    if (stdDev < sc.TickSize)
        return;

    float innerMult = Input_InnerMult.GetFloat();
    float outerMult = Input_OuterMult.GetFloat();

    float innerTop = mean + innerMult * stdDev;
    float innerBot = mean - innerMult * stdDev;
    float outerTop = mean + outerMult * stdDev;
    float outerBot = mean - outerMult * stdDev;

    Subgraph_InnerTop[sc.Index] = innerTop;
    Subgraph_InnerBot[sc.Index] = innerBot;
    Subgraph_OuterTop[sc.Index] = outerTop;
    Subgraph_OuterBot[sc.Index] = outerBot;
    Subgraph_Midline[sc.Index]  = mean;

    // Clear signals
    Subgraph_BuyArrow[sc.Index] = 0;
    Subgraph_SellArrow[sc.Index] = 0;
    Subgraph_ExitMarker[sc.Index] = 0;

    if (Input_Enabled.GetYesNo() == 0)
        return;

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
    }

    // Daily loss limit
    float maxLoss = Input_MaxDailyLoss.GetFloat();
    if (maxLoss > 0.0f && r_DailyPnL <= -maxLoss)
    {
        if (!r_DailyLossHit)
        {
            r_DailyLossHit = 1;
            sc.FlattenAndCancelAllOrders();
        }
        return;
    }

    // Per-side consecutive stop limit
    int maxConsecStops = Input_MaxConsecStops.GetInt();
    bool longBlocked  = (maxConsecStops > 0 && r_ConsecLongStops >= maxConsecStops);
    bool shortBlocked = (maxConsecStops > 0 && r_ConsecShortStops >= maxConsecStops);

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

    // -- Position --
    s_SCPositionData PosData;
    sc.GetTradePosition(PosData);
    int posQty = PosData.PositionQuantity;

    // Flatten at flatten time
    if (mustFlatten && posQty != 0)
    {
        sc.FlattenAndCancelAllOrders();
        Subgraph_ExitMarker[sc.Index] = sc.Close[sc.Index];
        return;
    }

    if (!canTrade)
        return;

    // -- Stop step-up at midline --
    if (Input_StopStepUp.GetYesNo() != 0 && posQty != 0)
    {
        float high  = sc.High[sc.Index];
        float low   = sc.Low[sc.Index];
        float stepUpStop = r_EntryPrice + Input_StopStepUpTicks.GetInt() * sc.TickSize;

        // Check if price reached midline this bar
        if (!r_StepUpDone)
        {
            if (posQty > 0 && high >= mean)
                r_StepUpDone = 1;
            else if (posQty < 0 && low <= mean)
                r_StepUpDone = 1;
        }

        // If step-up active, check if tighter stop is violated
        if (r_StepUpDone)
        {
            if (posQty > 0)
                stepUpStop = r_EntryPrice + Input_StopStepUpTicks.GetInt() * sc.TickSize;
            else
                stepUpStop = r_EntryPrice - Input_StopStepUpTicks.GetInt() * sc.TickSize;

            bool violated = (posQty > 0 && low <= stepUpStop)
                         || (posQty < 0 && high >= stepUpStop);

            if (violated)
            {
                float pnl;
                if (posQty > 0)
                    pnl = (float)posQty * (sc.Close[sc.Index] - r_EntryPrice)
                          * sc.CurrencyValuePerTick / sc.TickSize;
                else
                    pnl = (float)(-posQty) * (r_EntryPrice - sc.Close[sc.Index])
                          * sc.CurrencyValuePerTick / sc.TickSize;

                sc.FlattenAndCancelAllOrders();
                Subgraph_ExitMarker[sc.Index] = sc.Close[sc.Index];
                r_DailyPnL += pnl;

                if (pnl < 0)
                {
                    if (r_LastEntryDir == 1)
                    {
                        r_ConsecLongStops++;
                        if (maxConsecStops > 0 && r_ConsecLongStops >= maxConsecStops)
                            r_ConsecShortStops = 0;
                    }
                    else if (r_LastEntryDir == -1)
                    {
                        r_ConsecShortStops++;
                        if (maxConsecStops > 0 && r_ConsecShortStops >= maxConsecStops)
                            r_ConsecLongStops = 0;
                    }
                }
                else
                {
                    if (r_LastEntryDir == 1)       r_ConsecLongStops = 0;
                    else if (r_LastEntryDir == -1) r_ConsecShortStops = 0;
                }

                r_StepUpDone = 0;
                r_EntryPrice = 0.0f;
                return;
            }
        }
    }

    // -- Martingale qty --
    int baseQty = Input_BaseQty.GetInt();
    int qty = baseQty;

    // Martingale uses the stop count for the side about to be entered
    // (applied per-signal below, not here)

    // -- Signals --
    float high  = sc.High[sc.Index];
    float low   = sc.Low[sc.Index];
    float close = sc.Close[sc.Index];

    bool buySignal  = (low <= innerBot && low > outerBot);
    bool sellSignal = (high >= innerTop && high < outerTop);

    // BUY at bottom band, target = top band, stop = outer bottom
    if (buySignal && posQty <= 0 && !longBlocked)
    {
        if (posQty < 0)
        {
            sc.FlattenAndCancelAllOrders();
            Subgraph_ExitMarker[sc.Index] = close;
            float pnl = (float)(-posQty) * (PosData.AveragePrice - close)
                        * sc.CurrencyValuePerTick / sc.TickSize;
            r_DailyPnL += pnl;
            if (pnl < 0)
            {
                r_ConsecShortStops++;
                if (maxConsecStops > 0 && r_ConsecShortStops >= maxConsecStops)
                    r_ConsecLongStops = 0;
            }
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
        }
    }
    // SELL at top band, target = bottom band, stop = outer top
    else if (sellSignal && posQty >= 0 && !shortBlocked)
    {
        if (posQty > 0)
        {
            sc.FlattenAndCancelAllOrders();
            Subgraph_ExitMarker[sc.Index] = close;
            float pnl = (float)posQty * (close - PosData.AveragePrice)
                        * sc.CurrencyValuePerTick / sc.TickSize;
            r_DailyPnL += pnl;
            if (pnl < 0)
            {
                r_ConsecLongStops++;
                if (maxConsecStops > 0 && r_ConsecLongStops >= maxConsecStops)
                    r_ConsecShortStops = 0;
            }
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
        }
    }

    // Track stops from attached orders (position went flat without a reversal signal)
    if (posQty == 0 && PosData.LastTradeProfitLoss < 0)
    {
        if (r_LastEntryDir == 1)
        {
            r_ConsecLongStops++;
            if (maxConsecStops > 0 && r_ConsecLongStops >= maxConsecStops)
                r_ConsecShortStops = 0;
        }
        else if (r_LastEntryDir == -1)
        {
            r_ConsecShortStops++;
            if (maxConsecStops > 0 && r_ConsecShortStops >= maxConsecStops)
                r_ConsecLongStops = 0;
        }
    }
    else if (posQty == 0 && PosData.LastTradeProfitLoss > 0)
    {
        if (r_LastEntryDir == 1)       r_ConsecLongStops = 0;
        else if (r_LastEntryDir == -1) r_ConsecShortStops = 0;
    }
}
