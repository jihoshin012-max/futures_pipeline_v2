"""Holdout validation of final rangefade rotation candidate configs.

Runs four configs on NQ-250tick-holdout.csv (12/15/2025 - 3/13/2026):
  1. N=2 (final candidate)
  2. N=0 (no circuit breaker baseline)
  3. N=3 (alternative threshold)
  4. N=0 simple baseline (same params, explicit comparison)

Config: LB=100, inner=0.75, outer=1.75, qty=1, no martingale, no step-up
RTH: 09:30-15:45, entry at bar close, ddof=0
NQ tick_size=0.25, tick_value=$5.00, cost=$4/trade

Simulation logic matches consec-stop-analysis.py exactly:
  - Target/stop resolution every bar while in position
  - Priority: target > stop > reversal
  - Same-bar target+stop: close determines winner
  - Same-bar re-entry after target/stop resolution
  - Reversal: exit at close, enter opposite at close
  - Circuit breaker: block side at N consecutive stops,
    reset BOTH counters when opposite side fires
  - Per-side daily reset of consecutive counters
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

CONFIGS = [
    {"label": "N=2 (candidate)", "threshold": 2},
    {"label": "N=0 (no CB)", "threshold": 0},
    {"label": "N=3", "threshold": 3},
]

DATA_PATH = Path(r"C:\Projects\futures_pipeline\data\NQ-250tick-holdout.csv")
OUTPUT_DIR = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")

# Calibration reference values for comparison
CALIBRATION_REF = {
    2: {"pnl": 33228, "maxdd": 17681, "pnl_dd": 1.88, "ev": 11.21},
    0: {"pnl": 23083, "maxdd": 23021, "pnl_dd": 1.00, "ev": 5.21},
    3: {"pnl": 29907, "maxdd": 22818, "pnl_dd": 1.31, "ev": 8.22},
}


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
    return RTH_START <= time_hms <= RTH_END


# ── Simulate ──
def simulate(bars: dict, bands: dict, max_consec: int):
    """Run rangefade rotation sim with given consecutive-stop threshold.
    Returns (summary_dict, trades_list).
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

    trades = []
    daily_pnls = {}
    cb_fired_count = 0
    trades_blocked = 0
    forced_opposite = 0

    equity = 0.0
    peak_equity = 0.0
    max_dd = 0.0

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

            short_blocked = (max_consec > 0 and consec_short >= max_consec)
            long_blocked = (max_consec > 0 and consec_long >= max_consec)
            if dir_to_enter == "long" and short_blocked:
                consec_long = 0
                consec_short = 0
                forced_opposite += 1
            elif dir_to_enter == "short" and long_blocked:
                consec_long = 0
                consec_short = 0
                forced_opposite += 1

            entry_price, target_price, stop_price, entry_target_offset, entry_stop_offset = compute_entry(i, dir_to_enter)
            direction = dir_to_enter
            in_position = True
            entry_bar = i
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
            trades.append({
                "date": date_int[i], "dir": direction, "entry": entry_price,
                "exit_price": entry_price + pnl_pts if direction == "long" else entry_price - pnl_pts,
                "pnl_dollar": round(pnl_dollar, 2), "exit_type": exit_type,
                "bar_idx": i, "std_at_entry": sd[entry_bar],
            })
            record_pnl(pnl_dollar, date_int[i])
            update_consec(direction, is_loss)
            in_position = False
            direction = None
            continue

        elif target_hit:
            pnl_pts = entry_target_offset
            pnl_dollar = pnl_pts * POINT_VALUE - COST_PER_TRADE
            trades.append({
                "date": date_int[i], "dir": direction, "entry": entry_price,
                "exit_price": target_price, "pnl_dollar": round(pnl_dollar, 2),
                "exit_type": "TARGET", "bar_idx": i, "std_at_entry": sd[entry_bar],
            })
            record_pnl(pnl_dollar, date_int[i])
            update_consec(direction, False)
            in_position = False
            direction = None
            continue

        elif stop_hit:
            pnl_pts = -entry_stop_offset
            pnl_dollar = pnl_pts * POINT_VALUE - COST_PER_TRADE
            trades.append({
                "date": date_int[i], "dir": direction, "entry": entry_price,
                "exit_price": stop_price, "pnl_dollar": round(pnl_dollar, 2),
                "exit_type": "STOP", "bar_idx": i, "std_at_entry": sd[entry_bar],
            })
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
            trades.append({
                "date": date_int[i], "dir": direction, "entry": entry_price,
                "exit_price": close[i], "pnl_dollar": round(pnl_dollar, 2),
                "exit_type": "REVERSAL", "bar_idx": i, "std_at_entry": sd[entry_bar],
            })
            record_pnl(pnl_dollar, date_int[i])
            update_consec(direction, is_loss)

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

            if new_direction == "long" and short_blocked:
                consec_long = 0
                consec_short = 0
                forced_opposite += 1
            elif new_direction == "short" and long_blocked:
                consec_long = 0
                consec_short = 0
                forced_opposite += 1

            entry_price, target_price, stop_price, entry_target_offset, entry_stop_offset = compute_entry(i, new_direction)
            direction = new_direction
            in_position = True
            entry_bar = i
            i += 1
            continue

        i += 1

    # ── Compute summary stats ──
    total_trades = len(trades)
    total_pnl = sum(t["pnl_dollar"] for t in trades)
    winning_trades = sum(1 for t in trades if t["pnl_dollar"] > 0)
    win_rate = (winning_trades / total_trades * 100) if total_trades > 0 else 0.0
    ev_per_trade = (total_pnl / total_trades) if total_trades > 0 else 0.0

    # Largest single trade loss
    largest_loss = min((t["pnl_dollar"] for t in trades), default=0.0)

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
    best_day = max(daily_pnls.values()) if daily_pnls else 0.0

    trades_per_day = (total_trades / total_days) if total_days > 0 else 0.0

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
        "best_day": round(best_day, 2),
        "max_drawdown": round(max_dd, 2),
        "pnl_dd_ratio": round(pnl_dd_ratio, 3),
        "win_rate": round(win_rate, 1),
        "ev_per_trade": round(ev_per_trade, 2),
        "largest_loss": round(largest_loss, 2),
        "trades_per_day": round(trades_per_day, 2),
        "cb_fired": cb_fired_count,
        "trades_blocked": trades_blocked,
        "forced_opposite": forced_opposite,
    }, trades


