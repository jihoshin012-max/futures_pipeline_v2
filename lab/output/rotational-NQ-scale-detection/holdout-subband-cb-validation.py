"""Holdout validation of top sub-band configs on NQ-250tick-holdout data.

Runs 4 configs (baseline + 3 sub-band variants) on holdout period
(12/15/2025 - 3/13/2026) and reports calibration vs holdout comparison.

Simulation logic copied verbatim from subband-with-cb-analysis.py.
"""
from __future__ import annotations

import csv
import sys
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
MAX_DAILY_LOSS = 2500.00
CIRCUIT_BREAKER_N = 2  # main band CB
EMA_PERIOD = 50
EMA_THRESHOLD = 0.1

RTH_START = "09:30:00"
RTH_END = "15:45:00"

DATA_PATH = Path(r"C:\Projects\futures_pipeline\data\NQ-250tick-holdout.csv")
OUTPUT_DIR = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")


# -- Load 250-tick bar data --
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


# -- Compute EMA --
def compute_ema(closes: np.ndarray, period: int) -> np.ndarray:
    n = len(closes)
    ema = np.full(n, np.nan)
    if n < period:
        return ema
    ema[period - 1] = np.mean(closes[:period])
    k = 2.0 / (period + 1)
    for i in range(period, n):
        ema[i] = closes[i] * k + ema[i - 1] * (1 - k)
    return ema


def is_rth(time_hms: str) -> bool:
    return RTH_START <= time_hms <= RTH_END


