"""
Martingale overlay analysis on top RTH-filtered range-fade combinations.

Uses identical simulation logic to multiplier-grid-analysis.py (v4):
- ddof=0 population std
- Same-bar re-entry
- Reversal logic (target > stop > reversal priority)
- RTH filter 09:30-15:45 for new entries only
- Entry at bar close

Adds per-side martingale position sizing:
- Track consecutive stops independently for long and short sides
- qty = floor(base * mult^consecutiveStops), capped at max, minimum 1
- Win resets that side's counter; loss increments it
- Reversal exit: negative PnL = loss, positive PnL = win
"""

import pandas as pd
import numpy as np
import os
import math
from itertools import product

# --- Config ---
LOOKBACK = 50
TICK_SIZE = 0.25
TICK_VALUE = 5.00
POINT_VALUE = TICK_VALUE / TICK_SIZE  # $20 per point
TRADING_DAYS = 73

RTH_START = "09:30:00"
RTH_END = "15:45:00"

DATA_PATH = r"c:\Projects\futures_pipeline\data\NQ-250tick-calibration.csv"
OUTPUT_DIR = r"c:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection"

# Top combinations to test
COMBOS = [
    (1.50, 2.00),  # best EV
    (0.50, 2.00),  # 2nd best EV, all-regime positive
    (1.50, 3.00),  # 3rd best EV
    (0.75, 2.50),  # 4th best, strong in high vol
    (0.50, 1.75),  # 5th best, all-regime positive
    (1.00, 2.00),  # current v3 settings
]

# Martingale configurations: (mult, max_contracts) — None = no martingale
MART_CONFIGS = [
    None,           # baseline: qty=1 always
    (1.5, 2),       # current v3 settings
    (1.5, 3),
    (2.0, 2),
    (2.0, 3),
    (2.0, 4),
]

def mart_label(cfg):
    if cfg is None:
        return "no_martingale"
    return f"m{cfg[0]}_max{cfg[1]}"

# --- Load data ---
df = pd.read_csv(DATA_PATH)
df.columns = df.columns.str.strip()

high = df["High"].values.astype(float)
low = df["Low"].values.astype(float)
close = df["Last"].values.astype(float)
dates = df["Date"].values
times_raw = df["Time"].values

def parse_time_hms(t):
    s = str(t).strip()
    return s[:8]

bar_times = np.array([parse_time_hms(t) for t in times_raw])

def is_rth(bar_idx):
    t = bar_times[bar_idx]
    return t >= RTH_START and t <= RTH_END

n = len(df)

# --- Precompute rolling mean/std (ddof=0) ---
roll_mean = np.full(n, np.nan)
roll_std = np.full(n, np.nan)

for i in range(LOOKBACK - 1, n):
    window = close[i - LOOKBACK + 1 : i + 1]
    roll_mean[i] = np.mean(window)
    roll_std[i] = np.std(window, ddof=0)

print(f"Loaded {n} bars, rolling stats computed.")

# --- Count unique dates ---
unique_dates = len(set(dates[~pd.isna(dates)]))
print(f"Unique trading dates: {unique_dates}")


