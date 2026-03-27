# archetype: rotational
"""
zigzag.py — Reusable zig-zag engine with session handling for NQ fractal analysis.

Ported from fractal_01_prepare.py and fractal_02_analyze.py (validated Sept 2025 - Mar 2026).
All numba-accelerated functions preserve exact logic from original discovery analysis.
"""
import numpy as np
import numba as nb
import pandas as pd
from pathlib import Path

# === SESSION CONSTANTS ===
RTH_START = 9 * 3600 + 30 * 60    # 09:30 = 34200s
RTH_END   = 16 * 3600 + 15 * 60   # 16:15 = 58500s
ETH_EVENING = 18 * 3600           # 18:00 = 64800s

THRESHOLDS = [3, 5, 7, 10, 15, 25, 50]
PARENT_CHILD = [(50, 25), (25, 15), (25, 10), (15, 7), (10, 5), (7, 3)]
SPLITS = ['RTH', 'ETH', 'Combined']

BLOCK_LABELS_30 = [
    '09:30', '10:00', '10:30', '11:00', '11:30', '12:00', '12:30',
    '13:00', '13:30', '14:00', '14:30', '15:00', '15:30',
]
BLOCK_LABELS_60 = ['09:30', '10:30', '11:30', '12:30', '13:30', '14:30', '15:30']


# === NUMBA CORE FUNCTIONS ===
# These are EXACT ports from the validated fractal_01_prepare.py / fractal_02_analyze.py.
# Do NOT modify the logic — only the packaging has changed.

@nb.njit(cache=True)
def next_day(yyyymmdd):
    """Next calendar day from YYYYMMDD integer."""
    y = yyyymmdd // 10000
    m = (yyyymmdd // 100) % 100
    d = yyyymmdd % 100
    days_in = np.array([0, 31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31])
    dim = days_in[m]
    if m == 2 and (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)):
        dim = 29
    d += 1
    if d > dim:
        d = 1
        m += 1
        if m > 12:
            m = 1
            y += 1
    return y * 10000 + m * 100 + d


@nb.njit(cache=True)
def compute_trading_dates(cal_dates, time_secs):
    """Evening (>=18:00) belongs to next day's trading session."""
    n = len(cal_dates)
    td = np.empty(n, dtype=np.int32)
    for i in range(n):
        if time_secs[i] >= 64800.0:
            td[i] = next_day(cal_dates[i])
        else:
            td[i] = cal_dates[i]
    return td


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


@nb.njit(cache=True)
def zigzag_core(prices, session_ids, threshold):
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


@nb.njit(cache=True)
def child_walk_completion(c_prices, c_dirs, c_sids, c_time_secs, parent_thresh):
    """Walk child swings tracking displacement from anchor.
    Uses the child-walk method (NOT parent zig-zag overlay) to capture
    both successes AND failures.

    Returns per-attempt arrays:
      is_success, retrace_count, max_fav_frac, anchor_idx, gross_movement, anchor_ts
    """
    n = len(c_prices)
    mx = n // 2 + 1
    o_succ  = np.empty(mx, dtype=nb.boolean)
    o_ret   = np.empty(mx, dtype=np.int32)
    o_fav   = np.empty(mx, dtype=np.float64)
    o_anch  = np.empty(mx, dtype=np.int64)
    o_gross = np.empty(mx, dtype=np.float64)
    o_ts    = np.empty(mx, dtype=np.float32)
    cnt = 0

    i = 0
    while i < n - 1:
        cs = c_sids[i]
        anch_p = c_prices[i]
        anch_ts = c_time_secs[i]

        i += 1
        if c_sids[i] != cs:
            continue

        disp = c_prices[i] - anch_p
        if disp == 0.0:
            continue

        att = np.int8(1) if disp > 0 else np.int8(-1)
        n_ret = np.int32(0)
        max_fav = abs(disp)
        gross = abs(disp)
        prev_p = c_prices[i]

        # Check immediate resolution
        if abs(disp) >= parent_thresh:
            o_succ[cnt] = True; o_ret[cnt] = 0
            o_fav[cnt] = max_fav / parent_thresh
            o_anch[cnt] = i - 1; o_gross[cnt] = gross
            o_ts[cnt] = anch_ts; cnt += 1
            continue

        resolved = False
        while True:
            i += 1
            if i >= n or c_sids[i] != cs:
                break
            mv = abs(c_prices[i] - prev_p)
            gross += mv
            prev_p = c_prices[i]
            disp = c_prices[i] - anch_p
            fav = disp * att
            if fav > max_fav:
                max_fav = fav
            if c_dirs[i] != att:
                n_ret += 1
            if fav >= parent_thresh:
                o_succ[cnt] = True; o_ret[cnt] = n_ret
                o_fav[cnt] = max_fav / parent_thresh
                o_anch[cnt] = i - (n_ret + 1)
                o_gross[cnt] = gross; o_ts[cnt] = anch_ts
                cnt += 1; resolved = True; break
            elif fav <= -parent_thresh:
                o_succ[cnt] = False; o_ret[cnt] = n_ret
                o_fav[cnt] = max_fav / parent_thresh
                o_anch[cnt] = i - (n_ret + 1)
                o_gross[cnt] = gross; o_ts[cnt] = anch_ts
                cnt += 1; resolved = True; break

    return (o_succ[:cnt], o_ret[:cnt], o_fav[:cnt],
            o_anch[:cnt], o_gross[:cnt], o_ts[:cnt])


