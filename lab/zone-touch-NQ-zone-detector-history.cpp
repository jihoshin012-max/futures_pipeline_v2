// STUDY VERSION LOG
// Current: v3.1 (2026-03-18) — Pipeline snapshot
// No modifications since snapshot

/*===========================================================================*\
 * Supply and Demand Zones Study for Sierra Chart
 * Version: 4.0
 *
 * Zone detection via AutoLoop, drawing via s_UseTool on last bar only.
 * Broken zones replaced by editable horizontal rays at first bar's open.
 *
 * Changes from v3.0:
 *   - Delta Volume Profile rendered inside zone rectangles via GDI callback
 *   - VP scans the 2 bars that form each zone (ask-bid delta per tick level)
 *   - Blue/orange coloring for ask-dominant/bid-dominant price levels
 *   - Horizontal line extends from tip of longest delta bar
 *   - Configurable max VP profiles, colors, and transparency
 *
\*===========================================================================*/

#include "sierrachart.h"
#include <cfloat>

SCDLLName("Supply and Demand Zones V4")

static const int MAX_ZONE_CAPACITY    = 500;
static const int LINE_NUMBER_BASE     = 73590000;
static const int RAY_LINE_NUMBER_BASE    = 73600000;
static const int VP_RAY_LINE_NUMBER_BASE = 73610000;

// ── Types ─────────────────────────────────────────────────────────────────

enum ZoneType
{
	ZONE_TYPE_SUPPLY = 1,
	ZONE_TYPE_DEMAND = 2
};

struct ZoneData
{
	int      StartBarIndex;
	float    TopPrice;
	float    BottomPrice;
	ZoneType Type;
	bool     IsBroken;
	int      BreakBarIndex;
	bool     IsActive;
	bool     RayDrawn;
	bool     DrawingDirty; // true = rectangle needs UseTool call
};

struct ZoneStorage
{
	ZoneData Zones[MAX_ZONE_CAPACITY];
	int      HighWaterMark; // highest slot index ever written + 1
};

// ── Volume Profile Types ─────────────────────────────────────────────────

static const int      MAX_VP_LEVELS    = 200;
static const int      MAX_VP_PROFILES  = MAX_ZONE_CAPACITY;
static const uint32_t VP_STORAGE_MAGIC = 0x56505354; // "VPST"

struct VPLevelData
{
	int PriceInTicks;
	int AskVolume;
	int BidVolume;
	int Delta;     // AskVolume - BidVolume (signed)
	int AbsDelta;  // |Delta| for bar width scaling
};

struct VPProfile
{
	int         ZoneIndex;
	int         LevelCount;
	VPLevelData Levels[MAX_VP_LEVELS];
	int         MaxAbsDeltaIdx;  // index into Levels[] with highest |delta|
	int         MaxAbsDelta;     // value of highest |delta| (for normalization)
	float       ZoneTop;
	float       ZoneBottom;
	int         StartBarIndex;
	int         EndBarIndex;     // BreakBarIndex if broken, else current last bar
	bool        Valid;
	float       ImbalancePrice;  // price of highest in-zone |delta| level
	int         HitBarIndex;     // first bar where price touches imbalance level
	bool        ImbalanceValid;
};

struct VPStorage
{
	uint32_t  MagicNumber;
	int       ProfileCount;
	VPProfile Profiles[MAX_VP_PROFILES];
	COLORREF  AskColor;
	COLORREF  BidColor;
	int       Transparency;
	int       TicksPerBar;
	int       WidthPercent;
};

// ── Helpers ───────────────────────────────────────────────────────────────

static void DeleteAllRays(SCStudyInterfaceRef sc, int hwm)
{
	for (int i = 0; i < hwm; i++)
		sc.DeleteUserDrawnACSDrawing(sc.ChartNumber, RAY_LINE_NUMBER_BASE + i);
}

static void DeleteAllVPRays(SCStudyInterfaceRef sc)
{
	for (int k = 0; k < MAX_VP_PROFILES; k++)
		sc.DeleteACSChartDrawing(sc.ChartNumber, TOOL_DELETE_CHARTDRAWING, VP_RAY_LINE_NUMBER_BASE + k);
}

static void DeleteAllRectangles(SCStudyInterfaceRef sc, int hwm)
{
	for (int i = 0; i < hwm; i++)
		sc.DeleteACSChartDrawing(sc.ChartNumber, TOOL_DELETE_CHARTDRAWING, LINE_NUMBER_BASE + i);
}

// Apply zone type-specific color/transparency to a Tool struct
static void ApplyZoneStyle(s_UseTool& Tool, const ZoneData& z,
                            COLORREF supColor, COLORREF demColor,
                            int supTrans, int demTrans)
{
	if (z.Type == ZONE_TYPE_SUPPLY)
	{
		Tool.Color             = supColor;
		Tool.SecondaryColor    = supColor;
		Tool.TransparencyLevel = supTrans;
	}
	else
	{
		Tool.Color             = demColor;
		Tool.SecondaryColor    = demColor;
		Tool.TransparencyLevel = demTrans;
	}
}

// Evict oldest active unbroken zone if count exceeds maxZones.
// Returns true if an eviction occurred.
static bool EvictIfOverMaxZones(ZoneStorage* p, int maxZones)
{
	if (maxZones <= 0)
		return false;

	int active = 0;
	for (int i = 0; i < p->HighWaterMark; i++)
		if (p->Zones[i].IsActive && !p->Zones[i].IsBroken)
			active++;

	if (active < maxZones)
		return false;

	int oldestIdx = -1;
	int oldestBar = INT_MAX;
	for (int i = 0; i < p->HighWaterMark; i++)
	{
		if (p->Zones[i].IsActive && !p->Zones[i].IsBroken
		    && p->Zones[i].StartBarIndex < oldestBar)
		{
			oldestBar = p->Zones[i].StartBarIndex;
			oldestIdx = i;
		}
	}

	if (oldestIdx >= 0)
	{
		p->Zones[oldestIdx].IsActive = false;
		return true;
	}
	return false;
}

