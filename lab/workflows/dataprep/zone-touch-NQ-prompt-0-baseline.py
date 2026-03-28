# archetype: zone-touch
"""
Zone Touch NQ — Prompt 0: Data Preparation & Baseline
Implements: lab/workflows/dataprep/zone-touch-NQ-prompt-0-baseline.md

Calibration only. No features, no scoring, no filtering.
Every touch is measured equally. Outputs raw trade results
and summary statistics to lab/output/.
"""

import sys
import os

# Force UTF-8 output on Windows (cp1252 can't encode box-drawing/math chars)
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import timedelta
import time as _time

# ═══════════════════════════════════════════════════════════════
# CONFIGURATION — from _config/, never hardcoded
# ═══════════════════════════════════════════════════════════════

ROOT = Path(__file__).resolve().parent.parent.parent.parent  # lab/workflows/dataprep/ -> pipeline root
DATA = ROOT / 'data'
OUTPUT = ROOT / 'lab' / 'output'

# _config/instruments.md — NQ
TICK_SIZE = 0.25
TICK_VALUE = 5.00
EXPERIMENT_COST_TICKS = 3

# _config/period-config.md
# Prompt 0 uses ALL data (no fitting = no contamination)
CAL_START = pd.Timestamp('2025-09-21')
CAL_END = pd.Timestamp('2025-12-14 23:59:59')
HO_START = pd.Timestamp('2025-12-17')
HO_END = pd.Timestamp('2026-03-13 23:59:59')

# Prompt 0 Part B2 — sweep variables
ENTRY_OFFSETS = [0, 10, 20, 40]        # ticks inside zone edge
STOP_BUFFERS = [0, 5, 10, 20]          # ticks past structural level
TIME_CAPS_MIN = [30, 60, 120, 240]     # minutes

# Comeback thresholds (points, not ticks)
COMEBACK_THRESHOLDS_PTS = [10, 20, 30]
COMEBACK_RETURN_TICKS = 5              # within 5 ticks of entry edge

# Reference sweep for concurrent exposure / resolved_seq
REF_OFFSET = 0
REF_BUFFER = 10
REF_CAP = 120

# _config/instruments.md — NQ session times
RTH_START_TIME = pd.Timestamp('1900-01-01 09:30:00').time()
RTH_END_TIME = pd.Timestamp('1900-01-01 15:50:00').time()  # 15:50 per prompt spec

# TF ordering for analysis
TF_ORDER = ['15m', '30m', '60m', '90m', '120m', '240m', '360m', '480m', '720m']
TF_MINUTES = {'15m': 15, '30m': 30, '60m': 60, '90m': 90, '120m': 120,
              '240m': 240, '360m': 360, '480m': 480, '720m': 720}


def compute_pf(subset, r_target):
    """Profit Factor at a given R target. PF = gross wins / gross losses."""
    risk = subset['RiskTicks']
    target_ticks = risk * r_target
    win_mask = subset['MFE_Ticks'] >= target_ticks
    gross_win = (win_mask * target_ticks).sum()
    gross_loss = (~win_mask * risk).sum()
    return gross_win / gross_loss if gross_loss > 0 else np.inf


def is_rth(dt):
    """Check if datetime falls within RTH (09:30-15:50 ET)."""
    t = dt.time() if hasattr(dt, 'time') else pd.Timestamp(dt).time()
    return RTH_START_TIME <= t <= RTH_END_TIME


def tag_session(sim_df):
    """Add Session column (RTH/ETH) based on DateTime."""
    times = pd.to_datetime(sim_df['DateTime']).dt.time
    sim_df['Session'] = np.where(
        (times >= RTH_START_TIME) & (times <= RTH_END_TIME),
        'RTH', 'ETH'
    )
    return sim_df


# ═══════════════════════════════════════════════════════════════
# DATA LOADING
# ═══════════════════════════════════════════════════════════════

def load_zte():
    """Load ZTE calibration + holdout data, remove VP_RAY, drop SC columns."""
    print("[load] ZTE data (calibration + holdout)...")
    dfs = []
    for role in ['calibration', 'holdout']:
        path = DATA / f'NQ-zte-{role}.csv'
        d = pd.read_csv(path)
        d['Period'] = role
        print(f"  {role}: {len(d)} rows")
        dfs.append(d)
    df = pd.concat(dfs, ignore_index=True)
    n_raw = len(df)

    # Remove VP_RAY
    vp_count = (df['TouchType'] == 'VP_RAY').sum()
    df = df[df['TouchType'] != 'VP_RAY'].copy()

    # Drop SC internal columns
    df = df.drop(columns=['SourceChart', 'SourceStudyID'], errors='ignore')

    # Parse datetime
    df['DateTime'] = pd.to_datetime(df['DateTime'])
    df = df.sort_values('DateTime').reset_index(drop=True)

    # Build TouchID for ray join
    df['TouchID'] = (df['BarIndex'].astype(str) + '_'
                     + df['TouchType'] + '_'
                     + df['SourceLabel'])

    print(f"  combined: raw={n_raw}, VP_RAY removed={vp_count}, "
          f"total touches: {len(df)}")
    return df


def load_tick_data():
    """Load 1-tick bar data (Date, Time, Last only) for both periods."""
    print("[load] 1-tick data (calibration + holdout, this may take a few minutes)...")
    t0 = _time.time()

    dfs = []
    for role in ['calibration', 'holdout']:
        path = DATA / f'NQ-1tick-{role}.csv'
        d = pd.read_csv(
            path,
            usecols=[0, 1, 5],
            skipinitialspace=True,
            dtype={0: str, 1: str, 5: np.float64},
            header=0,
        )
        d.columns = ['Date', 'Time', 'Last']
        d['DateTime'] = pd.to_datetime(d['Date'] + ' ' + d['Time'])
        d = d[['DateTime', 'Last']]
        print(f"  {role}: {len(d):,} rows")
        dfs.append(d)

    df = pd.concat(dfs, ignore_index=True)
    df = df.sort_values('DateTime').reset_index(drop=True)

    elapsed = _time.time() - t0
    print(f"  combined: {len(df):,} rows, loaded in {elapsed:.1f}s")
    print(f"  range: {df['DateTime'].iloc[0]} -> {df['DateTime'].iloc[-1]}")
    return df


def load_rays():
    """Load ray context data (both periods), filter TF >= 60m."""
    print("[load] Ray context data (calibration + holdout)...")
    dfs = []
    for role in ['calibration', 'holdout']:
        path = DATA / f'NQ-ray-context-{role}.csv'
        d = pd.read_csv(path)
        print(f"  {role}: {len(d):,} rows")
        dfs.append(d)
    df = pd.concat(dfs, ignore_index=True)
    n_raw = len(df)

    # Filter: TF >= 60m
    tf_min = df['RayTF'].map(TF_MINUTES)
    tf_removed = (tf_min < 60).sum()
    df = df[tf_min >= 60].copy()

    print(f"  raw: {n_raw:,}, TF<60m removed: {tf_removed:,}, "
          f"remaining: {len(df):,}")
    return df


def load_ray_reference():
    """Load ray reference data (both periods)."""
    print("[load] Ray reference data (calibration + holdout)...")
    dfs = []
    for role in ['calibration', 'holdout']:
        path = DATA / f'NQ-ray-reference-{role}.csv'
        d = pd.read_csv(path)
        dfs.append(d)
    df = pd.concat(dfs, ignore_index=True)
    print(f"  combined: {len(df):,} rows")
    return df


# ═══════════════════════════════════════════════════════════════
# PART A: ZONE GEOMETRY ANALYSIS
# ═══════════════════════════════════════════════════════════════

