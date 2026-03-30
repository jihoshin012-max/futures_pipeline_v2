// archetype: rotational
// @study    rotational-NQ-scale-detection-chop
// @version  1.0
// @type     ACSIL indicator study
// @summary  Choppiness ratio filter for 250-tick bars — outputs trade-allowed boolean
// @version: 1.0
// @author: ATEAM
// @type: indicator
// @features: autoloop
// @looping: autoloop
// @complexity: low
// @inputs: Lookback, Chop Threshold, Enable
// @summary: Computes choppiness ratio (|net_move| / summed_range) over a
//           lookback window of bars. Outputs the ratio as a subgraph and
//           a boolean "trade allowed" signal. Designed for 250-tick charts.
//           The rotation strategy reads SG_TradeAllowed via inter-study ref.
//
// Choppiness ratio:
//   chop = abs(Close[i] - Close[i - lookback + 1]) / sum(High[j] - Low[j]) for j in [i-lookback+1..i]
//   Near 0 = rotational (lots of movement, little displacement) -> TRADE
//   Near 1 = trending (movement converts to displacement) -> NO TRADE
//
// Validated: P1 E[R] $3.13 -> $55.41 with chop<0.10 lb=3.
//            P2 E[R] $3.78 -> $54.86 (held out of sample).
//            Sanity check: random filter at same retention shows no improvement.
//            Stress test: threshold stable 0.05-0.15, survives 6t slippage.

#include "sierrachart.h"
#include <cmath>

SCDLLName("ATEAM_ChoppinessFilter")

SCSFExport scsf_ATEAM_ChoppinessFilter(SCStudyInterfaceRef sc)
{
    // --- Input references ---
    SCInputRef InputLookback   = sc.Input[0];
    SCInputRef InputThreshold  = sc.Input[1];
    SCInputRef InputEnable     = sc.Input[2];

    // --- Subgraph references ---
    SCSubgraphRef SG_Choppiness    = sc.Subgraph[0];  // Raw choppiness ratio [0, 1]
    SCSubgraphRef SG_TradeAllowed  = sc.Subgraph[1];  // 1.0 = trade, 0.0 = no trade
    SCSubgraphRef SG_Background    = sc.Subgraph[2];  // Visual: background color when blocked

    // === SET DEFAULTS ===

    if (sc.SetDefaults)
    {
        sc.GraphName = "ATEAM Choppiness Filter";
        sc.StudyDescription =
            "Choppiness ratio: |net_move| / summed_range over lookback bars. "
            "Low choppiness (< threshold) = rotational = trade allowed. "
            "High choppiness = trending = trade blocked. "
            "Output SG_TradeAllowed (subgraph 1) for inter-study reference.";

        sc.AutoLoop = 1;
        sc.GraphRegion = 1;      // Separate region below price
        sc.FreeDLL = 0;

        // --- Inputs ---
        InputLookback.Name = "Lookback (bars)";
        InputLookback.SetInt(3);
        InputLookback.SetIntLimits(2, 50);

        InputThreshold.Name = "Choppiness Threshold";
        InputThreshold.SetFloat(0.10f);
        InputThreshold.SetFloatLimits(0.01f, 1.0f);

        InputEnable.Name = "Enable Filter";
        InputEnable.SetYesNo(1);

        // --- Subgraphs ---
        // Option B: colored bars + dimmed red background wash
        SG_Choppiness.Name = "Choppiness";
        SG_Choppiness.DrawStyle = DRAWSTYLE_BAR;
        SG_Choppiness.PrimaryColor = RGB(34, 197, 94);     // green (below threshold)
        SG_Choppiness.SecondaryColor = RGB(239, 68, 68);   // red (at/above threshold)
        SG_Choppiness.LineWidth = 3;

        SG_TradeAllowed.Name = "Trade Allowed";
        SG_TradeAllowed.DrawStyle = DRAWSTYLE_HIDDEN;
        // Hidden — read by rotation strategy via inter-study reference
        // 1.0 = choppiness below threshold (trade allowed)
        // 0.0 = choppiness at or above threshold (trade blocked)

        SG_Background.Name = "Threshold";
        SG_Background.DrawStyle = DRAWSTYLE_HIDDEN;
        SG_Background.DrawZeros = 0;

        // Threshold reference line
        sc.Subgraph[3].Name = "Threshold Line";
        sc.Subgraph[3].DrawStyle = DRAWSTYLE_LINE;
        sc.Subgraph[3].PrimaryColor = RGB(245, 158, 11);   // amber
        sc.Subgraph[3].LineWidth = 1;
        sc.Subgraph[3].LineStyle = LINESTYLE_DASH;

        return;
    }

    // === COMPUTATION ===

    int lookback = InputLookback.GetInt();
    float threshold = InputThreshold.GetFloat();
    int enabled = InputEnable.GetYesNo();
    int ci = sc.Index;

    // Not enough bars for lookback — need lookback bars [i-lookback+1 .. i]
    if (ci < lookback - 1)
    {
        SG_Choppiness[ci] = 0.0f;
        SG_TradeAllowed[ci] = 1.0f;  // allow during warmup
        SG_Background[ci] = 0.0f;
        return;
    }

    // Disabled — always allow
    if (!enabled)
    {
        SG_Choppiness[ci] = 0.0f;
        SG_TradeAllowed[ci] = 1.0f;
        SG_Background[ci] = 0.0f;
        return;
    }

    // Compute choppiness ratio over lookback bars
    // Python: net_move = abs(close[i] - close[i - lookback + 1])
    // summed_range = sum of (High[j] - Low[j]) for j in [ci - lookback + 1 .. ci]

    float closeNow = sc.Close[ci];
    float closePrev = sc.Close[ci - lookback + 1];
    float netMove = fabs(closeNow - closePrev);

    float summedRange = 0.0f;
    for (int j = ci - lookback + 1; j <= ci; j++)
    {
        summedRange += sc.High[j] - sc.Low[j];
    }

    float chop = 0.0f;
    if (summedRange > 0.0001f)
    {
        chop = netMove / summedRange;
    }

    SG_Choppiness[ci] = chop;

    // Threshold reference line
    sc.Subgraph[3][ci] = threshold;

    // Trade allowed if choppiness < threshold
    if (chop < threshold)
    {
        SG_TradeAllowed[ci] = 1.0f;
        SG_Choppiness.DataColor[ci] = SG_Choppiness.PrimaryColor;  // green bar
    }
    else
    {
        SG_TradeAllowed[ci] = 0.0f;
        SG_Choppiness.DataColor[ci] = SG_Choppiness.SecondaryColor;  // red bar
    }
}
