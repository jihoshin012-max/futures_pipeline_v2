"""
Phase 1: Trigger predictiveness analysis for range-fade rotation strategy.

For each bar, computes 9 trigger values and records them at trade entry.
Then computes correlations, conditional EV by tercile/bucket, and trigger rankings.

Simulation logic matches multiplier-grid-analysis.py:
- ddof=0, same-bar re-entry, reversals, RTH filter 09:30-15:45
- Entry at bar close, lookback=50
- NQ tick size=0.25, tick value=$5.00

Triggers:
  1. stddev_ratio (50-bar stdDev / 200-bar stdDev)
  2. bar_speed (rolling 10-bar avg duration in seconds)
  3. bar_range (rolling 10-bar avg High-Low)
  4. consecutive_stops (per-combo, trade-level — resets daily)
  5. price_position_vs_midline (fraction of last 20 bars closing above mean)
  6. band_touch_rate (last 20 bars touching inner bands, combo-specific)
  7. rolling_win_rate (last 10 trades, combo-specific — resets daily)
  8. range_speed (rolling 10-bar avg range / rolling 10-bar avg duration)
  9. speed_range_regime (categorical: fast_wide, fast_narrow, slow_wide, slow_narrow)

Changes from previous run:
- Trigger #4 (consecutive_stops): resets at start of each new trading day
- Trigger #7 (rolling_win_rate): resets at start of each new trading day
  (rolling window of last 10 trades is per-day; undefined until 10 trades that day)
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

COMBOS = [
    (1.50, 2.00),
    (0.75, 2.50),
    (1.00, 2.00),
    (0.50, 2.00),
    (1.25, 2.00),
]

DATA_PATH = r"c:\Projects\futures_pipeline\data\NQ-250tick-calibration.csv"
OUTPUT_DIR = r"c:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection"

# Triggers 1-8 are numeric (tercile bucketing). Trigger 9 is categorical.
NUMERIC_TRIGGER_NAMES = [
    "stddev_ratio",
    "bar_speed",
    "bar_range",
    "consecutive_stops",
    "price_position_vs_midline",
    "band_touch_rate",
    "rolling_win_rate",
    "range_speed",
]

ALL_TRIGGER_NAMES = NUMERIC_TRIGGER_NAMES + ["speed_range_regime"]

# --- Load data ---
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

# --- Parse timestamps for bar speed calculation ---
print("Parsing timestamps for bar speed...")
timestamps = np.full(n, np.nan)
for i in range(n):
    date_str = str(dates[i]).strip()
    time_str = str(times_raw[i]).strip()
    # Format: "9/21/2025" + "18:00:00.000000"
    try:
        # Handle microseconds by truncating to seconds
        time_parts = time_str.split(".")
        dt_str = f"{date_str} {time_parts[0]}"
        dt = datetime.strptime(dt_str, "%m/%d/%Y %H:%M:%S")
        timestamps[i] = dt.timestamp()
    except Exception:
        pass

# --- Precompute rolling stats ---
print("Computing rolling statistics...")

# Rolling mean/std lookback=50 (ddof=0)
roll_mean_50 = np.full(n, np.nan)
roll_std_50 = np.full(n, np.nan)

for i in range(LOOKBACK - 1, n):
    window = close[i - LOOKBACK + 1 : i + 1]
    roll_mean_50[i] = np.mean(window)
    roll_std_50[i] = np.std(window, ddof=0)

# Rolling std lookback=200 (ddof=0) for stddev ratio
LOOKBACK_200 = 200
roll_std_200 = np.full(n, np.nan)
for i in range(LOOKBACK_200 - 1, n):
    window = close[i - LOOKBACK_200 + 1 : i + 1]
    roll_std_200[i] = np.std(window, ddof=0)

# Trigger 1: StdDev ratio (50-bar / 200-bar)
stddev_ratio = np.full(n, np.nan)
for i in range(n):
    if not np.isnan(roll_std_50[i]) and not np.isnan(roll_std_200[i]) and roll_std_200[i] > 0:
        stddev_ratio[i] = roll_std_50[i] / roll_std_200[i]

# Trigger 2: Bar speed — rolling 10-bar average of bar duration (seconds)
bar_duration = np.full(n, np.nan)
for i in range(1, n):
    if not np.isnan(timestamps[i]) and not np.isnan(timestamps[i - 1]):
        dur = timestamps[i] - timestamps[i - 1]
        if dur > 0:
            bar_duration[i] = dur

bar_speed = np.full(n, np.nan)
for i in range(10, n):
    window = bar_duration[i - 9 : i + 1]
    valid = window[~np.isnan(window)]
    if len(valid) >= 5:
        bar_speed[i] = np.mean(valid)

# Trigger 3: Bar range — rolling 10-bar average of (High - Low)
bar_range_raw = high - low
bar_range = np.full(n, np.nan)
for i in range(9, n):
    bar_range[i] = np.mean(bar_range_raw[i - 9 : i + 1])

# Trigger 5: Price position vs midline — fraction of last 20 bars where Close > rolling 50-bar mean
price_position = np.full(n, np.nan)
for i in range(19, n):
    if not np.isnan(roll_mean_50[i]):
        count_above = 0
        valid_count = 0
        for j in range(i - 19, i + 1):
            if not np.isnan(roll_mean_50[j]):
                valid_count += 1
                if close[j] > roll_mean_50[j]:
                    count_above += 1
        if valid_count > 0:
            price_position[i] = count_above / valid_count

# Trigger 8: Range speed — rolling 10-bar avg range / rolling 10-bar avg duration
# This is bar_range / bar_speed (points per second)
range_speed = np.full(n, np.nan)
for i in range(10, n):
    if not np.isnan(bar_range[i]) and not np.isnan(bar_speed[i]) and bar_speed[i] > 0:
        range_speed[i] = bar_range[i] / bar_speed[i]

# Trigger 9: Speed-range regime — categorical based on rolling 200-bar medians
# "fast" = bar_speed < median (shorter bars = faster), "slow" = bar_speed >= median
# "wide" = bar_range > median, "narrow" = bar_range <= median
print("Computing rolling 200-bar medians for speed-range regime...")
speed_range_regime = np.full(n, None, dtype=object)
regime_median_speed = np.full(n, np.nan)
regime_median_range = np.full(n, np.nan)
for i in range(209, n):  # need 200 bars of bar_speed/bar_range, which start at index 10/9
    # Collect valid speed/range values in the last 200 bars
    speed_window = bar_speed[i - 199 : i + 1]
    range_window = bar_range[i - 199 : i + 1]
    valid_speed = speed_window[~np.isnan(speed_window)]
    valid_range = range_window[~np.isnan(range_window)]
    if len(valid_speed) >= 50 and len(valid_range) >= 50:
        median_speed = np.median(valid_speed)
        median_range = np.median(valid_range)
        regime_median_speed[i] = median_speed
        regime_median_range[i] = median_range
        cur_speed = bar_speed[i]
        cur_range = bar_range[i]
        if np.isnan(cur_speed) or np.isnan(cur_range):
            continue
        if cur_speed < median_speed and cur_range > median_range:
            speed_range_regime[i] = "fast_wide"
        elif cur_speed < median_speed and cur_range <= median_range:
            speed_range_regime[i] = "fast_narrow"
        elif cur_speed >= median_speed and cur_range > median_range:
            speed_range_regime[i] = "slow_wide"
        else:
            speed_range_regime[i] = "slow_narrow"

print("Bar-level triggers computed.")


def simulate_with_triggers(inner_mult, outer_mult):
    """
    Run simulation for one combo. Returns list of trade dicts with trigger values.
    Triggers 4 (consecutive_stops), 6 (band_touch_rate), 7 (rolling_win_rate) are
    combo-specific and computed inline.

    Daily resets:
    - consecutive_stops resets to 0 at the start of each new trading day
    - rolling_win_rate resets (clears trade history) at the start of each new trading day;
      undefined (NaN) until 10 trades have occurred that day
    """
    # Compute bands
    inner_top = roll_mean_50 + inner_mult * roll_std_50
    inner_bot = roll_mean_50 - inner_mult * roll_std_50
    outer_top = roll_mean_50 + outer_mult * roll_std_50
    outer_bot = roll_mean_50 - outer_mult * roll_std_50

    # Precompute band touch rate (trigger 6) — combo-specific
    # Count of last 20 bars where low <= innerBot OR high >= innerTop, / 20
    band_touch = np.full(n, np.nan)
    for i in range(19, n):
        count = 0
        for j in range(i - 19, i + 1):
            if not np.isnan(inner_bot[j]) and not np.isnan(inner_top[j]):
                if low[j] <= inner_bot[j] or high[j] >= inner_top[j]:
                    count += 1
        band_touch[i] = count / 20.0

    trades = []
    consecutive_stop_count = 0  # running count for trigger 4 — resets daily
    recent_results_today = []  # for rolling win rate (trigger 7) — resets daily
    current_trade_date = None  # track current trading day for resets

    # Position state
    in_position = False
    direction = None
    entry_price = 0.0
    target_price = 0.0
    stop_price = 0.0
    entry_bar = 0

    def check_buy_signal(bar_idx):
        if np.isnan(roll_mean_50[bar_idx]):
            return False
        return low[bar_idx] <= inner_bot[bar_idx] and low[bar_idx] > outer_bot[bar_idx]

    def check_sell_signal(bar_idx):
        if np.isnan(roll_mean_50[bar_idx]):
            return False
        return high[bar_idx] >= inner_top[bar_idx] and high[bar_idx] < outer_top[bar_idx]

    def enter_position(bar_idx, dir_):
        ep = close[bar_idx]
        t_offset = inner_top[bar_idx] - inner_bot[bar_idx]
        if dir_ == "long":
            s_offset = inner_bot[bar_idx] - outer_bot[bar_idx]
            tp = ep + t_offset
            sp = ep - s_offset
        else:
            s_offset = outer_top[bar_idx] - inner_top[bar_idx]
            tp = ep - t_offset
            sp = ep + s_offset
        return ep, tp, sp, t_offset, s_offset, bar_idx

    def check_daily_reset(bar_idx):
        """Check if we've entered a new trading day and reset if so."""
        nonlocal current_trade_date, consecutive_stop_count, recent_results_today
        this_date = bar_dates[bar_idx]
        if current_trade_date is None or this_date != current_trade_date:
            current_trade_date = this_date
            consecutive_stop_count = 0
            recent_results_today = []

    def get_trigger_values(bar_idx):
        """Get trigger values at the bar of entry."""
        # Rolling win rate: last 10 trades THIS DAY only
        if len(recent_results_today) >= 10:
            rwr = sum(recent_results_today[-10:]) / 10.0
        else:
            rwr = np.nan

        return {
            "stddev_ratio": stddev_ratio[bar_idx] if not np.isnan(stddev_ratio[bar_idx]) else np.nan,
            "bar_speed": bar_speed[bar_idx] if not np.isnan(bar_speed[bar_idx]) else np.nan,
            "bar_range": bar_range[bar_idx] if not np.isnan(bar_range[bar_idx]) else np.nan,
            "consecutive_stops": consecutive_stop_count,
            "price_position_vs_midline": price_position[bar_idx] if not np.isnan(price_position[bar_idx]) else np.nan,
            "band_touch_rate": band_touch[bar_idx] if not np.isnan(band_touch[bar_idx]) else np.nan,
            "rolling_win_rate": rwr,
            "range_speed": range_speed[bar_idx] if not np.isnan(range_speed[bar_idx]) else np.nan,
            "speed_range_regime": speed_range_regime[bar_idx] if speed_range_regime[bar_idx] is not None else np.nan,
        }

    def record_trade(pnl_pts, exit_type, entry_b, exit_b, ep, dir_, triggers):
        nonlocal consecutive_stop_count
        pnl_dollar = pnl_pts * POINT_VALUE
        is_win = pnl_pts > 0

        trades.append({
            "entry_bar": entry_b,
            "exit_bar": exit_b,
            "direction": dir_,
            "entry_price": ep,
            "exit_type": exit_type,
            "pnl_points": pnl_pts,
            "pnl_dollar": pnl_dollar,
            "is_win": is_win,
            **triggers,
        })

        # Update consecutive stops (resets are handled by check_daily_reset)
        if exit_type == "stop":
            consecutive_stop_count += 1
        else:
            consecutive_stop_count = 0

        # Update rolling win rate tracker (daily list)
        recent_results_today.append(1 if is_win else 0)

    i = LOOKBACK
    while i < n:
        if not in_position:
            if np.isnan(roll_mean_50[i]):
                i += 1
                continue
            if not is_rth(i):
                i += 1
                continue

            # Check for daily reset before any trade logic
            check_daily_reset(i)

            buy_sig = check_buy_signal(i)
            sell_sig = check_sell_signal(i)

            if buy_sig:
                direction = "long"
            elif sell_sig:
                direction = "short"
            else:
                i += 1
                continue

            triggers_at_entry = get_trigger_values(i)
            (entry_price, target_price, stop_price,
             entry_target_offset, entry_stop_offset, entry_bar) = enter_position(i, direction)
            in_position = True
            i += 1
            continue

        if i >= n:
            break

        # Check for daily reset on exit bars too (so consecutive_stops resets properly)
        check_daily_reset(i)

        if direction == "long":
            target_hit = high[i] >= target_price
            stop_hit = low[i] <= stop_price
            buy_reversal = False
            sell_reversal = check_sell_signal(i) and is_rth(i)
        else:
            target_hit = low[i] <= target_price
            stop_hit = high[i] >= stop_price
            buy_reversal = check_buy_signal(i) and is_rth(i)
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

            record_trade(pnl, exit_type, entry_bar, i, entry_price, direction, triggers_at_entry)
            in_position = False
            direction = None
            continue

        elif target_hit:
            pnl = entry_target_offset
            record_trade(pnl, "target", entry_bar, i, entry_price, direction, triggers_at_entry)
            in_position = False
            direction = None
            continue

        elif stop_hit:
            pnl = -entry_stop_offset
            record_trade(pnl, "stop", entry_bar, i, entry_price, direction, triggers_at_entry)
            in_position = False
            direction = None
            continue

        elif reversal_signal:
            if direction == "long":
                pnl = close[i] - entry_price
                new_direction = "short"
            else:
                pnl = entry_price - close[i]
                new_direction = "long"

            record_trade(pnl, "reversal", entry_bar, i, entry_price, direction, triggers_at_entry)

            # Get triggers for the new entry
            triggers_at_entry = get_trigger_values(i)
            (entry_price, target_price, stop_price,
             entry_target_offset, entry_stop_offset, entry_bar) = enter_position(i, new_direction)
            direction = new_direction
            in_position = True
            i += 1
            continue

        i += 1

    return trades


