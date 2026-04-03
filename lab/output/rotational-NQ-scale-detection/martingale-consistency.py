"""Martingale consistency test across rangefade band configs.

Tests 5 band configs × 5 martingale configs, measuring daily consistency.
Simulation matches rotational-NQ-study-rangefade-v3.cpp logic:
  - Rolling mean + stddev (ddof=0) of Close
  - Bands: inner = mean ± innerMult*std, outer = mean ± outerMult*std
  - Buy when low <= innerBot (and low > outerBot), sell when high >= innerTop (and high < outerTop)
  - Entry at bar close, target = opposite inner band, stop = outer band
  - Same-bar re-entry after reversal exit
  - Priority: target > stop > reversal (checked in that order within bar)
  - Per-side consecutive stop tracking for martingale
  - Consecutive stop counters reset daily
  - RTH only: 09:30-15:45

Usage:
    python martingale-consistency.py

Output:
    martingale-consistency.csv
    martingale-consistency-summary.txt
"""
from __future__ import annotations

import csv
import math
import sys
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
#  Constants
# ---------------------------------------------------------------------------
TICK_SIZE = 0.25
TICK_VALUE = 5.00
DOLLAR_PER_POINT = TICK_VALUE / TICK_SIZE  # $20/point

RTH_START_SEC = 9 * 3600 + 30 * 60   # 09:30
RTH_END_SEC = 15 * 3600 + 45 * 60    # 15:45

DATA_PATH = Path(r"C:\Projects\futures_pipeline\data\NQ-250tick-calibration.csv")
OUTPUT_DIR = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")

# ---------------------------------------------------------------------------
#  Band configs to test
# ---------------------------------------------------------------------------
BAND_CONFIGS = [
    {"lb": 20, "inner": 1.00, "outer": 1.25},
    {"lb": 20, "inner": 1.00, "outer": 1.50},
    {"lb": 50, "inner": 1.00, "outer": 1.25},
    {"lb": 50, "inner": 0.50, "outer": 1.75},
    {"lb": 100, "inner": 0.75, "outer": 1.75},
]

# ---------------------------------------------------------------------------
#  Martingale configs to test
# ---------------------------------------------------------------------------
MARTINGALE_CONFIGS = [
    {"name": "none", "enabled": False, "mult": 1.0, "max_qty": 1},
    {"name": "m1.5_max2", "enabled": True, "mult": 1.5, "max_qty": 2},
    {"name": "m1.5_max3", "enabled": True, "mult": 1.5, "max_qty": 3},
    {"name": "m2.0_max2", "enabled": True, "mult": 2.0, "max_qty": 2},
    {"name": "m2.0_max3", "enabled": True, "mult": 2.0, "max_qty": 3},
]


# ---------------------------------------------------------------------------
#  Load data
# ---------------------------------------------------------------------------
def load_bars(filepath: Path):
    """Load SC 250-tick bar CSV."""
    dates, times = [], []
    opens, highs, lows, closes = [], [], [], []

    with open(filepath, "r") as f:
        reader = csv.reader(f)
        next(reader)  # skip header
        for row in reader:
            if len(row) < 6:
                continue
            dates.append(row[0].strip())
            times.append(row[1].strip())
            opens.append(float(row[2]))
            highs.append(float(row[3]))
            lows.append(float(row[4]))
            closes.append(float(row[5]))

    n = len(closes)

    # Parse time to seconds
    time_secs = []
    for t in times:
        parts = t.split(":")
        hr = int(parts[0])
        mn = int(parts[1])
        sec = int(float(parts[2])) if len(parts) > 2 else 0
        time_secs.append(hr * 3600 + mn * 60 + sec)

    # Parse date to int YYYYMMDD
    date_ints = []
    for d in dates:
        parts = d.split("/")
        mo, dy, yr = int(parts[0]), int(parts[1]), int(parts[2])
        date_ints.append(yr * 10000 + mo * 100 + dy)

    return {
        "n": n,
        "date_int": np.array(date_ints, dtype=np.int32),
        "time_sec": np.array(time_secs, dtype=np.int32),
        "open": np.array(opens, dtype=np.float64),
        "high": np.array(highs, dtype=np.float64),
        "low": np.array(lows, dtype=np.float64),
        "close": np.array(closes, dtype=np.float64),
    }


