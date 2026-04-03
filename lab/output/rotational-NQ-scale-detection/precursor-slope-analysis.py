"""
Precursor analysis combining bar range and band slope conditions.

For each 20-trade block, compute precursor conditions from the 10 (and 5) trades
preceding it, then compare precursor profiles by block type (HIGH/MEDIUM/LOW).
Also tests combined filters using both range and slope conditions.
"""

import pandas as pd
import numpy as np
from pathlib import Path

OUT = Path(r"c:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")

# ── Load data ──────────────────────────────────────────────────────────────
trades = pd.read_csv(OUT / "band-slope-analysis.csv")
blocks = pd.read_csv(OUT / "winrate-regime-blocks.csv")

# Rename band_width_change to bw_change_10 for clarity
trades.rename(columns={"band_width_change": "bw_change_10"}, inplace=True)

# Trade-level features available for precursor computation
TRADE_FEATURES = [
    "std100", "avg_range_10", "band_width",
    "abs_mean_slope_10", "abs_mean_slope_20", "bw_change_10",
    "mean_slope_10", "mean_slope_20",
]

print(f"Trades: {len(trades)}, Blocks: {len(blocks)}")
print(f"Block classes: {blocks['class'].value_counts().to_dict()}")


# ══════════════════════════════════════════════════════════════════════════
# STEP 1: Compute precursor conditions for each block
# ══════════════════════════════════════════════════════════════════════════

precursor_rows = []

for _, blk in blocks.iterrows():
    block_num = int(blk["block_num"])
    trade_start = int(blk["trade_start"])  # 1-based trade index
    block_class = blk["class"]

    # Trade indices are 1-based in blocks; pandas rows are 0-based
    # trade_start=21 means block starts at trades.iloc[20]
    idx_start = trade_start - 1  # 0-based start of this block

    # 10-trade precursor: trades [idx_start-10, idx_start)
    if idx_start >= 10:
        pre10 = trades.iloc[idx_start - 10: idx_start]
        row = {"block_num": block_num, "block_class": block_class, "precursor_window": 10}
        for feat in TRADE_FEATURES:
            row[f"pre_{feat}"] = pre10[feat].mean()
        row["precursor_win_rate"] = pre10["win"].mean() * 100
        row["precursor_avg_pnl"] = pre10["pnl_dollar"].mean()
        precursor_rows.append(row)

    # 5-trade precursor: trades [idx_start-5, idx_start)
    if idx_start >= 5:
        pre5 = trades.iloc[idx_start - 5: idx_start]
        row = {"block_num": block_num, "block_class": block_class, "precursor_window": 5}
        for feat in TRADE_FEATURES:
            row[f"pre_{feat}"] = pre5[feat].mean()
        row["precursor_win_rate"] = pre5["win"].mean() * 100
        row["precursor_avg_pnl"] = pre5["pnl_dollar"].mean()
        precursor_rows.append(row)

precursors = pd.DataFrame(precursor_rows)
print(f"\nPrecursor records: {len(precursors)}")


# ══════════════════════════════════════════════════════════════════════════
# STEP 2: Compare precursor conditions by block type
# ══════════════════════════════════════════════════════════════════════════

pre_cols = [c for c in precursors.columns if c.startswith("pre_")]
pre_cols.append("precursor_win_rate")
pre_cols.append("precursor_avg_pnl")

results_step2 = []

for window in [10, 5]:
    subset = precursors[precursors["precursor_window"] == window]
    for cls in ["HIGH", "MEDIUM", "LOW"]:
        cls_data = subset[subset["block_class"] == cls]
        if len(cls_data) == 0:
            continue
        row = {"precursor_window": window, "block_class": cls, "n_blocks": len(cls_data)}
        for col in pre_cols:
            row[col] = cls_data[col].mean()
        results_step2.append(row)

step2_df = pd.DataFrame(results_step2)

# Compute separation: (HIGH_mean - LOW_mean) / pooled_std for ranking
sep_rows = []
for window in [10, 5]:
    subset = precursors[precursors["precursor_window"] == window]
    high = subset[subset["block_class"] == "HIGH"]
    low = subset[subset["block_class"] == "LOW"]
    if len(high) == 0 or len(low) == 0:
        continue
    for col in pre_cols:
        h_mean = high[col].mean()
        l_mean = low[col].mean()
        pooled_std = subset[col].std()
        sep = (h_mean - l_mean) / pooled_std if pooled_std > 0 else 0
        sep_rows.append({
            "precursor_window": window,
            "condition": col,
            "high_mean": h_mean,
            "low_mean": l_mean,
            "medium_mean": subset[subset["block_class"] == "MEDIUM"][col].mean(),
            "diff_high_low": h_mean - l_mean,
            "separation_z": sep,
            "abs_separation_z": abs(sep),
        })