def simulate_martingale(inner_mult, outer_mult, mart_cfg):
    """
    Run simulation with martingale position sizing.

    mart_cfg: None for baseline (qty=1), or (mult, max_contracts).

    Returns list of trade dicts with all fields needed for analysis.
    """
    use_martingale = mart_cfg is not None
    if use_martingale:
        mart_mult, mart_max = mart_cfg

    # Compute bands
    inner_top = roll_mean + inner_mult * roll_std
    inner_bot = roll_mean - inner_mult * roll_std
    outer_top = roll_mean + outer_mult * roll_std
    outer_bot = roll_mean - outer_mult * roll_std

    trades = []

    # Martingale state — per side
    consec_long = 0
    consec_short = 0

    # Position state
    in_position = False
    direction = None
    entry_price = 0.0
    target_price = 0.0
    stop_price = 0.0
    entry_bar = 0
    entry_target_offset = 0.0
    entry_stop_offset = 0.0
    position_qty = 1

    def get_qty(dir_):
        if not use_martingale:
            return 1
        consec = consec_long if dir_ == "long" else consec_short
        qty = int(math.floor(1 * mart_mult ** consec))
        qty = max(1, min(qty, mart_max))
        return qty

    def check_buy_signal(bar_idx):
        if np.isnan(roll_mean[bar_idx]):
            return False
        return low[bar_idx] <= inner_bot[bar_idx] and low[bar_idx] > outer_bot[bar_idx]

    def check_sell_signal(bar_idx):
        if np.isnan(roll_mean[bar_idx]):
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

    def record_trade(pnl_pts, exit_type, dir_, qty, entry_b, exit_b):
        nonlocal consec_long, consec_short

        pnl_dollar = pnl_pts * POINT_VALUE * qty
        is_win = pnl_pts > 0

        # Update martingale counters
        if dir_ == "long":
            if is_win:
                consec_long = 0
            else:
                consec_long += 1
        else:
            if is_win:
                consec_short = 0
            else:
                consec_short += 1

        trades.append({
            "direction": dir_,
            "qty": qty,
            "pnl_pts": pnl_pts,
            "pnl_dollar": pnl_dollar,
            "exit_type": exit_type,
            "entry_bar": entry_b,
            "exit_bar": exit_b,
            "is_win": is_win,
        })

    i = LOOKBACK
    while i < n:
        if not in_position:
            if np.isnan(roll_mean[i]):
                i += 1
                continue
            if not is_rth(i):
                i += 1
                continue

            buy_sig = check_buy_signal(i)
            sell_sig = check_sell_signal(i)

            if buy_sig:
                direction = "long"
            elif sell_sig:
                direction = "short"
            else:
                i += 1
                continue

            position_qty = get_qty(direction)
            (entry_price, target_price, stop_price,
             entry_target_offset, entry_stop_offset,
             entry_bar) = enter_position(i, direction)
            in_position = True
            i += 1
            continue

        # In position — check resolution
        if i >= n:
            break

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

            record_trade(pnl, exit_type, direction, position_qty, entry_bar, i)
            in_position = False
            direction = None
            continue

        elif target_hit:
            pnl = entry_target_offset
            record_trade(pnl, "target", direction, position_qty, entry_bar, i)
            in_position = False
            direction = None
            continue

        elif stop_hit:
            pnl = -entry_stop_offset
            record_trade(pnl, "stop", direction, position_qty, entry_bar, i)
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

            record_trade(pnl, "reversal", direction, position_qty, entry_bar, i)

            # Enter opposite direction
            position_qty = get_qty(new_direction)
            (entry_price, target_price, stop_price,
             entry_target_offset, entry_stop_offset,
             entry_bar) = enter_position(i, new_direction)
            direction = new_direction
            in_position = True
            i += 1
            continue

        i += 1

    return trades


# --- Run all simulations ---
all_results = []
equity_curves = []

