"""Consecutive-stop circuit breaker grid search for rangefade rotation.

Simulates the rangefade rotation algo (LB=100, inner=0.75, outer=1.75)
on 250-tick NQ bars with varying consecutive-stop thresholds.

Logic matches rotational-NQ-study-rangefade-v3.cpp and the validated
multiplier-grid-analysis.py simulation:

  While in a position, each bar is checked (in order):
    1. Target hit? (high >= targetPrice for long, low <= targetPrice for short)
    2. Stop hit? (low <= stopPrice for long, high >= stopPrice for short)
    3. Reversal signal? (opposite-side entry signal while in position)

  Priority: target > stop > reversal on same bar.
  If target AND stop both hit: close on winning side = target, else stop.

  After any exit (target, stop, or reversal):
    - Stop or losing reversal: increment that side's consec counter
    - Target or winning reversal: reset that side's counter to 0
    - Counters reset daily

  Circuit breaker:
    - When one side reaches N consecutive stops, block that side
    - Only opposite side can enter
    - When opposite side entry fires while a side is blocked: reset BOTH to 0
    - One-shot circuit breaker per v3 code

  Same-bar re-entry: after resolution (target/stop), re-check same bar
  for a new entry signal before advancing.

Config: LB=100, inner=0.75, outer=1.75, qty=1, no martingale, no step-up
RTH: 09:30-15:45, entry at bar close, ddof=0
NQ tick_size=0.25, tick_value=$5.00, cost=$4/trade
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

THRESHOLDS = [0, 2, 3, 4, 5, 7, 10]

DATA_PATH = Path(r"C:\Projects\futures_pipeline\data\NQ-250tick-calibration.csv")
OUTPUT_DIR = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")


# ── Load 250-tick bar data ──
def load_bars(filepath: Path):
    """Load SC 250-tick bar CSV. Returns arrays."""
    dates_str, times_str = [], []
    date_ints, time_secs = [], []
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
            # Extract HH:MM:SS for RTH comparison
            times_str.append(time_s[:8])

            # Parse date -> int YYYYMMDD for daily grouping
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


def is_rth(time_hms: str) -> bool:
    """Check if bar time is within RTH window (09:30:00 - 15:45:00 inclusive)."""
    return RTH_START <= time_hms <= RTH_END


# ── Simulate with given max_consec_stops ──
def simulate(bars: dict, bands: dict, max_consec: int,
             single_day_date: str | None = None):
    """
    Run the rangefade rotation sim with a given consecutive-stop threshold.
    max_consec=0 means disabled (baseline).

    If single_day_date is provided (e.g. "10/10/2025"), also returns a
    detailed trade log for that date.

    Returns (summary_dict, trades_list, single_day_log_or_None).
    """
    n = bars["n"]
    date_int = bars["date_int"]
    date_str = bars["date_str"]
    time_str = bars["time_str"]
    high = bars["high"]
    low = bars["low"]
    close = bars["close"]

    it = bands["inner_top"]
    ib = bands["inner_bot"]
    ot = bands["outer_top"]
    ob = bands["outer_bot"]
    sd = bands["std"]

    # Position state
    in_position = False
    direction = None  # "long" or "short"
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
    daily_pnls = {}  # date_int -> total pnl ($)
    cb_fired_count = 0
    trades_blocked = 0
    forced_opposite = 0

    equity = 0.0
    peak_equity = 0.0
    max_dd = 0.0

    # Single day log
    single_day_log = [] if single_day_date is not None else None

    def check_buy_signal(idx):
        if np.isnan(it[idx]):
            return False
        return low[idx] <= ib[idx] and low[idx] > ob[idx]

    def check_sell_signal(idx):
        if np.isnan(it[idx]):
            return False
        return high[idx] >= it[idx] and high[idx] < ot[idx]

    def compute_entry(idx, dir_):
        """Compute entry price, target, stop for a new position."""
        ep = close[idx]
        t_offset = it[idx] - ib[idx]  # target offset = inner band width
        if dir_ == "long":
            s_offset = ib[idx] - ob[idx]  # stop offset = inner-to-outer distance
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

    def update_consec(side, is_loss, entry_was_forced_opposite):
        """Update consecutive stop counters after an exit.
        side: "long" or "short" (the side that was exited)
        is_loss: True if the exit was a loss (stop or losing reversal)
        """
        nonlocal consec_long, consec_short, cb_fired_count
        if side == "long":
            if is_loss:
                consec_long += 1
                if max_consec > 0 and consec_long == max_consec:
                    cb_fired_count += 1
            else:
                consec_long = 0
        else:
            if is_loss:
                consec_short += 1
                if max_consec > 0 and consec_short == max_consec:
                    cb_fired_count += 1
            else:
                consec_short = 0

    i = LOOKBACK  # start after valid rolling stats
    while i < n:
        # Need valid bands
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
            # ── Not in position: check for entry ──
            if not is_rth(time_str[i]):
                i += 1
                continue

            buy_sig = check_buy_signal(i)
            sell_sig = check_sell_signal(i)

            # Determine which signal to act on
            dir_to_enter = None
            if buy_sig and sell_sig:
                # Both signals: skip (ambiguous)
                i += 1
                continue
            elif buy_sig:
                long_blocked = (max_consec > 0 and consec_long >= max_consec)
                if long_blocked:
                    trades_blocked += 1
                    i += 1
                    continue
                dir_to_enter = "long"
            elif sell_sig:
                short_blocked = (max_consec > 0 and consec_short >= max_consec)
                if short_blocked:
                    trades_blocked += 1
                    i += 1
                    continue
                dir_to_enter = "short"
            else:
                i += 1
                continue

            # Check if opposite side was blocked -> forced opposite entry
            short_blocked = (max_consec > 0 and consec_short >= max_consec)
            long_blocked = (max_consec > 0 and consec_long >= max_consec)
            was_forced = False
            if dir_to_enter == "long" and short_blocked:
                was_forced = True
                consec_long = 0
                consec_short = 0
                forced_opposite += 1
            elif dir_to_enter == "short" and long_blocked:
                was_forced = True
                consec_long = 0
                consec_short = 0
                forced_opposite += 1

            entry_price, target_price, stop_price, entry_target_offset, entry_stop_offset = compute_entry(i, dir_to_enter)
            direction = dir_to_enter
            in_position = True
            entry_bar = i
            i += 1
            continue

        # ── In position: check bar i for resolution ──
        if direction == "long":
            target_hit = high[i] >= target_price
            stop_hit = low[i] <= stop_price
            # Reversal: sell signal while long, RTH only
            sell_reversal = check_sell_signal(i) and is_rth(time_str[i])
            reversal_signal = sell_reversal
        else:
            target_hit = low[i] <= target_price
            stop_hit = high[i] >= stop_price
            buy_reversal = check_buy_signal(i) and is_rth(time_str[i])
            reversal_signal = buy_reversal

        # Priority resolution
        if target_hit and stop_hit:
            # Both hit on same bar: use close to determine
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
            trades.append({
                "date": date_int[i], "dir": direction, "entry": entry_price,
                "exit_price": entry_price + pnl_pts if direction == "long" else entry_price - pnl_pts,
                "pnl_dollar": round(pnl_dollar, 2), "exit_type": exit_type,
                "bar_idx": i,
            })
            record_pnl(pnl_dollar, date_int[i])
            update_consec(direction, is_loss, False)

            if single_day_log is not None and date_str[i].strip() == single_day_date:
                single_day_log.append({
                    "time": time_str[i], "direction": direction,
                    "entry_price": round(entry_price, 2),
                    "exit_type": exit_type,
                    "pnl": round(pnl_dollar, 2),
                    "consec_long": consec_long, "consec_short": consec_short,
                    "long_blocked": int(max_consec > 0 and consec_long >= max_consec),
                    "short_blocked": int(max_consec > 0 and consec_short >= max_consec),
                })

            in_position = False
            direction = None
            # Same-bar re-entry: don't advance i
            continue

        elif target_hit:
            # Target takes priority over reversal
            pnl_pts = entry_target_offset
            pnl_dollar = pnl_pts * POINT_VALUE - COST_PER_TRADE
            exit_price = target_price
            trades.append({
                "date": date_int[i], "dir": direction, "entry": entry_price,
                "exit_price": exit_price, "pnl_dollar": round(pnl_dollar, 2),
                "exit_type": "TARGET", "bar_idx": i,
            })
            record_pnl(pnl_dollar, date_int[i])
            update_consec(direction, False, False)

            if single_day_log is not None and date_str[i].strip() == single_day_date:
                single_day_log.append({
                    "time": time_str[i], "direction": direction,
                    "entry_price": round(entry_price, 2),
                    "exit_type": "TARGET",
                    "pnl": round(pnl_dollar, 2),
                    "consec_long": consec_long, "consec_short": consec_short,
                    "long_blocked": int(max_consec > 0 and consec_long >= max_consec),
                    "short_blocked": int(max_consec > 0 and consec_short >= max_consec),
                })

            in_position = False
            direction = None
            # Same-bar re-entry
            continue

        elif stop_hit:
            # Stop takes priority over reversal
            pnl_pts = -entry_stop_offset
            pnl_dollar = pnl_pts * POINT_VALUE - COST_PER_TRADE
            exit_price = stop_price
            trades.append({
                "date": date_int[i], "dir": direction, "entry": entry_price,
                "exit_price": exit_price, "pnl_dollar": round(pnl_dollar, 2),
                "exit_type": "STOP", "bar_idx": i,
            })
            record_pnl(pnl_dollar, date_int[i])
            update_consec(direction, True, False)

            if single_day_log is not None and date_str[i].strip() == single_day_date:
                single_day_log.append({
                    "time": time_str[i], "direction": direction,
                    "entry_price": round(entry_price, 2),
                    "exit_type": "STOP",
                    "pnl": round(pnl_dollar, 2),
                    "consec_long": consec_long, "consec_short": consec_short,
                    "long_blocked": int(max_consec > 0 and consec_long >= max_consec),
                    "short_blocked": int(max_consec > 0 and consec_short >= max_consec),
                })

            in_position = False
            direction = None
            # Same-bar re-entry
            continue

        elif reversal_signal:
            # Reversal: exit current position at close, enter opposite
            if direction == "long":
                pnl_pts = close[i] - entry_price
                new_direction = "short"
            else:
                pnl_pts = entry_price - close[i]
                new_direction = "long"

            pnl_dollar = pnl_pts * POINT_VALUE - COST_PER_TRADE
            is_loss = (pnl_dollar < 0)
            trades.append({
                "date": date_int[i], "dir": direction, "entry": entry_price,
                "exit_price": close[i], "pnl_dollar": round(pnl_dollar, 2),
                "exit_type": "REVERSAL", "bar_idx": i,
            })
            record_pnl(pnl_dollar, date_int[i])
            update_consec(direction, is_loss, False)

            if single_day_log is not None and date_str[i].strip() == single_day_date:
                single_day_log.append({
                    "time": time_str[i], "direction": direction,
                    "entry_price": round(entry_price, 2),
                    "exit_type": "REVERSAL",
                    "pnl": round(pnl_dollar, 2),
                    "consec_long": consec_long, "consec_short": consec_short,
                    "long_blocked": int(max_consec > 0 and consec_long >= max_consec),
                    "short_blocked": int(max_consec > 0 and consec_short >= max_consec),
                })

            # Recompute blocked after consec update
            long_blocked = (max_consec > 0 and consec_long >= max_consec)
            short_blocked = (max_consec > 0 and consec_short >= max_consec)

            # Check if new direction is blocked
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
            was_forced = False
            if new_direction == "long" and short_blocked:
                was_forced = True
                consec_long = 0
                consec_short = 0
                forced_opposite += 1
            elif new_direction == "short" and long_blocked:
                was_forced = True
                consec_long = 0
                consec_short = 0
                forced_opposite += 1

            # Enter opposite direction immediately
            entry_price, target_price, stop_price, entry_target_offset, entry_stop_offset = compute_entry(i, new_direction)
            direction = new_direction
            in_position = True
            entry_bar = i
            i += 1
            continue

        # No resolution this bar
        i += 1

    # ── Compute summary stats ──
    total_trades = len(trades)
    total_pnl = sum(t["pnl_dollar"] for t in trades)
    winning_trades = sum(1 for t in trades if t["pnl_dollar"] > 0)
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
    ev_per_trade = (total_pnl / total_trades) if total_trades > 0 else 0.0

    # Daily stats
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

    return {
        "threshold": max_consec,
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
        "cb_fired": cb_fired_count,
        "trades_blocked": trades_blocked,
        "forced_opposite": forced_opposite,
    }, trades, single_day_log


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

    # ── Run grid ──
    results = []
    for threshold in THRESHOLDS:
        label = f"N={threshold}" if threshold > 0 else "N=0 (disabled)"
        print(f"  Simulating {label}...")
        res, _, _ = simulate(bars, bands, threshold)
        results.append(res)

    # ── Write grid CSV ──
    csv_path = OUTPUT_DIR / "consec-stop-grid.csv"
    cols = [
        "threshold", "total_trades", "total_pnl", "up_days", "down_days",
        "win_day_pct", "avg_up_day", "avg_down_day", "worst_day",
        "max_drawdown", "pnl_dd_ratio", "win_rate", "ev_per_trade",
        "cb_fired", "trades_blocked", "forced_opposite",
    ]
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in results:
            w.writerow(r)
    print(f"\nWrote grid CSV to {csv_path}")

    # ── Single day log for 10/10/2025 with N=3 ──
    print("\nGenerating single-day trade log for 10/10/2025 (N=3)...")
    _, _, single_day = simulate(bars, bands, 3, single_day_date="10/10/2025")

    single_day_path = OUTPUT_DIR / "consec-stop-single-day-10102025.csv"
    if single_day:
        sd_cols = ["time", "direction", "entry_price", "exit_type", "pnl",
                   "consec_long", "consec_short", "long_blocked", "short_blocked"]
        with open(single_day_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=sd_cols)
            w.writeheader()
            for row in single_day:
                w.writerow(row)
        print(f"Wrote single-day log ({len(single_day)} trades) to {single_day_path}")
    else:
        print("No trades found on 10/10/2025 with N=3.")

    # ── Write summary ──
    summary_path = OUTPUT_DIR / "consec-stop-summary.txt"
    baseline = results[0]  # N=0

    lines = []
    lines.append("=" * 90)
    lines.append("CONSECUTIVE-STOP CIRCUIT BREAKER GRID SEARCH")
    lines.append("(with correct target/stop resolution)")
    lines.append("=" * 90)
    lines.append(f"Config: LB={LOOKBACK}, Inner={INNER_MULT}, Outer={OUTER_MULT}")
    lines.append(f"Data: {DATA_PATH.name} ({bars['n']} bars)")
    lines.append(f"Cost: ${COST_PER_TRADE}/trade, Qty=1, No martingale, No step-up")
    lines.append(f"RTH: 09:30-15:45, Entry at bar close, ddof=0")
    lines.append("")
    lines.append("Simulation logic:")
    lines.append("  - Target/stop checked every bar while in position")
    lines.append("  - Priority: target > stop > reversal")
    lines.append("  - Same-bar target+stop: close determines winner")
    lines.append("  - Same-bar re-entry after target/stop resolution")
    lines.append("  - Reversal: exit at close, enter opposite at close")
    lines.append("  - Circuit breaker: block side at N consecutive stops,")
    lines.append("    reset BOTH counters when opposite side fires")
    lines.append("")

    # Table header
    hdr = (f"{'N':>4s}  {'Trades':>6s}  {'PnL($)':>10s}  {'Up':>3s}  {'Dn':>3s}  "
           f"{'WinD%':>5s}  {'AvgUp':>8s}  {'AvgDn':>8s}  {'Worst':>9s}  "
           f"{'MaxDD':>9s}  {'PnL/DD':>7s}  {'WR%':>5s}  {'EV/T':>7s}  "
           f"{'CBFire':>6s}  {'Block':>5s}  {'Force':>5s}")
    lines.append(hdr)
    lines.append("-" * len(hdr))

    for r in results:
        label = str(r["threshold"]) if r["threshold"] > 0 else "OFF"
        line = (f"{label:>4s}  {r['total_trades']:>6d}  {r['total_pnl']:>10.2f}  "
                f"{r['up_days']:>3d}  {r['down_days']:>3d}  {r['win_day_pct']:>5.1f}  "
                f"{r['avg_up_day']:>8.2f}  {r['avg_down_day']:>8.2f}  {r['worst_day']:>9.2f}  "
                f"{r['max_drawdown']:>9.2f}  {r['pnl_dd_ratio']:>7.3f}  "
                f"{r['win_rate']:>5.1f}  {r['ev_per_trade']:>7.2f}  "
                f"{r['cb_fired']:>6d}  {r['trades_blocked']:>5d}  {r['forced_opposite']:>5d}")
        lines.append(line)

    lines.append("")
    lines.append("=" * 90)
    lines.append("ANALYSIS")
    lines.append("=" * 90)

    active = [r for r in results if r["threshold"] > 0]
    if active:
        best_pnl = max(active, key=lambda r: r["total_pnl"])
        best_ratio = max(active, key=lambda r: r["pnl_dd_ratio"])
        best_ev = max(active, key=lambda r: r["ev_per_trade"])

        lines.append(f"Baseline (N=OFF): {baseline['total_trades']} trades, "
                     f"PnL=${baseline['total_pnl']:.2f}, "
                     f"MaxDD=${baseline['max_drawdown']:.2f}, "
                     f"PnL/DD={baseline['pnl_dd_ratio']:.3f}")
        lines.append("")

        lines.append(f"Best total PnL:   N={best_pnl['threshold']} "
                     f"(${best_pnl['total_pnl']:.2f}, "
                     f"delta vs baseline: ${best_pnl['total_pnl'] - baseline['total_pnl']:+.2f})")
        lines.append(f"Best PnL/DD:      N={best_ratio['threshold']} "
                     f"({best_ratio['pnl_dd_ratio']:.3f}, "
                     f"baseline: {baseline['pnl_dd_ratio']:.3f})")
        lines.append(f"Best EV/trade:    N={best_ev['threshold']} "
                     f"(${best_ev['ev_per_trade']:.2f}, "
                     f"baseline: ${baseline['ev_per_trade']:.2f})")
        lines.append("")

        lines.append("Deltas vs baseline (N=OFF):")
        for r in active:
            dpnl = r["total_pnl"] - baseline["total_pnl"]
            dtrades = r["total_trades"] - baseline["total_trades"]
            ddd = r["max_drawdown"] - baseline["max_drawdown"]
            lines.append(f"  N={r['threshold']:>2d}: PnL {dpnl:+10.2f}, "
                         f"Trades {dtrades:+5d}, "
                         f"MaxDD {ddd:+9.2f}, "
                         f"CB fired {r['cb_fired']:>3d}x, "
                         f"blocked {r['trades_blocked']:>4d}, "
                         f"forced-opposite {r['forced_opposite']:>3d}")

    # Single day log in summary
    if single_day:
        lines.append("")
        lines.append("=" * 90)
        lines.append("SINGLE-DAY TRADE LOG: 10/10/2025 (N=3)")
        lines.append("=" * 90)
        lines.append(f"{'Time':>15s}  {'Dir':>5s}  {'Entry':>10s}  {'Exit':>8s}  "
                     f"{'PnL($)':>8s}  {'CL':>3s}  {'CS':>3s}  {'LBlk':>4s}  {'SBlk':>4s}")
        lines.append("-" * 75)
        for t in single_day:
            lines.append(f"{t['time']:>15s}  {t['direction']:>5s}  {t['entry_price']:>10.2f}  "
                         f"{t['exit_type']:>8s}  {t['pnl']:>8.2f}  "
                         f"{t['consec_long']:>3d}  {t['consec_short']:>3d}  "
                         f"{t['long_blocked']:>4d}  {t['short_blocked']:>4d}")

    lines.append("")

    with open(summary_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Wrote summary to {summary_path}")

    # Print summary to stdout
    print("\n" + "\n".join(lines))


if __name__ == "__main__":
    main()