# -- Simulate --
def simulate(bars: dict, bands: dict, ema: np.ndarray,
             target_mode: str, switchback_rule: str,
             sub_inner_mult: float, sub_outer_mult: float,
             sub_cb_n: int, use_subband: bool):
    """
    Run rangefade rotation sim with:
    - Main CB N=2, MDL $2500
    - Sub-band circuit breaker (sub_cb_n consecutive sub-band stops -> switch back)
    - Counter reset on switch-back (both consec_long and consec_short -> 0)
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
    is_subband_trade = False

    # Consecutive stop state (main bands)
    consec_long = 0
    consec_short = 0
    last_date = 0

    # Sub-band directional mode
    subband_mode = False
    subband_allowed_dir = None
    subband_wins_today = 0
    subband_trades_today = 0
    subband_switched_back = False
    subband_consec_stops = 0

    # Tracking
    main_trades = []
    sub_trades = []
    daily_pnls = {}
    sub_cb_fire_count = 0
    subband_activation_count = 0

    equity = 0.0
    peak_equity = 0.0
    max_dd = 0.0
    daily_loss = 0.0

    def check_buy_signal(idx):
        if np.isnan(it[idx]):
            return False
        return low[idx] <= ib[idx] and low[idx] > ob[idx]

    def check_sell_signal(idx):
        if np.isnan(it[idx]):
            return False
        return high[idx] >= it[idx] and high[idx] < ot[idx]

    def compute_main_entry(idx, dir_):
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

    def compute_subband_levels(idx, dir_):
        if dir_ == "short":
            inner_band = it[idx]
            outer_band = ot[idx]
        else:
            inner_band = ib[idx]
            outer_band = ob[idx]

        sub_mid = (inner_band + outer_band) / 2.0
        half_width = abs(outer_band - inner_band) / 2.0

        if dir_ == "short":
            sub_inner = sub_mid - sub_inner_mult * half_width
            sub_outer = sub_mid + sub_outer_mult * half_width
        else:
            sub_inner = sub_mid + sub_inner_mult * half_width
            sub_outer = sub_mid - sub_outer_mult * half_width

        return sub_inner, sub_outer, half_width

    def compute_subband_target_offset(idx, dir_, half_width):
        standard_offset = 2.0 * sub_inner_mult * half_width

        if target_mode == "1.5x":
            return standard_offset * 1.5
        elif target_mode == "3.0x":
            return standard_offset * 3.0
        elif target_mode == "main_band_width":
            return it[idx] - ib[idx]
        elif target_mode == "to_opposite_inner":
            if dir_ == "short":
                return close[idx] - ib[idx]
            else:
                return it[idx] - close[idx]
        return standard_offset

    def check_subband_entry(idx, allowed_dir):
        if np.isnan(it[idx]):
            return False
        sub_inner, sub_outer, _ = compute_subband_levels(idx, allowed_dir)
        if allowed_dir == "short":
            return high[idx] >= sub_inner and high[idx] < sub_outer
        else:
            return low[idx] <= sub_inner and low[idx] > sub_outer

    def enter_subband(idx, allowed_dir):
        sub_inner, sub_outer, half_width = compute_subband_levels(idx, allowed_dir)
        ep = close[idx]
        t_offset = compute_subband_target_offset(idx, allowed_dir, half_width)
        s_offset = (sub_outer_mult + sub_inner_mult) * half_width
        if allowed_dir == "short":
            tp = ep - t_offset
            sp = ep + s_offset
        else:
            tp = ep + t_offset
            sp = ep - s_offset
        return ep, tp, sp, t_offset, s_offset

    def record_pnl(pnl_dollar, exit_bar_date_int):
        nonlocal equity, peak_equity, max_dd, daily_loss
        equity += pnl_dollar
        daily_loss += pnl_dollar
        if equity > peak_equity:
            peak_equity = equity
        dd = peak_equity - equity
        if dd > max_dd:
            max_dd = dd
        if exit_bar_date_int not in daily_pnls:
            daily_pnls[exit_bar_date_int] = 0.0
        daily_pnls[exit_bar_date_int] += pnl_dollar

    def mdl_breached():
        return daily_loss <= -MAX_DAILY_LOSS

    def check_ema_confirms(idx, failing_side):
        if np.isnan(ema[idx]) or np.isnan(it[idx]):
            return False
        band_width = it[idx] - ib[idx]
        if failing_side == "long":
            return ema[idx] <= ib[idx] + EMA_THRESHOLD * band_width
        else:
            return ema[idx] >= it[idx] - EMA_THRESHOLD * band_width

    def should_switch_back(idx):
        if switchback_rule == "sub_cb_only":
            return False
        elif switchback_rule == "R1":
            return subband_wins_today >= 1
        elif switchback_rule == "R5":
            return subband_trades_today >= 10
        return False

    def do_switch_back():
        nonlocal subband_mode, subband_allowed_dir, subband_switched_back
        nonlocal consec_long, consec_short
        subband_mode = False
        subband_allowed_dir = None
        subband_switched_back = True
        consec_long = 0
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
            subband_mode = False
            subband_allowed_dir = None
            subband_wins_today = 0
            subband_trades_today = 0
            subband_switched_back = False
            subband_consec_stops = 0
            daily_loss = 0.0
            if in_position:
                in_position = False
                direction = None
                is_subband_trade = False

        # MDL check
        if mdl_breached() and not in_position:
            i += 1
            continue

        if not in_position:
            if not is_rth(time_str[i]):
                i += 1
                continue

            # Check switch-back before trying sub-band entry
            if subband_mode and use_subband and should_switch_back(i):
                do_switch_back()

            if subband_mode and use_subband:
                # Sub-band directional mode
                if check_subband_entry(i, subband_allowed_dir):
                    entry_price, target_price, stop_price, entry_target_offset, entry_stop_offset = enter_subband(i, subband_allowed_dir)
                    direction = subband_allowed_dir
                    in_position = True
                    is_subband_trade = True
                    i += 1
                    continue
                else:
                    i += 1
                    continue
            else:
                # Normal main-band entry
                buy_sig = check_buy_signal(i)
                sell_sig = check_sell_signal(i)

                dir_to_enter = None
                if buy_sig and sell_sig:
                    i += 1
                    continue
                elif buy_sig:
                    long_blocked = consec_long >= CIRCUIT_BREAKER_N
                    if long_blocked:
                        if use_subband and not subband_mode and not subband_switched_back:
                            if check_ema_confirms(i, "long"):
                                subband_mode = True
                                subband_allowed_dir = "short"
                                subband_wins_today = 0
                                subband_trades_today = 0
                                subband_consec_stops = 0
                                subband_activation_count += 1
                                continue
                        i += 1
                        continue
                    dir_to_enter = "long"
                elif sell_sig:
                    short_blocked = consec_short >= CIRCUIT_BREAKER_N
                    if short_blocked:
                        if use_subband and not subband_mode and not subband_switched_back:
                            if check_ema_confirms(i, "short"):
                                subband_mode = True
                                subband_allowed_dir = "long"
                                subband_wins_today = 0
                                subband_trades_today = 0
                                subband_consec_stops = 0
                                subband_activation_count += 1
                                continue
                        i += 1
                        continue
                    dir_to_enter = "short"
                else:
                    i += 1
                    continue

                # Forced opposite reset
                short_blocked = consec_short >= CIRCUIT_BREAKER_N
                long_blocked = consec_long >= CIRCUIT_BREAKER_N
                if dir_to_enter == "long" and short_blocked:
                    consec_long = 0
                    consec_short = 0
                elif dir_to_enter == "short" and long_blocked:
                    consec_long = 0
                    consec_short = 0

                entry_price, target_price, stop_price, entry_target_offset, entry_stop_offset = compute_main_entry(i, dir_to_enter)
                direction = dir_to_enter
                in_position = True
                is_subband_trade = False
                i += 1
                continue

        # -- In position: check resolution --
        if direction == "long":
            target_hit = high[i] >= target_price
            stop_hit = low[i] <= stop_price
            if is_subband_trade:
                reversal_signal = False
            else:
                reversal_signal = check_sell_signal(i) and is_rth(time_str[i])
        else:
            target_hit = low[i] <= target_price
            stop_hit = high[i] >= stop_price
            if is_subband_trade:
                reversal_signal = False
            else:
                reversal_signal = check_buy_signal(i) and is_rth(time_str[i])

        def handle_subband_exit(is_win, is_loss):
            nonlocal subband_consec_stops, sub_cb_fire_count
            if is_loss:
                subband_consec_stops += 1
                if sub_cb_n > 0 and subband_consec_stops >= sub_cb_n:
                    sub_cb_fire_count += 1
                    do_switch_back()
            elif is_win:
                subband_consec_stops = 0

        # Priority resolution
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
            is_win = (exit_type == "TARGET")
            is_loss = (exit_type == "STOP")
            trade_rec = {
                "date": date_int[i], "dir": direction, "entry": entry_price,
                "pnl_dollar": round(pnl_dollar, 2), "exit_type": exit_type,
                "is_subband": is_subband_trade,
            }
            if is_subband_trade:
                sub_trades.append(trade_rec)
                subband_trades_today += 1
                if is_win:
                    subband_wins_today += 1
                handle_subband_exit(is_win, is_loss)
            else:
                main_trades.append(trade_rec)
            record_pnl(pnl_dollar, date_int[i])

            if not is_subband_trade:
                if direction == "long":
                    if is_loss:
                        consec_long += 1
                    else:
                        consec_long = 0
                else:
                    if is_loss:
                        consec_short += 1
                    else:
                        consec_short = 0

            in_position = False
            direction = None
            is_subband_trade = False
            continue

        elif target_hit:
            pnl_pts = entry_target_offset
            pnl_dollar = pnl_pts * POINT_VALUE - COST_PER_TRADE
            trade_rec = {
                "date": date_int[i], "dir": direction, "entry": entry_price,
                "pnl_dollar": round(pnl_dollar, 2), "exit_type": "TARGET",
                "is_subband": is_subband_trade,
            }
            if is_subband_trade:
                sub_trades.append(trade_rec)
                subband_trades_today += 1
                subband_wins_today += 1
                handle_subband_exit(True, False)
            else:
                main_trades.append(trade_rec)
            record_pnl(pnl_dollar, date_int[i])

            if not is_subband_trade:
                if direction == "long":
                    consec_long = 0
                else:
                    consec_short = 0

            in_position = False
            direction = None
            is_subband_trade = False
            continue

        elif stop_hit:
            pnl_pts = -entry_stop_offset
            pnl_dollar = pnl_pts * POINT_VALUE - COST_PER_TRADE
            trade_rec = {
                "date": date_int[i], "dir": direction, "entry": entry_price,
                "pnl_dollar": round(pnl_dollar, 2), "exit_type": "STOP",
                "is_subband": is_subband_trade,
            }
            if is_subband_trade:
                sub_trades.append(trade_rec)
                subband_trades_today += 1
                handle_subband_exit(False, True)
            else:
                main_trades.append(trade_rec)
            record_pnl(pnl_dollar, date_int[i])

            if not is_subband_trade:
                if direction == "long":
                    consec_long += 1
                else:
                    consec_short += 1

            in_position = False
            direction = None
            is_subband_trade = False
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
            trade_rec = {
                "date": date_int[i], "dir": direction, "entry": entry_price,
                "pnl_dollar": round(pnl_dollar, 2), "exit_type": "REVERSAL",
                "is_subband": False,
            }
            main_trades.append(trade_rec)
            record_pnl(pnl_dollar, date_int[i])

            if direction == "long":
                if is_loss:
                    consec_long += 1
                else:
                    consec_long = 0
            else:
                if is_loss:
                    consec_short += 1
                else:
                    consec_short = 0

            long_blocked = consec_long >= CIRCUIT_BREAKER_N
            short_blocked = consec_short >= CIRCUIT_BREAKER_N

            if new_direction == "long" and long_blocked:
                in_position = False
                direction = None
                is_subband_trade = False
                i += 1
                continue
            if new_direction == "short" and short_blocked:
                in_position = False
                direction = None
                is_subband_trade = False
                i += 1
                continue

            if new_direction == "long" and short_blocked:
                consec_long = 0
                consec_short = 0
            elif new_direction == "short" and long_blocked:
                consec_long = 0
                consec_short = 0

            entry_price, target_price, stop_price, entry_target_offset, entry_stop_offset = compute_main_entry(i, new_direction)
            direction = new_direction
            in_position = True
            is_subband_trade = False
            i += 1
            continue

        i += 1

    # -- Summary stats --
    all_trades = main_trades + sub_trades
    total_trades = len(all_trades)
    total_pnl = sum(t["pnl_dollar"] for t in all_trades)
    ev_per_trade = (total_pnl / total_trades) if total_trades > 0 else 0.0

    winning_trades = sum(1 for t in all_trades if t["pnl_dollar"] > 0)
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0

    sub_total = len(sub_trades)
    sub_winning = sum(1 for t in sub_trades if t["pnl_dollar"] > 0)
    sub_win_rate = (sub_winning / sub_total * 100) if sub_total > 0 else 0.0
    sub_pnl = sum(t["pnl_dollar"] for t in sub_trades)
    sub_ev = (sub_pnl / sub_total) if sub_total > 0 else 0.0

    up_days = sum(1 for v in daily_pnls.values() if v > 0)
    down_days = sum(1 for v in daily_pnls.values() if v <= 0)
    total_days = up_days + down_days
    win_day_pct = (up_days / total_days * 100) if total_days > 0 else 0.0
    worst_day = min(daily_pnls.values()) if daily_pnls else 0.0

    up_vals = [v for v in daily_pnls.values() if v > 0]
    down_vals = [v for v in daily_pnls.values() if v <= 0]
    avg_up_day = (sum(up_vals) / len(up_vals)) if up_vals else 0.0
    avg_down_day = (sum(down_vals) / len(down_vals)) if down_vals else 0.0

    pnl_dd_ratio = (total_pnl / max_dd) if max_dd > 0 else float("inf")

    return {
        "total_trades": total_trades,
        "main_trades": len(main_trades),
        "sub_trades": sub_total,
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
        "sub_win_rate": round(sub_win_rate, 1),
        "sub_ev_per_trade": round(sub_ev, 2),
        "sub_cb_fires": sub_cb_fire_count,
        "subband_activations": subband_activation_count,
    }


# -- Config definitions --
CONFIGS = [
    {
        "name": "Baseline",
        "use_subband": False,
        "sub_inner_mult": 0.5,
        "sub_outer_mult": 1.25,
        "target_mode": "1.0x",
        "switchback_rule": "R0",
        "sub_cb_n": 0,
        "cal_pnl": 33043,
        "cal_pnl_dd": 2.13,
    },
    {
        "name": "Best PnL/DD",
        "use_subband": True,
        "sub_inner_mult": 0.5,
        "sub_outer_mult": 1.25,
        "target_mode": "to_opposite_inner",
        "switchback_rule": "R1",
        "sub_cb_n": 3,
        "cal_pnl": 34935,
        "cal_pnl_dd": 3.37,
    },
    {
        "name": "Best PnL",
        "use_subband": True,
        "sub_inner_mult": 0.5,
        "sub_outer_mult": 1.25,
        "target_mode": "to_opposite_inner",
        "switchback_rule": "sub_cb_only",
        "sub_cb_n": 5,
        "cal_pnl": 47975,
        "cal_pnl_dd": 2.99,
    },
    {
        "name": "Runner-up PnL/DD",
        "use_subband": True,
        "sub_inner_mult": 0.5,
        "sub_outer_mult": 1.25,
        "target_mode": "3.0x",
        "switchback_rule": "R5",
        "sub_cb_n": 5,
        "cal_pnl": 35188,
        "cal_pnl_dd": 3.16,
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

    print(f"Computing bands (LB={LOOKBACK}, inner={INNER_MULT}, outer={OUTER_MULT})...")
    bands = compute_bands(bars)

    print(f"Computing EMA (period={EMA_PERIOD})...")
    ema = compute_ema(bars["close"], EMA_PERIOD)

    # Run all configs
    results = []
    for cfg in CONFIGS:
        print(f"  Simulating: {cfg['name']}...")
        res = simulate(
            bars, bands, ema,
            target_mode=cfg["target_mode"],
            switchback_rule=cfg["switchback_rule"],
            sub_inner_mult=cfg["sub_inner_mult"],
            sub_outer_mult=cfg["sub_outer_mult"],
            sub_cb_n=cfg["sub_cb_n"],
            use_subband=cfg["use_subband"],
        )
        res["config_name"] = cfg["name"]
        res["cal_pnl"] = cfg["cal_pnl"]
        res["cal_pnl_dd"] = cfg["cal_pnl_dd"]
        results.append(res)
        print(f"    PnL=${res['total_pnl']:.2f}  PnL/DD={res['pnl_dd_ratio']:.3f}  "
              f"Trades={res['total_trades']} (main={res['main_trades']}, sub={res['sub_trades']})")

    # -- Write CSV --
    csv_path = OUTPUT_DIR / "holdout-subband-cb.csv"
    csv_cols = [
        "config_name",
        "total_trades", "main_trades", "sub_trades",
        "total_pnl", "up_days", "down_days", "win_day_pct",
        "avg_up_day", "avg_down_day", "worst_day",
        "max_drawdown", "pnl_dd_ratio",
        "win_rate", "ev_per_trade",
        "sub_win_rate", "sub_ev_per_trade",
        "subband_activations", "sub_cb_fires",
        "cal_pnl", "cal_pnl_dd",
    ]
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=csv_cols)
        w.writeheader()
        for r in results:
            w.writerow({k: r[k] for k in csv_cols})
    print(f"\nWrote CSV to {csv_path}")

    # -- Write summary --
    summary_path = OUTPUT_DIR / "holdout-subband-cb-summary.txt"
    lines = []
    lines.append("=" * 100)
    lines.append("HOLDOUT VALIDATION: SUB-BAND WITH CIRCUIT BREAKER")
    lines.append("=" * 100)
    lines.append(f"Holdout data: {DATA_PATH.name} ({bars['n']} bars)")
    lines.append(f"Holdout period: 12/15/2025 - 3/13/2026")
    lines.append(f"Config: LB={LOOKBACK}, Inner={INNER_MULT}, Outer={OUTER_MULT}, "
                 f"Main CB N={CIRCUIT_BREAKER_N}, MDL=${MAX_DAILY_LOSS}")
    lines.append(f"EMA period={EMA_PERIOD}, EMA threshold={EMA_THRESHOLD}")
    lines.append(f"Cost: ${COST_PER_TRADE}/trade, Qty=1")
    lines.append(f"RTH: {RTH_START}-{RTH_END}, Entry at bar close, ddof=0")
    lines.append(f"Sub-band trigger: 2 main-band consecutive stops on one side "
                 f"AND EMA within {EMA_THRESHOLD} of inner band on failing side")
    lines.append(f"Directional sub-band only (opposite of failures)")
    lines.append(f"Counter reset on switch-back to main")
    lines.append("")

    for r in results:
        cfg_name = r["config_name"]
        # Find matching config for details
        cfg = next(c for c in CONFIGS if c["name"] == cfg_name)

        lines.append("-" * 100)
        if cfg["use_subband"]:
            lines.append(f"{cfg_name}: si={cfg['sub_inner_mult']}, so={cfg['sub_outer_mult']}, "
                         f"target={cfg['target_mode']}, switch-back={cfg['switchback_rule']}, "
                         f"sub CB={cfg['sub_cb_n']}")
        else:
            lines.append(f"{cfg_name}: No sub-bands")
        lines.append("-" * 100)
        lines.append("")

        lines.append(f"  {'Metric':<28s}  {'Calibration':>14s}  {'Holdout':>14s}")
        lines.append(f"  {'-'*28}  {'-'*14}  {'-'*14}")
        lines.append(f"  {'Total PnL ($)':<28s}  {'$'+str(cfg['cal_pnl']):>14s}  {'${:,.2f}'.format(r['total_pnl']):>14s}")
        lines.append(f"  {'PnL/MaxDD':<28s}  {cfg['cal_pnl_dd']:>14.2f}  {r['pnl_dd_ratio']:>14.3f}")
        lines.append("")
        lines.append(f"  Holdout details:")
        lines.append(f"    Total trades:          {r['total_trades']}")
        lines.append(f"    Main trades:           {r['main_trades']}")
        lines.append(f"    Sub-band trades:       {r['sub_trades']}")
        lines.append(f"    Up days:               {r['up_days']}")
        lines.append(f"    Down days:             {r['down_days']}")
        lines.append(f"    Win day %:             {r['win_day_pct']:.1f}%")
        lines.append(f"    Avg up day:            ${r['avg_up_day']:,.2f}")
        lines.append(f"    Avg down day:          ${r['avg_down_day']:,.2f}")
        lines.append(f"    Worst single day:      ${r['worst_day']:,.2f}")
        lines.append(f"    Max drawdown:          ${r['max_drawdown']:,.2f}")
        lines.append(f"    Net PnL/MaxDD:         {r['pnl_dd_ratio']:.3f}")
        lines.append(f"    Win rate:              {r['win_rate']:.1f}%")
        lines.append(f"    EV per trade:          ${r['ev_per_trade']:,.2f}")
        lines.append(f"    Sub-band win rate:     {r['sub_win_rate']:.1f}%")
        lines.append(f"    Sub-band EV/trade:     ${r['sub_ev_per_trade']:,.2f}")
        lines.append(f"    Sub-band activations:  {r['subband_activations']}")
        lines.append(f"    Sub CB fires:          {r['sub_cb_fires']}")
        lines.append("")

    # Comparison table
    lines.append("=" * 100)
    lines.append("CALIBRATION vs HOLDOUT COMPARISON")
    lines.append("=" * 100)
    lines.append("")
    hdr = f"  {'Config':<20s}  {'Cal PnL':>10s}  {'HO PnL':>12s}  {'Cal PnL/DD':>10s}  {'HO PnL/DD':>10s}  {'HO Trades':>9s}  {'HO WinR%':>8s}  {'HO EV':>8s}"
    lines.append(hdr)
    lines.append(f"  {'-'*20}  {'-'*10}  {'-'*12}  {'-'*10}  {'-'*10}  {'-'*9}  {'-'*8}  {'-'*8}")
    for r in results:
        cfg = next(c for c in CONFIGS if c["name"] == r["config_name"])
        lines.append(f"  {r['config_name']:<20s}  "
                     f"{'$'+str(cfg['cal_pnl']):>10s}  "
                     f"{'${:,.2f}'.format(r['total_pnl']):>12s}  "
                     f"{cfg['cal_pnl_dd']:>10.2f}  "
                     f"{r['pnl_dd_ratio']:>10.3f}  "
                     f"{r['total_trades']:>9d}  "
                     f"{r['win_rate']:>7.1f}%  "
                     f"{'${:,.2f}'.format(r['ev_per_trade']):>8s}")
    lines.append("")

    summary_text = "\n".join(lines)
    with open(summary_path, "w") as f:
        f.write(summary_text)
    print(f"Wrote summary to {summary_path}")
    print()
    print(summary_text)


if __name__ == "__main__":
    main()