# --- Run simulations ---
all_combo_trades = {}

for inner_mult, outer_mult in COMBOS:
    combo_key = f"i{inner_mult:.2f}_o{outer_mult:.2f}"
    print(f"Simulating {combo_key}...")
    trades = simulate_with_triggers(inner_mult, outer_mult)
    all_combo_trades[combo_key] = trades
    print(f"  {len(trades)} trades")

# ===================================================================
# A. Correlation matrix: trigger value vs trade PnL (numeric triggers only)
# ===================================================================
print("\nComputing correlation matrix...")

corr_rows = []
for combo_key, trades in all_combo_trades.items():
    if len(trades) < 30:
        continue
    tdf = pd.DataFrame(trades)
    for trigger in NUMERIC_TRIGGER_NAMES:
        valid = tdf[[trigger, "pnl_dollar"]].dropna()
        if len(valid) >= 30:
            corr = valid[trigger].corr(valid["pnl_dollar"])
        else:
            corr = np.nan
        corr_rows.append({
            "combo": combo_key,
            "trigger": trigger,
            "correlation": round(corr, 6) if not np.isnan(corr) else np.nan,
            "n_trades": len(valid),
        })

corr_df = pd.DataFrame(corr_rows)
corr_df.to_csv(os.path.join(OUTPUT_DIR, "trigger-correlations.csv"), index=False)
print(f"Wrote trigger-correlations.csv ({len(corr_df)} rows)")

