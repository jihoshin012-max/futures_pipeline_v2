#include "sierrachart.h"

SCDLLName("Initial Balance Box")

/*
    Initial Balance Box Study
    =========================
    Draws a box for the initial balance period (e.g., 09:30-10:30)
    using the high/low of that period. At the end of the IB period,
    horizontal rays extend right from the top and bottom of the box.
    Each ray stops extending when price touches it (high/low reaches
    the ray level).

    Works on any time-based chart (5 min, 15 min, etc.).
    New box drawn each trading day.
*/

SCSFExport scsf_InitialBalanceBox(SCStudyInterfaceRef sc)
{
    SCInputRef Input_IBStartTime   = sc.Input[0];
    SCInputRef Input_IBEndTime     = sc.Input[1];
    SCInputRef Input_BoxColor      = sc.Input[2];
    SCInputRef Input_BoxTransp     = sc.Input[3];
    SCInputRef Input_RayColor      = sc.Input[4];
    SCInputRef Input_RayWidth      = sc.Input[5];
    SCInputRef Input_Enabled       = sc.Input[6];
    SCInputRef Input_ShowRays      = sc.Input[7];
    SCInputRef Input_InstanceID    = sc.Input[8];

    SCSubgraphRef Subgraph_IBHigh       = sc.Subgraph[0];
    SCSubgraphRef Subgraph_IBLow        = sc.Subgraph[1];
    SCSubgraphRef Subgraph_HighBroken   = sc.Subgraph[2];
    SCSubgraphRef Subgraph_LowBroken    = sc.Subgraph[3];

    // Persistent state for right-click menu
    int& r_HideRaysMenuID  = sc.GetPersistentInt(0);
    int& r_RaysHidden      = sc.GetPersistentInt(1);

    if (sc.SetDefaults)
    {
        sc.GraphName = "Initial Balance Box";
        sc.AutoLoop = 1;
        sc.GraphRegion = 0;
        sc.UpdateStartIndex = 0;

        Input_IBStartTime.Name = "IB Start Time";
        Input_IBStartTime.SetTime(HMS_TIME(9, 30, 0));

        Input_IBEndTime.Name = "IB End Time";
        Input_IBEndTime.SetTime(HMS_TIME(10, 30, 0));

        Input_BoxColor.Name = "Box Color";
        Input_BoxColor.SetColor(RGB(0, 100, 200));

        Input_BoxTransp.Name = "Box Transparency (%)";
        Input_BoxTransp.SetInt(80);
        Input_BoxTransp.SetIntLimits(0, 100);

        Input_RayColor.Name = "Ray Color";
        Input_RayColor.SetColor(RGB(0, 100, 200));

        Input_RayWidth.Name = "Ray Line Width";
        Input_RayWidth.SetInt(2);
        Input_RayWidth.SetIntLimits(1, 5);

        Input_Enabled.Name = "Enabled";
        Input_Enabled.SetYesNo(1);

        Input_ShowRays.Name = "Show Rays";
        Input_ShowRays.SetYesNo(0);

        Input_InstanceID.Name = "Instance ID (1-9, unique per chart)";
        Input_InstanceID.SetInt(1);
        Input_InstanceID.SetIntLimits(1, 9);

        Subgraph_IBHigh.Name = "IB1 High";
        Subgraph_IBHigh.DrawStyle = DRAWSTYLE_IGNORE;
        Subgraph_IBHigh.PrimaryColor = RGB(0, 200, 0);

        Subgraph_IBLow.Name = "IB1 Low";
        Subgraph_IBLow.DrawStyle = DRAWSTYLE_IGNORE;
        Subgraph_IBLow.PrimaryColor = RGB(200, 0, 0);

        Subgraph_HighBroken.Name = "IB1 High Ray Broken";
        Subgraph_HighBroken.DrawStyle = DRAWSTYLE_IGNORE;
        Subgraph_HighBroken.PrimaryColor = RGB(255, 255, 0);

        Subgraph_LowBroken.Name = "IB1 Low Ray Broken";
        Subgraph_LowBroken.DrawStyle = DRAWSTYLE_IGNORE;
        Subgraph_LowBroken.PrimaryColor = RGB(255, 255, 0);

        return;
    }

    if (Input_Enabled.GetYesNo() == 0)
        return;

    // -- Cleanup on study removal --
    if (sc.LastCallToFunction)
    {
        if (r_HideRaysMenuID != 0)
            sc.RemoveACSChartShortcutMenuItem(sc.ChartNumber, r_HideRaysMenuID);
        return;
    }

    int instanceID = Input_InstanceID.GetInt();
    int BOX_LINE_BASE = 88800000 + instanceID * 10000;

    // Update subgraph names based on instance ID
    {
        char name[64];
        sprintf(name, "IB%d High", instanceID);
        Subgraph_IBHigh.Name = name;
        sprintf(name, "IB%d Low", instanceID);
        Subgraph_IBLow.Name = name;
        sprintf(name, "IB%d High Ray Broken", instanceID);
        Subgraph_HighBroken.Name = name;
        sprintf(name, "IB%d Low Ray Broken", instanceID);
        Subgraph_LowBroken.Name = name;
    }
    int RAY_LINE_BASE = 88810000 + instanceID * 10000;

    // -- Register right-click menu with instance label --
    if (r_HideRaysMenuID == 0)
    {
        char menuLabel[64];
        sprintf(menuLabel, "Hide IB %d Rays", instanceID);
        r_HideRaysMenuID = sc.AddACSChartShortcutMenuItem(sc.ChartNumber, menuLabel);
        r_RaysHidden = 0;
    }

    // -- Handle menu event --
    if (sc.MenuEventID != 0 && sc.MenuEventID == r_HideRaysMenuID)
    {
        r_RaysHidden = !r_RaysHidden;

        char menuLabel[64];
        if (r_RaysHidden)
            sprintf(menuLabel, "Show IB %d Rays", instanceID);
        else
            sprintf(menuLabel, "Hide IB %d Rays", instanceID);
        sc.ChangeACSChartShortcutMenuItemText(sc.ChartNumber, r_HideRaysMenuID, menuLabel);
    }

    // Only draw on last bar to avoid redundant drawing calls
    if (sc.Index != sc.ArraySize - 1)
        return;

    int ibStartTime = Input_IBStartTime.GetTime();
    int ibEndTime   = Input_IBEndTime.GetTime();
    int boxTransp   = Input_BoxTransp.GetInt();
    COLORREF boxColor = Input_BoxColor.GetColor();
    COLORREF rayColor = Input_RayColor.GetColor();
    int rayWidth    = Input_RayWidth.GetInt();

    // Scan all bars to find IB periods by day
    // Track unique days
    struct IBDay {
        int startBarIndex;
        int endBarIndex;
        float ibHigh;
        float ibLow;
        int dayDate;
        bool complete;
        int topRayEndBar;   // -1 = still extending
        int botRayEndBar;   // -1 = still extending
    };

    const int MAX_DAYS = 500;
    IBDay days[MAX_DAYS];
    int dayCount = 0;

    int currentDate = 0;
    int currentDayIdx = -1;

    for (int i = 0; i < sc.ArraySize; i++)
    {
        int barDate = sc.BaseDateTimeIn[i].GetDate();
        int barTime = sc.BaseDateTimeIn[i].GetTimeInSeconds();

        // Clear subgraphs
        Subgraph_IBHigh[i] = 0;
        Subgraph_IBLow[i] = 0;
        Subgraph_HighBroken[i] = 0;
        Subgraph_LowBroken[i] = 0;

        // New day
        if (barDate != currentDate)
        {
            currentDate = barDate;
            if (dayCount < MAX_DAYS)
            {
                currentDayIdx = dayCount;
                days[currentDayIdx].dayDate = barDate;
                days[currentDayIdx].startBarIndex = -1;
                days[currentDayIdx].endBarIndex = -1;
                days[currentDayIdx].ibHigh = -FLT_MAX;
                days[currentDayIdx].ibLow = FLT_MAX;
                days[currentDayIdx].complete = false;
                days[currentDayIdx].topRayEndBar = -1;
                days[currentDayIdx].botRayEndBar = -1;
                dayCount++;
            }
            else
            {
                currentDayIdx = -1;
            }
        }

        if (currentDayIdx < 0)
            continue;

        IBDay& day = days[currentDayIdx];

        // Within IB period
        if (barTime >= ibStartTime && barTime < ibEndTime)
        {
            if (day.startBarIndex < 0)
                day.startBarIndex = i;
            day.endBarIndex = i;

            if (sc.High[i] > day.ibHigh)
                day.ibHigh = sc.High[i];
            if (sc.Low[i] < day.ibLow)
                day.ibLow = sc.Low[i];
        }

        // After IB period — mark complete and check for ray touches
        if (barTime >= ibEndTime && day.startBarIndex >= 0)
        {
            day.complete = true;

            // Check top ray touch
            if (day.topRayEndBar < 0 && sc.High[i] >= day.ibHigh)
                day.topRayEndBar = i;

            // Check bottom ray touch
            if (day.botRayEndBar < 0 && sc.Low[i] <= day.ibLow)
                day.botRayEndBar = i;

            // Populate subgraphs — persist from IB complete to end of day
            Subgraph_IBHigh[i] = day.ibHigh;
            Subgraph_IBLow[i] = day.ibLow;
            Subgraph_HighBroken[i] = (day.topRayEndBar >= 0) ? 1.0f : 0.0f;
            Subgraph_LowBroken[i] = (day.botRayEndBar >= 0) ? 1.0f : 0.0f;
        }
    }

    // Draw boxes and rays for each day
    s_UseTool RectTool;
    RectTool.Clear();
    RectTool.ChartNumber = sc.ChartNumber;
    RectTool.DrawingType = DRAWING_RECTANGLEHIGHLIGHT;
    RectTool.Region = 0;
    RectTool.AddAsUserDrawnDrawing = 0;
    RectTool.AddMethod = UTAM_ADD_OR_ADJUST;

    s_UseTool RayTool;
    RayTool.Clear();
    RayTool.ChartNumber = sc.ChartNumber;
    RayTool.Region = 0;
    RayTool.AddAsUserDrawnDrawing = 0;
    RayTool.AddMethod = UTAM_ADD_OR_ADJUST;
    RayTool.LineStyle = LINESTYLE_SOLID;
    RayTool.DisplayHorizontalLineValue = 1;

    for (int d = 0; d < dayCount; d++)
    {
        IBDay& day = days[d];
        if (day.startBarIndex < 0 || day.endBarIndex < 0)
            continue;
        if (day.ibHigh <= day.ibLow)
            continue;

        // Draw box from IB start to IB end
        RectTool.LineNumber = BOX_LINE_BASE + d;
        RectTool.BeginIndex = day.startBarIndex;
        RectTool.EndIndex = day.endBarIndex;
        RectTool.BeginValue = day.ibLow;
        RectTool.EndValue = day.ibHigh;
        RectTool.Color = boxColor;
        RectTool.SecondaryColor = boxColor;
        RectTool.TransparencyLevel = boxTransp;
        sc.UseTool(RectTool);

        if (!day.complete)
            continue;

        // Rays: draw or hide based on input toggle AND right-click toggle
        bool showRays = (Input_ShowRays.GetYesNo() != 0) && !r_RaysHidden;

        // Top ray: from IB end to touch point (or chart edge)
        RayTool.LineNumber = RAY_LINE_BASE + d * 2;
        RayTool.DrawingType = DRAWING_LINE;
        if (showRays)
        {
            int topRayEnd = day.topRayEndBar >= 0 ? day.topRayEndBar : sc.ArraySize - 1;
            RayTool.BeginIndex = day.endBarIndex;
            RayTool.EndIndex = topRayEnd;
            RayTool.BeginValue = day.ibHigh;
            RayTool.EndValue = day.ibHigh;
            RayTool.Color = rayColor;
            RayTool.LineWidth = rayWidth;
        }
        else
        {
            RayTool.BeginIndex = 0;
            RayTool.EndIndex = 0;
            RayTool.BeginValue = 0;
            RayTool.EndValue = 0;
            RayTool.Color = RGB(0, 0, 0);
            RayTool.LineWidth = 0;
        }
        sc.UseTool(RayTool);

        // Bottom ray: from IB end to touch point (or chart edge)
        RayTool.LineNumber = RAY_LINE_BASE + d * 2 + 1;
        if (showRays)
        {
            int botRayEnd = day.botRayEndBar >= 0 ? day.botRayEndBar : sc.ArraySize - 1;
            RayTool.BeginIndex = day.endBarIndex;
            RayTool.EndIndex = botRayEnd;
            RayTool.BeginValue = day.ibLow;
            RayTool.EndValue = day.ibLow;
            RayTool.Color = rayColor;
            RayTool.LineWidth = rayWidth;
        }
        else
        {
            RayTool.BeginIndex = 0;
            RayTool.EndIndex = 0;
            RayTool.BeginValue = 0;
            RayTool.EndValue = 0;
            RayTool.Color = RGB(0, 0, 0);
            RayTool.LineWidth = 0;
        }
        sc.UseTool(RayTool);
    }
}
