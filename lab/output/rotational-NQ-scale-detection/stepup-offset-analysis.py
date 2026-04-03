"""
Step-up stop offset grid for LB=100, inner=0.75, outer=1.75, no martingale.

Step-up logic:
  - After entry, monitor if price reaches the rolling mean (dynamic midline)
  - For long: bar high >= mean triggers step-up
  - For short: bar low <= mean triggers step-up
  - Once triggered, move stop to: entry_price + offset_ticks * tick_size
    (long: positive offset = above entry; short: stop = entry - offset)
  - Step-up stop checked every bar (real-time via high/low)
  - If step-up stop violated: exit at bar close
  - Original outer band stop still active before midline reached
  - Step-up fires once per trade

Outputs:
  stepup-offset-grid.csv   — one row per offset
  stepup-offset-summary.txt — text summary
"""

import csv
import math
import os
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import List, Optional, Tuple

# ── Constants ──────────────────────────────────────────────────────
TICK_SIZE = 0.25
TICK_VALUE = 5.00
POINT_VALUE = TICK_VALUE / TICK_SIZE  # $20 per point
COST_PER_TRADE = 4.00  # round-trip

DATA_PATH = "c:/Projects/futures_pipeline/data/NQ-250tick-calibration.csv"
OUTPUT_DIR = "c:/Projects/futures_pipeline/lab/output/rotational-NQ-scale-detection"

# RTH window
RTH_START_MINS = 9 * 60 + 30   # 09:30
RTH_END_MINS = 15 * 60 + 45    # 15:45

# Fixed config
LOOKBACK = 100
INNER_MULT = 0.75
OUTER_MULT = 1.75

# Step-up offsets to test (in ticks)
STEPUP_OFFSETS = [-40, -20, -10, 0, 10, 20, 40, None]  # None = no step-up (baseline)


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
        next(reader)  # header
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
    parts = t.split(":")
    h = int(parts[0])
    m = int(parts[1])
    s = float(parts[2]) if len(parts) > 2 else 0.0
    return h * 60 + m + s / 60.0


def is_rth(time_str: str) -> bool:
    mins = time_to_minutes(time_str)
    return RTH_START_MINS <= mins <= RTH_END_MINS