# ===================================================================
# B. Conditional EV by trigger bucket
#    Triggers 1-8: terciles (low/med/high)
#    Trigger 9: 4 categorical buckets
# ===================================================================
print("Computing conditional EV by terciles/buckets...")

ev_rows = []
# Store tercile boundaries for summary
tercile_boundaries = {}

for combo_key, trades in all_combo_trades.items():
    if len(trades) < 30:
        continue
    tdf = pd.DataFrame(trades)

    # --- Numeric triggers: tercile bucketing ---
    for trigger in NUMERIC_TRIGGER_NAMES:
        valid = tdf[[trigger, "pnl_dollar", "is_win"]].dropna()
        if len(valid) < 30:
            continue

        # Compute tercile boundaries
        t_low = valid[trigger].quantile(1 / 3)
        t_high = valid[trigger].quantile(2 / 3)

        # Store boundaries
        if trigger not in tercile_boundaries:
            tercile_boundaries[trigger] = {}
        tercile_boundaries[trigger][combo_key] = (t_low, t_high)

        buckets = {
            "low": valid[valid[trigger] <= t_low],
            "medium": valid[(valid[trigger] > t_low) & (valid[trigger] <= t_high)],
            "high": valid[valid[trigger] > t_high],
        }

        for bucket_name, bucket_df in buckets.items():
            if len(bucket_df) == 0:
                continue
            ev_rows.append({
                "combo": combo_key,
                "trigger": trigger,
                "bucket": bucket_name,
                "trade_count": len(bucket_df),
                "win_rate": round(bucket_df["is_win"].mean() * 100, 2),
                "ev_per_trade_dollar": round(bucket_df["pnl_dollar"].mean(), 2),
                "total_pnl_dollar": round(bucket_df["pnl_dollar"].sum(), 2),
                "tercile_low_bound": round(t_low, 4),
                "tercile_high_bound": round(t_high, 4),
            })

    # --- Trigger 9: categorical bucketing ---
    trigger = "speed_range_regime"
    # Filter to rows where regime is not NaN (string column)
    valid = tdf[["speed_range_regime", "pnl_dollar", "is_win"]].copy()
    valid = valid[valid["speed_range_regime"].apply(lambda x: isinstance(x, str))]
    if len(valid) >= 30:
        for regime in ["fast_wide", "fast_narrow", "slow_wide", "slow_narrow"]:
            bucket_df = valid[valid["speed_range_regime"] == regime]
            if len(bucket_df) == 0:
                continue
            ev_rows.append({
                "combo": combo_key,
                "trigger": trigger,
                "bucket": regime,
                "trade_count": len(bucket_df),
                "win_rate": round(bucket_df["is_win"].mean() * 100, 2),
                "ev_per_trade_dollar": round(bucket_df["pnl_dollar"].mean(), 2),
                "total_pnl_dollar": round(bucket_df["pnl_dollar"].sum(), 2),
                "tercile_low_bound": np.nan,
                "tercile_high_bound": np.nan,
            })

