"""Step-up stop offset grid with N=2 circuit breaker AND $2,500 max daily loss.

Combines three mechanisms:
  1. Step-up stop (move stop to entry +/- offset after midline reached)
  2. Consecutive-stop circuit breaker N=2 (per-side, daily reset, forced opposite resets both)
  3. Max daily loss $2,500 (track daily PnL incl costs, halt when <= -$2500, reset daily)

Base config: LB=100, inner=0.75, outer=1.75, qty=1, no martingale
RTH: 09:30-15:45, entry at bar close, ddof=0
NQ tick_size=0.25, tick_value=$5.00, cost=$4/trade

Step-up logic:
  - After entry, monitor if price reaches the rolling mean (dynamic midline)
  - For long: bar high >= mean triggers step-up
  - For short: bar low <= mean triggers step-up
  - Once triggered, move stop to: entry_price + offset_ticks * tick_size
    (long: positive offset = above entry; short: stop = entry - offset)
  - Step-up stop checked every bar (real-time via high/low), exit at bar close
  - Original outer stop still active before midline reached
  - Step-up fires once per trade
  - Step-up exit counts toward consecutive stop counter if PnL < 0

Outputs:
  stepup-with-cb-mdl.csv       -- one row per offset
  stepup-with-cb-mdl-summary.txt -- text summary
"""
from __future__ import annotations

import csv
import sys
import time
from pathlib import Path

import numpy as np


# -- Constants --
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

MAX_CONSEC = 2           # Circuit breaker threshold (fixed)
MAX_DAILY_LOSS = 2500.0  # Max daily loss threshold (fixed)

# Step-up offsets to test (in ticks); None = no step-up (baseline)
STEPUP_OFFSETS = [None, -40, -20, -10, 0, 10, 20, 40]

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


