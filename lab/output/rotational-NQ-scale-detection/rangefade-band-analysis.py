"""
Range-fade band parameter analysis for rotational-NQ.
Computes stddev regime, time-of-day, lookback sensitivity,
and volatility expansion/contraction analyses.
"""
import pandas as pd
import numpy as np
import os

OUT_DIR = r"c:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection"

# ── Load data ──────────────────────────────────────────────────
df = pd.read_csv(
    r"c:\Projects\futures_pipeline\data\NQ-250tick-calibration.csv",
    skipinitialspace=True,
)
df.columns = df.columns.str.strip()

# Parse datetime
df["DateTime"] = pd.to_datetime(df["Date"].str.strip() + " " + df["Time"].str.strip(),
                                 format="%m/%d/%Y %H:%M:%S.%f")
df["Hour"] = df["DateTime"].dt.hour

# Core columns
for c in ["Open", "High", "Low", "Last"]:
    df[c] = pd.to_numeric(df[c], errors="coerce")

df["BarRange"] = df["High"] - df["Low"]

print(f"Loaded {len(df)} rows, date range: {df['DateTime'].min()} to {df['DateTime'].max()}")

# ── Helper: rolling stats ──────────────────────────────────────
def add_rolling(df, lookback):
    prefix = f"lb{lookback}_"
    df[prefix + "mean"] = df["Last"].rolling(lookback, min_periods=lookback).mean()
    df[prefix + "std"] = df["Last"].rolling(lookback, min_periods=lookback).std()
    df[prefix + "dev"] = (df["Last"] - df[prefix + "mean"]).abs()
    df[prefix + "inner_touch"] = (df[prefix + "dev"] >= 1.0 * df[prefix + "std"]).astype(int)
    df[prefix + "outer_touch"] = (df[prefix + "dev"] >= 2.0 * df[prefix + "std"]).astype(int)
    return df

# Compute base lookback=50
df = add_rolling(df, 50)

# ── 1. StdDev Regime Analysis ─────────────────────────────────
print("\n=== 1. StdDev Regime Analysis ===")
valid = df.dropna(subset=["lb50_std"]).copy()
valid["std_quintile"] = pd.qcut(valid["lb50_std"], 5, labels=["Q1(low)", "Q2", "Q3", "Q4", "Q5(high)"])

regime = valid.groupby("std_quintile", observed=True).agg(
    avg_bar_range=("BarRange", "mean"),
    avg_stddev=("lb50_std", "mean"),
    inner_touch_pct=("lb50_inner_touch", "mean"),
    outer_touch_pct=("lb50_outer_touch", "mean"),
    count=("Last", "count"),
).reset_index()

regime["inner_touch_per100"] = regime["inner_touch_pct"] * 100
regime["outer_touch_per100"] = regime["outer_touch_pct"] * 100
regime_out = regime[["std_quintile", "avg_bar_range", "avg_stddev",
                      "inner_touch_per100", "outer_touch_per100", "count"]]
regime_out.columns = ["quintile", "avg_bar_range", "avg_stddev",
                       "inner_touch_per_100bars", "outer_touch_per_100bars", "bar_count"]
regime_out = regime_out.round(4)
regime_out.to_csv(os.path.join(OUT_DIR, "stddev-regime-analysis.csv"), index=False)
print(regime_out.to_string(index=False))

# ── 2. Time-of-Day Analysis ───────────────────────────────────
print("\n=== 2. Time-of-Day Analysis ===")
tod = valid.groupby("Hour").agg(
    avg_stddev=("lb50_std", "mean"),
    avg_bar_range=("BarRange", "mean"),
    inner_touch_per100=("lb50_inner_touch", lambda x: x.mean() * 100),
    outer_touch_per100=("lb50_outer_touch", lambda x: x.mean() * 100),
    count=("Last", "count"),
).reset_index()
tod = tod.round(4)
tod.to_csv(os.path.join(OUT_DIR, "time-of-day-analysis.csv"), index=False)
print(tod.to_string(index=False))

# ── 3. Lookback Sensitivity ──────────────────────────────────
print("\n=== 3. Lookback Sensitivity ===")
lookbacks = [20, 35, 50, 75, 100]
lb_results = []

