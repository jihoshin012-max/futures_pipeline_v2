// SpeedRead_V2.cpp
// Sierra Chart ACSIL Study - SpeedRead V2 Market Speed Indicator
// Measures how fast the market is moving using price velocity, volume rate,
// and optionally tick rate. Helps identify dead/slow tape vs active/aggressive tape.
//
// MODIFICATION: Added median normalization for price velocity.
// The ATR normalization compresses values on tick-based charts (e.g., 250-tick).
// Median normalization uses a trailing window of price_travel values as the
// denominator, centering the ratio around 1.0 regardless of bar type.
// This matches the Python pipeline that validated threshold=48.
//
// CSV LOGGING: Enable "CSV Verification Log" input to write all intermediate
// values to SpeedRead_V2_Log.csv in the Sierra Chart Data Files folder.
// Use for verification against Python pipeline, then disable for production.
//
// Subgraph 0: Composite Speed (histogram, color-coded)
//   - Arrays[0]: Raw composite (pre-smoothing), used internally for SMA
//   - Arrays[1]: Price travel per bar, used internally for median calculation
// Subgraph 1: Price Velocity component (line, hidden by default)
// Subgraph 2: Volume Rate component (line, hidden by default)
// Subgraph 3: Slow Zone Threshold (horizontal line)
// Subgraph 4: Fast Zone Threshold (horizontal line)
// Subgraph 5: Roll50 — 50-bar SMA of raw composite (for V1.4 autotrader)

#include "sierrachart.h"
#include <cstdio>

SCDLLName("SpeedRead_V2")

// ------------------------------------------------------------
// Helper: compute median of a float array using insertion sort
// Designed for small arrays (<=500 elements). Sorts in-place.
// ------------------------------------------------------------
static float ComputeMedian(float* arr, int count)
{
    if (count <= 0)
        return 0.0f;

    // Insertion sort — fast enough for N <= 500
    for (int i = 1; i < count; i++)
    {
        float key = arr[i];
        int j = i - 1;
        while (j >= 0 && arr[j] > key)
        {
            arr[j + 1] = arr[j];
            j--;
        }
        arr[j + 1] = key;
    }

    if (count % 2 == 1)
        return arr[count / 2];
    else
        return (arr[count / 2 - 1] + arr[count / 2]) / 2.0f;
}