# ── Rolling Statistics ─────────────────────────────────────────────
class RollingStats:
    """Rolling mean/stddev with population ddof=0."""

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
def simulate_stepup(bars: List[Bar], stepup_offset_ticks) -> dict:
    """
    Run simulation for LB=100, inner=0.75, outer=1.75, no martingale,
    with optional step-up stop at given offset (in ticks).
    stepup_offset_ticks: int or None (None = no step-up, baseline)
    """
    rolling = RollingStats(LOOKBACK)
    position = 0  # 1=long, -1=short, 0=flat
    entry_price = 0.0
    target_price = 0.0
    stop_price = 0.0

    # Step-up state
    stepup_armed = False  # Has midline been reached?
    stepup_stop = 0.0     # The step-up stop price once armed

    # Tracking
    daily_pnl = defaultdict(float)
    total_trades = 0
    total_wins = 0
    total_pnl_raw = 0.0  # before costs

    # Step-up specific tracking
    stepup_fired_count = 0       # Trades where step-up armed (midline reached)
    stepup_stop_hit_count = 0    # Trades where step-up stop was the exit
    target_after_stepup_count = 0  # Trades where target hit after step-up armed

    current_date = None

    # For each bar, we also need the current rolling mean for midline check
    # We'll store mean from rolling stats

    current_mean = None

    for bar in bars:
        if bar.date != current_date:
            current_date = bar.date

        # Update rolling stats with close price
        mean, stddev = rolling.update(bar.close)

        if mean is None or stddev is None or stddev == 0:
            continue

        current_mean = mean

        if not is_rth(bar.time_str):
            continue

        # Compute bands
        inner_top = mean + INNER_MULT * stddev
        inner_bot = mean - INNER_MULT * stddev
        outer_top = mean + OUTER_MULT * stddev
        outer_bot = mean - OUTER_MULT * stddev

        band_width = inner_top - inner_bot
        if band_width <= 0:
            continue

        # Signals
        buy_signal = (bar.low <= inner_bot) and (bar.low > outer_bot)
        sell_signal = (bar.high >= inner_top) and (bar.high < outer_top)

        bars_to_process = True
        while bars_to_process:
            bars_to_process = False

            if position != 0:
                resolved = False
                exit_price = None
                exit_type = None

                # --- Step-up midline check (before exit checks) ---
                if stepup_offset_ticks is not None and not stepup_armed:
                    if position == 1 and bar.high >= current_mean:
                        stepup_armed = True
                        stepup_stop = entry_price + stepup_offset_ticks * TICK_SIZE
                        stepup_fired_count += 1
                    elif position == -1 and bar.low <= current_mean:
                        stepup_armed = True
                        stepup_stop = entry_price - stepup_offset_ticks * TICK_SIZE
                        stepup_fired_count += 1

                # --- Target check ---
                if position == 1 and bar.high >= target_price:
                    exit_price = target_price
                    pnl_pts = target_price - entry_price
                    pnl_dollars = pnl_pts * POINT_VALUE
                    exit_type = "target"
                    if stepup_armed:
                        target_after_stepup_count += 1
                    resolved = True
                elif position == -1 and bar.low <= target_price:
                    exit_price = target_price
                    pnl_pts = entry_price - target_price
                    pnl_dollars = pnl_pts * POINT_VALUE
                    exit_type = "target"
                    if stepup_armed:
                        target_after_stepup_count += 1
                    resolved = True

                # --- Step-up stop check (real-time, before outer stop) ---
                if not resolved and stepup_armed:
                    if position == 1 and bar.low <= stepup_stop:
                        # Step-up stop hit — exit at bar close
                        pnl_pts = bar.close - entry_price
                        pnl_dollars = pnl_pts * POINT_VALUE
                        exit_type = "stepup_stop"
                        stepup_stop_hit_count += 1
                        resolved = True
                    elif position == -1 and bar.high >= stepup_stop:
                        # Step-up stop hit — exit at bar close
                        pnl_pts = entry_price - bar.close
                        pnl_dollars = pnl_pts * POINT_VALUE
                        exit_type = "stepup_stop"
                        stepup_stop_hit_count += 1
                        resolved = True

                # --- Original outer stop check ---
                if not resolved:
                    if position == 1 and bar.low <= stop_price:
                        pnl_pts = stop_price - entry_price
                        pnl_dollars = pnl_pts * POINT_VALUE
                        exit_type = "outer_stop"
                        resolved = True
                    elif position == -1 and bar.high >= stop_price:
                        pnl_pts = entry_price - stop_price
                        pnl_dollars = pnl_pts * POINT_VALUE
                        exit_type = "outer_stop"
                        resolved = True

                # --- Reversal check ---
                if not resolved:
                    if position == 1 and sell_signal:
                        pnl_pts = bar.close - entry_price
                        pnl_dollars = pnl_pts * POINT_VALUE
                        exit_type = "reversal"

                        # Record exit
                        total_trades += 1
                        total_pnl_raw += pnl_dollars
                        if pnl_dollars >= 0:
                            total_wins += 1
                        daily_pnl[current_date] += pnl_dollars - COST_PER_TRADE

                        # Enter short
                        position = -1
                        entry_price = bar.close
                        target_offset = band_width
                        stop_offset = outer_top - inner_top
                        target_price = entry_price - target_offset
                        stop_price = entry_price + stop_offset
                        stepup_armed = False
                        resolved = True

                    elif position == -1 and buy_signal:
                        pnl_pts = entry_price - bar.close
                        pnl_dollars = pnl_pts * POINT_VALUE
                        exit_type = "reversal"

                        total_trades += 1
                        total_pnl_raw += pnl_dollars
                        if pnl_dollars >= 0:
                            total_wins += 1
                        daily_pnl[current_date] += pnl_dollars - COST_PER_TRADE

                        # Enter long
                        position = 1
                        entry_price = bar.close
                        target_offset = band_width
                        stop_offset = inner_bot - outer_bot
                        target_price = entry_price + target_offset
                        stop_price = entry_price - stop_offset
                        stepup_armed = False
                        resolved = True

                # Record non-reversal exits
                if resolved and exit_type != "reversal":
                    total_trades += 1
                    total_pnl_raw += pnl_dollars
                    if pnl_dollars >= 0:
                        total_wins += 1
                    daily_pnl[current_date] += pnl_dollars - COST_PER_TRADE
                    position = 0

                if resolved and position == 0:
                    bars_to_process = True
                    continue

            # ── New entry (flat) ──
            if position == 0:
                if buy_signal and sell_signal:
                    pass  # ambiguous
                elif buy_signal:
                    position = 1
                    entry_price = bar.close
                    target_offset = band_width
                    stop_offset = inner_bot - outer_bot
                    target_price = entry_price + target_offset
                    stop_price = entry_price - stop_offset
                    stepup_armed = False
                elif sell_signal:
                    position = -1
                    entry_price = bar.close
                    target_offset = band_width
                    stop_offset = outer_top - inner_top
                    target_price = entry_price - target_offset
                    stop_price = entry_price + stop_offset
                    stepup_armed = False

    # ── Compute metrics ──
    if total_trades == 0:
        return None

    total_costs = total_trades * COST_PER_TRADE
    net_pnl = total_pnl_raw - total_costs

    dates_with_trades = {d for d, p in daily_pnl.items()}
    trading_days = len(dates_with_trades)

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

    pnl_maxdd_ratio = net_pnl / max_dd if max_dd > 0 else (999.0 if net_pnl > 0 else 0)
    win_rate = total_wins / total_trades * 100 if total_trades > 0 else 0
    ev_per_trade = net_pnl / total_trades if total_trades > 0 else 0

    offset_label = f"{stepup_offset_ticks}" if stepup_offset_ticks is not None else "none"

    return {
        "offset_ticks": offset_label,
        "total_trades": total_trades,
        "net_pnl": round(net_pnl, 2),
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
        "win_rate_pct": round(win_rate, 2),
        "ev_per_trade": round(ev_per_trade, 2),
        "stepup_fired": stepup_fired_count,
        "stepup_fired_pct": round(stepup_fired_count / total_trades * 100, 2) if total_trades > 0 else 0,
        "stepup_stop_hit": stepup_stop_hit_count,
        "target_after_stepup": target_after_stepup_count,
    }


