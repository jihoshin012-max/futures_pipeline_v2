"""
Fresh grid search: lookback × inner × outer × martingale
Ranked by daily consistency (win day %, PnL/MaxDD, max drawdown).

Reads NQ-250tick-calibration.csv, simulates rangefade bands with
RTH-only entries, target/stop/reversal logic, optional martingale.

Outputs:
  fresh-grid-results.csv  — all positive-PnL configs, sorted
  fresh-grid-summary.txt  — top 20 + pattern analysis
"""

import csv
import math
import os
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import List, Tuple, Optional

# ── Constants ──────────────────────────────────────────────────────
TICK_SIZE = 0.25
TICK_VALUE = 5.00
POINT_VALUE = TICK_VALUE / TICK_SIZE  # $20 per point

DATA_PATH = "c:/Projects/futures_pipeline/data/NQ-250tick-calibration.csv"
OUTPUT_DIR = "c:/Projects/futures_pipeline/lab/output/rotational-NQ-scale-detection"

# RTH window
RTH_START = "09:30"
RTH_END = "15:45"

# Grid parameters
LOOKBACKS = [20, 30, 40, 50, 75, 100]
INNER_MULTS = [0.5, 0.75, 1.0, 1.25, 1.5]
OUTER_MULTS = [1.25, 1.5, 1.75, 2.0, 2.5, 3.0]

# Martingale configs: (name, multiplier, max_qty)
MARTINGALE_CONFIGS = [
    ("none", 1.0, 1),
    ("m1.5_max2", 1.5, 2),
    ("m2.0_max2", 2.0, 2),
]

MIN_TRADING_DAYS = 30


# ── Data Loading ───────────────────────────────────────────────────
@dataclass
class Bar:
    date: str
    time_str: str
    open: float
    high: float
    low: float
    close: float  # "Last" column


def load_data(path: str) -> List[Bar]:
    bars = []
    with open(path, "r") as f:
        reader = csv.reader(f)
        header = next(reader)
        # Columns: Date, Time, Open, High, Low, Last, ...
        for row in reader:
            if len(row) < 6:
                continue
            date_str = row[0].strip()
            time_str = row[1].strip()
            try:
                o = float(row[2])
                h = float(row[3])
                l = float(row[4])
                c = float(row[5])
            except (ValueError, IndexError):
                continue
            bars.append(Bar(date=date_str, time_str=time_str,
                            open=o, high=h, low=l, close=c))
    return bars


def time_to_minutes(t: str) -> float:
    """Convert HH:MM:SS... to minutes since midnight."""
    parts = t.split(":")
    h = int(parts[0])
    m = int(parts[1])
    s = float(parts[2]) if len(parts) > 2 else 0.0
    return h * 60 + m + s / 60.0


def is_rth(time_str: str) -> bool:
    mins = time_to_minutes(time_str)
    return 9 * 60 + 30 <= mins <= 15 * 60 + 45


# ── Rolling Statistics ─────────────────────────────────────────────
class RollingStats:
    """Efficient rolling mean/stddev with population ddof=0."""

    def __init__(self, lookback: int):
        self.lookback = lookback
        self.values: list = []
        self.sum_ = 0.0
        self.sum_sq = 0.0

    def update(self, val: float) -> Tuple[Optional[float], Optional[float]]:
        self.values.append(val)
        self.sum_ += val
        self.sum_sq += val * val

        if len(self.values) > self.lookback:
            old = self.values.pop(0)
            self.sum_ -= old
            self.sum_sq -= old * old

        if len(self.values) < self.lookback:
            return None, None

        mean = self.sum_ / self.lookback
        variance = self.sum_sq / self.lookback - mean * mean
        if variance < 0:
            variance = 0.0
        stddev = math.sqrt(variance)
        return mean, stddev


# ── Simulation ─────────────────────────────────────────────────────
@dataclass
class TradeResult:
    pnl: float  # dollar PnL
    is_win: bool
    side: int  # 1=long, -1=short
    qty: int