sep_df = pd.DataFrame(sep_rows).sort_values(["precursor_window", "abs_separation_z"], ascending=[True, False])

# Save step 2
step2_df.to_csv(OUT / "precursor-slope-conditions.csv", index=False)
print("\n── STEP 2: Precursor conditions by block type ──")
print(step2_df.to_string(index=False))

print("\n── Condition ranking by HIGH-LOW separation (10-trade precursor) ──")
s10 = sep_df[sep_df["precursor_window"] == 10]
print(s10[["condition", "high_mean", "medium_mean", "low_mean", "diff_high_low", "separation_z"]].to_string(index=False))


# ══════════════════════════════════════════════════════════════════════════
# STEP 3: 2D precursor analysis (quadrant distribution)
# ══════════════════════════════════════════════════════════════════════════

# Use 10-trade precursor window
pre10 = precursors[precursors["precursor_window"] == 10].copy()

# Compute medians for splits
median_abs_slope = pre10["pre_abs_mean_slope_10"].median()
median_bw = pre10["pre_band_width"].median()

print(f"\nMedian abs_mean_slope_10 (precursor): {median_abs_slope:.4f}")
print(f"Median band_width (precursor): {median_bw:.4f}")

def classify_quadrant(row):
    flat = row["pre_abs_mean_slope_10"] < median_abs_slope
    wide = row["pre_band_width"] >= median_bw
    if flat and wide:
        return "FLAT_WIDE"
    elif flat and not wide:
        return "FLAT_NARROW"
    elif not flat and wide:
        return "ANGLED_WIDE"
    else:
        return "ANGLED_NARROW"

pre10["quadrant"] = pre10.apply(classify_quadrant, axis=1)

# Quadrant distribution by block class
quad_rows = []
for cls in ["HIGH", "MEDIUM", "LOW"]:
    cls_data = pre10[pre10["block_class"] == cls]
    n = len(cls_data)
    if n == 0:
        continue
    for q in ["FLAT_WIDE", "FLAT_NARROW", "ANGLED_WIDE", "ANGLED_NARROW"]:
        count = (cls_data["quadrant"] == q).sum()
        quad_rows.append({
            "block_class": cls,
            "quadrant": q,
            "count": count,
            "pct": round(count / n * 100, 1) if n > 0 else 0,
            "n_blocks": n,
        })

quad_df = pd.DataFrame(quad_rows)
quad_df.to_csv(OUT / "precursor-slope-2d.csv", index=False)

print("\n── STEP 3: 2D quadrant distribution before HIGH vs LOW blocks ──")
for cls in ["HIGH", "MEDIUM", "LOW"]:
    print(f"\n  {cls}:")
    sub = quad_df[quad_df["block_class"] == cls]
    for _, r in sub.iterrows():
        print(f"    {r['quadrant']}: {r['count']}/{r['n_blocks']} ({r['pct']}%)")


# ══════════════════════════════════════════════════════════════════════════
# STEP 4: Precursor trajectory (10 trades before each block type)
# ══════════════════════════════════════════════════════════════════════════

trajectory_features = ["avg_range_10", "band_width", "abs_mean_slope_10", "bw_change_10", "std100"]
traj_rows = []

for cls in ["HIGH", "LOW"]:
    cls_blocks = blocks[blocks["class"] == cls]
    for offset in range(-10, 0):  # -10 = earliest, -1 = just before block
        vals = {feat: [] for feat in trajectory_features}
        vals["win"] = []
        for _, blk in cls_blocks.iterrows():
            idx = int(blk["trade_start"]) - 1 + offset  # 0-based
            if 0 <= idx < len(trades):
                for feat in trajectory_features:
                    vals[feat].append(trades.iloc[idx][feat])
                vals["win"].append(trades.iloc[idx]["win"])

        row = {"block_class": cls, "offset": offset}
        for feat in trajectory_features:
            row[f"avg_{feat}"] = np.mean(vals[feat]) if vals[feat] else np.nan
        row["win_rate"] = np.mean(vals["win"]) * 100 if vals["win"] else np.nan
        row["n_samples"] = len(vals["win"])
        traj_rows.append(row)

traj_df = pd.DataFrame(traj_rows)
traj_df.to_csv(OUT / "precursor-slope-trajectory.csv", index=False)

print("\n── STEP 4: Precursor trajectory ──")
for cls in ["HIGH", "LOW"]:
    print(f"\n  {cls} blocks — trajectory from -10 to -1:")
    sub = traj_df[traj_df["block_class"] == cls]
    print(sub[["offset", "avg_avg_range_10", "avg_band_width", "avg_abs_mean_slope_10",
               "avg_bw_change_10", "win_rate"]].to_string(index=False))

