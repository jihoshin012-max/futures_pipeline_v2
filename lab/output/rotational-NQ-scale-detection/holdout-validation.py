"""
Holdout validation for top range-fade rotation configs.

Uses identical simulation logic to multiplier-grid-analysis.py (v4)
and martingale-analysis.py:
- ddof=0 population std
- Same-bar re-entry
- Reversal logic (target > stop > reversal priority)
- RTH filter 09:30-15:45 for new entries only
- Entry at bar close

Holdout period: 12/15/2025 - 3/13/2026 (NQ 250-tick)
"""

import pandas as pd
import numpy as np
import os
import math

# --- Config ---
LOOKBACK = 50
TICK_SIZE = 0.25
TICK_VALUE = 5.00
POINT_VALUE = TICK_VALUE / TICK_SIZE  # $20 per point

RTH_START = "09:30:00"
RTH_END = "15:45:00"

DATA_PATH = r"c:\Projects\futures_pipeline\data\NQ-250tick-holdout.csv"
OUTPUT_DIR = r"c:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection"

# --- Baseline combos (Part A) ---
BASELINE_COMBOS = [
    (1.50, 2.00),  # best EV on calibration
    (0.50, 2.00),  # all-regime positive
    (1.50, 3.00),  # 3rd best
    (0.75, 2.50),  # strong in high vol
    (0.50, 1.75),  # all-regime positive
    (1.25, 2.00),
    (1.00, 2.00),  # current v3 settings
]

# Calibration reference values for summary comparison
CAL_REF = {
    (1.50, 2.00): {"ev": 4.73, "pf": 1.04},
    (0.50, 2.00): {"ev": 4.30, "pf": 1.04},
    (1.50, 3.00): {"ev": 3.56, "pf": 1.02},
    (0.75, 2.50): {"ev": 3.47, "pf": 1.02},
    (0.50, 1.75): {"ev": 3.46, "pf": 1.03},
    (1.25, 2.00): {"ev": 3.34, "pf": 1.02},
    (1.00, 2.00): {"ev": 1.74, "pf": 1.01},
}

# --- Martingale combos (Part B) ---
MART_COMBOS = [
    # (inner, outer, mart_cfg_list)
    (1.50, 2.00, [None, (1.5, 3)]),
    (0.50, 2.00, [None]),                 # mart hurt this one
    (0.50, 1.75, [None, (2.0, 4)]),
]

# --- Load data ---
df = pd.read_csv(DATA_PATH)
df.columns = df.columns.str.strip()

high = df["High"].values.astype(float)
low = df["Low"].values.astype(float)
close = df["Last"].values.astype(float)
dates = df["Date"].values
times_raw = df["Time"].values

def parse_time_hms(t):
    s = str(t).strip()
    return s[:8]

bar_times = np.array([parse_time_hms(t) for t in times_raw])

def is_rth(bar_idx):
    t = bar_times[bar_idx]
    return t >= RTH_START and t <= RTH_END

n = len(df)

# --- Precompute rolling mean/std (ddof=0) ---
roll_mean = np.full(n, np.nan)
roll_std = np.full(n, np.nan)

for i in range(LOOKBACK - 1, n):
    window = close[i - LOOKBACK + 1 : i + 1]
    roll_mean[i] = np.mean(window)
    roll_std[i] = np.std(window, ddof=0)

print(f"Loaded {n} bars, rolling stats computed from bar {LOOKBACK-1} onward.")

# --- RTH bar count ---
rth_count = sum(1 for i in range(n) if is_rth(i))
print(f"RTH bars (09:30-15:45): {rth_count} / {n} total ({rth_count/n*100:.1f}%)")

# --- StdDev terciles from HOLDOUT data ---
valid_std = roll_std[~np.isnan(roll_std)]
tercile_low = np.percentile(valid_std, 33.33)
tercile_high = np.percentile(valid_std, 66.67)

def get_regime(sd):
    if sd <= tercile_low:
        return "low"
    elif sd <= tercile_high:
        return "medium"
    else:
        return "high"