def part_a1_zone_width(zte):
    """A1: Zone width distribution by timeframe."""
    print("\n[A1] Zone width distribution by timeframe")
    results = {}
    for tf in TF_ORDER:
        subset = zte[zte['SourceLabel'] == tf]
        if len(subset) == 0:
            continue
        w = subset['ZoneWidthTicks']
        results[tf] = {
            'count': len(subset),
            'min': w.min(),
            'p25': w.quantile(0.25),
            'median': w.median(),
            'p75': w.quantile(0.75),
            'p90': w.quantile(0.90),
            'p95': w.quantile(0.95),
            'max': w.max(),
            'width_pts_median': w.median() * TICK_SIZE,
        }
        print(f"  {tf:>5s}: n={results[tf]['count']:>4d}  "
              f"median={results[tf]['median']:.0f}t "
              f"({results[tf]['width_pts_median']:.1f}pts)  "
              f"P90={results[tf]['p90']:.0f}t")

    # All timeframes combined
    w = zte['ZoneWidthTicks']
    results['ALL'] = {
        'count': len(zte),
        'min': w.min(), 'p25': w.quantile(0.25),
        'median': w.median(), 'p75': w.quantile(0.75),
        'p90': w.quantile(0.90), 'p95': w.quantile(0.95),
        'max': w.max(),
        'width_pts_median': w.median() * TICK_SIZE,
    }
    print(f"  {'ALL':>5s}: n={len(zte):>4d}  "
          f"median={results['ALL']['median']:.0f}t  "
          f"P90={results['ALL']['p90']:.0f}t")
    return results


def part_a2_ray_availability(zte, rays):
    """A2: Ray availability after TF>=60m filter."""
    print("\n[A2] Ray availability (filtered rays)")

    # Join rays to touches
    touch_ids_with_rays = rays['TouchID'].unique()
    zte_with_ray = zte['TouchID'].isin(touch_ids_with_rays)
    ray_rate = zte_with_ray.mean()

    # Ray count per touch
    ray_counts = rays.groupby('TouchID').size()
    touch_ray_counts = zte[['TouchID']].merge(
        ray_counts.rename('RayCount'), left_on='TouchID',
        right_index=True, how='left'
    )
    touch_ray_counts['RayCount'] = touch_ray_counts['RayCount'].fillna(0).astype(int)

    # Ray position within zone
    ray_detail = rays.merge(
        zte[['TouchID', 'ZoneTop', 'ZoneBot', 'ZoneWidthTicks', 'TouchType']],
        on='TouchID', how='left'
    )
    zone_width_price = (ray_detail['ZoneTop'] - ray_detail['ZoneBot'])
    ray_position = np.where(
        ray_detail['TouchType'] == 'DEMAND_EDGE',
        (ray_detail['ZoneTop'] - ray_detail['RayPrice']) / zone_width_price,
        (ray_detail['RayPrice'] - ray_detail['ZoneBot']) / zone_width_price,
    )
    ray_detail['RelPosition'] = ray_position  # 0=near touch edge, 1=near opposite

    results = {
        'availability_rate': ray_rate,
        'touches_with_ray': zte_with_ray.sum(),
        'touches_total': len(zte),
        'ray_count_dist': touch_ray_counts['RayCount'].value_counts().sort_index().to_dict(),
        'ray_position_median': np.nanmedian(ray_detail['RelPosition']),
        'ray_position_p25': np.nanpercentile(ray_detail['RelPosition'], 25),
        'ray_position_p75': np.nanpercentile(ray_detail['RelPosition'], 75),
    }

    print(f"  ray availability: {ray_rate:.1%} "
          f"({results['touches_with_ray']}/{results['touches_total']})")
    print(f"  rays per touch dist: {results['ray_count_dist']}")
    print(f"  ray position (0=touch edge, 1=opposite): "
          f"median={results['ray_position_median']:.2f}")
    return results


def part_a3_nested_zones(zte):
    """A3: Nested zone frequency — smaller TF zone inside larger TF zone at touch time."""
    print("\n[A3] Nested zone frequency")

    # Identify unique zones: (ZoneTop, ZoneBot, SourceLabel, TouchType)
    # A zone is "alive" from first touch to break (or last observation)
    zones = zte.groupby(['ZoneTop', 'ZoneBot', 'SourceLabel', 'TouchType']).agg(
        first_touch=('DateTime', 'min'),
        last_touch=('DateTime', 'max'),
        broken=('ZoneBroken', 'max'),
    ).reset_index()
    zones['TF_min'] = zones['SourceLabel'].map(TF_MINUTES)

    nested_count = 0
    checked_count = 0

    # For each touch on a zone with TF >= 60m, check for nested smaller zones
    large_tf = zte[zte['SourceLabel'].map(TF_MINUTES) >= 60].copy()
    for _, touch in large_tf.iterrows():
        checked_count += 1
        zt, zb = touch['ZoneTop'], touch['ZoneBot']
        tf_min = TF_MINUTES[touch['SourceLabel']]
        dt = touch['DateTime']

        # Find zones with smaller TF, contained within this zone,
        # that were alive at touch time
        candidates = zones[
            (zones['TF_min'] < tf_min)
            & (zones['ZoneTop'] <= zt)
            & (zones['ZoneBot'] >= zb)
            & (zones['first_touch'] <= dt)
            & (zones['TouchType'] == touch['TouchType'])
        ]
        if len(candidates) > 0:
            nested_count += 1

    nested_rate = nested_count / checked_count if checked_count > 0 else 0

    # Breakdown by outer zone TF
    results_by_tf = {}
    for tf in TF_ORDER:
        tf_m = TF_MINUTES[tf]
        if tf_m < 60:
            continue
        subset = large_tf[large_tf['SourceLabel'] == tf]
        if len(subset) == 0:
            continue
        tf_nested = 0
        for _, touch in subset.iterrows():
            zt, zb = touch['ZoneTop'], touch['ZoneBot']
            dt = touch['DateTime']
            candidates = zones[
                (zones['TF_min'] < tf_m)
                & (zones['ZoneTop'] <= zt)
                & (zones['ZoneBot'] >= zb)
                & (zones['first_touch'] <= dt)
                & (zones['TouchType'] == touch['TouchType'])
            ]
            if len(candidates) > 0:
                tf_nested += 1
        rate = tf_nested / len(subset) if len(subset) > 0 else 0
        results_by_tf[tf] = {'count': len(subset), 'nested': tf_nested, 'rate': rate}
        print(f"  {tf}: {rate:.1%} nested ({tf_nested}/{len(subset)})")

    results = {
        'overall_rate': nested_rate,
        'checked': checked_count,
        'nested': nested_count,
        'by_tf': results_by_tf,
    }
    print(f"  overall nested rate (TF>=60m): {nested_rate:.1%}")
    return results


# ═══════════════════════════════════════════════════════════════
# PART B: TICK-LEVEL BASELINE SIMULATION
# ═══════════════════════════════════════════════════════════════

def find_tick_index(touch_dt, tick_datetimes):
    """Binary search for the tick at or just after touch_dt."""
    idx = np.searchsorted(tick_datetimes, touch_dt, side='left')
    return int(idx) if idx < len(tick_datetimes) else -1


def find_best_ray_stop(touch, rays_for_touch):
    """Find the nearest valid ray inside the zone for use as stop."""
    if rays_for_touch is None or len(rays_for_touch) == 0:
        return None

    is_demand = touch['TouchType'] == 'DEMAND_EDGE'
    zone_top = touch['ZoneTop']
    zone_bot = touch['ZoneBot']
    touch_price = touch['TouchPrice']

    best = None
    for _, ray in rays_for_touch.iterrows():
        rp = ray['RayPrice']
        if is_demand:
            # Ray inside zone: between ZoneBot and TouchPrice
            # Closer to entry than opposite edge means rp > ZoneBot
            if zone_bot < rp < touch_price:
                if best is None or rp > best:
                    best = rp  # highest = closest to entry = tightest stop
        else:
            # Ray inside zone: between TouchPrice and ZoneTop
            if touch_price < rp < zone_top:
                if best is None or rp < best:
                    best = rp  # lowest = closest to entry = tightest stop
    return best


