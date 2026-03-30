# archetype: rotational
"""
mtzz_signals.py — Multi-Threshold ZigZag signal precomputation for rotation scale detection.

Precomputes per-bar signal arrays from 250-tick aggregated bars, then maps back to
1-tick resolution for use in the filtered sweep. All signals are causal (entry-time safe).

Six approaches:
  1. ATR-relative scaling
  2. Range/displacement ratio
  3. Rolling zigzag median
  4. Multi-threshold zigzag (dominant scale + completion counts)
  5. Rolling cycle health (computed inline during sweep, placeholder here)
  6. Completion asymmetry (long/short ratio from zigzag)

Usage:
    from mtzz_signals import load_bars_extended, precompute_all_signals
    bars = load_bars_extended(filepath)
    signals = precompute_all_signals(bars)
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import numba as nb


# ---------------------------------------------------------------------------
#  Zigzag engine — exact copy from fractal_01_prepare.py (authoritative impl)
#  This is the algorithm that will be ported 1:1 to C++.
# ---------------------------------------------------------------------------

@nb.njit(cache=True)
def zigzag(prices, session_ids, threshold):
    """Zig-zag swing detection with session boundary resets.

    Returns (swing_idx, swing_price, swing_dir, swing_sid).
    swing_dir: +1 = confirmed high, -1 = confirmed low.
    """
    n = len(prices)
    max_out = 5_000_000
    out_idx   = np.empty(max_out, dtype=np.int64)
    out_price = np.empty(max_out, dtype=np.float64)
    out_dir   = np.empty(max_out, dtype=np.int8)
    out_sid   = np.empty(max_out, dtype=np.int32)
    cnt = 0

    INIT, UP, DOWN = 0, 1, -1
    state = INIT
    ep = 0.0; ei = np.int64(0)
    sh = 0.0; sl = 0.0
    hi_i = np.int64(0); lo_i = np.int64(0)
    cs = np.int32(-999)

    for i in range(n):
        p = prices[i]
        s = session_ids[i]

        if s != cs:
            cs = s
            state = INIT
            sh = p; sl = p
            hi_i = np.int64(i); lo_i = np.int64(i)
            continue

        if state == INIT:
            if p > sh:
                sh = p; hi_i = np.int64(i)
            if p < sl:
                sl = p; lo_i = np.int64(i)
            if sh - sl >= threshold:
                if cnt >= max_out:
                    break
                if hi_i > lo_i:
                    out_idx[cnt] = lo_i; out_price[cnt] = sl
                    out_dir[cnt] = np.int8(-1); out_sid[cnt] = cs
                    cnt += 1
                    state = UP; ep = sh; ei = hi_i
                else:
                    out_idx[cnt] = hi_i; out_price[cnt] = sh
                    out_dir[cnt] = np.int8(1); out_sid[cnt] = cs
                    cnt += 1
                    state = DOWN; ep = sl; ei = lo_i

        elif state == UP:
            if p > ep:
                ep = p; ei = np.int64(i)
            elif ep - p >= threshold:
                if cnt >= max_out:
                    break
                out_idx[cnt] = ei; out_price[cnt] = ep
                out_dir[cnt] = np.int8(1); out_sid[cnt] = cs
                cnt += 1
                state = DOWN; ep = p; ei = np.int64(i)

        else:  # DOWN
            if p < ep:
                ep = p; ei = np.int64(i)
            elif p - ep >= threshold:
                if cnt >= max_out:
                    break
                out_idx[cnt] = ei; out_price[cnt] = ep
                out_dir[cnt] = np.int8(-1); out_sid[cnt] = cs
                cnt += 1
                state = UP; ep = p; ei = np.int64(i)

    return out_idx[:cnt], out_price[:cnt], out_dir[:cnt], out_sid[:cnt]


# ---------------------------------------------------------------------------
#  Session helpers — from fractal_01_prepare.py
# ---------------------------------------------------------------------------

@nb.njit(cache=True)
def assign_session_ids(keys):
    """Incrementing session ID when key value changes."""
    n = len(keys)
    sids = np.empty(n, dtype=np.int32)
    sid = 0
    sids[0] = sid
    for i in range(1, n):
        if keys[i] != keys[i - 1]:
            sid += 1
        sids[i] = sid
    return sids


# ---------------------------------------------------------------------------
#  Bar loading — extended to include ATR (col 38)
# ---------------------------------------------------------------------------

RTH_OPEN_SEC  = 9 * 3600 + 30 * 60        # 09:30:00
RTH_CLOSE_SEC = 15 * 3600 + 49 * 60 + 50  # 15:49:50


def load_bars_extended(filepath: str) -> dict:
    """Load bar data from SC-format CSV. Returns numpy arrays + ATR."""
    print(f"  Counting rows...")
    with open(filepath, "r") as f:
        n_rows = sum(1 for _ in f) - 1

    print(f"  Allocating arrays for {n_rows} rows...")
    last_arr = np.empty(n_rows, dtype=np.float32)
    high_arr = np.empty(n_rows, dtype=np.float32)
    low_arr  = np.empty(n_rows, dtype=np.float32)
    open_arr = np.empty(n_rows, dtype=np.float32)
    time_sec_arr = np.empty(n_rows, dtype=np.int32)
    date_int_arr = np.empty(n_rows, dtype=np.int32)
    atr_arr  = np.empty(n_rows, dtype=np.float32)
    bid_vol_arr = np.empty(n_rows, dtype=np.float32)
    ask_vol_arr = np.empty(n_rows, dtype=np.float32)
    dt_strings: list[str] = []

    print(f"  Parsing CSV...")
    idx = 0
    with open(filepath, "r") as f:
        header = next(f)
        # Find column indices from header
        cols = [c.strip() for c in header.split(",")]
        atr_col = None
        bid_vol_col = None
        ask_vol_col = None
        for ci, name in enumerate(cols):
            if name == "ATR":
                atr_col = ci
            elif name == "Bid Volume":
                bid_vol_col = ci
            elif name == "Ask Volume":
                ask_vol_col = ci
        if atr_col is None:
            print("  WARNING: ATR column not found, filling with NaN")
        if bid_vol_col is None or ask_vol_col is None:
            print("  WARNING: Bid/Ask Volume columns not found, filling with 0")

        for line in f:
            parts = line.split(",")
            if len(parts) < 6:
                continue
            date_str = parts[0].strip()
            time_str = parts[1].strip()
            o = float(parts[2])
            h = float(parts[3])
            l = float(parts[4])
            c = float(parts[5])

            tparts = time_str.split(":")
            hr = int(tparts[0])
            mn = int(tparts[1])
            sec = int(float(tparts[2])) if len(tparts) > 2 else 0
            tsec = hr * 3600 + mn * 60 + sec

            dparts = date_str.split("-")
            yr = int(dparts[0])
            mo = int(dparts[1])
            dy = int(dparts[2])
            dint = yr * 10000 + mo * 100 + dy

            last_arr[idx] = c
            high_arr[idx] = h
            low_arr[idx] = l
            open_arr[idx] = o
            time_sec_arr[idx] = tsec
            date_int_arr[idx] = dint
            dt_strings.append(f"{yr:04d}-{mo:02d}-{dy:02d} {hr:02d}:{mn:02d}:{sec:02d}")

            if atr_col is not None and atr_col < len(parts):
                try:
                    atr_arr[idx] = float(parts[atr_col])
                except ValueError:
                    atr_arr[idx] = np.nan
            else:
                atr_arr[idx] = np.nan

            if bid_vol_col is not None and bid_vol_col < len(parts):
                try:
                    bid_vol_arr[idx] = float(parts[bid_vol_col])
                except ValueError:
                    bid_vol_arr[idx] = 0.0
            else:
                bid_vol_arr[idx] = 0.0

            if ask_vol_col is not None and ask_vol_col < len(parts):
                try:
                    ask_vol_arr[idx] = float(parts[ask_vol_col])
                except ValueError:
                    ask_vol_arr[idx] = 0.0
            else:
                ask_vol_arr[idx] = 0.0

            idx += 1
            if idx % 5_000_000 == 0:
                print(f"    {idx / 1_000_000:.0f}M rows parsed...")

    if idx < n_rows:
        last_arr = last_arr[:idx]
        high_arr = high_arr[:idx]
        low_arr = low_arr[:idx]
        open_arr = open_arr[:idx]
        time_sec_arr = time_sec_arr[:idx]
        date_int_arr = date_int_arr[:idx]
        atr_arr = atr_arr[:idx]
        bid_vol_arr = bid_vol_arr[:idx]
        ask_vol_arr = ask_vol_arr[:idx]

    return {
        "last": last_arr, "high": high_arr, "low": low_arr, "open": open_arr,
        "time_sec": time_sec_arr, "date_int": date_int_arr,
        "datetime": dt_strings, "atr": atr_arr,
        "bid_vol": bid_vol_arr, "ask_vol": ask_vol_arr, "n": idx,
    }


# ---------------------------------------------------------------------------
#  250-tick bar aggregation
# ---------------------------------------------------------------------------

@nb.njit(cache=True)
def _aggregate_bars(last, high, low, open_arr, time_sec, date_int, atr,
                    bid_vol, ask_vol, n, bar_size):
    """Aggregate 1-tick bars into N-tick OHLC bars with continuous counting.

    Counts ticks continuously across date boundaries (no reset at midnight).
    This matches SC's 250-tick chart behavior, which counts within the full
    session (18:00-17:00 next day) without resetting at midnight.

    Returns aggregated arrays + mapping array (agg_bar_id per tick bar).
    agg_bar_id[i] = which aggregated bar tick i belongs to.
    """
    max_agg = (n // bar_size) + 1000
    a_open  = np.empty(max_agg, dtype=np.float64)
    a_high  = np.empty(max_agg, dtype=np.float64)
    a_low   = np.empty(max_agg, dtype=np.float64)
    a_close = np.empty(max_agg, dtype=np.float64)
    a_tsec  = np.empty(max_agg, dtype=np.int32)
    a_dint  = np.empty(max_agg, dtype=np.int32)
    a_atr   = np.empty(max_agg, dtype=np.float64)
    a_bid_vol = np.empty(max_agg, dtype=np.float64)
    a_ask_vol = np.empty(max_agg, dtype=np.float64)
    tick_to_agg = np.empty(n, dtype=np.int32)

    agg_idx = 0
    tick_count = 0

    for i in range(n):
        if tick_count == 0:
            # Start new agg bar
            a_open[agg_idx] = open_arr[i]
            a_high[agg_idx] = high[i]
            a_low[agg_idx] = low[i]
            a_close[agg_idx] = last[i]
            a_tsec[agg_idx] = time_sec[i]
            a_dint[agg_idx] = date_int[i]
            a_atr[agg_idx] = atr[i]
            a_bid_vol[agg_idx] = bid_vol[i]
            a_ask_vol[agg_idx] = ask_vol[i]
            tick_to_agg[i] = agg_idx
            tick_count = 1
        else:
            # Update current agg bar
            if high[i] > a_high[agg_idx]:
                a_high[agg_idx] = high[i]
            if low[i] < a_low[agg_idx]:
                a_low[agg_idx] = low[i]
            a_close[agg_idx] = last[i]
            a_atr[agg_idx] = atr[i]  # last ATR value in the agg bar
            a_bid_vol[agg_idx] += bid_vol[i]
            a_ask_vol[agg_idx] += ask_vol[i]
            tick_to_agg[i] = agg_idx
            tick_count += 1

            if tick_count >= bar_size:
                agg_idx += 1
                tick_count = 0

    n_agg = agg_idx + 1 if tick_count > 0 else agg_idx

    return (a_open[:n_agg], a_high[:n_agg], a_low[:n_agg], a_close[:n_agg],
            a_tsec[:n_agg], a_dint[:n_agg], a_atr[:n_agg],
            a_bid_vol[:n_agg], a_ask_vol[:n_agg], tick_to_agg, n_agg)


def aggregate_to_ntick(bars: dict, bar_size: int = 250) -> tuple[dict, np.ndarray]:
    """Aggregate 1-tick bars into N-tick OHLC bars.

    Returns:
        agg_bars: dict with same keys as bars but aggregated
        tick_to_agg: int32[n_ticks] mapping each tick bar to its agg bar index
    """
    # Provide bid_vol/ask_vol if available, else zeros
    bid_vol = bars.get("bid_vol", np.zeros(bars["n"], dtype=np.float32))
    ask_vol = bars.get("ask_vol", np.zeros(bars["n"], dtype=np.float32))
    result = _aggregate_bars(
        bars["last"], bars["high"], bars["low"], bars["open"],
        bars["time_sec"], bars["date_int"], bars["atr"],
        bid_vol, ask_vol, bars["n"], bar_size,
    )
    (a_open, a_high, a_low, a_close, a_tsec, a_dint, a_atr,
     a_bid_vol, a_ask_vol, tick_to_agg, n_agg) = result

    agg_bars = {
        "open": a_open, "high": a_high, "low": a_low, "last": a_close,
        "time_sec": a_tsec, "date_int": a_dint, "atr": a_atr,
        "bid_vol": a_bid_vol, "ask_vol": a_ask_vol, "n": n_agg,
    }
    return agg_bars, tick_to_agg


def map_signal_to_ticks(signal_agg: np.ndarray, tick_to_agg: np.ndarray) -> np.ndarray:
    """Forward-fill aggregated signal values to tick-level resolution."""
    return signal_agg[tick_to_agg]


# ---------------------------------------------------------------------------
#  Signal 1: ATR-relative scaling
# ---------------------------------------------------------------------------

def compute_atr_scale_signal(atr: np.ndarray, warmup_bars: int = 200) -> np.ndarray:
    """ATR ratio signal: atr[i] / median(atr[:warmup_bars]).

    Values > 1.0 = wider swings than baseline, < 1.0 = narrower.
    """
    n = len(atr)
    out = np.ones(n, dtype=np.float64)

    # Compute baseline from first warmup_bars valid ATR values
    valid = atr[:min(warmup_bars, n)]
    valid = valid[~np.isnan(valid)]
    if len(valid) == 0:
        return out
    baseline = np.median(valid)
    if baseline < 0.01:
        return out

    for i in range(n):
        if not np.isnan(atr[i]) and atr[i] > 0:
            out[i] = atr[i] / baseline
    return out


# ---------------------------------------------------------------------------
#  Signal 2: Range/displacement ratio
# ---------------------------------------------------------------------------

@nb.njit(cache=True)
def compute_range_displacement_signal(high, low, close, session_ids,
                                       lookback=200):
    """Rolling range/displacement ratio.

    High ratio = rotational (lots of range, little net movement).
    Low ratio = trending (range converts to displacement).
    """
    n = len(close)
    out = np.full(n, np.nan, dtype=np.float64)
    # Cumulative range for rolling sum
    bar_range = np.empty(n, dtype=np.float64)
    for i in range(n):
        bar_range[i] = high[i] - low[i]

    cum_range = np.empty(n + 1, dtype=np.float64)
    cum_range[0] = 0.0
    for i in range(n):
        cum_range[i + 1] = cum_range[i] + bar_range[i]

    for i in range(lookback, n):
        # Only compute within same session
        if session_ids[i] != session_ids[i - lookback]:
            # Find start of current session within lookback
            start = i
            for j in range(i - 1, max(i - lookback - 1, -1), -1):
                if session_ids[j] != session_ids[i]:
                    start = j + 1
                    break
            if i - start < 20:
                continue
            sum_range = cum_range[i + 1] - cum_range[start]
            net_disp = abs(close[i] - close[start])
        else:
            sum_range = cum_range[i + 1] - cum_range[i - lookback]
            net_disp = abs(close[i] - close[i - lookback])

        if net_disp < 0.01:
            out[i] = 100.0  # effectively infinite = very rotational
        else:
            out[i] = sum_range / net_disp
    return out


# ---------------------------------------------------------------------------
#  Signal 3: Rolling zigzag median swing size
# ---------------------------------------------------------------------------

@nb.njit(cache=True)
def _sorted_median(buf, count):
    """Median from a sorted buffer of length count."""
    if count == 0:
        return 0.0
    if count % 2 == 1:
        return buf[count // 2]
    return (buf[count // 2 - 1] + buf[count // 2]) / 2.0


@nb.njit(cache=True)
def _sorted_insert(buf, count, val, max_size):
    """Insert val into sorted buffer, evicting oldest if full.
    Returns new count. Buffer is maintained in sorted order."""
    if count < max_size:
        # Find insertion point
        pos = count
        for j in range(count):
            if val < buf[j]:
                pos = j
                break
        # Shift right
        for j in range(count, pos, -1):
            buf[j] = buf[j - 1]
        buf[pos] = val
        return count + 1
    return count


@nb.njit(cache=True)
def compute_rolling_zz_median(swing_idx, swing_price, n_bars, window=20):
    """Per-bar rolling median of last `window` zigzag swing sizes.

    Swing size = abs(price[k+1] - price[k]) for consecutive swings.
    Signal holds steady between swing points (forward-fill).
    """
    n_swings = len(swing_idx)
    out = np.full(n_bars, np.nan, dtype=np.float64)

    if n_swings < 2:
        return out

    # Precompute swing sizes
    n_sizes = n_swings - 1
    sizes = np.empty(n_sizes, dtype=np.float64)
    size_bar = np.empty(n_sizes, dtype=np.int64)  # bar at which size is confirmed
    for k in range(n_sizes):
        sizes[k] = abs(swing_price[k + 1] - swing_price[k])
        size_bar[k] = swing_idx[k + 1]  # confirmed when second swing point is set

    # Walk bars, maintain rolling window of recent swing sizes
    sorted_buf = np.empty(window, dtype=np.float64)
    # Use a FIFO queue for eviction: track insertion order
    fifo = np.empty(window, dtype=np.float64)
    fifo_head = 0
    buf_count = 0
    size_ptr = 0
    current_median = np.nan

    for i in range(n_bars):
        # Add any new swing sizes that completed at or before bar i
        while size_ptr < n_sizes and size_bar[size_ptr] <= i:
            val = sizes[size_ptr]
            if buf_count >= window:
                # Evict oldest (fifo_head)
                old_val = fifo[fifo_head % window]
                # Remove old_val from sorted buffer
                for j in range(buf_count):
                    if sorted_buf[j] == old_val:
                        for jj in range(j, buf_count - 1):
                            sorted_buf[jj] = sorted_buf[jj + 1]
                        buf_count -= 1
                        break
                fifo_head += 1

            # Insert new value into sorted buffer
            buf_count = _sorted_insert(sorted_buf, buf_count, val, window)
            fifo[size_ptr % window] = val
            size_ptr += 1

            if buf_count > 0:
                current_median = _sorted_median(sorted_buf, buf_count)

        out[i] = current_median
    return out


# ---------------------------------------------------------------------------
#  Signal 4: Multi-threshold zigzag — dominant scale + completion counts
# ---------------------------------------------------------------------------

@nb.njit(cache=True)
def _count_completions_rolling(swing_idx, swing_dir, n_bars, window=500):
    """Per-bar rolling completion count in a TIME-BASED window.

    Counts swings that completed within the last `window` AGG BARS (not last N
    swings). This makes completion density comparable across thresholds:
    - 10pt threshold might have 15 completions in 500 bars
    - 50pt threshold might have 2 completions in 500 bars
    The raw counts directly reflect completion density per unit time.

    Returns (total_count[n_bars], long_count[n_bars], short_count[n_bars]).
    """
    n_swings = len(swing_idx)
    total = np.zeros(n_bars, dtype=np.int32)
    longs = np.zeros(n_bars, dtype=np.int32)
    shorts = np.zeros(n_bars, dtype=np.int32)

    if n_swings == 0:
        return total, longs, shorts

    # Use FIFO of swing directions with bar indices
    fifo_bar = np.empty(n_swings, dtype=np.int64)
    fifo_dir = np.empty(n_swings, dtype=np.int8)
    fifo_head = 0
    fifo_tail = 0  # next write position
    sp = 0
    cur_total = 0
    cur_long = 0
    cur_short = 0

    for i in range(n_bars):
        # Add swings that completed at or before bar i
        while sp < n_swings and swing_idx[sp] <= i:
            fifo_bar[fifo_tail] = swing_idx[sp]
            fifo_dir[fifo_tail] = swing_dir[sp]
            fifo_tail += 1
            cur_total += 1
            if swing_dir[sp] == 1:
                cur_long += 1
            else:
                cur_short += 1
            sp += 1

        # Evict swings OLDER than window bars (time-based eviction)
        cutoff = i - window
        while fifo_head < fifo_tail and fifo_bar[fifo_head] < cutoff:
            d = fifo_dir[fifo_head]
            fifo_head += 1
            cur_total -= 1
            if d == 1:
                cur_long -= 1
            else:
                cur_short -= 1

        total[i] = cur_total
        longs[i] = cur_long
        shorts[i] = cur_short

    return total, longs, shorts


def compute_multi_threshold_signals(prices, session_ids, n_bars,
                                     thresholds=None, window=500):
    """Run zigzag at multiple thresholds, compute per-bar dominant scale.

    window: time-based, in agg bars (500 agg bars ≈ 125K ticks ≈ ~1 RTH session).

    Returns dict with:
        dominant_scale: float64[n_bars] — threshold with most completions
        confidence: float64[n_bars] — (winner - runner_up) / winner
        completion_counts: int32[n_bars, n_thresholds]
        long_counts: int32[n_bars, n_thresholds]
        short_counts: int32[n_bars, n_thresholds]
    """
    if thresholds is None:
        thresholds = [10.0, 15.0, 20.0, 25.0, 30.0, 50.0]
    n_thresh = len(thresholds)

    all_counts = np.zeros((n_bars, n_thresh), dtype=np.int32)
    all_longs  = np.zeros((n_bars, n_thresh), dtype=np.int32)
    all_shorts = np.zeros((n_bars, n_thresh), dtype=np.int32)

    for ti, thresh in enumerate(thresholds):
        s_idx, s_price, s_dir, s_sid = zigzag(prices, session_ids, thresh)
        total, lc, sc = _count_completions_rolling(s_idx, s_dir, n_bars, window)
        all_counts[:, ti] = total
        all_longs[:, ti] = lc
        all_shorts[:, ti] = sc

    # Normalize completion counts before picking dominant scale.
    # Raw counts always favor the smallest threshold (more swings by definition).
    # Normalize each threshold by its overall average count across all bars,
    # so the signal measures which scale is OVER-represented right now.
    avg_counts = np.zeros(n_thresh, dtype=np.float64)
    for ti in range(n_thresh):
        valid = all_counts[:, ti]
        nonzero = valid[valid > 0]
        if len(nonzero) > 0:
            avg_counts[ti] = np.mean(nonzero)
        else:
            avg_counts[ti] = 1.0  # avoid div by zero

    # Compute dominant scale and confidence per bar using normalized counts
    dominant = np.full(n_bars, np.nan, dtype=np.float64)
    confidence = np.zeros(n_bars, dtype=np.float64)
    norm_counts = np.zeros((n_bars, n_thresh), dtype=np.float64)

    for i in range(n_bars):
        best_norm = 0.0
        best_thresh = 0.0
        second_norm = 0.0
        any_active = False
        for ti in range(n_thresh):
            c = all_counts[i, ti]
            if c > 0:
                any_active = True
                nc = c / avg_counts[ti]  # normalized: 1.0 = average activity
                norm_counts[i, ti] = nc
                if nc > best_norm:
                    second_norm = best_norm
                    best_norm = nc
                    best_thresh = thresholds[ti]
                elif nc > second_norm:
                    second_norm = nc
        if any_active and best_norm > 0:
            dominant[i] = best_thresh
            confidence[i] = (best_norm - second_norm) / best_norm

    return {
        "dominant_scale": dominant,
        "confidence": confidence,
        "completion_counts": all_counts,
        "norm_counts": norm_counts,
        "long_counts": all_longs,
        "short_counts": all_shorts,
        "thresholds": thresholds,
    }


# ---------------------------------------------------------------------------
#  Signal 7: Retracement health (from fractal Fact 2)
# ---------------------------------------------------------------------------

@nb.njit(cache=True)
def _child_walk_rolling(c_prices, c_dirs, c_sids, parent_thresh, n_agg_bars,
                        swing_idx, window=20):
    """Rolling child-walk completion with retracement tracking.

    For each parent-scale attempt, records success/failure and retracement count.
    Returns per-agg-bar rolling average retracement count and completion rate.

    Uses the child-walk method (walks child swings, tracks displacement toward
    parent threshold) — captures both successes AND failures.
    """
    n_swings = len(c_prices)
    # Per-attempt results
    max_attempts = n_swings // 2 + 1
    att_bar = np.empty(max_attempts, dtype=np.int64)   # agg bar of resolution
    att_succ = np.empty(max_attempts, dtype=nb.boolean)
    att_ret = np.empty(max_attempts, dtype=np.int32)
    att_cnt = 0

    # Walk child swings
    i = 0
    while i < n_swings - 1:
        cs = c_sids[i]
        anch_p = c_prices[i]
        i += 1
        if c_sids[i] != cs:
            continue
        disp = c_prices[i] - anch_p
        if disp == 0.0:
            continue

        att_dir = np.int8(1) if disp > 0 else np.int8(-1)
        n_ret = np.int32(0)
        prev_p = c_prices[i]

        # Immediate resolution
        if abs(disp) >= parent_thresh:
            att_bar[att_cnt] = swing_idx[i]
            att_succ[att_cnt] = True
            att_ret[att_cnt] = 0
            att_cnt += 1
            continue

        resolved = False
        while True:
            i += 1
            if i >= n_swings or c_sids[i] != cs:
                break
            prev_p = c_prices[i]
            disp = c_prices[i] - anch_p
            fav = disp * att_dir
            if c_dirs[i] != att_dir:
                n_ret += 1
            if fav >= parent_thresh:
                att_bar[att_cnt] = swing_idx[i]
                att_succ[att_cnt] = True
                att_ret[att_cnt] = n_ret
                att_cnt += 1
                resolved = True
                break
            elif fav <= -parent_thresh:
                att_bar[att_cnt] = swing_idx[i]
                att_succ[att_cnt] = False
                att_ret[att_cnt] = n_ret
                att_cnt += 1
                resolved = True
                break

    # Now build per-agg-bar rolling signals
    avg_retrace = np.full(n_agg_bars, np.nan, dtype=np.float64)
    completion_rate = np.full(n_agg_bars, np.nan, dtype=np.float64)

    # FIFO window over attempts
    fifo_succ = np.empty(window, dtype=nb.boolean)
    fifo_ret = np.empty(window, dtype=np.int32)
    fifo_head = 0
    fifo_count = 0
    att_ptr = 0

    for bi in range(n_agg_bars):
        # Add attempts that resolved at or before this agg bar
        while att_ptr < att_cnt and att_bar[att_ptr] <= bi:
            if fifo_count < window:
                fifo_succ[fifo_count] = att_succ[att_ptr]
                fifo_ret[fifo_count] = att_ret[att_ptr]
                fifo_count += 1
            else:
                # Overwrite oldest
                idx = fifo_head % window
                fifo_succ[idx] = att_succ[att_ptr]
                fifo_ret[idx] = att_ret[att_ptr]
                fifo_head += 1
            att_ptr += 1

        if fifo_count > 0:
            # Compute rolling stats
            total_ret = 0
            total_succ = 0
            n_items = min(fifo_count, window)
            start = fifo_head if fifo_count >= window else 0
            for j in range(n_items):
                idx = (start + j) % window
                total_ret += fifo_ret[idx]
                if fifo_succ[idx]:
                    total_succ += 1
            avg_retrace[bi] = total_ret / n_items
            completion_rate[bi] = total_succ / n_items

    return avg_retrace, completion_rate


def compute_retracement_health(prices, session_ids, n_bars,
                                thresholds=None, child_threshold=5.0,
                                window=20):
    """Per-bar retracement health at each threshold scale.

    For each threshold (treated as parent), runs child-walk at child_threshold.
    Returns rolling avg retracement count and completion rate per threshold.

    Healthy rotation: low avg retracement (0-1), high completion rate (>70%).
    Degrading: avg retracement climbing (2+), completion rate dropping.
    """
    if thresholds is None:
        thresholds = [10.0, 15.0, 20.0, 25.0, 30.0, 50.0]
    n_thresh = len(thresholds)

    # Run child zigzag once
    c_idx, c_price, c_dir, c_sid = zigzag(prices, session_ids, child_threshold)

    avg_retrace_all = np.full((n_bars, n_thresh), np.nan, dtype=np.float64)
    comp_rate_all = np.full((n_bars, n_thresh), np.nan, dtype=np.float64)

    for ti, parent_thresh in enumerate(thresholds):
        if parent_thresh <= child_threshold:
            continue  # parent must be larger than child
        avg_ret, comp_rate = _child_walk_rolling(
            c_price, c_dir, c_sid, parent_thresh, n_bars,
            c_idx, window,
        )
        avg_retrace_all[:, ti] = avg_ret
        comp_rate_all[:, ti] = comp_rate

    return {
        "avg_retrace": avg_retrace_all,       # float64[n_bars, n_thresh]
        "completion_rate": comp_rate_all,       # float64[n_bars, n_thresh]
        "thresholds": thresholds,
    }


# ---------------------------------------------------------------------------
#  Signal 6: Completion asymmetry
# ---------------------------------------------------------------------------

def compute_asymmetry(long_counts, short_counts, n_bars):
    """Per-bar asymmetry ratio from multi-threshold signals.

    asymmetry = (long - short) / (long + short), range [-1, 1].
    Computed as sum across all thresholds.
    """
    out = np.zeros(n_bars, dtype=np.float64)
    for i in range(n_bars):
        total_long = 0
        total_short = 0
        for ti in range(long_counts.shape[1]):
            total_long += long_counts[i, ti]
            total_short += short_counts[i, ti]
        total = total_long + total_short
        if total > 0:
            out[i] = (total_long - total_short) / total
    return out


# ---------------------------------------------------------------------------
#  Signal 7: Chop-vs-Trend regime classifier
# ---------------------------------------------------------------------------

@nb.njit(cache=True)
def _linreg_slope_r2(close, n, lookback):
    """Rolling linear regression slope and R² on close prices.

    slope: points-per-bar trend direction/speed.
    r2: goodness of fit — high = clean trend, low = noisy/choppy.
    """
    slope_out = np.full(n, np.nan, dtype=np.float64)
    r2_out = np.full(n, np.nan, dtype=np.float64)

    # Precompute x values [0, 1, ..., lookback-1]
    x_sum = 0.0
    x2_sum = 0.0
    for j in range(lookback):
        x_sum += j
        x2_sum += j * j
    x_mean = x_sum / lookback

    for i in range(lookback - 1, n):
        y_sum = 0.0
        xy_sum = 0.0
        for j in range(lookback):
            y = close[i - lookback + 1 + j]
            y_sum += y
            xy_sum += j * y
        y_mean = y_sum / lookback

        denom = x2_sum - lookback * x_mean * x_mean
        if abs(denom) < 1e-12:
            continue
        slope = (xy_sum - lookback * x_mean * y_mean) / denom
        slope_out[i] = slope

        # R² = 1 - SS_res / SS_tot
        ss_tot = 0.0
        ss_res = 0.0
        for j in range(lookback):
            y = close[i - lookback + 1 + j]
            y_hat = y_mean + slope * (j - x_mean)
            ss_tot += (y - y_mean) ** 2
            ss_res += (y - y_hat) ** 2
        if ss_tot > 1e-12:
            r2_out[i] = 1.0 - ss_res / ss_tot
        else:
            r2_out[i] = 0.0

    return slope_out, r2_out


@nb.njit(cache=True)
def _choppiness_ratio(high, low, close, n, lookback):
    """Choppiness ratio: |net_move| / summed_range over lookback bars.

    Near 0 = chop (lots of movement, no progress).
    Near 1 = trend (movement converts to displacement).
    """
    out = np.full(n, np.nan, dtype=np.float64)
    for i in range(lookback - 1, n):
        net_move = abs(close[i] - close[i - lookback + 1])
        summed_range = 0.0
        for j in range(lookback):
            idx = i - lookback + 1 + j
            summed_range += high[idx] - low[idx]
        if summed_range > 1e-12:
            out[i] = net_move / summed_range
        else:
            out[i] = 0.0
    return out


@nb.njit(cache=True)
def _signed_vol_ratio(bid_vol, ask_vol, n, lookback):
    """Rolling signed volume imbalance: sum(ask - bid) / sum(ask + bid).

    Range [-1, 1]. Sustained one-sided pressure = trend; flipping = chop.
    We return the absolute value — high = directional pressure, low = balanced.
    """
    out = np.full(n, np.nan, dtype=np.float64)
    for i in range(lookback - 1, n):
        delta_sum = 0.0
        total_sum = 0.0
        for j in range(lookback):
            idx = i - lookback + 1 + j
            delta_sum += ask_vol[idx] - bid_vol[idx]
            total_sum += ask_vol[idx] + bid_vol[idx]
        if total_sum > 1e-12:
            out[i] = abs(delta_sum) / total_sum
        else:
            out[i] = 0.0
    return out


@nb.njit(cache=True)
def _signed_price_volume(close, volume, n, lookback):
    """Rolling signed price volume: sum(sign(close_change) * volume) / sum(volume).

    Close-to-close proxy for directional pressure when bid/ask delta is weak.
    Range [-1, 1]. Positive = buying pressure, negative = selling.
    Returns absolute value.
    """
    out = np.full(n, np.nan, dtype=np.float64)
    for i in range(lookback, n):
        signed_sum = 0.0
        total_sum = 0.0
        for j in range(lookback):
            idx = i - lookback + 1 + j
            if idx < 1:
                continue
            diff = close[idx] - close[idx - 1]
            v = volume[idx]
            if diff > 0:
                signed_sum += v
            elif diff < 0:
                signed_sum -= v
            total_sum += v
        if total_sum > 1e-12:
            out[i] = abs(signed_sum) / total_sum
        else:
            out[i] = 0.0
    return out


@nb.njit(cache=True)
def _cumulative_delta(bid_vol, ask_vol, n, lookback):
    """Rolling cumulative delta: sum(ask_vol - bid_vol) over lookback.

    Raw magnitude of directional volume pressure (not normalized).
    Returns absolute value.
    """
    out = np.full(n, np.nan, dtype=np.float64)
    for i in range(lookback - 1, n):
        delta_sum = 0.0
        for j in range(lookback):
            idx = i - lookback + 1 + j
            delta_sum += ask_vol[idx] - bid_vol[idx]
        out[i] = abs(delta_sum)
    return out


@nb.njit(cache=True)
def _bar_duration(time_sec, date_int, n):
    """Duration of each bar in seconds (time between consecutive bars).

    Resets at session boundaries (date change). Fast bars = high activity.
    """
    out = np.full(n, np.nan, dtype=np.float64)
    for i in range(1, n):
        if date_int[i] != date_int[i - 1]:
            continue  # session boundary
        dur = time_sec[i] - time_sec[i - 1]
        if dur >= 0:
            out[i] = float(dur)
    return out


@nb.njit(cache=True)
def _rolling_skew(close, n, lookback):
    """Rolling skewness of log returns over lookback bars."""
    out = np.full(n, np.nan, dtype=np.float64)
    for i in range(lookback, n):
        # Compute log returns in window
        rets = np.empty(lookback, dtype=np.float64)
        for j in range(lookback):
            idx = i - lookback + 1 + j
            if close[idx - 1] > 0 and close[idx] > 0:
                rets[j] = np.log(close[idx] / close[idx - 1])
            else:
                rets[j] = 0.0
        # Mean, std
        mean = 0.0
        for j in range(lookback):
            mean += rets[j]
        mean /= lookback
        var = 0.0
        for j in range(lookback):
            var += (rets[j] - mean) ** 2
        var /= lookback
        std = np.sqrt(var)
        if std < 1e-12:
            out[i] = 0.0
            continue
        # Skewness
        skew = 0.0
        for j in range(lookback):
            skew += ((rets[j] - mean) / std) ** 3
        out[i] = skew / lookback
    return out


@nb.njit(cache=True)
def _rolling_kurtosis(close, n, lookback):
    """Rolling excess kurtosis of log returns over lookback bars."""
    out = np.full(n, np.nan, dtype=np.float64)
    for i in range(lookback, n):
        rets = np.empty(lookback, dtype=np.float64)
        for j in range(lookback):
            idx = i - lookback + 1 + j
            if close[idx - 1] > 0 and close[idx] > 0:
                rets[j] = np.log(close[idx] / close[idx - 1])
            else:
                rets[j] = 0.0
        mean = 0.0
        for j in range(lookback):
            mean += rets[j]
        mean /= lookback
        var = 0.0
        for j in range(lookback):
            var += (rets[j] - mean) ** 2
        var /= lookback
        std = np.sqrt(var)
        if std < 1e-12:
            out[i] = 0.0
            continue
        kurt = 0.0
        for j in range(lookback):
            kurt += ((rets[j] - mean) / std) ** 4
        out[i] = kurt / lookback - 3.0  # excess kurtosis
    return out


@nb.njit(cache=True)
def _rolling_entropy(close, n, lookback, n_bins=10):
    """Rolling Shannon entropy of log returns over lookback bars.

    Discretizes returns into n_bins bins, computes entropy.
    High entropy = unpredictable. Low entropy = concentrated/patterned.
    """
    out = np.full(n, np.nan, dtype=np.float64)
    for i in range(lookback, n):
        rets = np.empty(lookback, dtype=np.float64)
        for j in range(lookback):
            idx = i - lookback + 1 + j
            if close[idx - 1] > 0 and close[idx] > 0:
                rets[j] = np.log(close[idx] / close[idx - 1])
            else:
                rets[j] = 0.0
        # Find min/max for binning
        rmin = rets[0]; rmax = rets[0]
        for j in range(1, lookback):
            if rets[j] < rmin: rmin = rets[j]
            if rets[j] > rmax: rmax = rets[j]
        rng = rmax - rmin
        if rng < 1e-12:
            out[i] = 0.0
            continue
        # Bin counts
        counts = np.zeros(n_bins, dtype=np.int32)
        for j in range(lookback):
            b = int((rets[j] - rmin) / rng * (n_bins - 1))
            if b >= n_bins: b = n_bins - 1
            if b < 0: b = 0
            counts[b] += 1
        # Shannon entropy
        ent = 0.0
        for b in range(n_bins):
            if counts[b] > 0:
                p = counts[b] / lookback
                ent -= p * np.log(p)
        # Normalize by max entropy (log(n_bins))
        max_ent = np.log(float(n_bins))
        out[i] = ent / max_ent if max_ent > 0 else 0.0
    return out


# ---------------------------------------------------------------------------
#  Entry & in-trade regime signals (Track A + B shared)
# ---------------------------------------------------------------------------

def compute_entry_signals(agg_bars, lookback=3):
    """Compute directional + rate-of-change regime features on aggregated bars.

    Returns dict of float64 arrays (one per agg bar):
        signed_chop  — displacement/range with sign preserved [-1, 1]
        dchop        — chop[i] - chop[i-1] (regime transition rate)
        d2chop       — dchop[i] - dchop[i-1] (transition acceleration)
        signed_slope — raw regression slope (points/bar, signed)
        dr2          — r2[i] - r2[i-1]
        dslope       — abs_slope[i] - abs_slope[i-1]
    """
    close = agg_bars["last"].astype(np.float64)
    high = agg_bars["high"].astype(np.float64)
    low = agg_bars["low"].astype(np.float64)
    n = agg_bars["n"]

    # Reuse existing functions for base signals
    chop = _choppiness_ratio(high, low, close, n, lookback)  # unsigned
    slope, r2 = _linreg_slope_r2(close, n, lookback)

    # Signed choppiness: same as chop but preserves direction
    signed_chop = _signed_choppiness(high, low, close, n, lookback)

    # Rate-of-change signals
    dchop = np.full(n, np.nan, dtype=np.float64)
    d2chop = np.full(n, np.nan, dtype=np.float64)
    dr2 = np.full(n, np.nan, dtype=np.float64)
    dslope = np.full(n, np.nan, dtype=np.float64)

    for i in range(1, n):
        if not np.isnan(chop[i]) and not np.isnan(chop[i - 1]):
            dchop[i] = chop[i] - chop[i - 1]
        if not np.isnan(r2[i]) and not np.isnan(r2[i - 1]):
            dr2[i] = r2[i] - r2[i - 1]
        if not np.isnan(slope[i]) and not np.isnan(slope[i - 1]):
            dslope[i] = abs(slope[i]) - abs(slope[i - 1])
    for i in range(2, n):
        if not np.isnan(dchop[i]) and not np.isnan(dchop[i - 1]):
            d2chop[i] = dchop[i] - dchop[i - 1]

    return {
        "signed_chop": signed_chop,
        "dchop": dchop,
        "d2chop": d2chop,
        "signed_slope": slope,   # slope is already signed
        "dr2": dr2,
        "dslope": dslope,
        "choppiness": chop,      # include unsigned for filter
        "slope_abs": np.where(np.isnan(slope), np.nan, np.abs(slope)),
        "r2": r2,
    }


@nb.njit(cache=True)
def _signed_choppiness(high, low, close, n, lookback):
    """Signed choppiness: (close[i] - close[i-lb+1]) / summed_range.

    Range [-1, 1]. Positive = upward displacement, negative = downward.
    """
    out = np.full(n, np.nan, dtype=np.float64)
    for i in range(lookback - 1, n):
        net_move = close[i] - close[i - lookback + 1]
        summed_range = 0.0
        for j in range(lookback):
            idx = i - lookback + 1 + j
            summed_range += high[idx] - low[idx]
        if summed_range > 1e-12:
            out[i] = net_move / summed_range
        else:
            out[i] = 0.0
    return out


def compute_regime_signals(agg_bars, lookback=5):
    """Compute all regime classifier features on aggregated bars.

    Returns dict of float64 arrays (one per agg bar):
        slope, r2, choppiness, vol_imbalance, signed_price_vol,
        cum_delta, bar_duration, skew, kurtosis, entropy, regime_label
    regime_label: 0=unclear, 1=chop, 2=trend
    """
    close = agg_bars["last"].astype(np.float64)
    high = agg_bars["high"].astype(np.float64)
    low = agg_bars["low"].astype(np.float64)
    bid_vol = agg_bars.get("bid_vol", np.zeros_like(close))
    ask_vol = agg_bars.get("ask_vol", np.zeros_like(close))
    volume = agg_bars.get("volume", bid_vol + ask_vol)
    if not isinstance(volume, np.ndarray):
        volume = np.zeros_like(close)
    time_sec = agg_bars.get("time_sec", np.zeros(len(close), dtype=np.int32))
    date_int = agg_bars.get("date_int", np.zeros(len(close), dtype=np.int32))
    n = agg_bars["n"]

    slope, r2 = _linreg_slope_r2(close, n, lookback)
    choppiness = _choppiness_ratio(high, low, close, n, lookback)
    vol_imb = _signed_vol_ratio(bid_vol, ask_vol, n, lookback)
    signed_pv = _signed_price_volume(close, volume, n, lookback)
    cum_delta = _cumulative_delta(bid_vol, ask_vol, n, lookback)
    bar_dur = _bar_duration(time_sec, date_int, n)
    skew = _rolling_skew(close, n, lookback)
    kurtosis = _rolling_kurtosis(close, n, lookback)
    entropy = _rolling_entropy(close, n, lookback)

    # Composite regime label
    # Chop: low R² AND low choppiness ratio AND low vol imbalance
    # Trend: high R² AND high choppiness ratio
    # Unclear: everything else
    regime = np.zeros(n, dtype=np.int32)  # 0 = unclear
    for i in range(n):
        if np.isnan(r2[i]) or np.isnan(choppiness[i]):
            continue
        r2_v = r2[i]
        chop_v = choppiness[i]
        vi = vol_imb[i] if not np.isnan(vol_imb[i]) else 0.0

        # Trend: clean directional move
        if r2_v >= 0.6 and chop_v >= 0.4:
            regime[i] = 2  # trend
        # Chop: noisy, mean-reverting
        elif r2_v < 0.3 and chop_v < 0.25:
            regime[i] = 1  # chop
        # Use vol imbalance as tiebreaker
        elif r2_v < 0.4 and vi < 0.15:
            regime[i] = 1  # chop (balanced volume confirms rotation)
        elif r2_v >= 0.5 and vi >= 0.2:
            regime[i] = 2  # trend (directional volume confirms trend)
        # else: 0 = unclear

    return {
        "slope": slope,
        "r2": r2,
        "choppiness": choppiness,
        "vol_imbalance": vol_imb,
        "signed_price_vol": signed_pv,
        "cum_delta": cum_delta,
        "bar_duration": bar_dur,
        "skew": skew,
        "kurtosis": kurtosis,
        "entropy": entropy,
        "regime": regime,
    }


REGIME_UNCLEAR = 0
REGIME_CHOP = 1
REGIME_TREND = 2


# ---------------------------------------------------------------------------
#  Master precompute
# ---------------------------------------------------------------------------

def precompute_all_signals(bars: dict, bar_size: int = 250,
                            zz_threshold: float = 5.0,
                            zz_window: int = 20,
                            range_lookback: int = 200,
                            atr_warmup: int = 200,
                            mt_thresholds: list | None = None,
                            mt_window: int = 500) -> dict:
    """Precompute all signal arrays.

    Signals are computed on 250-tick aggregated bars, then mapped back
    to 1-tick resolution via forward-fill.

    Returns dict with per-tick signal arrays + metadata.
    """
    n_ticks = bars["n"]
    t0 = time.time()

    # --- Step 1: Aggregate to N-tick bars ---
    print(f"  Aggregating to {bar_size}-tick bars...")
    agg_bars, tick_to_agg = aggregate_to_ntick(bars, bar_size)
    n_agg = agg_bars["n"]
    print(f"    {n_ticks} ticks -> {n_agg} agg bars ({time.time()-t0:.1f}s)")

    # --- Step 2: Session IDs for aggregated bars ---
    agg_session_ids = assign_session_ids(agg_bars["date_int"])

    # --- Step 3: Compute signals on aggregated bars ---

    # Signal 1: ATR ratio
    print(f"  Computing ATR ratio signal...")
    t1 = time.time()
    atr_ratio_agg = compute_atr_scale_signal(agg_bars["atr"], atr_warmup)
    print(f"    done ({time.time()-t1:.1f}s)")

    # Signal 2: Range/displacement
    print(f"  Computing range/displacement signal...")
    t1 = time.time()
    range_disp_agg = compute_range_displacement_signal(
        agg_bars["high"], agg_bars["low"], agg_bars["last"],
        agg_session_ids, range_lookback,
    )
    print(f"    done ({time.time()-t1:.1f}s)")

    # Signal 3: Rolling zigzag median (at zz_threshold)
    print(f"  Computing rolling zigzag median (thresh={zz_threshold})...")
    t1 = time.time()
    # Use Close prices for zigzag on agg bars
    agg_prices = agg_bars["last"].astype(np.float64)
    zz_idx, zz_price, zz_dir, zz_sid = zigzag(agg_prices, agg_session_ids,
                                                 zz_threshold)
    zz_median_agg = compute_rolling_zz_median(zz_idx, zz_price, n_agg, zz_window)
    print(f"    {len(zz_idx)} swings detected, done ({time.time()-t1:.1f}s)")

    # Signal 4: Multi-threshold zigzag
    if mt_thresholds is None:
        mt_thresholds = [10.0, 15.0, 20.0, 25.0, 30.0, 50.0]
    print(f"  Computing multi-threshold zigzag ({len(mt_thresholds)} thresholds)...")
    t1 = time.time()
    mt_signals = compute_multi_threshold_signals(
        agg_prices, agg_session_ids, n_agg, mt_thresholds, mt_window,
    )
    print(f"    done ({time.time()-t1:.1f}s)")

    # Signal 6: Asymmetry
    print(f"  Computing completion asymmetry...")
    t1 = time.time()
    asymmetry_agg = compute_asymmetry(
        mt_signals["long_counts"], mt_signals["short_counts"], n_agg,
    )
    print(f"    done ({time.time()-t1:.1f}s)")

    # --- Step 4: Map signals to tick resolution ---
    print(f"  Mapping signals to tick resolution...")
    t1 = time.time()
    result = {
        "atr_ratio": map_signal_to_ticks(atr_ratio_agg, tick_to_agg),
        "range_disp": map_signal_to_ticks(range_disp_agg, tick_to_agg),
        "zz_median": map_signal_to_ticks(zz_median_agg, tick_to_agg),
        "dominant_scale": map_signal_to_ticks(mt_signals["dominant_scale"], tick_to_agg),
        "confidence": map_signal_to_ticks(mt_signals["confidence"], tick_to_agg),
        "asymmetry": map_signal_to_ticks(asymmetry_agg, tick_to_agg),
        # Per-threshold completion counts (tick-mapped)
        "completion_counts": np.zeros((n_ticks, len(mt_thresholds)), dtype=np.int32),
        "long_counts": np.zeros((n_ticks, len(mt_thresholds)), dtype=np.int32),
        "short_counts": np.zeros((n_ticks, len(mt_thresholds)), dtype=np.int32),
        # Metadata
        "mt_thresholds": mt_thresholds,
        "bar_size": bar_size,
        "n_agg_bars": n_agg,
        "tick_to_agg": tick_to_agg,
    }
    # Map per-threshold counts
    for ti in range(len(mt_thresholds)):
        result["completion_counts"][:, ti] = map_signal_to_ticks(
            mt_signals["completion_counts"][:, ti], tick_to_agg)
        result["long_counts"][:, ti] = map_signal_to_ticks(
            mt_signals["long_counts"][:, ti], tick_to_agg)
        result["short_counts"][:, ti] = map_signal_to_ticks(
            mt_signals["short_counts"][:, ti], tick_to_agg)

    # --- Warmup masks (per-filter) ---
    # ATR: valid after warmup_bars agg bars
    atr_warmup_tick = 0
    for i in range(n_ticks):
        if tick_to_agg[i] >= atr_warmup:
            atr_warmup_tick = i
            break
    # ZZ median: valid once we have zz_window swings
    zz_warmup_tick = 0
    if len(zz_idx) >= zz_window:
        # Bar index of the zz_window-th swing
        warmup_agg_bar = int(zz_idx[zz_window - 1])
        for i in range(n_ticks):
            if tick_to_agg[i] >= warmup_agg_bar:
                zz_warmup_tick = i
                break
    # Range/disp: valid after lookback agg bars
    rd_warmup_tick = 0
    for i in range(n_ticks):
        if tick_to_agg[i] >= range_lookback:
            rd_warmup_tick = i
            break
    # Multi-threshold: valid once dominant_scale is non-NaN
    mt_warmup_tick = 0
    for i in range(n_ticks):
        if not np.isnan(result["dominant_scale"][i]):
            mt_warmup_tick = i
            break

    result["warmup_ticks"] = {
        "atr": atr_warmup_tick,
        "range_disp": rd_warmup_tick,
        "zz_median": zz_warmup_tick,
        "dominant_scale": mt_warmup_tick,
        "asymmetry": mt_warmup_tick,
    }

    total = time.time() - t0
    print(f"  Signal precompute complete: {total:.1f}s total")
    return result


# ---------------------------------------------------------------------------
#  CLI for standalone validation
# ---------------------------------------------------------------------------
#  Audit log helper
# ---------------------------------------------------------------------------

AUDIT_LOG = Path(r"C:\Projects\futures_pipeline\audit\audit_log.md")
JOURNAL_LOG = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-journal-scale-detection.md")


def append_audit(event: str, detail: str, archetype: str = "rotational",
                 instrument: str = "NQ"):
    """Append a row to the audit log (ICM convention: append-only)."""
    from datetime import datetime
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    row = f"| {ts} | {archetype} | {instrument} | {event} | {detail} |\n"
    with open(AUDIT_LOG, "a") as f:
        f.write(row)


def append_journal(entry: str):
    """Append a timestamped entry to the scale detection journal."""
    from datetime import datetime
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(JOURNAL_LOG, "a") as f:
        f.write(f"\n### {ts}\n\n{entry}\n")


# ---------------------------------------------------------------------------
#  CLI for standalone validation
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="MTZZ Signal Precomputation")
    parser.add_argument("--bar-file", type=str,
                        default=r"C:\Projects\futures_pipeline\data\NQ-1tick-calibration.csv")
    parser.add_argument("--bar-size", type=int, default=250)
    parser.add_argument("--max-rows", type=int, default=0,
                        help="Limit rows for testing (0=all)")
    args = parser.parse_args()

    print(f"Loading bars from {args.bar_file}...")
    bars = load_bars_extended(args.bar_file)
    if args.max_rows > 0 and bars["n"] > args.max_rows:
        print(f"  Truncating to {args.max_rows} rows for testing")
        for k in ["last", "high", "low", "open", "time_sec", "date_int", "atr"]:
            bars[k] = bars[k][:args.max_rows]
        bars["datetime"] = bars["datetime"][:args.max_rows]
        bars["n"] = args.max_rows

    signals = precompute_all_signals(bars, bar_size=args.bar_size)

    # Print summary
    print(f"\n--- Signal Summary ---")
    print(f"Ticks: {bars['n']:,}")
    print(f"Agg bars ({args.bar_size}-tick): {signals['n_agg_bars']:,}")
    print(f"ATR ratio: min={np.nanmin(signals['atr_ratio']):.3f}, "
          f"max={np.nanmax(signals['atr_ratio']):.3f}, "
          f"median={np.nanmedian(signals['atr_ratio']):.3f}")
    print(f"Range/disp: min={np.nanmin(signals['range_disp']):.2f}, "
          f"max={np.nanmax(signals['range_disp']):.2f}, "
          f"median={np.nanmedian(signals['range_disp']):.2f}")
    zz_valid = signals['zz_median'][~np.isnan(signals['zz_median'])]
    if len(zz_valid) > 0:
        print(f"ZZ median: min={zz_valid.min():.1f}, max={zz_valid.max():.1f}, "
              f"median={np.median(zz_valid):.1f}")
    dom_valid = signals['dominant_scale'][~np.isnan(signals['dominant_scale'])]
    if len(dom_valid) > 0:
        unique, counts = np.unique(dom_valid, return_counts=True)
        print(f"Dominant scale distribution:")
        for u, c in zip(unique, counts):
            print(f"  {u:.0f}pt: {c:,} bars ({c/len(dom_valid)*100:.1f}%)")
    print(f"Asymmetry: min={signals['asymmetry'].min():.3f}, "
          f"max={signals['asymmetry'].max():.3f}")
    print(f"\nWarmup ticks: {signals['warmup_ticks']}")