def simulate_config(
    bars: List[Bar],
    lookback: int,
    inner_mult: float,
    outer_mult: float,
    mart_name: str,
    mart_mult: float,
    mart_max: int,
) -> Optional[dict]:
    """Run simulation for one config. Returns metrics dict or None."""

    rolling = RollingStats(lookback)
    position = 0  # 1=long, -1=short, 0=flat
    entry_price = 0.0
    target_price = 0.0
    stop_price = 0.0
    pos_qty = 0

    # Daily tracking
    daily_pnl = defaultdict(float)
    total_trades = 0
    total_pnl = 0.0
    largest_single_loss = 0.0
    all_wins_pnl = 0.0
    all_losses_pnl = 0.0

    # Martingale per-side counters
    consec_stops_long = 0
    consec_stops_short = 0
    current_date = None

    def get_qty(side: int) -> int:
        if mart_name == "none":
            return 1
        consec = consec_stops_long if side == 1 else consec_stops_short
        qty = int(math.floor(1.0 * mart_mult ** consec))
        if qty < 1:
            qty = 1
        if qty > mart_max:
            qty = mart_max
        return qty

    def record_trade(pnl_dollars: float, side: int, qty: int):
        nonlocal total_trades, total_pnl, largest_single_loss
        nonlocal all_wins_pnl, all_losses_pnl
        nonlocal consec_stops_long, consec_stops_short

        total_trades += 1
        total_pnl += pnl_dollars
        daily_pnl[current_date] += pnl_dollars

        if pnl_dollars < largest_single_loss:
            largest_single_loss = pnl_dollars

        if pnl_dollars >= 0:
            all_wins_pnl += pnl_dollars
            # Win resets that side's counter
            if side == 1:
                consec_stops_long = 0
            else:
                consec_stops_short = 0
        else:
            all_losses_pnl += abs(pnl_dollars)

    def record_stop(side: int):
        nonlocal consec_stops_long, consec_stops_short
        if side == 1:
            consec_stops_long += 1
        else:
            consec_stops_short += 1

    for bar in bars:
        # Reset daily martingale counters
        if bar.date != current_date:
            current_date = bar.date
            consec_stops_long = 0
            consec_stops_short = 0

        # Update rolling stats with close price
        mean, stddev = rolling.update(bar.close)

        if mean is None or stddev is None or stddev == 0:
            continue

        if not is_rth(bar.time_str):
            continue

        # Compute bands
        inner_top = mean + inner_mult * stddev
        inner_bot = mean - inner_mult * stddev
        outer_top = mean + outer_mult * stddev
        outer_bot = mean - outer_mult * stddev

        band_width = inner_top - inner_bot
        if band_width <= 0:
            continue

        # ── Check signals ──
        buy_signal = (bar.low <= inner_bot) and (bar.low > outer_bot)
        sell_signal = (bar.high >= inner_top) and (bar.high < outer_top)

        # Process existing position first, then check for new entries
        # Can loop for same-bar re-entry after resolution
        bars_to_process = True
        while bars_to_process:
            bars_to_process = False

            if position != 0:
                # Check resolution: target > stop > reversal
                resolved = False

                # Target check
                if position == 1 and bar.high >= target_price:
                    pnl_pts = target_price - entry_price
                    pnl_dollars = pnl_pts * POINT_VALUE * pos_qty
                    record_trade(pnl_dollars, position, pos_qty)
                    position = 0
                    resolved = True
                elif position == -1 and bar.low <= target_price:
                    pnl_pts = entry_price - target_price
                    pnl_dollars = pnl_pts * POINT_VALUE * pos_qty
                    record_trade(pnl_dollars, position, pos_qty)
                    position = 0
                    resolved = True

                # Stop check (only if not already resolved by target)
                if not resolved:
                    if position == 1 and bar.low <= stop_price:
                        pnl_pts = stop_price - entry_price
                        pnl_dollars = pnl_pts * POINT_VALUE * pos_qty
                        record_stop(1)
                        record_trade(pnl_dollars, position, pos_qty)
                        position = 0
                        resolved = True
                    elif position == -1 and bar.high >= stop_price:
                        pnl_pts = entry_price - stop_price
                        pnl_dollars = pnl_pts * POINT_VALUE * pos_qty
                        record_stop(-1)
                        record_trade(pnl_dollars, position, pos_qty)
                        position = 0
                        resolved = True

                # Reversal check (only if not resolved by target/stop)
                if not resolved:
                    if position == 1 and sell_signal:
                        # Exit long at close
                        pnl_pts = bar.close - entry_price
                        pnl_dollars = pnl_pts * POINT_VALUE * pos_qty
                        if pnl_dollars < 0:
                            record_stop(1)
                        record_trade(pnl_dollars, position, pos_qty)
                        # Enter short
                        qty = get_qty(-1)
                        position = -1
                        pos_qty = qty
                        entry_price = bar.close
                        target_offset = band_width
                        stop_offset = outer_top - inner_top
                        target_price = entry_price - target_offset
                        stop_price = entry_price + stop_offset
                        resolved = True
                    elif position == -1 and buy_signal:
                        # Exit short at close
                        pnl_pts = entry_price - bar.close
                        pnl_dollars = pnl_pts * POINT_VALUE * pos_qty
                        if pnl_dollars < 0:
                            record_stop(-1)
                        record_trade(pnl_dollars, position, pos_qty)
                        # Enter long
                        qty = get_qty(1)
                        position = 1
                        pos_qty = qty
                        entry_price = bar.close
                        target_offset = band_width
                        stop_offset = inner_bot - outer_bot
                        target_price = entry_price + target_offset
                        stop_price = entry_price - stop_offset
                        resolved = True

                if resolved and position == 0:
                    # Same-bar re-entry possible
                    bars_to_process = True
                    continue

            # ── New entry (flat) ──
            if position == 0:
                if buy_signal and sell_signal:
                    # Both signals — skip (ambiguous)
                    pass
                elif buy_signal:
                    qty = get_qty(1)
                    position = 1
                    pos_qty = qty
                    entry_price = bar.close
                    target_offset = band_width
                    stop_offset = inner_bot - outer_bot
                    target_price = entry_price + target_offset
                    stop_price = entry_price - stop_offset
                elif sell_signal:
                    qty = get_qty(-1)
                    position = -1
                    pos_qty = qty
                    entry_price = bar.close
                    target_offset = band_width
                    stop_offset = outer_top - inner_top
                    target_price = entry_price - target_offset
                    stop_price = entry_price + stop_offset

    # ── Compute metrics ──
    if total_trades == 0:
        return None

    dates_with_trades = {d for d, p in daily_pnl.items()}
    trading_days = len(dates_with_trades)

    if trading_days < MIN_TRADING_DAYS:
        return None

    up_days = sum(1 for d in dates_with_trades if daily_pnl[d] > 0)
    down_days = sum(1 for d in dates_with_trades if daily_pnl[d] < 0)
    flat_days = trading_days - up_days - down_days

    win_day_pct = up_days / trading_days * 100 if trading_days > 0 else 0

    up_day_pnls = [daily_pnl[d] for d in dates_with_trades if daily_pnl[d] > 0]
    down_day_pnls = [daily_pnl[d] for d in dates_with_trades if daily_pnl[d] < 0]

    avg_up_day = sum(up_day_pnls) / len(up_day_pnls) if up_day_pnls else 0
    avg_down_day = sum(down_day_pnls) / len(down_day_pnls) if down_day_pnls else 0
    worst_day = min(daily_pnl[d] for d in dates_with_trades)

    # Max drawdown from daily cumulative PnL
    sorted_dates = sorted(dates_with_trades)
    cum_pnl = 0.0
    peak = 0.0
    max_dd = 0.0
    for d in sorted_dates:
        cum_pnl += daily_pnl[d]
        if cum_pnl > peak:
            peak = cum_pnl
        dd = peak - cum_pnl
        if dd > max_dd:
            max_dd = dd

    pnl_maxdd_ratio = total_pnl / max_dd if max_dd > 0 else (999.0 if total_pnl > 0 else 0)

    ev_per_trade = total_pnl / total_trades if total_trades > 0 else 0
    profit_factor = all_wins_pnl / all_losses_pnl if all_losses_pnl > 0 else (999.0 if all_wins_pnl > 0 else 0)
    trades_per_day = total_trades / trading_days if trading_days > 0 else 0

    return {
        "lookback": lookback,
        "inner_mult": inner_mult,
        "outer_mult": outer_mult,
        "martingale": mart_name,
        "total_trades": total_trades,
        "total_pnl": round(total_pnl, 2),
        "up_days": up_days,
        "down_days": down_days,
        "flat_days": flat_days,
        "trading_days": trading_days,
        "win_day_pct": round(win_day_pct, 2),
        "avg_up_day": round(avg_up_day, 2),
        "avg_down_day": round(avg_down_day, 2),
        "worst_day": round(worst_day, 2),
        "max_drawdown": round(max_dd, 2),
        "pnl_maxdd_ratio": round(pnl_maxdd_ratio, 4),
        "largest_single_loss": round(largest_single_loss, 2),
        "ev_per_trade": round(ev_per_trade, 2),
        "profit_factor": round(profit_factor, 4),
        "trades_per_day": round(trades_per_day, 2),
    }


