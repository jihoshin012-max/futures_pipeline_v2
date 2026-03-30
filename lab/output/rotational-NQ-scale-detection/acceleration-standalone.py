# archetype: rotational
"""Standalone acceleration analysis: does tangential acceleration predict
short-term price behavior on 250-tick bars, independent of the strategy?

Question: at bar N, if we know the acceleration of price displacement,
does that predict where price goes over the next 1-5 bars?

Acceleration = d²s/dt² = (speed[i] - speed[i-1]) / bar_duration[i]
where speed = (close[i] - close[i-1]) / bar_duration[i]
"""
import sys, importlib, time
import numpy as np
from collections import defaultdict

sys.path.insert(0, r"c:\Projects\futures_pipeline\lab")

_engine = importlib.import_module("rotational-NQ-scale-detection-engine")
load_bars_extended = _engine.load_bars_extended
aggregate_to_ntick = _engine.aggregate_to_ntick

BS = 250

print("Loading P1...")
t0 = time.time()
bars = load_bars_extended(r"C:\Projects\futures_pipeline\data\NQ-1tick-calibration.csv")
print(f"  {bars['n']} ticks ({time.time()-t0:.0f}s)")

print("Aggregating (SC-aligned)...")
agg, t2a = aggregate_to_ntick(bars, BS)
n_agg = agg["n"]
print(f"  {n_agg} agg bars")

close = agg["last"]
high = agg["high"]
low = agg["low"]
tsec = agg["time_sec"]
dint = agg["date_int"]

# Compute bar duration from tick data
# Each agg bar spans 250 ticks. Duration = time of last tick - time of first tick.
# Approximate: use time_sec difference between consecutive agg bars.
bar_duration = np.full(n_agg, np.nan, dtype=np.float64)
for i in range(1, n_agg):
    # Duration approximation: time between bar i start and bar i-1 start
    # Better: use the actual time span within the bar
    # Since we have time_sec per agg bar (first tick's time), consecutive difference works
    dt = tsec[i] - tsec[i - 1]
    # Handle session boundary (overnight gap)
    if dint[i] != dint[i - 1] or dt <= 0 or dt > 3600:
        bar_duration[i] = np.nan
    else:
        bar_duration[i] = float(dt)

# Compute speed and acceleration
speed = np.full(n_agg, np.nan, dtype=np.float64)
accel = np.full(n_agg, np.nan, dtype=np.float64)

for i in range(1, n_agg):
    if np.isnan(bar_duration[i]) or bar_duration[i] < 0.1:
        continue
    speed[i] = (close[i] - close[i - 1]) / bar_duration[i]

for i in range(2, n_agg):
    if np.isnan(speed[i]) or np.isnan(speed[i - 1]):
        continue
    if np.isnan(bar_duration[i]) or bar_duration[i] < 0.1:
        continue
    accel[i] = (speed[i] - speed[i - 1]) / bar_duration[i]

# Filter to RTH only (09:30 = 34200 to 15:50 = 57000)
RTH_OPEN = 34200
RTH_CLOSE = 57000

# For each bar with valid acceleration, measure what happens next
# Forward returns: price change over next 1, 2, 3, 5 bars
horizons = [1, 2, 3, 5]

print(f"\nComputing forward returns at horizons {horizons}...")

records = []
for i in range(2, n_agg - max(horizons)):
    if np.isnan(accel[i]):
        continue
    # RTH filter
    if tsec[i] < RTH_OPEN or tsec[i] > RTH_CLOSE:
        continue
    # Skip session boundaries in forward window
    skip = False
    for h in horizons:
        if dint[i + h] != dint[i]:
            skip = True
            break
    if skip:
        continue

    fwd = {}
    for h in horizons:
        fwd[h] = float(close[i + h] - close[i])

    records.append({
        "bar": i,
        "speed": speed[i],
        "accel": accel[i],
        "bar_range": float(high[i] - low[i]),
        **{f"fwd_{h}": fwd[h] for h in horizons},
    })

print(f"  {len(records)} valid RTH bars with acceleration + forward returns")

# === Analysis 1: Acceleration buckets vs forward returns ===
print(f"\n{'='*70}")
print("ANALYSIS 1: Acceleration buckets -> forward return (pts)")
print(f"{'='*70}")

accel_vals = np.array([r["accel"] for r in records])
pcts = [0, 10, 25, 40, 50, 60, 75, 90, 100]
boundaries = np.percentile(accel_vals, pcts)

for h in horizons:
    print(f"\n  Horizon: {h} bar(s)")
    print(f"  {'Accel bucket':>20} {'N':>6} {'Mean fwd':>10} {'Median fwd':>10} {'% positive':>10}")
    print(f"  {'-'*60}")

    for j in range(len(pcts) - 1):
        lo, hi = boundaries[j], boundaries[j + 1]
        if j == len(pcts) - 2:
            subset = [r for r in records if r["accel"] >= lo]
        else:
            subset = [r for r in records if lo <= r["accel"] < hi]
        if not subset:
            continue
        fwds = np.array([r[f"fwd_{h}"] for r in subset])
        label = f"P{pcts[j]}-P{pcts[j+1]}"
        pct_pos = 100 * np.mean(fwds > 0)
        print(f"  {label:>20} {len(subset):>6} {np.mean(fwds):>10.4f} {np.median(fwds):>10.4f} {pct_pos:>9.1f}%")

