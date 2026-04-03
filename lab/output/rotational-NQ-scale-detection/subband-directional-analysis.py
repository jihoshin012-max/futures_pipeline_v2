"""Sub-band directional trading after circuit breaker + EMA confirmation.

After N=2 consecutive stops on one side AND EMA confirms trend direction,
switch to directional-only sub-band trades for rest of day.

Base logic: LB=100, inner=0.75, outer=1.75, N=2 circuit breaker,
$2500 max daily loss. Sub-band geometry carved from inner-to-outer space.

Grid: EMA threshold × sub-inner mult × sub-outer mult.
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

RTH_START = "09:30:00"
RTH_END = "15:45:00"

# Grid parameters
EMA_THRESHOLDS = [0.0, 0.1, 0.25]
SUB_INNER_MULTS = [0.5, 0.75, 1.0]
SUB_OUTER_MULTS = [1.0, 1.25, 1.5]

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
    # Seed with SMA
    ema[period - 1] = np.mean(closes[:period])
    k = 2.0 / (period + 1)
    for i in range(period, n):
        ema[i] = closes[i] * k + ema[i - 1] * (1 - k)
    return ema


def is_rth(time_hms: str) -> bool:
    return RTH_START <= time_hms <= RTH_END


# ── Simulate ──
def simulate(bars: dict, bands: dict, ema: np.ndarray,
             ema_threshold: float, sub_inner_mult: float, sub_outer_mult: float,
             use_subband: bool):
    """
    Run rangefade rotation sim with N=2 circuit breaker + MDL $2500.
    If use_subband=True, after CB fires AND EMA confirms, switch to
    directional sub-band trading for rest of day.
    If use_subband=False, baseline: just main bands with N=2 + MDL.
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
    direction = None  # "long" or "short"
    entry_price = 0.0
    target_price = 0.0
    stop_price = 0.0
    entry_target_offset = 0.0
    entry_stop_offset = 0.0
    is_subband_trade = False  # whether current trade is a sub-band trade

    # Consecutive stop state
    consec_long = 0
    consec_short = 0
    last_date = 0

    # Sub-band directional mode
    subband_mode = False  # True when in directional sub-band mode
    subband_allowed_dir = None  # "long" or "short" — the ONLY allowed direction

    # Tracking
    main_trades = []
    sub_trades = []
    daily_pnls = {}
    cb_fired_count = 0
    subband_activations = 0

    equity = 0.0
    peak_equity = 0.0
    max_dd = 0.0
    daily_loss = 0.0  # tracks current day loss for MDL

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
        """Compute sub-band entry/stop/target for directional trade.
        dir_ is the allowed direction (opposite of the failing side).
        """
        if dir_ == "short":
            # Trading short from the top sub-band
            inner_band = it[idx]
            outer_band = ot[idx]
        else:
            # Trading long from the bottom sub-band
            inner_band = ib[idx]
            outer_band = ob[idx]

        sub_mid = (inner_band + outer_band) / 2.0
        half_width = abs(outer_band - inner_band) / 2.0

        if dir_ == "short":
            sub_inner = sub_mid - sub_inner_mult * half_width  # entry level (lower)
            sub_outer = sub_mid + sub_outer_mult * half_width  # stop level (higher)
            # Entry signal: high >= sub_inner AND high < sub_outer
            # Entry at close
            # Target: sub_inner band width down from entry
            # Stop: sub_outer - sub_inner distance up from entry
            sub_inner_width = sub_outer - sub_inner  # this is the band width for target calc
            # Actually, per spec: target = sub-inner band width
            # sub-inner band width = distance from sub_mid to sub_inner? No.
            # Re-reading spec: "Target = sub-inner band width (same logic as main bands)"
            # Main bands: target offset = inner_top - inner_bot = inner band width
            # Sub-band analogy: target offset = sub_inner_width = sub_outer - sub_inner? No.
            # The sub-inner is one level, sub-outer is another.
            # "sub-inner band width" = the width of the sub-inner band region
            # Looking at main band: target = it - ib (the inner band width)
            # For sub-bands: the inner band occupies sub_inner_mult * half_width on each side of subMid
            # So sub-inner band width = 2 * sub_inner_mult * half_width
            # But that seems too large. Let me re-read the spec more carefully.
            #
            # Actually the spec says:
            # "Target = sub-inner band width (same logic as main bands)"
            # In main bands: target = inner_top - inner_bot = 2 * INNER_MULT * std
            # The sub-band inner width = 2 * sub_inner_mult * half_width
            # But we only trade one side, so target = sub_inner_mult * half_width * 2?
            # No, for main bands the target is the FULL inner band width (top - bot).
            # For sub-bands, the "inner" band is from subMid - subInnerMult*hw to subMid + subInnerMult*hw.
            # Width = 2 * subInnerMult * hw.
            # BUT we're only on one side. The spec says:
            # "Target = sub-inner band width down" for shorts
            # So target offset = 2 * subInnerMult * halfWidth? That would be the full sub-inner width.
            # But that's the same logic as main: target = full inner width regardless of side.
            #
            # Actually wait, let me re-read more carefully:
            # "Sub-inner (entry) = subMid - subInnerMult * halfWidth (for shorts from top)"
            # So sub_inner for shorts = subMid - subInnerMult * halfWidth (this is the entry LEVEL)
            # "Sub-outer (stop) = subMid + subOuterMult * halfWidth (for shorts)"
            # So sub_outer for shorts = subMid + subOuterMult * halfWidth (stop LEVEL)
            #
            # "Target = sub-inner band width down"
            # sub-inner band width = the width of the sub-inner zone
            # The sub-inner zone on the short side: from (subMid - subInnerMult*hw) to subMid
            # That width = subInnerMult * halfWidth
            # OR the full sub-inner band width = 2 * subInnerMult * halfWidth
            #
            # Looking at main bands: entry at inner, target = inner band width = it - ib = 2*INNER*std
            # You enter at one inner band and target the OTHER inner band (full width).
            # For sub-bands there's no "other side" — we only trade one direction.
            # So "sub-inner band width" likely means the sub-inner width on this side only:
            # = subInnerMult * halfWidth
            #
            # Actually, let me re-read the spec one more time:
            # "Target = sub-inner band width (same logic as main bands)"
            # Main bands: target offset = it[i] - ib[i] = 2 * INNER_MULT * std
            # Sub-band analogy: sub-inner band width = the distance between sub-inner levels
            # If sub-inner on top side = subMid + subInnerMult*hw
            # and sub-inner on bot side = subMid - subInnerMult*hw
            # then sub-inner band width = 2 * subInnerMult * hw
            # This is the "same logic" as main bands where target = full inner width.
            #
            # I'll use target_offset = 2 * subInnerMult * halfWidth (full sub-inner width)
            # and stop_offset = distance from entry to stop = (sub_outer - sub_inner) for shorts
            # Wait no. Let me think about stop differently.
            # "Stop = sub-outer - sub-inner distance up" for shorts
            # stop_offset = sub_outer - sub_inner = (subOuterMult + subInnerMult) * halfWidth
            # Hmm, that's the distance from sub_inner to sub_outer.
            # For main bands: stop_offset = ib - ob = INNER*std - (-OUTER*std) = (INNER+OUTER)*std
            # Wait no: stop_offset for long = ib[i] - ob[i] = (mean - INNER*std) - (mean - OUTER*std) = (OUTER-INNER)*std
            # So stop_offset = (OUTER_MULT - INNER_MULT) * std = distance from inner to outer on that side.
            # Sub-band analogy: stop_offset = sub_outer - sub_inner (distance from entry level to stop level)
            # For shorts: sub_inner = subMid - subInnerMult*hw, sub_outer = subMid + subOuterMult*hw
            # stop_offset = sub_outer - sub_inner = (subOuterMult + subInnerMult) * hw
            #
            # And target: main bands target = it - ib = 2*INNER*std (you go from one inner to the other)
            # Sub-band target: hmm, you can't go to the other sub-band. Let me just use the spec literally:
            # "Target = sub-inner band width" = 2 * subInnerMult * halfWidth
            #
            # This is consistent with the spec statement "same logic as main bands" where target = full inner width.

            target_offset = 2.0 * sub_inner_mult * half_width
            stop_offset = (sub_outer_mult + sub_inner_mult) * half_width
        else:
            sub_inner = sub_mid + sub_inner_mult * half_width  # entry level (higher, towards mean)
            sub_outer = sub_mid - sub_outer_mult * half_width  # stop level (lower)

            target_offset = 2.0 * sub_inner_mult * half_width
            stop_offset = (sub_outer_mult + sub_inner_mult) * half_width

        return sub_inner, sub_outer, target_offset, stop_offset

    def check_subband_entry(idx, allowed_dir):
        """Check if sub-band entry signal fires on this bar."""
        if np.isnan(it[idx]):
            return False
        sub_inner, sub_outer, _, _ = compute_subband_levels(idx, allowed_dir)
        if allowed_dir == "short":
            # Entry when high >= sub_inner AND high < sub_outer
            return high[idx] >= sub_inner and high[idx] < sub_outer
        else:
            # Entry when low <= sub_inner AND low > sub_outer
            return low[idx] <= sub_inner and low[idx] > sub_outer

    def enter_subband(idx, allowed_dir):
        """Compute entry price, target, stop for sub-band directional trade."""
        sub_inner, sub_outer, t_offset, s_offset = compute_subband_levels(idx, allowed_dir)
        ep = close[idx]
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
        daily_loss += pnl_dollar  # pnl_dollar is negative for losses
        if equity > peak_equity:
            peak_equity = equity
        dd = peak_equity - equity
        if dd > max_dd:
            max_dd = dd
        if exit_bar_date_int not in daily_pnls:
            daily_pnls[exit_bar_date_int] = 0.0
        daily_pnls[exit_bar_date_int] += pnl_dollar

    def mdl_breached():
        """Check if max daily loss has been hit."""
        return daily_loss <= -MAX_DAILY_LOSS

    def check_ema_confirms(idx, failing_side):
        """Check if EMA confirms trend after circuit breaker.
        failing_side: "long" means 2 long failures (downtrend expected)
                      "short" means 2 short failures (uptrend expected)
        """
        if np.isnan(ema[idx]) or np.isnan(it[idx]):
            return False
        band_width = it[idx] - ib[idx]  # inner band width
        if failing_side == "long":
            # 2 long failures -> downtrend -> EMA should be near/below inner bot
            return ema[idx] <= ib[idx] + ema_threshold * band_width
        else:
            # 2 short failures -> uptrend -> EMA should be near/above inner top
            return ema[idx] >= it[idx] - ema_threshold * band_width

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
            daily_loss = 0.0
            if in_position:
                # Force close at day boundary (shouldn't happen with RTH filter, but safety)
                in_position = False
                direction = None
                is_subband_trade = False

        # MDL check — no new trades if breached
        if mdl_breached() and not in_position:
            i += 1
            continue

        if not in_position:
            if not is_rth(time_str[i]):
                i += 1
                continue

            if subband_mode and use_subband:
                # ── Sub-band directional mode: only allowed direction ──
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
                # ── Normal main-band entry ──
                buy_sig = check_buy_signal(i)
                sell_sig = check_sell_signal(i)

                dir_to_enter = None
                if buy_sig and sell_sig:
                    i += 1
                    continue
                elif buy_sig:
                    long_blocked = consec_long >= CIRCUIT_BREAKER_N
                    if long_blocked:
                        # Check if we should activate sub-band mode
                        if use_subband and not subband_mode:
                            if check_ema_confirms(i, "long"):
                                subband_mode = True
                                subband_allowed_dir = "short"  # opposite of failing side
                                subband_activations += 1
                                cb_fired_count += 1
                                # Don't enter main band, try sub-band on this bar
                                continue  # re-check this bar in sub-band mode
                            else:
                                cb_fired_count += 1
                        i += 1
                        continue
                    dir_to_enter = "long"
                elif sell_sig:
                    short_blocked = consec_short >= CIRCUIT_BREAKER_N
                    if short_blocked:
                        if use_subband and not subband_mode:
                            if check_ema_confirms(i, "short"):
                                subband_mode = True
                                subband_allowed_dir = "long"
                                subband_activations += 1
                                cb_fired_count += 1
                                continue
                            else:
                                cb_fired_count += 1
                        i += 1
                        continue
                    dir_to_enter = "short"
                else:
                    i += 1
                    continue

                # Check forced opposite reset (from base logic)
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
                reversal_signal = False  # No reversals in sub-band mode
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
            is_loss = (exit_type == "STOP")
            trade_rec = {
                "date": date_int[i], "dir": direction, "entry": entry_price,
                "pnl_dollar": round(pnl_dollar, 2), "exit_type": exit_type,
                "is_subband": is_subband_trade,
            }
            if is_subband_trade:
                sub_trades.append(trade_rec)
            else:
                main_trades.append(trade_rec)
            record_pnl(pnl_dollar, date_int[i])

            if not is_subband_trade:
                # Update consec counters for main trades only
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
            continue  # same-bar re-entry

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
            trade_rec = {
                "date": date_int[i], "dir": direction, "entry": entry_price,
                "pnl_dollar": round(pnl_dollar, 2), "exit_type": "REVERSAL",
                "is_subband": False,
            }
            main_trades.append(trade_rec)
            record_pnl(pnl_dollar, date_int[i])

            # Update consec
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

            # Check if new direction blocked
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

            # Forced opposite reset
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
    winning = sum(1 for t in all_trades if t["pnl_dollar"] > 0)
    win_rate = (winning / total_trades * 100) if total_trades > 0 else 0.0
    ev_per_trade = (total_pnl / total_trades) if total_trades > 0 else 0.0

    sub_winning = sum(1 for t in sub_trades if t["pnl_dollar"] > 0)
    sub_total = len(sub_trades)
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
        "ema_threshold": ema_threshold,
        "sub_inner_mult": sub_inner_mult,
        "sub_outer_mult": sub_outer_mult,
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
        "win_rate": round(win_rate, 1),
        "ev_per_trade": round(ev_per_trade, 2),
        "sub_win_rate": round(sub_win_rate, 1),
        "sub_ev_per_trade": round(sub_ev, 2),
        "subband_activations": subband_activations,
        "cb_fired": cb_fired_count,
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
    baseline = simulate(bars, bands, ema, 0.0, 0.5, 1.0, use_subband=False)
    baseline["ema_threshold"] = "N/A"
    baseline["sub_inner_mult"] = "N/A"
    baseline["sub_outer_mult"] = "N/A"

    # ── Grid search ──
    results = []
    configs = []
    for ema_thresh in EMA_THRESHOLDS:
        for si in SUB_INNER_MULTS:
            for so in SUB_OUTER_MULTS:
                if so <= si:
                    continue  # sub_outer must be > sub_inner
                configs.append((ema_thresh, si, so))

    print(f"\nRunning {len(configs)} sub-band configs...")
    for ema_thresh, si, so in configs:
        label = f"EMA_th={ema_thresh}, si={si}, so={so}"
        print(f"  {label}...")
        res = simulate(bars, bands, ema, ema_thresh, si, so, use_subband=True)
        results.append(res)

    # ── Write grid CSV ──
    csv_path = OUTPUT_DIR / "subband-directional-grid.csv"
    cols = [
        "ema_threshold", "sub_inner_mult", "sub_outer_mult",
        "total_trades", "main_trades", "sub_trades",
        "total_pnl", "up_days", "down_days", "win_day_pct",
        "worst_day", "max_drawdown", "pnl_dd_ratio",
        "win_rate", "ev_per_trade",
        "sub_win_rate", "sub_ev_per_trade",
        "subband_activations", "cb_fired",
    ]
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        # Baseline first
        bl_row = {k: baseline[k] for k in cols}
        w.writerow(bl_row)
        for r in results:
            w.writerow({k: r[k] for k in cols})
    print(f"\nWrote grid CSV to {csv_path}")

    # ── Write summary ──
    summary_path = OUTPUT_DIR / "subband-directional-summary.txt"
    lines = []
    lines.append("=" * 100)
    lines.append("SUB-BAND DIRECTIONAL TRADING AFTER CIRCUIT BREAKER + EMA CONFIRMATION")
    lines.append("=" * 100)
    lines.append(f"Config: LB={LOOKBACK}, Inner={INNER_MULT}, Outer={OUTER_MULT}, "
                 f"CB N={CIRCUIT_BREAKER_N}, MDL=${MAX_DAILY_LOSS}")
    lines.append(f"EMA period: {EMA_PERIOD}")
    lines.append(f"Data: {DATA_PATH.name} ({bars['n']} bars)")
    lines.append(f"Cost: ${COST_PER_TRADE}/trade, Qty=1")
    lines.append(f"RTH: {RTH_START}-{RTH_END}, Entry at bar close, ddof=0")
    lines.append("")
    lines.append("Concept:")
    lines.append("  - Trade normally on main bands until circuit breaker fires (N=2 consec stops)")
    lines.append("  - If EMA confirms trend (within threshold of inner band on failing side),")
    lines.append("    switch to directional sub-band trades for rest of day")
    lines.append("  - Sub-bands carved from inner-to-outer space on the relevant side")
    lines.append("  - Only trade OPPOSITE direction of failures (fade the trend)")
    lines.append("  - No reversals in sub-band mode; daily reset returns to main bands")
    lines.append("")

    lines.append("-" * 100)
    lines.append("BASELINE (N=2 CB + MDL $2500, no sub-band switching)")
    lines.append("-" * 100)
    lines.append(f"  Trades: {baseline['total_trades']} (main: {baseline['main_trades']}, sub: {baseline['sub_trades']})")
    lines.append(f"  Total PnL: ${baseline['total_pnl']:.2f}")
    lines.append(f"  Up/Down days: {baseline['up_days']}/{baseline['down_days']} ({baseline['win_day_pct']:.1f}%)")
    lines.append(f"  Worst day: ${baseline['worst_day']:.2f}")
    lines.append(f"  Max drawdown: ${baseline['max_drawdown']:.2f}")
    lines.append(f"  PnL/MaxDD: {baseline['pnl_dd_ratio']:.3f}")
    lines.append(f"  Win rate: {baseline['win_rate']:.1f}%")
    lines.append(f"  EV/trade: ${baseline['ev_per_trade']:.2f}")
    lines.append(f"  CB fired: {baseline['cb_fired']}")
    lines.append("")

    lines.append("-" * 100)
    lines.append("GRID RESULTS")
    lines.append("-" * 100)
    hdr = (f"{'EMA_th':>6s}  {'si':>5s}  {'so':>5s}  {'Trades':>6s}  {'Main':>5s}  {'Sub':>4s}  "
           f"{'PnL($)':>10s}  {'Up':>3s}  {'Dn':>3s}  {'WD%':>5s}  {'Worst':>9s}  "
           f"{'MaxDD':>9s}  {'PnL/DD':>7s}  {'WR%':>5s}  {'EV/T':>7s}  "
           f"{'sWR%':>5s}  {'sEV':>7s}  {'sAct':>4s}")
    lines.append(hdr)
    lines.append("-" * len(hdr))

    for r in results:
        line = (f"{r['ema_threshold']:>6.2f}  {r['sub_inner_mult']:>5.2f}  {r['sub_outer_mult']:>5.2f}  "
                f"{r['total_trades']:>6d}  {r['main_trades']:>5d}  {r['sub_trades']:>4d}  "
                f"{r['total_pnl']:>10.2f}  {r['up_days']:>3d}  {r['down_days']:>3d}  "
                f"{r['win_day_pct']:>5.1f}  {r['worst_day']:>9.2f}  "
                f"{r['max_drawdown']:>9.2f}  {r['pnl_dd_ratio']:>7.3f}  "
                f"{r['win_rate']:>5.1f}  {r['ev_per_trade']:>7.2f}  "
                f"{r['sub_win_rate']:>5.1f}  {r['sub_ev_per_trade']:>7.2f}  "
                f"{r['subband_activations']:>4d}")
        lines.append(line)

    lines.append("")
    lines.append("-" * 100)
    lines.append("COMPARISON TO BASELINE")
    lines.append("-" * 100)

    beats_baseline = []
    for r in results:
        dpnl = r["total_pnl"] - baseline["total_pnl"]
        ddd = r["max_drawdown"] - baseline["max_drawdown"]
        dratio = r["pnl_dd_ratio"] - baseline["pnl_dd_ratio"]
        label = f"EMA_th={r['ema_threshold']:.2f}, si={r['sub_inner_mult']:.2f}, so={r['sub_outer_mult']:.2f}"
        lines.append(f"  {label}: dPnL={dpnl:+.2f}, dMaxDD={ddd:+.2f}, dPnL/DD={dratio:+.3f}, "
                     f"sub_trades={r['sub_trades']}, sub_activations={r['subband_activations']}")
        if r["total_pnl"] > baseline["total_pnl"]:
            beats_baseline.append((r, dpnl))

    lines.append("")
    if beats_baseline:
        beats_baseline.sort(key=lambda x: x[1], reverse=True)
        lines.append(f"Configs that beat baseline PnL: {len(beats_baseline)}/{len(results)}")
        for r, dpnl in beats_baseline:
            lines.append(f"  EMA_th={r['ema_threshold']:.2f}, si={r['sub_inner_mult']:.2f}, "
                         f"so={r['sub_outer_mult']:.2f}: "
                         f"PnL=${r['total_pnl']:.2f} (+${dpnl:.2f}), "
                         f"PnL/DD={r['pnl_dd_ratio']:.3f}, "
                         f"sub_trades={r['sub_trades']}, sub_WR={r['sub_win_rate']:.1f}%, "
                         f"sub_EV=${r['sub_ev_per_trade']:.2f}")
    else:
        lines.append("No configs beat baseline PnL.")

    # Best by PnL/DD ratio
    best_ratio = max(results, key=lambda r: r["pnl_dd_ratio"])
    lines.append("")
    lines.append(f"Best PnL/DD ratio: EMA_th={best_ratio['ema_threshold']:.2f}, "
                 f"si={best_ratio['sub_inner_mult']:.2f}, so={best_ratio['sub_outer_mult']:.2f} "
                 f"({best_ratio['pnl_dd_ratio']:.3f} vs baseline {baseline['pnl_dd_ratio']:.3f})")

    best_pnl = max(results, key=lambda r: r["total_pnl"])
    lines.append(f"Best total PnL: EMA_th={best_pnl['ema_threshold']:.2f}, "
                 f"si={best_pnl['sub_inner_mult']:.2f}, so={best_pnl['sub_outer_mult']:.2f} "
                 f"(${best_pnl['total_pnl']:.2f} vs baseline ${baseline['total_pnl']:.2f})")

    lines.append("")

    with open(summary_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Wrote summary to {summary_path}")

    print("\n" + "\n".join(lines))


if __name__ == "__main__":
    main()
