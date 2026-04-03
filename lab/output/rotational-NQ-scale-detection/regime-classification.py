"""
Regime classification accuracy test.
Tests how well real-time triggers can classify the current volatility regime
defined by 50-bar rolling stdDev of Close, bucketed into terciles.
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

OUT = Path(__file__).parent

# ── Load data ──────────────────────────────────────────────────────────────
df = pd.read_csv(
    "c:/Projects/futures_pipeline/data/NQ-250tick-calibration.csv",
    skipinitialspace=True,
)
df.columns = df.columns.str.strip()

# Parse datetime
df["datetime"] = pd.to_datetime(
    df["Date"].str.strip() + " " + df["Time"].str.strip(),
    format="%m/%d/%Y %H:%M:%S.%f",
)

# RTH filter: 09:30 - 15:45
df["time_only"] = df["datetime"].dt.time
from datetime import time as dtime
rth_start = dtime(9, 30)
rth_end = dtime(15, 45)
df = df[(df["time_only"] >= rth_start) & (df["time_only"] <= rth_end)].copy()
df = df.reset_index(drop=True)
print(f"RTH bars: {len(df):,}")

# ── Compute bar duration (seconds) ────────────────────────────────────────
df["bar_duration_sec"] = df["datetime"].diff().dt.total_seconds()
# First bar and session-crossing bars: cap at reasonable max, NaN for first
df.loc[df["bar_duration_sec"] > 3600, "bar_duration_sec"] = np.nan
df.loc[df["bar_duration_sec"] <= 0, "bar_duration_sec"] = np.nan

# ── Ground truth: 50-bar rolling stdDev of Close, tercile regime ──────────
df["stddev50"] = df["Last"].rolling(50, min_periods=50).std(ddof=0)

# Tercile boundaries from full calibration data (non-NaN)
valid = df["stddev50"].dropna()
p33 = valid.quantile(1/3)
p67 = valid.quantile(2/3)
print(f"StdDev50 tercile boundaries: P33={p33:.4f}, P67={p67:.4f}")

def regime_label(s):
    if s <= p33:
        return "low"
    elif s <= p67:
        return "medium"
    else:
        return "high"

df["actual_regime"] = df["stddev50"].apply(lambda x: regime_label(x) if pd.notna(x) else np.nan)

# ── Trigger features ──────────────────────────────────────────────────────

# 1. StdDev ratio: 50-bar / 200-bar
df["stddev200"] = df["Last"].rolling(200, min_periods=200).std(ddof=0)
df["stddev_ratio"] = df["stddev50"] / df["stddev200"]

# 2. Bar range: rolling 10-bar avg of (High - Low)
df["bar_range"] = df["High"] - df["Low"]
df["avg_range_10"] = df["bar_range"].rolling(10, min_periods=10).mean()

# 3. Bar speed: rolling 10-bar avg bar duration (seconds)
df["avg_speed_10"] = df["bar_duration_sec"].rolling(10, min_periods=10).mean()

# ── Drop rows without all features ───────────────────────────────────────
mask = df["actual_regime"].notna() & df["stddev_ratio"].notna() & df["avg_range_10"].notna() & df["avg_speed_10"].notna()
df_valid = df[mask].copy().reset_index(drop=True)
print(f"Valid bars (all features present): {len(df_valid):,}")

regime_counts = df_valid["actual_regime"].value_counts()
print(f"Regime distribution: {dict(regime_counts)}")

# ── Helper: compute metrics ───────────────────────────────────────────────
REGIMES = ["low", "medium", "high"]

def compute_metrics(predicted, actual, name):
    """Returns summary dict, confusion matrix df, per-regime precision/recall."""
    correct = (predicted == actual).sum()
    total = len(predicted)
    accuracy = correct / total

    # Per-regime accuracy, precision, recall
    per_regime = {}
    for r in REGIMES:
        tp = ((predicted == r) & (actual == r)).sum()
        fp = ((predicted == r) & (actual != r)).sum()
        fn = ((predicted != r) & (actual == r)).sum()
        actual_count = (actual == r).sum()
        pred_count = (predicted == r).sum()

        recall = tp / actual_count if actual_count > 0 else 0
        precision = tp / pred_count if pred_count > 0 else 0

        per_regime[r] = {
            "accuracy": recall,  # % of actual r correctly classified
            "precision": precision,
            "recall": recall,
            "tp": tp, "fp": fp, "fn": fn,
            "actual_count": actual_count,
            "pred_count": pred_count,
        }

    # Confusion matrix: rows=actual, cols=predicted
    cm = pd.DataFrame(0, index=REGIMES, columns=REGIMES)
    for r_actual in REGIMES:
        for r_pred in REGIMES:
            cm.loc[r_actual, r_pred] = ((actual == r_actual) & (predicted == r_pred)).sum()

    summary = {
        "classifier": name,
        "overall_accuracy": accuracy,
        "low_recall": per_regime["low"]["recall"],
        "low_precision": per_regime["low"]["precision"],
        "medium_recall": per_regime["medium"]["recall"],
        "medium_precision": per_regime["medium"]["precision"],
        "high_recall": per_regime["high"]["recall"],
        "high_precision": per_regime["high"]["precision"],
    }
    return summary, cm, per_regime


# ── Helper: compute lag ───────────────────────────────────────────────────
def compute_lag(predicted, actual):
    """When actual regime changes, how many bars until predicted also changes?"""
    actual_arr = actual.values
    pred_arr = predicted.values

    # Find regime change points in actual
    change_points = []
    for i in range(1, len(actual_arr)):
        if actual_arr[i] != actual_arr[i-1]:
            change_points.append(i)

    if not change_points:
        return {"mean_lag": np.nan, "median_lag": np.nan, "n_transitions": 0}

    lags = []
    for cp in change_points:
        new_regime = actual_arr[cp]
        # Look forward from cp to find when predicted matches new_regime
        found = False
        for j in range(cp, min(cp + 200, len(pred_arr))):
            if pred_arr[j] == new_regime:
                lags.append(j - cp)
                found = True
                break
        if not found:
            lags.append(200)  # cap at 200 if never caught up

    return {
        "mean_lag": np.mean(lags),
        "median_lag": np.median(lags),
        "p75_lag": np.percentile(lags, 75),
        "p90_lag": np.percentile(lags, 90),
        "n_transitions": len(change_points),
        "n_caught": sum(1 for l in lags if l < 200),
    }


# ══════════════════════════════════════════════════════════════════════════
# CLASSIFIERS
# ══════════════════════════════════════════════════════════════════════════

all_summaries = []
all_confusions = {}
all_lags = []

actual = df_valid["actual_regime"]

# ── 1a. StdDev ratio (default thresholds) ─────────────────────────────────
def stddev_ratio_classify(ratio, lo=0.7, hi=1.5):
    if ratio < lo:
        return "low"
    elif ratio <= hi:
        return "medium"
    else:
        return "high"

pred_1a = df_valid["stddev_ratio"].apply(lambda x: stddev_ratio_classify(x))
s, cm, _ = compute_metrics(pred_1a, actual, "StdDev ratio (default 0.7/1.5)")
all_summaries.append(s)
all_confusions["StdDev ratio (default 0.7/1.5)"] = cm
lag = compute_lag(pred_1a, actual)
lag["classifier"] = "StdDev ratio (default 0.7/1.5)"
all_lags.append(lag)

# ── 1b. StdDev ratio (optimized thresholds) ──────────────────────────────
best_acc = 0
best_lo, best_hi = 0.7, 1.5
for lo in np.arange(0.5, 1.1, 0.05):
    for hi in np.arange(lo + 0.1, 2.1, 0.05):
        pred = df_valid["stddev_ratio"].apply(lambda x, lo=lo, hi=hi: stddev_ratio_classify(x, lo, hi))
        acc = (pred == actual).mean()
        if acc > best_acc:
            best_acc = acc
            best_lo, best_hi = lo, hi

print(f"Optimized StdDev ratio thresholds: lo={best_lo:.2f}, hi={best_hi:.2f}, acc={best_acc:.4f}")
pred_1b = df_valid["stddev_ratio"].apply(lambda x: stddev_ratio_classify(x, best_lo, best_hi))
s, cm, _ = compute_metrics(pred_1b, actual, f"StdDev ratio (optimized {best_lo:.2f}/{best_hi:.2f})")
all_summaries.append(s)
all_confusions[f"StdDev ratio (optimized {best_lo:.2f}/{best_hi:.2f})"] = cm
lag = compute_lag(pred_1b, actual)
lag["classifier"] = f"StdDev ratio (optimized {best_lo:.2f}/{best_hi:.2f})"
all_lags.append(lag)

# ── 2a. Bar range (tercile thresholds) ────────────────────────────────────
range_p33 = df_valid["avg_range_10"].quantile(1/3)
range_p67 = df_valid["avg_range_10"].quantile(2/3)
print(f"Bar range terciles: P33={range_p33:.4f}, P67={range_p67:.4f}")

def range_classify(val, lo=range_p33, hi=range_p67):
    if val <= lo:
        return "low"
    elif val <= hi:
        return "medium"
    else:
        return "high"

pred_2a = df_valid["avg_range_10"].apply(range_classify)
s, cm, _ = compute_metrics(pred_2a, actual, "Bar range (tercile)")
all_summaries.append(s)
all_confusions["Bar range (tercile)"] = cm
lag = compute_lag(pred_2a, actual)
lag["classifier"] = "Bar range (tercile)"
all_lags.append(lag)

# ── 2b. Bar range (optimized thresholds) ──────────────────────────────────
range_vals = df_valid["avg_range_10"].values
range_pcts = np.arange(0.15, 0.50, 0.02)
best_acc = 0
best_rlo, best_rhi = range_p33, range_p67
for lo_pct in range_pcts:
    for hi_pct in np.arange(lo_pct + 0.05, 0.85, 0.02):
        lo_val = np.quantile(range_vals, lo_pct)
        hi_val = np.quantile(range_vals, hi_pct)
        pred = pd.Series(["low" if v <= lo_val else "high" if v > hi_val else "medium" for v in range_vals])
        acc = (pred.values == actual.values).mean()
        if acc > best_acc:
            best_acc = acc
            best_rlo, best_rhi = lo_val, hi_val

print(f"Optimized bar range thresholds: lo={best_rlo:.4f}, hi={best_rhi:.4f}, acc={best_acc:.4f}")
pred_2b = pd.Series(["low" if v <= best_rlo else "high" if v > best_rhi else "medium" for v in range_vals])
s, cm, _ = compute_metrics(pred_2b, actual, f"Bar range (optimized)")
all_summaries.append(s)
all_confusions["Bar range (optimized)"] = cm
lag = compute_lag(pred_2b, actual)
lag["classifier"] = "Bar range (optimized)"
all_lags.append(lag)

# ── 3. Bar speed (tercile thresholds) ─────────────────────────────────────
# Note: fast bars (low duration) = potentially high vol
speed_p33 = df_valid["avg_speed_10"].quantile(1/3)
speed_p67 = df_valid["avg_speed_10"].quantile(2/3)
print(f"Bar speed terciles: P33={speed_p33:.4f}s, P67={speed_p67:.4f}s")

# Inverted: low duration → high vol
def speed_classify(val):
    if val >= speed_p67:   # slow bars → low vol
        return "low"
    elif val >= speed_p33: # medium bars → medium vol
        return "medium"
    else:                  # fast bars → high vol
        return "high"

pred_3 = df_valid["avg_speed_10"].apply(speed_classify)
s, cm, _ = compute_metrics(pred_3, actual, "Bar speed (tercile, inverted)")
all_summaries.append(s)
all_confusions["Bar speed (tercile, inverted)"] = cm
lag = compute_lag(pred_3, actual)
lag["classifier"] = "Bar speed (tercile, inverted)"
all_lags.append(lag)

# ── 4. Speed-range 2D regime ─────────────────────────────────────────────
speed_median = df_valid["avg_speed_10"].median()
range_median = df_valid["avg_range_10"].median()

def speed_range_bucket(row):
    fast = row["avg_speed_10"] < speed_median
    wide = row["avg_range_10"] > range_median
    if fast and wide:
        return "fast_wide"
    elif fast and not wide:
        return "fast_narrow"
    elif not fast and wide:
        return "slow_wide"
    else:
        return "slow_narrow"

df_valid["speed_range_bucket"] = df_valid.apply(speed_range_bucket, axis=1)

# Map each bucket to most common actual regime
bucket_mode = df_valid.groupby("speed_range_bucket")["actual_regime"].agg(lambda x: x.value_counts().index[0])
print(f"Speed-range bucket -> regime mapping: {dict(bucket_mode)}")

# Also print distribution
for b in ["fast_wide", "fast_narrow", "slow_wide", "slow_narrow"]:
    sub = df_valid[df_valid["speed_range_bucket"] == b]
    dist = sub["actual_regime"].value_counts(normalize=True)
    print(f"  {b}: {dict(dist.round(3))}")

pred_4 = df_valid["speed_range_bucket"].map(bucket_mode)
s, cm, _ = compute_metrics(pred_4, actual, "Speed-range 2D")
all_summaries.append(s)
all_confusions["Speed-range 2D"] = cm
lag = compute_lag(pred_4, actual)
lag["classifier"] = "Speed-range 2D"
all_lags.append(lag)

# ── 5a. Combined: StdDev ratio + bar range (agreement rule) ──────────────
# Use optimized thresholds for both
pred_ratio = pred_1b.values  # optimized stddev ratio
pred_range = pred_2b.values  # optimized bar range

# Individual accuracies
acc_ratio = (pred_1b == actual).mean()
acc_range = (pred_2b == actual).mean()

combined_5a = []
for i in range(len(df_valid)):
    if pred_ratio[i] == pred_range[i]:
        combined_5a.append(pred_ratio[i])  # agree → use that
    else:
        # disagree → use whichever has higher individual accuracy
        if acc_ratio >= acc_range:
            combined_5a.append(pred_ratio[i])
        else:
            combined_5a.append(pred_range[i])

pred_5a = pd.Series(combined_5a)
s, cm, _ = compute_metrics(pred_5a, actual, "Combined agree/fallback")
all_summaries.append(s)
all_confusions["Combined agree/fallback"] = cm
lag = compute_lag(pred_5a, actual)
lag["classifier"] = "Combined agree/fallback"
all_lags.append(lag)

# ── 5b. Combined: normalized avg, bucketed into terciles ──────────────────
# Normalize both to [0,1] range
ratio_vals = df_valid["stddev_ratio"].values
range_vals_norm = df_valid["avg_range_10"].values

ratio_min, ratio_max = ratio_vals.min(), ratio_vals.max()
range_min, range_max = range_vals_norm.min(), range_vals_norm.max()

ratio_norm = (ratio_vals - ratio_min) / (ratio_max - ratio_min)
range_norm = (range_vals_norm - range_min) / (range_max - range_min)

combined_score = (ratio_norm + range_norm) / 2
combined_p33 = np.quantile(combined_score, 1/3)
combined_p67 = np.quantile(combined_score, 2/3)

pred_5b = pd.Series(["low" if v <= combined_p33 else "high" if v > combined_p67 else "medium" for v in combined_score])
s, cm, _ = compute_metrics(pred_5b, actual, "Combined normalized avg (tercile)")
all_summaries.append(s)
all_confusions["Combined normalized avg (tercile)"] = cm
lag = compute_lag(pred_5b, actual)
lag["classifier"] = "Combined normalized avg (tercile)"
all_lags.append(lag)

# ══════════════════════════════════════════════════════════════════════════
# OUTPUT
# ══════════════════════════════════════════════════════════════════════════

# 1. Summary CSV
summary_df = pd.DataFrame(all_summaries)
summary_df = summary_df.sort_values("overall_accuracy", ascending=False)
summary_df.to_csv(OUT / "regime-classification.csv", index=False, float_format="%.4f")
print("\n=== Classification Accuracy ===")
print(summary_df.to_string(index=False))

# 2. Confusion matrices CSV
cm_rows = []
for name, cm in all_confusions.items():
    for actual_r in REGIMES:
        for pred_r in REGIMES:
            cm_rows.append({
                "classifier": name,
                "actual": actual_r,
                "predicted": pred_r,
                "count": cm.loc[actual_r, pred_r],
            })
cm_df = pd.DataFrame(cm_rows)
cm_df.to_csv(OUT / "regime-confusion-matrices.csv", index=False)

# 3. Lag analysis CSV
lag_df = pd.DataFrame(all_lags)
lag_df = lag_df[["classifier", "mean_lag", "median_lag", "p75_lag", "p90_lag", "n_transitions", "n_caught"]]
lag_df = lag_df.sort_values("mean_lag")
lag_df.to_csv(OUT / "regime-lag-analysis.csv", index=False, float_format="%.2f")
print("\n=== Lag Analysis ===")
print(lag_df.to_string(index=False))

# 4. Text summary
lines = []
lines.append("REGIME CLASSIFICATION ACCURACY TEST")
lines.append("=" * 60)
lines.append(f"Data: NQ-250tick-calibration.csv (RTH only)")
lines.append(f"Valid bars: {len(df_valid):,}")
lines.append(f"Regime definition: 50-bar rolling stdDev of Close (ddof=0)")
lines.append(f"Tercile boundaries: P33={p33:.4f}, P67={p67:.4f}")
lines.append(f"Regime distribution: {dict(regime_counts)}")
lines.append("")
lines.append("CLASSIFIER RANKING (by overall accuracy)")
lines.append("-" * 60)
for _, row in summary_df.iterrows():
    lines.append(f"  {row['overall_accuracy']:.1%}  {row['classifier']}")
    lines.append(f"         Low:  recall={row['low_recall']:.1%}  precision={row['low_precision']:.1%}")
    lines.append(f"         Med:  recall={row['medium_recall']:.1%}  precision={row['medium_precision']:.1%}")
    lines.append(f"         High: recall={row['high_recall']:.1%}  precision={row['high_precision']:.1%}")
    lines.append("")

lines.append("LAG ANALYSIS (bars until trigger catches regime change)")
lines.append("-" * 60)
for _, row in lag_df.iterrows():
    lines.append(f"  {row['classifier']}")
    lines.append(f"    mean={row['mean_lag']:.1f}  median={row['median_lag']:.1f}  p75={row['p75_lag']:.1f}  p90={row['p90_lag']:.1f}")
    lines.append(f"    transitions={int(row['n_transitions'])}  caught={int(row['n_caught'])}")
    lines.append("")

lines.append("CONFUSION MATRICES")
lines.append("-" * 60)
for name, cm in all_confusions.items():
    lines.append(f"\n  {name}")
    lines.append(f"  {'actual\\predicted':>20s}  {'low':>8s}  {'medium':>8s}  {'high':>8s}")
    for r in REGIMES:
        lines.append(f"  {r:>20s}  {cm.loc[r, 'low']:>8d}  {cm.loc[r, 'medium']:>8d}  {cm.loc[r, 'high']:>8d}")

lines.append("")
lines.append(f"Speed-range bucket mappings: {dict(bucket_mode)}")
lines.append(f"Optimized StdDev ratio thresholds: {best_lo:.2f} / {best_hi:.2f}")
lines.append(f"Bar range terciles: {range_p33:.4f} / {range_p67:.4f}")
lines.append(f"Bar speed terciles: {speed_p33:.4f}s / {speed_p67:.4f}s")

summary_text = "\n".join(lines)
(OUT / "regime-classification-summary.txt").write_text(summary_text)
print(f"\nOutput written to {OUT}")