def main():
    t0 = time.time()
    print("Loading data...")
    bars = load_data(DATA_PATH)
    print(f"  Loaded {len(bars)} bars in {time.time()-t0:.1f}s")

    print(f"\nConfig: LB={LOOKBACK}, Inner={INNER_MULT}, Outer={OUTER_MULT}, No martingale")
    print(f"Cost: ${COST_PER_TRADE}/trade")
    print(f"Offsets to test: {STEPUP_OFFSETS}\n")

    results = []
    for offset in STEPUP_OFFSETS:
        label = f"offset={offset}" if offset is not None else "baseline (no step-up)"
        print(f"  Simulating {label}...")
        metrics = simulate_stepup(bars, offset)
        if metrics is not None:
            results.append(metrics)
            print(f"    Trades={metrics['total_trades']}, "
                  f"NetPnL=${metrics['net_pnl']:,.0f}, "
                  f"WinDay%={metrics['win_day_pct']:.1f}%, "
                  f"StepUpFired={metrics['stepup_fired']}")

    elapsed = time.time() - t0
    print(f"\nCompleted in {elapsed:.1f}s")

    # ── Write CSV ──
    csv_path = os.path.join(OUTPUT_DIR, "stepup-offset-grid.csv")
    fieldnames = [
        "offset_ticks", "total_trades", "net_pnl",
        "up_days", "down_days", "flat_days", "trading_days",
        "win_day_pct", "avg_up_day", "avg_down_day", "worst_day",
        "max_drawdown", "pnl_maxdd_ratio", "win_rate_pct", "ev_per_trade",
        "stepup_fired", "stepup_fired_pct",
        "stepup_stop_hit", "target_after_stepup",
    ]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    print(f"  Wrote {csv_path}")

    # ── Write Summary ──
    summary_path = os.path.join(OUTPUT_DIR, "stepup-offset-summary.txt")
    with open(summary_path, "w") as f:
        f.write("STEP-UP STOP OFFSET GRID\n")
        f.write("=" * 78 + "\n\n")
        f.write(f"Config: LB={LOOKBACK}, Inner={INNER_MULT}, Outer={OUTER_MULT}, Qty=1 (no martingale)\n")
        f.write(f"Data: {DATA_PATH}\n")
        f.write(f"Bars: {len(bars)}\n")
        f.write(f"Cost: ${COST_PER_TRADE}/trade (deducted from PnL)\n")
        f.write(f"NQ tick size: {TICK_SIZE}, tick value: ${TICK_VALUE}\n")
        f.write(f"RTH: 09:30-15:45\n")
        f.write(f"Entry: bar close, ddof=0, same-bar re-entry, reversal logic\n")
        f.write(f"Runtime: {elapsed:.1f}s\n\n")

        f.write("Step-up logic:\n")
        f.write("  After entry, if price reaches rolling mean (dynamic midline):\n")
        f.write("    Long: bar high >= mean -> step-up armed\n")
        f.write("    Short: bar low <= mean -> step-up armed\n")
        f.write("  Once armed, stop moves to entry +/- offset_ticks * tick_size\n")
        f.write("  Step-up stop checked real-time (bar high/low), exit at bar close\n")
        f.write("  Original outer stop still active before midline reached\n")
        f.write("  Step-up fires once per trade\n\n")

        f.write("RESULTS\n")
        f.write("-" * 78 + "\n\n")

        # Header
        f.write(f"{'Offset':>8}  {'Trades':>6}  {'Net PnL':>10}  {'WinDay%':>8}  "
                f"{'AvgUp':>8}  {'AvgDown':>8}  {'Worst':>8}  {'MaxDD':>8}  "
                f"{'PnL/DD':>7}  {'WinR%':>6}  {'EV/Tr':>7}\n")
        f.write(f"{'':>8}  {'':>6}  {'($)':>10}  {'':>8}  "
                f"{'($)':>8}  {'($)':>8}  {'($)':>8}  {'($)':>8}  "
                f"{'':>7}  {'':>6}  {'($)':>7}\n")
        f.write("-" * 78 + "\n")

        for r in results:
            offset_str = r["offset_ticks"]
            if offset_str == "none":
                offset_str = "baseline"
            f.write(f"{offset_str:>8}  {r['total_trades']:>6}  {r['net_pnl']:>10,.0f}  "
                    f"{r['win_day_pct']:>7.1f}%  "
                    f"{r['avg_up_day']:>8,.0f}  {r['avg_down_day']:>8,.0f}  "
                    f"{r['worst_day']:>8,.0f}  {r['max_drawdown']:>8,.0f}  "
                    f"{r['pnl_maxdd_ratio']:>7.2f}  "
                    f"{r['win_rate_pct']:>5.1f}%  "
                    f"{r['ev_per_trade']:>7.2f}\n")

        f.write("\n\nSTEP-UP ACTIVITY\n")
        f.write("-" * 78 + "\n\n")
        f.write(f"{'Offset':>8}  {'Fired':>6}  {'Fired%':>7}  "
                f"{'StopHit':>7}  {'TargetAfter':>11}\n")
        f.write("-" * 78 + "\n")

        for r in results:
            offset_str = r["offset_ticks"]
            if offset_str == "none":
                offset_str = "baseline"
            f.write(f"{offset_str:>8}  {r['stepup_fired']:>6}  "
                    f"{r['stepup_fired_pct']:>6.1f}%  "
                    f"{r['stepup_stop_hit']:>7}  "
                    f"{r['target_after_stepup']:>11}\n")

        # Find best offset for daily consistency
        f.write("\n\nANALYSIS\n")
        f.write("-" * 78 + "\n\n")

        # Best by win day %
        best_winday = max(results, key=lambda r: r["win_day_pct"])
        f.write(f"Best Win Day %: offset={best_winday['offset_ticks']} "
                f"({best_winday['win_day_pct']:.1f}%)\n")

        # Best by PnL/MaxDD
        best_ratio = max(results, key=lambda r: r["pnl_maxdd_ratio"])
        f.write(f"Best PnL/MaxDD: offset={best_ratio['offset_ticks']} "
                f"({best_ratio['pnl_maxdd_ratio']:.2f})\n")

        # Best by net PnL
        best_pnl = max(results, key=lambda r: r["net_pnl"])
        f.write(f"Best Net PnL:   offset={best_pnl['offset_ticks']} "
                f"(${best_pnl['net_pnl']:,.0f})\n")

        # Best by EV/trade
        best_ev = max(results, key=lambda r: r["ev_per_trade"])
        f.write(f"Best EV/trade:  offset={best_ev['offset_ticks']} "
                f"(${best_ev['ev_per_trade']:.2f})\n")

        # Lowest max drawdown
        best_dd = min(results, key=lambda r: r["max_drawdown"])
        f.write(f"Lowest MaxDD:   offset={best_dd['offset_ticks']} "
                f"(${best_dd['max_drawdown']:,.0f})\n")

        # Daily consistency composite: rank by win_day% (primary), then PnL/MaxDD
        ranked = sorted(results, key=lambda r: (-r["win_day_pct"], -r["pnl_maxdd_ratio"]))
        f.write(f"\nBest for daily consistency (Win Day % > PnL/MaxDD):\n")
        f.write(f"  #1: offset={ranked[0]['offset_ticks']} -- "
                f"WinDay%={ranked[0]['win_day_pct']:.1f}%, "
                f"PnL/MaxDD={ranked[0]['pnl_maxdd_ratio']:.2f}, "
                f"Net PnL=${ranked[0]['net_pnl']:,.0f}\n")
        if len(ranked) > 1:
            f.write(f"  #2: offset={ranked[1]['offset_ticks']} -- "
                    f"WinDay%={ranked[1]['win_day_pct']:.1f}%, "
                    f"PnL/MaxDD={ranked[1]['pnl_maxdd_ratio']:.2f}, "
                    f"Net PnL=${ranked[1]['net_pnl']:,.0f}\n")
        if len(ranked) > 2:
            f.write(f"  #3: offset={ranked[2]['offset_ticks']} -- "
                    f"WinDay%={ranked[2]['win_day_pct']:.1f}%, "
                    f"PnL/MaxDD={ranked[2]['pnl_maxdd_ratio']:.2f}, "
                    f"Net PnL=${ranked[2]['net_pnl']:,.0f}\n")

        # Delta vs baseline
        baseline = next((r for r in results if r["offset_ticks"] == "none"), None)
        if baseline:
            f.write(f"\n\nDELTA VS BASELINE (no step-up)\n")
            f.write("-" * 78 + "\n\n")
            f.write(f"{'Offset':>8}  {'dPnL':>10}  {'dWinDay%':>9}  "
                    f"{'dMaxDD':>8}  {'dPnL/DD':>8}  {'dEV/Tr':>8}\n")
            f.write("-" * 78 + "\n")
            for r in results:
                if r["offset_ticks"] == "none":
                    continue
                d_pnl = r["net_pnl"] - baseline["net_pnl"]
                d_wd = r["win_day_pct"] - baseline["win_day_pct"]
                d_dd = r["max_drawdown"] - baseline["max_drawdown"]
                d_ratio = r["pnl_maxdd_ratio"] - baseline["pnl_maxdd_ratio"]
                d_ev = r["ev_per_trade"] - baseline["ev_per_trade"]
                f.write(f"{r['offset_ticks']:>8}  {d_pnl:>+10,.0f}  {d_wd:>+8.1f}%  "
                        f"{d_dd:>+8,.0f}  {d_ratio:>+8.2f}  {d_ev:>+8.2f}\n")

    print(f"  Wrote {summary_path}")
    print(f"\nDone.")


if __name__ == "__main__":
    main()
