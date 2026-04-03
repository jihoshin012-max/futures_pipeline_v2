"""Martingale sizing test on rangefade rotation with N=2 circuit breaker.

Uses the same simulation logic as consec-stop-analysis.py (target/stop
resolution, reversals, same-bar re-entry, RTH filter, ddof=0, circuit
breaker) but adds martingale position sizing.

Martingale logic (matching v3):
  - Base qty = 1
  - Per-side consecutive stop counters (same ones used for circuit breaker)
  - Next trade qty on losing side = floor(base * mult^consecutiveStops)
  - Cap at max contracts, minimum 1
  - Win resets that side's counter to 0
  - Reversal with negative PnL = loss (increments), positive = win (resets)
  - Counters reset daily
  - When circuit breaker fires and forced opposite entry happens,
    both counters reset -- martingale qty goes back to base

Config: LB=100, inner=0.75, outer=1.75, N=2
RTH: 09:30-15:45, entry at bar close, ddof=0
NQ tick_size=0.25, tick_value=$5.00, cost=$4/trade * qty
"""
from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

import numpy as np


# -- Constants --
LOOKBACK = 100
INNER_MULT = 0.75
OUTER_MULT = 1.75
TICK_SIZE = 0.25
TICK_VALUE = 5.00
POINT_VALUE = TICK_VALUE / TICK_SIZE  # $20 per point
COST_PER_TRADE = 4.00  # per contract

RTH_START = "09:30:00"
RTH_END = "15:45:00"

MAX_CONSEC = 2  # circuit breaker threshold

# Martingale configs: (label, mult, max_qty)
MARTINGALE_CONFIGS = [
    ("no-martingale", 1.0, 1),
    ("m1.5-max2", 1.5, 2),
    ("m1.5-max3", 1.5, 3),
    ("m2.0-max2", 2.0, 2),
    ("m2.0-max3", 2.0, 3),
]

DATA_PATH = Path(r"C:\Projects\futures_pipeline\data\NQ-250tick-calibration.csv")
OUTPUT_DIR = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")


# -- Load 250-tick bar data --
def load_bars(filepath: Path):
    dates_str, times_str = [], []
    date_ints = []
    opens, highs, lows, closes = [], [], [], []

    with open(filepath, "r") as f:
        reader = csv.reader(f)
        next(reader)  # skip header
        for row in reader:
            if len(row) < 6:
                continue
            date_s = row[0].strip()
            time_s = row[1].strip()
            o, h, l, c = float(row[2]), float(row[3]), float(row[4]), float(row[5])

            dates_str.append(date_s)
            times_str.append(time_s[:8])

            dp = date_s.split("/")
            mo, dy, yr = int(dp[0]), int(dp[1]), int(dp[2])
            date_ints.append(yr * 10000 + mo * 100 + dy)

            opens.append(o)
            highs.append(h)
            lows.append(l)
            closes.append(c)

    n = len(closes)
    return {
        "n": n,
        "date_str": dates_str,
        "time_str": times_str,
        "date_int": np.array(date_ints, dtype=np.int32),
        "high": np.array(highs, dtype=np.float64),
        "low": np.array(lows, dtype=np.float64),
        "close": np.array(closes, dtype=np.float64),
    }


# -- Compute bands --
def compute_bands(bars: dict):
    n = bars["n"]
    c = bars["close"]

    mean = np.full(n, np.nan)
    std = np.full(n, np.nan)

    for i in range(LOOKBACK - 1, n):
        window = c[i - LOOKBACK + 1 : i + 1]
        mean[i] = np.mean(window)
        std[i] = np.std(window, ddof=0)

    return {
        "mean": mean,
        "std": std,
        "inner_top": mean + INNER_MULT * std,
        "inner_bot": mean - INNER_MULT * std,
        "outer_top": mean + OUTER_MULT * std,
        "outer_bot": mean - OUTER_MULT * std,
    }


def is_rth(time_hms: str) -> bool:
    return RTH_START <= time_hms <= RTH_END


