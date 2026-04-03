"""
Speed analysis of 250-tick NQ bar data.
Computes bar duration distributions, speed-range cross-tabs,
speed regime/band behavior, speed transitions, and intraday speed profiles.
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime

OUT = Path(r"c:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")

# Load data
df = pd.read_csv(r"c:\Projects\futures_pipeline\data\NQ-250tick-calibration.csv")
df.columns = df.columns.str.strip()

# Parse datetime
df['datetime'] = pd.to_datetime(df['Date'].str.strip() + ' ' + df['Time'].str.strip(),
                                 format='%m/%d/%Y %H:%M:%S.%f')

# Bar duration in seconds (diff between consecutive bars)
df['duration_s'] = df['datetime'].diff().dt.total_seconds()

# Bar range
df['range'] = df['High'] - df['Low']
df['abs_co'] = (df['Last'] - df['Open']).abs()

# Drop first row (no duration) and session gaps (duration > 3600s = 1hr)
mask = (df['duration_s'].notna()) & (df['duration_s'] > 0) & (df['duration_s'] <= 3600)
work = df[mask].copy()

print(f"Total bars: {len(df)}, Bars with valid duration: {len(work)}")

# ============================================================
# 1. Bar Duration Analysis
# ============================================================

dur = work['duration_s']
stats = {
    'count': len(dur),
    'mean': dur.mean(),
    'median': dur.median(),
    'p10': dur.quantile(0.10),
    'p25': dur.quantile(0.25),
    'p75': dur.quantile(0.75),
    'p90': dur.quantile(0.90),
    'std': dur.std(),
    'min': dur.min(),
    'max': dur.max(),
}

# Speed buckets
def speed_bucket(s):
    if s < 10:
        return 'fast'
    elif s < 30:
        return 'moderate'
    elif s <= 120:
        return 'slow'
    else:
        return 'very_slow'

work['speed_bucket'] = work['duration_s'].apply(speed_bucket)

bucket_order = ['fast', 'moderate', 'slow', 'very_slow']
bucket_stats = work.groupby('speed_bucket').agg(
    count=('duration_s', 'size'),
    avg_range=('range', 'mean'),
    avg_abs_co=('abs_co', 'mean'),
    avg_duration=('duration_s', 'mean'),
    median_duration=('duration_s', 'median'),
).reindex(bucket_order)
bucket_stats['pct_of_total'] = (bucket_stats['count'] / len(work) * 100).round(2)

bucket_stats.to_csv(OUT / 'speed-bucket-stats.csv')
print("\n=== Speed Bucket Stats ===")
print(bucket_stats)

# ============================================================
# 2. Speed + Range Cross-Tabulation
# ============================================================

work['range_quintile'] = pd.qcut(work['range'], 5, labels=['Q1_narrow', 'Q2', 'Q3', 'Q4', 'Q5_wide'], duplicates='drop')

cross = pd.crosstab(work['speed_bucket'], work['range_quintile'])
cross = cross.reindex(bucket_order)
cross_pct = (cross / len(work) * 100).round(3)

cross.to_csv(OUT / 'speed-range-crosstab-count.csv')
cross_pct.to_csv(OUT / 'speed-range-crosstab-pct.csv')

print("\n=== Speed x Range Cross-Tab (counts) ===")
print(cross)
print("\n=== Speed x Range Cross-Tab (% of total) ===")
print(cross_pct)

# ============================================================
# 3. Speed Regime and Band Behavior
# ============================================================

# Rolling 50-bar stdDev of Last
work['roll50_std'] = work['Last'].rolling(50).std()

# Band touches: define inner/outer bands using stdDev multiples from rolling mean
roll50_mean = work['Last'].rolling(50).mean()
work['inner_band_touch'] = ((work['High'] >= roll50_mean + work['roll50_std']) |
                             (work['Low'] <= roll50_mean - work['roll50_std'])).astype(int)
work['outer_band_touch'] = ((work['High'] >= roll50_mean + 2 * work['roll50_std']) |
                             (work['Low'] <= roll50_mean - 2 * work['roll50_std'])).astype(int)

# Also check if Roll50 column exists in original data
if 'Roll50' in df.columns:
    work['roll50_from_data'] = df.loc[work.index, 'Roll50']

band_by_speed = work.dropna(subset=['roll50_std']).groupby('speed_bucket').agg(
    count=('duration_s', 'size'),
    avg_stddev=('roll50_std', 'mean'),
    inner_touch_rate=('inner_band_touch', 'mean'),
    outer_touch_rate=('outer_band_touch', 'mean'),
).reindex(bucket_order)
band_by_speed['inner_touch_rate'] = (band_by_speed['inner_touch_rate'] * 100).round(2)
band_by_speed['outer_touch_rate'] = (band_by_speed['outer_touch_rate'] * 100).round(2)

band_by_speed.to_csv(OUT / 'speed-band-behavior.csv')

# Rolling 10-bar avg duration
work['roll10_duration'] = work['duration_s'].rolling(10).mean()

# Correlation between rolling duration and stdDev
valid = work.dropna(subset=['roll10_duration', 'roll50_std'])
corr = valid['roll10_duration'].corr(valid['roll50_std'])

print("\n=== Band Behavior by Speed Bucket ===")
print(band_by_speed)
print(f"\nCorrelation(roll10_duration, roll50_std): {corr:.4f}")

# ============================================================
# 4. Speed Transitions
# ============================================================

work['roll10_dur'] = work['duration_s'].rolling(10).mean()

# Detect slow-to-fast transitions using two approaches:
# Strict: rolling 10-bar avg drops below 15s after being above 30s on prior bar
# Relaxed: rolling 10-bar avg drops below 20s after being above 30s within last 5 bars
work['was_slow'] = work['roll10_dur'].shift(1) > 30
work['now_fast'] = work['roll10_dur'] < 15
work['slow_to_fast'] = work['was_slow'] & work['now_fast']

# Relaxed: was above 30s at any point in last 5 bars, now below 20s
work['was_slow_5'] = work['roll10_dur'].rolling(5).max().shift(1) > 30
work['now_fast_relaxed'] = work['roll10_dur'] < 20
work['slow_to_fast_relaxed'] = work['was_slow_5'] & work['now_fast_relaxed']
# Deduplicate: only keep first occurrence in each cluster (gap > 10 bars)
relaxed_idx = work.index[work['slow_to_fast_relaxed']]
if len(relaxed_idx) > 0:
    keep = [relaxed_idx[0]]
    for i in range(1, len(relaxed_idx)):
        if work.index.get_loc(relaxed_idx[i]) - work.index.get_loc(relaxed_idx[i-1]) > 10:
            keep.append(relaxed_idx[i])
    work['slow_to_fast_relaxed_dedup'] = False
    work.loc[keep, 'slow_to_fast_relaxed_dedup'] = True
else:
    work['slow_to_fast_relaxed_dedup'] = False

# StdDev expansion: rolling 50-bar std increases by >20% over next 20 bars
work['future_std_20'] = work['roll50_std'].shift(-20)
work['std_expansion'] = work['future_std_20'] > work['roll50_std'] * 1.2

transitions_strict = work[work['slow_to_fast']].copy()
transitions_relaxed = work[work['slow_to_fast_relaxed_dedup']].copy()

def analyze_transitions(trans, label):
    n = len(trans)
    valid = trans.dropna(subset=['roll50_std', 'future_std_20'])
    n_expansion = int(valid['std_expansion'].sum()) if len(valid) > 0 else 0

    lag_to_touch = []
    for idx in trans.index:
        pos = work.index.get_loc(idx)
        future = work.iloc[pos:pos+50]
        touches = future[future['inner_band_touch'] == 1]
        if len(touches) > 0:
            lag = work.index.get_loc(touches.index[0]) - pos
            lag_to_touch.append(lag)

    return {
        'label': label,
        'n_transitions': n,
        'n_valid': len(valid),
        'n_expansion': n_expansion,
        'pct_expansion': n_expansion / len(valid) * 100 if len(valid) > 0 else 0,
        'avg_lag': np.mean(lag_to_touch) if lag_to_touch else None,
        'median_lag': np.median(lag_to_touch) if lag_to_touch else None,
        'n_with_touch': len(lag_to_touch),
    }

strict_res = analyze_transitions(transitions_strict, "Strict (>30s -> <15s, consecutive)")
relaxed_res = analyze_transitions(transitions_relaxed, "Relaxed (>30s in last 5 -> <20s, deduped)")

for r in [strict_res, relaxed_res]:
    print(f"\n=== Speed Transitions: {r['label']} ===")
    print(f"  Transitions: {r['n_transitions']}")
    if r['n_valid'] > 0:
        print(f"  StdDev expansion within 20 bars: {r['n_expansion']}/{r['n_valid']} ({r['pct_expansion']:.1f}%)")
    if r['avg_lag'] is not None:
        print(f"  Avg lag to band touch: {r['avg_lag']:.1f} bars (median {r['median_lag']:.1f})")
        print(f"  Transitions with touch within 50 bars: {r['n_with_touch']}/{r['n_transitions']}")

# ============================================================
# 5. Intraday Speed Profile
# ============================================================

# Convert to ET (data appears to already be ET based on 18:00 start)
work['hour'] = work['datetime'].dt.hour

# Count trading days
n_days = work['datetime'].dt.date.nunique()

hourly = work.groupby('hour').agg(
    bar_count=('duration_s', 'size'),
    avg_duration=('duration_s', 'mean'),
    median_duration=('duration_s', 'median'),
    avg_range=('range', 'mean'),
    avg_abs_co=('abs_co', 'mean'),
).round(3)
hourly['bars_per_hour'] = (hourly['bar_count'] / n_days).round(1)

hourly.to_csv(OUT / 'speed-intraday-profile.csv')

print(f"\n=== Intraday Speed Profile (ET, {n_days} trading days) ===")
print(hourly)

# ============================================================
# Write summary
# ============================================================

with open(OUT / 'speed-analysis-summary.txt', 'w') as f:
    f.write("SPEED ANALYSIS — NQ 250-Tick Bars\n")
    f.write("=" * 60 + "\n\n")
    f.write(f"Data: NQ-250tick-calibration.csv\n")
    f.write(f"Total bars: {len(df)}\n")
    f.write(f"Bars with valid duration (<=3600s gap): {len(work)}\n")
    f.write(f"Trading days: {n_days}\n")
    f.write(f"Date range: {work['datetime'].min()} to {work['datetime'].max()}\n\n")

    f.write("1. BAR DURATION DISTRIBUTION\n")
    f.write("-" * 40 + "\n")
    for k, v in stats.items():
        f.write(f"  {k:>8}: {v:.2f}\n")
    f.write("\n")

    f.write("  Speed Bucket Breakdown:\n")
    f.write(f"  {'Bucket':<12} {'Count':>8} {'%':>7} {'AvgRange':>10} {'AvgAbsCO':>10} {'AvgDur':>8} {'MedDur':>8}\n")
    for b in bucket_order:
        row = bucket_stats.loc[b]
        f.write(f"  {b:<12} {int(row['count']):>8} {row['pct_of_total']:>6.1f}% "
                f"{row['avg_range']:>10.2f} {row['avg_abs_co']:>10.2f} "
                f"{row['avg_duration']:>8.1f} {row['median_duration']:>8.1f}\n")
    f.write("\n")

    f.write("2. SPEED x RANGE CROSS-TABULATION\n")
    f.write("-" * 40 + "\n")
    f.write("  Counts:\n")
    f.write("    " + cross.to_string().replace("\n", "\n    ") + "\n\n")
    f.write("  Percentages of total:\n")
    f.write("    " + cross_pct.to_string().replace("\n", "\n    ") + "\n\n")

    # Range quintile boundaries
    range_cuts = pd.qcut(work['range'], 5, retbins=True, duplicates='drop')[1]
    f.write("  Range quintile boundaries: " + ", ".join(f"{x:.2f}" for x in range_cuts) + "\n\n")

    f.write("3. SPEED REGIME AND BAND BEHAVIOR\n")
    f.write("-" * 40 + "\n")
    f.write("  Band definition: inner = mean +/- 1*stdDev, outer = mean +/- 2*stdDev\n")
    f.write("  Using rolling 50-bar window for mean and stdDev of Last price.\n\n")
    f.write(f"  {'Bucket':<12} {'Count':>8} {'AvgStdDev':>10} {'InnerTouch%':>12} {'OuterTouch%':>12}\n")
    for b in bucket_order:
        row = band_by_speed.loc[b]
        f.write(f"  {b:<12} {int(row['count']):>8} {row['avg_stddev']:>10.2f} "
                f"{row['inner_touch_rate']:>11.1f}% {row['outer_touch_rate']:>11.1f}%\n")
    f.write(f"\n  Correlation(roll10_bar_duration, roll50_stdDev): {corr:.4f}\n\n")

    f.write("4. SPEED TRANSITIONS (slow->fast)\n")
    f.write("-" * 40 + "\n")
    f.write(f"  StdDev expansion defined as: roll50_std increases >20% within 20 bars.\n\n")
    for r in [strict_res, relaxed_res]:
        f.write(f"  {r['label']}:\n")
        f.write(f"    Transitions detected: {r['n_transitions']}\n")
        if r['n_valid'] > 0:
            f.write(f"    StdDev expansion within 20 bars: {r['n_expansion']}/{r['n_valid']} "
                    f"({r['pct_expansion']:.1f}%)\n")
        if r['avg_lag'] is not None:
            f.write(f"    Avg lag to inner band touch: {r['avg_lag']:.1f} bars (median {r['median_lag']:.1f})\n")
            f.write(f"    Transitions with touch within 50 bars: {r['n_with_touch']}/{r['n_transitions']}\n")
        f.write("\n")

    f.write("5. INTRADAY SPEED PROFILE (by hour, ET)\n")
    f.write("-" * 40 + "\n")
    f.write(f"  Trading days in sample: {n_days}\n\n")
    f.write(f"  {'Hour':>4} {'Bars':>8} {'Bars/Day':>9} {'AvgDur':>8} {'MedDur':>8} {'AvgRange':>10}\n")
    for h in hourly.index:
        row = hourly.loc[h]
        f.write(f"  {h:>4} {int(row['bar_count']):>8} {row['bars_per_hour']:>9.1f} "
                f"{row['avg_duration']:>8.1f} {row['median_duration']:>8.1f} "
                f"{row['avg_range']:>10.2f}\n")

print("\n=== Output files written ===")
print("  speed-bucket-stats.csv")
print("  speed-range-crosstab-count.csv")
print("  speed-range-crosstab-pct.csv")
print("  speed-band-behavior.csv")
print("  speed-intraday-profile.csv")
print("  speed-analysis-summary.txt")