def simulate_touch(touch, tick_prices, tick_datetimes, tick_start,
                   ray_stop_price):
    """
    Simulate one touch across all sweep combinations.
    Returns list of result dicts.
    """
    results = []
    is_demand = touch['TouchType'] == 'DEMAND_EDGE'
    direction = 1 if is_demand else -1

    zone_top = touch['ZoneTop']
    zone_bot = touch['ZoneBot']
    touch_price = touch['TouchPrice']
    touch_time = touch['DateTime']

    # Opposite edge (base stop before buffer)
    opposite_edge = zone_bot if is_demand else zone_top

    # Convert touch_time to numpy datetime64 for array math
    touch_time_np = np.datetime64(touch_time)

    # Max forward window
    max_seconds = max(TIME_CAPS_MIN) * 60
    max_time = touch_time_np + np.timedelta64(max_seconds, 's')

    # Find end of forward window
    end_idx = np.searchsorted(tick_datetimes, max_time, side='right')
    end_idx = min(end_idx, len(tick_prices))

    if end_idx <= tick_start + 1:
        return results  # not enough tick data

    # Forward slices (numpy arrays for speed)
    fwd_prices = tick_prices[tick_start + 1:end_idx]  # start AFTER touch tick
    fwd_times = tick_datetimes[tick_start + 1:end_idx]

    if len(fwd_prices) == 0:
        return results

    # Time deltas in seconds from touch
    fwd_delta_sec = (fwd_times - touch_time_np) / np.timedelta64(1, 's')

    # Precompute truncation index per time cap
    cap_end = {}
    for cap in TIME_CAPS_MIN:
        cap_sec = cap * 60
        n = np.searchsorted(fwd_delta_sec, cap_sec, side='right')
        cap_end[cap] = min(n, len(fwd_prices))

    # Cost proxy: tick-to-tick price change at entry
    if tick_start > 0:
        cost_proxy_ticks = abs(tick_prices[tick_start] - tick_prices[tick_start - 1]) / TICK_SIZE
    else:
        cost_proxy_ticks = np.nan

    # Stop configurations: always zone_edge, optionally ray
    stop_configs = [('zone_edge', opposite_edge)]
    if ray_stop_price is not None:
        stop_configs.append(('ray', ray_stop_price))

    for time_cap in TIME_CAPS_MIN:
        n = cap_end[time_cap]
        if n == 0:
            continue

        prices = fwd_prices[:n]
        delta_sec = fwd_delta_sec[:n]

        for entry_offset in ENTRY_OFFSETS:
            # Entry deeper into zone
            if is_demand:
                entry_price = touch_price - entry_offset * TICK_SIZE
                if entry_price <= zone_bot:
                    continue  # offset pushes past zone
            else:
                entry_price = touch_price + entry_offset * TICK_SIZE
                if entry_price >= zone_top:
                    continue

            # Excursion from entry (positive = favorable)
            excursion = (prices - entry_price) * direction

            for stop_type, stop_base in stop_configs:
                for stop_buffer in STOP_BUFFERS:
                    # Apply buffer
                    if is_demand:
                        stop_price = stop_base - stop_buffer * TICK_SIZE
                    else:
                        stop_price = stop_base + stop_buffer * TICK_SIZE

                    risk_ticks = abs(entry_price - stop_price) / TICK_SIZE
                    if risk_ticks <= 0:
                        continue

                    # Stop hit detection
                    if is_demand:
                        stop_mask = prices <= stop_price
                    else:
                        stop_mask = prices >= stop_price

                    if stop_mask.any():
                        stop_idx = np.argmax(stop_mask)
                        exit_reason = 'stop'
                        resolution_sec = delta_sec[stop_idx]
                        pnl_ticks = -risk_ticks

                        # MFE/MAE up to stop point
                        seg = excursion[:stop_idx + 1]
                        mfe_ticks = max(seg.max(), 0) / TICK_SIZE
                        mae_ticks = max((-seg).max(), 0) / TICK_SIZE
                        mfe_idx_local = np.argmax(seg)
                        time_to_mfe_sec = delta_sec[mfe_idx_local]

                        # Zone break velocity (10s after stop)
                        break_end_sec = resolution_sec + 10
                        after_mask = ((fwd_delta_sec > resolution_sec)
                                      & (fwd_delta_sec <= break_end_sec))
                        if after_mask.any():
                            after_prices = fwd_prices[after_mask]
                            if is_demand:
                                move = (stop_price - after_prices.min()) / TICK_SIZE
                            else:
                                move = (after_prices.max() - stop_price) / TICK_SIZE
                            break_vel = max(move, 0) / 10.0
                        else:
                            break_vel = np.nan
                    else:
                        exit_reason = 'time_cap'
                        pnl_ticks = excursion[-1] / TICK_SIZE
                        mfe_ticks = max(excursion.max(), 0) / TICK_SIZE
                        mae_ticks = max((-excursion).max(), 0) / TICK_SIZE
                        mfe_idx_local = np.argmax(excursion)
                        time_to_mfe_sec = delta_sec[mfe_idx_local]
                        resolution_sec = delta_sec[-1]
                        break_vel = np.nan

                    # R:R at MFE
                    rr_at_mfe = mfe_ticks / risk_ticks

                    # Win flags at R multiples
                    win_1r = mfe_ticks >= 1.0 * risk_ticks
                    win_1_5r = mfe_ticks >= 1.5 * risk_ticks
                    win_2r = mfe_ticks >= 2.0 * risk_ticks
                    win_2_5r = mfe_ticks >= 2.5 * risk_ticks
                    win_3r = mfe_ticks >= 3.0 * risk_ticks

                    # Comeback analysis
                    comebacks = {}
                    # Use full excursion up to resolution for comeback
                    if exit_reason == 'stop':
                        check_exc = excursion[:stop_idx + 1]
                    else:
                        check_exc = excursion

                    for thresh_pts in COMEBACK_THRESHOLDS_PTS:
                        peak_mask = check_exc >= thresh_pts
                        if peak_mask.any():
                            first_peak = np.argmax(peak_mask)
                            after_peak = check_exc[first_peak:]
                            comeback = (after_peak <= COMEBACK_RETURN_TICKS * TICK_SIZE).any()
                        else:
                            comeback = False
                        comebacks[thresh_pts] = comeback

                    # Bounce shape
                    bounce_shape = (time_to_mfe_sec / resolution_sec
                                    if resolution_sec > 0 else np.nan)

                    results.append({
                        'TouchIdx': touch.name,
                        'DateTime': touch['DateTime'],
                        'TouchType': touch['TouchType'],
                        'SourceLabel': touch['SourceLabel'],
                        'ZoneTop': zone_top,
                        'ZoneBot': zone_bot,
                        'ZoneWidthTicks': touch['ZoneWidthTicks'],
                        'CascadeState': touch['CascadeState'],
                        'ZoneAgeBars': touch['ZoneAgeBars'],
                        'ZTE_Seq': touch['TouchSequence'],
                        'EntryOffset': entry_offset,
                        'StopBuffer': stop_buffer,
                        'TimeCapMin': time_cap,
                        'StopType': stop_type,
                        'EntryPrice': entry_price,
                        'StopPrice': stop_price,
                        'Direction': direction,
                        'RiskTicks': risk_ticks,
                        'MFE_Ticks': mfe_ticks,
                        'MAE_Ticks': mae_ticks,
                        'TimeToMFE_Sec': time_to_mfe_sec,
                        'TimeToResolution_Sec': resolution_sec,
                        'ExitReason': exit_reason,
                        'PnL_Ticks': pnl_ticks,
                        'RR_at_MFE': rr_at_mfe,
                        'Win_1R': win_1r,
                        'Win_1_5R': win_1_5r,
                        'Win_2R': win_2r,
                        'Win_2_5R': win_2_5r,
                        'Win_3R': win_3r,
                        'Comeback_10pt': comebacks[10],
                        'Comeback_20pt': comebacks[20],
                        'Comeback_30pt': comebacks[30],
                        'ZoneBreakVelocity': break_vel,
                        'BounceShape': bounce_shape,
                        'CostProxyTicks': cost_proxy_ticks,
                    })

    return results


def run_simulation(zte, ticks, rays):
    """Run Part B simulation across all touches and sweep combos."""
    print("\n[B] Running tick-level baseline simulation...")
    t0 = _time.time()

    # Convert tick data to numpy for speed
    tick_datetimes = ticks['DateTime'].values  # numpy datetime64
    tick_prices = ticks['Last'].values         # numpy float64

    # Pre-group rays by TouchID
    ray_groups = rays.groupby('TouchID')

    # Track tick match rate
    matched = 0
    unmatched = 0

    all_results = []
    n_touches = len(zte)

    for i, (idx, touch) in enumerate(zte.iterrows()):
        if (i + 1) % 500 == 0 or i == 0:
            elapsed = _time.time() - t0
            print(f"  touch {i+1}/{n_touches} "
                  f"({elapsed:.0f}s elapsed, "
                  f"{len(all_results):,} results so far)")

        # Find tick index
        touch_dt = touch['DateTime']
        tick_idx = find_tick_index(touch_dt, tick_datetimes)
        if tick_idx < 0 or tick_idx >= len(tick_prices):
            unmatched += 1
            continue

        # Check timestamp gap
        tick_dt = pd.Timestamp(tick_datetimes[tick_idx])
        gap_sec = abs((tick_dt - touch_dt).total_seconds())
        if gap_sec > 1.0:
            unmatched += 1
            continue
        matched += 1

        # Get rays for this touch
        tid = touch['TouchID']
        if tid in ray_groups.groups:
            rays_for_touch = ray_groups.get_group(tid)
        else:
            rays_for_touch = None

        ray_stop = find_best_ray_stop(touch, rays_for_touch)

        # Simulate
        touch_results = simulate_touch(
            touch, tick_prices, tick_datetimes, tick_idx, ray_stop
        )
        all_results.extend(touch_results)

    elapsed = _time.time() - t0
    print(f"  done: {len(all_results):,} results in {elapsed:.1f}s")
    print(f"  tick match: {matched}/{matched+unmatched} "
          f"({matched/(matched+unmatched):.1%})")

    if len(all_results) == 0:
        print("  WARNING: no simulation results produced")
        return pd.DataFrame()

    df = pd.DataFrame(all_results)
    df['DateTime'] = pd.to_datetime(df['DateTime'])
    return df