for lb in lookbacks:
    df = add_rolling(df, lb)
    p = f"lb{lb}_"
    v = df.dropna(subset=[p + "std"]).copy()

    inner_bw = 2.0 * 1.0 * v[p + "std"]  # inner top - inner bot = 2 * innerMult * std
    outer_bw = 2.0 * 2.0 * v[p + "std"]  # outer band width

    # Approximate target vs stop hits:
    # When inner band touched on one side, track if price next reaches:
    #   - opposite inner band (target hit)
    #   - same-side outer band (stop hit)
    inner_touches = v[v[p + "inner_touch"] == 1].copy()
    mean_col = p + "mean"
    std_col = p + "std"

    # Determine side of touch
    inner_touches["side"] = np.where(
        v.loc[inner_touches.index, "Last"] > v.loc[inner_touches.index, mean_col],
        "upper", "lower"
    )

    # For a sample of inner touches, look forward to see target vs stop
    target_hits = 0
    stop_hits = 0
    max_forward = 200
    sample_indices = inner_touches.index[::5]  # sample every 5th touch for speed

    for idx in sample_indices:
        pos = df.index.get_loc(idx)
        side = inner_touches.loc[idx, "side"]
        entry_mean = df.loc[idx, mean_col]
        entry_std = df.loc[idx, std_col]

        if side == "upper":
            target_level = entry_mean - 1.0 * entry_std  # opposite inner
            stop_level = entry_mean + 2.0 * entry_std    # same side outer
        else:
            target_level = entry_mean + 1.0 * entry_std
            stop_level = entry_mean - 2.0 * entry_std

        for fwd in range(1, min(max_forward, len(df) - pos)):
            fwd_price = df.iloc[pos + fwd]["Last"]
            if side == "upper":
                if fwd_price <= target_level:
                    target_hits += 1
                    break
                elif fwd_price >= stop_level:
                    stop_hits += 1
                    break
            else:
                if fwd_price >= target_level:
                    target_hits += 1
                    break
                elif fwd_price <= stop_level:
                    stop_hits += 1
                    break

    total_resolved = target_hits + stop_hits
    target_ratio = target_hits / total_resolved if total_resolved > 0 else 0

    lb_results.append({
        "lookback": lb,
        "avg_inner_band_width": inner_bw.mean(),
        "avg_outer_band_width": outer_bw.mean(),
        "inner_touch_per_100bars": v[p + "inner_touch"].mean() * 100,
        "outer_touch_per_100bars": v[p + "outer_touch"].mean() * 100,
        "target_hits": target_hits,
        "stop_hits": stop_hits,
        "target_pct": round(target_ratio * 100, 2),
        "sampled_trades": total_resolved,
    })

lb_df = pd.DataFrame(lb_results).round(4)
lb_df.to_csv(os.path.join(OUT_DIR, "lookback-sensitivity.csv"), index=False)
print(lb_df.to_string(index=False))

# ── 4. Volatility Expansion/Contraction ──────────────────────
print("\n=== 4. Volatility Expansion/Contraction ===")
df["std200"] = df["Last"].rolling(200, min_periods=200).std()
df["vol_ratio"] = df["lb50_std"] / df["std200"]

v4 = df.dropna(subset=["vol_ratio", "lb50_inner_touch"]).copy()

def classify_regime(r):
    if r > 1.5:
        return "expanding"
    elif r < 0.7:
        return "contracting"
    else:
        return "normal"

v4["vol_regime"] = v4["vol_ratio"].apply(classify_regime)

# Band touch rates by regime
vol_regime = v4.groupby("vol_regime").agg(
    avg_vol_ratio=("vol_ratio", "mean"),
    avg_stddev_50=("lb50_std", "mean"),
    avg_bar_range=("BarRange", "mean"),
    inner_touch_per100=("lb50_inner_touch", lambda x: x.mean() * 100),
    outer_touch_per100=("lb50_outer_touch", lambda x: x.mean() * 100),
    count=("Last", "count"),
).reset_index()

# Average bars between inner band touches by regime
for regime_name in ["expanding", "contracting", "normal"]:
    subset = v4[v4["vol_regime"] == regime_name]
    touches = subset[subset["lb50_inner_touch"] == 1].index
    if len(touches) > 1:
        gaps = np.diff([df.index.get_loc(t) for t in touches])
        avg_gap = gaps.mean()
    else:
        avg_gap = np.nan
    vol_regime.loc[vol_regime["vol_regime"] == regime_name, "avg_bars_between_inner_touches"] = avg_gap

vol_regime = vol_regime.round(4)
vol_regime.to_csv(os.path.join(OUT_DIR, "volatility-expansion-contraction.csv"), index=False)
print(vol_regime.to_string(index=False))

# ── Summary ──────────────────────────────────────────────────
print("\n=== Writing summary ===")
summary_lines = []
summary_lines.append("RANGE-FADE BAND PARAMETER ANALYSIS — SUMMARY")
summary_lines.append(f"Data: NQ 250-tick, {len(df)} bars, {df['DateTime'].min()} to {df['DateTime'].max()}")
summary_lines.append(f"Base parameters: lookback=50, innerMult=1.0, outerMult=2.0")
summary_lines.append("")