@nb.njit(cache=True)
def parent_child_overlay(p_prices, p_dirs, p_sids, p_idx,
                         c_prices, c_dirs, c_sids, c_idx):
    """For each parent swing, find children within and compute metrics."""
    np_swings = len(p_prices) - 1
    o_nchild  = np.empty(np_swings, dtype=np.int32)
    o_nwith   = np.empty(np_swings, dtype=np.int32)
    o_nret    = np.empty(np_swings, dtype=np.int32)
    o_waste   = np.empty(np_swings, dtype=np.float64)
    o_valid   = np.empty(np_swings, dtype=nb.boolean)

    c_ptr = np.int64(0)
    nc = len(c_prices)

    for pi in range(np_swings):
        if p_sids[pi] != p_sids[pi + 1]:
            o_valid[pi] = False; continue
        o_valid[pi] = True
        ps = p_idx[pi]; pe = p_idx[pi + 1]
        p_dir = np.int8(1) if p_prices[pi+1] > p_prices[pi] else np.int8(-1)
        p_net = abs(p_prices[pi+1] - p_prices[pi])

        while c_ptr < nc and c_idx[c_ptr] < ps:
            c_ptr += 1

        gross = 0.0; n_w = 0; n_r = 0; n_mv = 0
        j = c_ptr
        while j < nc - 1 and c_idx[j+1] <= pe and c_sids[j] == p_sids[pi]:
            sz = abs(c_prices[j+1] - c_prices[j])
            d  = np.int8(1) if c_prices[j+1] > c_prices[j] else np.int8(-1)
            gross += sz; n_mv += 1
            if d == p_dir:
                n_w += 1
            else:
                n_r += 1
            j += 1

        o_nchild[pi] = n_mv; o_nwith[pi] = n_w; o_nret[pi] = n_r
        o_waste[pi] = ((gross - p_net) / gross * 100.0) if gross > 0 else 0.0

    return o_nchild, o_nwith, o_nret, o_waste, o_valid


# === DATA LOADING ===

def warmup_numba():
    """Pre-compile all numba functions with dummy data."""
    _ = next_day(np.int32(20250101))
    _ = compute_trading_dates(
        np.array([20250101], dtype=np.int32),
        np.array([70000.0], dtype=np.float32),
    )
    _ = assign_session_ids(np.array([1, 1, 2], dtype=np.int32))
    _p = np.array([100.0, 103.0, 100.0, 104.0], dtype=np.float32)
    _s = np.array([0, 0, 0, 0], dtype=np.int32)
    _ = zigzag_core(_p, _s, 3.0)
    _p64 = np.array([100., 103., 100., 104., 99., 105., 98.], dtype=np.float64)
    _d = np.array([-1, 1, -1, 1, -1, 1, -1], dtype=np.int8)
    _s7 = np.array([0, 0, 0, 0, 0, 0, 0], dtype=np.int32)
    _t = np.array([34200., 34300., 34400., 34500., 34600., 34700., 34800.], dtype=np.float32)
    _i = np.array([0, 10, 20, 30, 40, 50, 60], dtype=np.int64)
    _ = child_walk_completion(_p64, _d, _s7, _t, 25.0)
    _ = parent_child_overlay(_p64, _d, _s7, _i, _p64, _d, _s7, _i)


