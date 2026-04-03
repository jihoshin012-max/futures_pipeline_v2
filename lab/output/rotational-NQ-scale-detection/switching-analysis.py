"""
Phase 2 (redo): Switching rule analysis for range-fade rotation strategy.

Tests dynamic switching between main and sub configs based on trigger signals.
When a trigger enters the "bad" zone for the main config, switch to a sub config
that has the best EV in that same zone (from Phase 1 conditional EV data).

Corrections from previous Phase 2:
- Rolling win rate resets daily (needs 10 trades before activating each day)
- Consecutive stops reset daily (per-side counters reset at new day)
- When rolling_win_rate is undefined (not enough trades that day), do NOT trigger switch
- R2 return rule: sub-band win rate > 50% with min 3 trades (not 5)
- R3 variants: R3_N3, R3_N5, R3_N10

Simulation logic matches Phase 1 (trigger-analysis.py):
- ddof=0, same-bar re-entry, reversals, RTH filter 09:30-15:45
- Entry at bar close, lookback=50
- NQ tick size=0.25, tick value=$5.00
- When switching configs, current position continues with existing target/stop.
  New config applies to NEXT entry only.
- 73 trading days.

Top 4 triggers from Phase 1:
  1. bar_range (rolling 10-bar avg High-Low)
  2. consecutive_stops (per-side, daily reset)
  3. speed_range_regime (2D categorical: fast_wide, fast_narrow, slow_wide, slow_narrow)
  4. rolling_win_rate (last 10 trades, daily reset)
"""

import pandas as pd
import numpy as np
import os
from datetime import datetime

# --- Config ---
LOOKBACK = 50
TICK_SIZE = 0.25
TICK_VALUE = 5.00
POINT_VALUE = TICK_VALUE / TICK_SIZE  # $20 per point

RTH_START = "09:30:00"
RTH_END = "15:45:00"

ALL_COMBOS = [
    (1.50, 2.00),
    (0.75, 2.50),
    (1.00, 2.00),
    (0.50, 2.00),
    (1.25, 2.00),
]

MAIN_COMBOS = [
    (1.50, 2.00),
    (0.75, 2.50),
    (1.00, 2.00),
]

TRIGGERS_TO_TEST = ["bar_range", "consecutive_stops", "speed_range_regime", "rolling_win_rate"]

DATA_PATH = r"c:\Projects\futures_pipeline\data\NQ-250tick-calibration.csv"
OUTPUT_DIR = r"c:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection"
PHASE1_EV_PATH = os.path.join(OUTPUT_DIR, "trigger-conditional-ev.csv")

# --- Load Phase 1 conditional EV data ---
phase1_ev = pd.read_csv(PHASE1_EV_PATH)


def combo_key(inner, outer):
    return f"i{inner:.2f}_o{outer:.2f}"


def parse_combo_key(ck):
    """Parse 'i1.50_o2.00' -> (1.50, 2.00)"""
    parts = ck.split("_")
    return float(parts[0][1:]), float(parts[1][1:])


# --- Build switch-to lookup from Phase 1 data ---
def build_switch_lookup():
    """
    Returns dict: (main_combo_key, trigger) -> {
        'worst_buckets': [list of worst bucket names],
        'switch_to': {bucket_name: (best_combo_key, best_ev)},
        'main_ev_by_bucket': {bucket_name: ev},
        'tercile_bounds': (low, high) or None for categorical
    }
    """
    lookup = {}

    for inner, outer in MAIN_COMBOS:
        mck = combo_key(inner, outer)

        for trigger in TRIGGERS_TO_TEST:
            main_data = phase1_ev[(phase1_ev["combo"] == mck) & (phase1_ev["trigger"] == trigger)]
            if len(main_data) == 0:
                continue

            # Find worst bucket for main config
            worst_ev = main_data["ev_per_trade_dollar"].min()
            worst_buckets = main_data[main_data["ev_per_trade_dollar"] == worst_ev]["bucket"].tolist()

            # Get tercile bounds
            if trigger != "speed_range_regime":
                bounds_row = main_data.iloc[0]
                tercile_bounds = (bounds_row["tercile_low_bound"], bounds_row["tercile_high_bound"])
            else:
                tercile_bounds = None

            # For each worst bucket, find the best combo
            switch_to = {}
            for bucket in worst_buckets:
                bucket_data = phase1_ev[(phase1_ev["trigger"] == trigger) & (phase1_ev["bucket"] == bucket)]
                if len(bucket_data) == 0:
                    continue
                best_row = bucket_data.loc[bucket_data["ev_per_trade_dollar"].idxmax()]
                best_ck = best_row["combo"]
                best_ev = best_row["ev_per_trade_dollar"]
                # Don't switch to self
                if best_ck == mck:
                    other = bucket_data[bucket_data["combo"] != mck]
                    if len(other) > 0:
                        best_row = other.loc[other["ev_per_trade_dollar"].idxmax()]
                        best_ck = best_row["combo"]
                        best_ev = best_row["ev_per_trade_dollar"]
                    else:
                        best_ck = mck
                        best_ev = worst_ev
                switch_to[bucket] = (best_ck, best_ev)

            main_ev_by_bucket = {}
            for _, row in main_data.iterrows():
                main_ev_by_bucket[row["bucket"]] = row["ev_per_trade_dollar"]

            lookup[(mck, trigger)] = {
                "worst_buckets": worst_buckets,
                "switch_to": switch_to,
                "main_ev_by_bucket": main_ev_by_bucket,
                "tercile_bounds": tercile_bounds,
            }

    return lookup


switch_lookup = build_switch_lookup()

# Print switch lookup for verification
print("SWITCH LOOKUP (main -> sub mapping):")
print("=" * 80)
for (mck, trigger), info in switch_lookup.items():
    print(f"  {mck} x {trigger}:")
    print(f"    Worst buckets: {info['worst_buckets']}")
    print(f"    Main EV by bucket: {info['main_ev_by_bucket']}")
    for bucket, (sub_ck, sub_ev) in info['switch_to'].items():
        print(f"    When in '{bucket}' -> switch to {sub_ck} (EV=${sub_ev:.2f})")
    if info['tercile_bounds']:
        print(f"    Tercile bounds: {info['tercile_bounds']}")
    print()


# --- Load market data ---
print("Loading market data...")
df = pd.read_csv(DATA_PATH)
df.columns = df.columns.str.strip()

high = df["High"].values.astype(float)
low = df["Low"].values.astype(float)
close = df["Last"].values.astype(float)
dates = df["Date"].values
times_raw = df["Time"].values

n = len(df)
print(f"Loaded {n} bars.")

# --- Parse bar times for RTH filter ---
def parse_time_hms(t):
    s = str(t).strip()
    return s[:8]

bar_times = np.array([parse_time_hms(t) for t in times_raw])

def is_rth(bar_idx):
    t = bar_times[bar_idx]
    return t >= RTH_START and t <= RTH_END

# --- Parse dates for daily reset detection ---
bar_dates = np.array([str(d).strip() for d in dates])

# --- Parse timestamps for bar speed ---
print("Parsing timestamps...")
timestamps = np.full(n, np.nan)
for i in range(n):
    date_str = str(dates[i]).strip()
    time_str = str(times_raw[i]).strip()
    try:
        time_parts = time_str.split(".")
        dt_str = f"{date_str} {time_parts[0]}"
        dt = datetime.strptime(dt_str, "%m/%d/%Y %H:%M:%S")
        timestamps[i] = dt.timestamp()
    except Exception:
        pass

