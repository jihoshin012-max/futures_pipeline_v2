"""
Band slope / angle analysis for the win rate regime study.

Computes rolling-mean slope at trade entry and analyzes how slope
interacts with band width and win rate regimes.

Inputs:
  - NQ-250tick-calibration.csv  (127K bars of 250-tick data)
  - winrate-regime-trades.csv   (2685 trades with features)
  - winrate-regime-blocks.csv   (134 blocks with regime labels)

Outputs:
  - band-slope-analysis.csv     (every trade with slope features)
  - band-slope-by-block.csv     (block averages with slope)
  - band-slope-2d-grid.csv      (flat/angled × wide/narrow 2x2)
  - band-slope-expansion.csv    (expanding vs contracting)
  - band-slope-summary.txt      (text summary)
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

# ── paths ──────────────────────────────────────────────────────
BASE = Path(r"c:/Projects/futures_pipeline")
DATA = BASE / "data" / "NQ-250tick-calibration.csv"
OUT  = BASE / "lab" / "output" / "rotational-NQ-scale-detection"

trades_path = OUT / "winrate-regime-trades.csv"
blocks_path = OUT / "winrate-regime-blocks.csv"

# ── 1. Load calibration data and compute rolling mean + slopes ─
print("Loading calibration data...")
cal = pd.read_csv(DATA)
cal.columns = cal.columns.str.strip()

# Mid price as proxy for band midline
cal["mid"] = (cal["High"] + cal["Low"]) / 2.0

# LB=100 rolling mean (same as std100 basis)
cal["mean100"] = cal["mid"].rolling(100, min_periods=100).mean()

# Rolling std (for band width = 1.5 * std100, matching the study)
cal["std100"] = cal["mid"].rolling(100, min_periods=100).std()
cal["band_width_calc"] = 1.5 * cal["std100"]

# Slopes
cal["mean_slope_10"] = (cal["mean100"] - cal["mean100"].shift(10)) / 10.0
cal["mean_slope_20"] = (cal["mean100"] - cal["mean100"].shift(20)) / 20.0
cal["abs_mean_slope_10"] = cal["mean_slope_10"].abs()
cal["abs_mean_slope_20"] = cal["mean_slope_20"].abs()

# Band width change over last 10 bars
cal["band_width_change"] = cal["band_width_calc"] - cal["band_width_calc"].shift(10)

# Parse datetime for matching
cal["dt_str"] = cal["Date"].str.strip() + " " + cal["Time"].str.strip()
# Handle the microsecond format in Time column
cal["datetime"] = pd.to_datetime(cal["dt_str"], format="%m/%d/%Y %H:%M:%S.%f")
cal["date_only"] = cal["datetime"].dt.date
cal["time_only"] = cal["datetime"].dt.time

print(f"  Loaded {len(cal)} bars, {cal['mean_slope_10'].notna().sum()} with slope data")

# ── 2. Load trades and blocks ─────────────────────────────────
trades = pd.read_csv(trades_path)
trades.columns = trades.columns.str.strip()

blocks = pd.read_csv(blocks_path)
blocks.columns = blocks.columns.str.strip()

print(f"  Loaded {len(trades)} trades, {len(blocks)} blocks")

# Parse trade entry datetime
trades["entry_dt"] = pd.to_datetime(
    trades["entry_date"].str.strip() + " " + trades["entry_time"].str.strip(),
    format="%m/%d/%Y %H:%M:%S"
)

# ── 3. Match each trade to nearest calibration bar ─────────────
# For each trade, find the calibration bar with datetime <= entry_dt
# (the bar active at trade entry)
print("Matching trades to calibration bars...")

# Build lookup: for each trade, binary search for the closest bar
cal_sorted = cal.dropna(subset=["mean_slope_10"]).sort_values("datetime").reset_index(drop=True)
cal_dts = cal_sorted["datetime"].values  # numpy array for searchsorted

trade_indices = np.searchsorted(cal_dts, trades["entry_dt"].values, side="right") - 1
# Clamp to valid range
trade_indices = np.clip(trade_indices, 0, len(cal_sorted) - 1)

# Pull slope features for each trade
trades["mean_slope_10"] = cal_sorted["mean_slope_10"].values[trade_indices]
trades["abs_mean_slope_10"] = cal_sorted["abs_mean_slope_10"].values[trade_indices]
trades["mean_slope_20"] = cal_sorted["mean_slope_20"].values[trade_indices]
trades["abs_mean_slope_20"] = cal_sorted["abs_mean_slope_20"].values[trade_indices]
trades["band_width_change"] = cal_sorted["band_width_change"].values[trade_indices]
trades["band_width_calc"] = cal_sorted["band_width_calc"].values[trade_indices]

print(f"  Matched. slope_10 range: [{trades['mean_slope_10'].min():.4f}, {trades['mean_slope_10'].max():.4f}]")
print(f"  abs_slope_10 median: {trades['abs_mean_slope_10'].median():.4f}")
print(f"  band_width_change range: [{trades['band_width_change'].min():.4f}, {trades['band_width_change'].max():.4f}]")

# ── 4. Assign block info to each trade ─────────────────────────
# trades are numbered 1..N; blocks have trade_start, trade_end
trades["trade_num"] = range(1, len(trades) + 1)

def assign_block(trade_num):
    for _, blk in blocks.iterrows():
        if blk["trade_start"] <= trade_num <= blk["trade_end"]:
            return blk["block_num"], blk["class"]
    return np.nan, "UNKNOWN"

block_nums = []
block_classes = []
for tn in trades["trade_num"]:
    bn, bc = assign_block(tn)
    block_nums.append(bn)
    block_classes.append(bc)

trades["block_num"] = block_nums
trades["block_class"] = block_classes

# ── 5. Save trade-level output ─────────────────────────────────
trade_out_cols = [
    "entry_date", "entry_time", "exit_date", "exit_time",
    "direction", "entry_price", "exit_price", "exit_type",
    "pnl_pts", "pnl_dollar", "win",
    "std100", "avg_range_10", "band_width",
    "mean_slope_10", "abs_mean_slope_10",
    "mean_slope_20", "abs_mean_slope_20",
    "band_width_change", "band_width_calc",
    "block_num", "block_class"
]
trades[trade_out_cols].to_csv(OUT / "band-slope-analysis.csv", index=False)
print(f"  Wrote band-slope-analysis.csv ({len(trades)} trades)")

# ── 6. Block averages with slope ───────────────────────────────
block_agg = trades.groupby("block_num").agg(
    block_class=("block_class", "first"),
    trades=("win", "count"),
    wins=("win", "sum"),
    win_rate=("win", "mean"),
    total_pnl=("pnl_dollar", "sum"),
    avg_std100=("std100", "mean"),
    avg_range_10=("avg_range_10", "mean"),
    avg_band_width=("band_width", "mean"),
    avg_abs_slope_10=("abs_mean_slope_10", "mean"),
    avg_abs_slope_20=("abs_mean_slope_20", "mean"),
    avg_band_width_change=("band_width_change", "mean"),
    avg_mean_slope_10=("mean_slope_10", "mean"),
).reset_index()

block_agg["win_rate"] = (block_agg["win_rate"] * 100).round(1)

block_agg.to_csv(OUT / "band-slope-by-block.csv", index=False)
print(f"  Wrote band-slope-by-block.csv ({len(block_agg)} blocks)")

# Block-class summary
print("\n=== Block Class Averages ===")
class_summary = block_agg.groupby("block_class").agg(
    n_blocks=("block_num", "count"),
    avg_win_rate=("win_rate", "mean"),
    avg_pnl=("total_pnl", "mean"),
    avg_abs_slope_10=("avg_abs_slope_10", "mean"),
    avg_abs_slope_20=("avg_abs_slope_20", "mean"),
    avg_band_width=("avg_band_width", "mean"),
    avg_bw_change=("avg_band_width_change", "mean"),
    avg_range_10=("avg_range_10", "mean"),
).round(4)
print(class_summary.to_string())

# ── 7. 2D grid: flat/angled × wide/narrow ─────────────────────
median_abs_slope = trades["abs_mean_slope_10"].median()
median_bw = trades["band_width"].median()

print(f"\n=== 2D Grid Splits ===")
print(f"  abs_mean_slope_10 median: {median_abs_slope:.4f}")
print(f"  band_width median: {median_bw:.4f}")

trades["slope_cat"] = np.where(trades["abs_mean_slope_10"] <= median_abs_slope, "FLAT", "ANGLED")
trades["width_cat"] = np.where(trades["band_width"] <= median_bw, "NARROW", "WIDE")
trades["quadrant"] = trades["slope_cat"] + "_" + trades["width_cat"]

grid = trades.groupby("quadrant").agg(
    trade_count=("win", "count"),
    wins=("win", "sum"),
    win_rate=("win", "mean"),
    avg_pnl_dollar=("pnl_dollar", "mean"),
    total_pnl=("pnl_dollar", "sum"),
    avg_abs_slope_10=("abs_mean_slope_10", "mean"),
    avg_band_width=("band_width", "mean"),
    avg_std100=("std100", "mean"),
).reset_index()
grid["win_rate"] = (grid["win_rate"] * 100).round(2)
grid["avg_pnl_dollar"] = grid["avg_pnl_dollar"].round(2)
grid["total_pnl"] = grid["total_pnl"].round(2)

grid.to_csv(OUT / "band-slope-2d-grid.csv", index=False)
print(f"\n=== 2x2 Grid: Flat/Angled × Wide/Narrow ===")
print(grid.to_string(index=False))

# ── 8. Expansion / contraction analysis ────────────────────────
# Three bins: contracting, flat (near zero), expanding
bwc_abs_median = trades["band_width_change"].abs().median()
# Use a small threshold near zero for "flat" — 10% of abs median
flat_threshold = bwc_abs_median * 0.25

def classify_expansion(v):
    if v > flat_threshold:
        return "EXPANDING"
    elif v < -flat_threshold:
        return "CONTRACTING"
    else:
        return "FLAT_BW"

trades["expansion_cat"] = trades["band_width_change"].apply(classify_expansion)

expansion = trades.groupby("expansion_cat").agg(
    trade_count=("win", "count"),
    wins=("win", "sum"),
    win_rate=("win", "mean"),
    avg_pnl_dollar=("pnl_dollar", "mean"),
    total_pnl=("pnl_dollar", "sum"),
    avg_band_width=("band_width", "mean"),
    avg_abs_slope_10=("abs_mean_slope_10", "mean"),
    avg_bw_change=("band_width_change", "mean"),
).reset_index()
expansion["win_rate"] = (expansion["win_rate"] * 100).round(2)
expansion["avg_pnl_dollar"] = expansion["avg_pnl_dollar"].round(2)
expansion["total_pnl"] = expansion["total_pnl"].round(2)

expansion.to_csv(OUT / "band-slope-expansion.csv", index=False)
print(f"\n=== Expansion / Contraction ===")
print(f"  flat_threshold: ±{flat_threshold:.4f}")
print(expansion.to_string(index=False))

# ── 9. Also: 2D grid by block class (HIGH/MED/LOW) ────────────
print("\n=== Quadrant breakdown by block class ===")
quad_class = trades.groupby(["quadrant", "block_class"]).agg(
    trade_count=("win", "count"),
    win_rate=("win", "mean"),
    avg_pnl=("pnl_dollar", "mean"),
).reset_index()
quad_class["win_rate"] = (quad_class["win_rate"] * 100).round(2)
quad_class["avg_pnl"] = quad_class["avg_pnl"].round(2)
print(quad_class.to_string(index=False))

# ── 10. Summary text ───────────────────────────────────────────
summary_lines = []
summary_lines.append("BAND SLOPE / ANGLE ANALYSIS — SUMMARY")
summary_lines.append("=" * 50)
summary_lines.append(f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
summary_lines.append(f"Trades: {len(trades)}")
summary_lines.append(f"Blocks: {len(blocks)}")
summary_lines.append("")

summary_lines.append("METHODOLOGY")
summary_lines.append("-" * 40)
summary_lines.append("mean_slope_10 = (mean100[i] - mean100[i-10]) / 10")
summary_lines.append("mean_slope_20 = (mean100[i] - mean100[i-20]) / 20")
summary_lines.append("band_width_change = band_width[i] - band_width[i-10]")
summary_lines.append(f"2D grid splits: abs_slope_10 median = {median_abs_slope:.4f}, band_width median = {median_bw:.4f}")
summary_lines.append(f"Expansion flat threshold: ±{flat_threshold:.4f}")
summary_lines.append("")

summary_lines.append("BLOCK CLASS AVERAGES (slope conditions)")
summary_lines.append("-" * 40)
for cls in ["HIGH", "MEDIUM", "LOW"]:
    if cls in class_summary.index:
        row = class_summary.loc[cls]
        summary_lines.append(f"  {cls}:")
        summary_lines.append(f"    n_blocks        = {int(row['n_blocks'])}")
        summary_lines.append(f"    avg_win_rate    = {row['avg_win_rate']:.1f}%")
        summary_lines.append(f"    avg_abs_slope_10 = {row['avg_abs_slope_10']:.4f}")
        summary_lines.append(f"    avg_abs_slope_20 = {row['avg_abs_slope_20']:.4f}")
        summary_lines.append(f"    avg_band_width  = {row['avg_band_width']:.2f}")
        summary_lines.append(f"    avg_bw_change   = {row['avg_bw_change']:.4f}")
        summary_lines.append(f"    avg_range_10    = {row['avg_range_10']:.2f}")

summary_lines.append("")
summary_lines.append("2x2 GRID: FLAT/ANGLED × WIDE/NARROW")
summary_lines.append("-" * 40)
for _, row in grid.iterrows():
    summary_lines.append(f"  {row['quadrant']:20s}  n={int(row['trade_count']):4d}  WR={row['win_rate']:5.1f}%  EV=${row['avg_pnl_dollar']:8.2f}  Total=${row['total_pnl']:10.2f}")

summary_lines.append("")
summary_lines.append("EXPANSION / CONTRACTION")
summary_lines.append("-" * 40)
for _, row in expansion.iterrows():
    summary_lines.append(f"  {row['expansion_cat']:15s}  n={int(row['trade_count']):4d}  WR={row['win_rate']:5.1f}%  EV=${row['avg_pnl_dollar']:8.2f}  Total=${row['total_pnl']:10.2f}")

summary_lines.append("")
summary_lines.append("KEY QUESTION: What band conditions produce HIGH win rate blocks?")
summary_lines.append("-" * 50)

# Determine answer from data
# Compare HIGH vs LOW block slope/width
if "HIGH" in class_summary.index and "LOW" in class_summary.index:
    h = class_summary.loc["HIGH"]
    l = class_summary.loc["LOW"]

    slope_diff = h["avg_abs_slope_10"] - l["avg_abs_slope_10"]
    bw_diff = h["avg_band_width"] - l["avg_band_width"]
    bwc_diff = h["avg_bw_change"] - l["avg_bw_change"]

    summary_lines.append(f"  HIGH vs LOW abs_slope_10 diff: {slope_diff:+.4f} ({'HIGH steeper' if slope_diff > 0 else 'HIGH flatter'})")
    summary_lines.append(f"  HIGH vs LOW band_width diff:   {bw_diff:+.2f} ({'HIGH wider' if bw_diff > 0 else 'HIGH narrower'})")
    summary_lines.append(f"  HIGH vs LOW bw_change diff:    {bwc_diff:+.4f} ({'HIGH expanding more' if bwc_diff > 0 else 'HIGH contracting more'})")

# Find the best quadrant
best_quad = grid.loc[grid["win_rate"].idxmax()]
worst_quad = grid.loc[grid["win_rate"].idxmin()]
summary_lines.append("")
summary_lines.append(f"  Best quadrant:  {best_quad['quadrant']}  WR={best_quad['win_rate']:.1f}%  EV=${best_quad['avg_pnl_dollar']:.2f}")
summary_lines.append(f"  Worst quadrant: {worst_quad['quadrant']}  WR={worst_quad['win_rate']:.1f}%  EV=${worst_quad['avg_pnl_dollar']:.2f}")

# Classify answer
summary_lines.append("")
summary_lines.append("ANSWER:")
if best_quad["quadrant"] == "FLAT_WIDE":
    summary_lines.append("  -> A) Wide AND flat: high volatility range-bound produces highest win rates")
elif best_quad["quadrant"] == "ANGLED_WIDE":
    summary_lines.append("  -> B) Wide AND angled: trending with wide bands produces highest win rates")
elif best_quad["quadrant"] == "FLAT_NARROW":
    summary_lines.append("  -> Flat AND narrow: low-vol, range-bound produces highest win rates")
elif best_quad["quadrant"] == "ANGLED_NARROW":
    summary_lines.append("  -> Angled AND narrow: trending with narrow bands produces highest win rates")

# Also note the best EV quadrant if different from best WR
best_ev_quad = grid.loc[grid["avg_pnl_dollar"].idxmax()]
if best_ev_quad["quadrant"] != best_quad["quadrant"]:
    summary_lines.append(f"  (Note: best EV is {best_ev_quad['quadrant']} at ${best_ev_quad['avg_pnl_dollar']:.2f}/trade)")

summary_text = "\n".join(summary_lines)
(OUT / "band-slope-summary.txt").write_text(summary_text, encoding="utf-8")
print(f"\n  Wrote band-slope-summary.txt")
print("\n" + summary_text)