# -- Simulate with step-up + CB N=2 + MDL $2500 --
def simulate(bars: dict, bands: dict, stepup_offset_ticks):
    """
    Run simulation with:
      - Circuit breaker N=2
      - Max daily loss $2,500
      - Optional step-up stop at given offset (None = disabled)

    Returns summary dict.
    """
    n = bars["n"]
    date_int = bars["date_int"]
    time_str = bars["time_str"]
    high = bars["high"]
    low = bars["low"]
    close = bars["close"]

    band_it = bands["inner_top"]
    band_ib = bands["inner_bot"]
    band_ot = bands["outer_top"]
    band_ob = bands["outer_bot"]
    band_mean = bands["mean"]

    max_consec = MAX_CONSEC
    max_daily_loss = MAX_DAILY_LOSS

    # Position state
    in_position = False
    direction = None
    entry_price = 0.0
    target_price = 0.0
    stop_price = 0.0
    entry_target_offset = 0.0
    entry_stop_offset = 0.0

    # Step-up state
    stepup_armed = False
    stepup_stop = 0.0

    # Consecutive stop state
    consec_long = 0
    consec_short = 0
    last_date = 0

    # Max daily loss state
    daily_pnl_running = 0.0
    daily_halted = False
    days_halted = 0

    # Tracking
    trades = []
    daily_pnls = {}  # date_int -> total pnl ($)

    equity = 0.0
    peak_equity = 0.0
    max_dd = 0.0

    # Step-up tracking
    stepup_fired_count = 0
    stepup_stop_hit_count = 0

    def check_buy_signal(idx):
        if np.isnan(band_it[idx]):
            return False
        return low[idx] <= band_ib[idx] and low[idx] > band_ob[idx]

    def check_sell_signal(idx):
        if np.isnan(band_it[idx]):
            return False
        return high[idx] >= band_it[idx] and high[idx] < band_ot[idx]

    def compute_entry(idx, dir_):
        ep = close[idx]
        t_offset = band_it[idx] - band_ib[idx]
        if dir_ == "long":
            s_offset = band_ib[idx] - band_ob[idx]
            tp = ep + t_offset
            sp = ep - s_offset
        else:
            s_offset = band_ot[idx] - band_it[idx]
            tp = ep - t_offset
            sp = ep + s_offset
        return ep, tp, sp, t_offset, s_offset

    def record_pnl(pnl_dollar, exit_bar_date_int):
        nonlocal equity, peak_equity, max_dd, daily_pnl_running
        equity += pnl_dollar
        if equity > peak_equity:
            peak_equity = equity
        dd = peak_equity - equity
        if dd > max_dd:
            max_dd = dd
        if exit_bar_date_int not in daily_pnls:
            daily_pnls[exit_bar_date_int] = 0.0
        daily_pnls[exit_bar_date_int] += pnl_dollar
        daily_pnl_running += pnl_dollar

    def check_daily_halt():
        nonlocal daily_halted, days_halted
        if max_daily_loss > 0 and daily_pnl_running <= -max_daily_loss:
            if not daily_halted:
                daily_halted = True
                days_halted += 1
            return True
        return daily_halted

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
        if np.isnan(band_it[i]):
            i += 1
            continue

        # Daily reset
        today = date_int[i]
        if today != last_date:
            last_date = today
            consec_long = 0
            consec_short = 0
            daily_pnl_running = 0.0
            daily_halted = False

        # If daily halt is active, flatten any position and skip
        if daily_halted:
            if in_position:
                if direction == "long":
                    pnl_pts = close[i] - entry_price
                else:
                    pnl_pts = entry_price - close[i]
                pnl_dollar = pnl_pts * POINT_VALUE - COST_PER_TRADE
                is_loss = (pnl_dollar < 0)
                trades.append({
                    "date": date_int[i], "dir": direction, "entry": entry_price,
                    "exit_price": close[i], "pnl_dollar": round(pnl_dollar, 2),
                    "exit_type": "DAILY_HALT",
                })
                record_pnl(pnl_dollar, date_int[i])
                update_consec(direction, is_loss)
                in_position = False
                direction = None
                stepup_armed = False
            i += 1
            continue

        if not in_position:
            # -- Not in position: check for entry --
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

            # Forced opposite reset
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
            stepup_armed = False
            i += 1
            continue

        # -- In position: check bar i for resolution --

        # Step-up midline check (before exit checks)
        if stepup_offset_ticks is not None and not stepup_armed:
            if not np.isnan(band_mean[i]):
                if direction == "long" and high[i] >= band_mean[i]:
                    stepup_armed = True
                    stepup_stop = entry_price + stepup_offset_ticks * TICK_SIZE
                    stepup_fired_count += 1
                elif direction == "short" and low[i] <= band_mean[i]:
                    stepup_armed = True
                    stepup_stop = entry_price - stepup_offset_ticks * TICK_SIZE
                    stepup_fired_count += 1

        # Target check
        if direction == "long":
            target_hit = high[i] >= target_price
            stop_hit = low[i] <= stop_price
            reversal_signal = check_sell_signal(i) and is_rth(time_str[i])
        else:
            target_hit = low[i] <= target_price
            stop_hit = high[i] >= stop_price
            reversal_signal = check_buy_signal(i) and is_rth(time_str[i])

        # Step-up stop check (real-time via high/low)
        stepup_stop_hit = False
        if stepup_armed:
            if direction == "long" and low[i] <= stepup_stop:
                stepup_stop_hit = True
            elif direction == "short" and high[i] >= stepup_stop:
                stepup_stop_hit = True

        # Priority: target > stop > stepup_stop > reversal
        # But stepup_stop fires BEFORE outer stop per the stepup logic:
        # "Step-up stop checked on every bar ... Original outer band stop still exists -- fires if hit before midline reached"
        # Once stepup is armed, the stepup stop replaces the outer stop conceptually.
        # Priority: target > stepup_stop (if armed) > outer_stop (if not armed) > reversal

        if target_hit and stop_hit and not stepup_armed:
            # Both target and outer stop hit, no step-up: use close to decide
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
            })
            record_pnl(pnl_dollar, date_int[i])
            update_consec(direction, is_loss)
            in_position = False
            direction = None
            stepup_armed = False
            if check_daily_halt():
                continue
            continue  # same-bar re-entry

        elif target_hit and stepup_stop_hit and stepup_armed:
            # Both target and step-up stop hit on same bar
            # Target takes priority per spec (target > stop > reversal)
            pnl_pts = entry_target_offset
            pnl_dollar = pnl_pts * POINT_VALUE - COST_PER_TRADE
            trades.append({
                "date": date_int[i], "dir": direction, "entry": entry_price,
                "exit_price": target_price, "pnl_dollar": round(pnl_dollar, 2),
                "exit_type": "TARGET",
            })
            record_pnl(pnl_dollar, date_int[i])
            update_consec(direction, False)
            in_position = False
            direction = None
            stepup_armed = False
            if check_daily_halt():
                continue
            continue

        elif target_hit:
            # Target hit (no conflict)
            pnl_pts = entry_target_offset
            pnl_dollar = pnl_pts * POINT_VALUE - COST_PER_TRADE
            trades.append({
                "date": date_int[i], "dir": direction, "entry": entry_price,
                "exit_price": target_price, "pnl_dollar": round(pnl_dollar, 2),
                "exit_type": "TARGET",
            })
            record_pnl(pnl_dollar, date_int[i])
            update_consec(direction, False)
            in_position = False
            direction = None
            stepup_armed = False
            if check_daily_halt():
                continue
            continue

        elif stepup_stop_hit and stepup_armed:
            # Step-up stop hit -- exit at bar close
            if direction == "long":
                pnl_pts = close[i] - entry_price
            else:
                pnl_pts = entry_price - close[i]
            pnl_dollar = pnl_pts * POINT_VALUE - COST_PER_TRADE
            is_loss = (pnl_dollar < 0)
            stepup_stop_hit_count += 1
            trades.append({
                "date": date_int[i], "dir": direction, "entry": entry_price,
                "exit_price": close[i], "pnl_dollar": round(pnl_dollar, 2),
                "exit_type": "STEPUP_STOP",
            })
            record_pnl(pnl_dollar, date_int[i])
            update_consec(direction, is_loss)
            in_position = False
            direction = None
            stepup_armed = False
            if check_daily_halt():
                continue
            continue

        elif stop_hit and not stepup_armed:
            # Outer stop hit (step-up not armed)
            pnl_pts = -entry_stop_offset
            pnl_dollar = pnl_pts * POINT_VALUE - COST_PER_TRADE
            trades.append({
                "date": date_int[i], "dir": direction, "entry": entry_price,
                "exit_price": stop_price, "pnl_dollar": round(pnl_dollar, 2),
                "exit_type": "STOP",
            })
            record_pnl(pnl_dollar, date_int[i])
            update_consec(direction, True)
            in_position = False
            direction = None
            stepup_armed = False
            if check_daily_halt():
                continue
            continue

        elif stop_hit and stepup_armed:
            # Outer stop also hit but step-up is armed -- the outer stop
            # still exists. If the outer stop level is violated AND the
            # step-up stop level is NOT violated, outer stop fires.
            # If both violated, step-up stop takes priority (it's tighter
            # or equal for protective offsets).
            # We already handled stepup_stop_hit above, so if we're here
            # it means outer stop hit but stepup stop was NOT hit.
            # Outer stop fires.
            pnl_pts = -entry_stop_offset
            pnl_dollar = pnl_pts * POINT_VALUE - COST_PER_TRADE
            trades.append({
                "date": date_int[i], "dir": direction, "entry": entry_price,
                "exit_price": stop_price, "pnl_dollar": round(pnl_dollar, 2),
                "exit_type": "STOP",
            })
            record_pnl(pnl_dollar, date_int[i])
            update_consec(direction, True)
            in_position = False
            direction = None
            stepup_armed = False
            if check_daily_halt():
                continue
            continue

        elif reversal_signal:
            # Reversal: exit at close, enter opposite
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
                "exit_type": "REVERSAL",
            })
            record_pnl(pnl_dollar, date_int[i])
            update_consec(direction, is_loss)
            stepup_armed = False

            # Check daily halt after reversal exit
            if check_daily_halt():
                in_position = False
                direction = None
                continue

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

            # Forced opposite reset
            if new_direction == "long" and short_blocked:
                consec_long = 0
                consec_short = 0
            elif new_direction == "short" and long_blocked:
                consec_long = 0
                consec_short = 0

            entry_price, target_price, stop_price, entry_target_offset, entry_stop_offset = compute_entry(i, new_direction)
            direction = new_direction
            in_position = True
            stepup_armed = False
            i += 1
            continue

        # No resolution this bar
        i += 1

    # -- Compute summary stats --
    total_trades = len(trades)
    if total_trades == 0:
        return None

    total_pnl = sum(t["pnl_dollar"] for t in trades)
    winning_trades = sum(1 for t in trades if t["pnl_dollar"] > 0)
    win_rate = (winning_trades / total_trades * 100)
    ev_per_trade = total_pnl / total_trades

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

    offset_label = f"{stepup_offset_ticks}" if stepup_offset_ticks is not None else "none"

    return {
        "offset_ticks": offset_label,
        "total_trades": total_trades,
        "net_pnl": round(total_pnl, 2),
        "up_days": up_days,
        "down_days": down_days,
        "total_days": total_days,
        "win_day_pct": round(win_day_pct, 2),
        "avg_up_day": round(avg_up_day, 2),
        "avg_down_day": round(avg_down_day, 2),
        "worst_day": round(worst_day, 2),
        "max_drawdown": round(max_dd, 2),
        "pnl_maxdd_ratio": round(pnl_dd_ratio, 4),
        "win_rate_pct": round(win_rate, 2),
        "ev_per_trade": round(ev_per_trade, 2),
        "stepup_fired": stepup_fired_count,
        "stepup_fired_pct": round(stepup_fired_count / total_trades * 100, 2),
        "stepup_stop_hit": stepup_stop_hit_count,
        "days_mdl_triggered": days_halted,
    }


