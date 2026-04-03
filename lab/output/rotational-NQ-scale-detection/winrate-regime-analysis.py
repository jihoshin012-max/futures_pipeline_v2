"""Win-rate regime analysis for rangefade rotation.

Simulates the base config (LB=100, inner=0.75, outer=1.75, CB N=2, MDL $2500,
no martingale, RTH only, entry at bar close, ddof=0, cost $4/trade) and tags
every trade with market conditions at entry time.

Then groups trades into 20-trade blocks, classifies blocks by win rate
(high >= 60%, low < 40%, medium in between), and identifies which market
conditions best separate high from low win-rate blocks.

Outputs:
  - winrate-regime-trades.csv
  - winrate-regime-blocks.csv
  - winrate-regime-conditions.csv
  - winrate-regime-separators.csv
  - winrate-regime-daily.csv
  - winrate-regime-summary.txt
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

CB_N = 2  # circuit breaker consecutive stop threshold
MDL = 2500.0  # max daily loss

RTH_START = "09:30:00"
RTH_END = "15:45:00"

BLOCK_SIZE = 20

DATA_PATH = Path(r"C:\Projects\futures_pipeline\data\NQ-250tick-calibration.csv")
OUTPUT_DIR = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")


# ── Load bars ──
def load_bars(filepath: Path):
    dates_str, times_str = [], []
    date_ints, time_secs_list = [], []
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

            # Parse time to seconds for duration calc
            tp = time_s[:8].split(":")
            secs = int(tp[0]) * 3600 + int(tp[1]) * 60 + int(tp[2])
            time_secs_list.append(secs)

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
        "time_secs": np.array(time_secs_list, dtype=np.int32),
        "open": np.array(opens, dtype=np.float64),
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


# ── Precompute market condition arrays ──
def compute_conditions(bars: dict, bands: dict):
    """Precompute rolling market condition arrays for the full bar series."""
    n = bars["n"]
    c = bars["close"]
    h = bars["high"]
    l = bars["low"]
    time_secs = bars["time_secs"]

    # (a) 100-bar stddev — already in bands["std"]
    std100 = bands["std"]

    # (b) Rolling 10-bar avg bar range (H-L)
    bar_range = h - l
    avg_range_10 = np.full(n, np.nan)
    for i in range(9, n):
        avg_range_10[i] = np.mean(bar_range[i - 9 : i + 1])

    # (c) Rolling 10-bar avg bar duration (seconds)
    # Duration = time diff between consecutive bars (same day only)
    date_int = bars["date_int"]
    bar_duration = np.full(n, np.nan)
    for i in range(1, n):
        if date_int[i] == date_int[i - 1]:
            bar_duration[i] = float(time_secs[i] - time_secs[i - 1])
        else:
            bar_duration[i] = np.nan

    avg_duration_10 = np.full(n, np.nan)
    for i in range(10, n):
        window = bar_duration[i - 9 : i + 1]
        valid = window[~np.isnan(window)]
        if len(valid) >= 5:
            avg_duration_10[i] = np.mean(valid)

    # (d) Band width = inner_top - inner_bot (points)
    band_width = bands["inner_top"] - bands["inner_bot"]

    # (e) Range/BW ratio = avg_range_10 / band_width
    range_bw_ratio = np.full(n, np.nan)
    valid_mask = (~np.isnan(avg_range_10)) & (~np.isnan(band_width)) & (band_width > 0)
    range_bw_ratio[valid_mask] = avg_range_10[valid_mask] / band_width[valid_mask]

    # (f) EMA50 position relative to bands
    # Compute EMA50 of close
    ema50 = np.full(n, np.nan)
    alpha = 2.0 / (50 + 1)
    ema50[0] = c[0]
    for i in range(1, n):
        ema50[i] = alpha * c[i] + (1 - alpha) * ema50[i - 1]

    ema_position = np.full(n, np.nan)
    valid_mask = (~np.isnan(bands["inner_top"])) & (band_width > 0)
    ema_position[valid_mask] = (
        (ema50[valid_mask] - bands["inner_bot"][valid_mask]) / band_width[valid_mask]
    )

    # (g) Price position relative to bands
    price_position = np.full(n, np.nan)
    price_position[valid_mask] = (
        (c[valid_mask] - bands["inner_bot"][valid_mask]) / band_width[valid_mask]
    )

    # (h) StdDev ratio: 100-bar / 500-bar
    std500 = np.full(n, np.nan)
    for i in range(499, n):
        window = c[i - 499 : i + 1]
        std500[i] = np.std(window, ddof=0)

    stddev_ratio = np.full(n, np.nan)
    valid_mask = (~np.isnan(std100)) & (~np.isnan(std500)) & (std500 > 0)
    stddev_ratio[valid_mask] = std100[valid_mask] / std500[valid_mask]

    # (i) Hour of day
    hour = np.array([ts // 3600 for ts in time_secs], dtype=np.float64)

    return {
        "std100": std100,
        "avg_range_10": avg_range_10,
        "avg_duration_10": avg_duration_10,
        "band_width": band_width,
        "range_bw_ratio": range_bw_ratio,
        "ema_position": ema_position,
        "price_position": price_position,
        "stddev_ratio": stddev_ratio,
        "hour": hour,
    }


CONDITION_NAMES = [
    "std100", "avg_range_10", "avg_duration_10", "band_width",
    "range_bw_ratio", "ema_position", "price_position", "stddev_ratio", "hour",
]


def is_rth(time_hms: str) -> bool:
    return RTH_START <= time_hms <= RTH_END


# ── Simulate with CB N=2 and MDL $2500 ──
def simulate(bars: dict, bands: dict, conditions: dict):
    """Run the rangefade rotation sim with CB N=2 and MDL $2500.
    Returns list of trade dicts with market conditions at entry."""
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

    max_consec = CB_N

    # Position state
    in_position = False
    direction = None
    entry_price = 0.0
    target_price = 0.0
    stop_price = 0.0
    entry_target_offset = 0.0
    entry_stop_offset = 0.0
    entry_bar_idx = 0
    entry_conditions = {}

    # Consecutive stop state
    consec_long = 0
    consec_short = 0
    last_date = 0

    # MDL state
    daily_pnl_running = 0.0
    mdl_date = 0
    mdl_locked = False

    trades = []

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

    def get_conditions_at(idx):
        """Snapshot market conditions at bar idx."""
        conds = {}
        for name in CONDITION_NAMES:
            val = conditions[name][idx]
            conds[name] = val if not np.isnan(val) else None
        return conds

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

    def record_trade(exit_bar_idx, exit_type, pnl_pts, exit_price_val):
        nonlocal daily_pnl_running, mdl_locked
        pnl_dollar = pnl_pts * POINT_VALUE - COST_PER_TRADE
        is_loss = pnl_dollar < 0

        trade = {
            "entry_date": date_str[entry_bar_idx],
            "entry_time": time_str[entry_bar_idx],
            "exit_date": date_str[exit_bar_idx],
            "exit_time": time_str[exit_bar_idx],
            "direction": direction,
            "entry_price": round(entry_price, 2),
            "exit_price": round(exit_price_val, 2),
            "exit_type": exit_type,
            "pnl_pts": round(pnl_pts, 4),
            "pnl_dollar": round(pnl_dollar, 2),
            "win": 1 if pnl_dollar > 0 else 0,
        }
        # Attach entry conditions
        for name in CONDITION_NAMES:
            trade[name] = entry_conditions.get(name)

        trades.append(trade)
        update_consec(direction, is_loss)

        # MDL tracking
        daily_pnl_running += pnl_dollar
        if daily_pnl_running <= -MDL:
            mdl_locked = True

        return is_loss

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
            daily_pnl_running = 0.0
            mdl_locked = False
            mdl_date = today

        if not in_position:
            if not is_rth(time_str[i]):
                i += 1
                continue

            # MDL check
            if mdl_locked:
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
            entry_bar_idx = i
            entry_conditions = get_conditions_at(i)
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

            exit_price_val = entry_price + pnl_pts if direction == "long" else entry_price - pnl_pts
            record_trade(i, exit_type, pnl_pts, exit_price_val)
            in_position = False
            direction = None
            continue

        elif target_hit:
            pnl_pts = entry_target_offset
            exit_price_val = target_price
            record_trade(i, "TARGET", pnl_pts, exit_price_val)
            in_position = False
            direction = None
            continue

        elif stop_hit:
            pnl_pts = -entry_stop_offset
            exit_price_val = stop_price
            record_trade(i, "STOP", pnl_pts, exit_price_val)
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

            exit_price_val = close[i]
            record_trade(i, "REVERSAL", pnl_pts, exit_price_val)

            # Check MDL after reversal exit
            if mdl_locked:
                in_position = False
                direction = None
                i += 1
                continue

            # Check if new direction blocked
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
            entry_bar_idx = i
            entry_conditions = get_conditions_at(i)
            i += 1
            continue

        i += 1

    return trades


# ── Block analysis ──
def compute_blocks(trades: list[dict]):
    """Split trades into non-overlapping blocks of BLOCK_SIZE."""
    blocks = []
    n_full = len(trades) // BLOCK_SIZE
    for b in range(n_full):
        start = b * BLOCK_SIZE
        end = start + BLOCK_SIZE
        chunk = trades[start:end]

        wins = sum(t["win"] for t in chunk)
        wr = wins / BLOCK_SIZE * 100
        total_pnl = sum(t["pnl_dollar"] for t in chunk)

        block = {
            "block_num": b + 1,
            "trade_start": start + 1,
            "trade_end": end,
            "win_rate": round(wr, 1),
            "total_pnl": round(total_pnl, 2),
            "wins": wins,
            "losses": BLOCK_SIZE - wins,
        }

        # Classify
        if wr >= 60:
            block["class"] = "HIGH"
        elif wr < 40:
            block["class"] = "LOW"
        else:
            block["class"] = "MEDIUM"

        # Average conditions
        for name in CONDITION_NAMES:
            vals = [t[name] for t in chunk if t[name] is not None]
            block[name] = round(np.mean(vals), 6) if vals else None

        blocks.append(block)

    return blocks


def compute_condition_stats(blocks: list[dict]):
    """Compute condition averages/medians by block class."""
    classes = ["HIGH", "MEDIUM", "LOW"]
    stats = {}
    for cls in classes:
        cls_blocks = [b for b in blocks if b["class"] == cls]
        stats[cls] = {"count": len(cls_blocks)}
        for name in CONDITION_NAMES:
            vals = [b[name] for b in cls_blocks if b[name] is not None]
            if vals:
                stats[cls][f"{name}_mean"] = round(np.mean(vals), 6)
                stats[cls][f"{name}_median"] = round(np.median(vals), 6)
            else:
                stats[cls][f"{name}_mean"] = None
                stats[cls][f"{name}_median"] = None
    return stats


def find_best_separator(blocks: list[dict], cond_name: str):
    """Find the threshold that best separates HIGH from LOW blocks for a given condition.
    Uses a simple decision stump approach."""
    # Get blocks that are HIGH or LOW with valid condition values
    hl_blocks = [b for b in blocks if b["class"] in ("HIGH", "LOW") and b[cond_name] is not None]
    if len(hl_blocks) < 4:
        return None

    vals = np.array([b[cond_name] for b in hl_blocks])
    labels = np.array([1 if b["class"] == "HIGH" else 0 for b in hl_blocks])

    # Sort by value
    order = np.argsort(vals)
    vals_sorted = vals[order]
    labels_sorted = labels[order]

    best_acc = 0
    best_thresh = None
    best_direction = None  # "above" means HIGH above threshold
    best_wr_above = None
    best_wr_below = None
    n_total = len(hl_blocks)

    # Try thresholds at midpoints
    unique_vals = np.unique(vals_sorted)
    if len(unique_vals) < 2:
        return None

    thresholds = (unique_vals[:-1] + unique_vals[1:]) / 2

    for thresh in thresholds:
        above_mask = vals > thresh
        below_mask = ~above_mask

        n_above = above_mask.sum()
        n_below = below_mask.sum()
        if n_above < 1 or n_below < 1:
            continue

        # Direction 1: HIGH above threshold
        correct_1 = (labels[above_mask] == 1).sum() + (labels[below_mask] == 0).sum()
        acc_1 = correct_1 / n_total

        # Direction 2: HIGH below threshold
        correct_2 = (labels[above_mask] == 0).sum() + (labels[below_mask] == 1).sum()
        acc_2 = correct_2 / n_total

        if acc_1 >= acc_2 and acc_1 > best_acc:
            best_acc = acc_1
            best_thresh = thresh
            best_direction = "HIGH_above"
        elif acc_2 > acc_1 and acc_2 > best_acc:
            best_acc = acc_2
            best_thresh = thresh
            best_direction = "HIGH_below"

    if best_thresh is None:
        return None

    # Compute actual win rates on each side of threshold
    above_mask = np.array([b[cond_name] > best_thresh for b in blocks if b[cond_name] is not None])
    all_valid = [b for b in blocks if b[cond_name] is not None]

    above_blocks = [b for b in all_valid if b[cond_name] > best_thresh]
    below_blocks = [b for b in all_valid if b[cond_name] <= best_thresh]

    wr_above = np.mean([b["win_rate"] for b in above_blocks]) if above_blocks else None
    wr_below = np.mean([b["win_rate"] for b in below_blocks]) if below_blocks else None
    n_above = len(above_blocks)
    n_below = len(below_blocks)

    return {
        "condition": cond_name,
        "threshold": round(best_thresh, 6),
        "accuracy": round(best_acc, 4),
        "direction": best_direction,
        "wr_above_thresh": round(wr_above, 2) if wr_above is not None else None,
        "wr_below_thresh": round(wr_below, 2) if wr_below is not None else None,
        "n_above": n_above,
        "n_below": n_below,
    }


def compute_daily_stats(trades: list[dict]):
    """Group trades by exit date and compute daily stats."""
    from collections import defaultdict
    daily = defaultdict(list)
    for t in trades:
        daily[t["exit_date"]].append(t)

    results = []
    for date, day_trades in sorted(daily.items()):
        n_trades = len(day_trades)
        wins = sum(t["win"] for t in day_trades)
        wr = wins / n_trades * 100 if n_trades > 0 else 0
        total_pnl = sum(t["pnl_dollar"] for t in day_trades)

        row = {
            "date": date,
            "n_trades": n_trades,
            "wins": wins,
            "win_rate": round(wr, 1),
            "total_pnl": round(total_pnl, 2),
        }
        for name in CONDITION_NAMES:
            vals = [t[name] for t in day_trades if t[name] is not None]
            row[name] = round(np.mean(vals), 6) if vals else None

        results.append(row)

    return results


# ── Main ──
def main():
    if not DATA_PATH.exists():
        print(f"ERROR: Cannot find bar data at {DATA_PATH}")
        sys.exit(1)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Loading 250-tick bars from {DATA_PATH}...")
    bars = load_bars(DATA_PATH)
    print(f"Loaded {bars['n']} bars")

    print("Computing bands...")
    bands = compute_bands(bars)

    print("Precomputing market conditions...")
    conditions = compute_conditions(bars, bands)

    print("Simulating (CB N=2, MDL $2500)...")
    trades = simulate(bars, bands, conditions)
    print(f"Total trades: {len(trades)}")

    # ── Step 1: Write trades CSV ──
    trades_path = OUTPUT_DIR / "winrate-regime-trades.csv"
    trade_cols = [
        "entry_date", "entry_time", "exit_date", "exit_time", "direction",
        "entry_price", "exit_price", "exit_type", "pnl_pts", "pnl_dollar", "win",
    ] + CONDITION_NAMES

    with open(trades_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=trade_cols)
        w.writeheader()
        for t in trades:
            row = {k: t.get(k) for k in trade_cols}
            # Round condition values
            for name in CONDITION_NAMES:
                if row[name] is not None:
                    row[name] = round(row[name], 6)
            w.writerow(row)
    print(f"Wrote {len(trades)} trades to {trades_path}")

    # ── Step 2: Blocks ──
    blocks = compute_blocks(trades)
    blocks_path = OUTPUT_DIR / "winrate-regime-blocks.csv"
    block_cols = ["block_num", "trade_start", "trade_end", "wins", "losses",
                  "win_rate", "total_pnl", "class"] + CONDITION_NAMES
    with open(blocks_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=block_cols)
        w.writeheader()
        for b in blocks:
            w.writerow({k: b.get(k) for k in block_cols})
    print(f"Wrote {len(blocks)} blocks to {blocks_path}")

    # ── Step 3: Condition stats by class ──
    cond_stats = compute_condition_stats(blocks)
    cond_path = OUTPUT_DIR / "winrate-regime-conditions.csv"
    cond_cols = ["class", "count"]
    for name in CONDITION_NAMES:
        cond_cols.append(f"{name}_mean")
        cond_cols.append(f"{name}_median")
    with open(cond_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cond_cols)
        w.writeheader()
        for cls in ["HIGH", "MEDIUM", "LOW"]:
            row = {"class": cls, "count": cond_stats[cls]["count"]}
            for name in CONDITION_NAMES:
                row[f"{name}_mean"] = cond_stats[cls].get(f"{name}_mean")
                row[f"{name}_median"] = cond_stats[cls].get(f"{name}_median")
            w.writerow(row)
    print(f"Wrote condition stats to {cond_path}")

    # ── Step 4: Best separators ──
    separators = []
    for name in CONDITION_NAMES:
        result = find_best_separator(blocks, name)
        if result:
            separators.append(result)
    separators.sort(key=lambda x: x["accuracy"], reverse=True)

    sep_path = OUTPUT_DIR / "winrate-regime-separators.csv"
    sep_cols = ["condition", "threshold", "accuracy", "direction",
                "wr_above_thresh", "wr_below_thresh", "n_above", "n_below"]
    with open(sep_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=sep_cols)
        w.writeheader()
        for s in separators:
            w.writerow(s)
    print(f"Wrote {len(separators)} separators to {sep_path}")

    # ── Step 5: Daily stats ──
    daily = compute_daily_stats(trades)
    daily_path = OUTPUT_DIR / "winrate-regime-daily.csv"
    daily_cols = ["date", "n_trades", "wins", "win_rate", "total_pnl"] + CONDITION_NAMES
    with open(daily_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=daily_cols)
        w.writeheader()
        for d in daily:
            w.writerow({k: d.get(k) for k in daily_cols})
    print(f"Wrote {len(daily)} daily rows to {daily_path}")

    # ── Summary ──
    lines = []
    lines.append("=" * 90)
    lines.append("WIN-RATE REGIME ANALYSIS")
    lines.append("=" * 90)
    lines.append(f"Config: LB={LOOKBACK}, Inner={INNER_MULT}, Outer={OUTER_MULT}, CB N={CB_N}, MDL=${MDL}")
    lines.append(f"Data: {DATA_PATH.name} ({bars['n']} bars)")
    lines.append(f"Cost: ${COST_PER_TRADE}/trade, RTH 09:30-15:45, ddof=0")
    lines.append(f"Block size: {BLOCK_SIZE} trades")
    lines.append(f"Total trades: {len(trades)}")
    lines.append(f"Total blocks: {len(blocks)}")
    lines.append("")

    # Overall stats
    total_pnl = sum(t["pnl_dollar"] for t in trades)
    total_wins = sum(t["win"] for t in trades)
    overall_wr = total_wins / len(trades) * 100 if trades else 0
    lines.append(f"Overall: WR={overall_wr:.1f}%, PnL=${total_pnl:.2f}")
    lines.append("")

    # Block class distribution
    for cls in ["HIGH", "MEDIUM", "LOW"]:
        cls_blocks = [b for b in blocks if b["class"] == cls]
        if cls_blocks:
            avg_wr = np.mean([b["win_rate"] for b in cls_blocks])
            avg_pnl = np.mean([b["total_pnl"] for b in cls_blocks])
            total_cls_pnl = sum(b["total_pnl"] for b in cls_blocks)
            lines.append(f"{cls:>6s}: {len(cls_blocks)} blocks, avg WR={avg_wr:.1f}%, "
                         f"avg block PnL=${avg_pnl:.2f}, total PnL=${total_cls_pnl:.2f}")
    lines.append("")

    # Condition comparison: HIGH vs LOW
    lines.append("-" * 90)
    lines.append("CONDITION COMPARISON: HIGH vs LOW WIN-RATE BLOCKS")
    lines.append("-" * 90)
    lines.append(f"{'Condition':<20s}  {'HIGH mean':>12s}  {'LOW mean':>12s}  {'Diff':>12s}  {'|Diff|':>10s}")
    lines.append("-" * 70)

    diffs = []
    for name in CONDITION_NAMES:
        h_mean = cond_stats["HIGH"].get(f"{name}_mean")
        l_mean = cond_stats["LOW"].get(f"{name}_mean")
        if h_mean is not None and l_mean is not None:
            diff = h_mean - l_mean
            abs_diff = abs(diff)
            # Normalize by mean magnitude for ranking
            avg_mag = (abs(h_mean) + abs(l_mean)) / 2
            rel_diff = abs_diff / avg_mag if avg_mag > 0 else 0
            diffs.append((name, h_mean, l_mean, diff, abs_diff, rel_diff))

    # Sort by relative difference
    diffs.sort(key=lambda x: x[5], reverse=True)

    for name, h_mean, l_mean, diff, abs_diff, rel_diff in diffs:
        lines.append(f"{name:<20s}  {h_mean:>12.4f}  {l_mean:>12.4f}  {diff:>+12.4f}  {rel_diff:>10.2%}")

    lines.append("")
    lines.append("Ranked by relative difference (|diff| / avg magnitude):")
    for rank, (name, _, _, _, _, rel_diff) in enumerate(diffs, 1):
        lines.append(f"  {rank}. {name} ({rel_diff:.2%})")

    # Separators
    lines.append("")
    lines.append("-" * 90)
    lines.append("BEST SINGLE-CONDITION SEPARATORS (HIGH vs LOW blocks)")
    lines.append("-" * 90)
    lines.append(f"{'Condition':<20s}  {'Threshold':>12s}  {'Accuracy':>8s}  {'Direction':>12s}  "
                 f"{'WR above':>10s}  {'WR below':>10s}  {'N above':>8s}  {'N below':>8s}")
    lines.append("-" * 90)
    for s in separators:
        lines.append(f"{s['condition']:<20s}  {s['threshold']:>12.4f}  {s['accuracy']:>8.2%}  "
                     f"{s['direction']:>12s}  "
                     f"{s['wr_above_thresh']:>10.2f}  {s['wr_below_thresh']:>10.2f}  "
                     f"{s['n_above']:>8d}  {s['n_below']:>8d}")

    # Daily analysis: best/worst 10 days
    lines.append("")
    lines.append("-" * 90)
    lines.append("DAILY ANALYSIS: 10 BEST AND 10 WORST DAYS")
    lines.append("-" * 90)

    daily_sorted = sorted(daily, key=lambda d: d["total_pnl"], reverse=True)
    min_trades_for_daily = 2  # need at least 2 trades to be meaningful

    good_days = [d for d in daily_sorted if d["n_trades"] >= min_trades_for_daily][:10]
    bad_days = [d for d in reversed(daily_sorted) if d["n_trades"] >= min_trades_for_daily][:10]

    lines.append("")
    lines.append("TOP 10 DAYS:")
    lines.append(f"{'Date':<15s}  {'Trades':>6s}  {'WR%':>5s}  {'PnL($)':>10s}  "
                 f"{'std100':>8s}  {'avgRng':>8s}  {'avgDur':>8s}  {'BW':>8s}  "
                 f"{'rng/BW':>8s}  {'EMApos':>8s}  {'Prpos':>8s}  {'sdRat':>8s}  {'Hour':>6s}")
    for d in good_days:
        cvals = []
        for name in CONDITION_NAMES:
            v = d.get(name)
            cvals.append(f"{v:>8.3f}" if v is not None else f"{'N/A':>8s}")
        lines.append(f"{d['date']:<15s}  {d['n_trades']:>6d}  {d['win_rate']:>5.1f}  "
                     f"{d['total_pnl']:>10.2f}  {'  '.join(cvals)}")

    lines.append("")
    lines.append("BOTTOM 10 DAYS:")
    lines.append(f"{'Date':<15s}  {'Trades':>6s}  {'WR%':>5s}  {'PnL($)':>10s}  "
                 f"{'std100':>8s}  {'avgRng':>8s}  {'avgDur':>8s}  {'BW':>8s}  "
                 f"{'rng/BW':>8s}  {'EMApos':>8s}  {'Prpos':>8s}  {'sdRat':>8s}  {'Hour':>6s}")
    for d in bad_days:
        cvals = []
        for name in CONDITION_NAMES:
            v = d.get(name)
            cvals.append(f"{v:>8.3f}" if v is not None else f"{'N/A':>8s}")
        lines.append(f"{d['date']:<15s}  {d['n_trades']:>6d}  {d['win_rate']:>5.1f}  "
                     f"{d['total_pnl']:>10.2f}  {'  '.join(cvals)}")

    # Good vs bad day condition comparison
    lines.append("")
    lines.append("GOOD vs BAD DAY CONDITION AVERAGES:")
    lines.append(f"{'Condition':<20s}  {'Good days':>12s}  {'Bad days':>12s}  {'Diff':>12s}")
    lines.append("-" * 60)
    for name in CONDITION_NAMES:
        good_vals = [d[name] for d in good_days if d.get(name) is not None]
        bad_vals = [d[name] for d in bad_days if d.get(name) is not None]
        g_mean = np.mean(good_vals) if good_vals else None
        b_mean = np.mean(bad_vals) if bad_vals else None
        if g_mean is not None and b_mean is not None:
            diff = g_mean - b_mean
            lines.append(f"{name:<20s}  {g_mean:>12.4f}  {b_mean:>12.4f}  {diff:>+12.4f}")
        else:
            lines.append(f"{name:<20s}  {'N/A':>12s}  {'N/A':>12s}  {'N/A':>12s}")

    lines.append("")

    summary_path = OUTPUT_DIR / "winrate-regime-summary.txt"
    with open(summary_path, "w") as f:
        f.write("\n".join(lines) + "\n")
    print(f"Wrote summary to {summary_path}")

    # Print summary
    print()
    print("\n".join(lines))


if __name__ == "__main__":
    main()