# --- Precompute rolling stats (same as Phase 1) ---
print("Computing rolling statistics...")

roll_mean_50 = np.full(n, np.nan)
roll_std_50 = np.full(n, np.nan)

for i in range(LOOKBACK - 1, n):
    window = close[i - LOOKBACK + 1 : i + 1]
    roll_mean_50[i] = np.mean(window)
    roll_std_50[i] = np.std(window, ddof=0)

# Bar range: rolling 10-bar avg of (High - Low)
bar_range_raw = high - low
bar_range_arr = np.full(n, np.nan)
for i in range(9, n):
    bar_range_arr[i] = np.mean(bar_range_raw[i - 9 : i + 1])

# Bar speed: rolling 10-bar avg of bar duration (seconds)
bar_duration = np.full(n, np.nan)
for i in range(1, n):
    if not np.isnan(timestamps[i]) and not np.isnan(timestamps[i - 1]):
        dur = timestamps[i] - timestamps[i - 1]
        if dur > 0:
            bar_duration[i] = dur

bar_speed_arr = np.full(n, np.nan)
for i in range(10, n):
    window = bar_duration[i - 9 : i + 1]
    valid = window[~np.isnan(window)]
    if len(valid) >= 5:
        bar_speed_arr[i] = np.mean(valid)

# Speed-range regime: categorical
print("Computing rolling 200-bar medians for speed-range regime...")
speed_range_regime_arr = np.full(n, None, dtype=object)
for i in range(209, n):
    speed_window = bar_speed_arr[i - 199 : i + 1]
    range_window = bar_range_arr[i - 199 : i + 1]
    valid_speed = speed_window[~np.isnan(speed_window)]
    valid_range = range_window[~np.isnan(range_window)]
    if len(valid_speed) >= 50 and len(valid_range) >= 50:
        median_speed = np.median(valid_speed)
        median_range = np.median(valid_range)
        cur_speed = bar_speed_arr[i]
        cur_range = bar_range_arr[i]
        if np.isnan(cur_speed) or np.isnan(cur_range):
            continue
        if cur_speed < median_speed and cur_range > median_range:
            speed_range_regime_arr[i] = "fast_wide"
        elif cur_speed < median_speed and cur_range <= median_range:
            speed_range_regime_arr[i] = "fast_narrow"
        elif cur_speed >= median_speed and cur_range > median_range:
            speed_range_regime_arr[i] = "slow_wide"
        else:
            speed_range_regime_arr[i] = "slow_narrow"

print("Bar-level triggers computed.")


# --- Precompute bands for ALL combos ---
print("Precomputing bands for all combos...")
combo_bands = {}
for inner_mult, outer_mult in ALL_COMBOS:
    ck = combo_key(inner_mult, outer_mult)
    inner_top = roll_mean_50 + inner_mult * roll_std_50
    inner_bot = roll_mean_50 - inner_mult * roll_std_50
    outer_top = roll_mean_50 + outer_mult * roll_std_50
    outer_bot = roll_mean_50 - outer_mult * roll_std_50
    combo_bands[ck] = {
        "inner_top": inner_top,
        "inner_bot": inner_bot,
        "outer_top": outer_top,
        "outer_bot": outer_bot,
    }


# --- Static simulation with daily resets ---
def run_static_simulation(inner_mult, outer_mult):
    """Run the full simulation for one combo, return list of trade dicts with trigger values.
    consecutive_stops: per-side, resets daily.
    rolling_win_rate: last 10 trades THIS DAY, undefined until 10 trades that day.
    """
    ck = combo_key(inner_mult, outer_mult)
    bands = combo_bands[ck]
    inner_top = bands["inner_top"]
    inner_bot = bands["inner_bot"]
    outer_top = bands["outer_top"]
    outer_bot = bands["outer_bot"]

    trades = []
    # Per-side consecutive stop counters (daily reset)
    consec_stops_long = 0
    consec_stops_short = 0
    recent_results_today = []  # for rolling win rate (daily reset)
    current_trade_date = None

    in_position = False
    direction = None
    entry_price = 0.0
    target_price = 0.0
    stop_price = 0.0
    entry_bar = 0
    entry_target_offset = 0.0
    entry_stop_offset = 0.0
    triggers_at_entry = {}

    def check_buy(bar_idx):
        if np.isnan(roll_mean_50[bar_idx]):
            return False
        return low[bar_idx] <= inner_bot[bar_idx] and low[bar_idx] > outer_bot[bar_idx]

    def check_sell(bar_idx):
        if np.isnan(roll_mean_50[bar_idx]):
            return False
        return high[bar_idx] >= inner_top[bar_idx] and high[bar_idx] < outer_top[bar_idx]

    def check_daily_reset(bar_idx):
        nonlocal current_trade_date, consec_stops_long, consec_stops_short, recent_results_today
        this_date = bar_dates[bar_idx]
        if current_trade_date is None or this_date != current_trade_date:
            current_trade_date = this_date
            consec_stops_long = 0
            consec_stops_short = 0
            recent_results_today = []

    def get_triggers(bar_idx, dir_for_consec):
        """Get trigger values at entry. dir_for_consec is the direction being entered."""
        # Rolling win rate: last 10 trades THIS DAY only
        if len(recent_results_today) >= 10:
            rwr = sum(recent_results_today[-10:]) / 10.0
        else:
            rwr = np.nan

        # consecutive_stops is per-side
        if dir_for_consec == "long":
            cs = consec_stops_long
        else:
            cs = consec_stops_short

        return {
            "bar_range": bar_range_arr[bar_idx] if not np.isnan(bar_range_arr[bar_idx]) else np.nan,
            "consecutive_stops": cs,
            "rolling_win_rate": rwr,
            "speed_range_regime": speed_range_regime_arr[bar_idx] if speed_range_regime_arr[bar_idx] is not None else np.nan,
        }

    i = LOOKBACK
    while i < n:
        if not in_position:
            if np.isnan(roll_mean_50[i]) or not is_rth(i):
                i += 1
                continue

            check_daily_reset(i)

            buy_sig = check_buy(i)
            sell_sig = check_sell(i)
            if buy_sig:
                direction = "long"
            elif sell_sig:
                direction = "short"
            else:
                i += 1
                continue

            triggers_at_entry = get_triggers(i, direction)
            entry_price = close[i]
            t_offset = inner_top[i] - inner_bot[i]
            if direction == "long":
                s_offset = inner_bot[i] - outer_bot[i]
                target_price = entry_price + t_offset
                stop_price = entry_price - s_offset
            else:
                s_offset = outer_top[i] - inner_top[i]
                target_price = entry_price - t_offset
                stop_price = entry_price + s_offset
            entry_target_offset = t_offset
            entry_stop_offset = s_offset
            entry_bar = i
            in_position = True
            i += 1
            continue

        if i >= n:
            break

        check_daily_reset(i)

        if direction == "long":
            target_hit = high[i] >= target_price
            stop_hit = low[i] <= stop_price
            buy_reversal = False
            sell_reversal = check_sell(i) and is_rth(i)
        else:
            target_hit = low[i] <= target_price
            stop_hit = high[i] >= stop_price
            buy_reversal = check_buy(i) and is_rth(i)
            sell_reversal = False

        reversal_signal = buy_reversal or sell_reversal

        if target_hit and stop_hit:
            if direction == "long":
                if close[i] >= entry_price:
                    pnl = entry_target_offset
                    exit_type = "target"
                else:
                    pnl = -entry_stop_offset
                    exit_type = "stop"
            else:
                if close[i] <= entry_price:
                    pnl = entry_target_offset
                    exit_type = "target"
                else:
                    pnl = -entry_stop_offset
                    exit_type = "stop"
        elif target_hit:
            pnl = entry_target_offset
            exit_type = "target"
        elif stop_hit:
            pnl = -entry_stop_offset
            exit_type = "stop"
        elif reversal_signal:
            if direction == "long":
                pnl = close[i] - entry_price
                new_direction = "short"
            else:
                pnl = entry_price - close[i]
                new_direction = "long"
            exit_type = "reversal"
        else:
            i += 1
            continue

        pnl_dollar = pnl * POINT_VALUE
        is_win = pnl > 0

        trades.append({
            "entry_bar": entry_bar,
            "exit_bar": i,
            "direction": direction,
            "entry_price": entry_price,
            "exit_type": exit_type,
            "pnl_points": pnl,
            "pnl_dollar": pnl_dollar,
            "is_win": is_win,
            **triggers_at_entry,
        })

        # Update per-side consecutive stops
        if exit_type == "stop":
            if direction == "long":
                consec_stops_long += 1
            else:
                consec_stops_short += 1
        else:
            if direction == "long":
                consec_stops_long = 0
            else:
                consec_stops_short = 0

        recent_results_today.append(1 if is_win else 0)

        if exit_type == "reversal":
            triggers_at_entry = get_triggers(i, new_direction)
            entry_price_new = close[i]
            t_offset_new = inner_top[i] - inner_bot[i]
            if new_direction == "long":
                s_offset_new = inner_bot[i] - outer_bot[i]
                target_price = entry_price_new + t_offset_new
                stop_price = entry_price_new - s_offset_new
            else:
                s_offset_new = outer_top[i] - inner_top[i]
                target_price = entry_price_new - t_offset_new
                stop_price = entry_price_new + s_offset_new
            entry_target_offset = t_offset_new
            entry_stop_offset = s_offset_new
            entry_price = entry_price_new
            entry_bar = i
            direction = new_direction
            in_position = True
            i += 1
            continue

        in_position = False
        direction = None
        # same-bar re-entry: don't increment i
        continue

    return trades


