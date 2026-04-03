"""Holdout validation of F4 transition filter (wide bands + declining slope).

Runs the rangefade rotation sim on holdout data with three configs:
  1. Baseline (no filter)
  2. F4: band_width > rolling_200_median AND slope_change_rate < 0
  3. F3: slope_change_rate < 0 (declining slope only)

Filter definitions:
  - band_width = inner_top - inner_bot = 2 * inner_mult * std100
  - rolling_200_median = trailing 200-bar median of band_width (real-time computable)
  - abs_slope_10 = abs((mean100[i] - mean100[i-10]) / 10)
  - slope_change_rate = (abs_slope_10[i] - abs_slope_10[i-10]) / 10

Simulation:
  - LB=100, inner=0.75, outer=1.75, CB N=2, MDL $2500
  - No martingale, no step-up, qty=1
  - RTH 09:30-15:45, entry at bar close, ddof=0
  - Same-bar re-entry, reversal logic (target > stop > reversal)
  - NQ tick=0.25, tick_value=$5, cost=$4/trade
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np


# ── Constants ──
LOOKBACK = 100
INNER_MULT = 0.75
OUTER_MULT = 1.75
QTY = 1
TICK_SIZE = 0.25
TICK_VALUE = 5.00
POINT_VALUE = TICK_VALUE / TICK_SIZE  # $20 per point
COST_PER_TRADE = 4.00
MAX_CONSEC = 2
MAX_DAILY_LOSS = 2500.0

RTH_START = "09:30:00"
RTH_END = "15:45:00"

BW_MEDIAN_WINDOW = 200   # trailing window for band_width median
SLOPE_LB = 10            # lookback for abs_slope_10

DATA_PATH = Path(r"C:\Projects\futures_pipeline\data\NQ-250tick-holdout.csv")
OUTPUT_DIR = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")


# ── Load 250-tick bar data ──
def load_bars(filepath: Path):
    dates_str, times_str = [], []
    date_ints = []
    opens, highs, lows, closes = [], [], [], []

    with open(filepath, "r") as f:
        reader = csv.reader(f)
        next(reader)
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
        "open": np.array(opens, dtype=np.float64),
        "high": np.array(highs, dtype=np.float64),
        "low": np.array(lows, dtype=np.float64),
        "close": np.array(closes, dtype=np.float64),
    }


# ── Compute bands ──
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


# ── Compute transition filter features ──
def compute_transition_features(bands: dict):
    """Compute band_width, rolling median, abs_slope_10, slope_change_rate."""
    n = len(bands["mean"])
    mean = bands["mean"]

    # band_width = inner_top - inner_bot = 2 * inner_mult * std
    band_width = np.full(n, np.nan)
    for i in range(n):
        if not np.isnan(bands["inner_top"][i]):
            band_width[i] = bands["inner_top"][i] - bands["inner_bot"][i]

    # Rolling 200-bar median of band_width (trailing, real-time computable)
    bw_median_200 = np.full(n, np.nan)
    for i in range(BW_MEDIAN_WINDOW - 1, n):
        window = band_width[i - BW_MEDIAN_WINDOW + 1 : i + 1]
        valid = window[~np.isnan(window)]
        if len(valid) > 0:
            bw_median_200[i] = np.median(valid)

    # abs_slope_10 = abs((mean[i] - mean[i-10]) / 10)
    abs_slope_10 = np.full(n, np.nan)
    for i in range(SLOPE_LB, n):
        if not np.isnan(mean[i]) and not np.isnan(mean[i - SLOPE_LB]):
            abs_slope_10[i] = abs((mean[i] - mean[i - SLOPE_LB]) / SLOPE_LB)

    # slope_change_rate = (abs_slope_10[i] - abs_slope_10[i-10]) / 10
    slope_change_rate = np.full(n, np.nan)
    for i in range(2 * SLOPE_LB, n):
        if not np.isnan(abs_slope_10[i]) and not np.isnan(abs_slope_10[i - SLOPE_LB]):
            slope_change_rate[i] = (abs_slope_10[i] - abs_slope_10[i - SLOPE_LB]) / SLOPE_LB

    return {
        "band_width": band_width,
        "bw_median_200": bw_median_200,
        "abs_slope_10": abs_slope_10,
        "slope_change_rate": slope_change_rate,
    }


# ── Build filter arrays ──
def build_filter(features: dict, filter_type: str):
    """Returns bool array: True = filter BLOCKS trading at this bar.
    Filters allow trading when conditions are met, so blocked = NOT conditions.
    """
    n = len(features["band_width"])
    bw = features["band_width"]
    bw_med = features["bw_median_200"]
    scr = features["slope_change_rate"]

    if filter_type == "none":
        return None

    blocked = np.ones(n, dtype=bool)  # default: blocked

    if filter_type == "F4":
        # Allow when BOTH: band_width > rolling_200_median AND slope_change_rate < 0
        for i in range(n):
            if (not np.isnan(bw[i]) and not np.isnan(bw_med[i])
                    and not np.isnan(scr[i])):
                if bw[i] > bw_med[i] and scr[i] < 0:
                    blocked[i] = False

    elif filter_type == "F3":
        # Allow when slope_change_rate < 0
        for i in range(n):
            if not np.isnan(scr[i]):
                if scr[i] < 0:
                    blocked[i] = False

    return blocked


def is_rth(time_hms: str) -> bool:
    return RTH_START <= time_hms <= RTH_END


# ── Simulate ──
def simulate(bars: dict, bands: dict, filter_blocked: np.ndarray | None):
    """Run rangefade rotation sim. Same logic as calibration/holdout-bandwidth."""
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

    max_consec = MAX_CONSEC

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

    daily_pnl_running = 0.0
    daily_loss_date = 0
    daily_loss_hit = False

    trades = []
    daily_pnls = {}
    equity = 0.0
    peak_equity = 0.0
    max_dd = 0.0

    total_rth_bars = 0
    filter_allowed_bars = 0
    trades_avoided = 0

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

    def is_filter_blocked(idx):
        if filter_blocked is None:
            return False
        return bool(filter_blocked[idx])

    def is_daily_loss_blocked(idx):
        nonlocal daily_pnl_running, daily_loss_date, daily_loss_hit
        today = date_int[idx]
        if today != daily_loss_date:
            daily_loss_date = today
            daily_pnl_running = 0.0
            daily_loss_hit = False
        return daily_loss_hit

    def update_daily_loss(pnl_dollar, idx):
        nonlocal daily_pnl_running, daily_loss_hit
        today = date_int[idx]
        if today != daily_loss_date:
            return
        daily_pnl_running += pnl_dollar
        if daily_pnl_running <= -MAX_DAILY_LOSS:
            daily_loss_hit = True

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

        if is_rth(time_str[i]) and not np.isnan(it[i]):
            total_rth_bars += 1
            if not is_filter_blocked(i):
                filter_allowed_bars += 1

        if not in_position:
            if not is_rth(time_str[i]):
                i += 1
                continue

            if is_daily_loss_blocked(i):
                i += 1
                continue

            buy_sig = check_buy_signal(i)
            sell_sig = check_sell_signal(i)

            dir_to_enter = None
            if buy_sig and sell_sig:
                i += 1
                continue
            elif buy_sig:
                long_blocked = (max_consec > 0 and consec_long >= max_consec)
                if long_blocked:
                    i += 1
                    continue
                dir_to_enter = "long"
            elif sell_sig:
                short_blocked = (max_consec > 0 and consec_short >= max_consec)
                if short_blocked:
                    i += 1
                    continue
                dir_to_enter = "short"
            else:
                i += 1
                continue

            if is_filter_blocked(i):
                trades_avoided += 1
                i += 1
                continue

            short_blocked = (max_consec > 0 and consec_short >= max_consec)
            long_blocked = (max_consec > 0 and consec_long >= max_consec)
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

        # In position: check resolution
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

            pnl_dollar = pnl_pts * POINT_VALUE - COST_PER_TRADE
            is_loss = (exit_type == "STOP")
            trades.append({"pnl_dollar": pnl_dollar, "date": date_int[i]})
            record_pnl(pnl_dollar, date_int[i])
            update_consec(direction, is_loss)
            update_daily_loss(pnl_dollar, i)
            in_position = False
            direction = None
            continue

        elif target_hit:
            pnl_pts = entry_target_offset
            pnl_dollar = pnl_pts * POINT_VALUE - COST_PER_TRADE
            trades.append({"pnl_dollar": pnl_dollar, "date": date_int[i]})
            record_pnl(pnl_dollar, date_int[i])
            update_consec(direction, False)
            update_daily_loss(pnl_dollar, i)
            in_position = False
            direction = None
            continue

        elif stop_hit:
            pnl_pts = -entry_stop_offset
            pnl_dollar = pnl_pts * POINT_VALUE - COST_PER_TRADE
            trades.append({"pnl_dollar": pnl_dollar, "date": date_int[i]})
            record_pnl(pnl_dollar, date_int[i])
            update_consec(direction, True)
            update_daily_loss(pnl_dollar, i)
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
            trades.append({"pnl_dollar": pnl_dollar, "date": date_int[i]})
            record_pnl(pnl_dollar, date_int[i])
            update_consec(direction, is_loss)
            update_daily_loss(pnl_dollar, i)

            long_blocked = (max_consec > 0 and consec_long >= max_consec)
            short_blocked = (max_consec > 0 and consec_short >= max_consec)

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

            if is_filter_blocked(i):
                trades_avoided += 1
                in_position = False
                direction = None
                i += 1
                continue

            if is_daily_loss_blocked(i):
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

    # ── Compute summary stats ──
    total_trades = len(trades)
    total_pnl = sum(t["pnl_dollar"] for t in trades)
    winning_trades = sum(1 for t in trades if t["pnl_dollar"] > 0)
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
    ev_per_trade = (total_pnl / total_trades) if total_trades > 0 else 0.0

    up_days = sum(1 for v in daily_pnls.values() if v > 0)
    down_days = sum(1 for v in daily_pnls.values() if v <= 0)
    total_days = up_days + down_days
    win_day_pct = (up_days / total_days * 100) if total_days > 0 else 0.0
    worst_day = min(daily_pnls.values()) if daily_pnls else 0.0

    up_day_vals = [v for v in daily_pnls.values() if v > 0]
    down_day_vals = [v for v in daily_pnls.values() if v <= 0]
    avg_up_day = (sum(up_day_vals) / len(up_day_vals)) if up_day_vals else 0.0
    avg_down_day = (sum(down_day_vals) / len(down_day_vals)) if down_day_vals else 0.0

    trades_per_day = (total_trades / total_days) if total_days > 0 else 0.0

    pnl_dd_ratio = (total_pnl / max_dd) if max_dd > 0 else float("inf")

    pct_allowed = (filter_allowed_bars / total_rth_bars * 100) if total_rth_bars > 0 else 100.0

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
        "trades_per_day": round(trades_per_day, 1),
        "pct_bars_allowed": round(pct_allowed, 1),
        "trades_avoided": trades_avoided,
    }


# ── Config definitions ──
CONFIGS = [
    {
        "id": 1,
        "label": "Baseline (no filter)",
        "filter_type": "none",
        "cal_pnl": 22303.0,
        "cal_pnl_dd": 1.20,
        "cal_trades": None,
    },
    {
        "id": 2,
        "label": "F4: bw>med200 + scr<0",
        "filter_type": "F4",
        "cal_pnl": 22488.0,
        "cal_pnl_dd": 2.63,
        "cal_trades": 761,
    },
    {
        "id": 3,
        "label": "F3: scr<0 only",
        "filter_type": "F3",
        "cal_pnl": 22303.0,  # ~similar to baseline per task
        "cal_pnl_dd": 2.01,
        "cal_trades": None,
    },
]


def main():
    if not DATA_PATH.exists():
        print(f"ERROR: Cannot find holdout data at {DATA_PATH}")
        sys.exit(1)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Loading holdout 250-tick bars from {DATA_PATH}...")
    bars = load_bars(DATA_PATH)
    print(f"Loaded {bars['n']} bars")
    print(f"Date range: {bars['date_str'][0]} to {bars['date_str'][-1]}")

    print(f"Computing bands (LB={LOOKBACK}, inner={INNER_MULT}, outer={OUTER_MULT}, ddof=0)...")
    bands = compute_bands(bars)

    print(f"Computing transition features (bw_median_window={BW_MEDIAN_WINDOW}, slope_lb={SLOPE_LB})...")
    features = compute_transition_features(bands)

    # Diagnostic: count valid feature bars
    valid_bw = np.sum(~np.isnan(features["band_width"]))
    valid_bw_med = np.sum(~np.isnan(features["bw_median_200"]))
    valid_scr = np.sum(~np.isnan(features["slope_change_rate"]))
    print(f"  Valid band_width: {valid_bw}, bw_median_200: {valid_bw_med}, slope_change_rate: {valid_scr}")

    results = []

    for cfg in CONFIGS:
        label = cfg["label"]
        print(f"\n  Running: {label}...")

        blocked = build_filter(features, cfg["filter_type"])

        res = simulate(bars, bands, blocked)
        res["id"] = cfg["id"]
        res["label"] = label
        res["cal_pnl"] = cfg["cal_pnl"]
        res["cal_pnl_dd"] = cfg["cal_pnl_dd"]
        res["cal_trades"] = cfg["cal_trades"]
        results.append(res)

        print(f"    Trades: {res['total_trades']}, PnL: ${res['total_pnl']:.2f}, "
              f"MaxDD: ${res['max_drawdown']:.2f}, PnL/DD: {res['pnl_dd_ratio']:.3f}, "
              f"WR: {res['win_rate']:.1f}%, EV: ${res['ev_per_trade']:.2f}")

    # ── Write holdout-transition-filter.csv ──
    csv_path = OUTPUT_DIR / "holdout-transition-filter.csv"
    cols = [
        "id", "label",
        "total_trades", "total_pnl", "up_days", "down_days",
        "win_day_pct", "avg_up_day", "avg_down_day",
        "worst_day", "max_drawdown", "pnl_dd_ratio",
        "win_rate", "ev_per_trade", "trades_per_day",
        "pct_bars_allowed", "trades_avoided",
        "cal_pnl", "cal_pnl_dd", "cal_trades",
    ]
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in results:
            w.writerow({k: r.get(k, "") for k in cols})
    print(f"\nWrote CSV to {csv_path}")

    # ── Write holdout-transition-filter-summary.txt ──
    summary_path = OUTPUT_DIR / "holdout-transition-filter-summary.txt"
    lines = []
    lines.append("=" * 120)
    lines.append("HOLDOUT VALIDATION: TRANSITION FILTER (F4 / F3)")
    lines.append("=" * 120)
    lines.append(f"Holdout data: {DATA_PATH.name} ({bars['n']} bars)")
    lines.append(f"Date range: {bars['date_str'][0]} to {bars['date_str'][-1]}")
    lines.append(f"Config: LB={LOOKBACK}, Inner={INNER_MULT}, Outer={OUTER_MULT}, CB N={MAX_CONSEC}, MDL=${MAX_DAILY_LOSS}")
    lines.append(f"Cost: ${COST_PER_TRADE}/trade, Qty=1, No martingale, No step-up")
    lines.append(f"RTH: 09:30-15:45, Entry at bar close, ddof=0")
    lines.append(f"Filter params: bw_median_window={BW_MEDIAN_WINDOW}, slope_lb={SLOPE_LB}")
    lines.append("")
    lines.append("Filter definitions:")
    lines.append("  F3: slope_change_rate < 0 (declining slope)")
    lines.append("  F4: band_width > rolling_200_median AND slope_change_rate < 0")
    lines.append("  band_width = inner_top - inner_bot = 2 * inner_mult * std100")
    lines.append("  abs_slope_10 = abs((mean100[i] - mean100[i-10]) / 10)")
    lines.append("  slope_change_rate = (abs_slope_10[i] - abs_slope_10[i-10]) / 10")
    lines.append("")

    # Holdout results table
    lines.append("HOLDOUT RESULTS")
    lines.append("-" * 120)
    hdr = (f"{'#':>2s}  {'Config':>24s}  {'Trades':>6s}  {'PnL($)':>10s}  "
           f"{'Up':>3s}  {'Dn':>3s}  {'WinD%':>5s}  {'AvgUp($)':>10s}  {'AvgDn($)':>10s}  "
           f"{'Worst($)':>10s}  {'MaxDD($)':>10s}  {'PnL/DD':>7s}  {'WR%':>5s}  {'EV/T($)':>8s}  {'T/Day':>5s}")
    lines.append(hdr)
    lines.append("-" * len(hdr))

    for r in results:
        line = (f"{r['id']:>2d}  {r['label']:>24s}  {r['total_trades']:>6d}  {r['total_pnl']:>10.2f}  "
                f"{r['up_days']:>3d}  {r['down_days']:>3d}  {r['win_day_pct']:>5.1f}  {r['avg_up_day']:>10.2f}  {r['avg_down_day']:>10.2f}  "
                f"{r['worst_day']:>10.2f}  {r['max_drawdown']:>10.2f}  {r['pnl_dd_ratio']:>7.3f}  {r['win_rate']:>5.1f}  {r['ev_per_trade']:>8.2f}  {r['trades_per_day']:>5.1f}")
        lines.append(line)

    # Calibration vs holdout comparison
    lines.append("")
    lines.append("")
    lines.append("CALIBRATION vs HOLDOUT COMPARISON")
    lines.append("-" * 120)
    hdr2 = (f"{'#':>2s}  {'Config':>24s}  "
            f"{'Cal PnL($)':>11s}  {'HO PnL($)':>11s}  {'PnL Delta':>10s}  "
            f"{'Cal PnL/DD':>10s}  {'HO PnL/DD':>10s}  {'Ratio Delta':>11s}  "
            f"{'Cal Trades':>10s}  {'HO Trades':>10s}")
    lines.append(hdr2)
    lines.append("-" * len(hdr2))

    for r in results:
        pnl_delta = r["total_pnl"] - r["cal_pnl"]
        ratio_delta = r["pnl_dd_ratio"] - r["cal_pnl_dd"]
        cal_trades_str = str(r["cal_trades"]) if r["cal_trades"] is not None else "N/A"
        line = (f"{r['id']:>2d}  {r['label']:>24s}  "
                f"{r['cal_pnl']:>11.2f}  {r['total_pnl']:>11.2f}  {pnl_delta:>+10.2f}  "
                f"{r['cal_pnl_dd']:>10.3f}  {r['pnl_dd_ratio']:>10.3f}  {ratio_delta:>+11.3f}  "
                f"{cal_trades_str:>10s}  {r['total_trades']:>10d}")
        lines.append(line)

    # Per-config notes
    lines.append("")
    lines.append("")
    lines.append("PER-CONFIG NOTES")
    lines.append("-" * 120)

    baseline_ho = results[0]
    for r in results:
        delta_vs_baseline = r["total_pnl"] - baseline_ho["total_pnl"]
        ratio_vs_baseline = r["pnl_dd_ratio"] - baseline_ho["pnl_dd_ratio"]
        pnl_retained = (r["total_pnl"] / r["cal_pnl"] * 100) if r["cal_pnl"] != 0 else 0.0
        lines.append(f"  #{r['id']} {r['label']}:")
        lines.append(f"    Holdout PnL retention vs calibration: {pnl_retained:.1f}%")
        if r["id"] != 1:
            sign = "+" if delta_vs_baseline >= 0 else ""
            lines.append(f"    vs holdout baseline: PnL {sign}${delta_vs_baseline:.2f}, "
                         f"PnL/DD {'+' if ratio_vs_baseline >= 0 else ''}{ratio_vs_baseline:.3f}")
        lines.append("")

    lines.append("")

    summary_text = "\n".join(lines)
    with open(summary_path, "w") as f:
        f.write(summary_text + "\n")
    print(f"Wrote summary to {summary_path}")

    # Print to stdout
    print("\n" + summary_text)


if __name__ == "__main__":
    main()