def main():
    t0 = time.time()
    print("Loading data...")
    bars = load_data(DATA_PATH)
    print(f"  Loaded {len(bars)} bars in {time.time()-t0:.1f}s")

    # Build valid inner/outer combos
    combos = []
    for inner in INNER_MULTS:
        for outer in OUTER_MULTS:
            if outer > inner:
                combos.append((inner, outer))
    print(f"  {len(combos)} valid inner/outer combos")
    total_configs = len(combos) * len(LOOKBACKS) * len(MARTINGALE_CONFIGS)
    print(f"  {total_configs} total configurations to test")

    results = []
    done = 0
    t1 = time.time()

    for lookback in LOOKBACKS:
        for inner_mult, outer_mult in combos:
            for mart_name, mart_mult, mart_max in MARTINGALE_CONFIGS:
                done += 1
                if done % 50 == 0:
                    elapsed = time.time() - t1
                    rate = done / elapsed if elapsed > 0 else 0
                    eta = (total_configs - done) / rate if rate > 0 else 0
                    print(f"  [{done}/{total_configs}] {rate:.1f} configs/s, ETA {eta:.0f}s")

                metrics = simulate_config(
                    bars, lookback, inner_mult, outer_mult,
                    mart_name, mart_mult, mart_max,
                )
                if metrics is not None and metrics["total_pnl"] > 0:
                    results.append(metrics)

    elapsed_total = time.time() - t0
    print(f"\nCompleted {total_configs} configs in {elapsed_total:.1f}s")
    print(f"  {len(results)} configs with positive PnL and >= {MIN_TRADING_DAYS} trading days")

    # Sort by win day % (desc), then PnL/MaxDD (desc), then max drawdown (asc)
    results.sort(key=lambda r: (-r["win_day_pct"], -r["pnl_maxdd_ratio"], r["max_drawdown"]))

    # ── Write CSV ──────────────────────────────────────────────────
    csv_path = os.path.join(OUTPUT_DIR, "fresh-grid-results.csv")
    fieldnames = [
        "lookback", "inner_mult", "outer_mult", "martingale",
        "total_trades", "total_pnl", "up_days", "down_days", "flat_days",
        "trading_days", "win_day_pct", "avg_up_day", "avg_down_day",
        "worst_day", "max_drawdown", "pnl_maxdd_ratio",
        "largest_single_loss", "ev_per_trade", "profit_factor", "trades_per_day",
    ]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    print(f"  Wrote {csv_path} ({len(results)} rows)")

    # ── Write Summary ──────────────────────────────────────────────
    summary_path = os.path.join(OUTPUT_DIR, "fresh-grid-summary.txt")
    with open(summary_path, "w") as f:
        f.write("FRESH GRID SEARCH RESULTS\n")
        f.write("=" * 70 + "\n\n")
        f.write(f"Data: {DATA_PATH}\n")
        f.write(f"Total bars: {len(bars)}\n")
        f.write(f"Configurations tested: {total_configs}\n")
        f.write(f"Configs with positive PnL (>= {MIN_TRADING_DAYS} days): {len(results)}\n")
        f.write(f"Runtime: {elapsed_total:.1f}s\n\n")

        f.write("Grid:\n")
        f.write(f"  Lookbacks: {LOOKBACKS}\n")
        f.write(f"  Inner mults: {INNER_MULTS}\n")
        f.write(f"  Outer mults: {OUTER_MULTS}\n")
        f.write(f"  Martingale: {[m[0] for m in MARTINGALE_CONFIGS]}\n")
        f.write(f"  Valid inner/outer combos: {len(combos)}\n\n")

        f.write("Ranking: Win Day % (desc) > PnL/MaxDD (desc) > MaxDD (asc)\n")
        f.write("Filter: total PnL > 0, trading days >= 30\n\n")

        # Top 20
        top_n = min(20, len(results))
        f.write(f"TOP {top_n} CONFIGURATIONS\n")
        f.write("-" * 70 + "\n\n")

        for i, r in enumerate(results[:top_n]):
            f.write(f"#{i+1}: LB={r['lookback']} Inner={r['inner_mult']} "
                    f"Outer={r['outer_mult']} Mart={r['martingale']}\n")
            f.write(f"    WinDay%={r['win_day_pct']:.1f}%  "
                    f"PnL=${r['total_pnl']:,.0f}  "
                    f"PnL/MaxDD={r['pnl_maxdd_ratio']:.2f}  "
                    f"MaxDD=${r['max_drawdown']:,.0f}\n")
            f.write(f"    Trades={r['total_trades']}  "
                    f"Up={r['up_days']} Down={r['down_days']}  "
                    f"AvgUp=${r['avg_up_day']:,.0f} AvgDown=${r['avg_down_day']:,.0f}\n")
            f.write(f"    WorstDay=${r['worst_day']:,.0f}  "
                    f"LargestLoss=${r['largest_single_loss']:,.0f}  "
                    f"EV/trade=${r['ev_per_trade']:.2f}  "
                    f"PF={r['profit_factor']:.2f}  "
                    f"Trades/day={r['trades_per_day']:.1f}\n\n")

        # ── Pattern Analysis ───────────────────────────────────────
        f.write("\nPATTERN ANALYSIS\n")
        f.write("=" * 70 + "\n\n")

        if not results:
            f.write("No positive-PnL configurations found.\n")
        else:
            # By lookback
            f.write("By Lookback (avg of positive-PnL configs):\n")
            f.write(f"  {'LB':>5}  {'Count':>5}  {'AvgWinDay%':>10}  {'AvgPnL':>10}  "
                    f"{'AvgPnL/DD':>10}  {'AvgMaxDD':>10}\n")
            for lb in LOOKBACKS:
                subset = [r for r in results if r["lookback"] == lb]
                if subset:
                    avg_wd = sum(r["win_day_pct"] for r in subset) / len(subset)
                    avg_pnl = sum(r["total_pnl"] for r in subset) / len(subset)
                    avg_ratio = sum(r["pnl_maxdd_ratio"] for r in subset) / len(subset)
                    avg_dd = sum(r["max_drawdown"] for r in subset) / len(subset)
                    f.write(f"  {lb:>5}  {len(subset):>5}  {avg_wd:>10.1f}  "
                            f"{avg_pnl:>10,.0f}  {avg_ratio:>10.2f}  {avg_dd:>10,.0f}\n")
                else:
                    f.write(f"  {lb:>5}      0       —           —           —           —\n")

            # By inner mult
            f.write("\nBy Inner Mult (avg of positive-PnL configs):\n")
            f.write(f"  {'Inner':>5}  {'Count':>5}  {'AvgWinDay%':>10}  {'AvgPnL':>10}  "
                    f"{'AvgPnL/DD':>10}  {'AvgMaxDD':>10}\n")
            for im in INNER_MULTS:
                subset = [r for r in results if r["inner_mult"] == im]
                if subset:
                    avg_wd = sum(r["win_day_pct"] for r in subset) / len(subset)
                    avg_pnl = sum(r["total_pnl"] for r in subset) / len(subset)
                    avg_ratio = sum(r["pnl_maxdd_ratio"] for r in subset) / len(subset)
                    avg_dd = sum(r["max_drawdown"] for r in subset) / len(subset)
                    f.write(f"  {im:>5.2f}  {len(subset):>5}  {avg_wd:>10.1f}  "
                            f"{avg_pnl:>10,.0f}  {avg_ratio:>10.2f}  {avg_dd:>10,.0f}\n")
                else:
                    f.write(f"  {im:>5.2f}      0       —           —           —           —\n")

            # By outer mult
            f.write("\nBy Outer Mult (avg of positive-PnL configs):\n")
            f.write(f"  {'Outer':>5}  {'Count':>5}  {'AvgWinDay%':>10}  {'AvgPnL':>10}  "
                    f"{'AvgPnL/DD':>10}  {'AvgMaxDD':>10}\n")
            for om in OUTER_MULTS:
                subset = [r for r in results if r["outer_mult"] == om]
                if subset:
                    avg_wd = sum(r["win_day_pct"] for r in subset) / len(subset)
                    avg_pnl = sum(r["total_pnl"] for r in subset) / len(subset)
                    avg_ratio = sum(r["pnl_maxdd_ratio"] for r in subset) / len(subset)
                    avg_dd = sum(r["max_drawdown"] for r in subset) / len(subset)
                    f.write(f"  {om:>5.2f}  {len(subset):>5}  {avg_wd:>10.1f}  "
                            f"{avg_pnl:>10,.0f}  {avg_ratio:>10.2f}  {avg_dd:>10,.0f}\n")
                else:
                    f.write(f"  {om:>5.2f}      0       —           —           —           —\n")

            # By martingale
            f.write("\nBy Martingale (avg of positive-PnL configs):\n")
            f.write(f"  {'Mart':>10}  {'Count':>5}  {'AvgWinDay%':>10}  {'AvgPnL':>10}  "
                    f"{'AvgPnL/DD':>10}  {'AvgMaxDD':>10}\n")
            for mn, _, _ in MARTINGALE_CONFIGS:
                subset = [r for r in results if r["martingale"] == mn]
                if subset:
                    avg_wd = sum(r["win_day_pct"] for r in subset) / len(subset)
                    avg_pnl = sum(r["total_pnl"] for r in subset) / len(subset)
                    avg_ratio = sum(r["pnl_maxdd_ratio"] for r in subset) / len(subset)
                    avg_dd = sum(r["max_drawdown"] for r in subset) / len(subset)
                    f.write(f"  {mn:>10}  {len(subset):>5}  {avg_wd:>10.1f}  "
                            f"{avg_pnl:>10,.0f}  {avg_ratio:>10.2f}  {avg_dd:>10,.0f}\n")
                else:
                    f.write(f"  {mn:>10}      0       —           —           —           —\n")

            # Inner/Outer combo heatmap
            f.write("\nInner × Outer Combo Count (positive-PnL configs):\n")
            line = f"  {'':>5}"
            for om in OUTER_MULTS:
                line += f"  {om:>6.2f}"
            f.write(line + "\n")
            for im in INNER_MULTS:
                line = f"  {im:>5.2f}"
                for om in OUTER_MULTS:
                    cnt = sum(1 for r in results if r["inner_mult"] == im and r["outer_mult"] == om)
                    if om <= im:
                        line += f"  {'  —':>6}"
                    else:
                        line += f"  {cnt:>6}"
                f.write(line + "\n")

            # Top lookback/combo clusters
            f.write("\nTop 10 Lookback × Inner × Outer combos (by avg win day %):\n")
            combo_stats = {}
            for r in results:
                key = (r["lookback"], r["inner_mult"], r["outer_mult"])
                if key not in combo_stats:
                    combo_stats[key] = []
                combo_stats[key].append(r)
            ranked_combos = []
            for key, rs in combo_stats.items():
                avg_wd = sum(r["win_day_pct"] for r in rs) / len(rs)
                avg_pnl = sum(r["total_pnl"] for r in rs) / len(rs)
                avg_ratio = sum(r["pnl_maxdd_ratio"] for r in rs) / len(rs)
                ranked_combos.append((key, avg_wd, avg_pnl, avg_ratio, len(rs)))
            ranked_combos.sort(key=lambda x: (-x[1], -x[3]))
            f.write(f"  {'LB':>3} {'In':>5} {'Out':>5}  {'N':>2}  "
                    f"{'AvgWinDay%':>10}  {'AvgPnL':>10}  {'AvgPnL/DD':>10}\n")
            for (lb, im, om), avg_wd, avg_pnl, avg_ratio, n in ranked_combos[:10]:
                f.write(f"  {lb:>3} {im:>5.2f} {om:>5.2f}  {n:>2}  "
                        f"{avg_wd:>10.1f}  {avg_pnl:>10,.0f}  {avg_ratio:>10.2f}\n")

    print(f"  Wrote {summary_path}")
    print(f"\nDone in {time.time()-t0:.1f}s total.")


if __name__ == "__main__":
    main()