# ═══════════════════════════════════════════════════════════════
# POST-PROCESSING
# ═══════════════════════════════════════════════════════════════

def compute_resolved_sequence(sim_df):
    """
    Compute resolved_seq: recount touch sequence based on whether
    prior touch fully resolved before next touch on same zone.

    Uses reference sweep combo (REF_OFFSET, REF_BUFFER, REF_CAP).
    Resolution = stop hit, time_cap hit, or MFE >= 10pts without comeback.
    """
    print("\n[post] Computing resolved sequence...")

    # Filter to reference sweep
    ref = sim_df[
        (sim_df['EntryOffset'] == REF_OFFSET)
        & (sim_df['StopBuffer'] == REF_BUFFER)
        & (sim_df['TimeCapMin'] == REF_CAP)
        & (sim_df['StopType'] == 'zone_edge')
    ].copy()

    if len(ref) == 0:
        print("  WARNING: no reference sweep results found")
        sim_df['Resolved_Seq'] = sim_df['ZTE_Seq']
        return sim_df

    # Determine resolution for each touch
    ref['Resolved'] = (
        (ref['ExitReason'] == 'stop')
        | (ref['ExitReason'] == 'time_cap')
        | ((ref['MFE_Ticks'] >= 10 / TICK_SIZE) & (~ref['Comeback_10pt']))
    )

    # Zone identity: (ZoneTop, ZoneBot, TouchType)
    ref = ref.sort_values('DateTime')
    ref['ZoneKey'] = (ref['ZoneTop'].astype(str) + '_'
                      + ref['ZoneBot'].astype(str) + '_'
                      + ref['TouchType'])

    # Assign resolved_seq per zone
    resolved_seqs = {}
    for zone_key, group in ref.groupby('ZoneKey'):
        group = group.sort_values('DateTime')
        seq = 1
        for i, (idx, row) in enumerate(group.iterrows()):
            resolved_seqs[idx] = seq
            if not row['Resolved']:
                pass  # next touch same seq
            else:
                seq += 1

    ref['Resolved_Seq'] = ref.index.map(resolved_seqs)

    # Map resolved_seq back to all sweep combos via TouchIdx
    touch_resolved = ref[['TouchIdx', 'Resolved_Seq']].drop_duplicates('TouchIdx')
    sim_df = sim_df.merge(touch_resolved, on='TouchIdx', how='left',
                          suffixes=('', '_new'))
    if 'Resolved_Seq_new' in sim_df.columns:
        sim_df['Resolved_Seq'] = sim_df['Resolved_Seq_new']
        sim_df = sim_df.drop(columns=['Resolved_Seq_new'])
    sim_df['Resolved_Seq'] = sim_df['Resolved_Seq'].fillna(sim_df['ZTE_Seq'])

    print(f"  resolved_seq computed for {len(touch_resolved)} touches")
    return sim_df


def compute_concurrent_exposure(sim_df):
    """
    Count how many other trades are open at each touch timestamp.
    Uses reference sweep combo.
    """
    print("[post] Computing concurrent exposure...")

    ref = sim_df[
        (sim_df['EntryOffset'] == REF_OFFSET)
        & (sim_df['StopBuffer'] == REF_BUFFER)
        & (sim_df['TimeCapMin'] == REF_CAP)
        & (sim_df['StopType'] == 'zone_edge')
    ].copy()

    if len(ref) == 0:
        sim_df['ConcurrentOpen'] = 0
        return sim_df

    # Each trade: open from DateTime, duration = TimeToResolution_Sec
    ref['TradeEnd'] = ref['DateTime'] + pd.to_timedelta(ref['TimeToResolution_Sec'], unit='s')

    concurrent = []
    for _, row in ref.iterrows():
        dt = row['DateTime']
        # Count trades open at this timestamp (opened before, not yet closed)
        open_count = ((ref['DateTime'] <= dt) & (ref['TradeEnd'] > dt)).sum()
        concurrent.append(open_count - 1)  # exclude self

    ref['ConcurrentOpen'] = concurrent

    # Map back
    touch_conc = ref[['TouchIdx', 'ConcurrentOpen']].drop_duplicates('TouchIdx')
    sim_df = sim_df.merge(touch_conc, on='TouchIdx', how='left',
                          suffixes=('', '_new'))
    if 'ConcurrentOpen_new' in sim_df.columns:
        sim_df['ConcurrentOpen'] = sim_df['ConcurrentOpen_new']
        sim_df = sim_df.drop(columns=['ConcurrentOpen_new'])
    sim_df['ConcurrentOpen'] = sim_df['ConcurrentOpen'].fillna(0).astype(int)

    print(f"  median concurrent: {ref['ConcurrentOpen'].median():.0f}, "
          f"max: {ref['ConcurrentOpen'].max()}")
    return sim_df


# ═══════════════════════════════════════════════════════════════
# PART C: SUMMARY STATISTICS
# ═══════════════════════════════════════════════════════════════

def part_c1_mfe_distribution(sim_df):
    """C1: MFE distribution — the core deliverable."""
    print("\n[C1] MFE distribution")

    # Use zone_edge stop only for main summary
    df = sim_df[sim_df['StopType'] == 'zone_edge'].copy()

    def summarize(subset, label):
        if len(subset) == 0:
            return {}
        return {
            'label': label,
            'n': len(subset),
            'mfe_median': subset['MFE_Ticks'].median(),
            'mfe_p75': subset['MFE_Ticks'].quantile(0.75),
            'mfe_p90': subset['MFE_Ticks'].quantile(0.90),
            'risk_mean': subset['RiskTicks'].mean(),
            'rr_median': subset['RR_at_MFE'].median(),
            'win_1r': subset['Win_1R'].mean(),
            'win_1_5r': subset['Win_1_5R'].mean(),
            'win_2r': subset['Win_2R'].mean(),
            'win_3r': subset['Win_3R'].mean(),
            'pf_1r': compute_pf(subset, 1.0),
            'pf_1_5r': compute_pf(subset, 1.5),
            'pf_2r': compute_pf(subset, 2.0),
        }

    rows = []
    # Overall
    rows.append(summarize(df, 'ALL'))
    # By touch type
    rows.append(summarize(df[df['TouchType'] == 'DEMAND_EDGE'], 'DEMAND'))
    rows.append(summarize(df[df['TouchType'] == 'SUPPLY_EDGE'], 'SUPPLY'))
    # By session
    rows.append(summarize(df[df['Session'] == 'RTH'], 'RTH'))
    rows.append(summarize(df[df['Session'] == 'ETH'], 'ETH'))

    # Print summary
    for r in rows:
        if not r:
            continue
        print(f"  {r['label']:>8s}: n={r['n']:>6,}  "
              f"median MFE={r['mfe_median']:.0f}t  "
              f"median R:R={r['rr_median']:.2f}  "
              f"win@1R={r['win_1r']:.1%}  "
              f"PF@1R={r['pf_1r']:.2f}  "
              f"win@2R={r['win_2r']:.1%}")

    # Grouped by sweep params
    grouped = df.groupby(['EntryOffset', 'StopBuffer', 'TimeCapMin']).agg(
        n=('MFE_Ticks', 'count'),
        mfe_median=('MFE_Ticks', 'median'),
        risk_mean=('RiskTicks', 'mean'),
        rr_median=('RR_at_MFE', 'median'),
        win_1r=('Win_1R', 'mean'),
        win_2r=('Win_2R', 'mean'),
        win_3r=('Win_3R', 'mean'),
        break_rate=('ExitReason', lambda x: (x == 'stop').mean()),
    ).reset_index()

    # Find best combo by median R:R
    best = grouped.loc[grouped['rr_median'].idxmax()]
    print(f"\n  best sweep (by median R:R): "
          f"offset={best['EntryOffset']:.0f} "
          f"buffer={best['StopBuffer']:.0f} "
          f"cap={best['TimeCapMin']:.0f}min -> "
          f"R:R={best['rr_median']:.2f}")

    # Break-even R: where win_rate * R = (1 - win_rate) * 1
    # Solve: win_rate(R) * R = (1 - win_rate(R)) * 1
    # Approximate from discrete R values
    for r_mult in [1.0, 1.5, 2.0, 2.5, 3.0]:
        col = f'Win_{str(r_mult).replace(".", "_")}R'
        if col not in df.columns:
            continue
        wr = df[col].mean()
        ev = wr * r_mult - (1 - wr) * 1.0
        if ev >= 0:
            print(f"  break-even check: {r_mult}R target, "
                  f"win={wr:.1%}, EV={ev:+.2f}R [PASS]")
            break

    return {'overall': rows, 'by_sweep': grouped}


