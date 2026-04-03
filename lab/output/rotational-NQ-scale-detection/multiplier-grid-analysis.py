"""
Range-fade rotation strategy: multiplier grid search.

Fixed lookback = 50.
Inner multiplier grid: [0.5, 0.75, 1.0, 1.25, 1.5]
Outer multiplier grid: [1.25, 1.5, 1.75, 2.0, 2.5, 3.0]
Only combinations where outer > inner.

Entry price = bar Close (Last), not the inner band value.
Target offset = inner band width (innerTop - innerBot), applied from entry price.
Stop offset = inner-to-outer distance, applied from entry price.
Ambiguous bars: close on winning side = target hit, losing side = stop hit.

CORRECTIONS APPLIED (v2):
1. StdDev uses ddof=0 (population std) to match C++ sqrtf(sumSq / period).
2. Same-bar re-entry: after a trade resolves at bar j, bar j is checked
   for a new entry signal before advancing. This models the C++ study's
   reversal behavior where a stop/target hit and a new entry can occur
   on the same bar.

CORRECTION (v3): Reversal logic added to match C++ v3 study.
While in a position, each bar is checked for:
  1. Target hit (attached order)
  2. Stop hit (attached order)
  3. Reversal signal (opposite-side entry signal fires while in position)

Priority for same-bar conflicts:
  - Target AND reversal on same bar: target takes priority
  - Stop AND reversal on same bar: stop takes priority
  - Target AND stop on same bar: use close to determine (existing logic)

Reversal exits use bar close for PnL (not target/stop price).
After reversal exit, immediately enter opposite direction at close
with fresh target/stop offsets from current bar's bands.
Reversal signal must satisfy same entry conditions as fresh entry
(touch inner, stay inside outer).

CORRECTION (v4): RTH time filter added to match C++ v3 study settings.
- New entries only allowed when bar time >= 09:30:00 AND <= 15:45:00 ET
- Positions opened during RTH can still be resolved (target/stop) after 15:45
- Reversal entries are also blocked outside RTH — if a reversal signal fires
  outside RTH, it is ignored (position continues with existing target/stop)
- The time filter only blocks NEW entries, not exits
"""

import pandas as pd
import numpy as np
import os
from itertools import product

# --- Config ---
LOOKBACK = 50
INNER_MULTS = [0.5, 0.75, 1.0, 1.25, 1.5]
OUTER_MULTS = [1.25, 1.5, 1.75, 2.0, 2.5, 3.0]
TICK_SIZE = 0.25
TICK_VALUE = 5.00
POINT_VALUE = TICK_VALUE / TICK_SIZE  # $20 per point

RTH_START = "09:30:00"
RTH_END = "15:45:00"

DATA_PATH = r"c:\Projects\futures_pipeline\data\NQ-250tick-calibration.csv"
OUTPUT_DIR = r"c:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection"

# --- Load data ---
df = pd.read_csv(DATA_PATH)
df.columns = df.columns.str.strip()

high = df["High"].values.astype(float)
low = df["Low"].values.astype(float)
close = df["Last"].values.astype(float)
dates = df["Date"].values
times_raw = df["Time"].values

# --- Parse bar times for RTH filter ---
# Time format: "18:00:00.000000" — extract HH:MM:SS for comparison
def parse_time_hms(t):
    """Extract HH:MM:SS from time string like ' 18:00:00.000000'."""
    s = str(t).strip()
    # Take first 8 chars (HH:MM:SS), ignoring microseconds
    return s[:8]

bar_times = np.array([parse_time_hms(t) for t in times_raw])

def is_rth(bar_idx):
    """Check if bar time is within RTH window (09:30:00 - 15:45:00 inclusive)."""
    t = bar_times[bar_idx]
    return t >= RTH_START and t <= RTH_END

n = len(df)

# --- Precompute rolling mean/std for lookback=50 ---
# CORRECTION 1: ddof=0 (population std) to match C++ sqrtf(sumSq / period)
roll_mean = np.full(n, np.nan)
roll_std = np.full(n, np.nan)

for i in range(LOOKBACK - 1, n):
    window = close[i - LOOKBACK + 1 : i + 1]
    roll_mean[i] = np.mean(window)
    roll_std[i] = np.std(window, ddof=0)

print(f"Loaded {n} bars, rolling stats computed from bar {LOOKBACK-1} onward.")

