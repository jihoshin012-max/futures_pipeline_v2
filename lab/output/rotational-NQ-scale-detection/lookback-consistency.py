"""
Lookback × multiplier consistency and drawdown analysis.

Sweeps lookback periods [20, 30, 40, 50, 75, 100] against selected
multiplier combos. Computes daily PnL aggregates, drawdown, and
consistency score.

Simulation logic:
- RTH only (09:30-15:45), entry at bar close
- StdDev ddof=0 (population)
- Same-bar re-entry after resolution
- Reversal logic (target > stop > reversal priority)
- No martingale, 1 contract
- NQ tick size = 0.25, tick value = $5.00
"""

import pandas as pd
import numpy as np
import os
from itertools import product

# --- Config ---
LOOKBACKS = [20, 30, 40, 50, 75, 100]
COMBOS = [
    (1.00, 1.25, "smallest drawdowns"),
    (0.50, 1.75, "good consistency + low DD"),
    (0.50, 2.00, "highest win day %"),
    (1.50, 2.00, "best EV"),
    (0.75, 2.50, "holdout winner"),
    (1.00, 1.50, "nearby combo to test"),
    (0.75, 1.75, "nearby combo to test"),
]

TICK_SIZE = 0.25
TICK_VALUE = 5.00
POINT_VALUE = TICK_VALUE / TICK_SIZE  # $20 per point

RTH_START = "09:30:00"
RTH_END = "15:45:00"

DATA_PATH = r"c:\Projects\futures_pipeline\data\NQ-250tick-calibration.csv"
OUTPUT_DIR = r"c:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection"

# --- Load data ---
df = pd.read_csv(DATA_PATH)
df.columns = df.columns.str.strip()

high = df["High"].values.astype(float)
low = df["Low"].values.astype(float)
close = df["Last"].values.astype(float)
dates = df["Date"].values
times_raw = df["Time"].values

n = len(df)

# --- Parse bar times for RTH filter ---
def parse_time_hms(t):
    s = str(t).strip()
    return s[:8]

bar_times = np.array([parse_time_hms(t) for t in times_raw])

def is_rth(bar_idx):
    t = bar_times[bar_idx]
    return t >= RTH_START and t <= RTH_END

# --- Parse dates for daily aggregation ---
date_strs = np.array([str(d).strip() for d in dates])

# Count unique trading dates
unique_dates_set = set(date_strs)
unique_dates = len(unique_dates_set)
print(f"Loaded {n} bars, {unique_dates} unique dates.")

# Count RTH bars
rth_count = sum(1 for i in range(n) if is_rth(i))
print(f"RTH bars (09:30-15:45): {rth_count} / {n} ({rth_count/n*100:.1f}%)")


def simulate(lookback, inner_mult, outer_mult):
    """
    Run simulation for one lookback x multiplier combination.
    Returns list of (pnl_points, entry_date_str) tuples.
    """
    # Compute rolling mean/std
    roll_mean = np.full(n, np.nan)
    roll_std = np.full(n, np.nan)

    for i in range(lookback - 1, n):
        window = close[i - lookback + 1 : i + 1]
        roll_mean[i] = np.mean(window)
        roll_std[i] = np.std(window, ddof=0)

    # Compute bands
    inner_top = roll_mean + inner_mult * roll_std
    inner_bot = roll_mean - inner_mult * roll_std
    outer_top = roll_mean + outer_mult * roll_std
    outer_bot = roll_mean - outer_mult * roll_std

    trades = []  # list of (pnl_points, exit_date_str)

    in_position = False
    direction = None
    entry_price = 0.0
    target_price = 0.0
    stop_price = 0.0
    entry_target_offset = 0.0
    entry_stop_offset = 0.0

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
        return ep, tp, sp, t_offset, s_offset

    i = lookback
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

            (entry_price, target_price, stop_price,
             entry_target_offset, entry_stop_offset) = enter_position(i, direction)
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
                else:
                    pnl = -entry_stop_offset
            else:
                if close[i] <= entry_price:
                    pnl = entry_target_offset
                else:
                    pnl = -entry_stop_offset
            trades.append((pnl, date_strs[i]))
            in_position = False
            direction = None
            continue

        elif target_hit:
            pnl = entry_target_offset
            trades.append((pnl, date_strs[i]))
            in_position = False
            direction = None
            continue

        elif stop_hit:
            pnl = -entry_stop_offset
            trades.append((pnl, date_strs[i]))
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
            trades.append((pnl, date_strs[i]))

            (entry_price, target_price, stop_price,
             entry_target_offset, entry_stop_offset) = enter_position(i, new_direction)
            direction = new_direction
            in_position = True
            i += 1
            continue

        i += 1

    return trades