ev_df = pd.DataFrame(ev_rows)
ev_df.to_csv(os.path.join(OUTPUT_DIR, "trigger-conditional-ev.csv"), index=False)
print(f"Wrote trigger-conditional-ev.csv ({len(ev_df)} rows)")

# ===================================================================
# C. Trigger predictiveness ranking
#    For numeric triggers: avg tercile EV spread
#    For categorical trigger 9: max bucket EV - min bucket EV (same logic)
# ===================================================================
print("Computing trigger ranking...")

ranking_rows = []
for trigger in ALL_TRIGGER_NAMES:
    spreads = []
    for combo_key in all_combo_trades:
        sub = ev_df[(ev_df["combo"] == combo_key) & (ev_df["trigger"] == trigger)]
        if len(sub) < 2:
            continue
        best_ev = sub["ev_per_trade_dollar"].max()
        worst_ev = sub["ev_per_trade_dollar"].min()
        spreads.append(best_ev - worst_ev)

    if len(spreads) > 0:
        avg_spread = np.mean(spreads)
        max_spread = np.max(spreads)
        min_spread = np.min(spreads)
    else:
        avg_spread = max_spread = min_spread = np.nan

    # Average absolute correlation across combos (numeric only)
    sub_corr = corr_df[corr_df["trigger"] == trigger]
    avg_abs_corr = sub_corr["correlation"].abs().mean() if len(sub_corr) > 0 else np.nan

    ranking_rows.append({
        "trigger": trigger,
        "avg_tercile_ev_spread": round(avg_spread, 2) if not np.isnan(avg_spread) else np.nan,
        "max_tercile_ev_spread": round(max_spread, 2) if not np.isnan(max_spread) else np.nan,
        "min_tercile_ev_spread": round(min_spread, 2) if not np.isnan(min_spread) else np.nan,
        "avg_abs_correlation": round(avg_abs_corr, 6) if not np.isnan(avg_abs_corr) else np.nan,
        "n_combos_measured": len(spreads),
    })