print(f"StdDev terciles (holdout): low <= {tercile_low:.2f}, medium <= {tercile_high:.2f}, high > {tercile_high:.2f}")

# --- Count unique dates ---
unique_dates = len(set(dates[~pd.isna(dates)]))
print(f"Unique trading dates: {unique_dates}")


# =============================================================================
# Simulation engine (shared for baseline and martingale)
# =============================================================================

def simulate(inner_mult, outer_mult, mart_cfg=None):
    """
    Run simulation with optional martingale position sizing.

    mart_cfg: None for baseline (qty=1), or (mult, max_contracts).

    Returns list of trade dicts.
    """
    use_martingale = mart_cfg is not None
    if use_martingale:
        mart_mult, mart_max = mart_cfg

    # Compute bands
    inner_top = roll_mean + inner_mult * roll_std
    inner_bot = roll_mean - inner_mult * roll_std
    outer_top = roll_mean + outer_mult * roll_std
    outer_bot = roll_mean - outer_mult * roll_std

    trades = []

    # Martingale state — per side
    consec_long = 0
    consec_short = 0

    # Position state
    in_position = False
    direction = None
    entry_price = 0.0
    target_price = 0.0
    stop_price = 0.0
    entry_bar = 0
    entry_target_offset = 0.0
    entry_stop_offset = 0.0
    entry_regime = ""
    position_qty = 1

    def get_qty(dir_):
        if not use_martingale:
            return 1
        consec = consec_long if dir_ == "long" else consec_short
        qty = int(math.floor(1 * mart_mult ** consec))
        qty = max(1, min(qty, mart_max))
        return qty

    def check_buy_signal(bar_idx):
        if np.isnan(roll_mean[bar_idx]):
            return False
        return low[bar_idx] <= inner_bot[bar_idx] and low[bar_idx] > outer_bot[bar_idx]

    def check_sell_signal(bar_idx):
        if np.isnan(roll_mean[bar_idx]):
            return False
        return high[bar_idx] >= inner_top[bar_idx] and high[bar_idx] < outer_top[bar_idx]

    def enter_position(bar_idx, dir_):
        ep = close[bar_idx]
        t_offset = inner_top[bar_idx] - inner_bot[bar_idx]
        if dir_ == "long":
            s_offset = inner_bot[bar_idx] - outer_bot[bar_idx]
            tp = ep + t_offset
            sp = ep - s_offset
        else:
            s_offset = outer_top[bar_idx] - inner_top[bar_idx]
            tp = ep - t_offset
            sp = ep + s_offset
        regime = get_regime(roll_std[bar_idx])
        return ep, tp, sp, t_offset, s_offset, regime, bar_idx

    def record_trade(pnl_pts, exit_type, dir_, qty, t_off, s_off, regime, entry_b, exit_b):
        nonlocal consec_long, consec_short

        pnl_dollar = pnl_pts * POINT_VALUE * qty
        is_win = pnl_pts > 0

        # Update martingale counters
        if dir_ == "long":
            if is_win:
                consec_long = 0
            else:
                consec_long += 1
        else:
            if is_win:
                consec_short = 0
            else:
                consec_short += 1

        trades.append({
            "direction": dir_,
            "qty": qty,
            "pnl_pts": pnl_pts,
            "pnl_dollar": pnl_dollar,
            "exit_type": exit_type,
            "entry_bar": entry_b,
            "exit_bar": exit_b,
            "is_win": is_win,
            "regime": regime,
            "target_offset": t_off,
            "stop_offset": s_off,
        })

    i = LOOKBACK
    while i < n:
        if not in_position:
            if np.isnan(roll_mean[i]):
                i += 1
                continue
            if not is_rth(i):
                i += 1
                continue

            buy_sig = check_buy_signal(i)
            sell_sig = check_sell_signal(i)

            if buy_sig:
                direction = "long"
            elif sell_sig:
                direction = "short"
            else:
                i += 1
                continue

            position_qty = get_qty(direction)
            (entry_price, target_price, stop_price,
             entry_target_offset, entry_stop_offset,
             entry_regime, entry_bar) = enter_position(i, direction)
            in_position = True
            i += 1
            continue

        # In position — check resolution
        if i >= n:
            break

        if direction == "long":
            target_hit = high[i] >= target_price
            stop_hit = low[i] <= stop_price
            buy_reversal = False
            sell_reversal = check_sell_signal(i) and is_rth(i)
        else:
            target_hit = low[i] <= target_price
            stop_hit = high[i] >= stop_price
            buy_reversal = check_buy_signal(i) and is_rth(i)
            sell_reversal = False

        reversal_signal = buy_reversal or sell_reversal

        if target_hit and stop_hit:
            if direction == "long":
                if close[i] >= entry_price:
                    pnl = entry_target_offset
                    exit_type = "target"
                else:
                    pnl = -entry_stop_offset
                    exit_type = "stop"
            else:
                if close[i] <= entry_price:
                    pnl = entry_target_offset
                    exit_type = "target"
                else:
                    pnl = -entry_stop_offset
                    exit_type = "stop"

            record_trade(pnl, exit_type, direction, position_qty,
                         entry_target_offset, entry_stop_offset,
                         entry_regime, entry_bar, i)
            in_position = False
            direction = None
            continue

        elif target_hit:
            pnl = entry_target_offset
            record_trade(pnl, "target", direction, position_qty,
                         entry_target_offset, entry_stop_offset,
                         entry_regime, entry_bar, i)
            in_position = False
            direction = None
            continue

        elif stop_hit:
            pnl = -entry_stop_offset
            record_trade(pnl, "stop", direction, position_qty,
                         entry_target_offset, entry_stop_offset,
                         entry_regime, entry_bar, i)
            in_position = False
            direction = None
            continue

        elif reversal_signal:
            if direction == "long":
                pnl = close[i] - entry_price
                new_direction = "short"
            else:
                pnl = entry_price - close[i]
                new_direction = "long"

            record_trade(pnl, "reversal", direction, position_qty,
                         entry_target_offset, entry_stop_offset,
                         entry_regime, entry_bar, i)

            # Enter opposite direction
            position_qty = get_qty(new_direction)
            (entry_price, target_price, stop_price,
             entry_target_offset, entry_stop_offset,
             entry_regime, entry_bar) = enter_position(i, new_direction)
            direction = new_direction
            in_position = True
            i += 1
            continue

        i += 1

    return trades