def part_c2_risk_profile(sim_df):
    """C2: Risk profile."""
    print("\n[C2] Risk profile")
    df = sim_df[sim_df['StopType'] == 'zone_edge'].copy()

    break_rate = (df['ExitReason'] == 'stop').mean()
    mae_median = df['MAE_Ticks'].median()
    mae_p95 = df['MAE_Ticks'].quantile(0.95)
    comeback_20 = df['Comeback_20pt'].mean()
    break_vel = df.loc[df['ExitReason'] == 'stop', 'ZoneBreakVelocity']
    break_vel_median = break_vel.median() if len(break_vel) > 0 else np.nan

    results = {
        'break_rate': break_rate,
        'mae_median': mae_median,
        'mae_p95': mae_p95,
        'comeback_20pt_rate': comeback_20,
        'break_velocity_median': break_vel_median,
    }

    print(f"  break rate: {break_rate:.1%}")
    print(f"  MAE median: {mae_median:.0f}t, P95: {mae_p95:.0f}t")
    print(f"  comeback rate (after ≥20pt MFE): {comeback_20:.1%}")
    print(f"  zone break velocity: {break_vel_median:.1f} ticks/sec")
    return results


def part_c3_time_profile(sim_df):
    """C3: Time profile."""
    print("\n[C3] Time profile")
    df = sim_df[sim_df['StopType'] == 'zone_edge'].copy()

    mfe_sec = df['TimeToMFE_Sec']
    res_sec = df['TimeToResolution_Sec']

    results = {
        'time_to_mfe_median_min': mfe_sec.median() / 60,
        'time_to_resolution_median_min': res_sec.median() / 60,
        'pct_resolved_30min': (res_sec <= 1800).mean(),
        'pct_resolved_1hr': (res_sec <= 3600).mean(),
        'pct_resolved_4hr': (res_sec <= 14400).mean(),
    }

    print(f"  median time-to-MFE: {results['time_to_mfe_median_min']:.1f}min")
    print(f"  median time-to-resolution: {results['time_to_resolution_median_min']:.1f}min")
    print(f"  resolved within 30min: {results['pct_resolved_30min']:.1%}")
    print(f"  resolved within 1hr: {results['pct_resolved_1hr']:.1%}")
    print(f"  resolved within 4hr: {results['pct_resolved_4hr']:.1%}")
    return results


def part_c4_trade_series(sim_df):
    """C4: Trade series metrics — Sharpe, Sortino, Calmar, drawdown, streaks."""
    print("\n[C4] Trade series metrics")

    df = sim_df[sim_df['StopType'] == 'zone_edge'].copy()

    # Compute PF per sweep combo to find best
    grouped = df.groupby(['EntryOffset', 'StopBuffer', 'TimeCapMin'])
    pf_by_sweep = grouped.apply(lambda g: compute_pf(g, 1.0), include_groups=False)
    best_key = pf_by_sweep.idxmax()

    def series_metrics(subset, label):
        """Compute trade-level series metrics for a subset."""
        if len(subset) == 0:
            return {}

        # PnL per trade at 1R target
        risk = subset['RiskTicks'].values
        target = risk * 1.0
        mfe = subset['MFE_Ticks'].values
        pnl = np.where(mfe >= target, target, -risk)  # win = +target, loss = -risk
        pnl_cumsum = np.cumsum(pnl)

        # Sharpe (trade-level, not annualized)
        sharpe = pnl.mean() / pnl.std() if pnl.std() > 0 else np.inf

        # Sortino (downside deviation only)
        downside = pnl[pnl < 0]
        downside_std = downside.std() if len(downside) > 0 else 0
        sortino = pnl.mean() / downside_std if downside_std > 0 else np.inf

        # Max drawdown
        running_max = np.maximum.accumulate(pnl_cumsum)
        drawdown = running_max - pnl_cumsum
        max_dd = drawdown.max()

        # Calmar (total return / max drawdown)
        total_return = pnl_cumsum[-1]
        calmar = total_return / max_dd if max_dd > 0 else np.inf

        # Consecutive wins/losses
        wins = pnl > 0
        max_consec_wins = 0
        max_consec_losses = 0
        current = 0
        for w in wins:
            if w:
                current += 1
                max_consec_wins = max(max_consec_wins, current)
            else:
                current = 0
        current = 0
        for w in wins:
            if not w:
                current += 1
                max_consec_losses = max(max_consec_losses, current)
            else:
                current = 0

        # Equity curve slope (linear regression)
        x = np.arange(len(pnl_cumsum))
        if len(x) > 1:
            slope = np.polyfit(x, pnl_cumsum, 1)[0]
        else:
            slope = 0

        return {
            'label': label,
            'n': len(subset),
            'pf_1r': compute_pf(subset, 1.0),
            'sharpe': sharpe,
            'sortino': sortino,
            'calmar': calmar,
            'max_dd_ticks': max_dd,
            'max_consec_wins': max_consec_wins,
            'max_consec_losses': max_consec_losses,
            'eq_slope': slope,
            'total_return': total_return,
        }

    # Best sweep combo
    best_df = df[
        (df['EntryOffset'] == best_key[0])
        & (df['StopBuffer'] == best_key[1])
        & (df['TimeCapMin'] == best_key[2])
    ]
    best_metrics = series_metrics(best_df, f"best(o={best_key[0]},b={best_key[1]},c={best_key[2]})")

    print(f"  best sweep (by PF@1R): offset={best_key[0]}, buffer={best_key[1]}, cap={best_key[2]}min")
    if best_metrics:
        print(f"    PF@1R={best_metrics['pf_1r']:.2f}  "
              f"Sharpe={best_metrics['sharpe']:.3f}  "
              f"Sortino={best_metrics['sortino']:.3f}  "
              f"Calmar={best_metrics['calmar']:.3f}")
        print(f"    MaxDD={best_metrics['max_dd_ticks']:.0f}t  "
              f"ConsecW={best_metrics['max_consec_wins']}  "
              f"ConsecL={best_metrics['max_consec_losses']}  "
              f"EqSlope={best_metrics['eq_slope']:.2f}")

    # All combos with PF > 1.0
    profitable_keys = pf_by_sweep[pf_by_sweep > 1.0].index.tolist()
    all_profitable = []
    for key in profitable_keys:
        sub = df[(df['EntryOffset'] == key[0]) & (df['StopBuffer'] == key[1]) & (df['TimeCapMin'] == key[2])]
        m = series_metrics(sub, f"o={key[0]},b={key[1]},c={key[2]}")
        if m:
            all_profitable.append(m)

    print(f"\n  profitable combos (PF>1.0 at 1R): {len(all_profitable)}/{len(pf_by_sweep)}")

    return {'best': best_metrics, 'best_key': best_key, 'all_profitable': all_profitable}