summary_lines.append("1. STDDEV REGIME ANALYSIS")
summary_lines.append("-" * 60)
for _, row in regime_out.iterrows():
    summary_lines.append(
        f"  {row['quintile']:>10s}: avg_range={row['avg_bar_range']:8.2f}  "
        f"avg_std={row['avg_stddev']:8.2f}  "
        f"inner_touch/100={row['inner_touch_per_100bars']:6.2f}  "
        f"outer_touch/100={row['outer_touch_per_100bars']:6.2f}  "
        f"n={int(row['bar_count'])}"
    )
q1_inner = regime_out.iloc[0]["inner_touch_per_100bars"]
q5_inner = regime_out.iloc[-1]["inner_touch_per_100bars"]
q1_outer = regime_out.iloc[0]["outer_touch_per_100bars"]
q5_outer = regime_out.iloc[-1]["outer_touch_per_100bars"]
summary_lines.append(f"  Inner touch rate: Q1={q1_inner:.1f}/100 vs Q5={q5_inner:.1f}/100 ({q5_inner/q1_inner:.1f}x)")
summary_lines.append(f"  Outer touch rate: Q1={q1_outer:.1f}/100 vs Q5={q5_outer:.1f}/100 ({q5_outer/q1_outer:.1f}x)")
summary_lines.append("")

summary_lines.append("2. TIME-OF-DAY ANALYSIS")
summary_lines.append("-" * 60)
peak_hour = tod.loc[tod["avg_stddev"].idxmax()]
trough_hour = tod.loc[tod["avg_stddev"].idxmin()]
summary_lines.append(f"  Highest avg stddev: hour {int(peak_hour['Hour'])} ({peak_hour['avg_stddev']:.2f})")
summary_lines.append(f"  Lowest avg stddev:  hour {int(trough_hour['Hour'])} ({trough_hour['avg_stddev']:.2f})")
peak_inner = tod.loc[tod["inner_touch_per100"].idxmax()]
trough_inner = tod.loc[tod["inner_touch_per100"].idxmin()]
summary_lines.append(f"  Most inner touches/100:  hour {int(peak_inner['Hour'])} ({peak_inner['inner_touch_per100']:.1f})")
summary_lines.append(f"  Fewest inner touches/100: hour {int(trough_inner['Hour'])} ({trough_inner['inner_touch_per100']:.1f})")
summary_lines.append("")

summary_lines.append("3. LOOKBACK SENSITIVITY")
summary_lines.append("-" * 60)
for _, row in lb_df.iterrows():
    summary_lines.append(
        f"  LB={int(row['lookback']):>3d}: inner_bw={row['avg_inner_band_width']:8.2f}  "
        f"inner_touch/100={row['inner_touch_per_100bars']:6.2f}  "
        f"outer_touch/100={row['outer_touch_per_100bars']:6.2f}  "
        f"target_pct={row['target_pct']:5.1f}%  "
        f"(n={int(row['sampled_trades'])})"
    )
summary_lines.append("")

summary_lines.append("4. VOLATILITY EXPANSION/CONTRACTION")
summary_lines.append("-" * 60)
for _, row in vol_regime.iterrows():
    summary_lines.append(
        f"  {row['vol_regime']:>12s}: vol_ratio={row['avg_vol_ratio']:5.2f}  "
        f"std50={row['avg_stddev_50']:8.2f}  "
        f"inner/100={row['inner_touch_per100']:6.2f}  "
        f"outer/100={row['outer_touch_per100']:6.2f}  "
        f"avg_gap={row['avg_bars_between_inner_touches']:5.1f} bars  "
        f"n={int(row['count'])}"
    )

expanding = vol_regime[vol_regime["vol_regime"] == "expanding"]
contracting = vol_regime[vol_regime["vol_regime"] == "contracting"]
normal = vol_regime[vol_regime["vol_regime"] == "normal"]
summary_lines.append(f"  Distribution: expanding={int(expanding['count'].values[0])} bars "
                     f"({int(expanding['count'].values[0])/len(v4)*100:.1f}%), "
                     f"contracting={int(contracting['count'].values[0])} bars "
                     f"({int(contracting['count'].values[0])/len(v4)*100:.1f}%), "
                     f"normal={int(normal['count'].values[0])} bars "
                     f"({int(normal['count'].values[0])/len(v4)*100:.1f}%)")

summary_text = "\n".join(summary_lines)
with open(os.path.join(OUT_DIR, "summary.txt"), "w") as f:
    f.write(summary_text)

print(summary_text)
print("\nDone. Files written to:", OUT_DIR)