# ---------------------------------------------------------------------------
#  Simulation
# ---------------------------------------------------------------------------
def simulate(bars: dict, lb: int, inner_mult: float, outer_mult: float,
             mart_enabled: bool, mart_mult: float, mart_max: int) -> list[dict]:
    """Run rangefade simulation. Returns list of trade dicts."""
    n = bars["n"]
    close = bars["close"]
    high = bars["high"]
    low = bars["low"]
    date_int = bars["date_int"]
    time_sec = bars["time_sec"]

    trades = []

    # State
    pos_qty = 0        # +N = long N contracts, -N = short N contracts
    entry_price = 0.0
    last_entry_dir = 0  # 1=long, -1=short
    consec_long = 0
    consec_short = 0
    last_date = 0
    base_qty = 1

    def get_mart_qty(side_consec: int) -> int:
        if not mart_enabled or side_consec <= 0:
            return base_qty
        qty = int(base_qty * (mart_mult ** side_consec))
        if qty > mart_max:
            qty = mart_max
        if qty < 1:
            qty = 1
        return qty

    def record_trade(bar_idx: int, direction: int, qty: int, e_price: float,
                     x_price: float, exit_type: str):
        if direction == 1:
            pnl_points = x_price - e_price
        else:
            pnl_points = e_price - x_price
        pnl_dollar = pnl_points * DOLLAR_PER_POINT * qty
        trades.append({
            "date": int(date_int[bar_idx]),
            "bar_idx": bar_idx,
            "direction": direction,
            "qty": qty,
            "entry_price": e_price,
            "exit_price": x_price,
            "exit_type": exit_type,
            "pnl_dollar": pnl_dollar,
        })
        return pnl_dollar

    for i in range(n):
        t = time_sec[i]
        d = date_int[i]

        # Daily reset
        if d != last_date:
            last_date = d
            consec_long = 0
            consec_short = 0
            last_entry_dir = 0
            pos_qty = 0
            entry_price = 0.0

        # RTH filter
        if t < RTH_START_SEC or t > RTH_END_SEC:
            continue

        # Need enough bars for lookback
        if i < lb:
            continue

        # Compute bands on current bar's close (rolling window ending at i)
        window = close[i - lb + 1: i + 1]
        mean = np.mean(window)
        std = np.std(window, ddof=0)

        if std < TICK_SIZE:
            continue

        inner_top = mean + inner_mult * std
        inner_bot = mean - inner_mult * std
        outer_top = mean + outer_mult * std
        outer_bot = mean - outer_mult * std

        bar_high = high[i]
        bar_low = low[i]
        bar_close = close[i]

        # --- Position management: check target/stop on current bar ---
        if pos_qty != 0:
            abs_qty = abs(pos_qty)
            direction = 1 if pos_qty > 0 else -1

            # Check target hit (priority 1)
            target_hit = False
            if direction == 1 and bar_high >= inner_top:
                target_hit = True
                exit_price = inner_top
            elif direction == -1 and bar_low <= inner_bot:
                target_hit = True
                exit_price = inner_bot

            if target_hit:
                pnl = record_trade(i, direction, abs_qty, entry_price, exit_price, "TARGET")
                if direction == 1:
                    consec_long = 0
                else:
                    consec_short = 0
                pos_qty = 0
                entry_price = 0.0
                # Fall through to check for new entry on same bar
            else:
                # Check stop hit (priority 2)
                stop_hit = False
                if direction == 1 and bar_low <= outer_bot:
                    stop_hit = True
                    exit_price = outer_bot
                elif direction == -1 and bar_high >= outer_top:
                    stop_hit = True
                    exit_price = outer_top

                if stop_hit:
                    pnl = record_trade(i, direction, abs_qty, entry_price, exit_price, "STOP")
                    if direction == 1:
                        consec_long += 1
                    else:
                        consec_short += 1
                    pos_qty = 0
                    entry_price = 0.0
                    # Fall through to check for new entry on same bar

        # --- Signal generation ---
        buy_signal = (bar_low <= inner_bot and bar_low > outer_bot)
        sell_signal = (bar_high >= inner_top and bar_high < outer_top)

        if pos_qty == 0:
            # No position — check for entry
            if buy_signal and sell_signal:
                # Both signals — skip (ambiguous)
                continue
            elif buy_signal:
                qty = get_mart_qty(consec_long)
                pos_qty = qty
                entry_price = bar_close
                last_entry_dir = 1
            elif sell_signal:
                qty = get_mart_qty(consec_short)
                pos_qty = -qty
                entry_price = bar_close
                last_entry_dir = -1

        elif pos_qty > 0:
            # Long position — check for sell reversal
            if sell_signal:
                abs_qty = abs(pos_qty)
                direction = 1
                # Exit long
                exit_price = bar_close
                pnl = record_trade(i, direction, abs_qty, entry_price, exit_price, "REVERSAL")
                if pnl < 0:
                    consec_long += 1
                else:
                    consec_long = 0
                # Enter short
                qty = get_mart_qty(consec_short)
                pos_qty = -qty
                entry_price = bar_close
                last_entry_dir = -1

        elif pos_qty < 0:
            # Short position — check for buy reversal
            if buy_signal:
                abs_qty = abs(pos_qty)
                direction = -1
                # Exit short
                exit_price = bar_close
                pnl = record_trade(i, direction, abs_qty, entry_price, exit_price, "REVERSAL")
                if pnl < 0:
                    consec_short += 1
                else:
                    consec_short = 0
                # Enter long
                qty = get_mart_qty(consec_long)
                pos_qty = qty
                entry_price = bar_close
                last_entry_dir = 1

    # Close any open position at end of data
    if pos_qty != 0:
        direction = 1 if pos_qty > 0 else -1
        abs_qty = abs(pos_qty)
        exit_price = close[n - 1]
        record_trade(n - 1, direction, abs_qty, entry_price, exit_price, "DATA_END")

    return trades