def part_c5_structural(sim_df):
    """C5: Structural observations."""
    print("\n[C5] Structural observations")
    df = sim_df[sim_df['StopType'] == 'zone_edge'].copy()

    # Zone width breakpoint: R:R by width decile
    df['WidthDecile'] = pd.qcut(df['ZoneWidthTicks'], 10, labels=False,
                                 duplicates='drop')
    width_rr = df.groupby('WidthDecile').agg(
        width_median=('ZoneWidthTicks', 'median'),
        rr_median=('RR_at_MFE', 'median'),
        n=('RR_at_MFE', 'count'),
    ).reset_index()
    print("  zone width vs R:R:")
    for _, row in width_rr.iterrows():
        print(f"    width≈{row['width_median']:.0f}t: "
              f"R:R={row['rr_median']:.2f} (n={row['n']:.0f})")

    # Ray impact: compare zone_edge vs ray stop
    ray_results = sim_df[sim_df['StopType'] == 'ray']
    edge_results = sim_df[sim_df['StopType'] == 'zone_edge']
    if len(ray_results) > 0:
        # Match on same touch + sweep params
        ray_rr = ray_results['RR_at_MFE'].median()
        edge_rr = edge_results['RR_at_MFE'].median()
        print(f"\n  ray impact: ray stop R:R={ray_rr:.2f} "
              f"vs zone edge R:R={edge_rr:.2f}")
    else:
        print("\n  ray impact: no ray stop results available")

    # Concurrent exposure
    if 'ConcurrentOpen' in df.columns:
        conc = df.drop_duplicates('TouchIdx')['ConcurrentOpen']
        print(f"\n  concurrent exposure: "
              f"median={conc.median():.0f}, "
              f"P90={conc.quantile(0.90):.0f}, "
              f"max={conc.max()}")

    # Cost proxy vs fixed assumption
    cost = df['CostProxyTicks'].dropna()
    if len(cost) > 0:
        print(f"\n  tick-based cost proxy: "
              f"median={cost.median():.1f}t "
              f"vs fixed assumption={EXPERIMENT_COST_TICKS}t")

    return {'width_rr': width_rr}


def part_c6_sequence_comparison(sim_df):
    """C6: ZTE seq vs resolved seq comparison with PF."""
    print("\n[C6] Sequence comparison")

    # Use reference sweep, zone_edge only
    ref = sim_df[
        (sim_df['EntryOffset'] == REF_OFFSET)
        & (sim_df['StopBuffer'] == REF_BUFFER)
        & (sim_df['TimeCapMin'] == REF_CAP)
        & (sim_df['StopType'] == 'zone_edge')
    ].copy()

    if 'Resolved_Seq' not in ref.columns:
        print("  resolved_seq not computed")
        return {}

    results = {}
    for seq_col, label in [('ZTE_Seq', 'ZTE'), ('Resolved_Seq', 'Resolved')]:
        n = len(ref)
        seq1_mask = ref[seq_col] == 1
        seq2_mask = ref[seq_col] >= 2
        seq1 = seq1_mask.sum()
        seq2plus = seq2_mask.sum()
        rr_seq1 = ref.loc[seq1_mask, 'RR_at_MFE'].median()
        rr_seq2 = ref.loc[seq2_mask, 'RR_at_MFE'].median()
        pf_seq1 = compute_pf(ref[seq1_mask], 1.0) if seq1 > 0 else np.nan
        pf_seq2 = compute_pf(ref[seq2_mask], 1.0) if seq2plus > 0 else np.nan
        results[label] = {
            'total': n, 'seq1': seq1, 'seq2plus': seq2plus,
            'rr_seq1': rr_seq1, 'rr_seq2plus': rr_seq2,
            'pf_seq1': pf_seq1, 'pf_seq2plus': pf_seq2,
        }
        print(f"  {label:>8s}: total={n}, seq1={seq1}, seq2+={seq2plus}, "
              f"seq1 R:R={rr_seq1:.2f} PF={pf_seq1:.2f}, "
              f"seq2+ R:R={rr_seq2:.2f} PF={pf_seq2:.2f}")

    return results


def part_c7_session_comparison(sim_df):
    """C7: RTH vs ETH session comparison."""
    print("\n[C7] Session comparison (RTH vs ETH)")

    df = sim_df[sim_df['StopType'] == 'zone_edge'].copy()

    results = {}
    for session in ['RTH', 'ETH']:
        sub = df[df['Session'] == session]
        if len(sub) == 0:
            continue

        risk = sub['RiskTicks'].values
        target_1r = risk * 1.0
        mfe = sub['MFE_Ticks'].values
        pnl = np.where(mfe >= target_1r, target_1r, -risk)
        pnl_cumsum = np.cumsum(pnl)
        running_max = np.maximum.accumulate(pnl_cumsum)
        max_dd = (running_max - pnl_cumsum).max()
        sharpe = pnl.mean() / pnl.std() if pnl.std() > 0 else np.inf

        results[session] = {
            'n': len(sub),
            'pct': len(sub) / len(df),
            'mfe_median': sub['MFE_Ticks'].median(),
            'risk_median': sub['RiskTicks'].median(),
            'rr_median': sub['RR_at_MFE'].median(),
            'pf_1r': compute_pf(sub, 1.0),
            'pf_2r': compute_pf(sub, 2.0),
            'break_rate': (sub['ExitReason'] == 'stop').mean(),
            'time_to_res_median_min': sub['TimeToResolution_Sec'].median() / 60,
            'comeback_20pt': sub['Comeback_20pt'].mean(),
            'sharpe': sharpe,
            'max_dd_ticks': max_dd,
        }

        r = results[session]
        print(f"  {session:>3s}: n={r['n']:>6,} ({r['pct']:.1%})  "
              f"R:R={r['rr_median']:.2f}  "
              f"PF@1R={r['pf_1r']:.2f}  "
              f"PF@2R={r['pf_2r']:.2f}  "
              f"Sharpe={r['sharpe']:.3f}  "
              f"MaxDD={r['max_dd_ticks']:.0f}t")

    return results


# ═══════════════════════════════════════════════════════════════
# OUTPUT GENERATION
# ═══════════════════════════════════════════════════════════════

def save_raw_results(sim_df):
    """Save raw trade results CSV."""
    path = OUTPUT / 'zone-touch-NQ-baseline-raw.csv'
    sim_df.to_csv(path, index=False)
    print(f"\n[save] raw results: {path} ({len(sim_df):,} rows)")