// Find an available slot: first inactive, then evict oldest broken zone.
// Updates HighWaterMark if a new slot is used.
static int FindSlot(ZoneStorage* p)
{
	for (int i = 0; i < p->HighWaterMark; i++)
		if (!p->Zones[i].IsActive)
			return i;

	// No inactive slot found — try to expand into unused capacity
	if (p->HighWaterMark < MAX_ZONE_CAPACITY)
	{
		int slot = p->HighWaterMark;
		p->HighWaterMark++;
		return slot;
	}

	// At max capacity — evict oldest broken zone
	int oldest = -1;
	int oldestBar = INT_MAX;
	for (int i = 0; i < MAX_ZONE_CAPACITY; i++)
	{
		if (p->Zones[i].IsActive && p->Zones[i].IsBroken
		    && p->Zones[i].StartBarIndex < oldestBar)
		{
			oldestBar = p->Zones[i].StartBarIndex;
			oldest = i;
		}
	}

	if (oldest >= 0)
		p->Zones[oldest].IsActive = false;

	return oldest;
}

// Create a zone in the given slot
static void CreateZone(ZoneStorage* p, int slot, int barIndex,
                        float topPrice, float bottomPrice, ZoneType type)
{
	ZoneData& z    = p->Zones[slot];
	z.StartBarIndex = barIndex;
	z.TopPrice      = topPrice;
	z.BottomPrice   = bottomPrice;
	z.Type          = type;
	z.IsBroken      = false;
	z.BreakBarIndex = -1;
	z.IsActive      = true;
	z.RayDrawn      = false;
	z.DrawingDirty  = true;

	// Update high water mark
	if (slot + 1 > p->HighWaterMark)
		p->HighWaterMark = slot + 1;
}

// Find the Nth largest value in an array using partial selection (O(n) average).
// Modifies the array in-place. Returns the value at position n (0-indexed).
static int NthLargest(int* arr, int count, int n)
{
	if (n >= count)
		return arr[count - 1];

	// Simple partial selection: find the top n values
	// For small n relative to count, this is efficient
	for (int i = 0; i < n + 1; i++)
	{
		int maxIdx = i;
		for (int j = i + 1; j < count; j++)
		{
			if (arr[j] > arr[maxIdx])
				maxIdx = j;
		}
		if (maxIdx != i)
		{
			int tmp = arr[i];
			arr[i] = arr[maxIdx];
			arr[maxIdx] = tmp;
		}
	}
	return arr[n];
}

// ── GDI Callback (forward declaration) ───────────────────────────────────

void DrawVolumeProfileGDI(HWND WindowHandle, HDC DeviceContext, SCStudyInterfaceRef sc);

// ── Study Function ───────────────────────────────────────────────────────