# --- Run static baselines ---
print("\nRunning static baselines...")
static_results = {}
for inner_mult, outer_mult in ALL_COMBOS:
    ck = combo_key(inner_mult, outer_mult)
    trades = run_static_simulation(inner_mult, outer_mult)
    total_pnl = sum(t["pnl_dollar"] for t in trades)
    ev = np.mean([t["pnl_dollar"] for t in trades]) if trades else 0
    wr = sum(1 for t in trades if t["is_win"]) / len(trades) * 100 if trades else 0

    # Max drawdown
    equity = np.cumsum([t["pnl_dollar"] for t in trades])
    peak = np.maximum.accumulate(equity)
    dd = peak - equity
    max_dd = np.max(dd) if len(dd) > 0 else 0

    static_results[ck] = {
        "trades": trades,
        "n_trades": len(trades),
        "total_pnl": total_pnl,
        "ev": ev,
        "win_rate": wr,
        "max_dd": max_dd,
    }
    print(f"  {ck}: {len(trades)} trades, EV=${ev:.2f}, PnL=${total_pnl:.2f}, WR={wr:.1f}%, MaxDD=${max_dd:.2f}")


# --- Compute tercile/percentile boundaries from static runs ---
def compute_boundaries(trades, trigger):
    vals = [t[trigger] for t in trades if not (isinstance(t[trigger], float) and np.isnan(t[trigger]))]
    if len(vals) < 10:
        return None
    vals = np.array(vals, dtype=float)
    tercile_low = np.percentile(vals, 100 / 3)
    tercile_high = np.percentile(vals, 200 / 3)
    p25 = np.percentile(vals, 25)
    p75 = np.percentile(vals, 75)
    return {
        "tercile_low": tercile_low,
        "tercile_high": tercile_high,
        "p25": p25,
        "p75": p75,
    }


# Store all boundaries for summary output
all_boundaries = {}
for inner_mult, outer_mult in MAIN_COMBOS:
    ck = combo_key(inner_mult, outer_mult)
    all_boundaries[ck] = {}
    for trigger in ["bar_range", "consecutive_stops", "rolling_win_rate"]:
        b = compute_boundaries(static_results[ck]["trades"], trigger)
        if b is not None:
            all_boundaries[ck][trigger] = b