# -- Main --
def main():
    t0 = time.time()

    if not DATA_PATH.exists():
        print(f"ERROR: Cannot find bar data at {DATA_PATH}")
        sys.exit(1)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Loading 250-tick bars from {DATA_PATH}...")
    bars = load_bars(DATA_PATH)
    print(f"Loaded {bars['n']} bars")

    print(f"Computing bands (LB={LOOKBACK}, inner={INNER_MULT}, outer={OUTER_MULT})...")
    bands = compute_bands(bars)

    print(f"\nConfig: LB={LOOKBACK}, Inner={INNER_MULT}, Outer={OUTER_MULT}, Qty=1 (no martingale)")
    print(f"Circuit breaker: N={MAX_CONSEC} (per-side, daily reset, forced opposite resets both)")
    print(f"Max daily loss: ${MAX_DAILY_LOSS:.0f}")
    print(f"Cost: ${COST_PER_TRADE}/trade")
    print(f"Step-up offsets to test: {STEPUP_OFFSETS}\n")

    results = []
    for offset in STEPUP_OFFSETS:
        label = f"offset={offset}" if offset is not None else "baseline (no step-up)"
        print(f"  Simulating {label}...")
        metrics = simulate(bars, bands, offset)
        if metrics is not None:
            results.append(metrics)
            print(f"    Trades={metrics['total_trades']}, "
                  f"NetPnL=${metrics['net_pnl']:,.0f}, "
                  f"WinDay%={metrics['win_day_pct']:.1f}%, "
                  f"StepUpFired={metrics['stepup_fired']}, "
                  f"MDL days={metrics['days_mdl_triggered']}")

    elapsed = time.time() - t0
    print(f"\nCompleted in {elapsed:.1f}s")

    # -- Write CSV --
    csv_path = OUTPUT_DIR / "stepup-with-cb-mdl.csv"
    fieldnames = [
        "offset_ticks", "total_trades", "net_pnl",
        "up_days", "down_days", "total_days",
        "win_day_pct", "avg_up_day", "avg_down_day", "worst_day",
        "max_drawdown", "pnl_maxdd_ratio", "win_rate_pct", "ev_per_trade",
        "stepup_fired", "stepup_fired_pct",
        "stepup_stop_hit", "days_mdl_triggered",
    ]
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(results)
    print(f"  Wrote {csv_path}")

    # -- Write Summary --
    summary_path = OUTPUT_DIR / "stepup-with-cb-mdl-summary.txt"

    baseline = next((r for r in results if r["offset_ticks"] == "none"), None)

    lines = []
    lines.append("=" * 100)
    lines.append("STEP-UP STOP OFFSET GRID (with N=2 circuit breaker + $2,500 max daily loss)")
    lines.append("=" * 100)
    lines.append(f"Config: LB={LOOKBACK}, Inner={INNER_MULT}, Outer={OUTER_MULT}, Qty=1 (no martingale)")
    lines.append(f"Circuit breaker: N={MAX_CONSEC} (per-side, daily reset, forced opposite resets both)")
    lines.append(f"Max daily loss: ${MAX_DAILY_LOSS:.0f} (track daily PnL incl costs, halt when <= -${MAX_DAILY_LOSS:.0f}, reset daily)")
    lines.append(f"Data: {DATA_PATH.name} ({bars['n']} bars)")
    lines.append(f"Cost: ${COST_PER_TRADE}/trade")
    lines.append(f"NQ tick size: {TICK_SIZE}, tick value: ${TICK_VALUE}")
    lines.append(f"RTH: 09:30-15:45, Entry at bar close, ddof=0")
    lines.append(f"Runtime: {elapsed:.1f}s")
    lines.append("")
    lines.append("Step-up logic:")
    lines.append("  After entry, if price reaches rolling mean (dynamic midline):")
    lines.append("    Long: bar high >= mean -> step-up armed")
    lines.append("    Short: bar low <= mean -> step-up armed")
    lines.append("  Once armed, stop moves to entry +/- offset_ticks * tick_size")
    lines.append("  Step-up stop checked real-time (bar high/low), exit at bar close")
    lines.append("  Original outer stop still active before midline reached")
    lines.append("  Step-up fires once per trade")
    lines.append("  Step-up exit counts toward consec stop counter if PnL < 0")
    lines.append("")

    # Results table
    lines.append("RESULTS")
    lines.append("-" * 100)

    hdr = (f"{'Offset':>8}  {'Trades':>6}  {'Net PnL':>10}  {'WinDay%':>8}  "
           f"{'AvgUp':>8}  {'AvgDown':>8}  {'Worst':>8}  {'MaxDD':>8}  "
           f"{'PnL/DD':>7}  {'WinR%':>6}  {'EV/Tr':>7}  {'MDLdays':>7}")
    lines.append(hdr)
    hdr2 = (f"{'':>8}  {'':>6}  {'($)':>10}  {'':>8}  "
            f"{'($)':>8}  {'($)':>8}  {'($)':>8}  {'($)':>8}  "
            f"{'':>7}  {'':>6}  {'($)':>7}  {'':>7}")
    lines.append(hdr2)
    lines.append("-" * 100)

    for r in results:
        offset_str = r["offset_ticks"]
        if offset_str == "none":
            offset_str = "baseline"
        lines.append(
            f"{offset_str:>8}  {r['total_trades']:>6}  {r['net_pnl']:>10,.0f}  "
            f"{r['win_day_pct']:>7.1f}%  "
            f"{r['avg_up_day']:>8,.0f}  {r['avg_down_day']:>8,.0f}  "
            f"{r['worst_day']:>8,.0f}  {r['max_drawdown']:>8,.0f}  "
            f"{r['pnl_maxdd_ratio']:>7.2f}  "
            f"{r['win_rate_pct']:>5.1f}%  "
            f"{r['ev_per_trade']:>7.2f}  "
            f"{r['days_mdl_triggered']:>7}"
        )

    lines.append("")
    lines.append("STEP-UP ACTIVITY")
    lines.append("-" * 100)
    lines.append(f"{'Offset':>8}  {'Fired':>6}  {'Fired%':>7}  {'StopHit':>7}")
    lines.append("-" * 100)

    for r in results:
        offset_str = r["offset_ticks"]
        if offset_str == "none":
            offset_str = "baseline"
        lines.append(
            f"{offset_str:>8}  {r['stepup_fired']:>6}  "
            f"{r['stepup_fired_pct']:>6.1f}%  "
            f"{r['stepup_stop_hit']:>7}"
        )

    # Analysis
    lines.append("")
    lines.append("ANALYSIS")
    lines.append("-" * 100)

    best_winday = max(results, key=lambda r: r["win_day_pct"])
    best_ratio = max(results, key=lambda r: r["pnl_maxdd_ratio"])
    best_pnl = max(results, key=lambda r: r["net_pnl"])
    best_ev = max(results, key=lambda r: r["ev_per_trade"])
    best_dd = min(results, key=lambda r: r["max_drawdown"])
    best_worst = max(results, key=lambda r: r["worst_day"])

    lines.append(f"Best Win Day %: offset={best_winday['offset_ticks']} ({best_winday['win_day_pct']:.1f}%)")
    lines.append(f"Best PnL/MaxDD: offset={best_ratio['offset_ticks']} ({best_ratio['pnl_maxdd_ratio']:.2f})")
    lines.append(f"Best Net PnL:   offset={best_pnl['offset_ticks']} (${best_pnl['net_pnl']:,.0f})")
    lines.append(f"Best EV/trade:  offset={best_ev['offset_ticks']} (${best_ev['ev_per_trade']:.2f})")
    lines.append(f"Lowest MaxDD:   offset={best_dd['offset_ticks']} (${best_dd['max_drawdown']:,.0f})")
    lines.append(f"Best worst day: offset={best_worst['offset_ticks']} (${best_worst['worst_day']:,.0f})")

    # Daily consistency composite
    ranked = sorted(results, key=lambda r: (-r["win_day_pct"], -r["pnl_maxdd_ratio"]))
    lines.append(f"\nBest for daily consistency (Win Day % > PnL/MaxDD):")
    for rank, r in enumerate(ranked[:3], 1):
        lines.append(
            f"  #{rank}: offset={r['offset_ticks']} -- "
            f"WinDay%={r['win_day_pct']:.1f}%, "
            f"PnL/MaxDD={r['pnl_maxdd_ratio']:.2f}, "
            f"Net PnL=${r['net_pnl']:,.0f}"
        )

    # Delta vs baseline
    if baseline:
        lines.append("")
        lines.append("DELTA VS BASELINE (no step-up, with CB N=2 + MDL $2,500)")
        lines.append("-" * 100)
        lines.append(
            f"{'Offset':>8}  {'dPnL':>10}  {'dWinDay%':>9}  "
            f"{'dMaxDD':>8}  {'dPnL/DD':>8}  {'dEV/Tr':>8}  {'dWorst':>8}"
        )
        lines.append("-" * 100)
        for r in results:
            if r["offset_ticks"] == "none":
                continue
            d_pnl = r["net_pnl"] - baseline["net_pnl"]
            d_wd = r["win_day_pct"] - baseline["win_day_pct"]
            d_dd = r["max_drawdown"] - baseline["max_drawdown"]
            d_ratio = r["pnl_maxdd_ratio"] - baseline["pnl_maxdd_ratio"]
            d_ev = r["ev_per_trade"] - baseline["ev_per_trade"]
            d_worst = r["worst_day"] - baseline["worst_day"]
            lines.append(
                f"{r['offset_ticks']:>8}  {d_pnl:>+10,.0f}  {d_wd:>+8.1f}%  "
                f"{d_dd:>+8,.0f}  {d_ratio:>+8.2f}  {d_ev:>+8.2f}  {d_worst:>+8,.0f}"
            )

    lines.append("")

    with open(summary_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"  Wrote {summary_path}")

    # Print to stdout
    print("\n" + "\n".join(lines))


if __name__ == "__main__":
    main()
