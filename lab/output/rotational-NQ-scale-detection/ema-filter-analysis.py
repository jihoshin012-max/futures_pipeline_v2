"""EMA-based trade filter grid search for rangefade rotation.

Tests whether requiring the EMA to be inside the inner bands before
allowing new entries improves the rangefade rotation strategy.

Base config (validated): LB=100, inner=0.75, outer=1.75, N=2 circuit breaker
EMA filter: only allow new entries when innerBot + threshold*bw <= EMA <= innerTop - threshold*bw

Grid: EMA periods [20,30,50,75,100,150] x thresholds [0.0,0.1,0.25,0.5] + baseline
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

RTH_START = "09:30:00"
RTH_END = "15:45:00"

MAX_CONSEC = 2  # Circuit breaker N=2

EMA_PERIODS = [20, 30, 50, 75, 100, 150]
EMA_THRESHOLDS = [0.0, 0.1, 0.25, 0.5]

DATA_PATH = Path(r"C:\Projects\futures_pipeline\data\NQ-250tick-calibration.csv")
OUTPUT_DIR = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")


# ── Load 250-tick bar data ──
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


# ── Compute EMA ──
def compute_ema(close: np.ndarray, period: int) -> np.ndarray:
    """Exponential moving average. First value = SMA of first `period` bars."""
    n = len(close)
    ema = np.full(n, np.nan)
    if n < period:
        return ema

    # Seed with SMA
    ema[period - 1] = np.mean(close[:period])
    multiplier = 2.0 / (period + 1)

    for i in range(period, n):
        ema[i] = close[i] * multiplier + ema[i - 1] * (1 - multiplier)

    return ema


def is_rth(time_hms: str) -> bool:
    return RTH_START <= time_hms <= RTH_END


# ── Simulate with EMA filter ──
def simulate(bars: dict, bands: dict, ema: np.ndarray | None,
             ema_threshold: float):
    """
    Run the rangefade rotation sim with N=2 circuit breaker and optional EMA filter.

    ema=None means baseline (no EMA filter).
    ema_threshold: how far inside bands the EMA must be (as fraction of band width).
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

    max_consec = MAX_CONSEC

    # Position state
    in_position = False
    direction = None
    entry_price = 0.0
    target_price = 0.0
    stop_price = 0.0
    entry_target_offset = 0.0
    entry_stop_offset = 0.0

    # Consecutive stop state
    consec_long = 0
    consec_short = 0
    last_date = 0

    # Tracking
    trades = []
    daily_pnls = {}
    equity = 0.0
    peak_equity = 0.0
    max_dd = 0.0

    # EMA filter stats
    total_rth_bars_with_bands = 0
    ema_allowed_bars = 0

    def check_buy_signal(idx):
        if np.isnan(it[idx]):
            return False
        return low[idx] <= ib[idx] and low[idx] > ob[idx]

    def check_sell_signal(idx):
        if np.isnan(it[idx]):
            return False
        return high[idx] >= it[idx] and high[idx] < ot[idx]

    def ema_allows_entry(idx):
        """Check if EMA is within the inner bands (with threshold)."""
        if ema is None:
            return True
        if np.isnan(ema[idx]) or np.isnan(it[idx]):
            return False
        band_width = it[idx] - ib[idx]
        lower_bound = ib[idx] + ema_threshold * band_width
        upper_bound = it[idx] - ema_threshold * band_width
        return lower_bound <= ema[idx] <= upper_bound

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

        # Track EMA filter stats for RTH bars with valid bands
        if is_rth(time_str[i]) and not np.isnan(it[i]):
            total_rth_bars_with_bands += 1
            if ema_allows_entry(i):
                ema_allowed_bars += 1

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

            # EMA filter: block entry if EMA is outside inner bands
            if not ema_allows_entry(i):
                i += 1
                continue

            # Check forced opposite reset
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

        # ── In position: check resolution ──
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
            trades.append({"date": date_int[i], "pnl_dollar": round(pnl_dollar, 2)})
            record_pnl(pnl_dollar, date_int[i])
            update_consec(direction, is_loss)
            in_position = False
            direction = None
            continue  # same-bar re-entry

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

            # Check if new direction is blocked by circuit breaker
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

            # EMA filter on reversal entry
            if not ema_allows_entry(i):
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

    up_day_vals = [v for v in daily_pnls.values() if v > 0]
    down_day_vals = [v for v in daily_pnls.values() if v <= 0]
    avg_up_day = (sum(up_day_vals) / len(up_day_vals)) if up_day_vals else 0.0
    avg_down_day = (sum(down_day_vals) / len(down_day_vals)) if down_day_vals else 0.0
    worst_day = min(daily_pnls.values()) if daily_pnls else 0.0

    pnl_dd_ratio = (total_pnl / max_dd) if max_dd > 0 else float("inf")
    trades_per_day = (total_trades / total_days) if total_days > 0 else 0.0

    filter_active_pct = (ema_allowed_bars / total_rth_bars_with_bands * 100) if total_rth_bars_with_bands > 0 else 100.0

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
        "filter_active_pct": round(filter_active_pct, 1),
    }