# --- Switching simulation with daily resets ---
def run_switching_simulation(main_inner, main_outer, trigger, switch_rule, return_rule, return_n=None):
    """
    Run simulation with dynamic config switching. Returns all trades in chronological order.

    Daily resets:
    - consecutive_stops: per-side counters reset at new day
    - rolling_win_rate: trade history clears at new day; undefined until 10 trades that day
    - When rolling_win_rate is undefined, do NOT trigger a switch based on it

    switch_rule: 'A' (worst tercile), 'B' (worst 25th pctile), 'C' (worst categorical bucket)
    return_rule: 'R1' (trigger leaves bad zone), 'R2' (sub WR>50% min 3 trades that day),
                 'R3_N3/R3_N5/R3_N10' (after N sub trades), 'R4' (first sub win)
    """
    mck = combo_key(main_inner, main_outer)
    lookup_key = (mck, trigger)
    if lookup_key not in switch_lookup:
        return None

    info = switch_lookup[lookup_key]
    worst_buckets = info["worst_buckets"]
    switch_to_map = info["switch_to"]

    # Get boundaries
    if trigger in ("bar_range", "consecutive_stops", "rolling_win_rate"):
        bounds = all_boundaries.get(mck, {}).get(trigger)
        if bounds is None:
            return None
    else:
        bounds = None

    # Determine sub config
    sub_configs = {}
    for bucket in worst_buckets:
        if bucket in switch_to_map:
            sub_ck, _ = switch_to_map[bucket]
            sub_configs[bucket] = parse_combo_key(sub_ck)

    if not sub_configs:
        return None

    primary_worst = worst_buckets[0]
    sub_inner, sub_outer = sub_configs[primary_worst]
    sub_ck = combo_key(sub_inner, sub_outer)

    main_b = combo_bands[mck]
    sub_b = combo_bands[sub_ck]

    def is_in_bad_zone(trigger_val):
        if trigger == "speed_range_regime":
            return trigger_val in worst_buckets
        if isinstance(trigger_val, float) and np.isnan(trigger_val):
            return False

        if switch_rule == "A":
            if primary_worst == "low":
                return trigger_val <= bounds["tercile_low"]
            elif primary_worst == "high":
                return trigger_val > bounds["tercile_high"]
            else:
                return bounds["tercile_low"] < trigger_val <= bounds["tercile_high"]
        elif switch_rule == "B":
            if primary_worst == "low":
                return trigger_val <= bounds["p25"]
            elif primary_worst == "high":
                return trigger_val > bounds["p75"]
            else:
                return bounds["tercile_low"] < trigger_val <= bounds["tercile_high"]
        return False

    # State
    active_config = "main"
    all_trades = []  # chronological, with 'config' field
    sub_trade_count = 0
    sub_results_today = []  # sub-band trade results THIS DAY for R2

    # Daily-reset trigger state
    consec_stops_long = 0
    consec_stops_short = 0
    recent_results_today = []  # for rolling_win_rate (daily reset)
    current_trade_date = None

    in_position = False
    direction = None
    entry_price = 0.0
    target_price = 0.0
    stop_price = 0.0
    entry_bar = 0
    entry_target_offset = 0.0
    entry_stop_offset = 0.0
    entry_config = "main"

    def check_daily_reset(bar_idx):
        nonlocal current_trade_date, consec_stops_long, consec_stops_short
        nonlocal recent_results_today, sub_results_today
        this_date = bar_dates[bar_idx]
        if current_trade_date is None or this_date != current_trade_date:
            current_trade_date = this_date
            consec_stops_long = 0
            consec_stops_short = 0
            recent_results_today = []
            sub_results_today = []

    def get_bands():
        return main_b if active_config == "main" else sub_b

    def check_buy(bar_idx, bands_dict):
        if np.isnan(roll_mean_50[bar_idx]):
            return False
        return low[bar_idx] <= bands_dict["inner_bot"][bar_idx] and low[bar_idx] > bands_dict["outer_bot"][bar_idx]

    def check_sell(bar_idx, bands_dict):
        if np.isnan(roll_mean_50[bar_idx]):
            return False
        return high[bar_idx] >= bands_dict["inner_top"][bar_idx] and high[bar_idx] < bands_dict["outer_top"][bar_idx]

    def get_trigger_val(bar_idx, dir_hint=None):
        """Get the current trigger value.
        For consecutive_stops, dir_hint specifies which side to check.
        If dir_hint is None, use max of both sides.
        """
        if trigger == "bar_range":
            return bar_range_arr[bar_idx] if not np.isnan(bar_range_arr[bar_idx]) else np.nan
        elif trigger == "consecutive_stops":
            if dir_hint == "long":
                return consec_stops_long
            elif dir_hint == "short":
                return consec_stops_short
            else:
                return max(consec_stops_long, consec_stops_short)
        elif trigger == "rolling_win_rate":
            if len(recent_results_today) >= 10:
                return sum(recent_results_today[-10:]) / 10.0
            return np.nan  # undefined until 10 trades that day
        elif trigger == "speed_range_regime":
            return speed_range_regime_arr[bar_idx] if speed_range_regime_arr[bar_idx] is not None else np.nan
        return np.nan

    def check_forward_switch(bar_idx):
        nonlocal active_config, sub_trade_count, sub_results_today
        if active_config == "main":
            tv = get_trigger_val(bar_idx)
            # If trigger value is undefined (NaN), do NOT switch
            if isinstance(tv, float) and np.isnan(tv):
                return
            if is_in_bad_zone(tv):
                active_config = "sub"
                sub_trade_count = 0
                # Don't reset sub_results_today here - it persists within the day

    def check_return_switch(bar_idx):
        nonlocal active_config
        if active_config != "sub":
            return
        if return_rule == "R1":
            tv = get_trigger_val(bar_idx)
            if isinstance(tv, float) and np.isnan(tv):
                return
            if not is_in_bad_zone(tv):
                active_config = "main"
        elif return_rule == "R2":
            # Return when sub-band win rate > 50% with min 3 trades THIS DAY
            if len(sub_results_today) >= 3:
                if sum(sub_results_today) / len(sub_results_today) > 0.5:
                    active_config = "main"
        elif return_rule.startswith("R3"):
            if sub_trade_count >= return_n:
                active_config = "main"
        elif return_rule == "R4":
            if len(sub_results_today) > 0 and sub_results_today[-1] == 1:
                active_config = "main"

    def record_trade(pnl_pts, exit_type, config_at_entry, dir_at_entry):
        nonlocal consec_stops_long, consec_stops_short, sub_trade_count
        pnl_dollar = pnl_pts * POINT_VALUE
        is_win = pnl_pts > 0
        all_trades.append({
            "pnl_dollar": pnl_dollar,
            "is_win": is_win,
            "config": config_at_entry,
            "exit_type": exit_type,
        })
        # Update per-side consecutive stops
        if exit_type == "stop":
            if dir_at_entry == "long":
                consec_stops_long += 1
            else:
                consec_stops_short += 1
        else:
            if dir_at_entry == "long":
                consec_stops_long = 0
            else:
                consec_stops_short = 0

        recent_results_today.append(1 if is_win else 0)

        if config_at_entry == "sub":
            sub_trade_count += 1
            sub_results_today.append(1 if is_win else 0)

    i = LOOKBACK
    while i < n:
        if not in_position:
            if np.isnan(roll_mean_50[i]) or not is_rth(i):
                i += 1
                continue

            check_daily_reset(i)

            # Config switching check
            check_forward_switch(i)
            check_return_switch(i)

            bands = get_bands()
            buy_sig = check_buy(i, bands)
            sell_sig = check_sell(i, bands)

            if buy_sig:
                direction = "long"
            elif sell_sig:
                direction = "short"
            else:
                i += 1
                continue

            entry_config = active_config
            entry_price = close[i]
            t_offset = bands["inner_top"][i] - bands["inner_bot"][i]
            if direction == "long":
                s_offset = bands["inner_bot"][i] - bands["outer_bot"][i]
                target_price = entry_price + t_offset
                stop_price = entry_price - s_offset
            else:
                s_offset = bands["outer_top"][i] - bands["inner_top"][i]
                target_price = entry_price - t_offset
                stop_price = entry_price + s_offset
            entry_target_offset = t_offset
            entry_stop_offset = s_offset
            entry_bar = i
            in_position = True
            i += 1
            continue

        if i >= n:
            break

        check_daily_reset(i)

        if direction == "long":
            target_hit = high[i] >= target_price
            stop_hit = low[i] <= stop_price
            buy_reversal = False
            active_b = get_bands()
            sell_reversal = check_sell(i, active_b) and is_rth(i)
        else:
            target_hit = low[i] <= target_price
            stop_hit = high[i] >= stop_price
            active_b = get_bands()
            buy_reversal = check_buy(i, active_b) and is_rth(i)
            sell_reversal = False

        reversal_signal = buy_reversal or sell_reversal

        if target_hit and stop_hit:
            if direction == "long":
                if close[i] >= entry_price:
                    pnl = entry_target_offset; exit_type = "target"
                else:
                    pnl = -entry_stop_offset; exit_type = "stop"
            else:
                if close[i] <= entry_price:
                    pnl = entry_target_offset; exit_type = "target"
                else:
                    pnl = -entry_stop_offset; exit_type = "stop"
        elif target_hit:
            pnl = entry_target_offset; exit_type = "target"
        elif stop_hit:
            pnl = -entry_stop_offset; exit_type = "stop"
        elif reversal_signal:
            if direction == "long":
                pnl = close[i] - entry_price; new_direction = "short"
            else:
                pnl = entry_price - close[i]; new_direction = "long"
            exit_type = "reversal"
        else:
            i += 1
            continue

        record_trade(pnl, exit_type, entry_config, direction)

        if exit_type == "reversal":
            in_position = False
            check_forward_switch(i)
            check_return_switch(i)

            entry_config = active_config
            bands = get_bands()
            entry_price = close[i]
            t_offset = bands["inner_top"][i] - bands["inner_bot"][i]
            if new_direction == "long":
                s_offset = bands["inner_bot"][i] - bands["outer_bot"][i]
                target_price = entry_price + t_offset
                stop_price = entry_price - s_offset
            else:
                s_offset = bands["outer_top"][i] - bands["inner_top"][i]
                target_price = entry_price - t_offset
                stop_price = entry_price + s_offset
            entry_target_offset = t_offset
            entry_stop_offset = s_offset
            entry_bar = i
            direction = new_direction
            in_position = True
            i += 1
            continue

        in_position = False
        direction = None
        continue

    return all_trades