SCSFExport scsf_SupplyDemandZones(SCStudyInterfaceRef sc)
{
	// Subgraphs for Write File backtesting
	SCSubgraphRef SG_DemandSignal     = sc.Subgraph[0];
	SCSubgraphRef SG_DemandZoneTop    = sc.Subgraph[1];
	SCSubgraphRef SG_DemandZoneBot    = sc.Subgraph[2];
	SCSubgraphRef SG_SupplySignal     = sc.Subgraph[3];
	SCSubgraphRef SG_SupplyZoneTop    = sc.Subgraph[4];
	SCSubgraphRef SG_SupplyZoneBot    = sc.Subgraph[5];
	SCSubgraphRef SG_DemandBroken     = sc.Subgraph[6];
	SCSubgraphRef SG_SupplyBroken     = sc.Subgraph[7];
	SCSubgraphRef SG_NearestDemandTop = sc.Subgraph[8];
	SCSubgraphRef SG_NearestDemandBot = sc.Subgraph[9];
	SCSubgraphRef SG_NearestSupplyTop = sc.Subgraph[10];
	SCSubgraphRef SG_NearestSupplyBot = sc.Subgraph[11];
	SCSubgraphRef SG_DemandRayPrice   = sc.Subgraph[12];
	SCSubgraphRef SG_SupplyRayPrice   = sc.Subgraph[13];
	SCSubgraphRef SG_VPImbalancePrice = sc.Subgraph[14];

	SCInputRef Input_SupplyColor         = sc.Input[0];
	SCInputRef Input_DemandColor         = sc.Input[1];
	SCInputRef Input_SupplyTransparency  = sc.Input[2];
	SCInputRef Input_DemandTransparency  = sc.Input[3];
	SCInputRef Input_BodyRatioMultiplier = sc.Input[4];
	SCInputRef Input_MaxActiveZones      = sc.Input[5];
	SCInputRef Input_EnableSupply        = sc.Input[6];
	SCInputRef Input_EnableDemand        = sc.Input[7];
	SCInputRef Input_MaxRays             = sc.Input[8];
	SCInputRef Input_EnableRays          = sc.Input[9];
	SCInputRef Input_EnableVP            = sc.Input[10];
	SCInputRef Input_MaxVPProfiles       = sc.Input[11];
	SCInputRef Input_VPAskColor          = sc.Input[12];
	SCInputRef Input_VPBidColor          = sc.Input[13];
	SCInputRef Input_VPTransparency      = sc.Input[14];
	SCInputRef Input_VPTicksPerBar       = sc.Input[15];
	SCInputRef Input_VPWidthPercent      = sc.Input[16];
	SCInputRef Input_EnableVPRays        = sc.Input[17];
	SCInputRef Input_VPRayColor          = sc.Input[18];
	SCInputRef Input_VPRayWidth          = sc.Input[19];

	if (sc.SetDefaults)
	{
		sc.GraphName = "SD Zones V4 History [v3.1]";
		sc.GraphRegion = 0;
		sc.AutoLoop = 1;
		sc.CalculationPrecedence = LOW_PREC_LEVEL;
		sc.MaintainVolumeAtPriceData = 1;

		SG_DemandSignal.Name = "Demand Zone Signal";
		SG_DemandSignal.DrawStyle = DRAWSTYLE_IGNORE;
		SG_DemandSignal.PrimaryColor = RGB(0, 100, 255);

		SG_DemandZoneTop.Name = "Demand Zone Top";
		SG_DemandZoneTop.DrawStyle = DRAWSTYLE_IGNORE;
		SG_DemandZoneTop.PrimaryColor = RGB(0, 100, 255);

		SG_DemandZoneBot.Name = "Demand Zone Bottom";
		SG_DemandZoneBot.DrawStyle = DRAWSTYLE_IGNORE;
		SG_DemandZoneBot.PrimaryColor = RGB(0, 100, 255);

		SG_SupplySignal.Name = "Supply Zone Signal";
		SG_SupplySignal.DrawStyle = DRAWSTYLE_IGNORE;
		SG_SupplySignal.PrimaryColor = RGB(255, 0, 0);

		SG_SupplyZoneTop.Name = "Supply Zone Top";
		SG_SupplyZoneTop.DrawStyle = DRAWSTYLE_IGNORE;
		SG_SupplyZoneTop.PrimaryColor = RGB(255, 0, 0);

		SG_SupplyZoneBot.Name = "Supply Zone Bottom";
		SG_SupplyZoneBot.DrawStyle = DRAWSTYLE_IGNORE;
		SG_SupplyZoneBot.PrimaryColor = RGB(255, 0, 0);

		SG_DemandBroken.Name = "Demand Zone Broken";
		SG_DemandBroken.DrawStyle = DRAWSTYLE_IGNORE;
		SG_DemandBroken.PrimaryColor = RGB(0, 100, 255);

		SG_SupplyBroken.Name = "Supply Zone Broken";
		SG_SupplyBroken.DrawStyle = DRAWSTYLE_IGNORE;
		SG_SupplyBroken.PrimaryColor = RGB(255, 0, 0);

		SG_NearestDemandTop.Name = "Nearest Active Demand Top";
		SG_NearestDemandTop.DrawStyle = DRAWSTYLE_IGNORE;
		SG_NearestDemandTop.PrimaryColor = RGB(0, 100, 255);

		SG_NearestDemandBot.Name = "Nearest Active Demand Bottom";
		SG_NearestDemandBot.DrawStyle = DRAWSTYLE_IGNORE;
		SG_NearestDemandBot.PrimaryColor = RGB(0, 100, 255);

		SG_NearestSupplyTop.Name = "Nearest Active Supply Top";
		SG_NearestSupplyTop.DrawStyle = DRAWSTYLE_IGNORE;
		SG_NearestSupplyTop.PrimaryColor = RGB(255, 0, 0);

		SG_NearestSupplyBot.Name = "Nearest Active Supply Bottom";
		SG_NearestSupplyBot.DrawStyle = DRAWSTYLE_IGNORE;
		SG_NearestSupplyBot.PrimaryColor = RGB(255, 0, 0);

		SG_DemandRayPrice.Name = "Demand Ray Price";
		SG_DemandRayPrice.DrawStyle = DRAWSTYLE_IGNORE;
		SG_DemandRayPrice.PrimaryColor = RGB(0, 100, 255);

		SG_SupplyRayPrice.Name = "Supply Ray Price";
		SG_SupplyRayPrice.DrawStyle = DRAWSTYLE_IGNORE;
		SG_SupplyRayPrice.PrimaryColor = RGB(255, 0, 0);

		SG_VPImbalancePrice.Name = "VP Imbalance Price";
		SG_VPImbalancePrice.DrawStyle = DRAWSTYLE_IGNORE;
		SG_VPImbalancePrice.PrimaryColor = RGB(255, 255, 0);

		Input_SupplyColor.Name = "Supply Zone Color";
		Input_SupplyColor.SetColor(RGB(255, 0, 0));

		Input_DemandColor.Name = "Demand Zone Color";
		Input_DemandColor.SetColor(RGB(0, 100, 255));

		Input_SupplyTransparency.Name = "Supply Zone Transparency (0-100)";
		Input_SupplyTransparency.SetInt(80);
		Input_SupplyTransparency.SetIntLimits(0, 100);

		Input_DemandTransparency.Name = "Demand Zone Transparency (0-100)";
		Input_DemandTransparency.SetInt(80);
		Input_DemandTransparency.SetIntLimits(0, 100);

		Input_BodyRatioMultiplier.Name = "Body Ratio Multiplier";
		Input_BodyRatioMultiplier.SetFloat(1.5f);
		Input_BodyRatioMultiplier.SetFloatLimits(1.0f, 10.0f);

		Input_MaxActiveZones.Name = "Max Active Zones (0 = unlimited)";
		Input_MaxActiveZones.SetInt(0);
		Input_MaxActiveZones.SetIntLimits(0, MAX_ZONE_CAPACITY);

		Input_EnableSupply.Name = "Enable Supply Zones";
		Input_EnableSupply.SetYesNo(1);

		Input_EnableDemand.Name = "Enable Demand Zones";
		Input_EnableDemand.SetYesNo(1);

		Input_MaxRays.Name = "Max Rays to Show (0 = unlimited)";
		Input_MaxRays.SetInt(50);
		Input_MaxRays.SetIntLimits(0, MAX_ZONE_CAPACITY);

		Input_EnableRays.Name = "Enable Rays on Broken Zones";
		Input_EnableRays.SetYesNo(0);

		Input_EnableVP.Name = "Enable Volume Profile in Zones";
		Input_EnableVP.SetYesNo(1);

		Input_MaxVPProfiles.Name = "Max VP Profiles (0 = unlimited)";
		Input_MaxVPProfiles.SetInt(0);
		Input_MaxVPProfiles.SetIntLimits(0, MAX_ZONE_CAPACITY);

		Input_VPAskColor.Name = "VP Ask Dominant Color";
		Input_VPAskColor.SetColor(RGB(0, 100, 255));

		Input_VPBidColor.Name = "VP Bid Dominant Color";
		Input_VPBidColor.SetColor(RGB(255, 0, 0));

		Input_VPTransparency.Name = "VP Transparency (0-100)";
		Input_VPTransparency.SetInt(30);
		Input_VPTransparency.SetIntLimits(0, 100);

		Input_VPTicksPerBar.Name = "VP Ticks Per Volume Bar";
		Input_VPTicksPerBar.SetInt(20);
		Input_VPTicksPerBar.SetIntLimits(1, 100);

		Input_VPWidthPercent.Name = "VP Width % (10-500)";
		Input_VPWidthPercent.SetInt(30);
		Input_VPWidthPercent.SetIntLimits(10, 500);

		Input_EnableVPRays.Name = "Enable VP Imbalance Rays";
		Input_EnableVPRays.SetYesNo(1);

		Input_VPRayColor.Name = "VP Imbalance Ray Color";
		Input_VPRayColor.SetColor(RGB(255, 255, 0));

		Input_VPRayWidth.Name = "VP Imbalance Ray Width (1-10)";
		Input_VPRayWidth.SetInt(2);
		Input_VPRayWidth.SetIntLimits(1, 10);

		return;
	}

	// ── Persistent State ──

	ZoneStorage* p = (ZoneStorage*)sc.GetPersistentPointer(1);
	if (p == NULL)
	{
		p = (ZoneStorage*)sc.AllocateMemory(sizeof(ZoneStorage));
		if (p == NULL) return;
		memset(p, 0, sizeof(ZoneStorage));
		sc.SetPersistentPointer(1, p);
	}

	VPStorage* vp = (VPStorage*)sc.GetPersistentPointer(2);
	if (vp == NULL)
	{
		vp = (VPStorage*)sc.AllocateMemory(sizeof(VPStorage));
		if (vp == NULL) return;
		memset(vp, 0, sizeof(VPStorage));
		vp->MagicNumber = VP_STORAGE_MAGIC;
		sc.SetPersistentPointer(2, vp);
	}

	sc.p_GDIFunction = DrawVolumeProfileGDI;

	int& r_DeleteRaysMenuID = sc.GetPersistentInt(2);
	int& r_HideStudyMenuID  = sc.GetPersistentInt(3);
	int& r_IsHidden         = sc.GetPersistentInt(4);
	int& r_PrevRaysEnabled  = sc.GetPersistentInt(5);
	int& r_RaysDeleted      = sc.GetPersistentInt(6);
	int& r_PrevHideStudy    = sc.GetPersistentInt(7);
	int& r_HideVPMenuID     = sc.GetPersistentInt(8);
	int& r_VPHidden         = sc.GetPersistentInt(9);

	int hwm = p->HighWaterMark;

	// ── Cleanup on Study Removal ──

	if (sc.LastCallToFunction)
	{
		if (r_DeleteRaysMenuID != 0)
			sc.RemoveACSChartShortcutMenuItem(sc.ChartNumber, r_DeleteRaysMenuID);
		if (r_HideStudyMenuID != 0)
			sc.RemoveACSChartShortcutMenuItem(sc.ChartNumber, r_HideStudyMenuID);
		if (r_HideVPMenuID != 0)
			sc.RemoveACSChartShortcutMenuItem(sc.ChartNumber, r_HideVPMenuID);
		DeleteAllRays(sc, hwm);
		DeleteAllVPRays(sc);
		sc.FreeMemory(p);
		sc.SetPersistentPointer(1, NULL);
		if (vp != NULL)
		{
			sc.FreeMemory(vp);
			sc.SetPersistentPointer(2, NULL);
		}
		return;
	}

	// ── Full Recalculation ──

	if (sc.UpdateStartIndex == 0 && sc.Index == 0)
	{
		DeleteAllRectangles(sc, hwm);
		DeleteAllRays(sc, hwm);
		DeleteAllVPRays(sc);
		memset(p, 0, sizeof(ZoneStorage));
		hwm = 0;

		if (vp != NULL)
		{
			vp->ProfileCount = 0;
		}

		// Remove old menu items before re-registering (text may have been
		// changed to "Show S/D Rays" which makes Add treat it as a new item)
		if (r_DeleteRaysMenuID != 0)
			sc.RemoveACSChartShortcutMenuItem(sc.ChartNumber, r_DeleteRaysMenuID);
		if (r_HideStudyMenuID != 0)
			sc.RemoveACSChartShortcutMenuItem(sc.ChartNumber, r_HideStudyMenuID);
		if (r_HideVPMenuID != 0)
			sc.RemoveACSChartShortcutMenuItem(sc.ChartNumber, r_HideVPMenuID);

		r_DeleteRaysMenuID = sc.AddACSChartShortcutMenuItem(sc.ChartNumber, "Delete All S/D Rays");
		r_HideStudyMenuID  = sc.AddACSChartShortcutMenuItem(sc.ChartNumber, "Hide S/D Rays");
		r_HideVPMenuID     = sc.AddACSChartShortcutMenuItem(sc.ChartNumber, "Hide VP Profiles");

		r_RaysDeleted = 0;
	}

	// ── Handle Menu Events ──

	if (sc.MenuEventID != 0 && sc.MenuEventID == r_DeleteRaysMenuID)
	{
		DeleteAllRays(sc, hwm);
		r_RaysDeleted = 1;
		for (int i = 0; i < hwm; i++)
		{
			if (p->Zones[i].IsActive && p->Zones[i].IsBroken)
				p->Zones[i].RayDrawn = true;
		}
	}

	if (sc.MenuEventID != 0 && sc.MenuEventID == r_HideStudyMenuID)
	{
		r_IsHidden = !r_IsHidden;

		if (r_IsHidden)
		{
			DeleteAllRays(sc, hwm);
			sc.ChangeACSChartShortcutMenuItemText(sc.ChartNumber, r_HideStudyMenuID, "Show S/D Rays");
		}
		else
		{
			// User explicitly wants rays back — clear the delete flag
			r_RaysDeleted = 0;
			for (int i = 0; i < hwm; i++)
			{
				if (p->Zones[i].IsActive && p->Zones[i].IsBroken)
					p->Zones[i].RayDrawn = false;
			}
			sc.ChangeACSChartShortcutMenuItemText(sc.ChartNumber, r_HideStudyMenuID, "Hide S/D Rays");
		}
	}

	if (sc.MenuEventID != 0 && sc.MenuEventID == r_HideVPMenuID)
	{
		r_VPHidden = !r_VPHidden;

		if (r_VPHidden)
			sc.ChangeACSChartShortcutMenuItemText(sc.ChartNumber, r_HideVPMenuID, "Show VP Profiles");
		else
			sc.ChangeACSChartShortcutMenuItemText(sc.ChartNumber, r_HideVPMenuID, "Hide VP Profiles");
	}

	// ── Detect Enable Rays Toggle ──

	int CurrentRaysEnabled = Input_EnableRays.GetYesNo();
	if (r_PrevRaysEnabled != CurrentRaysEnabled)
	{
		if (!CurrentRaysEnabled)
		{
			DeleteAllRays(sc, hwm);
			for (int i = 0; i < hwm; i++)
			{
				if (p->Zones[i].IsActive && p->Zones[i].IsBroken)
					p->Zones[i].RayDrawn = false;
			}
		}
		r_PrevRaysEnabled = CurrentRaysEnabled;
	}

	// ── Detect SC Hide Study Checkbox ──

	if (sc.HideStudy != r_PrevHideStudy)
	{
		if (sc.HideStudy)
		{
			DeleteAllRectangles(sc, hwm);
			DeleteAllRays(sc, hwm);
			DeleteAllVPRays(sc);
		}
		else
		{
			r_RaysDeleted = 0;
			for (int i = 0; i < hwm; i++)
			{
				if (p->Zones[i].IsActive)
				{
					p->Zones[i].DrawingDirty = true;
					if (p->Zones[i].IsBroken)
						p->Zones[i].RayDrawn = false;
				}
			}
		}
		r_PrevHideStudy = sc.HideStudy;
	}

	// ── Per-Bar Processing ──

	float BodyRatio = Input_BodyRatioMultiplier.GetFloat();
	int   MaxZones  = Input_MaxActiveZones.GetInt();
	int   DoSupply  = Input_EnableSupply.GetYesNo();
	int   DoDemand  = Input_EnableDemand.GetYesNo();

	int ci = sc.Index;
	if (ci < 1) return;

	float CurO = sc.BaseData[SC_OPEN][ci];
	float CurH = sc.BaseData[SC_HIGH][ci];
	float CurL = sc.BaseData[SC_LOW][ci];
	float CurC = sc.BaseData[SC_LAST][ci];

	float PrvO = sc.BaseData[SC_OPEN][ci - 1];
	float PrvH = sc.BaseData[SC_HIGH][ci - 1];
	float PrvL = sc.BaseData[SC_LOW][ci - 1];
	float PrvC = sc.BaseData[SC_LAST][ci - 1];

	float Tick = sc.TickSize;

	// Clear subgraph values for this bar
	SG_DemandSignal[ci]     = 0;
	SG_DemandZoneTop[ci]    = 0;
	SG_DemandZoneBot[ci]    = 0;
	SG_SupplySignal[ci]     = 0;
	SG_SupplyZoneTop[ci]    = 0;
	SG_SupplyZoneBot[ci]    = 0;
	SG_DemandBroken[ci]     = 0;
	SG_SupplyBroken[ci]     = 0;
	SG_NearestDemandTop[ci] = 0;
	SG_NearestDemandBot[ci] = 0;
	SG_NearestSupplyTop[ci] = 0;
	SG_NearestSupplyBot[ci] = 0;
	SG_DemandRayPrice[ci]   = 0;
	SG_SupplyRayPrice[ci]   = 0;
	SG_VPImbalancePrice[ci] = 0;

	hwm = p->HighWaterMark; // refresh after potential menu-driven changes

	// ── Zone Detection (unified for supply and demand) ──

	// A lambda that handles both zone types to eliminate duplication
	auto DetectZone = [&](ZoneType type)
	{
		if (ci < sc.UpdateStartIndex || ci >= sc.ArraySize - 1)
			return;

		bool isSupply = (type == ZONE_TYPE_SUPPLY);

		// Pattern: supply = bullish prev + bearish current; demand = opposite
		bool prevPattern = isSupply ? (PrvC > PrvO) : (PrvC < PrvO);
		bool currPattern = isSupply ? (CurC < CurO) : (CurC > CurO);

		if (!prevPattern || !currPattern)
			return;

		float sb = (float)fabs(PrvC - PrvO);
		float rb = (float)fabs(CurC - CurO);

		// Breakout condition
		bool breakout = isSupply ? (CurL < PrvL) : (CurH > PrvH);

		if (!breakout || rb < BodyRatio * sb || sb < Tick)
			return;

		EvictIfOverMaxZones(p, MaxZones);

		int s = FindSlot(p);
		if (s < 0)
			return;

		float zoneTop, zoneBot;
		if (isSupply)
		{
			zoneTop = (CurH > PrvH) ? CurH : PrvH;
			zoneBot = PrvO;
		}
		else
		{
			zoneTop = PrvO;
			zoneBot = (CurL < PrvL) ? CurL : PrvL;
		}

		CreateZone(p, s, ci - 1, zoneTop, zoneBot, type);
		hwm = p->HighWaterMark;

		if (isSupply)
		{
			SG_SupplySignal[ci]  = 1;
			SG_SupplyZoneTop[ci] = zoneTop;
			SG_SupplyZoneBot[ci] = zoneBot;
		}
		else
		{
			SG_DemandSignal[ci]  = 1;
			SG_DemandZoneTop[ci] = zoneTop;
			SG_DemandZoneBot[ci] = zoneBot;
		}
	};

	if (DoSupply) DetectZone(ZONE_TYPE_SUPPLY);
	if (DoDemand) DetectZone(ZONE_TYPE_DEMAND);

	// ── Nearest Active Zone (edge distance) ──

	{
		float price = CurC;
		float bestDemandDist = FLT_MAX;
		float bestSupplyDist = FLT_MAX;

		for (int i = 0; i < hwm; i++)
		{
			const ZoneData& z = p->Zones[i];
			if (!z.IsActive || z.IsBroken)
				continue;
			if (ci <= z.StartBarIndex + 1)
				continue;

			// Edge distance: 0 if price is inside the zone
			float dist;
			if (price > z.TopPrice)
				dist = price - z.TopPrice;
			else if (price < z.BottomPrice)
				dist = z.BottomPrice - price;
			else
				dist = 0.0f;

			if (z.Type == ZONE_TYPE_DEMAND && dist < bestDemandDist)
			{
				bestDemandDist = dist;
				SG_NearestDemandTop[ci] = z.TopPrice;
				SG_NearestDemandBot[ci] = z.BottomPrice;
			}
			else if (z.Type == ZONE_TYPE_SUPPLY && dist < bestSupplyDist)
			{
				bestSupplyDist = dist;
				SG_NearestSupplyTop[ci] = z.TopPrice;
				SG_NearestSupplyBot[ci] = z.BottomPrice;
			}
		}
	}

	// ── Break Detection ──
	// MOVED AFTER nearest-zone output so that zones touched AND broken on the
	// same bar are still visible in subgraphs before being marked broken.
	// This makes recalc output include SBB zones, matching live behavior.

	int demandBreakCount = 0;
	int supplyBreakCount = 0;

	for (int i = 0; i < hwm; i++)
	{
		ZoneData& z = p->Zones[i];
		if (!z.IsActive || z.IsBroken)
			continue;
		if (ci <= z.StartBarIndex + 1)
			continue;

		if (z.Type == ZONE_TYPE_SUPPLY && CurH >= z.TopPrice)
		{
			z.IsBroken      = true;
			z.BreakBarIndex = ci;
			z.DrawingDirty  = true;
			supplyBreakCount++;
			SG_SupplyBroken[ci] = (float)supplyBreakCount;
			SG_SupplyRayPrice[ci] = z.BottomPrice;
		}
		else if (z.Type == ZONE_TYPE_DEMAND && CurL <= z.BottomPrice)
		{
			z.IsBroken      = true;
			z.BreakBarIndex = ci;
			z.DrawingDirty  = true;
			demandBreakCount++;
			SG_DemandBroken[ci] = (float)demandBreakCount;
			SG_DemandRayPrice[ci] = z.TopPrice;
		}
	}

	// ── Drawing — Last Bar Only ──

	if (ci != sc.ArraySize - 1 || sc.HideStudy)
		return;

	COLORREF SupColor = Input_SupplyColor.GetColor();
	COLORREF DemColor = Input_DemandColor.GetColor();
	int SupTrans = Input_SupplyTransparency.GetInt();
	int DemTrans = Input_DemandTransparency.GetInt();
	int MaxRays  = Input_MaxRays.GetInt();
	int DoRays   = Input_EnableRays.GetYesNo() && !r_IsHidden;

	// ── Ray Cutoff: find Nth most recent break bar via partial selection ──

	int RayCutoffBar = -1;
	if (DoRays && MaxRays > 0)
	{
		int breakBars[MAX_ZONE_CAPACITY];
		int breakCount = 0;
		for (int i = 0; i < hwm; i++)
		{
			if (p->Zones[i].IsActive && p->Zones[i].IsBroken)
				breakBars[breakCount++] = p->Zones[i].BreakBarIndex;
		}

		if (breakCount > MaxRays)
			RayCutoffBar = NthLargest(breakBars, breakCount, MaxRays - 1);
	}

	// ── Draw Zone Rectangles and Rays ──

	// Initialize Tool once with constant fields
	s_UseTool RectTool;
	RectTool.Clear();
	RectTool.ChartNumber           = sc.ChartNumber;
	RectTool.DrawingType           = DRAWING_RECTANGLEHIGHLIGHT;
	RectTool.Region                = 0;
	RectTool.AddAsUserDrawnDrawing = 0;
	RectTool.AddMethod             = UTAM_ADD_OR_ADJUST;

	s_UseTool RayTool;
	RayTool.Clear();
	RayTool.ChartNumber                = sc.ChartNumber;
	RayTool.DrawingType                = DRAWING_HORIZONTAL_RAY;
	RayTool.Region                     = 0;
	RayTool.AddAsUserDrawnDrawing      = 1;
	RayTool.AddMethod                  = UTAM_ADD_OR_ADJUST;
	RayTool.LineStyle                  = LINESTYLE_SOLID;
	RayTool.LineWidth                  = 2;
	RayTool.DisplayHorizontalLineValue = 1;
	RayTool.TextAlignment              = DT_RIGHT;

	for (int i = 0; i < hwm; i++)
	{
		ZoneData& z = p->Zones[i];
		if (!z.IsActive)
			continue;

		if (z.IsBroken)
		{
			// Broken zone: rectangle frozen at break bar
			// Skip UseTool if drawing hasn't changed since last draw
			if (z.DrawingDirty)
			{
				RectTool.LineNumber = LINE_NUMBER_BASE + i;
				RectTool.BeginIndex = z.StartBarIndex;
				RectTool.EndIndex   = z.BreakBarIndex;
				RectTool.BeginValue = z.BottomPrice;
				RectTool.EndValue   = z.TopPrice;
				ApplyZoneStyle(RectTool, z, SupColor, DemColor, SupTrans, DemTrans);
				sc.UseTool(RectTool);
				z.DrawingDirty = false;
			}

			// Ray drawing
			bool ShowRay = DoRays && (RayCutoffBar < 0 || z.BreakBarIndex >= RayCutoffBar);

			if (ShowRay && !z.RayDrawn)
			{
				float RayPrice = (z.Type == ZONE_TYPE_SUPPLY) ? z.BottomPrice : z.TopPrice;
				RayTool.LineNumber = RAY_LINE_NUMBER_BASE + i;
				RayTool.BeginIndex = z.StartBarIndex;
				RayTool.BeginValue = RayPrice;
				RayTool.Color      = (z.Type == ZONE_TYPE_SUPPLY) ? SupColor : DemColor;
				sc.UseTool(RayTool);
				z.RayDrawn = true;
			}
		}
		else
		{
			// Active zone: rectangle extends to current bar (always dirty)
			RectTool.LineNumber = LINE_NUMBER_BASE + i;
			RectTool.BeginIndex = z.StartBarIndex;
			RectTool.EndIndex   = ci;
			RectTool.BeginValue = z.BottomPrice;
			RectTool.EndValue   = z.TopPrice;
			ApplyZoneStyle(RectTool, z, SupColor, DemColor, SupTrans, DemTrans);
			sc.UseTool(RectTool);
		}
	}

	// ── Volume Profile Aggregation ──

	int DoVP = Input_EnableVP.GetYesNo();
	if (!DoVP || vp == NULL || r_VPHidden)
	{
		if (vp != NULL) vp->ProfileCount = 0;
		DeleteAllVPRays(sc);
		return;
	}

	int MaxVPCount = Input_MaxVPProfiles.GetInt();
	if (MaxVPCount <= 0 || MaxVPCount > MAX_VP_PROFILES) MaxVPCount = MAX_VP_PROFILES;

	// Collect all active zone indices
	int vpCandidates[MAX_ZONE_CAPACITY];
	int vpCandidateCount = 0;
	for (int i = 0; i < hwm; i++)
	{
		if (p->Zones[i].IsActive)
			vpCandidates[vpCandidateCount++] = i;
	}

	// Partial selection sort: pick MaxVPCount zones with highest StartBarIndex (most recent)
	int vpCount = (vpCandidateCount < MaxVPCount) ? vpCandidateCount : MaxVPCount;
	for (int k = 0; k < vpCount; k++)
	{
		int bestIdx = k;
		for (int j = k + 1; j < vpCandidateCount; j++)
		{
			if (p->Zones[vpCandidates[j]].StartBarIndex > p->Zones[vpCandidates[bestIdx]].StartBarIndex)
				bestIdx = j;
		}
		if (bestIdx != k)
		{
			int tmp = vpCandidates[k];
			vpCandidates[k] = vpCandidates[bestIdx];
			vpCandidates[bestIdx] = tmp;
		}
	}

	// Aggregate VAP data for each selected zone
	float TickSize = sc.TickSize;
	float HalfTick = TickSize * 0.5f;
	int TicksPerBar = Input_VPTicksPerBar.GetInt();
	if (TicksPerBar < 1) TicksPerBar = 1;

	for (int k = 0; k < vpCount; k++)
	{
		int zi = vpCandidates[k];
		ZoneData& z = p->Zones[zi];
		VPProfile& prof = vp->Profiles[k];

		prof.ZoneIndex     = zi;
		prof.ZoneTop       = z.TopPrice;
		prof.ZoneBottom    = z.BottomPrice;
		prof.StartBarIndex = z.StartBarIndex;
		prof.EndBarIndex   = z.IsBroken ? z.BreakBarIndex : ci;
		prof.LevelCount    = 0;
		prof.MaxAbsDelta   = 0;
		prof.MaxAbsDeltaIdx = 0;
		prof.Valid          = false;

		// First pass: find minimum PriceInTicks to align bucket boundaries
		// (native VP buckets relative to data start, not absolute multiples)
		int minPriceTicks = INT_MAX;
		for (int barOffset = 0; barOffset <= 1; barOffset++)
		{
			int barIdx = z.StartBarIndex + barOffset;
			if (barIdx < 0 || barIdx >= sc.ArraySize)
				continue;

			int numLevels = sc.VolumeAtPriceForBars->GetSizeAtBarIndex(barIdx);
			const s_VolumeAtPriceV2* pVAP = NULL;

			for (int vi = 0; vi < numLevels; vi++)
			{
				if (!sc.VolumeAtPriceForBars->GetVAPElementAtIndex(barIdx, vi, &pVAP))
					break;
				if (pVAP->PriceInTicks < minPriceTicks)
					minPriceTicks = pVAP->PriceInTicks;
			}
		}
		if (minPriceTicks == INT_MAX)
			minPriceTicks = 0;

		// Second pass: aggregate ALL price levels with relative bucketing
		for (int barOffset = 0; barOffset <= 1; barOffset++)
		{
			int barIdx = z.StartBarIndex + barOffset;
			if (barIdx < 0 || barIdx >= sc.ArraySize)
				continue;

			int numLevels = sc.VolumeAtPriceForBars->GetSizeAtBarIndex(barIdx);
			const s_VolumeAtPriceV2* pVAP = NULL;

			for (int vi = 0; vi < numLevels; vi++)
			{
				if (!sc.VolumeAtPriceForBars->GetVAPElementAtIndex(barIdx, vi, &pVAP))
					break;

				int priceTicks = pVAP->PriceInTicks;

				// Bucket ticks: align relative to data's lowest price
				int bucketTicks = minPriceTicks + ((priceTicks - minPriceTicks) / TicksPerBar) * TicksPerBar;

				// Find existing bucket or insert new
				int found = -1;
				for (int li = 0; li < prof.LevelCount; li++)
				{
					if (prof.Levels[li].PriceInTicks == bucketTicks)
					{
						found = li;
						break;
					}
				}

				if (found >= 0)
				{
					prof.Levels[found].AskVolume += (int)pVAP->AskVolume;
					prof.Levels[found].BidVolume += (int)pVAP->BidVolume;
				}
				else if (prof.LevelCount < MAX_VP_LEVELS)
				{
					VPLevelData& lvl = prof.Levels[prof.LevelCount];
					lvl.PriceInTicks = bucketTicks;
					lvl.AskVolume    = (int)pVAP->AskVolume;
					lvl.BidVolume    = (int)pVAP->BidVolume;
					lvl.Delta        = 0;
					lvl.AbsDelta     = 0;
					prof.LevelCount++;
				}
			}
		}

		// Compute delta and |delta| for bar width (matches native VP "Ask Volume - Bid Volume")
		for (int li = 0; li < prof.LevelCount; li++)
		{
			VPLevelData& lvl = prof.Levels[li];
			lvl.Delta    = lvl.AskVolume - lvl.BidVolume;
			lvl.AbsDelta = (lvl.Delta >= 0) ? lvl.Delta : -lvl.Delta;

			if (lvl.AbsDelta > prof.MaxAbsDelta)
			{
				prof.MaxAbsDelta    = lvl.AbsDelta;
				prof.MaxAbsDeltaIdx = li;
			}
		}

		prof.Valid = (prof.LevelCount > 0 && prof.MaxAbsDelta > 0);
	}

	vp->ProfileCount  = vpCount;
	vp->AskColor      = Input_VPAskColor.GetColor();
	vp->BidColor      = Input_VPBidColor.GetColor();
	vp->Transparency  = Input_VPTransparency.GetInt();
	vp->TicksPerBar   = TicksPerBar;
	vp->WidthPercent  = Input_VPWidthPercent.GetInt();

	// ── VP Imbalance Rays ──

	int DoVPRays = Input_EnableVPRays.GetYesNo();
	if (!DoVPRays)
	{
		DeleteAllVPRays(sc);
		return;
	}

	COLORREF vpRayColor = Input_VPRayColor.GetColor();
	int vpRayWidth = Input_VPRayWidth.GetInt();

	s_UseTool VPRayTool;
	VPRayTool.Clear();
	VPRayTool.ChartNumber           = sc.ChartNumber;
	VPRayTool.DrawingType           = DRAWING_LINE;
	VPRayTool.Region                = 0;
	VPRayTool.AddAsUserDrawnDrawing = 0;
	VPRayTool.AddMethod             = UTAM_ADD_OR_ADJUST;
	VPRayTool.LineStyle             = LINESTYLE_SOLID;
	VPRayTool.LineWidth             = (unsigned short)vpRayWidth;
	VPRayTool.Color                 = vpRayColor;
	VPRayTool.SecondaryColor        = vpRayColor;

	for (int k = 0; k < vpCount; k++)
	{
		VPProfile& prof = vp->Profiles[k];
		prof.ImbalanceValid = false;

		if (!prof.Valid)
		{
			sc.DeleteACSChartDrawing(sc.ChartNumber, TOOL_DELETE_CHARTDRAWING, VP_RAY_LINE_NUMBER_BASE + k);
			continue;
		}

		// Find in-zone max |delta| level
		int inZoneMaxAbsDelta = 0;
		int inZoneMaxIdx = -1;
		int zTopTicks = (int)(prof.ZoneTop / TickSize + 0.5f);
		int zBotTicks = (int)(prof.ZoneBottom / TickSize - 0.5f);

		for (int li = 0; li < prof.LevelCount; li++)
		{
			const VPLevelData& lvl = prof.Levels[li];
			if (lvl.AbsDelta == 0) continue;
			if (lvl.PriceInTicks > zTopTicks || lvl.PriceInTicks + TicksPerBar < zBotTicks)
				continue;
			if (lvl.AbsDelta > inZoneMaxAbsDelta)
			{
				inZoneMaxAbsDelta = lvl.AbsDelta;
				inZoneMaxIdx = li;
			}
		}

		if (inZoneMaxIdx < 0)
		{
			sc.DeleteACSChartDrawing(sc.ChartNumber, TOOL_DELETE_CHARTDRAWING, VP_RAY_LINE_NUMBER_BASE + k);
			continue;
		}

		double bps = (double)TickSize * TicksPerBar;
		float imbalancePrice = (float)((double)prof.Levels[inZoneMaxIdx].PriceInTicks * TickSize + bps * 0.5);

		// Scan forward to find first bar where price touches the imbalance level
		int hitBarIdx = ci; // default: extend to current bar
		int scanStart = prof.StartBarIndex + 2;
		for (int bi = scanStart; bi < sc.ArraySize; bi++)
		{
			float barHigh = sc.BaseData[SC_HIGH][bi];
			float barLow  = sc.BaseData[SC_LOW][bi];
			if (barLow <= imbalancePrice && barHigh >= imbalancePrice)
			{
				hitBarIdx = bi;
				break;
			}
		}

		prof.ImbalancePrice = imbalancePrice;
		prof.HitBarIndex    = hitBarIdx;
		prof.ImbalanceValid = true;

		VPRayTool.LineNumber = VP_RAY_LINE_NUMBER_BASE + k;
		VPRayTool.BeginIndex = prof.StartBarIndex;
		VPRayTool.BeginValue = imbalancePrice;
		VPRayTool.EndIndex   = hitBarIdx;
		VPRayTool.EndValue   = imbalancePrice;
		sc.UseTool(VPRayTool);

		// Fill subgraph with imbalance price across the ray's active range
		for (int bi = prof.StartBarIndex; bi <= hitBarIdx && bi < sc.ArraySize; bi++)
			SG_VPImbalancePrice[bi] = imbalancePrice;
	}

	// Delete ray drawings beyond current profile count
	for (int k = vpCount; k < MAX_VP_PROFILES; k++)
		sc.DeleteACSChartDrawing(sc.ChartNumber, TOOL_DELETE_CHARTDRAWING, VP_RAY_LINE_NUMBER_BASE + k);
}

