"""Holdout validation of top time-of-day filters for rangefade rotation.

Runs four configs on NQ-250tick-holdout.csv (12/15/2025 to 3/13/2026)
and compares against calibration results.

Base config: LB=100, inner=0.75, outer=1.75, CB N=2, MDL $2500
No martingale, no step-up, qty=1
Entry at bar close, ddof=0
NQ tick_size=0.25, tick_value=$5.00, cost=$4/trade

Time filters only block NEW entries. Existing positions continue
to target/stop/reversal regardless of time.
Reversal signal checks both is_rth AND is_entry_allowed.
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np


LOOKBACK = 100
INNER_MULT = 0.75
OUTER_MULT = 1.75
QTY = 1
TICK_SIZE = 0.25
TICK_VALUE = 5.00
POINT_VALUE = TICK_VALUE / TICK_SIZE
COST_PER_TRADE = 4.00
MAX_CONSEC = 2
MAX_DAILY_LOSS = 2500.0

RTH_START = "09:30:00"
RTH_END = "15:45:00"

DATA_PATH = Path("C:/Projects/futures_pipeline/data/NQ-250tick-holdout.csv")
OUTPUT_DIR = Path("C:/Projects/futures_pipeline/lab/output/rotational-NQ-scale-detection")

CONFIGS = {
    "baseline": {
        "label": "Baseline: full RTH 09:30-15:45",
        "windows": [("09:30:00", "15:45:00")],
        "cal_pnl": 33043,
        "cal_pnl_dd": 2.13,
        "cal_trades": 2685,
    },
    "skip_lunch": {
        "label": "Skip lunch: no entries 12:00-13:00",
        "windows": [("09:30:00", "12:00:00"), ("13:00:00", "15:45:00")],
        "cal_pnl": 38976,
        "cal_pnl_dd": 2.87,
        "cal_trades": 2451,
    },
    "afternoon": {
        "label": "Afternoon only: 13:00-15:45",
        "windows": [("13:00:00", "15:45:00")],
        "cal_pnl": 22341,
        "cal_pnl_dd": 5.28,
        "cal_trades": 974,
    },
    "skip_open_lunch": {
        "label": "Skip open+lunch: 10:00-12:00, 13:00-15:45",
        "windows": [("10:00:00", "12:00:00"), ("13:00:00", "15:45:00")],
        "cal_pnl": 26938,
        "cal_pnl_dd": 2.30,
        "cal_trades": 1968,
    },
}


def load_bars(filepath: Path):
    """Load SC 250-tick bar CSV. Returns arrays."""
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


def compute_bands(bars: dict):
    """Rolling mean/std (ddof=0) of close, then inner/outer bands."""
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


def is_rth(time_hms):
    return RTH_START <= time_hms <= RTH_END


def is_entry_allowed(time_hms, entry_windows):
    for start, end in entry_windows:
        if start <= time_hms <= end:
            return True
    return False


def simulate(bars, bands, entry_windows):
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

    in_position = False
    direction = None
    entry_price = 0.0
    target_price = 0.0
    stop_price = 0.0
    entry_target_offset = 0.0
    entry_stop_offset = 0.0
    consec_long = 0
    consec_short = 0
    last_date = 0
    daily_loss_today = 0.0
    mdl_halted = False
    trades = []
    daily_pnls = {}
    equity = 0.0
    peak_equity = 0.0
    max_dd = 0.0

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
        nonlocal equity, peak_equity, max_dd, daily_loss_today, mdl_halted
        equity += pnl_dollar
        if equity > peak_equity:
            peak_equity = equity
        dd = peak_equity - equity
        if dd > max_dd:
            max_dd = dd
        if exit_bar_date_int not in daily_pnls:
            daily_pnls[exit_bar_date_int] = 0.0
        daily_pnls[exit_bar_date_int] += pnl_dollar
        daily_loss_today += pnl_dollar
        if daily_loss_today <= -MAX_DAILY_LOSS:
            mdl_halted = True

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
        today = date_int[i]
        if today != last_date:
            last_date = today
            consec_long = 0
            consec_short = 0
            daily_loss_today = 0.0
            mdl_halted = False

        if not in_position:
            if not is_rth(time_str[i]):
                i += 1
                continue
            if mdl_halted:
                i += 1
                continue
            if not is_entry_allowed(time_str[i], entry_windows):
                i += 1
                continue
            buy_sig = check_buy_signal(i)
            sell_sig = check_sell_signal(i)
            dir_to_enter = None
            if buy_sig and sell_sig:
                i += 1
                continue
            elif buy_sig:
                if consec_long >= MAX_CONSEC:
                    i += 1
                    continue
                dir_to_enter = "long"
            elif sell_sig:
                if consec_short >= MAX_CONSEC:
                    i += 1
                    continue
                dir_to_enter = "short"
            else:
                i += 1
                continue
            short_blocked = (consec_short >= MAX_CONSEC)
            long_blocked = (consec_long >= MAX_CONSEC)
            if dir_to_enter == "long" and short_blocked:
                consec_long = 0
                consec_short = 0
            elif dir_to_enter == "short" and long_blocked:
                consec_long = 0
                consec_short = 0
            entry_price, target_price, stop_price, entry_target_offset, entry_stop_offset = compute_entry(i, dir_to_enter)
            direction = dir_to_enter
            in_position = True
            i += 1
            continue

        # In position: resolve regardless of time filter
        if direction == "long":
            target_hit = high[i] >= target_price
            stop_hit = low[i] <= stop_price
            reversal_signal = check_sell_signal(i) and is_rth(time_str[i]) and is_entry_allowed(time_str[i], entry_windows)
        else:
            target_hit = low[i] <= target_price
            stop_hit = high[i] >= stop_price
            reversal_signal = check_buy_signal(i) and is_rth(time_str[i]) and is_entry_allowed(time_str[i], entry_windows)

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
            pnl_dollar = pnl_pts * POINT_VALUE - COST_PER_TRADE
            is_loss = (exit_type == "STOP")
            trades.append({"date": date_int[i], "pnl_dollar": round(pnl_dollar, 2)})
            record_pnl(pnl_dollar, date_int[i])
            update_consec(direction, is_loss)
            in_position = False
            direction = None
            continue

        elif target_hit:
            pnl_pts = entry_target_offset
            pnl_dollar = pnl_pts * POINT_VALUE - COST_PER_TRADE
            trades.append({"date": date_int[i], "pnl_dollar": round(pnl_dollar, 2)})
            record_pnl(pnl_dollar, date_int[i])
            update_consec(direction, False)
            in_position = False
            direction = None
            continue

        elif stop_hit:
            pnl_pts = -entry_stop_offset
            pnl_dollar = pnl_pts * POINT_VALUE - COST_PER_TRADE
            trades.append({"date": date_int[i], "pnl_dollar": round(pnl_dollar, 2)})
            record_pnl(pnl_dollar, date_int[i])
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
            pnl_dollar = pnl_pts * POINT_VALUE - COST_PER_TRADE
            is_loss = (pnl_dollar < 0)
            trades.append({"date": date_int[i], "pnl_dollar": round(pnl_dollar, 2)})
            record_pnl(pnl_dollar, date_int[i])
            update_consec(direction, is_loss)
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
            if mdl_halted:
                in_position = False
                direction = None
                i += 1
                continue
            if not is_entry_allowed(time_str[i], entry_windows):
                in_position = False
                direction = None
                i += 1
                continue
            if new_direction == "long" and short_blocked:
                consec_long = 0
                consec_short = 0
            elif new_direction == "short" and long_blocked:
                consec_long = 0
                consec_short = 0
            entry_price, target_price, stop_price, entry_target_offset, entry_stop_offset = compute_entry(i, new_direction)
            direction = new_direction
            in_position = True
            i += 1
            continue

        i += 1

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
    trades_per_day = (total_trades / total_days) if total_days > 0 else 0.0
    pnl_dd_ratio = (total_pnl / max_dd) if max_dd > 0 else float("inf")
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
        "trades_per_day": round(trades_per_day, 2),
    }


def main():
    if not DATA_PATH.exists():
        print("ERROR: Cannot find bar data at", DATA_PATH)
        sys.exit(1)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    print("Loading 250-tick bars from", DATA_PATH, "...")
    bars = load_bars(DATA_PATH)
    nbar = bars["n"]
    print("Loaded", nbar, "bars")
    print("Computing bands (LB=%d, inner=%.2f, outer=%.2f)..." % (LOOKBACK, INNER_MULT, OUTER_MULT))
    bands = compute_bands(bars)

    results = []
    for config_id, cfg in CONFIGS.items():
        label = cfg["label"]
        print("  Simulating", label, "...")
        res = simulate(bars, bands, cfg["windows"])
        res["config_id"] = config_id
        res["label"] = label
        res["cal_pnl"] = cfg["cal_pnl"]
        res["cal_pnl_dd"] = cfg["cal_pnl_dd"]
        res["cal_trades"] = cfg["cal_trades"]
        results.append(res)

    # Write CSV
    csv_path = OUTPUT_DIR / "holdout-time-filter.csv"
    cols = [
        "config_id", "label", "total_trades", "total_pnl", "up_days", "down_days",
        "win_day_pct", "avg_up_day", "avg_down_day", "worst_day",
        "max_drawdown", "pnl_dd_ratio", "win_rate", "ev_per_trade", "trades_per_day",
    ]
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in results:
            w.writerow(r)
    print("\nWrote CSV to", csv_path)

    # Write summary
    summary_path = OUTPUT_DIR / "holdout-time-filter-summary.txt"
    lines = []
    lines.append("=" * 120)
    lines.append("HOLDOUT VALIDATION: TIME-OF-DAY FILTERS")
    lines.append("=" * 120)
    lines.append("Config: LB=%d, Inner=%.2f, Outer=%.2f, CB N=%d, MDL=$%d" % (LOOKBACK, INNER_MULT, OUTER_MULT, MAX_CONSEC, MAX_DAILY_LOSS))
    lines.append("Data: %s (%d bars)" % (DATA_PATH.name, nbar))
    lines.append("Period: 12/15/2025 - 3/13/2026 (holdout)")
    lines.append("Cost: $%.0f/trade, Qty=1, No martingale, No step-up" % COST_PER_TRADE)
    lines.append("RTH: 09:30-15:45, Entry at bar close, ddof=0")
    lines.append("")
    lines.append("Time filters block NEW entries only. Existing positions")
    lines.append("resolve (target/stop/reversal) regardless of time.")
    lines.append("Reversal signal checks both is_rth AND is_entry_allowed.")
    lines.append("")

    # Holdout results table
    lines.append("-" * 120)
    lines.append("HOLDOUT RESULTS")
    lines.append("-" * 120)
    hdr = "%-18s  %6s  %10s  %3s  %3s  %5s  %8s  %8s  %9s  %9s  %7s  %5s  %7s  %5s" % (
        "Config", "Trades", "PnL($)", "Up", "Dn", "WinD%", "AvgUp", "AvgDn",
        "Worst", "MaxDD", "PnL/DD", "WR%", "EV/T", "T/Day")
    lines.append(hdr)
    lines.append("-" * len(hdr))

    for r in results:
        line = "%-18s  %6d  %10.2f  %3d  %3d  %5.1f  %8.2f  %8.2f  %9.2f  %9.2f  %7.3f  %5.1f  %7.2f  %5.2f" % (
            r["config_id"], r["total_trades"], r["total_pnl"],
            r["up_days"], r["down_days"], r["win_day_pct"],
            r["avg_up_day"], r["avg_down_day"], r["worst_day"],
            r["max_drawdown"], r["pnl_dd_ratio"],
            r["win_rate"], r["ev_per_trade"], r["trades_per_day"])
        lines.append(line)

    # Calibration vs holdout comparison
    lines.append("")
    lines.append("-" * 120)
    lines.append("CALIBRATION vs HOLDOUT COMPARISON")
    lines.append("-" * 120)
    lines.append("")
    comp_hdr = "%-18s  %10s  %10s  %10s  %7s  %7s  %7s  %7s  %7s  %7s" % (
        "Config", "Cal PnL", "HO PnL", "Delta PnL",
        "Cal P/D", "HO P/D", "D P/D",
        "Cal Tr", "HO Tr", "D Tr")
    lines.append(comp_hdr)
    lines.append("-" * len(comp_hdr))

    for r in results:
        dpnl = r["total_pnl"] - r["cal_pnl"]
        dpd = r["pnl_dd_ratio"] - r["cal_pnl_dd"]
        dtr = r["total_trades"] - r["cal_trades"]
        line = "%-18s  %10d  %10.2f  %+10.2f  %7.2f  %7.3f  %+7.3f  %7d  %7d  %+7d" % (
            r["config_id"], r["cal_pnl"], r["total_pnl"], dpnl,
            r["cal_pnl_dd"], r["pnl_dd_ratio"], dpd,
            r["cal_trades"], r["total_trades"], dtr)
        lines.append(line)

    lines.append("")

    NL = chr(10)
    with open(summary_path, "w") as f:
        f.write(NL.join(lines) + NL)
    print("Wrote summary to", summary_path)
    print(NL + NL.join(lines))


if __name__ == "__main__":
    main()