def compute_qty(consec_count: int, mult: float, max_qty: int) -> int:
    """Compute martingale qty: floor(1 * mult^consec), capped at max_qty, min 1."""
    raw = math.floor(1.0 * (mult ** consec_count))
    return max(1, min(raw, max_qty))


# -- Simulate --
def simulate(bars: dict, bands: dict, mart_mult: float, mart_max: int):
    """
    Run rangefade rotation sim with N=2 circuit breaker and martingale sizing.
    """
    n = bars["n"]
    date_int = bars["date_int"]
    time_str = bars["time_str"]
    high = bars["high"]
    low = bars["low"]
    close = bars["close"]

    it = bands["inner_top"]
    ib = bands["inner_bot"]
    ot = bands["outer_top"]
    ob = bands["outer_bot"]

    # Position state
    in_position = False
    direction = None
    entry_price = 0.0
    target_price = 0.0
    stop_price = 0.0
    entry_target_offset = 0.0
    entry_stop_offset = 0.0
    trade_qty = 1

    # Consecutive stop state (used for both circuit breaker and martingale)
    consec_long = 0
    consec_short = 0
    last_date = 0

    # Tracking
    trades = []
    daily_pnls = {}
    equity = 0.0
    peak_equity = 0.0
    max_dd = 0.0
    largest_single_loss = 0.0

    def check_buy_signal(idx):
        if np.isnan(it[idx]):
            return False
        return low[idx] <= ib[idx] and low[idx] > ob[idx]

    def check_sell_signal(idx):
        if np.isnan(it[idx]):
            return False
        return high[idx] >= it[idx] and high[idx] < ot[idx]

    def compute_entry(idx, dir_):
        ep = close[idx]
        t_offset = it[idx] - ib[idx]
        if dir_ == "long":
            s_offset = ib[idx] - ob[idx]
            tp = ep + t_offset
            sp = ep - s_offset
        else:
            s_offset = ot[idx] - it[idx]
            tp = ep - t_offset
            sp = ep + s_offset
        return ep, tp, sp, t_offset, s_offset

    def record_pnl(pnl_dollar, exit_bar_date_int):
        nonlocal equity, peak_equity, max_dd
        equity += pnl_dollar
        if equity > peak_equity:
            peak_equity = equity
        dd = peak_equity - equity
        if dd > max_dd:
            max_dd = dd
        if exit_bar_date_int not in daily_pnls:
            daily_pnls[exit_bar_date_int] = 0.0
        daily_pnls[exit_bar_date_int] += pnl_dollar

    def update_consec(side, is_loss):
        nonlocal consec_long, consec_short
        if side == "long":
            if is_loss:
                consec_long += 1
            else:
                consec_long = 0
        else:
            if is_loss:
                consec_short += 1
            else:
                consec_short = 0

    i = LOOKBACK
    while i < n:
        if np.isnan(it[i]):
            i += 1
            continue

        # Daily reset
        today = date_int[i]
        if today != last_date:
            last_date = today
            consec_long = 0
            consec_short = 0

        if not in_position:
            if not is_rth(time_str[i]):
                i += 1
                continue

            buy_sig = check_buy_signal(i)
            sell_sig = check_sell_signal(i)

            dir_to_enter = None
            if buy_sig and sell_sig:
                i += 1
                continue
            elif buy_sig:
                long_blocked = (consec_long >= MAX_CONSEC)
                if long_blocked:
                    i += 1
                    continue
                dir_to_enter = "long"
            elif sell_sig:
                short_blocked = (consec_short >= MAX_CONSEC)
                if short_blocked:
                    i += 1
                    continue
                dir_to_enter = "short"
            else:
                i += 1
                continue

            # Check forced opposite entry
            short_blocked = (consec_short >= MAX_CONSEC)
            long_blocked = (consec_long >= MAX_CONSEC)
            if dir_to_enter == "long" and short_blocked:
                consec_long = 0
                consec_short = 0
            elif dir_to_enter == "short" and long_blocked:
                consec_long = 0
                consec_short = 0

            # Compute martingale qty for the side we're entering
            if dir_to_enter == "long":
                trade_qty = compute_qty(consec_long, mart_mult, mart_max)
            else:
                trade_qty = compute_qty(consec_short, mart_mult, mart_max)

            entry_price, target_price, stop_price, entry_target_offset, entry_stop_offset = compute_entry(i, dir_to_enter)
            direction = dir_to_enter
            in_position = True
            i += 1
            continue

        # -- In position: check resolution --
        if direction == "long":
            target_hit = high[i] >= target_price
            stop_hit = low[i] <= stop_price
            reversal_signal = check_sell_signal(i) and is_rth(time_str[i])
        else:
            target_hit = low[i] <= target_price
            stop_hit = high[i] >= stop_price
            reversal_signal = check_buy_signal(i) and is_rth(time_str[i])

        if target_hit and stop_hit:
            if direction == "long":
                if close[i] >= entry_price:
                    pnl_pts = entry_target_offset
                    exit_type = "TARGET"
                else:
                    pnl_pts = -entry_stop_offset
                    exit_type = "STOP"
            else:
                if close[i] <= entry_price:
                    pnl_pts = entry_target_offset
                    exit_type = "TARGET"
                else:
                    pnl_pts = -entry_stop_offset
                    exit_type = "STOP"

            pnl_dollar = pnl_pts * POINT_VALUE * trade_qty - COST_PER_TRADE * trade_qty
            is_loss = (exit_type == "STOP")
            trades.append({"pnl_dollar": round(pnl_dollar, 2), "qty": trade_qty,
                           "exit_type": exit_type, "date": date_int[i], "dir": direction})
            record_pnl(pnl_dollar, date_int[i])
            if pnl_dollar < largest_single_loss:
                largest_single_loss = pnl_dollar
            update_consec(direction, is_loss)
            in_position = False
            direction = None
            continue

        elif target_hit:
            pnl_pts = entry_target_offset
            pnl_dollar = pnl_pts * POINT_VALUE * trade_qty - COST_PER_TRADE * trade_qty
            trades.append({"pnl_dollar": round(pnl_dollar, 2), "qty": trade_qty,
                           "exit_type": "TARGET", "date": date_int[i], "dir": direction})
            record_pnl(pnl_dollar, date_int[i])
            if pnl_dollar < largest_single_loss:
                largest_single_loss = pnl_dollar
            update_consec(direction, False)
            in_position = False
            direction = None
            continue

        elif stop_hit:
            pnl_pts = -entry_stop_offset
            pnl_dollar = pnl_pts * POINT_VALUE * trade_qty - COST_PER_TRADE * trade_qty
            trades.append({"pnl_dollar": round(pnl_dollar, 2), "qty": trade_qty,
                           "exit_type": "STOP", "date": date_int[i], "dir": direction})
            record_pnl(pnl_dollar, date_int[i])
            if pnl_dollar < largest_single_loss:
                largest_single_loss = pnl_dollar
            update_consec(direction, True)
            in_position = False
            direction = None
            continue

        elif reversal_signal:
            if direction == "long":
                pnl_pts = close[i] - entry_price
                new_direction = "short"
            else:
                pnl_pts = entry_price - close[i]
                new_direction = "long"

            pnl_dollar = pnl_pts * POINT_VALUE * trade_qty - COST_PER_TRADE * trade_qty
            is_loss = (pnl_dollar < 0)
            trades.append({"pnl_dollar": round(pnl_dollar, 2), "qty": trade_qty,
                           "exit_type": "REVERSAL", "date": date_int[i], "dir": direction})
            record_pnl(pnl_dollar, date_int[i])
            if pnl_dollar < largest_single_loss:
                largest_single_loss = pnl_dollar
            update_consec(direction, is_loss)

            # Check if new direction is blocked
            long_blocked = (consec_long >= MAX_CONSEC)
            short_blocked = (consec_short >= MAX_CONSEC)

            if new_direction == "long" and long_blocked:
                in_position = False
                direction = None
                i += 1
                continue
            if new_direction == "short" and short_blocked:
                in_position = False
                direction = None
                i += 1
                continue

            # Check forced opposite reset
            if new_direction == "long" and short_blocked:
                consec_long = 0
                consec_short = 0
            elif new_direction == "short" and long_blocked:
                consec_long = 0
                consec_short = 0

            # Compute qty for new direction
            if new_direction == "long":
                trade_qty = compute_qty(consec_long, mart_mult, mart_max)
            else:
                trade_qty = compute_qty(consec_short, mart_mult, mart_max)

            entry_price, target_price, stop_price, entry_target_offset, entry_stop_offset = compute_entry(i, new_direction)
            direction = new_direction
            in_position = True
            i += 1
            continue

        i += 1

    # -- Compute summary stats --
    total_trades = len(trades)
    total_pnl = sum(t["pnl_dollar"] for t in trades)
    winning_trades = sum(1 for t in trades if t["pnl_dollar"] > 0)
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
    ev_per_trade = (total_pnl / total_trades) if total_trades > 0 else 0.0

    up_days = sum(1 for v in daily_pnls.values() if v > 0)
    down_days = sum(1 for v in daily_pnls.values() if v <= 0)
    total_days = up_days + down_days
    win_day_pct = (up_days / total_days * 100) if total_days > 0 else 0.0

    up_day_vals = [v for v in daily_pnls.values() if v > 0]
    down_day_vals = [v for v in daily_pnls.values() if v <= 0]
    avg_up_day = (sum(up_day_vals) / len(up_day_vals)) if up_day_vals else 0.0
    avg_down_day = (sum(down_day_vals) / len(down_day_vals)) if down_day_vals else 0.0
    worst_day = min(daily_pnls.values()) if daily_pnls else 0.0

    pnl_dd_ratio = (total_pnl / max_dd) if max_dd > 0 else float("inf")

    trades_per_day = (total_trades / total_days) if total_days > 0 else 0.0

    return {
        "total_trades": total_trades,
        "total_pnl": round(total_pnl, 2),
        "up_days": up_days,
        "down_days": down_days,
        "win_day_pct": round(win_day_pct, 1),
        "avg_up_day": round(avg_up_day, 2),
        "avg_down_day": round(avg_down_day, 2),
        "worst_day": round(worst_day, 2),
        "max_drawdown": round(max_dd, 2),
        "pnl_dd_ratio": round(pnl_dd_ratio, 3),
        "win_rate": round(win_rate, 1),
        "ev_per_trade": round(ev_per_trade, 2),
        "largest_single_loss": round(largest_single_loss, 2),
        "trades_per_day": round(trades_per_day, 2),
    }


