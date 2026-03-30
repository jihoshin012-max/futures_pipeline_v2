# archetype: rotational
"""Standalone EMA derivatives analysis: do 1st/2nd derivatives of EMA9/EMA21
and their cross dynamics predict short-term price behavior on 250-tick bars?

Features:
  d_ema9, d_ema21       — 1st derivative (slope)
  d2_ema9, d2_ema21     — 2nd derivative (acceleration)
  ema_spread            — ema9 - ema21 (cross distance)
  d_spread              — spread change (widening/narrowing)
"""
import sys, importlib, time
import numpy as np
from collections import defaultdict

sys.path.insert(0, r"c:\Projects\futures_pipeline\lab")

_engine = importlib.import_module("rotational-NQ-scale-detection-engine")
load_bars_extended = _engine.load_bars_extended
aggregate_to_ntick = _engine.aggregate_to_ntick

BS = 250
RTH_OPEN = 34200
RTH_CLOSE = 57000


def ema(data, period):
    """Compute EMA on array."""
    out = np.full(len(data), np.nan, dtype=np.float64)
    k = 2.0 / (period + 1)
    first_valid = -1
    for i in range(len(data)):
        if np.isnan(data[i]):
            continue
        if first_valid < 0:
            out[i] = data[i]
            first_valid = i
        else:
            out[i] = data[i] * k + out[i - 1] * (1 - k)
    return out


print("Loading P1...")
t0 = time.time()
bars = load_bars_extended(r"C:\Projects\futures_pipeline\data\NQ-1tick-calibration.csv")
print(f"  {bars['n']} ticks ({time.time()-t0:.0f}s)")

print("Aggregating (SC-aligned)...")
agg, t2a = aggregate_to_ntick(bars, BS)
n_agg = agg["n"]
print(f"  {n_agg} agg bars")

close = np.array(agg["last"], dtype=np.float64)
tsec = agg["time_sec"]
dint = agg["date_int"]

# Compute EMAs
print("Computing EMAs...")
ema9 = ema(close, 9)
ema21 = ema(close, 21)

# 1st derivatives
d_ema9 = np.full(n_agg, np.nan)
d_ema21 = np.full(n_agg, np.nan)
for i in range(1, n_agg):
    if not np.isnan(ema9[i]) and not np.isnan(ema9[i - 1]):
        d_ema9[i] = ema9[i] - ema9[i - 1]
    if not np.isnan(ema21[i]) and not np.isnan(ema21[i - 1]):
        d_ema21[i] = ema21[i] - ema21[i - 1]

# 2nd derivatives
d2_ema9 = np.full(n_agg, np.nan)
d2_ema21 = np.full(n_agg, np.nan)
for i in range(2, n_agg):
    if not np.isnan(d_ema9[i]) and not np.isnan(d_ema9[i - 1]):
        d2_ema9[i] = d_ema9[i] - d_ema9[i - 1]
    if not np.isnan(d_ema21[i]) and not np.isnan(d_ema21[i - 1]):
        d2_ema21[i] = d_ema21[i] - d_ema21[i - 1]

# EMA cross dynamics
ema_spread = np.full(n_agg, np.nan)
d_spread = np.full(n_agg, np.nan)
for i in range(n_agg):
    if not np.isnan(ema9[i]) and not np.isnan(ema21[i]):
        ema_spread[i] = ema9[i] - ema21[i]
for i in range(1, n_agg):
    if not np.isnan(ema_spread[i]) and not np.isnan(ema_spread[i - 1]):
        d_spread[i] = ema_spread[i] - ema_spread[i - 1]

# Build records
horizons = [1, 2, 3, 5]
features = {
    "d_ema9": d_ema9,
    "d_ema21": d_ema21,
    "d2_ema9": d2_ema9,
    "d2_ema21": d2_ema21,
    "ema_spread": ema_spread,
    "d_spread": d_spread,
}

print(f"Computing forward returns at horizons {horizons}...")

records = []
for i in range(21, n_agg - max(horizons)):
    # Need all features valid
    skip = False
    for fname, farr in features.items():
        if np.isnan(farr[i]):
            skip = True
            break
    if skip:
        continue
    # RTH filter
    if tsec[i] < RTH_OPEN or tsec[i] > RTH_CLOSE:
        continue
    # Session boundary check in forward window
    for h in horizons:
        if dint[i + h] != dint[i]:
            skip = True
            break
    if skip:
        continue

    rec = {"bar": i}
    for fname, farr in features.items():
        rec[fname] = farr[i]
    for h in horizons:
        rec[f"fwd_{h}"] = float(close[i + h] - close[i])
    records.append(rec)