ranking_df = pd.DataFrame(ranking_rows)
ranking_df = ranking_df.sort_values("avg_tercile_ev_spread", ascending=False)
ranking_df.to_csv(os.path.join(OUTPUT_DIR, "trigger-ranking.csv"), index=False)
print(f"Wrote trigger-ranking.csv")

# ===================================================================
# Summary
# ===================================================================
print("\nGenerating summary...")

lines = []
lines.append("TRIGGER PREDICTIVENESS ANALYSIS — PHASE 1 (daily-reset)")
lines.append("=" * 80)
lines.append(f"Data: {n} bars")
lines.append(f"Lookback: {LOOKBACK}, StdDev ddof=0, RTH 09:30-15:45")
lines.append(f"Combos tested: {len(COMBOS)}")
lines.append(f"Triggers: {len(ALL_TRIGGER_NAMES)} (8 numeric + 1 categorical)")
lines.append("")
lines.append("DAILY RESET CHANGES")
lines.append("-" * 80)
lines.append("  Trigger #4 (consecutive_stops): resets to 0 at start of each new trading day")
lines.append("  Trigger #7 (rolling_win_rate): trade history clears at start of each new day;")
lines.append("    rolling win rate is NaN until 10 trades have occurred that day")
lines.append("")

lines.append("COMBOS AND TRADE COUNTS")
lines.append("-" * 80)
for combo_key, trades in all_combo_trades.items():
    wins = sum(1 for t in trades if t["is_win"])
    total = len(trades)
    ev = np.mean([t["pnl_dollar"] for t in trades]) if total > 0 else 0
    wr = wins / total * 100 if total > 0 else 0
    lines.append(f"  {combo_key}: {total} trades, WR={wr:.1f}%, EV=${ev:.2f}")