for inner_mult, outer_mult in COMBOS:
    for mart_cfg in MART_CONFIGS:
        label = mart_label(mart_cfg)
        combo_str = f"i{inner_mult}_o{outer_mult}"

        trades = simulate_martingale(inner_mult, outer_mult, mart_cfg)

        if len(trades) == 0:
            continue

        # Compute metrics
        total_trades = len(trades)
        pnl_dollars = np.array([t["pnl_dollar"] for t in trades])
        pnl_pts = np.array([t["pnl_pts"] for t in trades])
        qtys = np.array([t["qty"] for t in trades])
        wins = np.array([t["is_win"] for t in trades])

        total_pnl = float(np.sum(pnl_dollars))
        pnl_per_day = total_pnl / TRADING_DAYS

        win_count = int(np.sum(wins))
        loss_count = total_trades - win_count
        win_rate = win_count / total_trades * 100

        # Max drawdown (peak-to-trough of cumulative PnL)
        cum_pnl = np.cumsum(pnl_dollars)
        running_max = np.maximum.accumulate(cum_pnl)
        drawdowns = running_max - cum_pnl
        max_dd = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0

        # Max consecutive losses
        max_consec = 0
        current_streak = 0
        for w in wins:
            if not w:
                current_streak += 1
                max_consec = max(max_consec, current_streak)
            else:
                current_streak = 0

        # Profit factor
        gross_wins = float(np.sum(pnl_dollars[wins])) if win_count > 0 else 0
        gross_losses = float(abs(np.sum(pnl_dollars[~wins]))) if loss_count > 0 else 0
        profit_factor = gross_wins / gross_losses if gross_losses > 0 else float("inf")

        # Average win/loss (in dollars, including sizing)
        avg_win = float(np.mean(pnl_dollars[wins])) if win_count > 0 else 0
        avg_loss = float(np.mean(pnl_dollars[~wins])) if loss_count > 0 else 0

        # Largest single loss
        largest_loss = float(np.min(pnl_dollars)) if len(pnl_dollars) > 0 else 0

        # PnL/MaxDD ratio
        pnl_maxdd_ratio = total_pnl / max_dd if max_dd > 0 else float("inf")

        all_results.append({
            "inner_mult": inner_mult,
            "outer_mult": outer_mult,
            "martingale": label,
            "total_trades": total_trades,
            "total_pnl": round(total_pnl, 2),
            "pnl_per_day": round(pnl_per_day, 2),
            "win_rate_pct": round(win_rate, 2),
            "max_drawdown": round(max_dd, 2),
            "max_consec_losses": max_consec,
            "profit_factor": round(profit_factor, 4),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "largest_loss": round(largest_loss, 2),
            "pnl_maxdd_ratio": round(pnl_maxdd_ratio, 4),
        })

        # Equity curve data for no_martingale and m1.5_max2
        if label in ("no_martingale", "m1.5_max2"):
            cum = 0.0
            for idx, t in enumerate(trades):
                cum += t["pnl_dollar"]
                equity_curves.append({
                    "inner_mult": inner_mult,
                    "outer_mult": outer_mult,
                    "martingale": label,
                    "trade_num": idx + 1,
                    "pnl_dollar": round(t["pnl_dollar"], 2),
                    "cumulative_pnl": round(cum, 2),
                    "qty": t["qty"],
                    "direction": t["direction"],
                    "exit_type": t["exit_type"],
                })

        print(f"  {combo_str} {label}: {total_trades} trades, PnL=${total_pnl:+.2f}, "
              f"WR={win_rate:.1f}%, MaxDD=${max_dd:.2f}, PF={profit_factor:.2f}")

print(f"\nTotal result rows: {len(all_results)}")

# --- Write CSVs ---
results_df = pd.DataFrame(all_results)
results_df.to_csv(os.path.join(OUTPUT_DIR, "martingale-overlay.csv"), index=False)
print("Wrote martingale-overlay.csv")

equity_df = pd.DataFrame(equity_curves)
equity_df.to_csv(os.path.join(OUTPUT_DIR, "martingale-equity-curves.csv"), index=False)
print("Wrote martingale-equity-curves.csv")

# --- Write summary ---
lines = []
lines.append("MARTINGALE OVERLAY ANALYSIS — RANGE-FADE ROTATION (NQ 250-tick)")
lines.append("=" * 95)
lines.append(f"Data: {n} bars, {TRADING_DAYS} trading days")
lines.append(f"Simulation: ddof=0, same-bar re-entry, reversals, RTH 09:30-15:45")
lines.append(f"NQ tick size={TICK_SIZE}, tick value=${TICK_VALUE}, point value=${POINT_VALUE}")
lines.append("")
lines.append("MARTINGALE LOGIC:")
lines.append("  - Base quantity: 1 contract")
lines.append("  - Per-side consecutive loss tracking (long/short independent)")
lines.append("  - Next qty = floor(base * mult^consecutiveStops), capped at max, min 1")
lines.append("  - Win resets that side's counter; loss increments it")
lines.append("  - Reversal exit: negative PnL = loss, positive PnL = win")
lines.append("")

# For each combo, show all martingale configs side by side
for inner_mult, outer_mult in COMBOS:
    combo_rows = results_df[
        (results_df["inner_mult"] == inner_mult) &
        (results_df["outer_mult"] == outer_mult)
    ]

    lines.append(f"COMBO: inner={inner_mult:.2f}, outer={outer_mult:.2f}")
    lines.append("-" * 95)
    lines.append(f"  {'Config':<16s} {'Trades':>6s} {'TotalPnL':>10s} {'PnL/day':>9s} "
                 f"{'WR%':>6s} {'MaxDD':>10s} {'MaxCL':>5s} {'PF':>7s} "
                 f"{'AvgWin':>9s} {'AvgLoss':>9s} {'BigLoss':>9s} {'PnL/DD':>8s}")

    for _, row in combo_rows.iterrows():
        lines.append(
            f"  {row['martingale']:<16s} {int(row['total_trades']):>6d} "
            f"${row['total_pnl']:>9.2f} ${row['pnl_per_day']:>8.2f} "
            f"{row['win_rate_pct']:>5.1f}% ${row['max_drawdown']:>9.2f} "
            f"{int(row['max_consec_losses']):>5d} {row['profit_factor']:>7.2f} "
            f"${row['avg_win']:>8.2f} ${row['avg_loss']:>8.2f} "
            f"${row['largest_loss']:>8.2f} {row['pnl_maxdd_ratio']:>8.4f}"
        )
    lines.append("")

