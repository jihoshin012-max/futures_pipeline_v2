// STUDY VERSION LOG
// archetype: rotational (rangefade variant)
// @study: Range Fade Rotation Algo
// @type: trading-system
// @summary: Bollinger-style band-fading rotation strategy for NQ.
//           Computes rolling mean +/- sigma bands over a configurable
//           lookback, buys at the inner bottom band and sells at the
//           inner top band, continuously rotating between the two.
//           Outer bands serve as stop levels. Attached orders handle
//           target/stop exits.
//
// How it differs from the base rotational study (rotational-NQ-study.cpp):
//   - Base study uses a fixed point distance (StepDist) from observed
//     price extremes to trigger entries/adds/reversals — purely price-action.
//   - This variant uses statistical bands (rolling sigma) that adapt to
//     volatility, widening in fast markets and narrowing in slow ones.
//   - Simpler position management: no multi-level martingale state machine.
//     Martingale here is optional and only scales base qty after consecutive
//     stops (geometric multiplier), vs the base study's doubling ladder.
//   - Exits delegated to SC attached orders (limit target + stop) rather
//     than managed in custom code.
//   - Includes max daily loss shutoff, time filter, and daily state reset.
//
// Key parameters:
//   Lookback Period    — bars for mean/stddev calculation (default 60)
//   Inner Band Mult    — entry band width in sigma (default 1.25)
//   Outer Band Mult    — stop band width in sigma (default 1.5)
//   Base Qty           — starting position size (default 1)
//   Martingale Mult    — size increase after consecutive stops (default 2x)
//   Max Daily Loss     — dollar loss shutoff (default 0 = off)

#include "sierrachart.h"

SCDLLName("Range Fade Rotation Algo")

/*
    Range-Fade Rotation Algo
    ========================
    Price hits bottom band -> BUY, target = top band.
    Price hits top band -> SELL, target = bottom band.
    Stop = outer band.
    Always reversing between bands.
    Martingale: after a stop, increase size on next entry.
*/

