"""
Intraday transition analysis: trending → ranging conditions
and their impact on trade outcomes.

Computes per-bar slope trajectory, detects slope phases
(TRENDING / TRANSITION / RANGING), and tests combined filters.

Inputs:
  - NQ-250tick-calibration.csv  (127K bars)
  - band-slope-analysis.csv     (2685 trades with slope features)

Outputs:
  - intraday-transition-phases.csv        (WR, EV by slope phase)
  - intraday-transition-slope-change.csv   (tercile analysis)
  - intraday-transition-filters.csv        (filter test results)
  - intraday-transition-summary.txt        (text summary)
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import time as dt_time

# ── paths ──────────────────────────────────────────────────────
BASE = Path(r"c:/Projects/futures_pipeline")
DATA = BASE / "data" / "NQ-250tick-calibration.csv"
OUT  = BASE / "lab" / "output" / "rotational-NQ-scale-detection"

trades_path = OUT / "band-slope-analysis.csv"

COST_PER_TRADE = 4.0  # round-trip cost in dollars
TICK_VALUE = 5.0       # NQ $5 per 0.25 tick = $20/pt

# ── 1. Load calibration data ─────────────────────────────────
print("Loading calibration data...")
cal = pd.read_csv(DATA)
cal.columns = cal.columns.str.strip()

cal["mid"] = (cal["High"] + cal["Low"]) / 2.0
cal["mean100"] = cal["mid"].rolling(100, min_periods=100).mean()
cal["std100"] = cal["mid"].rolling(100, min_periods=100).std()
cal["band_width_calc"] = 1.5 * cal["std100"]

# Parse datetime
cal["dt_str"] = cal["Date"].str.strip() + " " + cal["Time"].str.strip()
cal["datetime"] = pd.to_datetime(cal["dt_str"], format="%m/%d/%Y %H:%M:%S.%f")
cal["date_only"] = cal["datetime"].dt.date
cal["time_only"] = cal["datetime"].dt.time

# Filter to RTH (09:30 - 15:45)
RTH_START = dt_time(9, 30)
RTH_END   = dt_time(15, 45)
cal["is_rth"] = cal["time_only"].apply(lambda t: RTH_START <= t <= RTH_END)

print(f"  {len(cal)} total bars, {cal['is_rth'].sum()} RTH bars")

# ── Step 1: Compute intraday slope trajectory per bar ────────
print("Computing slope trajectory...")

# abs_mean_slope_10: absolute slope of mean100 over 10 bars
cal["abs_slope_10"] = (cal["mean100"] - cal["mean100"].shift(10)).abs() / 10.0

# Smoothed slope: 20-bar rolling average of abs_slope_10
cal["smoothed_slope"] = cal["abs_slope_10"].rolling(20, min_periods=10).mean()

# slope_change_rate: rate of change of abs_slope_10 over 10 bars
cal["slope_change_rate"] = (cal["abs_slope_10"] - cal["abs_slope_10"].shift(10)) / 10.0

# ── Step 2: Compute session median + assign slope phase ──────
print("Assigning slope phases...")

# Session median of smoothed_slope (per trading day, RTH only)
rth = cal[cal["is_rth"]].copy()
session_medians = rth.groupby("date_only")["smoothed_slope"].median()
session_medians_slope10 = rth.groupby("date_only")["abs_slope_10"].median()

# Map session median back to each bar
cal["session_median_smoothed"] = cal["date_only"].map(session_medians)
cal["session_median_abs_slope_10"] = cal["date_only"].map(session_medians_slope10)

# Detect slope peak: smoothed slope declining after rising
# smoothed_slope_diff > 0 => rising, < 0 => declining
cal["smoothed_slope_diff"] = cal["smoothed_slope"] - cal["smoothed_slope"].shift(1)

# Rolling 5-bar sign of diff to smooth noise
cal["slope_direction"] = cal["smoothed_slope_diff"].rolling(5, min_periods=3).mean()

# Assign phase per bar (only for RTH bars with valid data)
def assign_phase(row):
    if pd.isna(row["smoothed_slope"]) or pd.isna(row["session_median_smoothed"]):
        return np.nan
    above_median = row["smoothed_slope"] > row["session_median_smoothed"]
    rising_or_flat = row["slope_direction"] >= 0 if not pd.isna(row["slope_direction"]) else True

    if above_median and rising_or_flat:
        return "TRENDING"
    elif above_median and not rising_or_flat:
        return "TRANSITION"
    else:
        return "RANGING"

cal["slope_phase"] = cal.apply(assign_phase, axis=1)

phase_counts = cal[cal["is_rth"]]["slope_phase"].value_counts()
print(f"  RTH bar phase counts: {phase_counts.to_dict()}")

# ── 3. Load trades and match to bar features ─────────────────
print("Loading trades...")
trades = pd.read_csv(trades_path)
trades.columns = trades.columns.str.strip()
print(f"  {len(trades)} trades loaded")

# Parse trade entry datetime
trades["entry_dt"] = pd.to_datetime(
    trades["entry_date"].str.strip() + " " + trades["entry_time"].str.strip(),
    format="%m/%d/%Y %H:%M:%S"
)

# Match each trade to the nearest calibration bar
cal_valid = cal.dropna(subset=["smoothed_slope"]).sort_values("datetime").reset_index(drop=True)
cal_dts = cal_valid["datetime"].values

trade_indices = np.searchsorted(cal_dts, trades["entry_dt"].values, side="right") - 1
trade_indices = np.clip(trade_indices, 0, len(cal_valid) - 1)

# Pull features
trades["smoothed_slope"] = cal_valid["smoothed_slope"].values[trade_indices]
trades["slope_phase"] = cal_valid["slope_phase"].values[trade_indices]
trades["slope_change_rate"] = cal_valid["slope_change_rate"].values[trade_indices]
trades["abs_slope_10"] = cal_valid["abs_slope_10"].values[trade_indices]
trades["session_median_abs_slope_10"] = cal_valid["session_median_abs_slope_10"].values[trade_indices]
trades["session_median_smoothed"] = cal_valid["session_median_smoothed"].values[trade_indices]
trades["bw_calc"] = cal_valid["band_width_calc"].values[trade_indices]

# Compute band_width median for filters
bw_median = trades["band_width"].median()
print(f"  band_width median: {bw_median:.4f}")

# ── Step 3: Win rate and EV by slope phase ───────────────────
print("\n=== Step 3: Performance by slope phase ===")

def compute_stats(df, label=""):
    n = len(df)
    if n == 0:
        return {"label": label, "trades": 0, "win_rate": np.nan,
                "ev_per_trade": np.nan, "total_pnl": np.nan}
    wins = df["win"].sum()
    wr = wins / n
    total_pnl = df["pnl_dollar"].sum() - n * COST_PER_TRADE
    ev = total_pnl / n
    return {
        "label": label,
        "trades": n,
        "win_rate": round(wr, 4),
        "ev_per_trade": round(ev, 2),
        "total_pnl": round(total_pnl, 2),
    }

phases_data = []
for phase in ["TRENDING", "TRANSITION", "RANGING"]:
    subset = trades[trades["slope_phase"] == phase]
    stats = compute_stats(subset, phase)
    phases_data.append(stats)
    print(f"  {phase}: {stats['trades']} trades, WR={stats['win_rate']}, EV=${stats['ev_per_trade']}, PnL=${stats['total_pnl']}")

# Add overall
stats_all = compute_stats(trades, "ALL")
phases_data.append(stats_all)
print(f"  ALL: {stats_all['trades']} trades, WR={stats_all['win_rate']}, EV=${stats_all['ev_per_trade']}, PnL=${stats_all['total_pnl']}")

phases_df = pd.DataFrame(phases_data)
phases_df.to_csv(OUT / "intraday-transition-phases.csv", index=False)
print(f"  Wrote intraday-transition-phases.csv")

# ── Step 4: Slope change rate terciles ───────────────────────
print("\n=== Step 4: Slope change rate terciles ===")

valid_scr = trades.dropna(subset=["slope_change_rate"]).copy()
tercile_edges = valid_scr["slope_change_rate"].quantile([1/3, 2/3]).values
print(f"  Tercile edges: {tercile_edges[0]:.6f}, {tercile_edges[1]:.6f}")

def assign_tercile(scr):
    if scr <= tercile_edges[0]:
        return "T1_declining"
    elif scr <= tercile_edges[1]:
        return "T2_stable"
    else:
        return "T3_rising"

valid_scr["scr_tercile"] = valid_scr["slope_change_rate"].apply(assign_tercile)

tercile_data = []
for tercile in ["T1_declining", "T2_stable", "T3_rising"]:
    subset = valid_scr[valid_scr["scr_tercile"] == tercile]
    scr_range = f"[{subset['slope_change_rate'].min():.6f}, {subset['slope_change_rate'].max():.6f}]"
    stats = compute_stats(subset, tercile)
    stats["scr_range"] = scr_range
    tercile_data.append(stats)
    print(f"  {tercile}: {stats['trades']} trades, WR={stats['win_rate']}, EV=${stats['ev_per_trade']}, PnL=${stats['total_pnl']}")

# Add overall for context
stats_valid = compute_stats(valid_scr, "ALL")
stats_valid["scr_range"] = f"[{valid_scr['slope_change_rate'].min():.6f}, {valid_scr['slope_change_rate'].max():.6f}]"
tercile_data.append(stats_valid)

tercile_df = pd.DataFrame(tercile_data)
tercile_df.to_csv(OUT / "intraday-transition-slope-change.csv", index=False)
print(f"  Wrote intraday-transition-slope-change.csv")

# ── Step 5: Combined width + slope phase filters ─────────────
print("\n=== Step 5: Filter tests ===")

def compute_filter_stats(df, label):
    n = len(df)
    if n == 0:
        return {
            "filter": label, "trades": 0, "total_pnl_net": np.nan,
            "max_dd": np.nan, "pnl_dd_ratio": np.nan,
            "win_rate": np.nan, "ev_per_trade": np.nan
        }
    wins = df["win"].sum()
    wr = wins / n
    gross_pnl = df["pnl_dollar"].sum()
    net_pnl = gross_pnl - n * COST_PER_TRADE
    ev = net_pnl / n

    # Max drawdown
    cumulative = (df["pnl_dollar"].values - COST_PER_TRADE).cumsum()
    running_max = np.maximum.accumulate(cumulative)
    drawdowns = running_max - cumulative
    max_dd = drawdowns.max() if len(drawdowns) > 0 else 0.0
    pnl_dd = net_pnl / max_dd if max_dd > 0 else np.nan

    return {
        "filter": label,
        "trades": n,
        "total_pnl_net": round(net_pnl, 2),
        "max_dd": round(max_dd, 2),
        "pnl_dd_ratio": round(pnl_dd, 3) if not pd.isna(pnl_dd) else np.nan,
        "win_rate": round(wr, 4),
        "ev_per_trade": round(ev, 2),
    }

# Prepare filters
wide = trades["band_width"] > bw_median
ranging = trades["slope_phase"] == "RANGING"
transition = trades["slope_phase"] == "TRANSITION"
scr_neg = trades["slope_change_rate"] < 0
flat_enough = trades["abs_slope_10"] < trades["session_median_abs_slope_10"]

filters = {
    "Baseline (no filter)": trades,
    "F1: bw>median AND RANGING": trades[wide & ranging],
    "F2: bw>median AND (TRANSITION or RANGING)": trades[wide & (transition | ranging)],
    "F3: slope_change_rate < 0": trades[scr_neg],
    "F4: bw>median AND slope_change_rate < 0": trades[wide & scr_neg],
    "F5: abs_slope_10 < session median": trades[flat_enough],
}

filter_data = []
for label, subset in filters.items():
    stats = compute_filter_stats(subset.copy().reset_index(drop=True), label)
    filter_data.append(stats)
    print(f"  {label}: {stats['trades']} trades, PnL=${stats['total_pnl_net']}, "
          f"DD=${stats['max_dd']}, PnL/DD={stats['pnl_dd_ratio']}, "
          f"WR={stats['win_rate']}, EV=${stats['ev_per_trade']}")

filter_df = pd.DataFrame(filter_data)
filter_df.to_csv(OUT / "intraday-transition-filters.csv", index=False)
print(f"  Wrote intraday-transition-filters.csv")

# ── Summary ──────────────────────────────────────────────────
print("\nWriting summary...")

summary_lines = []
summary_lines.append("INTRADAY TRANSITION ANALYSIS — SLOPE PHASE IMPACT ON TRADE OUTCOMES")
summary_lines.append("=" * 70)
summary_lines.append(f"Total trades: {len(trades)}")
summary_lines.append(f"Band width median: {bw_median:.4f}")
summary_lines.append(f"Slope change rate tercile edges: {tercile_edges[0]:.6f}, {tercile_edges[1]:.6f}")
summary_lines.append("")

summary_lines.append("STEP 3: Performance by slope phase")
summary_lines.append("-" * 50)
for _, row in phases_df.iterrows():
    summary_lines.append(f"  {row['label']:12s}: {int(row['trades']):5d} trades, "
                         f"WR={row['win_rate']:.4f}, EV=${row['ev_per_trade']:8.2f}, "
                         f"PnL=${row['total_pnl']:10.2f}")
summary_lines.append("")

summary_lines.append("STEP 4: Slope change rate terciles")
summary_lines.append("-" * 50)
for _, row in tercile_df.iterrows():
    summary_lines.append(f"  {row['label']:14s}: {int(row['trades']):5d} trades, "
                         f"WR={row['win_rate']:.4f}, EV=${row['ev_per_trade']:8.2f}, "
                         f"PnL=${row['total_pnl']:10.2f}  {row['scr_range']}")
summary_lines.append("")

summary_lines.append("STEP 5: Combined filters")
summary_lines.append("-" * 50)
for _, row in filter_df.iterrows():
    pnl_dd_str = f"{row['pnl_dd_ratio']:.3f}" if not pd.isna(row['pnl_dd_ratio']) else "N/A"
    summary_lines.append(f"  {row['filter']}")
    summary_lines.append(f"    Trades={int(row['trades']):5d}, PnL=${row['total_pnl_net']:10.2f}, "
                         f"DD=${row['max_dd']:10.2f}, PnL/DD={pnl_dd_str}, "
                         f"WR={row['win_rate']:.4f}, EV=${row['ev_per_trade']:8.2f}")
summary_lines.append("")

summary_text = "\n".join(summary_lines)
(OUT / "intraday-transition-summary.txt").write_text(summary_text)
print(f"  Wrote intraday-transition-summary.txt")

print("\nDone.")
