"""
Precursor condition analysis for win rate regime blocks.

For each block of 20 trades, looks at the trades PRECEDING the block
to identify leading indicators of high vs low win rate regimes.
"""

import pandas as pd
import numpy as np
from pathlib import Path

OUT_DIR = Path(__file__).parent

# --- Load data ---
trades = pd.read_csv(OUT_DIR / "winrate-regime-trades.csv")
blocks = pd.read_csv(OUT_DIR / "winrate-regime-blocks.csv")

CONDITION_COLS = [
    "std100", "avg_range_10", "avg_duration_10", "range_bw_ratio",
    "band_width", "ema_position", "price_position", "stddev_ratio", "hour"
]

# Ensure numeric
for col in CONDITION_COLS:
    trades[col] = pd.to_numeric(trades[col], errors="coerce")

# trades are 1-indexed in the blocks file (trade_start, trade_end)
# but 0-indexed in the DataFrame
# Block 1: trade_start=1, trade_end=20 -> DataFrame rows 0..19

# ============================================================
# STEP 1 & 2: Precursor conditions (10-trade and 5-trade)
# ============================================================

def compute_precursors(blocks_df, trades_df, lookback):
    """Compute average conditions of `lookback` trades before each block."""
    records = []
    for _, block in blocks_df.iterrows():
        block_num = int(block["block_num"])
        trade_start = int(block["trade_start"])  # 1-indexed

        # Preceding trade indices (1-indexed)
        pre_end = trade_start - 1
        pre_start = trade_start - lookback

        if pre_start < 1:
            continue  # not enough preceding trades

        # Convert to 0-indexed DataFrame rows
        pre_trades = trades_df.iloc[pre_start - 1 : pre_end]

        if len(pre_trades) < lookback:
            continue

        row = {"block_num": block_num, "block_class": block["class"],
               "block_win_rate": block["win_rate"], "block_pnl": block["total_pnl"]}

        # Precursor win rate
        row["precursor_win_rate"] = pre_trades["win"].mean() * 100

        # Precursor conditions
        for col in CONDITION_COLS:
            vals = pre_trades[col].dropna()
            row[f"pre_{col}"] = vals.mean() if len(vals) > 0 else np.nan

        records.append(row)

    return pd.DataFrame(records)


pre10 = compute_precursors(blocks, trades, 10)
pre5 = compute_precursors(blocks, trades, 5)

# Averages by block class
def summarize_by_class(pre_df, lookback_label):
    cond_cols = [c for c in pre_df.columns if c.startswith("pre_")] + ["precursor_win_rate"]
    summary = pre_df.groupby("block_class")[cond_cols].mean()
    summary["lookback"] = lookback_label
    summary["n_blocks"] = pre_df.groupby("block_class").size()
    return summary

sum10 = summarize_by_class(pre10, "10-trade")
sum5 = summarize_by_class(pre5, "5-trade")

precursor_summary = pd.concat([sum10, sum5])

# Compute differences (HIGH - LOW)
def compute_diffs(summary_df, lookback_label):
    sub = summary_df[summary_df["lookback"] == lookback_label]
    if "HIGH" not in sub.index or "LOW" not in sub.index:
        return None
    numeric_cols = sub.select_dtypes(include=[np.number]).columns
    diff = sub.loc["HIGH", numeric_cols] - sub.loc["LOW", numeric_cols]
    diff["lookback"] = lookback_label
    diff["block_class"] = "HIGH_minus_LOW"
    return diff

diff10 = compute_diffs(precursor_summary, "10-trade")
diff5 = compute_diffs(precursor_summary, "5-trade")

# Save precursor conditions
precursor_summary.to_csv(OUT_DIR / "precursor-conditions.csv")

# ============================================================
# STEP 4: Transition analysis
# ============================================================

def find_transitions(blocks_df, pre10_df):
    """Find HIGH->LOW and LOW->HIGH transitions within 2 blocks."""
    transitions = []
    classes = blocks_df["class"].tolist()
    block_nums = blocks_df["block_num"].tolist()

    for i in range(len(classes)):
        for gap in range(1, 3):  # within 2 blocks
            j = i + gap
            if j >= len(classes):
                continue

            from_class = classes[i]
            to_class = classes[j]

            if (from_class == "HIGH" and to_class == "LOW") or \
               (from_class == "LOW" and to_class == "HIGH"):

                bn_from = block_nums[i]
                bn_to = block_nums[j]

                # Get block-level conditions
                b_from = blocks_df[blocks_df["block_num"] == bn_from].iloc[0]
                b_to = blocks_df[blocks_df["block_num"] == bn_to].iloc[0]

                row = {
                    "from_block": bn_from, "to_block": bn_to,
                    "from_class": from_class, "to_class": to_class,
                    "gap": gap,
                    "from_wr": b_from["win_rate"], "to_wr": b_to["win_rate"],
                    "from_pnl": b_from["total_pnl"], "to_pnl": b_to["total_pnl"],
                }

                for col in CONDITION_COLS:
                    row[f"from_{col}"] = b_from[col]
                    row[f"to_{col}"] = b_to[col]
                    row[f"delta_{col}"] = b_to[col] - b_from[col]

                transitions.append(row)

    return pd.DataFrame(transitions)