def load_tick_data(data_path, date_start=None, date_end=None):
    """Load NQ 1-tick CSVs from data_path.

    Args:
        data_path: Directory containing NQ_BarData_1tick_*.csv files
        date_start: Optional YYYYMMDD int for start filter (inclusive)
        date_end: Optional YYYYMMDD int for end filter (inclusive)

    Returns:
        (prices, time_secs, cal_dates) as numpy arrays
    """
    data_path = Path(data_path)
    files = sorted(data_path.glob('NQ_BarData_1tick_*.csv'))
    if not files:
        raise FileNotFoundError(f"No NQ_BarData_1tick_*.csv files in {data_path}")

    frames = []
    for f in files:
        print(f"  Loading {f.name}...", flush=True)
        df = pd.read_csv(
            f, usecols=[0, 1, 5], skipinitialspace=True,
            header=0, dtype={' Last': np.float32, 'Last': np.float32},
            low_memory=False,
        )
        df.columns = ['date', 'time', 'price']
        frames.append(df)

    df = pd.concat(frames, ignore_index=True)
    del frames

    # Parse time -> seconds since midnight
    t = df['time'].str.strip()
    parts = t.str.split(':', n=2, expand=True)
    time_secs = (parts[0].astype(np.int32) * 3600 +
                 parts[1].astype(np.int32) * 60 +
                 parts[2].astype(np.float64)).values.astype(np.float32)
    del parts, t

    # Parse date -> YYYYMMDD integer
    d = df['date'].str.strip()
    dparts = d.str.split('-', n=2, expand=True)
    cal_dates = (dparts[0].astype(np.int32) * 10000 +
                 dparts[1].astype(np.int32) * 100 +
                 dparts[2].astype(np.int32)).values.astype(np.int32)
    del dparts, d

    prices = df['price'].values.astype(np.float32)
    del df

    # Apply date range filter
    if date_start is not None or date_end is not None:
        mask = np.ones(len(prices), dtype=np.bool_)
        if date_start is not None:
            mask &= cal_dates >= date_start
        if date_end is not None:
            mask &= cal_dates <= date_end
        prices = prices[mask].copy()
        time_secs = time_secs[mask].copy()
        cal_dates = cal_dates[mask].copy()

    print(f"  Total rows: {len(prices):,}", flush=True)
    return prices, time_secs, cal_dates


def get_session_mask(time_secs, split):
    """Return boolean mask for session split."""
    if split == 'RTH':
        return (time_secs >= RTH_START) & (time_secs < RTH_END)
    elif split == 'ETH':
        return (time_secs >= ETH_EVENING) | (time_secs < RTH_START)
    else:  # Combined
        return np.ones(len(time_secs), dtype=np.bool_)


def run_all_zigzags(prices, time_secs, cal_dates, splits=None, thresholds=None):
    """Run zig-zag at all thresholds for all session splits.

    Returns:
        results: dict keyed by (split, threshold) with swing data arrays
    """
    if splits is None:
        splits = SPLITS
    if thresholds is None:
        thresholds = THRESHOLDS

    trading_dates = compute_trading_dates(cal_dates, time_secs)
    results = {}

    for split in splits:
        mask = get_session_mask(time_secs, split)
        p  = prices[mask].copy()
        ts = time_secs[mask].copy()
        td = trading_dates[mask].copy()
        sids = assign_session_ids(td)

        print(f"  {split}: {len(p):,} rows", flush=True)

        for thresh in thresholds:
            sw_idx, sw_price, sw_dir, sw_sid = zigzag_core(p, sids, float(thresh))
            sw_ts = ts[sw_idx]

            results[(split, thresh)] = {
                'price':     sw_price.astype(np.float64),
                'dir':       sw_dir,
                'sid':       sw_sid,
                'time_secs': sw_ts.astype(np.float32),
                'orig_idx':  sw_idx,
            }
            print(f"    {thresh:2d}pt: {len(sw_price):>8,} swings", flush=True)

        del p, ts, td, sids

    return results


def parse_date_range(date_range_str):
    """Parse 'YYYY-MM-DD to YYYY-MM-DD' into (start_yyyymmdd, end_yyyymmdd)."""
    if not date_range_str:
        return None, None
    parts = date_range_str.replace(' ', '').split('to')
    if len(parts) != 2:
        raise ValueError(f"Date range must be 'YYYY-MM-DD to YYYY-MM-DD', got: {date_range_str}")

    def to_int(s):
        p = s.split('-')
        return int(p[0]) * 10000 + int(p[1]) * 100 + int(p[2])

    return to_int(parts[0]), to_int(parts[1])