# Compute slope of trajectory (linear trend over the 10 positions)
print("\n  Trajectory trends (slope of linear fit across -10 to -1):")
for cls in ["HIGH", "LOW"]:
    sub = traj_df[traj_df["block_class"] == cls].sort_values("offset")
    x = np.arange(10)
    for feat in ["avg_avg_range_10", "avg_band_width", "avg_abs_mean_slope_10", "avg_bw_change_10", "win_rate"]:
        y = sub[feat].values
        if len(y) == 10 and not np.any(np.isnan(y)):
            slope = np.polyfit(x, y, 1)[0]
            print(f"    {cls} {feat}: slope = {slope:.4f}")


# ══════════════════════════════════════════════════════════════════════════
# STEP 5: Combined precursor filters
# ══════════════════════════════════════════════════════════════════════════

# Compute rolling precursor conditions for each trade
# For each trade at index i, compute avg of trades [i-10, i) for features

# Full dataset medians for filter thresholds
full_median_abs_slope_10 = trades["abs_mean_slope_10"].median()
full_median_bw = trades["band_width"].median()

print(f"\nFull dataset median abs_mean_slope_10: {full_median_abs_slope_10:.4f}")
print(f"Full dataset median band_width: {full_median_bw:.4f}")

# Compute rolling 10-trade averages for filter conditions
trades["roll_avg_range_10"] = trades["avg_range_10"].rolling(10, min_periods=10).mean().shift(1)
trades["roll_abs_slope_10"] = trades["abs_mean_slope_10"].rolling(10, min_periods=10).mean().shift(1)
trades["roll_bw"] = trades["band_width"].rolling(10, min_periods=10).mean().shift(1)
trades["roll_bw_change"] = trades["bw_change_10"].rolling(10, min_periods=10).mean().shift(1)

# Also compute per-trade conditions (not rolling) for some filters
# Filter C needs per-trade quadrant classification
trades["is_flat"] = trades["abs_mean_slope_10"] < full_median_abs_slope_10
trades["is_wide"] = trades["band_width"] >= full_median_bw

# Drop trades without enough lookback
valid = trades.dropna(subset=["roll_avg_range_10"]).copy()
print(f"\nValid trades (with 10-trade lookback): {len(valid)}")

# Compute rolling median for filter thresholds from valid data
roll_median_abs_slope = valid["roll_abs_slope_10"].median()
roll_median_bw = valid["roll_bw"].median()
roll_median_range = valid["roll_avg_range_10"].median()

def compute_filter_stats(mask, label):
    """Compute PnL stats for trades matching the filter mask."""
    filtered = valid[mask]
    excluded = valid[~mask]
    n = len(filtered)
    if n == 0:
        return {"filter": label, "trades": 0, "pnl": 0, "max_dd": 0, "pnl_dd": 0,
                "win_rate": 0, "avg_pnl": 0, "excluded_trades": len(excluded),
                "excluded_pnl": 0, "excluded_wr": 0}

    pnl = filtered["pnl_dollar"].sum()
    cumulative = filtered["pnl_dollar"].cumsum()
    running_max = cumulative.cummax()
    drawdown = cumulative - running_max
    max_dd = drawdown.min()
    pnl_dd = pnl / abs(max_dd) if max_dd < 0 else float("inf")
    wr = filtered["win"].mean() * 100

    ex_pnl = excluded["pnl_dollar"].sum() if len(excluded) > 0 else 0
    ex_wr = excluded["win"].mean() * 100 if len(excluded) > 0 else 0

    return {
        "filter": label,
        "trades": n,
        "pnl": round(pnl, 2),
        "max_dd": round(max_dd, 2),
        "pnl_dd": round(pnl_dd, 3),
        "win_rate": round(wr, 1),
        "avg_pnl": round(pnl / n, 2),
        "excluded_trades": len(excluded),
        "excluded_pnl": round(ex_pnl, 2),
        "excluded_wr": round(ex_wr, 1),
    }

# Baseline: no filter
baseline_mask = pd.Series(True, index=valid.index)

# Filter A: precursor avg_range_10 >= 9.5 AND abs_mean_slope_10 < median
filter_a_mask = (valid["roll_avg_range_10"] >= 9.5) & (valid["roll_abs_slope_10"] < roll_median_abs_slope)

# Filter B: band_width >= median AND bw_change_10 < 0 (wide + contracting)
filter_b_mask = (valid["roll_bw"] >= roll_median_bw) & (valid["roll_bw_change"] < 0)

# Filter C: current trade in FLAT_WIDE quadrant
filter_c_mask = valid["is_flat"] & valid["is_wide"]