# === Analysis 2: Signed acceleration (direction-aware) ===
print(f"\n{'='*70}")
print("ANALYSIS 2: Does acceleration direction predict continuation?")
print("  Positive accel = speed increasing (move accelerating)")
print("  Negative accel = speed decreasing (move decelerating)")
print(f"{'='*70}")

for h in horizons:
    pos_accel = [r for r in records if r["accel"] > 0]
    neg_accel = [r for r in records if r["accel"] < 0]

    pos_fwd = np.array([r[f"fwd_{h}"] for r in pos_accel])
    neg_fwd = np.array([r[f"fwd_{h}"] for r in neg_accel])

    # For continuation: does positive acceleration predict positive forward return?
    # Compute correlation between accel sign and forward return sign
    pos_same_sign = np.mean([r[f"fwd_{h}"] * r["speed"] > 0 for r in pos_accel if abs(r["speed"]) > 0.001])
    neg_same_sign = np.mean([r[f"fwd_{h}"] * r["speed"] > 0 for r in neg_accel if abs(r["speed"]) > 0.001])

    print(f"\n  Horizon {h}: accel>0 continuation rate: {100*pos_same_sign:.1f}% "
          f"| accel<0 continuation rate: {100*neg_same_sign:.1f}%")

# === Analysis 3: Extreme acceleration ===
print(f"\n{'='*70}")
print("ANALYSIS 3: Extreme acceleration (top/bottom 10%)")
print(f"{'='*70}")

p10 = np.percentile(accel_vals, 10)
p90 = np.percentile(accel_vals, 90)

for h in horizons:
    extreme_neg = [r for r in records if r["accel"] <= p10]
    extreme_pos = [r for r in records if r["accel"] >= p90]
    middle = [r for r in records if p10 < r["accel"] < p90]

    en_fwd = np.array([r[f"fwd_{h}"] for r in extreme_neg])
    ep_fwd = np.array([r[f"fwd_{h}"] for r in extreme_pos])
    mid_fwd = np.array([r[f"fwd_{h}"] for r in middle])

    print(f"\n  Horizon {h}:")
    print(f"    Bottom 10% accel: mean_fwd={np.mean(en_fwd):+.4f} pts, "
          f"median={np.median(en_fwd):+.4f}, N={len(extreme_neg)}")
    print(f"    Middle 80%:       mean_fwd={np.mean(mid_fwd):+.4f} pts, "
          f"median={np.median(mid_fwd):+.4f}, N={len(middle)}")
    print(f"    Top 10% accel:    mean_fwd={np.mean(ep_fwd):+.4f} pts, "
          f"median={np.median(ep_fwd):+.4f}, N={len(extreme_pos)}")

# === Analysis 4: Speed + acceleration combined ===
print(f"\n{'='*70}")
print("ANALYSIS 4: Speed × Acceleration quadrants")
print("  Q1: fast + accelerating | Q2: fast + decelerating")
print("  Q3: slow + accelerating | Q4: slow + decelerating")
print(f"{'='*70}")

speed_vals = np.array([r["speed"] for r in records])
speed_median = np.median(np.abs(speed_vals))

for h in [1, 3]:
    print(f"\n  Horizon {h}:")
    quadrants = {
        "Q1 fast+accel": [r for r in records if abs(r["speed"]) >= speed_median and r["accel"] > 0],
        "Q2 fast+decel": [r for r in records if abs(r["speed"]) >= speed_median and r["accel"] <= 0],
        "Q3 slow+accel": [r for r in records if abs(r["speed"]) < speed_median and r["accel"] > 0],
        "Q4 slow+decel": [r for r in records if abs(r["speed"]) < speed_median and r["accel"] <= 0],
    }
    print(f"  {'Quadrant':>20} {'N':>6} {'Mean fwd':>10} {'|fwd| mean':>10} {'% continuation':>14}")
    for name, subset in quadrants.items():
        if not subset:
            continue
        fwds = np.array([r[f"fwd_{h}"] for r in subset])
        # Continuation = fwd same sign as current speed
        cont = np.mean([r[f"fwd_{h}"] * r["speed"] > 0 for r in subset if abs(r["speed"]) > 0.001])
        print(f"  {name:>20} {len(subset):>6} {np.mean(fwds):>10.4f} {np.mean(np.abs(fwds)):>10.4f} {100*cont:>13.1f}%")

print(f"\n{'='*70}")
print(f"Runtime: {time.time()-t0:.0f}s")