def save_summary_report(a1, a2, a3, c1, c2, c3, c4, c5, c6, c7):
    """Save summary statistics as markdown."""
    path = OUTPUT / 'zone-touch-NQ-baseline-summary.md'

    lines = []
    lines.append("# Zone Touch NQ — Prompt 0 Baseline Summary\n")
    lines.append(f"Generated: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}\n")
    lines.append(f"Calibration: {CAL_START.date()} to {CAL_END.date()}")
    lines.append(f"Holdout: {HO_START.date()} to {HO_END.date()}\n")

    # A1: Zone Width
    lines.append("\n## A1: Zone Width Distribution\n")
    lines.append("| TF | Count | Median (t) | Median (pts) | P90 (t) | Max (t) |")
    lines.append("|---|---|---|---|---|---|")
    for tf in TF_ORDER + ['ALL']:
        if tf in a1:
            r = a1[tf]
            lines.append(f"| {tf} | {r['count']} | {r['median']:.0f} | "
                         f"{r['width_pts_median']:.1f} | {r['p90']:.0f} | {r['max']:.0f} |")

    # A2: Ray Availability
    lines.append(f"\n## A2: Ray Availability\n")
    lines.append(f"- Rate: {a2['availability_rate']:.1%} "
                 f"({a2['touches_with_ray']}/{a2['touches_total']})")
    lines.append(f"- Ray position median: {a2['ray_position_median']:.2f} "
                 f"(0=touch edge, 1=opposite)")

    # A3: Nested Zones
    lines.append(f"\n## A3: Nested Zone Frequency\n")
    lines.append(f"- Overall rate (TF>=60m): {a3['overall_rate']:.1%}")

    # C1: MFE Distribution
    lines.append("\n## C1: MFE Distribution\n")
    lines.append("| Group | N | Median MFE (t) | Median R:R | Win@1R | Win@1.5R | Win@2R | Win@3R | PF@1R | PF@1.5R | PF@2R |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in c1['overall']:
        if not r:
            continue
        lines.append(f"| {r['label']} | {r['n']:,} | {r['mfe_median']:.0f} | "
                     f"{r['rr_median']:.2f} | {r['win_1r']:.1%} | "
                     f"{r['win_1_5r']:.1%} | "
                     f"{r['win_2r']:.1%} | {r['win_3r']:.1%} | "
                     f"{r['pf_1r']:.2f} | {r['pf_1_5r']:.2f} | {r['pf_2r']:.2f} |")

    # C1: Best sweeps
    lines.append("\n### Best Sweep Combos (by median R:R)\n")
    top = c1['by_sweep'].nlargest(10, 'rr_median')
    lines.append("| Offset | Buffer | Cap | N | R:R | Win@1R | Break% |")
    lines.append("|---|---|---|---|---|---|---|")
    for _, row in top.iterrows():
        lines.append(f"| {row['EntryOffset']:.0f} | {row['StopBuffer']:.0f} | "
                     f"{row['TimeCapMin']:.0f} | {row['n']:.0f} | "
                     f"{row['rr_median']:.2f} | {row['win_1r']:.1%} | "
                     f"{row['break_rate']:.1%} |")

    # C2: Risk Profile
    lines.append(f"\n## C2: Risk Profile\n")
    lines.append(f"| Metric | Value |")
    lines.append(f"|---|---|")
    lines.append(f"| Break rate | {c2['break_rate']:.1%} |")
    lines.append(f"| MAE median | {c2['mae_median']:.0f}t |")
    lines.append(f"| MAE P95 | {c2['mae_p95']:.0f}t |")
    lines.append(f"| Comeback rate (>=20pt MFE) | {c2['comeback_20pt_rate']:.1%} |")
    lines.append(f"| Zone break velocity | {c2['break_velocity_median']:.1f} t/s |")

    # C3: Time Profile
    lines.append(f"\n## C3: Time Profile\n")
    lines.append(f"| Metric | Value |")
    lines.append(f"|---|---|")
    lines.append(f"| Median time-to-MFE | {c3['time_to_mfe_median_min']:.1f}min |")
    lines.append(f"| Median time-to-resolution | {c3['time_to_resolution_median_min']:.1f}min |")
    lines.append(f"| Resolved within 30min | {c3['pct_resolved_30min']:.1%} |")
    lines.append(f"| Resolved within 1hr | {c3['pct_resolved_1hr']:.1%} |")
    lines.append(f"| Resolved within 4hr | {c3['pct_resolved_4hr']:.1%} |")

    # C4: Trade Series Metrics
    if c4 and c4.get('best'):
        b = c4['best']
        lines.append(f"\n## C4: Trade Series Metrics\n")
        lines.append(f"Best sweep (by PF@1R): {b['label']}\n")
        lines.append(f"| Metric | Value |")
        lines.append(f"|---|---|")
        lines.append(f"| PF at 1R | {b['pf_1r']:.2f} |")
        lines.append(f"| Sharpe (trade-level) | {b['sharpe']:.3f} |")
        lines.append(f"| Sortino | {b['sortino']:.3f} |")
        lines.append(f"| Calmar | {b['calmar']:.3f} |")
        lines.append(f"| Max drawdown | {b['max_dd_ticks']:.0f}t |")
        lines.append(f"| Max consecutive wins | {b['max_consec_wins']} |")
        lines.append(f"| Max consecutive losses | {b['max_consec_losses']} |")
        lines.append(f"| Equity curve slope | {b['eq_slope']:.2f} |")
        lines.append(f"\nProfitable combos (PF>1.0 at 1R): {len(c4['all_profitable'])}")

        if c4['all_profitable']:
            lines.append("\n### All Profitable Sweep Combos\n")
            lines.append("| Combo | PF@1R | Sharpe | Sortino | MaxDD(t) |")
            lines.append("|---|---|---|---|---|")
            for m in sorted(c4['all_profitable'], key=lambda x: x['pf_1r'], reverse=True)[:20]:
                lines.append(f"| {m['label']} | {m['pf_1r']:.2f} | "
                             f"{m['sharpe']:.3f} | {m['sortino']:.3f} | "
                             f"{m['max_dd_ticks']:.0f} |")

    # C5: Structural Observations
    lines.append(f"\n## C5: Structural Observations\n")
    lines.append("See console output for zone width breakpoint, ray impact, "
                 "concurrent exposure, and cost proxy analysis.")

    # C6: Sequence Comparison
    if c6:
        lines.append(f"\n## C6: Sequence Comparison\n")
        lines.append("| Metric | ZTE Seq | Resolved Seq |")
        lines.append("|---|---|---|")
        zte_s = c6.get('ZTE', {})
        res_s = c6.get('Resolved', {})
        lines.append(f"| Touch count | {zte_s.get('total', '')} | {res_s.get('total', '')} |")
        lines.append(f"| Seq 1 count | {zte_s.get('seq1', '')} | {res_s.get('seq1', '')} |")
        lines.append(f"| Seq 2+ count | {zte_s.get('seq2plus', '')} | {res_s.get('seq2plus', '')} |")
        lines.append(f"| Seq 1 R:R | {zte_s.get('rr_seq1', 0):.2f} | {res_s.get('rr_seq1', 0):.2f} |")
        lines.append(f"| Seq 2+ R:R | {zte_s.get('rr_seq2plus', 0):.2f} | {res_s.get('rr_seq2plus', 0):.2f} |")
        lines.append(f"| Seq 1 PF@1R | {zte_s.get('pf_seq1', 0):.2f} | {res_s.get('pf_seq1', 0):.2f} |")
        lines.append(f"| Seq 2+ PF@1R | {zte_s.get('pf_seq2plus', 0):.2f} | {res_s.get('pf_seq2plus', 0):.2f} |")

    # C7: Session Comparison
    if c7:
        lines.append(f"\n## C7: Session Comparison (RTH vs ETH)\n")
        lines.append("| Metric | RTH | ETH |")
        lines.append("|---|---|---|")
        rth = c7.get('RTH', {})
        eth = c7.get('ETH', {})
        lines.append(f"| Touch count | {rth.get('n', '')} | {eth.get('n', '')} |")
        lines.append(f"| % of total | {rth.get('pct', 0):.1%} | {eth.get('pct', 0):.1%} |")
        lines.append(f"| Median MFE (t) | {rth.get('mfe_median', 0):.0f} | {eth.get('mfe_median', 0):.0f} |")
        lines.append(f"| Median risk (t) | {rth.get('risk_median', 0):.0f} | {eth.get('risk_median', 0):.0f} |")
        lines.append(f"| Median R:R | {rth.get('rr_median', 0):.2f} | {eth.get('rr_median', 0):.2f} |")
        lines.append(f"| PF@1R | {rth.get('pf_1r', 0):.2f} | {eth.get('pf_1r', 0):.2f} |")
        lines.append(f"| PF@2R | {rth.get('pf_2r', 0):.2f} | {eth.get('pf_2r', 0):.2f} |")
        lines.append(f"| Break rate | {rth.get('break_rate', 0):.1%} | {eth.get('break_rate', 0):.1%} |")
        lines.append(f"| Median time-to-res | {rth.get('time_to_res_median_min', 0):.1f}min | {eth.get('time_to_res_median_min', 0):.1f}min |")
        lines.append(f"| Comeback rate (>=20pt) | {rth.get('comeback_20pt', 0):.1%} | {eth.get('comeback_20pt', 0):.1%} |")
        lines.append(f"| Sharpe | {rth.get('sharpe', 0):.3f} | {eth.get('sharpe', 0):.3f} |")
        lines.append(f"| Max drawdown (t) | {rth.get('max_dd_ticks', 0):.0f} | {eth.get('max_dd_ticks', 0):.0f} |")

    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    print(f"[save] summary: {path}")


def save_zone_geometry(a1, a2, a3):
    """Save zone geometry analysis."""
    path = OUTPUT / 'zone-touch-NQ-zone-geometry.md'
    lines = []
    lines.append("# Zone Touch NQ — Zone Geometry Analysis\n")
    lines.append(f"Generated: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}\n")

    lines.append("\n## Zone Width Distribution by Timeframe\n")
    lines.append("| TF | Count | Min | P25 | Median | P75 | P90 | P95 | Max |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for tf in TF_ORDER + ['ALL']:
        if tf in a1:
            r = a1[tf]
            lines.append(f"| {tf} | {r['count']} | {r['min']:.0f} | "
                         f"{r['p25']:.0f} | {r['median']:.0f} | {r['p75']:.0f} | "
                         f"{r['p90']:.0f} | {r['p95']:.0f} | {r['max']:.0f} |")

    lines.append(f"\n## Ray Availability (TF>=60m filter)\n")
    lines.append(f"- Availability rate: {a2['availability_rate']:.1%}")
    lines.append(f"- Touches with valid ray: {a2['touches_with_ray']}/{a2['touches_total']}")
    lines.append(f"- Rays per touch distribution: {a2['ray_count_dist']}")

    lines.append(f"\n## Nested Zone Frequency\n")
    lines.append(f"- Overall rate: {a3['overall_rate']:.1%}")
    if a3.get('by_tf'):
        lines.append("\n| Outer TF | Count | Nested | Rate |")
        lines.append("|---|---|---|---|")
        for tf, data in a3['by_tf'].items():
            lines.append(f"| {tf} | {data['count']} | {data['nested']} | "
                         f"{data['rate']:.1%} |")

    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    print(f"[save] zone geometry: {path}")


def save_ray_analysis(a2, rays, zte):
    """Save ray analysis detail."""
    path = OUTPUT / 'zone-touch-NQ-ray-analysis.md'
    lines = []
    lines.append("# Zone Touch NQ — Ray Availability Analysis\n")
    lines.append(f"Generated: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M')}\n")
    lines.append(f"Filter: TF >= 60m only (SBB filter not applied — "
                 f"ray origin zone not identifiable from ray context data)\n")
    lines.append(f"\n## Summary\n")
    lines.append(f"- Valid rays after filter: {len(rays):,}")
    lines.append(f"- Touches with valid ray: {a2['touches_with_ray']}/{a2['touches_total']} "
                 f"({a2['availability_rate']:.1%})")
    lines.append(f"- Ray position (0=touch edge, 1=opposite edge): "
                 f"P25={a2['ray_position_p25']:.2f}, "
                 f"median={a2['ray_position_median']:.2f}, "
                 f"P75={a2['ray_position_p75']:.2f}")

    # Ray count distribution
    lines.append(f"\n## Rays Per Touch\n")
    lines.append("| Ray Count | Touches |")
    lines.append("|---|---|")
    for k, v in sorted(a2['ray_count_dist'].items()):
        lines.append(f"| {k} | {v} |")

    # Rays by TF
    lines.append(f"\n## Rays by Timeframe\n")
    tf_counts = rays['RayTF'].value_counts().sort_index()
    lines.append("| Ray TF | Count |")
    lines.append("|---|---|")
    for tf, cnt in tf_counts.items():
        lines.append(f"| {tf} | {cnt:,} |")

    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines) + '\n')
    print(f"[save] ray analysis: {path}")