filter_results = []
filter_results.append(compute_filter_stats(baseline_mask, "Baseline (no filter)"))
filter_results.append(compute_filter_stats(filter_a_mask, "A: range>=9.5 & slope<median"))
filter_results.append(compute_filter_stats(filter_b_mask, "B: wide & contracting"))
filter_results.append(compute_filter_stats(filter_c_mask, "C: FLAT_WIDE quadrant"))

filter_df = pd.DataFrame(filter_results)
filter_df.to_csv(OUT / "precursor-slope-filters.csv", index=False)

print("\n── STEP 5: Combined filter results ──")
print(filter_df.to_string(index=False))


# ══════════════════════════════════════════════════════════════════════════
# SUMMARY
# ══════════════════════════════════════════════════════════════════════════

summary_lines = []
summary_lines.append("PRECURSOR SLOPE ANALYSIS SUMMARY")
summary_lines.append("=" * 60)
summary_lines.append(f"Total trades: {len(trades)}")
summary_lines.append(f"Total blocks: {len(blocks)}")
summary_lines.append(f"  HIGH (WR>=60%): {(blocks['class']=='HIGH').sum()}")
summary_lines.append(f"  MEDIUM (40-60%): {(blocks['class']=='MEDIUM').sum()}")
summary_lines.append(f"  LOW (WR<40%): {(blocks['class']=='LOW').sum()}")
summary_lines.append("")

# Top conditions separating HIGH from LOW
summary_lines.append("TOP CONDITIONS SEPARATING PRE-HIGH vs PRE-LOW (10-trade precursor)")
summary_lines.append("-" * 60)
s10 = sep_df[sep_df["precursor_window"] == 10].head(10)
for _, r in s10.iterrows():
    summary_lines.append(
        f"  {r['condition']:30s}  HIGH={r['high_mean']:8.3f}  LOW={r['low_mean']:8.3f}  "
        f"sep_z={r['separation_z']:+.3f}"
    )

summary_lines.append("")
summary_lines.append("QUADRANT DISTRIBUTION (precursor conditions before blocks)")
summary_lines.append("-" * 60)
for cls in ["HIGH", "LOW"]:
    summary_lines.append(f"  Before {cls} blocks:")
    sub = quad_df[quad_df["block_class"] == cls]
    for _, r in sub.iterrows():
        summary_lines.append(f"    {r['quadrant']:16s}: {r['pct']:5.1f}%")

summary_lines.append("")
summary_lines.append("TRAJECTORY TRENDS (linear slope from trade -10 to -1)")
summary_lines.append("-" * 60)
for cls in ["HIGH", "LOW"]:
    sub = traj_df[traj_df["block_class"] == cls].sort_values("offset")
    x = np.arange(10)
    summary_lines.append(f"  Before {cls} blocks:")
    for feat in ["avg_avg_range_10", "avg_band_width", "avg_abs_mean_slope_10", "avg_bw_change_10", "win_rate"]:
        y = sub[feat].values
        if len(y) == 10 and not np.any(np.isnan(y)):
            slope = np.polyfit(x, y, 1)[0]
            first = y[0]
            last = y[-1]
            summary_lines.append(f"    {feat:30s}: {first:.3f} -> {last:.3f}  (slope={slope:+.4f})")

summary_lines.append("")
summary_lines.append("COMBINED FILTER RESULTS")
summary_lines.append("-" * 60)
for _, r in filter_df.iterrows():
    summary_lines.append(
        f"  {r['filter']:35s}  trades={r['trades']:5d}  PnL={r['pnl']:10.2f}  "
        f"DD={r['max_dd']:10.2f}  PnL/DD={r['pnl_dd']:6.3f}  WR={r['win_rate']:5.1f}%"
    )

# Add excluded trade stats
summary_lines.append("")
summary_lines.append("EXCLUDED TRADE STATS")
summary_lines.append("-" * 60)
for _, r in filter_df.iterrows():
    if r["filter"] != "Baseline (no filter)":
        summary_lines.append(
            f"  {r['filter']:35s}  excluded={r['excluded_trades']:5d}  "
            f"excl_PnL={r['excluded_pnl']:10.2f}  excl_WR={r['excluded_wr']:5.1f}%"
        )

summary_text = "\n".join(summary_lines)
(OUT / "precursor-slope-summary.txt").write_text(summary_text, encoding="utf-8")
print(f"\n{summary_text}")

print("\n\nFiles written:")
for f in ["precursor-slope-conditions.csv", "precursor-slope-2d.csv",
          "precursor-slope-trajectory.csv", "precursor-slope-filters.csv",
          "precursor-slope-summary.txt"]:
    print(f"  {OUT / f}")