# --- Compute metrics from trade list ---
def compute_metrics(trades):
    if not trades:
        return None
    n_total = len(trades)
    n_main = sum(1 for t in trades if t["config"] == "main")
    n_sub = sum(1 for t in trades if t["config"] == "sub")

    overall_pnl = sum(t["pnl_dollar"] for t in trades)
    overall_ev = overall_pnl / n_total

    main_pnls = [t["pnl_dollar"] for t in trades if t["config"] == "main"]
    sub_pnls = [t["pnl_dollar"] for t in trades if t["config"] == "sub"]
    main_ev = np.mean(main_pnls) if main_pnls else 0
    sub_ev = np.mean(sub_pnls) if sub_pnls else 0

    wins = sum(1 for t in trades if t["is_win"])
    win_rate = wins / n_total * 100

    # Max drawdown
    equity = np.cumsum([t["pnl_dollar"] for t in trades])
    peak = np.maximum.accumulate(equity)
    dd = peak - equity
    max_dd = np.max(dd) if len(dd) > 0 else 0

    pnl_dd_ratio = overall_pnl / max_dd if max_dd > 0 else np.inf

    return {
        "n_total": n_total,
        "n_main": n_main,
        "n_sub": n_sub,
        "sub_pct": round(n_sub / n_total * 100, 1),
        "overall_pnl": overall_pnl,
        "overall_ev": overall_ev,
        "main_ev": main_ev,
        "sub_ev": sub_ev,
        "win_rate": win_rate,
        "max_dd": max_dd,
        "pnl_dd_ratio": pnl_dd_ratio,
    }


# --- Run all switching combinations ---
print("\nRunning switching simulations...")
results_rows = []

# Best static config EV (across all 5 combos)
best_static_ev = max(r["ev"] for r in static_results.values())
best_static_key = max(static_results, key=lambda k: static_results[k]["ev"])
print(f"Best static config: {best_static_key} with EV=${best_static_ev:.2f}")

# Store raw trades for martingale overlay later
switching_trades = {}

total_combos = 0
for main_inner, main_outer in MAIN_COMBOS:
    mck = combo_key(main_inner, main_outer)

    for trigger in TRIGGERS_TO_TEST:
        # Determine valid switch rules
        if trigger == "speed_range_regime":
            switch_rules = ["C"]
        else:
            switch_rules = ["A", "B"]

        return_rules_list = [
            ("R1", None),
            ("R2", None),
            ("R3_N3", 3),
            ("R3_N5", 5),
            ("R3_N10", 10),
            ("R4", None),
        ]

        for sw_rule in switch_rules:
            for ret_rule, ret_n in return_rules_list:
                total_combos += 1

                trades = run_switching_simulation(
                    main_inner, main_outer, trigger, sw_rule, ret_rule, return_n=ret_n
                )

                if trades is None or len(trades) == 0:
                    continue

                metrics = compute_metrics(trades)
                if metrics is None:
                    continue

                # Static baseline
                static_ev = static_results[mck]["ev"]

                # Sub config used
                lookup_key = (mck, trigger)
                sub_ck_used = "none"
                if lookup_key in switch_lookup:
                    for bkt, (sck, _) in switch_lookup[lookup_key]["switch_to"].items():
                        sub_ck_used = sck
                        break

                sys_id = f"{mck}|{trigger}|{sw_rule}|{ret_rule}"
                switching_trades[sys_id] = trades

                results_rows.append({
                    "main_config": mck,
                    "trigger": trigger,
                    "switch_rule": sw_rule,
                    "return_rule": ret_rule,
                    "sub_config": sub_ck_used,
                    "total_trades": metrics["n_total"],
                    "main_trades": metrics["n_main"],
                    "sub_trades": metrics["n_sub"],
                    "sub_pct": metrics["sub_pct"],
                    "overall_ev": round(metrics["overall_ev"], 2),
                    "main_mode_ev": round(metrics["main_ev"], 2),
                    "sub_mode_ev": round(metrics["sub_ev"], 2),
                    "overall_pnl": round(metrics["overall_pnl"], 2),
                    "max_drawdown": round(metrics["max_dd"], 2),
                    "pnl_dd_ratio": round(metrics["pnl_dd_ratio"], 3) if not np.isinf(metrics["pnl_dd_ratio"]) else 999.0,
                    "win_rate_pct": round(metrics["win_rate"], 2),
                    "static_main_ev": round(static_ev, 2),
                    "best_static_ev": round(best_static_ev, 2),
                    "ev_vs_static_main": round(metrics["overall_ev"] - static_ev, 2),
                    "ev_vs_best_static": round(metrics["overall_ev"] - best_static_ev, 2),
                })

print(f"Completed {total_combos} switching combinations, {len(results_rows)} with results.")

# --- Write results ---
results_df = pd.DataFrame(results_rows)
results_df = results_df.sort_values("overall_ev", ascending=False)
results_df.to_csv(os.path.join(OUTPUT_DIR, "switching-results.csv"), index=False)
print(f"Wrote switching-results.csv ({len(results_df)} rows)")


# ===================================================================
# MARTINGALE OVERLAY on top 3 switching systems
# ===================================================================
print("\nMartingale overlay on top 3 switching systems...")

top3_systems = results_df.head(3)

martingale_configs = [
    {"label": "m1.5_max3", "mult": 1.5, "max_level": 3},
    {"label": "m2.0_max2", "mult": 2.0, "max_level": 2},
]