def append_journal(sim_df, c1, c2, c3):
    """Append findings to journal."""
    path = OUTPUT / 'zone-touch-NQ-journal.md'

    # Create if doesn't exist
    if not path.exists():
        header = "# Zone Touch NQ — Journal\n\n"
    else:
        header = ""

    entry = []
    entry.append(f"\n---\n")
    entry.append(f"## {pd.Timestamp.now().strftime('%Y-%m-%d')} — Prompt 0 Baseline\n")
    entry.append(f"Calibration: {CAL_START.date()} to {CAL_END.date()}\n")

    # Key findings
    overall = c1['overall'][0] if c1['overall'] else {}
    if overall:
        entry.append(f"\n### Key Numbers\n")
        entry.append(f"- Median R:R at MFE: {overall.get('rr_median', 0):.2f}")
        entry.append(f"- Win rate at 1R: {overall.get('win_1r', 0):.1%}")
        entry.append(f"- Win rate at 2R: {overall.get('win_2r', 0):.1%}")
        entry.append(f"- Break rate: {c2['break_rate']:.1%}")
        entry.append(f"- Comeback rate (≥20pt): {c2['comeback_20pt_rate']:.1%}")
        entry.append(f"- Median time-to-MFE: {c3['time_to_mfe_median_min']:.1f}min")

    entry.append(f"\n### Review Gate Answers\n")
    entry.append(f"1. Raw edge? -> (check median R:R > 1.0 at any sweep)")
    entry.append(f"2. Width breakpoint? -> (see C4 width vs R:R)")
    entry.append(f"3. Ray improvement? -> (see C4 ray impact)")
    entry.append(f"4. Time cap sufficient? -> {c3['pct_resolved_4hr']:.1%} resolved by 4hr")
    entry.append(f"5. Resolved vs ZTE seq? -> (see C5)")
    entry.append(f"6. Tick cost vs 3t? -> (see C4 cost)")
    entry.append(f"7. Comeback rate? -> {c2['comeback_20pt_rate']:.1%} after ≥20pt MFE")
    entry.append(f"\nFull results: `lab/output/zone-touch-NQ-baseline-summary.md`")
    entry.append(f"Raw data: `lab/output/zone-touch-NQ-baseline-raw.csv`\n")

    with open(path, 'a', encoding='utf-8') as f:
        if header:
            f.write(header)
        f.write('\n'.join(entry) + '\n')
    print(f"[save] journal: {path}")


def update_output_readme(sim_df):
    """Update lab/output/README.md file inventory with baseline outputs."""
    readme = OUTPUT / 'README.md'
    if not readme.exists():
        return

    content = readme.read_text(encoding='utf-8')
    n_rows = len(sim_df)

    new_rows = [
        f'| `zone-touch-NQ-baseline-raw.csv` | baseline | zone-touch | Raw trade results ({n_rows:,} rows) |',
        f'| `zone-touch-NQ-baseline-summary.md` | baseline | zone-touch | Prompt 0 summary statistics |',
        f'| `zone-touch-NQ-zone-geometry.md` | baseline | zone-touch | Zone width + nested zone analysis |',
        f'| `zone-touch-NQ-ray-analysis.md` | baseline | zone-touch | Ray availability analysis |',
    ]

    # Insert new rows before "## Expected Files" section
    marker = '\n## Expected Files'
    if marker in content:
        insert_block = '\n'.join(r for r in new_rows if r.split('`')[1] not in content)
        if insert_block:
            content = content.replace(marker, '\n' + insert_block + '\n' + marker)
            readme.write_text(content, encoding='utf-8')
            print(f"[save] updated output README: {readme}")
    else:
        print("[save] output README: marker not found, skipping update")


# ═══════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════

def main():
    t_start = _time.time()
    print("=" * 60)
    print("Zone Touch NQ — Prompt 0: Baseline")
    print(f"Calibration: {CAL_START.date()} to {CAL_END.date()}")
    print(f"Holdout:     {HO_START.date()} to {HO_END.date()}")
    print("=" * 60)

    # Ensure output directory exists
    OUTPUT.mkdir(parents=True, exist_ok=True)

    # ── Load data ──
    zte = load_zte()
    ticks = load_tick_data()
    rays = load_rays()

    # ── Part A: Zone Geometry ──
    a1 = part_a1_zone_width(zte)
    a2 = part_a2_ray_availability(zte, rays)
    a3 = part_a3_nested_zones(zte)

    # ── Part B: Tick-Level Simulation ──
    sim_df = run_simulation(zte, ticks, rays)

    if len(sim_df) == 0:
        print("\nERROR: No simulation results. Check data coverage.")
        sys.exit(1)

    # ── Post-processing ──
    sim_df = compute_resolved_sequence(sim_df)
    sim_df = compute_concurrent_exposure(sim_df)
    sim_df = tag_session(sim_df)

    # ── Part C: Summary Statistics ──
    c1 = part_c1_mfe_distribution(sim_df)
    c2 = part_c2_risk_profile(sim_df)
    c3 = part_c3_time_profile(sim_df)
    c4 = part_c4_trade_series(sim_df)
    c5 = part_c5_structural(sim_df)
    c6 = part_c6_sequence_comparison(sim_df)
    c7 = part_c7_session_comparison(sim_df)

    # ── Save outputs ──
    save_raw_results(sim_df)
    save_summary_report(a1, a2, a3, c1, c2, c3, c4, c5, c6, c7)
    save_zone_geometry(a1, a2, a3)
    save_ray_analysis(a2, rays, zte)
    append_journal(sim_df, c1, c2, c3)
    update_output_readme(sim_df)

    elapsed = _time.time() - t_start
    print(f"\n{'=' * 60}")
    print(f"Done in {elapsed:.0f}s ({elapsed/60:.1f}min)")
    print(f"Results: {len(sim_df):,} rows")
    print(f"{'=' * 60}")


if __name__ == '__main__':
    main()