def compute_daily_stats(trades):
    """
    Given list of (pnl_points, date_str), compute daily aggregates
    and drawdown metrics.
    """
    if len(trades) == 0:
        return None

    # Aggregate PnL by date
    daily_pnl = {}
    for pnl_pts, dt in trades:
        pnl_dollar = pnl_pts * POINT_VALUE
        if dt not in daily_pnl:
            daily_pnl[dt] = 0.0
        daily_pnl[dt] += pnl_dollar

    # Sort dates
    sorted_dates = sorted(daily_pnl.keys())
    daily_vals = [daily_pnl[d] for d in sorted_dates]

    total_pnl = sum(daily_vals)

    up_days = [v for v in daily_vals if v > 0]
    down_days = [v for v in daily_vals if v < 0]
    flat_days = [v for v in daily_vals if v == 0]
    total_days = len(daily_vals)

    up_count = len(up_days)
    down_count = len(down_days)
    win_day_pct = (up_count / total_days * 100) if total_days > 0 else 0

    avg_up = np.mean(up_days) if up_days else 0
    avg_down = np.mean(down_days) if down_days else 0
    worst_day = min(daily_vals)
    best_day = max(daily_vals)

    # Max drawdown (peak-to-trough of cumulative daily PnL)
    cum_pnl = np.cumsum(daily_vals)
    peak = cum_pnl[0]
    max_dd = 0.0
    for v in cum_pnl:
        if v > peak:
            peak = v
        dd = peak - v
        if dd > max_dd:
            max_dd = dd

    pnl_maxdd_ratio = (total_pnl / max_dd) if max_dd > 0 else float("inf")

    # Trade-level stats
    pnls_pts = np.array([t[0] for t in trades])
    pnls_dollar = pnls_pts * POINT_VALUE
    total_trades = len(trades)
    wins = np.sum(pnls_pts > 0)
    win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
    ev_per_trade = total_pnl / total_trades if total_trades > 0 else 0
    trades_per_day = total_trades / total_days if total_days > 0 else 0

    gross_wins = np.sum(pnls_dollar[pnls_dollar > 0])
    gross_losses = abs(np.sum(pnls_dollar[pnls_dollar < 0]))
    profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else float("inf")

    # Consistency score
    if total_pnl > 0:
        consistency = (win_day_pct / 100.0) * (1.0 - max_dd / total_pnl)
    else:
        consistency = np.nan

    return {
        "total_trades": total_trades,
        "total_pnl": round(total_pnl, 2),
        "up_days": up_count,
        "down_days": down_count,
        "total_days": total_days,
        "win_day_pct": round(win_day_pct, 2),
        "avg_up_day": round(avg_up, 2),
        "avg_down_day": round(avg_down, 2),
        "worst_day": round(worst_day, 2),
        "best_day": round(best_day, 2),
        "max_drawdown": round(max_dd, 2),
        "pnl_maxdd_ratio": round(pnl_maxdd_ratio, 4),
        "trades_per_day": round(trades_per_day, 2),
        "win_rate": round(win_rate, 2),
        "ev_per_trade": round(ev_per_trade, 2),
        "profit_factor": round(profit_factor, 4),
        "consistency_score": round(consistency, 4) if not np.isnan(consistency) else "",
    }


# --- Run all combinations ---
all_results = []
total_combos = len(LOOKBACKS) * len(COMBOS)
combo_idx = 0

for lookback in LOOKBACKS:
    for inner_mult, outer_mult, label in COMBOS:
        combo_idx += 1
        print(f"  [{combo_idx}/{total_combos}] lb={lookback} inner={inner_mult} outer={outer_mult} ({label})")

        trades = simulate(lookback, inner_mult, outer_mult)
        stats = compute_daily_stats(trades)

        if stats is None:
            print(f"    -> No trades")
            continue

        row = {
            "lookback": lookback,
            "inner_mult": inner_mult,
            "outer_mult": outer_mult,
            "label": label,
        }
        row.update(stats)
        all_results.append(row)

        print(f"    -> {stats['total_trades']} trades, PnL=${stats['total_pnl']:.0f}, "
              f"WinDay%={stats['win_day_pct']:.1f}%, MaxDD=${stats['max_drawdown']:.0f}, "
              f"Consistency={stats['consistency_score']}")