SCSFExport scsf_SpeedRead_V2(SCStudyInterfaceRef sc)
{
    // Subgraph references
    SCSubgraphRef CompositeSpeed  = sc.Subgraph[0];
    SCSubgraphRef PriceVelocity   = sc.Subgraph[1];
    SCSubgraphRef VolumeRate      = sc.Subgraph[2];
    SCSubgraphRef SlowThreshold   = sc.Subgraph[3];
    SCSubgraphRef FastThreshold   = sc.Subgraph[4];
    SCSubgraphRef Roll50          = sc.Subgraph[5];

    // Input references (0-8 unchanged from original for backward compatibility)
    SCInputRef LookbackBars       = sc.Input[0];
    SCInputRef VolAvgBars         = sc.Input[1];
    SCInputRef PriceWeight        = sc.Input[2];
    SCInputRef VolumeWeight       = sc.Input[3];
    SCInputRef SlowLevel          = sc.Input[4];
    SCInputRef FastLevel          = sc.Input[5];
    SCInputRef SmoothingBars      = sc.Input[6];
    SCInputRef UseATRNormalize    = sc.Input[7];  // Kept for fallback, default OFF
    SCInputRef ATRLength          = sc.Input[8];
    // New inputs for median normalization
    SCInputRef UseMedianNormalize = sc.Input[9];  // NEW — default ON
    SCInputRef MedianWindow       = sc.Input[10]; // NEW — default 200
    // Roll50 period
    SCInputRef Roll50Period       = sc.Input[11]; // NEW — default 50
    // CSV logging for verification
    SCInputRef EnableCSVLog       = sc.Input[12]; // Moved from Input[11]

    if (sc.SetDefaults)
    {
        sc.GraphName = "SpeedRead V2";
        sc.StudyDescription = "SpeedRead V2 - median-normalized composite market speed indicator. Validated for rotation filter (threshold=48, composite < 48 = slow/skip, >= 48 = fast/trade). Enable CSV log for verification.";

        sc.AutoLoop = 1;
        sc.GraphRegion = 1;

        // Composite Speed - main histogram
        CompositeSpeed.Name = "Composite Speed";
        CompositeSpeed.DrawStyle = DRAWSTYLE_BAR;
        CompositeSpeed.PrimaryColor = RGB(0, 180, 255);
        CompositeSpeed.SecondaryColor = RGB(80, 80, 80);
        CompositeSpeed.SecondaryColorUsed = 1;
        CompositeSpeed.LineWidth = 4;
        CompositeSpeed.DrawZeros = 1;

        // Price Velocity - hidden by default
        PriceVelocity.Name = "Price Velocity";
        PriceVelocity.DrawStyle = DRAWSTYLE_LINE;
        PriceVelocity.PrimaryColor = RGB(255, 200, 0);
        PriceVelocity.LineWidth = 1;
        PriceVelocity.DrawZeros = 0;

        // Volume Rate - hidden by default
        VolumeRate.Name = "Volume Rate";
        VolumeRate.DrawStyle = DRAWSTYLE_LINE;
        VolumeRate.PrimaryColor = RGB(0, 255, 150);
        VolumeRate.LineWidth = 1;
        VolumeRate.DrawZeros = 0;

        // Threshold lines
        SlowThreshold.Name = "Slow Threshold";
        SlowThreshold.DrawStyle = DRAWSTYLE_LINE;
        SlowThreshold.PrimaryColor = RGB(120, 120, 120);
        SlowThreshold.LineWidth = 1;
        SlowThreshold.LineStyle = LINESTYLE_DOT;
        SlowThreshold.DrawZeros = 0;

        FastThreshold.Name = "Fast Threshold";
        FastThreshold.DrawStyle = DRAWSTYLE_LINE;
        FastThreshold.PrimaryColor = RGB(255, 80, 80);
        FastThreshold.LineWidth = 1;
        FastThreshold.LineStyle = LINESTYLE_DOT;
        FastThreshold.DrawZeros = 0;

        // Inputs (0-8: original, preserved for backward compatibility)
        LookbackBars.Name = "Price Velocity Lookback (Bars)";
        LookbackBars.SetInt(10);
        LookbackBars.SetIntLimits(2, 200);

        VolAvgBars.Name = "Volume Average Period (Bars)";
        VolAvgBars.SetInt(50);
        VolAvgBars.SetIntLimits(5, 500);

        PriceWeight.Name = "Price Velocity Weight (%)";
        PriceWeight.SetFloat(50.0f);
        PriceWeight.SetFloatLimits(0.0f, 100.0f);

        VolumeWeight.Name = "Volume Rate Weight (%)";
        VolumeWeight.SetFloat(50.0f);
        VolumeWeight.SetFloatLimits(0.0f, 100.0f);

        SlowLevel.Name = "Slow Zone Threshold";
        SlowLevel.SetFloat(30.0f);
        SlowLevel.SetFloatLimits(0.0f, 100.0f);

        FastLevel.Name = "Fast Zone Threshold";
        FastLevel.SetFloat(70.0f);
        FastLevel.SetFloatLimits(0.0f, 100.0f);

        SmoothingBars.Name = "Output Smoothing (Bars, 1=None)";
        SmoothingBars.SetInt(3);
        SmoothingBars.SetIntLimits(1, 20);

        // ATR normalization — default OFF (was ON in original)
        UseATRNormalize.Name = "Normalize with ATR (legacy, use Median instead)";
        UseATRNormalize.SetYesNo(0); // OFF by default now

        ATRLength.Name = "ATR Length (for ATR normalization only)";
        ATRLength.SetInt(20);
        ATRLength.SetIntLimits(5, 200);

        // Median normalization — NEW, default ON
        UseMedianNormalize.Name = "Normalize with Median (recommended for tick bars)";
        UseMedianNormalize.SetYesNo(1); // ON by default

        MedianWindow.Name = "Median Window (Bars)";
        MedianWindow.SetInt(200);
        MedianWindow.SetIntLimits(20, 500);

        // Roll50 — 50-bar SMA of raw composite for V1.4 autotrader
        Roll50.Name = "Roll50";
        Roll50.DrawStyle = DRAWSTYLE_LINE;
        Roll50.PrimaryColor = RGB(255, 255, 0);
        Roll50.LineWidth = 2;
        Roll50.DrawZeros = false;

        Roll50Period.Name = "Roll50 Period (bars)";
        Roll50Period.SetInt(50);
        Roll50Period.SetIntLimits(5, 200);

        // CSV logging — default OFF
        EnableCSVLog.Name = "CSV Verification Log (disable for production)";
        EnableCSVLog.SetYesNo(0); // OFF by default

        return;
    }

    // -- Computation --

    int lookback    = LookbackBars.GetInt();
    int volAvgLen   = VolAvgBars.GetInt();
    int smoothLen   = SmoothingBars.GetInt();
    int atrLen      = ATRLength.GetInt();
    int medianWin   = MedianWindow.GetInt();
    float pWeight   = PriceWeight.GetFloat() / 100.0f;
    float vWeight   = VolumeWeight.GetFloat() / 100.0f;
    bool doLog      = EnableCSVLog.GetBoolean();

    // Need enough bars for calculations
    int minBars = max(lookback, volAvgLen);
    if (UseATRNormalize.GetBoolean() && !UseMedianNormalize.GetBoolean())
        minBars = max(minBars, atrLen);
    if (sc.Index < minBars)
        return;

    // ==============================
    // 1) PRICE VELOCITY
    // ==============================
    // Sum of absolute bar-to-bar price changes over the lookback window
    // This captures total distance traveled, not just net displacement
    float priceTravel = 0.0f;
    for (int i = 0; i < lookback; i++)
    {
        int idx = sc.Index - i;
        int prevIdx = idx - 1;
        if (prevIdx >= 0)
        {
            priceTravel += fabs(sc.Close[idx] - sc.Close[prevIdx]);
        }
    }

    // Store priceTravel for median calculation (Arrays[1])
    sc.Subgraph[0].Arrays[1][sc.Index] = priceTravel;

    // Normalize price velocity
    float priceVelRaw = 0.0f;
    float medianPriceTravel = 0.0f;  // Track for logging

    if (UseMedianNormalize.GetBoolean())
    {
        // ---- MEDIAN NORMALIZATION (recommended for tick-based bars) ----
        // Uses trailing median of priceTravel as denominator.
        // Centers priceVelRaw around 1.0 regardless of bar type or ATR regime.

        // Determine how many bars of priceTravel history we have
        int availableBars = sc.Index - minBars + 1; // bars with valid priceTravel
        int windowSize = min(medianWin, availableBars);

        if (windowSize >= 20) // Need minimum history for stable median
        {
            // Copy trailing priceTravel values into local buffer for sorting
            // Stack allocation — 500 floats = 2KB, safe for ACSIL
            float medianBuf[500];
            int bufCount = min(windowSize, 500);

            for (int i = 0; i < bufCount; i++)
            {
                medianBuf[i] = sc.Subgraph[0].Arrays[1][sc.Index - i];
            }

            medianPriceTravel = ComputeMedian(medianBuf, bufCount);

            if (medianPriceTravel > 0.0f)
            {
                priceVelRaw = priceTravel / medianPriceTravel;
            }
        }
        // else: not enough history yet, priceVelRaw stays 0
    }
    else if (UseATRNormalize.GetBoolean())
    {
        // ---- ATR NORMALIZATION (legacy — compresses on tick bars) ----
        float atrSum = 0.0f;
        for (int i = 0; i < atrLen; i++)
        {
            int idx = sc.Index - i;
            if (idx < 1)
                break;

            float trueRange = sc.High[idx] - sc.Low[idx];
            float highClose = fabs(sc.High[idx] - sc.Close[idx - 1]);
            float lowClose  = fabs(sc.Low[idx] - sc.Close[idx - 1]);

            trueRange = max(trueRange, max(highClose, lowClose));
            atrSum += trueRange;
        }
        float atr = atrSum / (float)atrLen;

        if (atr > 0.0f)
        {
            priceVelRaw = priceTravel / (atr * (float)lookback);
        }
    }
    else
    {
        // ---- RAW (no normalization) ----
        if (sc.TickSize > 0.0f)
            priceVelRaw = priceTravel / sc.TickSize / (float)lookback;
        else
            priceVelRaw = priceTravel;
    }

    // ==============================
    // 2) VOLUME RATE
    // ==============================
    // Average volume EXCLUDES current bar (starts at i-1)
    float volSum = 0.0f;
    for (int i = 1; i <= volAvgLen; i++)
    {
        int idx = sc.Index - i;
        if (idx >= 0)
            volSum += sc.Volume[idx];
    }
    float avgVol = volSum / (float)volAvgLen;

    float volRateRaw = 0.0f;
    float recentVol = 0.0f;  // Track for logging
    if (avgVol > 0.0f)
    {
        // Recent volume INCLUDES current bar
        int recentBars = min(lookback, 5);
        for (int i = 0; i < recentBars; i++)
        {
            int idx = sc.Index - i;
            if (idx >= 0)
                recentVol += sc.Volume[idx];
        }
        recentVol /= (float)recentBars;

        volRateRaw = recentVol / avgVol;
    }

    // ==============================
    // 3) SCALE TO 0-100 RANGE
    // ==============================
    // tanh mapping centered at 1.0: value of 1.0 -> 50, >1 -> toward 100, <1 -> toward 0
    float priceScaled = 50.0f * (1.0f + tanhf((priceVelRaw - 1.0f) * 1.5f));
    float volScaled   = 50.0f * (1.0f + tanhf((volRateRaw  - 1.0f) * 1.5f));

    // Store component values
    PriceVelocity[sc.Index] = priceScaled;
    VolumeRate[sc.Index]    = volScaled;

    // ==============================
    // 4) COMPOSITE
    // ==============================
    float totalWeight = pWeight + vWeight;
    float compositeRaw = 0.0f;
    if (totalWeight > 0.0f)
    {
        compositeRaw = (priceScaled * pWeight + volScaled * vWeight) / totalWeight;
    }

    // ==============================
    // 5) SMOOTHING
    // ==============================
    // Store raw composite in Arrays[0], then SMA smooth
    sc.Subgraph[0].Arrays[0][sc.Index] = compositeRaw;

    float composite = compositeRaw;
    if (smoothLen > 1)
    {
        float smoothSum = 0.0f;
        int count = 0;
        for (int i = 0; i < smoothLen; i++)
        {
            int idx = sc.Index - i;
            if (idx >= 0)
            {
                smoothSum += sc.Subgraph[0].Arrays[0][idx];
                count++;
            }
        }
        if (count > 0)
            composite = smoothSum / (float)count;
    }

    CompositeSpeed[sc.Index] = composite;

    // ==============================
    // 5b) ROLL50 — SMA of raw composite
    // ==============================
    // Uses Arrays[0] (raw composite, pre-smoothing) to match Python pipeline's rolling(50).mean()
    {
        int roll50Len = Roll50Period.GetInt();
        float roll50Sum = 0.0f;
        int roll50Count = 0;
        for (int i = 0; i < roll50Len; i++)
        {
            int idx = sc.Index - i;
            if (idx >= minBars)
            {
                roll50Sum += sc.Subgraph[0].Arrays[0][idx];
                roll50Count++;
            }
        }
        if (roll50Count >= roll50Len)
            Roll50[sc.Index] = roll50Sum / (float)roll50Count;
        else
            Roll50[sc.Index] = 0.0f;  // Not enough data yet
    }

    // ==============================
    // 6) CSV LOGGING (verification only)
    // ==============================
    if (doLog)
    {
        SCString logPath;
        logPath.Format("%s\\SpeedRead_V2_Log.csv", sc.DataFilesFolder().GetChars());

        // Write header on first valid bar (truncates any existing file)
        if (sc.Index == minBars)
        {
            FILE* f = fopen(logPath.GetChars(), "w");
            if (f != NULL)
            {
                fprintf(f, "BarIndex,Date,Time,Close,Volume,"
                           "PriceTravel,MedianPriceTravel,PriceVelRaw,PriceScaled,"
                           "AvgVol,RecentVol,VolRateRaw,VolScaled,"
                           "CompositeRaw,CompositeSmoothed\n");
                fclose(f);
            }
        }

        // Append data row
        FILE* f = fopen(logPath.GetChars(), "a");
        if (f != NULL)
        {
            int year, month, day, hour, minute, second;
            sc.BaseDateTimeIn[sc.Index].GetDateTimeYMDHMS(year, month, day,
                                                           hour, minute, second);

            fprintf(f, "%d,%04d-%02d-%02d,%02d:%02d:%02d,%.2f,%.0f,"
                       "%.4f,%.4f,%.6f,%.4f,"
                       "%.2f,%.2f,%.6f,%.4f,"
                       "%.4f,%.4f\n",
                    sc.Index,
                    year, month, day,
                    hour, minute, second,
                    sc.Close[sc.Index],
                    sc.Volume[sc.Index],
                    priceTravel,
                    medianPriceTravel,
                    priceVelRaw,
                    priceScaled,
                    avgVol,
                    recentVol,
                    volRateRaw,
                    volScaled,
                    compositeRaw,
                    composite);

            fclose(f);
        }
    }

    // ==============================
    // 7) COLOR CODING
    // ==============================
    float slowThresh = SlowLevel.GetFloat();
    float fastThresh = FastLevel.GetFloat();

    if (composite >= fastThresh)
    {
        float intensity = min((composite - fastThresh) / (100.0f - fastThresh), 1.0f);
        int r = 255;
        int g = (int)(80.0f * (1.0f - intensity));
        int b = 0;
        CompositeSpeed.DataColor[sc.Index] = RGB(r, g, b);
    }
    else if (composite <= slowThresh)
    {
        float intensity = min((slowThresh - composite) / slowThresh, 1.0f);
        int gray = (int)(120.0f * (1.0f - intensity * 0.7f));
        CompositeSpeed.DataColor[sc.Index] = RGB(gray, gray, gray);
    }
    else
    {
        float pct = (composite - slowThresh) / (fastThresh - slowThresh);
        int r = (int)(pct * 200.0f);
        int g = (int)(140.0f + pct * 60.0f);
        int b = (int)(255.0f * (1.0f - pct));
        CompositeSpeed.DataColor[sc.Index] = RGB(r, g, b);
    }

    // Threshold lines
    SlowThreshold[sc.Index] = slowThresh;
    FastThreshold[sc.Index] = fastThresh;
}