# -- Main --
def main():
    if not DATA_PATH.exists():
        print(f"ERROR: Cannot find bar data at {DATA_PATH}")
        sys.exit(1)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Loading 250-tick bars from {DATA_PATH}...")
    bars = load_bars(DATA_PATH)
    print(f"Loaded {bars['n']} bars")

    print(f"Computing bands (LB={LOOKBACK}, inner={INNER_MULT}, outer={OUTER_MULT})...")
    bands = compute_bands(bars)

    # -- Run martingale configs --
    results = []
    for label, mult, max_qty in MARTINGALE_CONFIGS:
        print(f"  Simulating {label} (mult={mult}, max={max_qty})...")
        res = simulate(bars, bands, mult, max_qty)
        res["config"] = label
        res["mult"] = mult
        res["max_qty"] = max_qty
        results.append(res)

    # -- Write CSV --
    csv_path = OUTPUT_DIR / "consec-stop-martingale.csv"
    cols = [
        "config", "mult", "max_qty", "total_trades", "total_pnl",
        "up_days", "down_days", "win_day_pct", "avg_up_day", "avg_down_day",
        "worst_day", "max_drawdown", "pnl_dd_ratio", "win_rate", "ev_per_trade",
        "largest_single_loss", "trades_per_day",
    ]
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in results:
            w.writerow({k: r[k] for k in cols})
    print(f"\nWrote CSV to {csv_path}")

    # -- Write summary --
    summary_path = OUTPUT_DIR / "consec-stop-martingale-summary.txt"
    baseline = results[0]

    lines = []
    lines.append("=" * 100)
    lines.append("MARTINGALE SIZING TEST -- RANGEFADE ROTATION (N=2 CIRCUIT BREAKER)")
    lines.append("=" * 100)
    lines.append(f"Config: LB={LOOKBACK}, Inner={INNER_MULT}, Outer={OUTER_MULT}, N={MAX_CONSEC}")
    lines.append(f"Data: {DATA_PATH.name} ({bars['n']} bars)")
    lines.append(f"Cost: ${COST_PER_TRADE}/trade * qty, RTH: 09:30-15:45, ddof=0")
    lines.append("")
    lines.append("Martingale logic:")
    lines.append("  - Base qty = 1")
    lines.append("  - Next trade qty = floor(1 * mult^consecutiveStops), capped at max")
    lines.append("  - Per-side counters (same as circuit breaker)")
    lines.append("  - Win resets counter to 0")
    lines.append("  - Losing reversal increments, winning reversal resets")
    lines.append("  - Daily reset, forced-opposite resets both counters")
    lines.append("")

    # Table
    hdr = (f"{'Config':>16s}  {'Trades':>6s}  {'PnL($)':>10s}  {'Up':>3s}  {'Dn':>3s}  "
           f"{'WinD%':>5s}  {'AvgUp':>9s}  {'AvgDn':>9s}  {'Worst':>9s}  "
           f"{'MaxDD':>9s}  {'PnL/DD':>7s}  {'WR%':>5s}  {'EV/T':>7s}  "
           f"{'MaxLoss':>9s}  {'T/Day':>5s}")
    lines.append(hdr)
    lines.append("-" * len(hdr))

    for r in results:
        line = (f"{r['config']:>16s}  {r['total_trades']:>6d}  {r['total_pnl']:>10.2f}  "
                f"{r['up_days']:>3d}  {r['down_days']:>3d}  {r['win_day_pct']:>5.1f}  "
                f"{r['avg_up_day']:>9.2f}  {r['avg_down_day']:>9.2f}  {r['worst_day']:>9.2f}  "
                f"{r['max_drawdown']:>9.2f}  {r['pnl_dd_ratio']:>7.3f}  "
                f"{r['win_rate']:>5.1f}  {r['ev_per_trade']:>7.2f}  "
                f"{r['largest_single_loss']:>9.2f}  {r['trades_per_day']:>5.2f}")
        lines.append(line)

    lines.append("")
    lines.append("=" * 100)
    lines.append("DELTAS VS BASELINE (no-martingale)")
    lines.append("=" * 100)
    lines.append(f"Baseline: {baseline['total_trades']} trades, PnL=${baseline['total_pnl']:.2f}, "
                 f"MaxDD=${baseline['max_drawdown']:.2f}, PnL/DD={baseline['pnl_dd_ratio']:.3f}")
    lines.append("")

    for r in results[1:]:
        dpnl = r["total_pnl"] - baseline["total_pnl"]
        ddd = r["max_drawdown"] - baseline["max_drawdown"]
        dratio = r["pnl_dd_ratio"] - baseline["pnl_dd_ratio"]
        lines.append(f"  {r['config']:>12s}: PnL {dpnl:+10.2f}, MaxDD {ddd:+9.2f}, "
                     f"PnL/DD {dratio:+7.3f}, Worst day {r['worst_day']:.2f}, "
                     f"Max single loss {r['largest_single_loss']:.2f}")

    lines.append("")
    lines.append("NOTE: With N=2 circuit breaker, the per-side counter can only be 0 or 1")
    lines.append("when entering a trade (at 2 the side is blocked). Therefore:")
    lines.append("  - mult=1.5: floor(1 * 1.5^0)=1, floor(1 * 1.5^1)=1 -> qty always 1 (no effect)")
    lines.append("  - mult=2.0: floor(1 * 2.0^0)=1, floor(1 * 2.0^1)=2 -> qty=2 after 1 consecutive stop")
    lines.append("  - max=2 vs max=3: identical because max possible qty is 2 (consec can only be 0 or 1)")
    lines.append("")

    with open(summary_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Wrote summary to {summary_path}")

    print("\n" + "\n".join(lines))


if __name__ == "__main__":
    main()