# --- Count RTH bars for sanity check ---
rth_count = sum(1 for i in range(n) if is_rth(i))
print(f"RTH bars (09:30-15:45): {rth_count} / {n} total ({rth_count/n*100:.1f}%)")

# --- Compute stdDev terciles for regime bucketing ---
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

print(f"StdDev terciles: low <= {tercile_low:.2f}, medium <= {tercile_high:.2f}, high > {tercile_high:.2f}")

# --- Count unique dates for trades-per-day ---
unique_dates = len(set(dates[~pd.isna(dates)]))
print(f"Unique trading dates: {unique_dates}")


def simulate(inner_mult, outer_mult, detail_date=None):
    """
    Run simulation for one multiplier combination.

    If detail_date is not None (e.g. "10/10/2025"), returns (trades, detail_trades)
    where detail_trades is a list of dicts for trades entering on that date.
    Otherwise returns (trades, None).
    """
    # Compute bands
    inner_top = roll_mean + inner_mult * roll_std
    inner_bot = roll_mean - inner_mult * roll_std
    outer_top = roll_mean + outer_mult * roll_std
    outer_bot = roll_mean - outer_mult * roll_std

    # trades: list of (pnl_points, regime, target_offset, stop_offset, exit_type)
    trades = []
    detail_trades = [] if detail_date else None

    # Position state
    in_position = False
    direction = None
    entry_price = 0.0
    target_price = 0.0
    stop_price = 0.0
    entry_bar = 0
    entry_regime = ""
    entry_target_offset = 0.0
    entry_stop_offset = 0.0

    def check_buy_signal(bar_idx):
        """Check if bar has a buy signal (touch innerBot, stay above outerBot)."""
        if np.isnan(roll_mean[bar_idx]):
            return False
        return low[bar_idx] <= inner_bot[bar_idx] and low[bar_idx] > outer_bot[bar_idx]

    def check_sell_signal(bar_idx):
        """Check if bar has a sell signal (touch innerTop, stay below outerTop)."""
        if np.isnan(roll_mean[bar_idx]):
            return False
        return high[bar_idx] >= inner_top[bar_idx] and high[bar_idx] < outer_top[bar_idx]

    def enter_position(bar_idx, dir_):
        """Enter a new position at bar close. Returns position state."""
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

    def record_trade(pnl_pts, regime, t_off, s_off, exit_type, entry_b, exit_b, ep, dir_):
        trades.append((pnl_pts, regime, t_off, s_off, exit_type))
        if detail_trades is not None:
            entry_date_str = str(dates[entry_b]).strip()
            if entry_date_str == detail_date:
                detail_trades.append({
                    "entry_bar": entry_b,
                    "exit_bar": exit_b,
                    "direction": dir_,
                    "entry_price": ep,
                    "exit_type": exit_type,
                    "pnl_points": round(pnl_pts, 4),
                    "pnl_dollar": round(pnl_pts * POINT_VALUE, 2),
                    "entry_date": entry_date_str,
                    "entry_time": str(df["Time"].values[entry_b]).strip() if entry_b < n else "",
                    "exit_date": str(dates[exit_b]).strip(),
                    "exit_time": str(df["Time"].values[exit_b]).strip() if exit_b < n else "",
                    "target_offset": round(t_off, 4),
                    "stop_offset": round(s_off, 4),
                    "target_price": round(ep + t_off if dir_ == "long" else ep - t_off, 4),
                    "stop_price": round(ep - s_off if dir_ == "long" else ep + s_off, 4),
                    "close_at_exit": close[exit_b],
                    "high_at_exit": high[exit_b],
                    "low_at_exit": low[exit_b],
                    "innerTop": round(inner_top[exit_b], 4) if not np.isnan(inner_top[exit_b]) else "",
                    "innerBot": round(inner_bot[exit_b], 4) if not np.isnan(inner_bot[exit_b]) else "",
                    "outerTop": round(outer_top[exit_b], 4) if not np.isnan(outer_top[exit_b]) else "",
                    "outerBot": round(outer_bot[exit_b], 4) if not np.isnan(outer_bot[exit_b]) else "",
                    "mean": round(roll_mean[exit_b], 4) if not np.isnan(roll_mean[exit_b]) else "",
                    "stddev": round(roll_std[exit_b], 4) if not np.isnan(roll_std[exit_b]) else "",
                })

    i = LOOKBACK  # start after we have valid rolling stats
    while i < n:
        if not in_position:
            # Check for entry signal at bar i
            if np.isnan(roll_mean[i]):
                i += 1
                continue

            # RTH filter: only allow new entries during RTH
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

            (entry_price, target_price, stop_price,
             entry_target_offset, entry_stop_offset,
             entry_regime, entry_bar) = enter_position(i, direction)
            in_position = True
            i += 1
            continue

        # In position — check bar i for resolution
        if i >= n:
            break

        if direction == "long":
            target_hit = high[i] >= target_price
            stop_hit = low[i] <= stop_price
            buy_reversal = False  # already long, can't buy-reverse
            # RTH filter: reversal entries blocked outside RTH
            sell_reversal = check_sell_signal(i) and is_rth(i)
        else:  # short
            target_hit = low[i] <= target_price
            stop_hit = high[i] >= stop_price
            # RTH filter: reversal entries blocked outside RTH
            buy_reversal = check_buy_signal(i) and is_rth(i)
            sell_reversal = False  # already short, can't sell-reverse

        reversal_signal = buy_reversal or sell_reversal

        # Priority resolution
        if target_hit and stop_hit:
            # Both target and stop on same bar — use close
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

            record_trade(pnl, entry_regime, entry_target_offset,
                         entry_stop_offset, exit_type, entry_bar, i,
                         entry_price, direction)
            in_position = False
            direction = None
            # Same-bar re-entry: don't advance i, let the outer loop
            # re-check bar i for a new entry signal (RTH check applied there)
            continue

        elif target_hit:
            # Target takes priority over reversal
            pnl = entry_target_offset
            record_trade(pnl, entry_regime, entry_target_offset,
                         entry_stop_offset, "target", entry_bar, i,
                         entry_price, direction)
            in_position = False
            direction = None
            # Same-bar re-entry
            continue

        elif stop_hit:
            # Stop takes priority over reversal
            pnl = -entry_stop_offset
            record_trade(pnl, entry_regime, entry_target_offset,
                         entry_stop_offset, "stop", entry_bar, i,
                         entry_price, direction)
            in_position = False
            direction = None
            # Same-bar re-entry
            continue

        elif reversal_signal:
            # Reversal: exit current position at close, enter opposite
            # (RTH already checked above when computing reversal signals)
            if direction == "long":
                pnl = close[i] - entry_price  # long exit PnL
                exit_type = "reversal"
                new_direction = "short"
            else:
                pnl = entry_price - close[i]  # short exit PnL
                exit_type = "reversal"
                new_direction = "long"

            record_trade(pnl, entry_regime, entry_target_offset,
                         entry_stop_offset, exit_type, entry_bar, i,
                         entry_price, direction)

            # Immediately enter opposite direction
            (entry_price, target_price, stop_price,
             entry_target_offset, entry_stop_offset,
             entry_regime, entry_bar) = enter_position(i, new_direction)
            direction = new_direction
            in_position = True
            i += 1
            continue

        # No resolution this bar
        i += 1

    return trades, detail_trades