# ── Main ──
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

    # Pre-compute all EMAs
    emas = {}
    for period in EMA_PERIODS:
        emas[period] = compute_ema(bars["close"], period)

    # ── Run grid ──
    results = []

    # Baseline: no EMA filter
    print("  Simulating baseline (no EMA filter, N=2)...")
    res = simulate(bars, bands, None, 0.0)
    res["ema_period"] = "none"
    res["ema_threshold"] = "none"
    res["config"] = "baseline"
    results.append(res)

    # Grid
    for period in EMA_PERIODS:
        for threshold in EMA_THRESHOLDS:
            label = f"EMA={period}, thr={threshold}"
            print(f"  Simulating {label}...")
            res = simulate(bars, bands, emas[period], threshold)
            res["ema_period"] = period
            res["ema_threshold"] = threshold
            res["config"] = f"EMA{period}_thr{threshold}"
            results.append(res)

    # Separate zero-trade configs, sort remainder by PnL/DD descending
    results_with_trades = [r for r in results if r["total_trades"] > 0]
    results_no_trades = [r for r in results if r["total_trades"] == 0]
    results_sorted = sorted(results_with_trades, key=lambda r: r["pnl_dd_ratio"], reverse=True)

    # ── Write grid CSV ──
    csv_path = OUTPUT_DIR / "ema-filter-grid.csv"
    cols = [
        "config", "ema_period", "ema_threshold",
        "total_trades", "total_pnl", "up_days", "down_days",
        "win_day_pct", "avg_up_day", "avg_down_day", "worst_day",
        "max_drawdown", "pnl_dd_ratio", "win_rate", "ev_per_trade",
        "trades_per_day", "filter_active_pct",
    ]
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in results_sorted:
            w.writerow({k: r[k] for k in cols})
        for r in results_no_trades:
            w.writerow({k: r[k] for k in cols})
    print(f"\nWrote grid CSV to {csv_path}")

    # ── Write summary ──
    summary_path = OUTPUT_DIR / "ema-filter-summary.txt"
    baseline = [r for r in results if r["config"] == "baseline"][0]

    lines = []
    lines.append("=" * 110)
    lines.append("EMA FILTER GRID SEARCH — RANGEFADE ROTATION")
    lines.append("=" * 110)
    lines.append(f"Config: LB={LOOKBACK}, Inner={INNER_MULT}, Outer={OUTER_MULT}, Circuit breaker N={MAX_CONSEC}")
    lines.append(f"Data: {DATA_PATH.name} ({bars['n']} bars)")
    lines.append(f"Cost: ${COST_PER_TRADE}/trade, Qty=1, No martingale, No step-up")
    lines.append(f"RTH: 09:30-15:45, Entry at bar close, ddof=0")
    lines.append("")
    lines.append("EMA filter logic:")
    lines.append("  - Compute EMA of Close with given period")
    lines.append("  - Before any new entry (fresh or reversal), check:")
    lines.append("    innerBot + threshold*bandWidth <= EMA <= innerTop - threshold*bandWidth")
    lines.append("  - threshold=0.0: EMA just inside inner bands")
    lines.append("  - threshold=0.5: EMA at band midpoint (most restrictive)")
    lines.append("  - Existing positions continue to target/stop/reversal unaffected")
    lines.append("  - Circuit breaker N=2 still applies on top of EMA filter")
    lines.append("")

    # Table
    hdr = (f"{'Config':>18s}  {'Trades':>6s}  {'PnL($)':>10s}  {'Up':>3s}  {'Dn':>3s}  "
           f"{'WinD%':>5s}  {'AvgUp':>8s}  {'AvgDn':>8s}  {'Worst':>9s}  "
           f"{'MaxDD':>9s}  {'PnL/DD':>7s}  {'WR%':>5s}  {'EV/T':>7s}  "
           f"{'T/Day':>5s}  {'Filt%':>5s}")
    lines.append(hdr)
    lines.append("-" * len(hdr))

    for r in results_sorted:
        line = (f"{r['config']:>18s}  {r['total_trades']:>6d}  {r['total_pnl']:>10.2f}  "
                f"{r['up_days']:>3d}  {r['down_days']:>3d}  {r['win_day_pct']:>5.1f}  "
                f"{r['avg_up_day']:>8.2f}  {r['avg_down_day']:>8.2f}  {r['worst_day']:>9.2f}  "
                f"{r['max_drawdown']:>9.2f}  {r['pnl_dd_ratio']:>7.3f}  "
                f"{r['win_rate']:>5.1f}  {r['ev_per_trade']:>7.2f}  "
                f"{r['trades_per_day']:>5.2f}  {r['filter_active_pct']:>5.1f}")
        lines.append(line)

    lines.append("")
    lines.append("=" * 110)
    lines.append("ANALYSIS")
    lines.append("=" * 110)

    lines.append(f"Baseline (no EMA filter, N=2): {baseline['total_trades']} trades, "
                 f"PnL=${baseline['total_pnl']:.2f}, "
                 f"MaxDD=${baseline['max_drawdown']:.2f}, "
                 f"PnL/DD={baseline['pnl_dd_ratio']:.3f}, "
                 f"WR={baseline['win_rate']:.1f}%, "
                 f"EV/T=${baseline['ev_per_trade']:.2f}")
    lines.append("")

    # Find best by various metrics (exclude zero-trade configs)
    active = [r for r in results if r["config"] != "baseline" and r["total_trades"] > 0]
    if active:
        best_ratio = max(active, key=lambda r: r["pnl_dd_ratio"])
        best_pnl = max(active, key=lambda r: r["total_pnl"])
        best_ev = max(active, key=lambda r: r["ev_per_trade"])
        best_wr = max(active, key=lambda r: r["win_rate"])
        least_dd = min(active, key=lambda r: r["max_drawdown"])

        lines.append(f"Best PnL/DD:      {best_ratio['config']} "
                     f"({best_ratio['pnl_dd_ratio']:.3f}, "
                     f"baseline: {baseline['pnl_dd_ratio']:.3f}, "
                     f"delta: {best_ratio['pnl_dd_ratio'] - baseline['pnl_dd_ratio']:+.3f})")
        lines.append(f"Best total PnL:   {best_pnl['config']} "
                     f"(${best_pnl['total_pnl']:.2f}, "
                     f"delta: ${best_pnl['total_pnl'] - baseline['total_pnl']:+.2f})")
        lines.append(f"Best EV/trade:    {best_ev['config']} "
                     f"(${best_ev['ev_per_trade']:.2f}, "
                     f"baseline: ${baseline['ev_per_trade']:.2f})")
        lines.append(f"Best win rate:    {best_wr['config']} "
                     f"({best_wr['win_rate']:.1f}%, "
                     f"baseline: {baseline['win_rate']:.1f}%)")
        lines.append(f"Lowest MaxDD:     {least_dd['config']} "
                     f"(${least_dd['max_drawdown']:.2f}, "
                     f"baseline: ${baseline['max_drawdown']:.2f})")
        lines.append("")

        # Deltas vs baseline (skip zero-trade configs)
        lines.append("Deltas vs baseline:")
        for r in results_sorted:
            if r["config"] == "baseline" or r["total_trades"] == 0:
                continue
            dpnl = r["total_pnl"] - baseline["total_pnl"]
            dtrades = r["total_trades"] - baseline["total_trades"]
            ddd = r["max_drawdown"] - baseline["max_drawdown"]
            dratio = r["pnl_dd_ratio"] - baseline["pnl_dd_ratio"]
            lines.append(f"  {r['config']:>18s}: PnL {dpnl:+10.2f}, "
                         f"Trades {dtrades:+5d}, "
                         f"MaxDD {ddd:+9.2f}, "
                         f"PnL/DD {dratio:+7.3f}, "
                         f"Filter allows {r['filter_active_pct']:5.1f}% of bars")

        # Count how many configs beat baseline (only those with trades)
        lines.append("")
        beat_pnl = sum(1 for r in active if r["total_pnl"] > baseline["total_pnl"])
        beat_ratio = sum(1 for r in active if r["pnl_dd_ratio"] > baseline["pnl_dd_ratio"])
        beat_ev = sum(1 for r in active if r["ev_per_trade"] > baseline["ev_per_trade"])
        lines.append(f"Configs with trades: {len(active)}/24")
        lines.append(f"  Beating baseline on PnL:    {beat_pnl}/{len(active)}")
        lines.append(f"  Beating baseline on PnL/DD: {beat_ratio}/{len(active)}")
        lines.append(f"  Beating baseline on EV/T:   {beat_ev}/{len(active)}")
        lines.append("")
        zero_trade = sum(1 for r in results if r["config"] != "baseline" and r["total_trades"] == 0)
        if zero_trade > 0:
            lines.append(f"Note: {zero_trade} configs (threshold=0.5) produced 0 trades — filter too restrictive.")

    lines.append("")

    with open(summary_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Wrote summary to {summary_path}")

    # Print to stdout
    print("\n" + "\n".join(lines))


if __name__ == "__main__":
    main()