trans = find_transitions(blocks, pre10)

# Summarize transitions by type
trans_summary_rows = []
for ttype in [("HIGH", "LOW"), ("LOW", "HIGH")]:
    sub = trans[(trans["from_class"] == ttype[0]) & (trans["to_class"] == ttype[1])]
    if len(sub) == 0:
        continue
    row = {"transition": f"{ttype[0]}->{ttype[1]}", "count": len(sub)}
    delta_cols = [c for c in sub.columns if c.startswith("delta_")]
    for dc in delta_cols:
        row[f"avg_{dc}"] = sub[dc].mean()
    trans_summary_rows.append(row)

trans_summary = pd.DataFrame(trans_summary_rows)

# Save transitions
trans.to_csv(OUT_DIR / "precursor-transitions.csv", index=False)

# ============================================================
# STEP 5: Precursor as filter
# ============================================================

# Identify top differentiating precursors
cond_cols_pre = [c for c in pre10.columns if c.startswith("pre_")] + ["precursor_win_rate"]

# Compute effect sizes (HIGH mean - LOW mean) / pooled std
high_pre = pre10[pre10["block_class"] == "HIGH"]
low_pre = pre10[pre10["block_class"] == "LOW"]
med_pre = pre10[pre10["block_class"] == "MEDIUM"]

effect_sizes = {}
for col in cond_cols_pre:
    h_vals = high_pre[col].dropna()
    l_vals = low_pre[col].dropna()
    if len(h_vals) < 2 or len(l_vals) < 2:
        continue
    pooled_std = np.sqrt((h_vals.std()**2 + l_vals.std()**2) / 2)
    if pooled_std > 0:
        effect_sizes[col] = (h_vals.mean() - l_vals.mean()) / pooled_std
    else:
        effect_sizes[col] = 0

effect_df = pd.DataFrame([
    {"condition": k, "high_mean": high_pre[k].mean(), "low_mean": low_pre[k].mean(),
     "effect_size": v, "abs_effect": abs(v)}
    for k, v in effect_sizes.items()
]).sort_values("abs_effect", ascending=False)

# Pick top 3 conditions
top_conditions = effect_df.head(3)["condition"].tolist()

# For each top condition, test threshold filters
filter_results = []

for cond in top_conditions:
    h_mean = high_pre[cond].mean()
    l_mean = low_pre[cond].mean()

    # Try thresholds between low and high means
    if h_mean > l_mean:
        # Higher value -> HIGH blocks, filter OUT when below threshold
        direction = "above"
        thresholds = np.linspace(l_mean, h_mean, 20)
    else:
        # Lower value -> HIGH blocks, filter OUT when above threshold
        direction = "below"
        thresholds = np.linspace(h_mean, l_mean, 20)

    for thresh in thresholds:
        if direction == "above":
            # Trade only when precursor condition >= threshold
            kept = pre10[pre10[cond] >= thresh]
            filtered = pre10[pre10[cond] < thresh]
        else:
            kept = pre10[pre10[cond] <= thresh]
            filtered = pre10[pre10[cond] > thresh]

        kept_high = len(kept[kept["block_class"] == "HIGH"])
        kept_low = len(kept[kept["block_class"] == "LOW"])
        kept_med = len(kept[kept["block_class"] == "MEDIUM"])

        filtered_high = len(filtered[filtered["block_class"] == "HIGH"])
        filtered_low = len(filtered[filtered["block_class"] == "LOW"])
        filtered_med = len(filtered[filtered["block_class"] == "MEDIUM"])

        total_high = len(high_pre)
        total_low = len(low_pre)

        kept_pnl = kept["block_pnl"].sum()
        filtered_pnl = filtered["block_pnl"].sum()
        total_pnl = pre10["block_pnl"].sum()

        filter_results.append({
            "condition": cond,
            "direction": direction,
            "threshold": round(thresh, 4),
            "kept_blocks": len(kept),
            "filtered_blocks": len(filtered),
            "kept_high": kept_high,
            "filtered_high": filtered_high,
            "pct_high_kept": round(100 * kept_high / total_high, 1) if total_high > 0 else 0,
            "kept_low": kept_low,
            "filtered_low": filtered_low,
            "pct_low_filtered": round(100 * filtered_low / total_low, 1) if total_low > 0 else 0,
            "kept_med": kept_med,
            "filtered_med": filtered_med,
            "kept_pnl": round(kept_pnl, 2),
            "filtered_pnl": round(filtered_pnl, 2),
            "total_pnl": round(total_pnl, 2),
            "pnl_improvement": round(kept_pnl - total_pnl, 2),
        })

filter_df = pd.DataFrame(filter_results)
filter_df.to_csv(OUT_DIR / "precursor-filter-test.csv", index=False)

# ============================================================
# SUMMARY TEXT
# ============================================================