lines.append("")
lines.append("A. CORRELATION MATRIX (trigger vs trade PnL, numeric triggers only)")
lines.append("-" * 80)

# Pivot for readability
if len(corr_df) > 0:
    pivot = corr_df.pivot(index="trigger", columns="combo", values="correlation")
    lines.append(pivot.to_string())
else:
    lines.append("  No data.")

lines.append("")
lines.append("B. CONDITIONAL EV BY TRIGGER BUCKET")
lines.append("-" * 80)

for trigger in ALL_TRIGGER_NAMES:
    lines.append(f"\n  Trigger: {trigger}")
    sub = ev_df[ev_df["trigger"] == trigger]
    if len(sub) == 0:
        lines.append("    No data.")
        continue

    for combo_key in all_combo_trades:
        combo_sub = sub[sub["combo"] == combo_key]
        if len(combo_sub) == 0:
            continue
        parts = []
        for _, row in combo_sub.iterrows():
            parts.append(f"{row['bucket']}={row['trade_count']}t WR={row['win_rate']}% EV=${row['ev_per_trade_dollar']:.2f}")
        lines.append(f"    {combo_key}: {' | '.join(parts)}")

lines.append("")
lines.append("C. TRIGGER PREDICTIVENESS RANKING (by avg bucket EV spread)")
lines.append("-" * 80)
lines.append(f"  {'Trigger':<30s} {'Avg Spread':>12s} {'Max Spread':>12s} {'Avg |corr|':>12s}")