# Cross-combo comparison: best martingale config per combo
lines.append("BEST MARTINGALE CONFIG PER COMBO (by PnL/MaxDD ratio)")
lines.append("-" * 95)

for inner_mult, outer_mult in COMBOS:
    combo_rows = results_df[
        (results_df["inner_mult"] == inner_mult) &
        (results_df["outer_mult"] == outer_mult)
    ]
    # Exclude no_martingale for "best martingale" comparison
    mart_rows = combo_rows[combo_rows["martingale"] != "no_martingale"]
    if len(mart_rows) > 0:
        best = mart_rows.loc[mart_rows["pnl_maxdd_ratio"].idxmax()]
        baseline = combo_rows[combo_rows["martingale"] == "no_martingale"].iloc[0]

        pnl_lift = best["total_pnl"] - baseline["total_pnl"]
        dd_increase = best["max_drawdown"] - baseline["max_drawdown"]

        lines.append(
            f"  i={inner_mult:.2f} o={outer_mult:.2f}: "
            f"{best['martingale']} — "
            f"PnL=${best['total_pnl']:+.2f} (lift ${pnl_lift:+.2f}), "
            f"MaxDD=${best['max_drawdown']:.2f} (increase ${dd_increase:+.2f}), "
            f"PnL/DD={best['pnl_maxdd_ratio']:.4f} vs baseline {baseline['pnl_maxdd_ratio']:.4f}"
        )

lines.append("")

# Drawdown comparison table
lines.append("DRAWDOWN COMPARISON: BASELINE vs MARTINGALE CONFIGS")
lines.append("-" * 95)
lines.append(f"  {'Combo':<14s} {'no_mart DD':>10s} {'m1.5x2 DD':>10s} {'m1.5x3 DD':>10s} "
             f"{'m2.0x2 DD':>10s} {'m2.0x3 DD':>10s} {'m2.0x4 DD':>10s}")

for inner_mult, outer_mult in COMBOS:
    combo_rows = results_df[
        (results_df["inner_mult"] == inner_mult) &
        (results_df["outer_mult"] == outer_mult)
    ]
    vals = []
    for cfg in MART_CONFIGS:
        label = mart_label(cfg)
        row = combo_rows[combo_rows["martingale"] == label]
        if len(row) > 0:
            vals.append(f"${row.iloc[0]['max_drawdown']:>9.2f}")
        else:
            vals.append(f"{'N/A':>10s}")

    lines.append(f"  i{inner_mult:.2f}_o{outer_mult:.2f}  {'  '.join(vals)}")

lines.append("")

# Largest single loss comparison
lines.append("LARGEST SINGLE LOSS: BASELINE vs MARTINGALE CONFIGS")
lines.append("-" * 95)
lines.append(f"  {'Combo':<14s} {'no_mart':>10s} {'m1.5x2':>10s} {'m1.5x3':>10s} "
             f"{'m2.0x2':>10s} {'m2.0x3':>10s} {'m2.0x4':>10s}")

for inner_mult, outer_mult in COMBOS:
    combo_rows = results_df[
        (results_df["inner_mult"] == inner_mult) &
        (results_df["outer_mult"] == outer_mult)
    ]
    vals = []
    for cfg in MART_CONFIGS:
        label = mart_label(cfg)
        row = combo_rows[combo_rows["martingale"] == label]
        if len(row) > 0:
            vals.append(f"${row.iloc[0]['largest_loss']:>9.2f}")
        else:
            vals.append(f"{'N/A':>10s}")

    lines.append(f"  i{inner_mult:.2f}_o{outer_mult:.2f}  {'  '.join(vals)}")

summary_text = "\n".join(lines)
with open(os.path.join(OUTPUT_DIR, "martingale-summary.txt"), "w") as f:
    f.write(summary_text)
print("Wrote martingale-summary.txt")

print("\n" + summary_text)
print("\nDone.")
