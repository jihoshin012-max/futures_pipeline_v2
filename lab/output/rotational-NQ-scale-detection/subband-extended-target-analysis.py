"""Extended target and switch-back rule grid for directional sub-band trading.

Base config: LB=100, inner=0.75, outer=1.75, CB N=2, MDL $2500, qty=1,
EMA period=50, EMA threshold=0.1, sub-inner=0.5, sub-outer=1.25.
RTH only (09:30-15:45), entry at bar close, ddof=0.

Target extensions (applied to sub-band trades only):
  1.0x  — standard (sub-inner band width)
  1.5x  — 50% larger
  2.0x  — double
  3.0x  — triple
  main_band_width  — target = innerTop - innerBot
  to_opposite_inner — target = distance from sub-inner entry to opposite main inner band

Switch-back rules:
  R0 — Stay in sub-band mode rest of day (current behavior)
  R1 — Switch back to main after 1 sub-band win
  R2 — Switch back to main after 2 sub-band wins
  R3 — Switch back when EMA returns inside inner bands
  R4 — Switch back after 5 sub-band trades
  R5 — Switch back after 10 sub-band trades
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
MAX_DAILY_LOSS = 2500.00
CIRCUIT_BREAKER_N = 2
EMA_PERIOD = 50
EMA_THRESHOLD = 0.1
SUB_INNER_MULT = 0.5
SUB_OUTER_MULT = 1.25

RTH_START = "09:30:00"
RTH_END = "15:45:00"

# Grid dimensions
TARGET_MODES = ["1.0x", "1.5x", "2.0x", "3.0x", "main_band_width", "to_opposite_inner"]
SWITCHBACK_RULES = ["R0", "R1", "R2", "R3", "R4", "R5"]

DATA_PATH = Path(r"C:\Projects\futures_pipeline\data\NQ-250tick-calibration.csv")
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


# ── Simulate ──
def simulate(bars: dict, bands: dict, ema: np.ndarray,
             target_mode: str, switchback_rule: str,
             use_subband: bool):
    """
    Run rangefade rotation sim with N=2 circuit breaker + MDL $2500.

    target_mode: how to compute sub-band target
    switchback_rule: when to switch from sub-band mode back to main
    use_subband: if False, baseline (no sub-band switching)
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

    # Consecutive stop state
    consec_long = 0
    consec_short = 0
    last_date = 0

    # Sub-band directional mode
    subband_mode = False
    subband_allowed_dir = None
    subband_wins_today = 0
    subband_trades_today = 0
    subband_switched_back = False  # prevents re-activation after switch-back

    # Tracking
    main_trades = []
    sub_trades = []
    daily_pnls = {}

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
        """Compute sub-band geometry. Returns sub_inner, sub_outer, half_width."""
        if dir_ == "short":
            inner_band = it[idx]
            outer_band = ot[idx]
        else:
            inner_band = ib[idx]
            outer_band = ob[idx]

        sub_mid = (inner_band + outer_band) / 2.0
        half_width = abs(outer_band - inner_band) / 2.0

        if dir_ == "short":
            sub_inner = sub_mid - SUB_INNER_MULT * half_width
            sub_outer = sub_mid + SUB_OUTER_MULT * half_width
        else:
            sub_inner = sub_mid + SUB_INNER_MULT * half_width
            sub_outer = sub_mid - SUB_OUTER_MULT * half_width

        return sub_inner, sub_outer, half_width

    def compute_subband_target_offset(idx, dir_, half_width):
        """Compute target offset based on target_mode."""
        standard_offset = 2.0 * SUB_INNER_MULT * half_width

        if target_mode == "1.0x":
            return standard_offset
        elif target_mode == "1.5x":
            return standard_offset * 1.5
        elif target_mode == "2.0x":
            return standard_offset * 2.0
        elif target_mode == "3.0x":
            return standard_offset * 3.0
        elif target_mode == "main_band_width":
            return it[idx] - ib[idx]
        elif target_mode == "to_opposite_inner":
            # For a short entry at sub-inner top: target = sub_inner_level - main inner bottom
            # For a long entry at sub-inner bot: target = main inner top - sub_inner_level
            if dir_ == "short":
                sub_inner, _, _ = compute_subband_levels(idx, dir_)
                # Distance from entry (close) to main inner bottom
                return close[idx] - ib[idx]
            else:
                sub_inner, _, _ = compute_subband_levels(idx, dir_)
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
        s_offset = (SUB_OUTER_MULT + SUB_INNER_MULT) * half_width
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
        """Check if switch-back rule triggers (exit sub-band mode)."""
        if switchback_rule == "R0":
            return False  # Stay in sub-band rest of day
        elif switchback_rule == "R1":
            return subband_wins_today >= 1
        elif switchback_rule == "R2":
            return subband_wins_today >= 2
        elif switchback_rule == "R3":
            # EMA returns inside inner bands
            if np.isnan(ema[idx]) or np.isnan(it[idx]):
                return False
            return ib[idx] < ema[idx] < it[idx]
        elif switchback_rule == "R4":
            return subband_trades_today >= 5
        elif switchback_rule == "R5":
            return subband_trades_today >= 10
        return False

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
                subband_mode = False
                subband_allowed_dir = None
                subband_switched_back = True  # prevent re-activation today
                # Fall through to main band logic below

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
                                continue  # re-check this bar in sub-band mode
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

        # ── In position: check resolution ──
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
            # Reversal only for main-band trades
            if direction == "long":
                pnl_pts = close[i] - entry_price
                new_direction = "short"
            else:
                pnl_pts = entry_price - close[i]
                new_direction = "long"

            pnl_dollar = pnl_pts * POINT_VALUE - COST_PER_TRADE
            is_loss = (pnl_dollar < 0)
            # Count winning reversals as sub-band wins if we were tracking
            # (reversals only happen in main mode, so this doesn't apply)
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

    # ── Summary stats ──
    all_trades = main_trades + sub_trades
    total_trades = len(all_trades)
    total_pnl = sum(t["pnl_dollar"] for t in all_trades)
    ev_per_trade = (total_pnl / total_trades) if total_trades > 0 else 0.0

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

    pnl_dd_ratio = (total_pnl / max_dd) if max_dd > 0 else float("inf")

    return {
        "target_mode": target_mode,
        "switchback": switchback_rule,
        "total_trades": total_trades,
        "main_trades": len(main_trades),
        "sub_trades": sub_total,
        "total_pnl": round(total_pnl, 2),
        "up_days": up_days,
        "down_days": down_days,
        "win_day_pct": round(win_day_pct, 1),
        "worst_day": round(worst_day, 2),
        "max_drawdown": round(max_dd, 2),
        "pnl_dd_ratio": round(pnl_dd_ratio, 3),
        "ev_per_trade": round(ev_per_trade, 2),
        "sub_win_rate": round(sub_win_rate, 1),
        "sub_ev_per_trade": round(sub_ev, 2),
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

    print(f"Computing EMA (period={EMA_PERIOD})...")
    ema = compute_ema(bars["close"], EMA_PERIOD)

    # ── Baseline: main bands with N=2 + MDL, no sub-band switching ──
    print("Simulating baseline (N=2 + MDL $2500, no sub-bands)...")
    baseline = simulate(bars, bands, ema, "1.0x", "R0", use_subband=False)
    baseline["target_mode"] = "baseline"
    baseline["switchback"] = "N/A"

    # ── Grid search ──
    results = []
    total_configs = len(TARGET_MODES) * len(SWITCHBACK_RULES)
    print(f"\nRunning {total_configs} configs (6 targets x 6 switch-back rules)...")

    count = 0
    for tm in TARGET_MODES:
        for sb in SWITCHBACK_RULES:
            count += 1
            label = f"[{count}/{total_configs}] target={tm}, switchback={sb}"
            print(f"  {label}...")
            res = simulate(bars, bands, ema, tm, sb, use_subband=True)
            results.append(res)

    # ── Write grid CSV ──
    csv_path = OUTPUT_DIR / "subband-extended-target-grid.csv"
    cols = [
        "target_mode", "switchback",
        "total_trades", "main_trades", "sub_trades",
        "total_pnl", "up_days", "down_days", "win_day_pct",
        "worst_day", "max_drawdown", "pnl_dd_ratio",
        "ev_per_trade",
        "sub_win_rate", "sub_ev_per_trade",
    ]
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        bl_row = {k: baseline[k] for k in cols}
        w.writerow(bl_row)
        for r in results:
            w.writerow({k: r[k] for k in cols})
    print(f"\nWrote grid CSV to {csv_path}")

    # ── Write summary ──
    summary_path = OUTPUT_DIR / "subband-extended-target-summary.txt"
    lines = []
    lines.append("=" * 110)
    lines.append("EXTENDED TARGET + SWITCH-BACK RULE GRID FOR DIRECTIONAL SUB-BAND TRADING")
    lines.append("=" * 110)
    lines.append(f"Config: LB={LOOKBACK}, Inner={INNER_MULT}, Outer={OUTER_MULT}, "
                 f"CB N={CIRCUIT_BREAKER_N}, MDL=${MAX_DAILY_LOSS}")
    lines.append(f"EMA period={EMA_PERIOD}, EMA threshold={EMA_THRESHOLD}")
    lines.append(f"Sub-inner mult={SUB_INNER_MULT}, Sub-outer mult={SUB_OUTER_MULT}")
    lines.append(f"Data: {DATA_PATH.name} ({bars['n']} bars)")
    lines.append(f"Cost: ${COST_PER_TRADE}/trade, Qty=1")
    lines.append(f"RTH: {RTH_START}-{RTH_END}, Entry at bar close, ddof=0")
    lines.append("")
    lines.append("Target modes:")
    lines.append("  1.0x  = standard sub-inner band width")
    lines.append("  1.5x  = 1.5 * sub-inner band width")
    lines.append("  2.0x  = 2.0 * sub-inner band width")
    lines.append("  3.0x  = 3.0 * sub-inner band width")
    lines.append("  main_band_width = innerTop - innerBot")
    lines.append("  to_opposite_inner = distance from entry to opposite main inner band")
    lines.append("")
    lines.append("Switch-back rules:")
    lines.append("  R0 = stay in sub-band rest of day")
    lines.append("  R1 = switch back after 1 sub-band win")
    lines.append("  R2 = switch back after 2 sub-band wins")
    lines.append("  R3 = switch back when EMA inside inner bands")
    lines.append("  R4 = switch back after 5 sub-band trades")
    lines.append("  R5 = switch back after 10 sub-band trades")
    lines.append("")

    lines.append("-" * 110)
    lines.append("BASELINE (N=2 CB + MDL $2500, no sub-band switching)")
    lines.append("-" * 110)
    lines.append(f"  Trades: {baseline['total_trades']} (main: {baseline['main_trades']}, sub: {baseline['sub_trades']})")
    lines.append(f"  Total PnL: ${baseline['total_pnl']:.2f}")
    lines.append(f"  Up/Down days: {baseline['up_days']}/{baseline['down_days']} ({baseline['win_day_pct']:.1f}%)")
    lines.append(f"  Worst day: ${baseline['worst_day']:.2f}")
    lines.append(f"  Max drawdown: ${baseline['max_drawdown']:.2f}")
    lines.append(f"  PnL/MaxDD: {baseline['pnl_dd_ratio']:.3f}")
    lines.append(f"  EV/trade: ${baseline['ev_per_trade']:.2f}")
    lines.append("")

    lines.append("-" * 110)
    lines.append("FULL GRID RESULTS")
    lines.append("-" * 110)
    hdr = (f"{'Target':>18s}  {'SB':>3s}  {'Trades':>6s}  {'Main':>5s}  {'Sub':>4s}  "
           f"{'PnL($)':>10s}  {'Up':>3s}  {'Dn':>3s}  {'WD%':>5s}  {'Worst':>9s}  "
           f"{'MaxDD':>9s}  {'PnL/DD':>7s}  {'EV/T':>7s}  "
           f"{'sWR%':>5s}  {'sEV':>7s}")
    lines.append(hdr)
    lines.append("-" * len(hdr))

    for r in results:
        line = (f"{r['target_mode']:>18s}  {r['switchback']:>3s}  "
                f"{r['total_trades']:>6d}  {r['main_trades']:>5d}  {r['sub_trades']:>4d}  "
                f"{r['total_pnl']:>10.2f}  {r['up_days']:>3d}  {r['down_days']:>3d}  "
                f"{r['win_day_pct']:>5.1f}  {r['worst_day']:>9.2f}  "
                f"{r['max_drawdown']:>9.2f}  {r['pnl_dd_ratio']:>7.3f}  "
                f"{r['ev_per_trade']:>7.2f}  "
                f"{r['sub_win_rate']:>5.1f}  {r['sub_ev_per_trade']:>7.2f}")
        lines.append(line)

    # ── Comparison to baseline ──
    lines.append("")
    lines.append("-" * 110)
    lines.append("COMPARISON TO BASELINE")
    lines.append("-" * 110)

    beats_baseline = []
    for r in results:
        dpnl = r["total_pnl"] - baseline["total_pnl"]
        if r["total_pnl"] > baseline["total_pnl"]:
            beats_baseline.append((r, dpnl))

    if beats_baseline:
        beats_baseline.sort(key=lambda x: x[1], reverse=True)
        lines.append(f"\nConfigs that beat baseline PnL: {len(beats_baseline)}/{len(results)}")
        for r, dpnl in beats_baseline:
            lines.append(f"  {r['target_mode']:>18s} / {r['switchback']}: "
                         f"PnL=${r['total_pnl']:.2f} (+${dpnl:.2f}), "
                         f"PnL/DD={r['pnl_dd_ratio']:.3f}, "
                         f"sub={r['sub_trades']}, sWR={r['sub_win_rate']:.1f}%, "
                         f"sEV=${r['sub_ev_per_trade']:.2f}")
    else:
        lines.append("\nNo configs beat baseline PnL.")

    # ── Patterns by target size ──
    lines.append("")
    lines.append("-" * 110)
    lines.append("PATTERNS BY TARGET SIZE")
    lines.append("-" * 110)
    for tm in TARGET_MODES:
        subset = [r for r in results if r["target_mode"] == tm]
        pnls = [r["total_pnl"] for r in subset]
        avg_pnl = np.mean(pnls)
        best = max(subset, key=lambda r: r["total_pnl"])
        worst = min(subset, key=lambda r: r["total_pnl"])
        sub_evs = [r["sub_ev_per_trade"] for r in subset if r["sub_trades"] > 0]
        avg_sub_ev = np.mean(sub_evs) if sub_evs else 0.0
        sub_wrs = [r["sub_win_rate"] for r in subset if r["sub_trades"] > 0]
        avg_sub_wr = np.mean(sub_wrs) if sub_wrs else 0.0
        lines.append(f"  {tm:>18s}: avg PnL=${avg_pnl:.2f}, "
                     f"best=${best['total_pnl']:.2f} ({best['switchback']}), "
                     f"worst=${worst['total_pnl']:.2f} ({worst['switchback']}), "
                     f"avg sub WR={avg_sub_wr:.1f}%, avg sub EV=${avg_sub_ev:.2f}")

    # ── Patterns by switch-back rule ──
    lines.append("")
    lines.append("-" * 110)
    lines.append("PATTERNS BY SWITCH-BACK RULE")
    lines.append("-" * 110)
    for sb in SWITCHBACK_RULES:
        subset = [r for r in results if r["switchback"] == sb]
        pnls = [r["total_pnl"] for r in subset]
        avg_pnl = np.mean(pnls)
        avg_dd = np.mean([r["max_drawdown"] for r in subset])
        avg_ratio = np.mean([r["pnl_dd_ratio"] for r in subset])
        avg_sub = np.mean([r["sub_trades"] for r in subset])
        best = max(subset, key=lambda r: r["total_pnl"])
        lines.append(f"  {sb}: avg PnL=${avg_pnl:.2f}, avg MaxDD=${avg_dd:.2f}, "
                     f"avg PnL/DD={avg_ratio:.3f}, avg sub trades={avg_sub:.1f}, "
                     f"best=${best['total_pnl']:.2f} ({best['target_mode']})")

    # ── Best overall ──
    lines.append("")
    lines.append("-" * 110)
    lines.append("BEST CONFIGS")
    lines.append("-" * 110)

    best_pnl = max(results, key=lambda r: r["total_pnl"])
    lines.append(f"Best total PnL: {best_pnl['target_mode']} / {best_pnl['switchback']} "
                 f"(${best_pnl['total_pnl']:.2f} vs baseline ${baseline['total_pnl']:.2f})")

    best_ratio = max(results, key=lambda r: r["pnl_dd_ratio"])
    lines.append(f"Best PnL/DD ratio: {best_ratio['target_mode']} / {best_ratio['switchback']} "
                 f"({best_ratio['pnl_dd_ratio']:.3f} vs baseline {baseline['pnl_dd_ratio']:.3f})")

    best_ev = max(results, key=lambda r: r["ev_per_trade"])
    lines.append(f"Best EV/trade: {best_ev['target_mode']} / {best_ev['switchback']} "
                 f"(${best_ev['ev_per_trade']:.2f} vs baseline ${baseline['ev_per_trade']:.2f})")

    # Best sub-band EV (among configs with meaningful sub-band trades)
    sub_configs = [r for r in results if r["sub_trades"] >= 5]
    if sub_configs:
        best_sub = max(sub_configs, key=lambda r: r["sub_ev_per_trade"])
        lines.append(f"Best sub-band EV (>=5 trades): {best_sub['target_mode']} / {best_sub['switchback']} "
                     f"(${best_sub['sub_ev_per_trade']:.2f}, {best_sub['sub_trades']} sub trades, "
                     f"WR={best_sub['sub_win_rate']:.1f}%)")

    lines.append("")

    with open(summary_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Wrote summary to {summary_path}")

    print("\n" + "\n".join(lines))


if __name__ == "__main__":
    main()