print(f"\nCompleted {len(all_results)} combinations with trades.")

# --- Write CSV ---
results_df = pd.DataFrame(all_results)
csv_path = os.path.join(OUTPUT_DIR, "lookback-consistency.csv")
results_df.to_csv(csv_path, index=False)
print(f"Wrote {csv_path}")

# --- Write summary ---
lines = []
lines.append("LOOKBACK x MULTIPLIER CONSISTENCY AND DRAWDOWN ANALYSIS")
lines.append("=" * 100)
lines.append(f"Data: {n} bars, {unique_dates} unique dates")
lines.append(f"RTH bars (09:30-15:45): {rth_count} / {n} ({rth_count/n*100:.1f}%)")
lines.append(f"Lookbacks tested: {LOOKBACKS}")
lines.append(f"Multiplier combos tested: {len(COMBOS)}")
lines.append(f"Total combinations: {total_combos}")
lines.append(f"Combinations with trades: {len(all_results)}")
lines.append("")
lines.append("Simulation: RTH entry (09:30-15:45), bar close entry, ddof=0,")
lines.append("  same-bar re-entry, reversal logic, 1 contract, NQ $5/tick.")
lines.append("")
lines.append("Consistency score = win_day_pct * (1 - maxDD/totalPnL)")
lines.append("  Higher = more consistent daily returns with lower relative drawdown.")
lines.append("  Only computed when totalPnL > 0.")
lines.append("")

# Sort: consistency score desc, then win_day_pct desc, then pnl_maxdd_ratio desc
# Handle empty consistency scores (negative PnL combos)
def sort_key(row):
    cs = row.get("consistency_score", "")
    cs_val = float(cs) if cs != "" else -9999
    return (-cs_val, -row["win_day_pct"], -row["pnl_maxdd_ratio"])

sorted_results = sorted(all_results, key=sort_key)

lines.append("TOP 10 BY CONSISTENCY SCORE")
lines.append("-" * 100)
lines.append(
    f"  {'LB':>3s}  {'Inner':>5s}  {'Outer':>5s}  {'Label':>28s}  "
    f"{'Trades':>6s}  {'PnL$':>9s}  {'UpD':>4s}  {'DnD':>4s}  "
    f"{'WD%':>5s}  {'AvgUp$':>8s}  {'AvgDn$':>8s}  {'Worst$':>8s}  {'Best$':>8s}  "
    f"{'MaxDD$':>8s}  {'PnL/DD':>7s}  {'T/day':>5s}  {'WR%':>5s}  {'EV$':>7s}  "
    f"{'PF':>6s}  {'Consist':>7s}"
)

for idx, row in enumerate(sorted_results[:10]):
    cs_str = f"{row['consistency_score']:.4f}" if row['consistency_score'] != "" else "N/A"
    pnl_dd = f"{row['pnl_maxdd_ratio']:.2f}" if row['pnl_maxdd_ratio'] != float("inf") else "inf"
    pf_str = f"{row['profit_factor']:.2f}" if row['profit_factor'] != float("inf") else "inf"
    lines.append(
        f"  {row['lookback']:>3d}  {row['inner_mult']:>5.2f}  {row['outer_mult']:>5.2f}  "
        f"{row['label']:>28s}  "
        f"{row['total_trades']:>6d}  {row['total_pnl']:>9.0f}  "
        f"{row['up_days']:>4d}  {row['down_days']:>4d}  "
        f"{row['win_day_pct']:>5.1f}  {row['avg_up_day']:>8.0f}  {row['avg_down_day']:>8.0f}  "
        f"{row['worst_day']:>8.0f}  {row['best_day']:>8.0f}  "
        f"{row['max_drawdown']:>8.0f}  {pnl_dd:>7s}  "
        f"{row['trades_per_day']:>5.1f}  {row['win_rate']:>5.1f}  "
        f"{row['ev_per_trade']:>7.0f}  {pf_str:>6s}  {cs_str:>7s}"
    )