print(f"  {len(records)} valid RTH bars")


def analyze_feature(feat_name, horizon):
    """Bucket by feature percentiles and show forward returns."""
    vals = np.array([r[feat_name] for r in records])
    pcts = [0, 10, 25, 50, 75, 90, 100]
    boundaries = np.percentile(vals, pcts)

    rows = []
    for j in range(len(pcts) - 1):
        lo, hi = boundaries[j], boundaries[j + 1]
        if j == len(pcts) - 2:
            subset = [r for r in records if r[feat_name] >= lo]
        else:
            subset = [r for r in records if lo <= r[feat_name] < hi]
        if not subset:
            continue
        fwds = np.array([r[f"fwd_{horizon}"] for r in subset])
        rows.append((f"P{pcts[j]}-P{pcts[j+1]}", len(subset), np.mean(fwds),
                      np.median(fwds), 100 * np.mean(fwds > 0)))
    return rows


# === Run all features ===
for feat_name in features:
    print(f"\n{'='*70}")
    print(f"FEATURE: {feat_name}")
    print(f"{'='*70}")

    for h in horizons:
        print(f"\n  Horizon: {h} bar(s)")
        print(f"  {'Bucket':>12} {'N':>6} {'Mean fwd':>10} {'Median':>10} {'% pos':>8}")
        print(f"  {'-'*50}")
        rows = analyze_feature(feat_name, h)
        for label, n, mean, med, pct in rows:
            print(f"  {label:>12} {n:>6} {mean:>10.4f} {med:>10.4f} {pct:>7.1f}%")

# === EMA Cross: spread sign + d_spread direction ===
print(f"\n{'='*70}")
print("COMBINED: Spread sign x d_spread direction")
print("  Spread>0 + d_spread>0 = above and widening (bullish)")
print("  Spread>0 + d_spread<0 = above but narrowing (weakening)")
print("  Spread<0 + d_spread<0 = below and widening (bearish)")
print("  Spread<0 + d_spread>0 = below but narrowing (recovering)")
print(f"{'='*70}")

for h in [1, 3, 5]:
    print(f"\n  Horizon {h}:")
    combos = {
        "above+widen": [r for r in records if r["ema_spread"] > 0 and r["d_spread"] > 0],
        "above+narrow": [r for r in records if r["ema_spread"] > 0 and r["d_spread"] <= 0],
        "below+widen": [r for r in records if r["ema_spread"] <= 0 and r["d_spread"] <= 0],
        "below+narrow": [r for r in records if r["ema_spread"] <= 0 and r["d_spread"] > 0],
    }
    print(f"  {'Combo':>15} {'N':>6} {'Mean fwd':>10} {'Median':>10} {'% pos':>8}")
    print(f"  {'-'*52}")
    for name, subset in combos.items():
        if not subset:
            continue
        fwds = np.array([r[f"fwd_{h}"] for r in subset])
        print(f"  {name:>15} {len(subset):>6} {np.mean(fwds):>10.4f} "
              f"{np.median(fwds):>10.4f} {100*np.mean(fwds>0):>7.1f}%")

# === Summary: which features show any signal? ===
print(f"\n{'='*70}")
print("SUMMARY: Max forward return spread (P0-P10 vs P90-P100)")
print(f"{'='*70}")
print(f"  {'Feature':>12} {'H=1':>10} {'H=3':>10} {'H=5':>10}")
print(f"  {'-'*45}")
for feat_name in features:
    spreads = []
    for h in [1, 3, 5]:
        rows = analyze_feature(feat_name, h)
        if len(rows) >= 2:
            bottom = rows[0][2]  # mean fwd of lowest bucket
            top = rows[-1][2]    # mean fwd of highest bucket
            spreads.append(top - bottom)
        else:
            spreads.append(0)
    print(f"  {feat_name:>12} {spreads[0]:>10.4f} {spreads[1]:>10.4f} {spreads[2]:>10.4f}")

print(f"\nRuntime: {time.time()-t0:.0f}s")