# =============================================================================
# PART A: Baseline analysis
# =============================================================================
print("\n" + "=" * 80)
print("PART A: BASELINE HOLDOUT VALIDATION")
print("=" * 80)

baseline_results = []
regime_results = []

for inner_mult, outer_mult in BASELINE_COMBOS:
    trades = simulate(inner_mult, outer_mult, mart_cfg=None)

    if len(trades) == 0:
        print(f"  i={inner_mult:.2f} o={outer_mult:.2f}: NO TRADES")
        continue

    pnls_pts = np.array([t["pnl_pts"] for t in trades])
    pnls_dollar = np.array([t["pnl_dollar"] for t in trades])
    regimes = [t["regime"] for t in trades]
    exit_types = [t["exit_type"] for t in trades]
    target_offsets = np.array([t["target_offset"] for t in trades])
    stop_offsets = np.array([t["stop_offset"] for t in trades])

    total = len(trades)
    wins = pnls_pts > 0
    losses = pnls_pts < 0
    win_count = int(np.sum(wins))
    loss_count = int(np.sum(losses))
    win_rate = win_count / total * 100

    avg_win_pts = np.mean(pnls_pts[wins]) if win_count > 0 else 0
    avg_loss_pts = np.mean(pnls_pts[losses]) if loss_count > 0 else 0

    ev_pts = np.mean(pnls_pts)
    ev_dollar = ev_pts * POINT_VALUE

    gross_wins = np.sum(pnls_pts[wins]) * POINT_VALUE if win_count > 0 else 0
    gross_losses = abs(np.sum(pnls_pts[losses]) * POINT_VALUE) if loss_count > 0 else 0
    profit_factor = gross_wins / gross_losses if gross_losses > 0 else float("inf")

    # Max consecutive losses
    max_consec_loss = 0
    current_streak = 0
    for p in pnls_pts:
        if p < 0:
            current_streak += 1
            max_consec_loss = max(max_consec_loss, current_streak)
        else:
            current_streak = 0

    trades_per_day = total / unique_dates if unique_dates > 0 else 0

    avg_target_dist = np.mean(target_offsets)
    avg_stop_dist = np.mean(stop_offsets)
    rr_ratio = avg_target_dist / avg_stop_dist if avg_stop_dist > 0 else float("inf")

    n_target = sum(1 for e in exit_types if e == "target")
    n_stop = sum(1 for e in exit_types if e == "stop")
    n_reversal = sum(1 for e in exit_types if e == "reversal")

    baseline_results.append({
        "inner_mult": inner_mult,
        "outer_mult": outer_mult,
        "total_trades": total,
        "win_count": win_count,
        "loss_count": loss_count,
        "win_rate_pct": round(win_rate, 2),
        "avg_win_dollar": round(avg_win_pts * POINT_VALUE, 2),
        "avg_loss_dollar": round(avg_loss_pts * POINT_VALUE, 2),
        "ev_per_trade_dollar": round(ev_dollar, 2),
        "profit_factor": round(profit_factor, 4),
        "max_consecutive_losses": max_consec_loss,
        "trades_per_day": round(trades_per_day, 2),
        "avg_target_dist_pts": round(avg_target_dist, 2),
        "avg_stop_dist_pts": round(avg_stop_dist, 2),
        "reward_risk_ratio": round(rr_ratio, 4),
        "target_exits": n_target,
        "stop_exits": n_stop,
        "reversal_exits": n_reversal,
    })

    print(f"  i={inner_mult:.2f} o={outer_mult:.2f}: {total} trades, "
          f"EV=${ev_dollar:.2f}, WR={win_rate:.1f}%, PF={profit_factor:.2f}")

    # --- Regime breakdown ---
    for regime_name in ["low", "medium", "high"]:
        regime_mask = np.array([r == regime_name for r in regimes])
        regime_pnls = pnls_pts[regime_mask]
        regime_count = len(regime_pnls)
        if regime_count == 0:
            regime_results.append({
                "inner_mult": inner_mult,
                "outer_mult": outer_mult,
                "regime": regime_name,
                "trade_count": 0,
                "win_rate_pct": 0,
                "ev_per_trade_dollar": 0,
                "profit_factor": 0,
            })
            continue

        regime_wins = np.sum(regime_pnls > 0)
        regime_losses = np.sum(regime_pnls < 0)
        regime_wr = regime_wins / regime_count * 100
        regime_ev = np.mean(regime_pnls) * POINT_VALUE

        regime_gw = np.sum(regime_pnls[regime_pnls > 0]) * POINT_VALUE if regime_wins > 0 else 0
        regime_gl = abs(np.sum(regime_pnls[regime_pnls < 0]) * POINT_VALUE) if regime_losses > 0 else 0
        regime_pf = regime_gw / regime_gl if regime_gl > 0 else float("inf")

        regime_results.append({
            "inner_mult": inner_mult,
            "outer_mult": outer_mult,
            "regime": regime_name,
            "trade_count": regime_count,
            "win_rate_pct": round(regime_wr, 2),
            "ev_per_trade_dollar": round(regime_ev, 2),
            "profit_factor": round(regime_pf, 4),
        })