lines.append("")
lines.append("FULL GRID (sorted by consistency score, then win day %, then PnL/MaxDD)")
lines.append("-" * 100)
lines.append(
    f"  {'LB':>3s}  {'Inner':>5s}  {'Outer':>5s}  {'Label':>28s}  "
    f"{'Trades':>6s}  {'PnL$':>9s}  {'UpD':>4s}  {'DnD':>4s}  "
    f"{'WD%':>5s}  {'AvgUp$':>8s}  {'AvgDn$':>8s}  {'Worst$':>8s}  {'Best$':>8s}  "
    f"{'MaxDD$':>8s}  {'PnL/DD':>7s}  {'T/day':>5s}  {'WR%':>5s}  {'EV$':>7s}  "
    f"{'PF':>6s}  {'Consist':>7s}"
)

for idx, row in enumerate(sorted_results):
    cs_str = f"{row['consistency_score']:.4f}" if row['consistency_score'] != "" else "N/A"
    pnl_dd = f"{row['pnl_maxdd_ratio']:.2f}" if row['pnl_maxdd_ratio'] != float("inf") else "inf"
    pf_str = f"{row['profit_factor']:.2f}" if row['profit_factor'] != float("inf") else "inf"
    marker = " ***" if idx < 10 else ""
    lines.append(
        f"  {row['lookback']:>3d}  {row['inner_mult']:>5.2f}  {row['outer_mult']:>5.2f}  "
        f"{row['label']:>28s}  "
        f"{row['total_trades']:>6d}  {row['total_pnl']:>9.0f}  "
        f"{row['up_days']:>4d}  {row['down_days']:>4d}  "
        f"{row['win_day_pct']:>5.1f}  {row['avg_up_day']:>8.0f}  {row['avg_down_day']:>8.0f}  "
        f"{row['worst_day']:>8.0f}  {row['best_day']:>8.0f}  "
        f"{row['max_drawdown']:>8.0f}  {pnl_dd:>7s}  "
        f"{row['trades_per_day']:>5.1f}  {row['win_rate']:>5.1f}  "
        f"{row['ev_per_trade']:>7.0f}  {pf_str:>6s}  {cs_str:>7s}{marker}"
    )

# Per-lookback summary
lines.append("")
lines.append("PER-LOOKBACK SUMMARY (best combo for each lookback by consistency)")
lines.append("-" * 100)

for lb in LOOKBACKS:
    lb_rows = [r for r in sorted_results if r["lookback"] == lb]
    if not lb_rows:
        lines.append(f"  lb={lb}: No trades")
        continue
    # Best by consistency (already sorted)
    best = lb_rows[0]
    cs_str = f"{best['consistency_score']:.4f}" if best['consistency_score'] != "" else "N/A"
    lines.append(
        f"  lb={lb:>3d}: best = inner={best['inner_mult']:.2f} outer={best['outer_mult']:.2f} "
        f"({best['label']})  "
        f"PnL=${best['total_pnl']:.0f}  WD%={best['win_day_pct']:.1f}  "
        f"MaxDD=${best['max_drawdown']:.0f}  Consist={cs_str}  "
        f"Trades={best['total_trades']}  EV=${best['ev_per_trade']:.0f}"
    )

# Per-combo summary
lines.append("")
lines.append("PER-COMBO SUMMARY (best lookback for each combo by consistency)")
lines.append("-" * 100)

for inner_mult, outer_mult, label in COMBOS:
    combo_rows = [r for r in sorted_results
                  if r["inner_mult"] == inner_mult and r["outer_mult"] == outer_mult]
    if not combo_rows:
        lines.append(f"  inner={inner_mult:.2f} outer={outer_mult:.2f} ({label}): No trades")
        continue
    best = combo_rows[0]
    cs_str = f"{best['consistency_score']:.4f}" if best['consistency_score'] != "" else "N/A"
    lines.append(
        f"  inner={best['inner_mult']:.2f} outer={best['outer_mult']:.2f} "
        f"({label}):  best lb={best['lookback']}  "
        f"PnL=${best['total_pnl']:.0f}  WD%={best['win_day_pct']:.1f}  "
        f"MaxDD=${best['max_drawdown']:.0f}  Consist={cs_str}  "
        f"Trades={best['total_trades']}  EV=${best['ev_per_trade']:.0f}"
    )

summary_text = "\n".join(lines)
summary_path = os.path.join(OUTPUT_DIR, "lookback-consistency-summary.txt")
with open(summary_path, "w") as f:
    f.write(summary_text)
print(f"Wrote {summary_path}")

print("\n" + summary_text)
print("\nDone.")