mart_rows = []
for idx, sys_row in top3_systems.iterrows():
    sys_id = f"{sys_row['main_config']}|{sys_row['trigger']}|{sys_row['switch_rule']}|{sys_row['return_rule']}"
    trades = switching_trades.get(sys_id)
    if trades is None:
        continue

    for mc in martingale_configs:
        # Martingale: on loss, multiply contracts by mc['mult'], up to mc['max_level'] levels
        # Counters reset daily AND when switching configs
        contracts = 1.0
        mart_level = 0  # 0 = base, 1 = first escalation, etc.
        current_config = None
        current_date = None

        total_pnl = 0.0
        equity_curve = []
        largest_single_loss = 0.0

        for t in trades:
            # Detect config switch or day change
            # We don't have bar indices here, but we can track config changes
            if t["config"] != current_config:
                contracts = 1.0
                mart_level = 0
                current_config = t["config"]

            pnl = t["pnl_dollar"] * contracts
            total_pnl += pnl
            equity_curve.append(total_pnl)

            if pnl < largest_single_loss:
                largest_single_loss = pnl

            # After trade, adjust for next
            if not t["is_win"]:
                if mart_level < mc["max_level"]:
                    mart_level += 1
                    contracts = mc["mult"] ** mart_level
                # else stay at max
            else:
                contracts = 1.0
                mart_level = 0

        equity_arr = np.array(equity_curve)
        peak = np.maximum.accumulate(equity_arr)
        dd = peak - equity_arr
        max_dd = np.max(dd) if len(dd) > 0 else 0
        pnl_dd = total_pnl / max_dd if max_dd > 0 else np.inf

        mart_rows.append({
            "main_config": sys_row["main_config"],
            "trigger": sys_row["trigger"],
            "switch_rule": sys_row["switch_rule"],
            "return_rule": sys_row["return_rule"],
            "sub_config": sys_row["sub_config"],
            "martingale": mc["label"],
            "total_trades": len(trades),
            "overall_pnl": round(total_pnl, 2),
            "max_drawdown": round(max_dd, 2),
            "pnl_dd_ratio": round(pnl_dd, 3) if not np.isinf(pnl_dd) else 999.0,
            "largest_single_loss": round(largest_single_loss, 2),
            "base_pnl": sys_row["overall_pnl"],
            "base_max_dd": sys_row["max_drawdown"],
        })

mart_df = pd.DataFrame(mart_rows)
mart_df.to_csv(os.path.join(OUTPUT_DIR, "switching-martingale.csv"), index=False)
print(f"Wrote switching-martingale.csv ({len(mart_df)} rows)")


# ===================================================================
# DAILY-RESET MARTINGALE: re-run top 3 with daily resets
# ===================================================================
# The above martingale resets on config switch but not daily.
# Now also add daily resets by re-running the switching simulation
# with trade-level date tracking.

# We need bar index info to detect day boundaries.
# Re-run top 3 switching simulations but track entry_bar in trades.

def run_switching_simulation_with_bars(main_inner, main_outer, trigger, switch_rule, return_rule, return_n=None):
    """Same as run_switching_simulation but also returns entry_bar for each trade."""
    mck = combo_key(main_inner, main_outer)
    lookup_key = (mck, trigger)
    if lookup_key not in switch_lookup:
        return None

    info = switch_lookup[lookup_key]
    worst_buckets = info["worst_buckets"]
    switch_to_map = info["switch_to"]

    if trigger in ("bar_range", "consecutive_stops", "rolling_win_rate"):
        bounds = all_boundaries.get(mck, {}).get(trigger)
        if bounds is None:
            return None
    else:
        bounds = None

    sub_configs = {}
    for bucket in worst_buckets:
        if bucket in switch_to_map:
            sub_ck, _ = switch_to_map[bucket]
            sub_configs[bucket] = parse_combo_key(sub_ck)

    if not sub_configs:
        return None

    primary_worst = worst_buckets[0]
    sub_inner, sub_outer = sub_configs[primary_worst]
    sub_ck = combo_key(sub_inner, sub_outer)

    main_b = combo_bands[mck]
    sub_b = combo_bands[sub_ck]

    def is_in_bad_zone(trigger_val):
        if trigger == "speed_range_regime":
            return trigger_val in worst_buckets
        if isinstance(trigger_val, float) and np.isnan(trigger_val):
            return False
        if switch_rule == "A":
            if primary_worst == "low":
                return trigger_val <= bounds["tercile_low"]
            elif primary_worst == "high":
                return trigger_val > bounds["tercile_high"]
            else:
                return bounds["tercile_low"] < trigger_val <= bounds["tercile_high"]
        elif switch_rule == "B":
            if primary_worst == "low":
                return trigger_val <= bounds["p25"]
            elif primary_worst == "high":
                return trigger_val > bounds["p75"]
            else:
                return bounds["tercile_low"] < trigger_val <= bounds["tercile_high"]
        return False

    active_config = "main"
    all_trades = []
    sub_trade_count = 0
    sub_results_today = []
    consec_stops_long = 0
    consec_stops_short = 0
    recent_results_today = []
    current_trade_date = None

    in_position = False
    direction = None
    entry_price = 0.0
    target_price = 0.0
    stop_price = 0.0
    entry_bar_idx = 0
    entry_target_offset = 0.0
    entry_stop_offset = 0.0
    entry_config = "main"

    def check_daily_reset_inner(bar_idx):
        nonlocal current_trade_date, consec_stops_long, consec_stops_short
        nonlocal recent_results_today, sub_results_today
        this_date = bar_dates[bar_idx]
        if current_trade_date is None or this_date != current_trade_date:
            current_trade_date = this_date
            consec_stops_long = 0
            consec_stops_short = 0
            recent_results_today = []
            sub_results_today = []

    def get_bands_inner():
        return main_b if active_config == "main" else sub_b

    def check_buy_inner(bar_idx, bd):
        if np.isnan(roll_mean_50[bar_idx]):
            return False
        return low[bar_idx] <= bd["inner_bot"][bar_idx] and low[bar_idx] > bd["outer_bot"][bar_idx]

    def check_sell_inner(bar_idx, bd):
        if np.isnan(roll_mean_50[bar_idx]):
            return False
        return high[bar_idx] >= bd["inner_top"][bar_idx] and high[bar_idx] < bd["outer_top"][bar_idx]

    def get_trigger_val_inner(bar_idx):
        if trigger == "bar_range":
            return bar_range_arr[bar_idx] if not np.isnan(bar_range_arr[bar_idx]) else np.nan
        elif trigger == "consecutive_stops":
            return max(consec_stops_long, consec_stops_short)
        elif trigger == "rolling_win_rate":
            if len(recent_results_today) >= 10:
                return sum(recent_results_today[-10:]) / 10.0
            return np.nan
        elif trigger == "speed_range_regime":
            return speed_range_regime_arr[bar_idx] if speed_range_regime_arr[bar_idx] is not None else np.nan
        return np.nan

    def check_forward_inner(bar_idx):
        nonlocal active_config, sub_trade_count, sub_results_today
        if active_config == "main":
            tv = get_trigger_val_inner(bar_idx)
            if isinstance(tv, float) and np.isnan(tv):
                return
            if is_in_bad_zone(tv):
                active_config = "sub"
                sub_trade_count = 0

    def check_return_inner(bar_idx):
        nonlocal active_config
        if active_config != "sub":
            return
        if return_rule == "R1":
            tv = get_trigger_val_inner(bar_idx)
            if isinstance(tv, float) and np.isnan(tv):
                return
            if not is_in_bad_zone(tv):
                active_config = "main"
        elif return_rule == "R2":
            if len(sub_results_today) >= 3:
                if sum(sub_results_today) / len(sub_results_today) > 0.5:
                    active_config = "main"
        elif return_rule.startswith("R3"):
            if sub_trade_count >= return_n:
                active_config = "main"
        elif return_rule == "R4":
            if len(sub_results_today) > 0 and sub_results_today[-1] == 1:
                active_config = "main"

    i = LOOKBACK
    while i < n:
        if not in_position:
            if np.isnan(roll_mean_50[i]) or not is_rth(i):
                i += 1
                continue

            check_daily_reset_inner(i)
            check_forward_inner(i)
            check_return_inner(i)

            bd = get_bands_inner()
            buy_sig = check_buy_inner(i, bd)
            sell_sig = check_sell_inner(i, bd)

            if buy_sig:
                direction = "long"
            elif sell_sig:
                direction = "short"
            else:
                i += 1
                continue

            entry_config = active_config
            entry_price = close[i]
            t_offset = bd["inner_top"][i] - bd["inner_bot"][i]
            if direction == "long":
                s_offset = bd["inner_bot"][i] - bd["outer_bot"][i]
                target_price = entry_price + t_offset
                stop_price = entry_price - s_offset
            else:
                s_offset = bd["outer_top"][i] - bd["inner_top"][i]
                target_price = entry_price - t_offset
                stop_price = entry_price + s_offset
            entry_target_offset = t_offset
            entry_stop_offset = s_offset
            entry_bar_idx = i
            in_position = True
            i += 1
            continue

        if i >= n:
            break

        check_daily_reset_inner(i)

        if direction == "long":
            target_hit = high[i] >= target_price
            stop_hit = low[i] <= stop_price
            buy_reversal = False
            ab = get_bands_inner()
            sell_reversal = check_sell_inner(i, ab) and is_rth(i)
        else:
            target_hit = low[i] <= target_price
            stop_hit = high[i] >= stop_price
            ab = get_bands_inner()
            buy_reversal = check_buy_inner(i, ab) and is_rth(i)
            sell_reversal = False

        reversal_signal = buy_reversal or sell_reversal

        if target_hit and stop_hit:
            if direction == "long":
                if close[i] >= entry_price:
                    pnl = entry_target_offset; exit_type = "target"
                else:
                    pnl = -entry_stop_offset; exit_type = "stop"
            else:
                if close[i] <= entry_price:
                    pnl = entry_target_offset; exit_type = "target"
                else:
                    pnl = -entry_stop_offset; exit_type = "stop"
        elif target_hit:
            pnl = entry_target_offset; exit_type = "target"
        elif stop_hit:
            pnl = -entry_stop_offset; exit_type = "stop"
        elif reversal_signal:
            if direction == "long":
                pnl = close[i] - entry_price; new_direction = "short"
            else:
                pnl = entry_price - close[i]; new_direction = "long"
            exit_type = "reversal"
        else:
            i += 1
            continue

        pnl_dollar = pnl * POINT_VALUE
        is_win = pnl > 0
        all_trades.append({
            "pnl_dollar": pnl_dollar,
            "is_win": is_win,
            "config": entry_config,
            "exit_type": exit_type,
            "entry_bar": entry_bar_idx,
        })

        if exit_type == "stop":
            if direction == "long":
                consec_stops_long += 1
            else:
                consec_stops_short += 1
        else:
            if direction == "long":
                consec_stops_long = 0
            else:
                consec_stops_short = 0

        recent_results_today.append(1 if is_win else 0)
        if entry_config == "sub":
            sub_trade_count += 1
            sub_results_today.append(1 if is_win else 0)

        if exit_type == "reversal":
            in_position = False
            check_forward_inner(i)
            check_return_inner(i)
            entry_config = active_config
            bd = get_bands_inner()
            entry_price = close[i]
            t_offset = bd["inner_top"][i] - bd["inner_bot"][i]
            if new_direction == "long":
                s_offset = bd["inner_bot"][i] - bd["outer_bot"][i]
                target_price = entry_price + t_offset
                stop_price = entry_price - s_offset
            else:
                s_offset = bd["outer_top"][i] - bd["inner_top"][i]
                target_price = entry_price - t_offset
                stop_price = entry_price + s_offset
            entry_target_offset = t_offset
            entry_stop_offset = s_offset
            entry_bar_idx = i
            direction = new_direction
            in_position = True
            i += 1
            continue

        in_position = False
        direction = None
        continue

    return all_trades