// ── GDI Callback: Draw Volume Profiles ───────────────────────────────────

void DrawVolumeProfileGDI(HWND WindowHandle, HDC DeviceContext, SCStudyInterfaceRef sc)
{
	VPStorage* vp = (VPStorage*)sc.GetPersistentPointer(2);
	if (vp == NULL || vp->MagicNumber != VP_STORAGE_MAGIC || vp->ProfileCount == 0)
		return;

	if (sc.HideStudy)
		return;

	float tickSize = sc.TickSize;
	if (tickSize <= 0.0f)
		return;

	// Compute bar pixel height (how many pixels one VP bar occupies on screen)
	int ticksPerBar = vp->TicksPerBar;
	if (ticksPerBar < 1) ticksPerBar = 1;
	double barPriceSpan = (double)tickSize * ticksPerBar;

	int chartBottom = sc.StudyRegionBottomCoordinate;
	double refPrice = sc.YPixelCoordinateToGraphValue(chartBottom);
	int y1 = sc.RegionValueToYPixelCoordinate(refPrice, sc.GraphRegion);
	int y2 = sc.RegionValueToYPixelCoordinate(refPrice + barPriceSpan, sc.GraphRegion);
	int barPxHeight = abs(y2 - y1);
	if (barPxHeight < 1)
		barPxHeight = 1;

	// Transparency: blend VP colors toward black (GDI has no native alpha)
	// transparency 0 = fully opaque, 100 = fully transparent
	int trans = vp->Transparency;
	if (trans < 0) trans = 0;
	if (trans > 100) trans = 100;
	int opacityPct = 100 - trans; // 0-100, percentage of color to keep

	// Width scaling factor from user input (100 = normal, 200 = double, 50 = half)
	int widthPct = vp->WidthPercent;
	if (widthPct < 10) widthPct = 10;
	if (widthPct > 500) widthPct = 500;

	n_ACSIL::s_GraphicsPen pen;
	pen.m_PenStyle = n_ACSIL::s_GraphicsPen::e_PenStyle::PEN_STYLE_SOLID;

	for (int pi = 0; pi < vp->ProfileCount; pi++)
	{
		const VPProfile& prof = vp->Profiles[pi];
		if (!prof.Valid)
			continue;

		// Get zone rectangle pixel bounds
		int zoneLeftX  = sc.BarIndexToXPixelCoordinate(prof.StartBarIndex);
		int zoneRightX = sc.BarIndexToXPixelCoordinate(prof.EndBarIndex);
		int zoneWidth  = zoneRightX - zoneLeftX;
		if (zoneWidth < 4)
			continue;

		// VP bars scaled by width percentage
		int maxBarWidth = zoneWidth * widthPct / 100;
		if (maxBarWidth < 2) maxBarWidth = 2;

		// Zone price bounds in ticks for clipping (only draw levels inside zone)
		int zoneTopTicks = (int)(prof.ZoneTop / tickSize + 0.5f);
		int zoneBotTicks = (int)(prof.ZoneBottom / tickSize - 0.5f);

		// Draw each VP level as a thick horizontal line (pen width = bar height)
		for (int li = 0; li < prof.LevelCount; li++)
		{
			const VPLevelData& lvl = prof.Levels[li];
			if (lvl.AbsDelta == 0)
				continue;

			if (lvl.PriceInTicks > zoneTopTicks || lvl.PriceInTicks + ticksPerBar < zoneBotTicks)
				continue;

			double bucketPrice = (double)lvl.PriceInTicks * tickSize + barPriceSpan * 0.5;
			int centerY = sc.RegionValueToYPixelCoordinate(bucketPrice, sc.GraphRegion);

			int barWidth = (int)((float)lvl.AbsDelta / (float)prof.MaxAbsDelta * (float)maxBarWidth);
			if (barWidth < 1)
				barWidth = 1;

			COLORREF baseColor = (lvl.Delta >= 0) ? vp->AskColor : vp->BidColor;

			int r = GetRValue(baseColor) * opacityPct / 100;
			int g = GetGValue(baseColor) * opacityPct / 100;
			int b = GetBValue(baseColor) * opacityPct / 100;

			pen.m_Width = barPxHeight;
			pen.m_PenColor.SetRGB(r, g, b);
			sc.Graphics.SetPen(pen);

			sc.Graphics.MoveTo(zoneLeftX, centerY);
			sc.Graphics.LineTo(zoneLeftX + barWidth, centerY);
		}
	}
}