for _, row in ranking_df.iterrows():
    avg_sp = f"${row['avg_tercile_ev_spread']:.2f}" if not pd.isna(row['avg_tercile_ev_spread']) else "N/A"
    max_sp = f"${row['max_tercile_ev_spread']:.2f}" if not pd.isna(row['max_tercile_ev_spread']) else "N/A"
    avg_c = f"{row['avg_abs_correlation']:.4f}" if not pd.isna(row['avg_abs_correlation']) else "N/A"
    lines.append(f"  {row['trigger']:<30s} {avg_sp:>12s} {max_sp:>12s} {avg_c:>12s}")

lines.append("")
lines.append("=" * 80)
lines.append("D. THRESHOLD VALUES (for holdout application)")
lines.append("=" * 80)
lines.append("")
lines.append("Tercile boundaries (33rd / 67th percentile) per trigger per combo:")
lines.append("-" * 80)

for trigger in NUMERIC_TRIGGER_NAMES:
    lines.append(f"\n  {trigger}:")
    if trigger in tercile_boundaries:
        for combo_key in sorted(tercile_boundaries[trigger].keys()):
            t_low, t_high = tercile_boundaries[trigger][combo_key]
            lines.append(f"    {combo_key}: P33={t_low:.6f}  P67={t_high:.6f}")
    else:
        lines.append("    No data (insufficient trades).")

lines.append("")
lines.append("Speed-range regime median boundaries (rolling 200-bar):")
lines.append("-" * 80)
lines.append("  (These are per-bar rolling values, not fixed thresholds.)")
lines.append("  Reporting overall median of the rolling medians across all bars with valid regimes:")

# Compute summary stats of the rolling medians for reporting
valid_med_speed = regime_median_speed[~np.isnan(regime_median_speed)]
valid_med_range = regime_median_range[~np.isnan(regime_median_range)]
if len(valid_med_speed) > 0:
    lines.append(f"  Speed median — mean: {np.mean(valid_med_speed):.2f}s, "
                 f"median: {np.median(valid_med_speed):.2f}s, "
                 f"min: {np.min(valid_med_speed):.2f}s, "
                 f"max: {np.max(valid_med_speed):.2f}s")
if len(valid_med_range) > 0:
    lines.append(f"  Range median — mean: {np.mean(valid_med_range):.4f}, "
                 f"median: {np.median(valid_med_range):.4f}, "
                 f"min: {np.min(valid_med_range):.4f}, "
                 f"max: {np.max(valid_med_range):.4f}")

lines.append("")
lines.append("Larger EV spread = trigger state more predictive of performance shift.")
lines.append("Higher |correlation| = stronger linear relationship with trade PnL.")
lines.append("For speed_range_regime, spread is max - min across the 4 categorical buckets.")

summary_text = "\n".join(lines)

with open(os.path.join(OUTPUT_DIR, "trigger-analysis-summary.txt"), "w") as f:
    f.write(summary_text)
print("Wrote trigger-analysis-summary.txt")

print("\n" + summary_text)
print("\nDone.")