def compute_regime_breakdown(trades: list, bars: dict, bands: dict):
    """Split trades into stdDev terciles and compute per-regime stats."""
    # Collect all valid std values for tercile boundaries
    valid_stds = bands["std"][~np.isnan(bands["std"])]
    if len(valid_stds) == 0:
        return []

    t1 = np.percentile(valid_stds, 33.33)
    t2 = np.percentile(valid_stds, 66.67)

    regime_trades = {"low": [], "mid": [], "high": []}
    for t in trades:
        s = t.get("std_at_entry", np.nan)
        if np.isnan(s):
            continue
        if s <= t1:
            regime_trades["low"].append(t)
        elif s <= t2:
            regime_trades["mid"].append(t)
        else:
            regime_trades["high"].append(t)

    results = []
    for regime in ["low", "mid", "high"]:
        tlist = regime_trades[regime]
        n = len(tlist)
        if n == 0:
            results.append({"regime": regime, "trades": 0, "win_rate": 0.0, "ev": 0.0})
            continue
        wins = sum(1 for t in tlist if t["pnl_dollar"] > 0)
        total_pnl = sum(t["pnl_dollar"] for t in tlist)
        results.append({
            "regime": regime,
            "trades": n,
            "win_rate": round(wins / n * 100, 1),
            "ev": round(total_pnl / n, 2),
        })

    return results


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

    # ── Run configs ──
    all_results = []
    all_trades = {}
    all_regimes = {}

    for cfg in CONFIGS:
        threshold = cfg["threshold"]
        label = cfg["label"]
        print(f"  Simulating {label}...")
        res, trades = simulate(bars, bands, threshold)
        res["label"] = label
        all_results.append(res)
        all_trades[threshold] = trades
        all_regimes[threshold] = compute_regime_breakdown(trades, bars, bands)

    # ── Write holdout-final.csv ──
    csv_path = OUTPUT_DIR / "holdout-final.csv"
    cols = [
        "label", "threshold", "total_trades", "total_pnl", "up_days", "down_days",
        "win_day_pct", "avg_up_day", "avg_down_day", "worst_day", "best_day",
        "max_drawdown", "pnl_dd_ratio", "win_rate", "ev_per_trade",
        "largest_loss", "trades_per_day",
        "cb_fired", "trades_blocked", "forced_opposite",
    ]
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in all_results:
            w.writerow({k: r[k] for k in cols})
    print(f"Wrote {csv_path}")

    # ── Write holdout-final-by-regime.csv ──
    regime_csv_path = OUTPUT_DIR / "holdout-final-by-regime.csv"
    regime_cols = ["threshold", "label", "regime", "trades", "win_rate", "ev"]
    with open(regime_csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=regime_cols)
        w.writeheader()
        for cfg in CONFIGS:
            threshold = cfg["threshold"]
            label = cfg["label"]
            for rr in all_regimes[threshold]:
                w.writerow({
                    "threshold": threshold,
                    "label": label,
                    "regime": rr["regime"],
                    "trades": rr["trades"],
                    "win_rate": rr["win_rate"],
                    "ev": rr["ev"],
                })
    print(f"Wrote {regime_csv_path}")

    # ── Write holdout-final-summary.txt ──
    summary_path = OUTPUT_DIR / "holdout-final-summary.txt"
    lines = []

    lines.append("=" * 95)
    lines.append("HOLDOUT VALIDATION -- FINAL CANDIDATE CONFIGS")
    lines.append("=" * 95)
    lines.append(f"Config: LB={LOOKBACK}, Inner={INNER_MULT}, Outer={OUTER_MULT}")
    lines.append(f"Holdout data: {DATA_PATH.name} ({bars['n']} bars)")
    lines.append(f"Holdout period: 12/15/2025 - 3/13/2026")
    lines.append(f"Cost: ${COST_PER_TRADE}/trade, Qty=1, No martingale, No step-up")
    lines.append(f"RTH: 09:30-15:45, Entry at bar close, ddof=0")
    lines.append("")

    # Main results table
    hdr = (f"{'Config':>20s}  {'Trades':>6s}  {'PnL($)':>10s}  {'Up':>3s}  {'Dn':>3s}  "
           f"{'WinD%':>5s}  {'AvgUp':>8s}  {'AvgDn':>8s}  {'Worst':>9s}  {'Best':>9s}  "
           f"{'MaxDD':>9s}  {'PnL/DD':>7s}  {'WR%':>5s}  {'EV/T':>7s}  "
           f"{'MaxLoss':>8s}  {'T/Day':>5s}  "
           f"{'CBFire':>6s}  {'Block':>5s}")
    lines.append(hdr)
    lines.append("-" * len(hdr))

    for r in all_results:
        line = (f"{r['label']:>20s}  {r['total_trades']:>6d}  {r['total_pnl']:>10.2f}  "
                f"{r['up_days']:>3d}  {r['down_days']:>3d}  {r['win_day_pct']:>5.1f}  "
                f"{r['avg_up_day']:>8.2f}  {r['avg_down_day']:>8.2f}  {r['worst_day']:>9.2f}  "
                f"{r['best_day']:>9.2f}  "
                f"{r['max_drawdown']:>9.2f}  {r['pnl_dd_ratio']:>7.3f}  "
                f"{r['win_rate']:>5.1f}  {r['ev_per_trade']:>7.2f}  "
                f"{r['largest_loss']:>8.2f}  {r['trades_per_day']:>5.2f}  "
                f"{r['cb_fired']:>6d}  {r['trades_blocked']:>5d}")
        lines.append(line)

    # Calibration vs holdout comparison
    lines.append("")
    lines.append("=" * 95)
    lines.append("CALIBRATION vs HOLDOUT COMPARISON")
    lines.append("=" * 95)
    lines.append("")
    lines.append(f"{'Config':>20s}  {'':>10s}  {'PnL($)':>10s}  {'MaxDD($)':>10s}  {'PnL/DD':>7s}  {'EV/T($)':>8s}")
    lines.append("-" * 75)

    for r in all_results:
        t = r["threshold"]
        if t in CALIBRATION_REF:
            cal = CALIBRATION_REF[t]
            lines.append(f"{r['label']:>20s}  {'Calibr':>10s}  {cal['pnl']:>10.0f}  "
                         f"{cal['maxdd']:>10.0f}  {cal['pnl_dd']:>7.2f}  {cal['ev']:>8.2f}")
            lines.append(f"{'':>20s}  {'Holdout':>10s}  {r['total_pnl']:>10.2f}  "
                         f"{r['max_drawdown']:>10.2f}  {r['pnl_dd_ratio']:>7.3f}  {r['ev_per_trade']:>8.2f}")

            # Compute deltas
            dpnl = r['total_pnl'] - cal['pnl']
            ddd = r['max_drawdown'] - cal['maxdd']
            dratio = r['pnl_dd_ratio'] - cal['pnl_dd']
            dev = r['ev_per_trade'] - cal['ev']
            lines.append(f"{'':>20s}  {'Delta':>10s}  {dpnl:>+10.2f}  "
                         f"{ddd:>+10.2f}  {dratio:>+7.3f}  {dev:>+8.2f}")
            lines.append("")

    # Regime breakdown
    lines.append("=" * 95)
    lines.append("REGIME BREAKDOWN (stdDev terciles)")
    lines.append("=" * 95)
    lines.append("")
    lines.append(f"{'Config':>20s}  {'Regime':>6s}  {'Trades':>6s}  {'WR%':>5s}  {'EV($)':>8s}")
    lines.append("-" * 55)

    for cfg in CONFIGS:
        threshold = cfg["threshold"]
        label = cfg["label"]
        for rr in all_regimes[threshold]:
            lines.append(f"{label:>20s}  {rr['regime']:>6s}  {rr['trades']:>6d}  "
                         f"{rr['win_rate']:>5.1f}  {rr['ev']:>8.2f}")
        lines.append("")

    with open(summary_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Wrote {summary_path}")

    # Print to stdout
    print("\n" + "\n".join(lines))


if __name__ == "__main__":
    main()