# --- Simulate all combinations ---
combos = [(im, om) for im, om in product(INNER_MULTS, OUTER_MULTS) if om > im]
print(f"Testing {len(combos)} combinations...")

results = []
regime_results = []

for combo_idx, (inner_mult, outer_mult) in enumerate(combos):
    trades, _ = simulate(inner_mult, outer_mult)

    if len(trades) == 0:
        continue

    pnls = np.array([t[0] for t in trades])
    regimes = [t[1] for t in trades]
    exit_types = [t[4] for t in trades]

    wins = pnls > 0
    losses = pnls < 0
    win_count = int(np.sum(wins))
    loss_count = int(np.sum(losses))
    total = len(pnls)
    win_rate = win_count / total * 100 if total > 0 else 0

    avg_win_pts = np.mean(pnls[wins]) if win_count > 0 else 0
    avg_loss_pts = np.mean(pnls[losses]) if loss_count > 0 else 0
    avg_win_dollar = avg_win_pts * POINT_VALUE
    avg_loss_dollar = avg_loss_pts * POINT_VALUE

    ev_pts = np.mean(pnls)
    ev_dollar = ev_pts * POINT_VALUE

    gross_wins = np.sum(pnls[wins]) * POINT_VALUE if win_count > 0 else 0
    gross_losses = abs(np.sum(pnls[losses]) * POINT_VALUE) if loss_count > 0 else 0
    profit_factor = gross_wins / gross_losses if gross_losses > 0 else float("inf")

    # Max consecutive losses
    max_consec_loss = 0
    current_streak = 0
    for p in pnls:
        if p < 0:
            current_streak += 1
            max_consec_loss = max(max_consec_loss, current_streak)
        else:
            current_streak = 0

    trades_per_day = total / unique_dates if unique_dates > 0 else 0

    target_dists = np.array([t[2] for t in trades])
    stop_dists = np.array([t[3] for t in trades])
    avg_target_dist = np.mean(target_dists)
    avg_stop_dist = np.mean(stop_dists)
    rr_ratio = avg_target_dist / avg_stop_dist if avg_stop_dist > 0 else float("inf")

    # Count exit types
    n_target = sum(1 for e in exit_types if e == "target")
    n_stop = sum(1 for e in exit_types if e == "stop")
    n_reversal = sum(1 for e in exit_types if e == "reversal")

    results.append({
        "inner_mult": inner_mult,
        "outer_mult": outer_mult,
        "total_trades": total,
        "win_count": win_count,
        "loss_count": loss_count,
        "win_rate_pct": round(win_rate, 2),
        "avg_win_dollar": round(avg_win_dollar, 2),
        "avg_loss_dollar": round(avg_loss_dollar, 2),
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

    # --- Regime breakdown ---
    for regime_name in ["low", "medium", "high"]:
        regime_mask = [r == regime_name for r in regimes]
        regime_pnls = pnls[regime_mask]
        regime_count = len(regime_pnls)
        if regime_count == 0:
            regime_results.append({
                "inner_mult": inner_mult,
                "outer_mult": outer_mult,
                "regime": regime_name,
                "trade_count": 0,
                "win_rate_pct": 0,
                "ev_per_trade_dollar": 0,
            })
            continue

        regime_wins = np.sum(regime_pnls > 0)
        regime_wr = regime_wins / regime_count * 100
        regime_ev = np.mean(regime_pnls) * POINT_VALUE

        regime_results.append({
            "inner_mult": inner_mult,
            "outer_mult": outer_mult,
            "regime": regime_name,
            "trade_count": regime_count,
            "win_rate_pct": round(regime_wr, 2),
            "ev_per_trade_dollar": round(regime_ev, 2),
        })

    if (combo_idx + 1) % 5 == 0:
        print(f"  Completed {combo_idx + 1}/{len(combos)} combinations...")

print(f"Simulation complete. {len(results)} combinations with trades.")

# --- Write CSVs ---
results_df = pd.DataFrame(results)
results_df = results_df.sort_values("ev_per_trade_dollar", ascending=False)
results_df.to_csv(os.path.join(OUTPUT_DIR, "multiplier-grid-baseline.csv"), index=False)
print("Wrote multiplier-grid-baseline.csv")

regime_df = pd.DataFrame(regime_results)
regime_df.to_csv(os.path.join(OUTPUT_DIR, "multiplier-grid-by-regime.csv"), index=False)
print("Wrote multiplier-grid-by-regime.csv")

# --- Single-day trade log for 10/10/2025 with inner=1.0, outer=2.0 ---
print("\nGenerating single-day trade log for 10/10/2025 (inner=1.0, outer=2.0)...")
_, detail_trades = simulate(1.0, 2.0, detail_date="10/10/2025")

if detail_trades:
    detail_df = pd.DataFrame(detail_trades)
    col_order = [
        "entry_bar", "exit_bar", "direction", "entry_price",
        "target_price", "stop_price", "target_offset", "stop_offset",
        "exit_type", "pnl_points", "pnl_dollar", "close_at_exit",
        "high_at_exit", "low_at_exit",
        "entry_date", "entry_time", "exit_date", "exit_time",
        "mean", "stddev", "innerTop", "innerBot", "outerTop", "outerBot",
    ]
    detail_df = detail_df[[c for c in col_order if c in detail_df.columns]]
    detail_df.to_csv(os.path.join(OUTPUT_DIR, "calibration-single-day-10102025.csv"), index=False)
    print(f"Wrote calibration-single-day-10102025.csv ({len(detail_trades)} trades)")
else:
    print("No trades found entering on 10/10/2025.")

# --- Write summary ---
top5 = results_df.head(5)
summary_lines = []
summary_lines.append("RANGE-FADE MULTIPLIER GRID SEARCH — SUMMARY (v4: ddof=0 + same-bar re-entry + reversals + RTH filter)")
summary_lines.append("=" * 95)
summary_lines.append(f"Data: {n} bars, {unique_dates} trading days")
summary_lines.append(f"RTH bars (09:30-15:45): {rth_count} / {n} ({rth_count/n*100:.1f}%)")
summary_lines.append(f"Lookback: {LOOKBACK}")
summary_lines.append(f"StdDev terciles: low <= {tercile_low:.2f}, medium <= {tercile_high:.2f}, high > {tercile_high:.2f}")
summary_lines.append(f"Combinations tested: {len(combos)}")
summary_lines.append(f"Combinations with trades: {len(results)}")
summary_lines.append("")
summary_lines.append("CORRECTIONS APPLIED:")
summary_lines.append("  1. StdDev uses ddof=0 (population std) — matches C++ sqrtf(sumSq / period).")
summary_lines.append("     Previous version used ddof=1 (sample std), overstating band width.")
summary_lines.append("  2. Same-bar re-entry after trade resolution — after a trade resolves at")
summary_lines.append("     bar j, bar j is re-checked for a new entry signal before advancing.")
summary_lines.append("  3. Reversal logic — while in a position, each bar is checked for opposite-")
summary_lines.append("     side entry signals. If the opposite signal fires (touch inner, stay inside")
summary_lines.append("     outer), the current position is exited at bar close and the opposite")
summary_lines.append("     direction is entered immediately at close with fresh target/stop offsets.")
summary_lines.append("     Priority: target > stop > reversal on same bar.")
summary_lines.append("  4. RTH time filter — new entries (fresh and reversal) only allowed during")
summary_lines.append("     09:30:00 - 15:45:00 ET. Target/stop exits continue outside RTH.")
summary_lines.append("     Matches C++ v3 study AllowNewEntryStartTime / AllowNewEntryEndTime.")
summary_lines.append("")
summary_lines.append("TOP 5 COMBINATIONS BY EXPECTED VALUE ($/contract/trade)")
summary_lines.append("-" * 95)

for idx, row in top5.iterrows():
    summary_lines.append(
        f"  inner={row['inner_mult']:.2f}  outer={row['outer_mult']:.2f}  |  "
        f"EV=${row['ev_per_trade_dollar']:.2f}  WR={row['win_rate_pct']:.1f}%  "
        f"PF={row['profit_factor']:.2f}  Trades={row['total_trades']}  "
        f"R:R={row['reward_risk_ratio']:.2f}  "
        f"MaxConsecL={row['max_consecutive_losses']}  "
        f"Trades/day={row['trades_per_day']:.1f}"
    )
    summary_lines.append(
        f"    exits: target={row['target_exits']}  stop={row['stop_exits']}  "
        f"reversal={row['reversal_exits']}"
    )

summary_lines.append("")
summary_lines.append("REGIME BREAKDOWN FOR TOP 5")
summary_lines.append("-" * 95)

for idx, row in top5.iterrows():
    summary_lines.append(f"  inner={row['inner_mult']:.2f}  outer={row['outer_mult']:.2f}:")
    sub = regime_df[
        (regime_df["inner_mult"] == row["inner_mult"])
        & (regime_df["outer_mult"] == row["outer_mult"])
    ]
    for _, rrow in sub.iterrows():
        summary_lines.append(
            f"    {rrow['regime']:>6s}: count={rrow['trade_count']:>5d}  "
            f"WR={rrow['win_rate_pct']:>5.1f}%  EV=${rrow['ev_per_trade_dollar']:>8.2f}"
        )

# Pattern analysis across regimes
summary_lines.append("")
summary_lines.append("REGIME PATTERNS ACROSS ALL COMBINATIONS")
summary_lines.append("-" * 95)

for regime_name in ["low", "medium", "high"]:
    sub = regime_df[regime_df["regime"] == regime_name]
    sub_with_trades = sub[sub["trade_count"] > 0]
    if len(sub_with_trades) > 0:
        avg_wr = sub_with_trades["win_rate_pct"].mean()
        avg_ev = sub_with_trades["ev_per_trade_dollar"].mean()
        avg_count = sub_with_trades["trade_count"].mean()
        pos_ev_count = (sub_with_trades["ev_per_trade_dollar"] > 0).sum()
        summary_lines.append(
            f"  {regime_name:>6s} regime:  avg WR={avg_wr:.1f}%  avg EV=${avg_ev:.2f}  "
            f"avg trades={avg_count:.0f}  positive-EV combos={pos_ev_count}/{len(sub_with_trades)}"
        )

# Overall statistics
summary_lines.append("")
summary_lines.append("OVERALL STATISTICS")
summary_lines.append("-" * 95)
pos_ev = results_df[results_df["ev_per_trade_dollar"] > 0]
neg_ev = results_df[results_df["ev_per_trade_dollar"] <= 0]
summary_lines.append(f"  Positive-EV combinations: {len(pos_ev)}/{len(results_df)}")
summary_lines.append(f"  Negative-EV combinations: {len(neg_ev)}/{len(results_df)}")
if len(pos_ev) > 0:
    summary_lines.append(f"  Best EV: ${pos_ev.iloc[0]['ev_per_trade_dollar']:.2f} "
                         f"(inner={pos_ev.iloc[0]['inner_mult']}, outer={pos_ev.iloc[0]['outer_mult']})")
if len(neg_ev) > 0:
    worst = results_df.iloc[-1]
    summary_lines.append(f"  Worst EV: ${worst['ev_per_trade_dollar']:.2f} "
                         f"(inner={worst['inner_mult']}, outer={worst['outer_mult']})")

# Reversal statistics
summary_lines.append("")
summary_lines.append("REVERSAL EXIT STATISTICS")
summary_lines.append("-" * 95)
total_reversals = results_df["reversal_exits"].sum()
total_all_trades = results_df["total_trades"].sum()
summary_lines.append(f"  Total reversal exits across all combos: {total_reversals}")
summary_lines.append(f"  Total trades across all combos: {total_all_trades}")
if total_all_trades > 0:
    summary_lines.append(f"  Reversal exits as % of all trades: {total_reversals / total_all_trades * 100:.1f}%")

summary_lines.append("")
summary_lines.append("FULL GRID (sorted by EV)")
summary_lines.append("-" * 95)
summary_lines.append(
    f"  {'inner':>5s}  {'outer':>5s}  {'Trades':>6s}  {'WR%':>5s}  "
    f"{'EV$':>8s}  {'PF':>6s}  {'R:R':>5s}  {'MaxCL':>5s}  {'T/day':>5s}  "
    f"{'Tgt':>4s}  {'Stp':>4s}  {'Rev':>4s}"
)
for _, row in results_df.iterrows():
    summary_lines.append(
        f"  {row['inner_mult']:>5.2f}  {row['outer_mult']:>5.2f}  "
        f"{int(row['total_trades']):>6d}  {row['win_rate_pct']:>5.1f}  "
        f"{row['ev_per_trade_dollar']:>8.2f}  {row['profit_factor']:>6.2f}  "
        f"{row['reward_risk_ratio']:>5.2f}  {int(row['max_consecutive_losses']):>5d}  "
        f"{row['trades_per_day']:>5.1f}  "
        f"{int(row['target_exits']):>4d}  {int(row['stop_exits']):>4d}  "
        f"{int(row['reversal_exits']):>4d}"
    )

# Single-day detail
if detail_trades:
    summary_lines.append("")
    summary_lines.append("SINGLE-DAY TRADE LOG: 10/10/2025 (inner=1.0, outer=2.0, RTH only)")
    summary_lines.append("-" * 95)
    summary_lines.append(f"  {len(detail_trades)} trades entering on 10/10/2025")
    for t in detail_trades:
        summary_lines.append(
            f"  bar {t['entry_bar']}->{t['exit_bar']}  {t['direction']:>5s}  "
            f"entry={t['entry_price']:.2f}  exit_type={t['exit_type']}  "
            f"PnL={t['pnl_points']:+.2f}pts (${t['pnl_dollar']:+.2f})"
        )

summary_text = "\n".join(summary_lines)
with open(os.path.join(OUTPUT_DIR, "multiplier-grid-summary.txt"), "w") as f:
    f.write(summary_text)
print("Wrote multiplier-grid-summary.txt")

print("\nDone.")
print("\n" + summary_text)