SCSFExport scsf_RangeFadeRotation(SCStudyInterfaceRef sc)
{
    // Subgraphs — only the 4 bands + signals
    SCSubgraphRef Subgraph_InnerTop   = sc.Subgraph[0];
    SCSubgraphRef Subgraph_InnerBot   = sc.Subgraph[1];
    SCSubgraphRef Subgraph_OuterTop   = sc.Subgraph[2];
    SCSubgraphRef Subgraph_OuterBot   = sc.Subgraph[3];
    SCSubgraphRef Subgraph_BuyArrow   = sc.Subgraph[4];
    SCSubgraphRef Subgraph_SellArrow  = sc.Subgraph[5];
    SCSubgraphRef Subgraph_ExitMarker = sc.Subgraph[6];

    // Inputs
    SCInputRef Input_Enabled         = sc.Input[0];
    SCInputRef Input_Period          = sc.Input[1];
    SCInputRef Input_InnerMult       = sc.Input[2];
    SCInputRef Input_OuterMult       = sc.Input[3];
    SCInputRef Input_BaseQty         = sc.Input[4];
    SCInputRef Input_StartTime       = sc.Input[5];
    SCInputRef Input_EndTime         = sc.Input[6];
    SCInputRef Input_FlattenTime     = sc.Input[7];
    SCInputRef Input_UseTimeFilter   = sc.Input[8];
    SCInputRef Input_Martingale      = sc.Input[9];
    SCInputRef Input_MartingaleMult  = sc.Input[10];
    SCInputRef Input_MartingaleMax   = sc.Input[11];
    SCInputRef Input_MaxDailyLoss    = sc.Input[12];

    // Persistent state
    int&   r_ConsecutiveStops = sc.GetPersistentInt(0);
    int&   r_LastTradeDate    = sc.GetPersistentInt(1);
    float& r_DailyPnL        = sc.GetPersistentFloat(0);
    int&   r_DailyLossHit    = sc.GetPersistentInt(2);

    if (sc.SetDefaults)
    {
        sc.GraphName = "Range Fade Rotation Algo";
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

        Input_Enabled.Name = "Algo Enabled";
        Input_Enabled.SetYesNo(0);

        Input_Period.Name = "Lookback Period (bars)";
        Input_Period.SetInt(60);
        Input_Period.SetIntLimits(5, 1000);

        Input_InnerMult.Name = "Inner Band Multiplier";
        Input_InnerMult.SetFloat(1.25f);
        Input_InnerMult.SetFloatLimits(0.1f, 5.0f);

        Input_OuterMult.Name = "Outer Band Multiplier (Stop)";
        Input_OuterMult.SetFloat(1.5f);
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
        Input_Martingale.SetYesNo(0);

        Input_MartingaleMult.Name = "Martingale Multiplier";
        Input_MartingaleMult.SetFloat(2.0f);
        Input_MartingaleMult.SetFloatLimits(1.5f, 5.0f);

        Input_MartingaleMax.Name = "Martingale Max Contracts";
        Input_MartingaleMax.SetInt(8);
        Input_MartingaleMax.SetIntLimits(1, 50);

        Input_MaxDailyLoss.Name = "Max Daily Loss $ (0=off)";
        Input_MaxDailyLoss.SetFloat(0.0f);
        Input_MaxDailyLoss.SetFloatLimits(0.0f, 100000.0f);

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

    // ── Compute bands ──
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

    // Clear signals
    Subgraph_BuyArrow[sc.Index] = 0;
    Subgraph_SellArrow[sc.Index] = 0;
    Subgraph_ExitMarker[sc.Index] = 0;

    if (Input_Enabled.GetYesNo() == 0)
        return;

    if (sc.GetBarHasClosedStatus() == BHCS_BAR_HAS_NOT_CLOSED)
        return;

    // ── Daily reset ──
    int today = sc.BaseDateTimeIn[sc.Index].GetDate();
    if (today != r_LastTradeDate)
    {
        r_LastTradeDate = today;
        r_DailyPnL = 0.0f;
        r_DailyLossHit = 0;
        r_ConsecutiveStops = 0;
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

    // ── Time filter ──
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

    // ── Position ──
    s_SCPositionData PosData;
    sc.GetTradePosition(PosData);
    int posQty = PosData.PositionQuantity;

    // Flatten at flatten time
    if (mustFlatten && posQty != 0)
    {
        sc.FlattenAndCancelAllOrders();
        Subgraph_ExitMarker[sc.Index] = sc.Close[sc.Index];
        r_ConsecutiveStops = 0;
        return;
    }

    if (!canTrade)
        return;

    // ── Martingale qty ──
    int baseQty = Input_BaseQty.GetInt();
    int qty = baseQty;

    if (Input_Martingale.GetYesNo() != 0 && r_ConsecutiveStops > 0)
    {
        float mult = Input_MartingaleMult.GetFloat();
        int maxQty = Input_MartingaleMax.GetInt();
        qty = (int)(baseQty * powf(mult, (float)r_ConsecutiveStops));
        if (qty > maxQty) qty = maxQty;
        if (qty < 1) qty = 1;
    }

    // ── Signals ──
    float high  = sc.High[sc.Index];
    float low   = sc.Low[sc.Index];
    float close = sc.Close[sc.Index];

    bool buySignal  = (low <= innerBot && low > outerBot);
    bool sellSignal = (high >= innerTop && high < outerTop);

    // BUY at bottom band, target = top band, stop = outer bottom
    if (buySignal && posQty <= 0)
    {
        if (posQty < 0)
        {
            sc.FlattenAndCancelAllOrders();
            Subgraph_ExitMarker[sc.Index] = close;
            float pnl = (float)(-posQty) * (PosData.AveragePrice - close)
                        * sc.CurrencyValuePerTick / sc.TickSize;
            r_DailyPnL += pnl;
            if (pnl < 0) r_ConsecutiveStops++;
            else r_ConsecutiveStops = 0;
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
            Subgraph_BuyArrow[sc.Index] = low - stdDev * 0.2f;
    }
    // SELL at top band, target = bottom band, stop = outer top
    else if (sellSignal && posQty >= 0)
    {
        if (posQty > 0)
        {
            sc.FlattenAndCancelAllOrders();
            Subgraph_ExitMarker[sc.Index] = close;
            float pnl = (float)posQty * (close - PosData.AveragePrice)
                        * sc.CurrencyValuePerTick / sc.TickSize;
            r_DailyPnL += pnl;
            if (pnl < 0) r_ConsecutiveStops++;
            else r_ConsecutiveStops = 0;
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
            Subgraph_SellArrow[sc.Index] = high + stdDev * 0.2f;
    }

    // Track stops from attached orders (position went flat without a reversal signal)
    if (posQty == 0 && PosData.LastTradeProfitLoss < 0)
        r_ConsecutiveStops++;
    else if (posQty == 0 && PosData.LastTradeProfitLoss > 0)
        r_ConsecutiveStops = 0;
}