# =============================================================================
# PART B: Martingale overlay
# =============================================================================
print("\n" + "=" * 80)
print("PART B: MARTINGALE HOLDOUT VALIDATION")
print("=" * 80)

def mart_label(cfg):
    if cfg is None:
        return "no_martingale"
    return f"m{cfg[0]}_max{cfg[1]}"

mart_results = []

for inner_mult, outer_mult, mart_cfgs in MART_COMBOS:
    for mart_cfg in mart_cfgs:
        label = mart_label(mart_cfg)
        trades = simulate(inner_mult, outer_mult, mart_cfg=mart_cfg)

        if len(trades) == 0:
            print(f"  i={inner_mult:.2f} o={outer_mult:.2f} {label}: NO TRADES")
            continue

        total_trades = len(trades)
        pnl_dollars = np.array([t["pnl_dollar"] for t in trades])
        wins_mask = np.array([t["is_win"] for t in trades])

        total_pnl = float(np.sum(pnl_dollars))

        win_count = int(np.sum(wins_mask))
        loss_count = total_trades - win_count

        # Max drawdown
        cum_pnl = np.cumsum(pnl_dollars)
        running_max = np.maximum.accumulate(cum_pnl)
        drawdowns = running_max - cum_pnl
        max_dd = float(np.max(drawdowns)) if len(drawdowns) > 0 else 0

        # Max consecutive losses
        max_consec = 0
        current_streak = 0
        for w in wins_mask:
            if not w:
                current_streak += 1
                max_consec = max(max_consec, current_streak)
            else:
                current_streak = 0

        # Profit factor
        gross_wins = float(np.sum(pnl_dollars[wins_mask])) if win_count > 0 else 0
        gross_losses = float(abs(np.sum(pnl_dollars[~wins_mask]))) if loss_count > 0 else 0
        profit_factor = gross_wins / gross_losses if gross_losses > 0 else float("inf")

        # Largest single loss
        largest_loss = float(np.min(pnl_dollars)) if len(pnl_dollars) > 0 else 0

        # PnL/MaxDD ratio
        pnl_maxdd_ratio = total_pnl / max_dd if max_dd > 0 else float("inf")

        mart_results.append({
            "inner_mult": inner_mult,
            "outer_mult": outer_mult,
            "martingale": label,
            "total_trades": total_trades,
            "total_pnl": round(total_pnl, 2),
            "max_drawdown": round(max_dd, 2),
            "pnl_maxdd_ratio": round(pnl_maxdd_ratio, 4),
            "profit_factor": round(profit_factor, 4),
            "largest_loss": round(largest_loss, 2),
            "max_consec_losses": max_consec,
        })

        print(f"  i={inner_mult:.2f} o={outer_mult:.2f} {label}: "
              f"PnL=${total_pnl:+.2f}, MaxDD=${max_dd:.2f}, PF={profit_factor:.2f}")