# ---------------------------------------------------------------------------
#  Compute metrics from trades
# ---------------------------------------------------------------------------
def compute_metrics(trades: list[dict]) -> dict:
    """Compute daily consistency and risk metrics."""
    if not trades:
        return {
            "total_trades": 0, "total_pnl": 0, "up_days": 0, "down_days": 0,
            "win_day_pct": 0, "avg_up_day": 0, "avg_down_day": 0,
            "worst_day": 0, "best_day": 0, "max_drawdown": 0,
            "pnl_maxdd_ratio": 0, "largest_trade_loss": 0, "win_rate": 0,
            "ev_per_trade": 0, "profit_factor": 0, "max_consec_losses": 0,
        }

    total_trades = len(trades)
    total_pnl = sum(t["pnl_dollar"] for t in trades)

    # Win rate
    wins = sum(1 for t in trades if t["pnl_dollar"] > 0)
    win_rate = 100.0 * wins / total_trades if total_trades > 0 else 0

    # EV per trade
    ev_per_trade = total_pnl / total_trades if total_trades > 0 else 0

    # Profit factor
    gross_profit = sum(t["pnl_dollar"] for t in trades if t["pnl_dollar"] > 0)
    gross_loss = abs(sum(t["pnl_dollar"] for t in trades if t["pnl_dollar"] < 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Largest single trade loss
    largest_trade_loss = min(t["pnl_dollar"] for t in trades)

    # Max consecutive losing trades
    max_consec = 0
    current_consec = 0
    for t in trades:
        if t["pnl_dollar"] < 0:
            current_consec += 1
            if current_consec > max_consec:
                max_consec = current_consec
        else:
            current_consec = 0

    # Daily PnL
    daily_pnl = {}
    for t in trades:
        d = t["date"]
        daily_pnl[d] = daily_pnl.get(d, 0.0) + t["pnl_dollar"]

    days = sorted(daily_pnl.keys())
    day_pnls = [daily_pnl[d] for d in days]

    up_days = sum(1 for p in day_pnls if p > 0)
    down_days = sum(1 for p in day_pnls if p <= 0)
    total_days = up_days + down_days
    win_day_pct = 100.0 * up_days / total_days if total_days > 0 else 0

    up_pnls = [p for p in day_pnls if p > 0]
    down_pnls = [p for p in day_pnls if p <= 0]
    avg_up_day = sum(up_pnls) / len(up_pnls) if up_pnls else 0
    avg_down_day = sum(down_pnls) / len(down_pnls) if down_pnls else 0

    worst_day = min(day_pnls) if day_pnls else 0
    best_day = max(day_pnls) if day_pnls else 0

    # Max drawdown (peak-to-trough of daily cumulative PnL)
    cum_pnl = 0.0
    peak = 0.0
    max_dd = 0.0
    for p in day_pnls:
        cum_pnl += p
        if cum_pnl > peak:
            peak = cum_pnl
        dd = peak - cum_pnl
        if dd > max_dd:
            max_dd = dd

    pnl_maxdd = total_pnl / max_dd if max_dd > 0 else float("inf")

    return {
        "total_trades": total_trades,
        "total_pnl": round(total_pnl, 2),
        "up_days": up_days,
        "down_days": down_days,
        "win_day_pct": round(win_day_pct, 1),
        "avg_up_day": round(avg_up_day, 2),
        "avg_down_day": round(avg_down_day, 2),
        "worst_day": round(worst_day, 2),
        "best_day": round(best_day, 2),
        "max_drawdown": round(max_dd, 2),
        "pnl_maxdd_ratio": round(pnl_maxdd, 2),
        "largest_trade_loss": round(largest_trade_loss, 2),
        "win_rate": round(win_rate, 1),
        "ev_per_trade": round(ev_per_trade, 2),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else 999.99,
        "max_consec_losses": max_consec,
    }


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------
def main():
    if not DATA_PATH.exists():
        print(f"ERROR: Data file not found: {DATA_PATH}")
        sys.exit(1)

    print(f"Loading data from {DATA_PATH}...")
    bars = load_bars(DATA_PATH)
    print(f"Loaded {bars['n']} bars")

    # Count unique RTH dates
    rth_mask = (bars["time_sec"] >= RTH_START_SEC) & (bars["time_sec"] <= RTH_END_SEC)
    rth_dates = set(bars["date_int"][rth_mask])
    print(f"RTH trading days: {len(rth_dates)}")

    results = []

    for bc in BAND_CONFIGS:
        for mc in MARTINGALE_CONFIGS:
            label = f"LB={bc['lb']}_in={bc['inner']:.2f}_out={bc['outer']:.2f}"
            mart_label = mc["name"]
            print(f"  Running {label} | {mart_label}...", end="")

            trades = simulate(
                bars, bc["lb"], bc["inner"], bc["outer"],
                mc["enabled"], mc["mult"], mc["max_qty"],
            )
            metrics = compute_metrics(trades)

            row = {
                "lookback": bc["lb"],
                "inner_mult": bc["inner"],
                "outer_mult": bc["outer"],
                "martingale": mart_label,
                **metrics,
            }
            results.append(row)
            print(f" {metrics['total_trades']} trades, "
                  f"PnL=${metrics['total_pnl']:,.0f}, "
                  f"WinDay%={metrics['win_day_pct']:.1f}%, "
                  f"MaxDD=${metrics['max_drawdown']:,.0f}")

    # Write CSV
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = OUTPUT_DIR / "martingale-consistency.csv"
    fieldnames = list(results[0].keys())
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in results:
            w.writerow(r)
    print(f"\nWrote {len(results)} rows to {csv_path}")

    # Write summary
    summary_path = OUTPUT_DIR / "martingale-consistency-summary.txt"
    # Sort by win_day_pct desc, then pnl_maxdd_ratio desc
    sorted_results = sorted(results, key=lambda x: (-x["win_day_pct"], -x["pnl_maxdd_ratio"]))

    with open(summary_path, "w") as f:
        f.write("MARTINGALE CONSISTENCY TEST — SORTED BY WIN DAY %, THEN PNL/MAXDD\n")
        f.write("=" * 120 + "\n\n")

        # Header
        f.write(f"{'Config':<35} {'Mart':<12} {'Trades':>6} {'PnL($)':>10} "
                f"{'WinDay%':>8} {'UpD':>4} {'DnD':>4} "
                f"{'AvgUp($)':>10} {'AvgDn($)':>10} "
                f"{'Worst($)':>10} {'Best($)':>10} "
                f"{'MaxDD($)':>10} {'PnL/DD':>7} "
                f"{'BigLoss($)':>11} {'WR%':>5} {'EV($)':>8} "
                f"{'PF':>6} {'MaxCL':>5}\n")
        f.write("-" * 185 + "\n")

        highlights = []
        for r in sorted_results:
            label = f"LB={r['lookback']}_in={r['inner_mult']:.2f}_out={r['outer_mult']:.2f}"
            flag = ""
            if r["win_day_pct"] > 60 and r["max_drawdown"] < 10000:
                flag = " ***"
                highlights.append(r)

            f.write(f"{label:<35} {r['martingale']:<12} {r['total_trades']:>6} "
                    f"{r['total_pnl']:>10,.0f} "
                    f"{r['win_day_pct']:>7.1f}% {r['up_days']:>4} {r['down_days']:>4} "
                    f"{r['avg_up_day']:>10,.0f} {r['avg_down_day']:>10,.0f} "
                    f"{r['worst_day']:>10,.0f} {r['best_day']:>10,.0f} "
                    f"{r['max_drawdown']:>10,.0f} {r['pnl_maxdd_ratio']:>7.2f} "
                    f"{r['largest_trade_loss']:>11,.0f} {r['win_rate']:>4.1f}% "
                    f"{r['ev_per_trade']:>8,.0f} "
                    f"{r['profit_factor']:>6.2f} {r['max_consec_losses']:>5}"
                    f"{flag}\n")

        f.write("\n")
        f.write("=" * 120 + "\n")
        f.write(f"*** = Win day% > 60% AND Max DD < $10,000\n\n")

        if highlights:
            f.write(f"HIGHLIGHTED CONFIGS ({len(highlights)} found):\n")
            f.write("-" * 80 + "\n")
            for r in highlights:
                label = f"LB={r['lookback']}_in={r['inner_mult']:.2f}_out={r['outer_mult']:.2f}"
                f.write(f"  {label} | {r['martingale']:<12} | "
                        f"WinDay={r['win_day_pct']:.1f}% | "
                        f"MaxDD=${r['max_drawdown']:,.0f} | "
                        f"PnL=${r['total_pnl']:,.0f} | "
                        f"PnL/DD={r['pnl_maxdd_ratio']:.2f} | "
                        f"Worst=${r['worst_day']:,.0f} | "
                        f"BigLoss=${r['largest_trade_loss']:,.0f}\n")
        else:
            f.write("No configs met the >60% win day / <$10K max DD criteria.\n")

    print(f"Wrote summary to {summary_path}")


if __name__ == "__main__":
    main()
