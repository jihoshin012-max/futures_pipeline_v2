"""
Holdout validation of top switching systems and static baselines.

Uses CALIBRATION tercile boundaries (not holdout-derived) to test true OOS performance.

Systems tested:
  A1: Switching i1.50/o2.00 -> i0.75/o2.50, trigger=rolling_win_rate<=0.10,
      return rule R3_N5 (return after 5 sub trades)
  A2: Same switching, return rule R1 (return when trigger exits bad zone)
  B:  A2 + martingale m1.5 max3 (daily + config-switch reset)
  C1: Static i0.75/o2.50
  C2: Static i1.50/o2.00
  C3: Static i1.00/o2.00

Simulation rules (same as calibration):
  - RTH 09:30-15:45, ddof=0, same-bar re-entry, reversals, entry at bar close, lookback=50
  - Rolling win rate resets daily, needs 10 trades before activating
  - Consecutive stops reset daily (per-side) — used for martingale
  - NQ tick size=0.25, tick value=$5.00
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

# CALIBRATION tercile boundary for rolling_win_rate on i1.50_o2.00
# From switching-summary.txt: P33=0.100000 for rolling_win_rate
# Worst bucket = "low", so switch when rolling_win_rate <= 0.10
CALIB_RWR_P33 = 0.10

DATA_PATH = r"c:\Projects\futures_pipeline\data\NQ-250tick-holdout.csv"
OUTPUT_DIR = r"c:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection"

# Configs
MAIN_INNER, MAIN_OUTER = 1.50, 2.00
SUB_INNER, SUB_OUTER = 0.75, 2.50

STATIC_CONFIGS = [
    (0.75, 2.50, "C1_static_0.75_2.50"),
    (1.50, 2.00, "C2_static_1.50_2.00"),
    (1.00, 2.00, "C3_static_1.00_2.00"),
]

# --- Load data ---
print("Loading holdout data...")
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

# --- Parse dates for daily reset ---
bar_dates = np.array([str(d).strip() for d in dates])

# --- Precompute rolling stats ---
print("Computing rolling statistics...")
roll_mean = np.full(n, np.nan)
roll_std = np.full(n, np.nan)

for i in range(LOOKBACK - 1, n):
    window = close[i - LOOKBACK + 1 : i + 1]
    roll_mean[i] = np.mean(window)
    roll_std[i] = np.std(window, ddof=0)

# --- Precompute bands for all needed configs ---
def make_bands(inner_mult, outer_mult):
    return {
        "inner_top": roll_mean + inner_mult * roll_std,
        "inner_bot": roll_mean - inner_mult * roll_std,
        "outer_top": roll_mean + outer_mult * roll_std,
        "outer_bot": roll_mean - outer_mult * roll_std,
    }

all_bands = {}
for im, om, _ in STATIC_CONFIGS:
    key = f"{im}_{om}"
    if key not in all_bands:
        all_bands[key] = make_bands(im, om)

# Ensure main and sub are also present
main_key = f"{MAIN_INNER}_{MAIN_OUTER}"
sub_key = f"{SUB_INNER}_{SUB_OUTER}"
if main_key not in all_bands:
    all_bands[main_key] = make_bands(MAIN_INNER, MAIN_OUTER)
if sub_key not in all_bands:
    all_bands[sub_key] = make_bands(SUB_INNER, SUB_OUTER)

# --- Compute stdDev terciles on HOLDOUT data (for regime breakdown only) ---
# Use RTH bars with valid roll_std
rth_std_vals = []
for i in range(LOOKBACK - 1, n):
    if is_rth(i) and not np.isnan(roll_std[i]):
        rth_std_vals.append(roll_std[i])
rth_std_vals = np.array(rth_std_vals)
std_p33 = np.percentile(rth_std_vals, 100 / 3)
std_p67 = np.percentile(rth_std_vals, 200 / 3)
print(f"Holdout stdDev terciles: P33={std_p33:.4f}, P67={std_p67:.4f}")

def get_regime(bar_idx):
    """Classify bar into stdDev regime using HOLDOUT terciles."""
    s = roll_std[bar_idx]
    if np.isnan(s):
        return "unknown"
    if s <= std_p33:
        return "low_vol"
    elif s <= std_p67:
        return "mid_vol"
    else:
        return "high_vol"


# ===================================================================
# STATIC SIMULATION
# ===================================================================
def run_static(inner_mult, outer_mult, label):
    """Run static simulation, return list of trade dicts."""
    bands = all_bands[f"{inner_mult}_{outer_mult}"]
    inner_top = bands["inner_top"]
    inner_bot = bands["inner_bot"]
    outer_top = bands["outer_top"]
    outer_bot = bands["outer_bot"]

    trades = []
    consec_stops_long = 0
    consec_stops_short = 0
    recent_results_today = []
    current_trade_date = None

    in_position = False
    direction = None
    entry_price = 0.0
    target_price = 0.0
    stop_price = 0.0
    entry_bar = 0
    entry_target_offset = 0.0
    entry_stop_offset = 0.0

    def check_buy(idx):
        if np.isnan(roll_mean[idx]):
            return False
        return low[idx] <= inner_bot[idx] and low[idx] > outer_bot[idx]

    def check_sell(idx):
        if np.isnan(roll_mean[idx]):
            return False
        return high[idx] >= inner_top[idx] and high[idx] < outer_top[idx]

    def daily_reset(idx):
        nonlocal current_trade_date, consec_stops_long, consec_stops_short, recent_results_today
        d = bar_dates[idx]
        if current_trade_date is None or d != current_trade_date:
            current_trade_date = d
            consec_stops_long = 0
            consec_stops_short = 0
            recent_results_today = []

    i = LOOKBACK
    while i < n:
        if not in_position:
            if np.isnan(roll_mean[i]) or not is_rth(i):
                i += 1
                continue
            daily_reset(i)

            buy_sig = check_buy(i)
            sell_sig = check_sell(i)
            if buy_sig:
                direction = "long"
            elif sell_sig:
                direction = "short"
            else:
                i += 1
                continue

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

        daily_reset(i)

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

        # Update consec stops
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

        trades.append({
            "entry_bar": entry_bar,
            "exit_bar": i,
            "direction": direction,
            "entry_price": entry_price,
            "exit_type": exit_type,
            "pnl_points": pnl,
            "pnl_dollar": pnl_dollar,
            "is_win": is_win,
            "config": "main",
            "regime": get_regime(entry_bar),
        })

        if exit_type == "reversal":
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
        continue

    return trades


# ===================================================================
# SWITCHING SIMULATION
# ===================================================================
def run_switching(return_rule, return_n=None, label=""):
    """
    Run switching simulation: main=i1.50/o2.00, sub=i0.75/o2.50
    Trigger: rolling_win_rate, switch when <= CALIB_RWR_P33 (0.10)
    return_rule: 'R1' or 'R3_N5'
    """
    main_b = all_bands[main_key]
    sub_b = all_bands[sub_key]

    active_config = "main"
    trades = []
    sub_trade_count = 0

    consec_stops_long = 0
    consec_stops_short = 0
    recent_results_today = []
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

    def daily_reset(idx):
        nonlocal current_trade_date, consec_stops_long, consec_stops_short, recent_results_today
        d = bar_dates[idx]
        if current_trade_date is None or d != current_trade_date:
            current_trade_date = d
            consec_stops_long = 0
            consec_stops_short = 0
            recent_results_today = []

    def get_bands():
        return main_b if active_config == "main" else sub_b

    def check_buy(idx, bd):
        if np.isnan(roll_mean[idx]):
            return False
        return low[idx] <= bd["inner_bot"][idx] and low[idx] > bd["outer_bot"][idx]

    def check_sell(idx, bd):
        if np.isnan(roll_mean[idx]):
            return False
        return high[idx] >= bd["inner_top"][idx] and high[idx] < bd["outer_top"][idx]

    def get_rolling_wr():
        if len(recent_results_today) >= 10:
            return sum(recent_results_today[-10:]) / 10.0
        return np.nan

    def is_in_bad_zone():
        rwr = get_rolling_wr()
        if isinstance(rwr, float) and np.isnan(rwr):
            return False
        return rwr <= CALIB_RWR_P33

    def check_forward_switch():
        nonlocal active_config, sub_trade_count
        if active_config == "main" and is_in_bad_zone():
            active_config = "sub"
            sub_trade_count = 0

    def check_return_switch():
        nonlocal active_config
        if active_config != "sub":
            return
        if return_rule == "R1":
            rwr = get_rolling_wr()
            if isinstance(rwr, float) and np.isnan(rwr):
                return
            if not (rwr <= CALIB_RWR_P33):
                active_config = "main"
        elif return_rule == "R3_N5":
            if sub_trade_count >= return_n:
                active_config = "main"

    def record_trade(pnl_pts, exit_type, config_at_entry, dir_at_entry, ebar):
        nonlocal consec_stops_long, consec_stops_short, sub_trade_count
        pnl_dollar = pnl_pts * POINT_VALUE
        is_win = pnl_pts > 0

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

        trades.append({
            "entry_bar": ebar,
            "exit_bar": i,
            "direction": dir_at_entry,
            "entry_price": entry_price,
            "exit_type": exit_type,
            "pnl_points": pnl_pts,
            "pnl_dollar": pnl_dollar,
            "is_win": is_win,
            "config": config_at_entry,
            "regime": get_regime(ebar),
        })

    i = LOOKBACK
    while i < n:
        if not in_position:
            if np.isnan(roll_mean[i]) or not is_rth(i):
                i += 1
                continue
            daily_reset(i)
            check_forward_switch()
            check_return_switch()

            bd = get_bands()
            buy_sig = check_buy(i, bd)
            sell_sig = check_sell(i, bd)

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
            entry_bar = i
            in_position = True
            i += 1
            continue

        if i >= n:
            break

        daily_reset(i)

        if direction == "long":
            target_hit = high[i] >= target_price
            stop_hit = low[i] <= stop_price
            buy_reversal = False
            ab = get_bands()
            sell_reversal = check_sell(i, ab) and is_rth(i)
        else:
            target_hit = low[i] <= target_price
            stop_hit = high[i] >= stop_price
            ab = get_bands()
            buy_reversal = check_buy(i, ab) and is_rth(i)
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

        record_trade(pnl, exit_type, entry_config, direction, entry_bar)

        if exit_type == "reversal":
            in_position = False
            check_forward_switch()
            check_return_switch()

            entry_config = active_config
            bd = get_bands()
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
            entry_bar = i
            direction = new_direction
            in_position = True
            i += 1
            continue

        in_position = False
        direction = None
        continue

    return trades


# ===================================================================
# MARTINGALE OVERLAY
# ===================================================================
def apply_martingale(trades, mult=1.5, max_level=3):
    """Apply martingale overlay. Counters reset daily and on config switch."""
    contracts = 1.0
    mart_level = 0
    current_config = None
    current_date = None

    results = []
    for t in trades:
        # Detect daily or config switch reset
        t_date = bar_dates[t["entry_bar"]]
        if t_date != current_date:
            contracts = 1.0
            mart_level = 0
            current_date = t_date
        if t["config"] != current_config:
            contracts = 1.0
            mart_level = 0
            current_config = t["config"]

        scaled_pnl = t["pnl_dollar"] * contracts
        results.append({
            **t,
            "pnl_dollar": scaled_pnl,
            "contracts": contracts,
        })

        if not t["is_win"]:
            if mart_level < max_level:
                mart_level += 1
                contracts = mult ** mart_level
        else:
            contracts = 1.0
            mart_level = 0

    return results


# ===================================================================
# METRICS COMPUTATION
# ===================================================================
def compute_metrics(trades, system_label):
    if not trades:
        return None

    n_total = len(trades)
    n_main = sum(1 for t in trades if t["config"] == "main")
    n_sub = sum(1 for t in trades if t["config"] == "sub")

    total_pnl = sum(t["pnl_dollar"] for t in trades)
    ev = total_pnl / n_total
    wins = sum(1 for t in trades if t["is_win"])
    win_rate = wins / n_total * 100

    # Max drawdown
    equity = np.cumsum([t["pnl_dollar"] for t in trades])
    peak = np.maximum.accumulate(equity)
    dd = peak - equity
    max_dd = float(np.max(dd)) if len(dd) > 0 else 0.0

    pnl_dd = total_pnl / max_dd if max_dd > 0 else float('inf')

    # Profit factor
    gross_profit = sum(t["pnl_dollar"] for t in trades if t["pnl_dollar"] > 0)
    gross_loss = abs(sum(t["pnl_dollar"] for t in trades if t["pnl_dollar"] < 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float('inf')

    # Max consecutive losses
    max_consec_loss = 0
    cur_consec = 0
    for t in trades:
        if not t["is_win"]:
            cur_consec += 1
            max_consec_loss = max(max_consec_loss, cur_consec)
        else:
            cur_consec = 0

    # Largest single loss
    losses = [t["pnl_dollar"] for t in trades if t["pnl_dollar"] < 0]
    largest_loss = min(losses) if losses else 0.0

    return {
        "system": system_label,
        "total_trades": n_total,
        "main_trades": n_main,
        "sub_trades": n_sub,
        "win_rate_pct": round(win_rate, 2),
        "ev_per_trade": round(ev, 2),
        "total_pnl": round(total_pnl, 2),
        "max_drawdown": round(max_dd, 2),
        "pnl_maxdd_ratio": round(pnl_dd, 3) if not np.isinf(pnl_dd) else 999.0,
        "profit_factor": round(profit_factor, 3) if not np.isinf(profit_factor) else 999.0,
        "max_consec_losses": max_consec_loss,
        "largest_single_loss": round(largest_loss, 2),
    }


def compute_regime_breakdown(trades, system_label):
    """Per-regime trade count, win rate, EV."""
    rows = []
    for regime in ["low_vol", "mid_vol", "high_vol"]:
        regime_trades = [t for t in trades if t["regime"] == regime]
        if not regime_trades:
            rows.append({
                "system": system_label,
                "regime": regime,
                "trade_count": 0,
                "win_rate_pct": 0.0,
                "ev_per_trade": 0.0,
            })
            continue
        nt = len(regime_trades)
        wins = sum(1 for t in regime_trades if t["is_win"])
        pnl = sum(t["pnl_dollar"] for t in regime_trades)
        rows.append({
            "system": system_label,
            "regime": regime,
            "trade_count": nt,
            "win_rate_pct": round(wins / nt * 100, 2),
            "ev_per_trade": round(pnl / nt, 2),
        })
    return rows


# ===================================================================
# RUN ALL SYSTEMS
# ===================================================================
print("\n" + "=" * 80)
print("RUNNING HOLDOUT VALIDATION")
print("=" * 80)

# Count trading days
unique_dates = sorted(set(bar_dates))
# Filter to dates that have RTH bars
rth_dates = set()
for i in range(n):
    if is_rth(i):
        rth_dates.add(bar_dates[i])
print(f"Total bars: {n}, Trading days with RTH: {len(rth_dates)}")

all_metrics = []
all_regime = []

# --- Static baselines ---
print("\nStatic baselines:")
for im, om, label in STATIC_CONFIGS:
    trades = run_static(im, om, label)
    m = compute_metrics(trades, label)
    all_metrics.append(m)
    all_regime.extend(compute_regime_breakdown(trades, label))
    print(f"  {label}: {m['total_trades']} trades, EV=${m['ev_per_trade']}, "
          f"PnL=${m['total_pnl']}, WR={m['win_rate_pct']}%, MaxDD=${m['max_drawdown']}")

# --- Switching A1: R3_N5 ---
print("\nSwitching systems:")
trades_a1 = run_switching("R3_N5", return_n=5, label="A1_switch_R3N5")
m_a1 = compute_metrics(trades_a1, "A1_switch_R3N5")
all_metrics.append(m_a1)
all_regime.extend(compute_regime_breakdown(trades_a1, "A1_switch_R3N5"))
print(f"  A1 (R3_N5): {m_a1['total_trades']} trades, EV=${m_a1['ev_per_trade']}, "
      f"PnL=${m_a1['total_pnl']}, WR={m_a1['win_rate_pct']}%, MaxDD=${m_a1['max_drawdown']}")

# --- Switching A2: R1 ---
trades_a2 = run_switching("R1", return_n=None, label="A2_switch_R1")
m_a2 = compute_metrics(trades_a2, "A2_switch_R1")
all_metrics.append(m_a2)
all_regime.extend(compute_regime_breakdown(trades_a2, "A2_switch_R1"))
print(f"  A2 (R1):    {m_a2['total_trades']} trades, EV=${m_a2['ev_per_trade']}, "
      f"PnL=${m_a2['total_pnl']}, WR={m_a2['win_rate_pct']}%, MaxDD=${m_a2['max_drawdown']}")

# --- Switching B: A2 + martingale ---
trades_b_raw = run_switching("R1", return_n=None, label="B_switch_R1_mart")
trades_b = apply_martingale(trades_b_raw, mult=1.5, max_level=3)
m_b = compute_metrics(trades_b, "B_switch_R1_mart")
all_metrics.append(m_b)
all_regime.extend(compute_regime_breakdown(trades_b, "B_switch_R1_mart"))
print(f"  B (R1+m1.5): {m_b['total_trades']} trades, EV=${m_b['ev_per_trade']}, "
      f"PnL=${m_b['total_pnl']}, WR={m_b['win_rate_pct']}%, MaxDD=${m_b['max_drawdown']}")

# ===================================================================
# WRITE OUTPUTS
# ===================================================================

# --- holdout-switching.csv ---
metrics_df = pd.DataFrame(all_metrics)
metrics_path = os.path.join(OUTPUT_DIR, "holdout-switching.csv")
metrics_df.to_csv(metrics_path, index=False)
print(f"\nWrote {metrics_path}")

# --- holdout-switching-by-regime.csv ---
regime_df = pd.DataFrame(all_regime)
regime_path = os.path.join(OUTPUT_DIR, "holdout-switching-by-regime.csv")
regime_df.to_csv(regime_path, index=False)
print(f"Wrote {regime_path}")

# --- holdout-switching-summary.txt ---
summary_path = os.path.join(OUTPUT_DIR, "holdout-switching-summary.txt")

lines = []
lines.append("HOLDOUT SWITCHING VALIDATION")
lines.append("=" * 80)
lines.append(f"Holdout data: {n} bars, {len(rth_dates)} RTH trading days")
lines.append(f"Period: {bar_dates[0]} to {bar_dates[-1]}")
lines.append(f"Calibration rolling_win_rate P33 threshold: {CALIB_RWR_P33}")
lines.append(f"Holdout stdDev terciles: P33={std_p33:.4f}, P67={std_p67:.4f}")
lines.append("")

lines.append("SYSTEM COMPARISON")
lines.append("-" * 80)
header = f"{'System':<25} {'Trades':>7} {'WR%':>7} {'EV($)':>8} {'PnL($)':>10} {'MaxDD($)':>10} {'PnL/DD':>7} {'PF':>7} {'MaxCL':>6} {'BigLoss':>9}"
lines.append(header)
lines.append("-" * len(header))

for m in all_metrics:
    line = (f"{m['system']:<25} {m['total_trades']:>7} {m['win_rate_pct']:>7.1f} "
            f"{m['ev_per_trade']:>8.2f} {m['total_pnl']:>10.2f} {m['max_drawdown']:>10.2f} "
            f"{m['pnl_maxdd_ratio']:>7.3f} {m['profit_factor']:>7.3f} "
            f"{m['max_consec_losses']:>6} {m['largest_single_loss']:>9.2f}")
    lines.append(line)

lines.append("")

# Config breakdown for switching systems
lines.append("SWITCHING CONFIG BREAKDOWN")
lines.append("-" * 80)
for m in all_metrics:
    if "switch" in m["system"]:
        lines.append(f"  {m['system']}: {m['main_trades']} main + {m['sub_trades']} sub trades")

lines.append("")

# Regime breakdown
lines.append("REGIME BREAKDOWN (holdout stdDev terciles)")
lines.append("-" * 80)
regime_header = f"{'System':<25} {'Regime':<10} {'Trades':>7} {'WR%':>7} {'EV($)':>8}"
lines.append(regime_header)
lines.append("-" * len(regime_header))
for r in all_regime:
    lines.append(f"{r['system']:<25} {r['regime']:<10} {r['trade_count']:>7} "
                 f"{r['win_rate_pct']:>7.1f} {r['ev_per_trade']:>8.2f}")

lines.append("")

# Key comparisons
lines.append("KEY COMPARISONS")
lines.append("-" * 80)

# Find static 0.75/2.50 metrics
static_075 = next(m for m in all_metrics if m["system"] == "C1_static_0.75_2.50")
static_150 = next(m for m in all_metrics if m["system"] == "C2_static_1.50_2.00")

lines.append(f"  A1 (switch R3_N5) vs static 0.75/2.50: "
             f"EV ${m_a1['ev_per_trade']} vs ${static_075['ev_per_trade']} "
             f"(diff: ${m_a1['ev_per_trade'] - static_075['ev_per_trade']:+.2f})")
lines.append(f"  A1 (switch R3_N5) vs static 1.50/2.00: "
             f"EV ${m_a1['ev_per_trade']} vs ${static_150['ev_per_trade']} "
             f"(diff: ${m_a1['ev_per_trade'] - static_150['ev_per_trade']:+.2f})")
lines.append(f"  A2 (switch R1) vs static 0.75/2.50: "
             f"EV ${m_a2['ev_per_trade']} vs ${static_075['ev_per_trade']} "
             f"(diff: ${m_a2['ev_per_trade'] - static_075['ev_per_trade']:+.2f})")
lines.append(f"  A2 (switch R1) vs static 1.50/2.00: "
             f"EV ${m_a2['ev_per_trade']} vs ${static_150['ev_per_trade']} "
             f"(diff: ${m_a2['ev_per_trade'] - static_150['ev_per_trade']:+.2f})")
lines.append(f"  B (switch R1 + mart) vs static 0.75/2.50: "
             f"EV ${m_b['ev_per_trade']} vs ${static_075['ev_per_trade']} "
             f"(diff: ${m_b['ev_per_trade'] - static_075['ev_per_trade']:+.2f})")

best_static_ev = max(m["ev_per_trade"] for m in all_metrics if "static" in m["system"])
best_static_name = next(m["system"] for m in all_metrics if m["ev_per_trade"] == best_static_ev and "static" in m["system"])
lines.append("")
lines.append(f"  Best static config on holdout: {best_static_name} (EV=${best_static_ev})")

# Did switching beat static 0.75/2.50?
a1_beat = m_a1["ev_per_trade"] > static_075["ev_per_trade"]
a2_beat = m_a2["ev_per_trade"] > static_075["ev_per_trade"]
lines.append(f"  A1 beat static 0.75/2.50? {'YES' if a1_beat else 'NO'}")
lines.append(f"  A2 beat static 0.75/2.50? {'YES' if a2_beat else 'NO'}")

# Did switching beat best static?
a1_beat_best = m_a1["ev_per_trade"] > best_static_ev
a2_beat_best = m_a2["ev_per_trade"] > best_static_ev
lines.append(f"  A1 beat best static? {'YES' if a1_beat_best else 'NO'}")
lines.append(f"  A2 beat best static? {'YES' if a2_beat_best else 'NO'}")

lines.append("")

summary_text = "\n".join(lines)
with open(summary_path, "w") as f:
    f.write(summary_text)
print(f"Wrote {summary_path}")

print("\n" + summary_text)
print("\nDone.")