# =============================================================================
# Write CSVs
# =============================================================================
baseline_df = pd.DataFrame(baseline_results)
baseline_df.to_csv(os.path.join(OUTPUT_DIR, "holdout-baseline.csv"), index=False)
print("\nWrote holdout-baseline.csv")

regime_df = pd.DataFrame(regime_results)
regime_df.to_csv(os.path.join(OUTPUT_DIR, "holdout-by-regime.csv"), index=False)
print("Wrote holdout-by-regime.csv")

mart_df = pd.DataFrame(mart_results)
mart_df.to_csv(os.path.join(OUTPUT_DIR, "holdout-martingale.csv"), index=False)
print("Wrote holdout-martingale.csv")


# =============================================================================
# Write summary
# =============================================================================
lines = []
lines.append("HOLDOUT VALIDATION — RANGE-FADE ROTATION (NQ 250-tick)")
lines.append("=" * 100)
lines.append(f"Holdout period: 12/15/2025 - 3/13/2026")
lines.append(f"Data: {n} bars, {unique_dates} trading days")
lines.append(f"RTH bars (09:30-15:45): {rth_count} / {n} ({rth_count/n*100:.1f}%)")
lines.append(f"Lookback: {LOOKBACK}, ddof=0")
lines.append(f"StdDev terciles (holdout): low <= {tercile_low:.2f}, medium <= {tercile_high:.2f}, high > {tercile_high:.2f}")
lines.append("")