# Re-run top 3 with bar tracking for proper daily-reset martingale
print("Re-running top 3 with bar tracking for daily-reset martingale...")
mart_rows_daily = []

for idx, sys_row in top3_systems.iterrows():
    main_inner, main_outer = parse_combo_key(sys_row["main_config"])
    trigger = sys_row["trigger"]
    sw_rule = sys_row["switch_rule"]
    ret_rule = sys_row["return_rule"]
    ret_n = None
    if ret_rule.startswith("R3_N"):
        ret_n = int(ret_rule.split("N")[1])

    trades_with_bars = run_switching_simulation_with_bars(
        main_inner, main_outer, trigger, sw_rule, ret_rule, return_n=ret_n
    )
    if trades_with_bars is None or len(trades_with_bars) == 0:
        continue

    for mc in martingale_configs:
        contracts = 1.0
        mart_level = 0
        current_config = None
        current_date_str = None

        total_pnl = 0.0
        equity_curve = []
        largest_single_loss = 0.0

        for t in trades_with_bars:
            # Detect day change
            trade_date = bar_dates[t["entry_bar"]]
            if trade_date != current_date_str:
                contracts = 1.0
                mart_level = 0
                current_date_str = trade_date

            # Detect config switch
            if t["config"] != current_config:
                contracts = 1.0
                mart_level = 0
                current_config = t["config"]

            pnl = t["pnl_dollar"] * contracts
            total_pnl += pnl
            equity_curve.append(total_pnl)

            if pnl < largest_single_loss:
                largest_single_loss = pnl

            if not t["is_win"]:
                if mart_level < mc["max_level"]:
                    mart_level += 1
                    contracts = mc["mult"] ** mart_level
            else:
                contracts = 1.0
                mart_level = 0

        equity_arr = np.array(equity_curve)
        peak = np.maximum.accumulate(equity_arr)
        dd = peak - equity_arr
        max_dd = np.max(dd) if len(dd) > 0 else 0
        pnl_dd = total_pnl / max_dd if max_dd > 0 else np.inf

        mart_rows_daily.append({
            "main_config": sys_row["main_config"],
            "trigger": trigger,
            "switch_rule": sw_rule,
            "return_rule": ret_rule,
            "sub_config": sys_row["sub_config"],
            "martingale": mc["label"],
            "total_trades": len(trades_with_bars),
            "overall_pnl": round(total_pnl, 2),
            "max_drawdown": round(max_dd, 2),
            "pnl_dd_ratio": round(pnl_dd, 3) if not np.isinf(pnl_dd) else 999.0,
            "largest_single_loss": round(largest_single_loss, 2),
            "base_pnl": sys_row["overall_pnl"],
            "base_max_dd": sys_row["max_drawdown"],
            "daily_reset": True,
        })

# Combine both martingale runs (with and without daily reset for comparison)
# Use only the daily-reset version as specified
mart_final = pd.DataFrame(mart_rows_daily)
mart_final.to_csv(os.path.join(OUTPUT_DIR, "switching-martingale.csv"), index=False)
print(f"Wrote switching-martingale.csv ({len(mart_final)} rows)")


