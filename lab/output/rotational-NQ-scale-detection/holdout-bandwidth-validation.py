"""Holdout validation of top bandwidth filter configs.

Runs the same rangefade rotation simulation used in calibration
(bandwidth-filter-analysis.py) on the holdout period data
(NQ-250tick-holdout.csv, 12/15/2025 to 3/13/2026).

Configs tested:
  1. Baseline (no filter)
  2. StdDev > 50
  3. StdDev > 40
  4. Range/BW > 0.5
  5. Pctl tw=200 p=80
  6. Pctl tw=200 p=85
  7. Pctl tw=500 p=90
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

import numpy as np


# ── Constants (identical to calibration script) ──
LOOKBACK = 100
INNER_MULT = 0.75
OUTER_MULT = 1.75
QTY = 1
TICK_SIZE = 0.25
TICK_VALUE = 5.00
POINT_VALUE = TICK_VALUE / TICK_SIZE  # $20 per point
COST_PER_TRADE = 4.00
MAX_CONSEC = 2  # main circuit breaker N=2
MAX_DAILY_LOSS = 2500.0

RTH_START = "09:30:00"
RTH_END = "15:45:00"

DATA_PATH = Path(r"C:\Projects\futures_pipeline\data\NQ-250tick-holdout.csv")
OUTPUT_DIR = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")


# ── Load 250-tick bar data ──
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
        "open": np.array(opens, dtype=np.float64),
        "high": np.array(highs, dtype=np.float64),
        "low": np.array(lows, dtype=np.float64),
        "close": np.array(closes, dtype=np.float64),
    }


# ── Compute bands ──
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


# ── Filter computations ──
def compute_percentile_rank_filter(std_arr, trailing_window, pct_threshold):
    """Returns bool array: True = filter BLOCKS trading at this bar."""
    n = len(std_arr)
    blocked = np.zeros(n, dtype=bool)
    for i in range(trailing_window, n):
        if np.isnan(std_arr[i]):
            continue
        history = std_arr[i - trailing_window : i]
        valid = history[~np.isnan(history)]
        if len(valid) == 0:
            continue
        rank = np.sum(valid < std_arr[i]) / len(valid) * 100.0
        if rank > pct_threshold:
            blocked[i] = True
    return blocked


def compute_raw_std_filter(std_arr, threshold_pts):
    """Returns bool array: True = filter BLOCKS trading."""
    blocked = np.zeros(len(std_arr), dtype=bool)
    for i in range(len(std_arr)):
        if not np.isnan(std_arr[i]) and std_arr[i] > threshold_pts:
            blocked[i] = True
    return blocked


def compute_range_ratio_filter(bars, bands, ratio_threshold):
    """Returns bool array: True = filter BLOCKS trading.
    ratio = rolling 10-bar avg of (H-L) / inner band width
    """
    n = bars["n"]
    h = bars["high"]
    l = bars["low"]
    it = bands["inner_top"]
    ib = bands["inner_bot"]

    bar_range = h - l
    blocked = np.zeros(n, dtype=bool)

    for i in range(10, n):
        if np.isnan(it[i]) or np.isnan(ib[i]):
            continue
        band_width = it[i] - ib[i]
        if band_width <= 0:
            continue
        avg_range = np.mean(bar_range[i - 9 : i + 1])
        ratio = avg_range / band_width
        if ratio > ratio_threshold:
            blocked[i] = True
    return blocked


def is_rth(time_hms: str) -> bool:
    return RTH_START <= time_hms <= RTH_END


# ── Simulate ──
def simulate(bars: dict, bands: dict, filter_blocked: np.ndarray | None):
    """Run rangefade rotation sim. Identical logic to calibration script."""
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

    # Summary stats
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

    pnl_dd_ratio = (total_pnl / max_dd) if max_dd > 0 else float("inf")

    pct_allowed = (filter_allowed_bars / total_rth_bars * 100) if total_rth_bars > 0 else 100.0

    return {
        "total_trades": total_trades,
        "total_pnl": round(total_pnl, 2),
        "up_days": up_days,
        "down_days": down_days,
        "win_day_pct": round(win_day_pct, 1),
        "worst_day": round(worst_day, 2),
        "max_drawdown": round(max_dd, 2),
        "pnl_dd_ratio": round(pnl_dd_ratio, 3),
        "win_rate": round(win_rate, 1),
        "ev_per_trade": round(ev_per_trade, 2),
        "pct_bars_allowed": round(pct_allowed, 1),
        "trades_avoided": trades_avoided,
    }


# ── Config definitions ──
CONFIGS = [
    {
        "id": 1,
        "label": "Baseline (no filter)",
        "filter_type": "none",
        "cal_pnl": 33042.95,
        "cal_pnl_dd": 2.129,
    },
    {
        "id": 2,
        "label": "StdDev > 50",
        "filter_type": "raw_std",
        "threshold": 50,
        "cal_pnl": 35256.77,
        "cal_pnl_dd": 2.950,
    },
    {
        "id": 3,
        "label": "StdDev > 40",
        "filter_type": "raw_std",
        "threshold": 40,
        "cal_pnl": 16184.18,
        "cal_pnl_dd": 1.150,
    },
    {
        "id": 4,
        "label": "Range/BW > 0.5",
        "filter_type": "range_ratio",
        "threshold": 0.5,
        "cal_pnl": 31728.60,
        "cal_pnl_dd": 2.674,
    },
    {
        "id": 5,
        "label": "Pctl tw=200 p=80",
        "filter_type": "percentile",
        "tw": 200,
        "pct": 80,
        "cal_pnl": 35770.35,
        "cal_pnl_dd": 2.603,
    },
    {
        "id": 6,
        "label": "Pctl tw=200 p=85",
        "filter_type": "percentile",
        "tw": 200,
        "pct": 85,
        "cal_pnl": 22204.57,
        "cal_pnl_dd": 1.259,
    },
    {
        "id": 7,
        "label": "Pctl tw=500 p=90",
        "filter_type": "percentile",
        "tw": 500,
        "pct": 90,
        "cal_pnl": 29014.30,
        "cal_pnl_dd": 1.917,
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

    print(f"Computing bands (LB={LOOKBACK}, inner={INNER_MULT}, outer={OUTER_MULT})...")
    bands = compute_bands(bars)
    std_arr = bands["std"]

    results = []

    for cfg in CONFIGS:
        label = cfg["label"]
        print(f"\n  Running: {label}...")

        if cfg["filter_type"] == "none":
            blocked = None
        elif cfg["filter_type"] == "raw_std":
            blocked = compute_raw_std_filter(std_arr, cfg["threshold"])
        elif cfg["filter_type"] == "range_ratio":
            blocked = compute_range_ratio_filter(bars, bands, cfg["threshold"])
        elif cfg["filter_type"] == "percentile":
            blocked = compute_percentile_rank_filter(std_arr, cfg["tw"], cfg["pct"])
        else:
            raise ValueError(f"Unknown filter type: {cfg['filter_type']}")

        res = simulate(bars, bands, blocked)
        res["id"] = cfg["id"]
        res["label"] = label
        res["cal_pnl"] = cfg["cal_pnl"]
        res["cal_pnl_dd"] = cfg["cal_pnl_dd"]
        results.append(res)

        print(f"    Trades: {res['total_trades']}, PnL: ${res['total_pnl']:.2f}, "
              f"MaxDD: ${res['max_drawdown']:.2f}, PnL/DD: {res['pnl_dd_ratio']:.3f}")

    # ── Write holdout-bandwidth.csv ──
    csv_path = OUTPUT_DIR / "holdout-bandwidth.csv"
    cols = [
        "id", "label",
        "total_trades", "total_pnl", "up_days", "down_days",
        "win_day_pct", "worst_day", "max_drawdown", "pnl_dd_ratio",
        "win_rate", "ev_per_trade", "pct_bars_allowed", "trades_avoided",
        "cal_pnl", "cal_pnl_dd",
    ]
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in results:
            w.writerow({k: r[k] for k in cols})
    print(f"\nWrote CSV to {csv_path}")

    # ── Write holdout-bandwidth-summary.txt ──
    summary_path = OUTPUT_DIR / "holdout-bandwidth-summary.txt"
    lines = []
    lines.append("=" * 120)
    lines.append("HOLDOUT VALIDATION: BANDWIDTH FILTER CONFIGS")
    lines.append("=" * 120)
    lines.append(f"Holdout data: {DATA_PATH.name} ({bars['n']} bars)")
    lines.append(f"Date range: {bars['date_str'][0]} to {bars['date_str'][-1]}")
    lines.append(f"Config: LB={LOOKBACK}, Inner={INNER_MULT}, Outer={OUTER_MULT}, CB N={MAX_CONSEC}, MDL=${MAX_DAILY_LOSS}")
    lines.append(f"Cost: ${COST_PER_TRADE}/trade, Qty=1, No martingale, No step-up")
    lines.append(f"RTH: 09:30-15:45, Entry at bar close, ddof=0")
    lines.append("")

    # Holdout results table
    lines.append("HOLDOUT RESULTS")
    lines.append("-" * 120)
    hdr = (f"{'#':>2s}  {'Config':>22s}  {'Trades':>6s}  {'PnL($)':>10s}  "
           f"{'Up':>3s}  {'Dn':>3s}  {'WinD%':>5s}  {'Worst($)':>10s}  "
           f"{'MaxDD($)':>10s}  {'PnL/DD':>7s}  {'WR%':>5s}  {'EV/T($)':>8s}  "
           f"{'%Allow':>6s}  {'Avoid':>5s}")
    lines.append(hdr)
    lines.append("-" * len(hdr))

    for r in results:
        line = (f"{r['id']:>2d}  {r['label']:>22s}  {r['total_trades']:>6d}  {r['total_pnl']:>10.2f}  "
                f"{r['up_days']:>3d}  {r['down_days']:>3d}  {r['win_day_pct']:>5.1f}  {r['worst_day']:>10.2f}  "
                f"{r['max_drawdown']:>10.2f}  {r['pnl_dd_ratio']:>7.3f}  {r['win_rate']:>5.1f}  {r['ev_per_trade']:>8.2f}  "
                f"{r['pct_bars_allowed']:>6.1f}  {r['trades_avoided']:>5d}")
        lines.append(line)

    # Calibration vs holdout comparison
    lines.append("")
    lines.append("")
    lines.append("CALIBRATION vs HOLDOUT COMPARISON")
    lines.append("-" * 120)
    hdr2 = (f"{'#':>2s}  {'Config':>22s}  "
            f"{'Cal PnL($)':>11s}  {'HO PnL($)':>11s}  {'PnL Delta':>10s}  "
            f"{'Cal PnL/DD':>10s}  {'HO PnL/DD':>10s}  {'Ratio Delta':>11s}")
    lines.append(hdr2)
    lines.append("-" * len(hdr2))

    for r in results:
        pnl_delta = r["total_pnl"] - r["cal_pnl"]
        ratio_delta = r["pnl_dd_ratio"] - r["cal_pnl_dd"]
        line = (f"{r['id']:>2d}  {r['label']:>22s}  "
                f"{r['cal_pnl']:>11.2f}  {r['total_pnl']:>11.2f}  {pnl_delta:>+10.2f}  "
                f"{r['cal_pnl_dd']:>10.3f}  {r['pnl_dd_ratio']:>10.3f}  {ratio_delta:>+11.3f}")
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

    with open(summary_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Wrote summary to {summary_path}")

    # Print to stdout
    print("\n" + "\n".join(lines))


if __name__ == "__main__":
    main()