# --- Part A: Calibration vs Holdout comparison ---
lines.append("PART A: BASELINE — CALIBRATION vs HOLDOUT COMPARISON")
lines.append("-" * 100)
lines.append(f"  {'inner':>5s}  {'outer':>5s}  |  {'Cal EV':>8s}  {'Cal PF':>6s}  |  "
             f"{'HO EV':>8s}  {'HO PF':>6s}  {'HO WR%':>6s}  {'Trades':>6s}  "
             f"{'T/day':>5s}  {'R:R':>5s}  {'MaxCL':>5s}  |  {'EV delta':>8s}")

for row in baseline_results:
    im, om = row["inner_mult"], row["outer_mult"]
    cal = CAL_REF.get((im, om), {"ev": 0, "pf": 0})
    ev_delta = row["ev_per_trade_dollar"] - cal["ev"]
    lines.append(
        f"  {im:>5.2f}  {om:>5.2f}  |  "
        f"${cal['ev']:>7.2f}  {cal['pf']:>6.2f}  |  "
        f"${row['ev_per_trade_dollar']:>7.2f}  {row['profit_factor']:>6.2f}  "
        f"{row['win_rate_pct']:>5.1f}%  {row['total_trades']:>6d}  "
        f"{row['trades_per_day']:>5.1f}  {row['reward_risk_ratio']:>5.2f}  "
        f"{row['max_consecutive_losses']:>5d}  |  "
        f"${ev_delta:>+7.2f}"
    )

lines.append("")

# Exit type breakdown
lines.append("EXIT TYPE BREAKDOWN")
lines.append("-" * 100)
lines.append(f"  {'inner':>5s}  {'outer':>5s}  {'Target':>6s}  {'Stop':>6s}  {'Reversal':>8s}")
for row in baseline_results:
    lines.append(
        f"  {row['inner_mult']:>5.2f}  {row['outer_mult']:>5.2f}  "
        f"{row['target_exits']:>6d}  {row['stop_exits']:>6d}  {row['reversal_exits']:>8d}"
    )

lines.append("")

# Regime breakdown
lines.append("REGIME BREAKDOWN (holdout terciles)")
lines.append("-" * 100)
for row in baseline_results:
    im, om = row["inner_mult"], row["outer_mult"]
    lines.append(f"  inner={im:.2f}  outer={om:.2f}:")
    sub = [r for r in regime_results if r["inner_mult"] == im and r["outer_mult"] == om]
    for rr in sub:
        lines.append(
            f"    {rr['regime']:>6s}: count={rr['trade_count']:>5d}  "
            f"WR={rr['win_rate_pct']:>5.1f}%  EV=${rr['ev_per_trade_dollar']:>8.2f}  "
            f"PF={rr['profit_factor']:>6.2f}"
        )

lines.append("")

# --- Part B: Martingale overlay ---
lines.append("PART B: MARTINGALE OVERLAY — HOLDOUT")
lines.append("-" * 100)
lines.append(f"  {'inner':>5s}  {'outer':>5s}  {'Config':<16s}  {'TotalPnL':>10s}  "
             f"{'MaxDD':>10s}  {'PnL/DD':>8s}  {'PF':>7s}  {'BigLoss':>9s}  {'MaxCL':>5s}")

for mr in mart_results:
    lines.append(
        f"  {mr['inner_mult']:>5.2f}  {mr['outer_mult']:>5.2f}  "
        f"{mr['martingale']:<16s}  ${mr['total_pnl']:>9.2f}  "
        f"${mr['max_drawdown']:>9.2f}  {mr['pnl_maxdd_ratio']:>8.4f}  "
        f"{mr['profit_factor']:>7.2f}  ${mr['largest_loss']:>8.2f}  "
        f"{mr['max_consec_losses']:>5d}"
    )

lines.append("")

summary_text = "\n".join(lines)
with open(os.path.join(OUTPUT_DIR, "holdout-summary.txt"), "w") as f:
    f.write(summary_text)
print("Wrote holdout-summary.txt")

print("\n" + summary_text)
print("\nDone.")