# ===================================================================
# SUMMARY
# ===================================================================
lines = []
lines.append("SWITCHING RULE ANALYSIS - PHASE 2 (daily-reset redo)")
lines.append("=" * 90)
lines.append(f"Data: {n} bars, 73 trading days")
lines.append(f"Main configs tested: {[combo_key(i, o) for i, o in MAIN_COMBOS]}")
lines.append(f"Triggers tested: {TRIGGERS_TO_TEST}")
lines.append(f"Total switching systems tested: {len(results_df)}")
lines.append("")
lines.append("CORRECTIONS FROM PREVIOUS PHASE 2:")
lines.append("  - Rolling win rate: resets daily, needs 10 trades before activating each day")
lines.append("  - Consecutive stops: per-side counters, reset daily")
lines.append("  - When rolling_win_rate undefined (not enough trades that day), no switch triggered")
lines.append("  - R2 return rule: sub WR > 50% with min 3 trades (was 5)")
lines.append("  - R3 variants: R3_N3, R3_N5, R3_N10")
lines.append("  - Martingale: counters reset daily AND on config switch")
lines.append("")

lines.append("STATIC BASELINES")
lines.append("-" * 90)
for ck, sr in sorted(static_results.items(), key=lambda x: -x[1]["ev"]):
    lines.append(f"  {ck}: {sr['n_trades']} trades, EV=${sr['ev']:.2f}, "
                 f"PnL=${sr['total_pnl']:.2f}, WR={sr['win_rate']:.1f}%, MaxDD=${sr['max_dd']:.2f}")
lines.append(f"  Best static: {best_static_key} (EV=${best_static_ev:.2f})")
lines.append("")

lines.append("SWITCH-TO CONFIGS (from Phase 1 conditional EV)")
lines.append("-" * 90)
for (mck, trigger), info in switch_lookup.items():
    for bucket, (sub_ck, sub_ev) in info['switch_to'].items():
        main_ev_bucket = info['main_ev_by_bucket'].get(bucket, 0)
        lines.append(f"  {mck} x {trigger}: worst bucket='{bucket}' "
                     f"(main EV=${main_ev_bucket:.2f}) -> switch to {sub_ck} (EV=${sub_ev:.2f})")
lines.append("")

lines.append("THRESHOLD VALUES")
lines.append("=" * 90)
lines.append("")
lines.append("Tercile boundaries (P33 / P67) and P25 / P75 per trigger per main config:")
lines.append("-" * 90)

for ck in [combo_key(i, o) for i, o in MAIN_COMBOS]:
    lines.append(f"\n  {ck}:")
    for trigger in ["bar_range", "consecutive_stops", "rolling_win_rate"]:
        b = all_boundaries.get(ck, {}).get(trigger)
        if b:
            lines.append(f"    {trigger}:")
            lines.append(f"      P25={b['p25']:.6f}  P33={b['tercile_low']:.6f}  "
                         f"P67={b['tercile_high']:.6f}  P75={b['p75']:.6f}")

lines.append("")
lines.append("Speed-range regime: categorical (rolling 200-bar median boundaries)")
lines.append("  No fixed thresholds - per-bar rolling values.")
lines.append("")

lines.append("TOP 10 SWITCHING SYSTEMS (by overall EV)")
lines.append("-" * 90)
top10 = results_df.head(10)
for _, row in top10.iterrows():
    lines.append(f"  {row['main_config']} x {row['trigger']} ({row['switch_rule']}/{row['return_rule']}):")
    lines.append(f"    Sub config: {row['sub_config']}")
    lines.append(f"    Trades: {row['total_trades']} total ({row['main_trades']} main, {row['sub_trades']} sub = {row['sub_pct']}%)")
    lines.append(f"    Overall EV: ${row['overall_ev']:.2f} (main=${row['main_mode_ev']:.2f}, sub=${row['sub_mode_ev']:.2f})")
    lines.append(f"    PnL: ${row['overall_pnl']:.2f}, MaxDD: ${row['max_drawdown']:.2f}, PnL/DD: {row['pnl_dd_ratio']:.3f}")
    lines.append(f"    WR: {row['win_rate_pct']:.1f}%")
    lines.append(f"    vs static main: {'+' if row['ev_vs_static_main'] >= 0 else ''}{row['ev_vs_static_main']:.2f}, "
                 f"vs best static: {'+' if row['ev_vs_best_static'] >= 0 else ''}{row['ev_vs_best_static']:.2f}")
    lines.append("")

lines.append("BOTTOM 5 SWITCHING SYSTEMS (worst by overall EV)")
lines.append("-" * 90)
bottom5 = results_df.tail(5)
for _, row in bottom5.iterrows():
    lines.append(f"  {row['main_config']} x {row['trigger']} ({row['switch_rule']}/{row['return_rule']}): "
                 f"EV=${row['overall_ev']:.2f}, vs static main: {'+' if row['ev_vs_static_main'] >= 0 else ''}{row['ev_vs_static_main']:.2f}")
lines.append("")

lines.append("SUMMARY BY TRIGGER (average EV lift vs static main)")
lines.append("-" * 90)
for trigger in TRIGGERS_TO_TEST:
    sub = results_df[results_df["trigger"] == trigger]
    if len(sub) == 0:
        continue
    avg_lift = sub["ev_vs_static_main"].mean()
    best_lift = sub["ev_vs_static_main"].max()
    worst_lift = sub["ev_vs_static_main"].min()
    n_positive = (sub["ev_vs_static_main"] > 0).sum()
    lines.append(f"  {trigger}: avg lift=${avg_lift:.2f}, best=${best_lift:.2f}, worst=${worst_lift:.2f}, "
                 f"{n_positive}/{len(sub)} beat static main")
lines.append("")

lines.append("SUMMARY BY RETURN RULE (average EV lift vs static main)")
lines.append("-" * 90)
for ret in sorted(results_df["return_rule"].unique()):
    sub_df = results_df[results_df["return_rule"] == ret]
    avg_lift = sub_df["ev_vs_static_main"].mean()
    lines.append(f"  {ret}: avg lift=${avg_lift:.2f} ({len(sub_df)} systems)")
lines.append("")

# Count how many beat static main and best static
n_beat_main = (results_df["ev_vs_static_main"] > 0).sum()
n_beat_best = (results_df["ev_vs_best_static"] > 0).sum()
lines.append("VERDICT")
lines.append("-" * 90)
lines.append(f"  {n_beat_main}/{len(results_df)} switching systems beat their static main config EV")
lines.append(f"  {n_beat_best}/{len(results_df)} switching systems beat the best single static config EV (${best_static_ev:.2f})")
lines.append("")

lines.append("MARTINGALE OVERLAY (top 3 switching systems, daily+config-switch reset)")
lines.append("-" * 90)
if len(mart_final) > 0:
    for _, mrow in mart_final.iterrows():
        lines.append(f"  {mrow['main_config']} x {mrow['trigger']} ({mrow['switch_rule']}/{mrow['return_rule']}) + {mrow['martingale']}:")
        lines.append(f"    PnL: ${mrow['overall_pnl']:.2f} (base: ${mrow['base_pnl']:.2f})")
        lines.append(f"    MaxDD: ${mrow['max_drawdown']:.2f} (base: ${mrow['base_max_dd']:.2f})")
        lines.append(f"    PnL/DD: {mrow['pnl_dd_ratio']:.3f}")
        lines.append(f"    Largest single loss: ${mrow['largest_single_loss']:.2f}")
        lines.append("")
else:
    lines.append("  No martingale results.")

summary_text = "\n".join(lines)
with open(os.path.join(OUTPUT_DIR, "switching-summary.txt"), "w") as f:
    f.write(summary_text)
print(f"\nWrote switching-summary.txt")

print("\n" + summary_text)
print("\nDone.")