lines = []
lines.append("PRECURSOR CONDITION ANALYSIS — SUMMARY")
lines.append("=" * 50)
lines.append("")

# Block class distribution
lines.append("Block class distribution:")
for cls in ["HIGH", "MEDIUM", "LOW"]:
    n = len(blocks[blocks["class"] == cls])
    lines.append(f"  {cls}: {n} blocks")
lines.append("")

# 10-trade precursors
lines.append("10-TRADE PRECURSOR AVERAGES BY BLOCK CLASS")
lines.append("-" * 50)
for cls in ["HIGH", "LOW", "MEDIUM"]:
    sub = pre10[pre10["block_class"] == cls]
    lines.append(f"\n  {cls} blocks (n={len(sub)}):")
    lines.append(f"    Precursor win rate: {sub['precursor_win_rate'].mean():.1f}%")
    for col in CONDITION_COLS:
        pcol = f"pre_{col}"
        lines.append(f"    {col}: {sub[pcol].mean():.4f}")

lines.append("")
lines.append("DIFFERENCE (HIGH - LOW) for 10-trade precursors:")
if diff10 is not None:
    for col in cond_cols_pre:
        label = col.replace("pre_", "")
        lines.append(f"  {label}: {diff10[col]:.4f}")

lines.append("")
lines.append("5-TRADE PRECURSOR AVERAGES BY BLOCK CLASS")
lines.append("-" * 50)
for cls in ["HIGH", "LOW", "MEDIUM"]:
    sub = pre5[pre5["block_class"] == cls]
    lines.append(f"\n  {cls} blocks (n={len(sub)}):")
    lines.append(f"    Precursor win rate: {sub['precursor_win_rate'].mean():.1f}%")
    for col in CONDITION_COLS:
        pcol = f"pre_{col}"
        lines.append(f"    {col}: {sub[pcol].mean():.4f}")

lines.append("")
lines.append("DIFFERENCE (HIGH - LOW) for 5-trade precursors:")
if diff5 is not None:
    for col in cond_cols_pre:
        label = col.replace("pre_", "")
        lines.append(f"  {label}: {diff5[col]:.4f}")

# Effect sizes (ranked)
lines.append("")
lines.append("EFFECT SIZE RANKING (10-trade precursors, Cohen's d)")
lines.append("-" * 50)
lines.append("  Ranked by |effect size| — larger = more predictive of block type")
lines.append("")
for _, row in effect_df.iterrows():
    label = row["condition"].replace("pre_", "")
    lines.append(f"  {label:20s}  d={row['effect_size']:+.3f}  "
                 f"(HIGH mean={row['high_mean']:.4f}, LOW mean={row['low_mean']:.4f})")

# Transitions
lines.append("")
lines.append("TRANSITION ANALYSIS")
lines.append("-" * 50)
for _, row in trans_summary.iterrows():
    lines.append(f"\n  {row['transition']} transitions: {int(row['count'])}")
    delta_cols = [c for c in row.index if c.startswith("avg_delta_")]
    for dc in delta_cols:
        label = dc.replace("avg_delta_", "")
        lines.append(f"    avg delta {label}: {row[dc]:+.4f}")

# Filter test summary - best threshold per condition
lines.append("")
lines.append("FILTER TEST — BEST THRESHOLDS")
lines.append("-" * 50)
lines.append("  For each top condition, best thresholds at two retention levels.")
lines.append("")

for cond in top_conditions:
    label = cond.replace("pre_", "")
    sub = filter_df[filter_df["condition"] == cond]
    lines.append(f"  {label}:")

    for min_pct, tag in [(69, "~70%"), (60, "~60%"), (50, "~50%")]:
        viable = sub[sub["pct_high_kept"] >= min_pct]
        if len(viable) == 0:
            lines.append(f"    [{tag} HIGH kept]: No viable threshold")
            continue
        best = viable.loc[viable["pct_low_filtered"].idxmax()]
        lines.append(f"    [{tag} HIGH kept] threshold: {best['direction']} {best['threshold']:.4f}")
        lines.append(f"      HIGH kept: {int(best['kept_high'])}/{int(best['kept_high'] + best['filtered_high'])} "
                     f"({best['pct_high_kept']:.1f}%)")
        lines.append(f"      LOW filtered: {int(best['filtered_low'])}/{int(best['kept_low'] + best['filtered_low'])} "
                     f"({best['pct_low_filtered']:.1f}%)")
        lines.append(f"      PnL kept: {best['kept_pnl']:.2f}  "
                     f"(total: {best['total_pnl']:.2f}, "
                     f"delta: {best['pnl_improvement']:+.2f})")
    lines.append("")

summary_text = "\n".join(lines)
print(summary_text)

with open(OUT_DIR / "precursor-summary.txt", "w") as f:
    f.write(summary_text)

print("\n\nFiles written:")
for fname in ["precursor-conditions.csv", "precursor-transitions.csv",
              "precursor-filter-test.csv", "precursor-summary.txt"]:
    p = OUT_DIR / fname
    print(f"  {p} ({'EXISTS' if p.exists() else 'MISSING'})")
