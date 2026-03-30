# archetype: rotational
"""
rotational-NQ-scale-detection-step39.py — Track B2: Sanity check for in-trade
d2 hold + combined entry gate + hold test.

Part 1: Sanity check
  - d2_avg3 hold extended 69% of trades. Random hold at 69% rate (10 seeds).
  - Must beat all 10 random seeds.

Part 2: Combined test
  - Entry gate C (d2 neutral zone |d2|<=0.5) + in-trade d2_avg3 hold
  - Do they stack or overlap?
  - Full P1 per-week.

Prompt: rotational-NQ-prompt-ema-directional-b2.md (extension)
"""
from __future__ import annotations

import datetime
import importlib
import random
import sys
import time
from collections import defaultdict

import numpy as np

sys.path.insert(0, r"c:\Projects\futures_pipeline\lab")

_engine = importlib.import_module("rotational-NQ-scale-detection-engine")
_sweep = importlib.import_module("rotational-NQ-scale-detection-sweep")
load_bars_extended = _engine.load_bars_extended
aggregate_to_ntick = _engine.aggregate_to_ntick
compute_regime_signals = _engine.compute_regime_signals
compute_entry_signals = _engine.compute_entry_signals
map_signal_to_ticks = _engine.map_signal_to_ticks
run_sim_filtered = _sweep.run_sim_filtered
TICK_SIZE = _sweep.TICK_SIZE
INITIAL_QTY = 1

# ---------------------------------------------------------------------------
#  Config
# ---------------------------------------------------------------------------
SD = 10.0
HS = 60.0
MAX_LEVELS = 1
MAX_CONTRACT_SIZE = 2
MAX_FADES = 0
BAR_SIZE = 250
LB = 3

CHOP_THRESHOLD = 0.10
DR2_MAX = -0.40
DSLOPE_MAX = -2.0
FC_MAX = 0.40

COMM = 3.50
TV = 5.0

RTH_OPEN_SEC = 9 * 3600 + 30 * 60
RTH_CLOSE_SEC = 15 * 3600 + 49 * 60 + 50

DATA_FILE = r"C:\Projects\futures_pipeline\data\NQ-1tick-calibration.csv"


# ---------------------------------------------------------------------------
#  EMA
# ---------------------------------------------------------------------------
def ema(data, period):
    out = np.full(len(data), np.nan, dtype=np.float64)
    k = 2.0 / (period + 1)
    first_valid = -1
    for i in range(len(data)):
        if np.isnan(data[i]):
            continue
        if first_valid < 0:
            out[i] = data[i]
            first_valid = i
        else:
            out[i] = data[i] * k + out[i - 1] * (1 - k)
    return out


# ---------------------------------------------------------------------------
#  Signal precomputation
# ---------------------------------------------------------------------------
def precompute_signals(bars, bar_size, lookback):
    agg_bars, tick_to_agg = aggregate_to_ntick(bars, bar_size)
    regime = compute_regime_signals(agg_bars, lookback=lookback)
    entry = compute_entry_signals(agg_bars, lookback=lookback)

    n_agg = agg_bars["n"]
    a_high = agg_bars["high"]
    a_low = agg_bars["low"]
    last = bars["last"]

    prev_high = np.full(n_agg, np.nan, dtype=np.float64)
    prev_low = np.full(n_agg, np.nan, dtype=np.float64)
    prev_range = np.full(n_agg, np.nan, dtype=np.float64)
    for ai in range(1, n_agg):
        prev_high[ai] = float(a_high[ai - 1])
        prev_low[ai] = float(a_low[ai - 1])
        rng = float(a_high[ai - 1]) - float(a_low[ai - 1])
        prev_range[ai] = rng if rng > 0 else np.nan

    close_agg = np.array(agg_bars["last"], dtype=np.float64)
    ema9_agg = ema(close_agg, 9)

    d_ema9_agg = np.full(n_agg, np.nan, dtype=np.float64)
    for i in range(1, n_agg):
        if not np.isnan(ema9_agg[i]) and not np.isnan(ema9_agg[i - 1]):
            d_ema9_agg[i] = ema9_agg[i] - ema9_agg[i - 1]

    d2_ema9_agg = np.full(n_agg, np.nan, dtype=np.float64)
    for i in range(2, n_agg):
        if not np.isnan(d_ema9_agg[i]) and not np.isnan(d_ema9_agg[i - 1]):
            d2_ema9_agg[i] = d_ema9_agg[i] - d_ema9_agg[i - 1]

    d2_avg3_agg = np.full(n_agg, np.nan, dtype=np.float64)
    for i in range(2, n_agg):
        vals = [d2_ema9_agg[i - j] for j in range(min(3, i + 1))
                if i - j >= 0 and not np.isnan(d2_ema9_agg[i - j])]
        if len(vals) == 3:
            d2_avg3_agg[i] = sum(vals) / 3

    return {
        "choppiness": map_signal_to_ticks(regime["choppiness"], tick_to_agg),
        "dr2": map_signal_to_ticks(entry["dr2"], tick_to_agg),
        "dslope": map_signal_to_ticks(entry["dslope"], tick_to_agg),
        "prev_high": prev_high[tick_to_agg],
        "prev_low": prev_low[tick_to_agg],
        "prev_range": prev_range[tick_to_agg],
        "last": last,
        "d2_ema9": map_signal_to_ticks(d2_ema9_agg, tick_to_agg),
        "d2_avg3": map_signal_to_ticks(d2_avg3_agg, tick_to_agg),
        "warmup_ticks": {"dominant_scale": 0, "asymmetry": 0},
    }


# ---------------------------------------------------------------------------
#  Filters
# ---------------------------------------------------------------------------
def make_ab_filter():
    def f(signals, i, direction, step_dist):
        chop = signals["choppiness"][i]
        if np.isnan(chop):
            return True
        if chop >= CHOP_THRESHOLD:
            return False
        dr2 = signals["dr2"][i]
        if np.isnan(dr2):
            return True
        if dr2 > DR2_MAX:
            return False
        ds = signals["dslope"][i]
        if np.isnan(ds):
            return True
        if ds > DSLOPE_MAX:
            return False
        prev_range = signals["prev_range"][i]
        if np.isnan(prev_range):
            return True
        entry_price = float(signals["last"][i])
        if direction == 1:
            fc = (entry_price - float(signals["prev_low"][i])) / prev_range
        else:
            fc = (float(signals["prev_high"][i]) - entry_price) / prev_range
        return fc < FC_MAX
    return f


def make_ab_plus_d2_neutral(neutral_thresh=0.5):
    """A+B + entry gate C: d2 neutral zone."""
    ab = make_ab_filter()
    def f(signals, i, direction, step_dist):
        if not ab(signals, i, direction, step_dist):
            return False
        d2 = signals["d2_ema9"][i]
        if np.isnan(d2):
            return True
        if abs(d2) <= neutral_thresh:
            return True
        if direction == 1 and d2 < -neutral_thresh:
            return False
        if direction == -1 and d2 > neutral_thresh:
            return False
        return True
    return f


# ---------------------------------------------------------------------------
#  Forked sim with extended hold (from step38, condensed)
# ---------------------------------------------------------------------------
def run_sim_extended(bars, step_dist, hard_stop,
                     max_fades, max_levels, max_contract_size,
                     signal_arrays, filter_fn,
                     hold_check_fn):
    """hold_check_fn(bar_idx, direction) -> True to hold, False for normal reversal."""
    n = bars["n"]
    last = bars["last"]; high = bars["high"]; low = bars["low"]
    tsec = bars["time_sec"]; dint = bars["date_int"]; dt_str = bars["datetime"]
    max_cs = max_contract_size; tick_size = TICK_SIZE

    anchor = 0.0; watch_price = 0.0; watch_high = 0.0; watch_low = 0.0
    direction = 0; level = 0; fade_long = 0; fade_short = 0
    pos_qty = 0; avg_entry = 0.0; total_cost = 0.0
    cycle_id = 0
    w_start_dt = ""; w_start_price = 0.0; w_start_high = 0.0; w_start_low = 0.0
    w_start_bar = 0; c_start_bar = 0; c_depth = 0; c_peak = 0
    c_mfe = 0.0; c_mae = 0.0; saved_avg = 0.0
    rth_active = False; extended = False
    cycles = []

    def reset_state():
        nonlocal anchor, direction, level, watch_price, watch_high, watch_low, extended
        anchor = 0.0; direction = 0; level = 0
        watch_price = 0.0; watch_high = 0.0; watch_low = 0.0; extended = False

    def start_watch(i):
        nonlocal w_start_dt, w_start_price, w_start_high, w_start_low, w_start_bar
        nonlocal c_depth, c_peak, c_mfe, c_mae
        w_start_dt = dt_str[i]; w_start_price = last[i]
        w_start_high = last[i]; w_start_low = last[i]
        w_start_bar = i; c_depth = 0; c_peak = 0; c_mfe = 0.0; c_mae = 0.0

    def sim_entry(d, qty, price):
        nonlocal pos_qty, avg_entry, total_cost
        if pos_qty == 0:
            pos_qty = d * qty; avg_entry = price; total_cost = price * qty
        else:
            total_cost += price * qty; pos_qty += d * qty
            avg_entry = total_cost / abs(pos_qty)

    def sim_flatten(price):
        nonlocal pos_qty, avg_entry, total_cost
        pnl = 0.0
        if pos_qty != 0:
            if pos_qty > 0: pnl = (price - avg_entry) / tick_size * abs(pos_qty)
            else: pnl = (avg_entry - price) / tick_size * abs(pos_qty)
        pos_qty = 0; avg_entry = 0.0; total_cost = 0.0
        return pnl

    def record_cycle(i, exit_type, pnl):
        nonlocal cycle_id
        cycles.append({
            "cycle_id": cycle_id, "seed_bar": c_start_bar, "exit_bar": i,
            "seed_dt": dt_str[c_start_bar], "exit_dt": dt_str[i],
            "direction": "LONG" if direction == 1 else "SHORT",
            "seed_price": float(last[c_start_bar]),
            "avg_entry_price": saved_avg, "exit_price": float(last[i]),
            "exit_type": exit_type, "depth": c_depth, "max_position": c_peak,
            "pnl_ticks": pnl, "pnl_dollars": pnl * 5.0,
            "bars_held": i - c_start_bar, "mfe_ticks": c_mfe, "mae_ticks": c_mae,
            "watch_start_dt": w_start_dt, "watch_price": float(w_start_price),
            "watch_high": float(w_start_high), "watch_low": float(w_start_low),
            "watch_bars": c_start_bar - w_start_bar if c_start_bar > w_start_bar else 0,
        })
        cycle_id += 1

    def fade_blocked(d):
        if max_fades <= 0: return False
        return (d == 1 and fade_long >= max_fades) or (d == -1 and fade_short >= max_fades)

    def update_fade(d):
        nonlocal fade_long, fade_short
        if d == 1: fade_long += 1; fade_short = 0
        else: fade_short += 1; fade_long = 0

    def check_filter(bar_idx, dir_val, sd):
        if filter_fn is None: return True
        return filter_fn(signal_arrays, bar_idx, dir_val, sd)

    for i in range(n):
        price = float(last[i]); t = int(tsec[i])

        if RTH_OPEN_SEC <= t <= RTH_CLOSE_SEC:
            if not rth_active:
                rth_active = True
                if pos_qty != 0: saved_avg = avg_entry; sim_flatten(price)
                reset_state(); fade_long = 0; fade_short = 0; start_watch(i)
        else:
            if rth_active and t > RTH_CLOSE_SEC: rth_active = False
            continue

        if t >= RTH_CLOSE_SEC:
            if pos_qty != 0:
                saved_avg = avg_entry; pnl = sim_flatten(price)
                record_cycle(i, "EOD_FLATTEN", pnl); reset_state()
            elif watch_price != 0.0: reset_state()
            rth_active = False; continue

        if pos_qty != 0:
            if pos_qty > 0:
                hi_exc = (float(high[i]) - avg_entry) / tick_size
                lo_exc = (float(low[i]) - avg_entry) / tick_size
            else:
                hi_exc = (avg_entry - float(low[i])) / tick_size
                lo_exc = (avg_entry - float(high[i])) / tick_size
            if hi_exc > c_mfe: c_mfe = hi_exc
            if -lo_exc > c_mae: c_mae = -lo_exc
            if pos_qty > 0: exc = (price - avg_entry) / tick_size
            else: exc = (avg_entry - price) / tick_size
            if exc > c_mfe: c_mfe = exc
            if -exc > c_mae: c_mae = -exc

        if pos_qty != 0 and hard_stop > 0.0:
            if pos_qty > 0: unreal = (avg_entry - price) / tick_size
            else: unreal = (price - avg_entry) / tick_size
            if unreal >= hard_stop:
                saved_avg = avg_entry; pnl = sim_flatten(price)
                record_cycle(i, "HARD_STOP", pnl); reset_state()
                start_watch(i); continue

        # D2 exit check when in extended hold
        if pos_qty != 0 and extended:
            if not hold_check_fn(i, direction):
                saved_avg = avg_entry; pnl = sim_flatten(price)
                record_cycle(i, "D2_EXIT", pnl); reset_state()
                start_watch(i); continue

        # Entry
        if pos_qty == 0 and anchor == 0.0:
            if watch_price == 0.0:
                watch_price = price; watch_high = price; watch_low = price
                if not w_start_dt: start_watch(i)
                continue
            if price > watch_high: watch_high = price
            if price < watch_low: watch_low = price
            if price > w_start_high: w_start_high = price
            if price < w_start_low: w_start_low = price
            pfh = watch_high - price; pfl = price - watch_low
            sd = 0
            if pfh >= step_dist and pfl >= step_dist:
                sd = 1 if pfh >= pfl else -1
            elif pfh >= step_dist: sd = 1
            elif pfl >= step_dist: sd = -1
            else: continue
            if fade_blocked(sd):
                sd = -sd
                other = (pfh >= step_dist) if sd == 1 else (pfl >= step_dist)
                if not other or fade_blocked(sd): continue
            if not check_filter(i, sd, step_dist): continue
            sim_entry(sd, INITIAL_QTY, price)
            direction = sd; level = 0; anchor = price; watch_price = 0.0
            c_start_bar = i; c_depth = 0; c_peak = abs(pos_qty)
            c_mfe = 0.0; c_mae = 0.0; update_fade(sd)
            extended = False; continue

        if pos_qty == 0:
            reset_state(); start_watch(i); continue

        up = price - anchor; dn = anchor - price
        in_favor = (up >= step_dist) if direction == 1 else (dn >= step_dist)
        against = (dn >= step_dist) if direction == 1 else (up >= step_dist)

        # Reversal with hold check
        if in_favor:
            if hold_check_fn(i, direction):
                extended = True; anchor = price; continue
            else:
                saved_avg = avg_entry; pnl = sim_flatten(price)
                record_cycle(i, "REVERSAL", pnl)
                new_dir = -direction
                if fade_blocked(new_dir):
                    reset_state(); start_watch(i); continue
                if not check_filter(i, new_dir, step_dist):
                    reset_state(); start_watch(i); continue
                sim_entry(new_dir, INITIAL_QTY, price)
                direction = new_dir; level = 0; anchor = price
                c_start_bar = i; c_depth = 0; c_peak = abs(pos_qty)
                c_mfe = 0.0; c_mae = 0.0; update_fade(new_dir)
                w_start_dt = dt_str[i]; w_start_price = price
                w_start_high = price; w_start_low = price; w_start_bar = i
                extended = False; continue

        # Add
        if against:
            ul = level
            if ul >= max_levels: ul = 0
            aq = int(INITIAL_QTY * (2 ** ul) + 0.5)
            ap = abs(pos_qty)
            if ap + aq > max_cs:
                room = max_cs - ap
                if room <= 0: continue
                aq = room; level = 0
            sim_entry(direction, aq, price)
            level += 1
            if level >= max_levels: level = 0
            anchor = price; c_depth += 1
            if abs(pos_qty) > c_peak: c_peak = abs(pos_qty)
            continue

    if pos_qty != 0 and n > 0:
        saved_avg = avg_entry; pnl = sim_flatten(float(last[n - 1]))
        record_cycle(n - 1, "DATA_END", pnl)

    return cycles


# ---------------------------------------------------------------------------
#  Hold check functions
# ---------------------------------------------------------------------------
def make_d2_avg3_hold(signals):
    d2_avg3 = signals["d2_avg3"]
    def check(i, direction):
        val = float(d2_avg3[i])
        if np.isnan(val): return False
        if direction == 1: return val > 0
        else: return val <= 0
    return check


def make_d2_raw_hold(signals):
    d2 = signals["d2_ema9"]
    def check(i, direction):
        val = float(d2[i])
        if np.isnan(val): return False
        if direction == 1: return val > 0
        else: return val <= 0
    return check


def make_random_hold(hold_rate, seed_val):
    rng = random.Random(seed_val)
    def check(i, direction):
        return rng.random() < hold_rate
    return check


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------
def get_week(seed_dt):
    dt = seed_dt[:10]
    d = datetime.date(int(dt[:4]), int(dt[5:7]), int(dt[8:10]))
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def cycle_metrics(cycles):
    if not cycles:
        return {"n": 0, "wr": 0.0, "sr": 0.0, "er": 0.0, "pnl": 0.0}
    n = len(cycles)
    pnls = [c["pnl_ticks"] * TV - COMM * max(c.get("max_position", 1), 1) for c in cycles]
    total = sum(pnls)
    wins = sum(1 for p in pnls if p >= 0)
    stops = sum(1 for c in cycles if c["exit_type"] == "HARD_STOP")
    return {"n": n, "wr": wins/n, "sr": stops/n, "er": total/n, "pnl": total}


def per_week(cycles):
    weeks = defaultdict(list)
    for c in cycles:
        weeks[get_week(c["seed_dt"])].append(c)
    return {wk: cycle_metrics(weeks[wk]) for wk in sorted(weeks.keys())}


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 70)
    print("Track B2: Sanity check + combined test")
    print("=" * 70)

    print("\nLoading P1 data...")
    t0 = time.time()
    bars = load_bars_extended(DATA_FILE)
    print(f"  {bars['n']} ticks ({time.time()-t0:.0f}s)")

    print("\nPrecomputing signals...")
    t1 = time.time()
    signals = precompute_signals(bars, BAR_SIZE, LB)
    print(f"  Done ({time.time()-t1:.0f}s)")

    ab_filter = make_ab_filter()

    # =========================================================================
    # PART 1: Sanity check — d2_avg3 hold vs random hold
    # =========================================================================
    print(f"\n{'='*70}")
    print("PART 1: Sanity check — d2_avg3 hold vs random hold")
    print(f"{'='*70}")

    # Baseline (A+B, no hold)
    print("\n  Running A+B baseline...")
    t1 = time.time()
    bl_cycles = run_sim_filtered(
        bars, SD, HS, MAX_FADES, MAX_LEVELS, MAX_CONTRACT_SIZE,
        signal_arrays=signals, filter_fn=ab_filter
    )
    bl_m = cycle_metrics(bl_cycles)
    print(f"    {bl_m['n']} cycles, E[R]=${bl_m['er']:.2f} ({time.time()-t1:.0f}s)")

    # d2_avg3 hold
    print("\n  Running d2_avg3 hold...")
    t1 = time.time()
    d2_hold = make_d2_avg3_hold(signals)
    d2_cycles = run_sim_extended(
        bars, SD, HS, MAX_FADES, MAX_LEVELS, MAX_CONTRACT_SIZE,
        signal_arrays=signals, filter_fn=ab_filter,
        hold_check_fn=d2_hold
    )
    d2_m = cycle_metrics(d2_cycles)
    # Compute hold rate
    d2_exits = sum(1 for c in d2_cycles if c["exit_type"] == "D2_EXIT")
    reversals = sum(1 for c in d2_cycles if c["exit_type"] == "REVERSAL")
    hold_rate = d2_exits / (d2_exits + reversals) if (d2_exits + reversals) > 0 else 0
    print(f"    {d2_m['n']} cycles, E[R]=${d2_m['er']:.2f}")
    print(f"    Hold rate: {hold_rate:.0%} ({d2_exits} D2_EXIT / {d2_exits + reversals} reversal decisions)")
    print(f"    ({time.time()-t1:.0f}s)")

    # Random hold at same rate (10 seeds)
    print(f"\n  Running 10 random hold seeds at {hold_rate:.0%} rate...")
    random_ers = []
    for seed in range(10):
        t1 = time.time()
        rand_hold = make_random_hold(hold_rate, seed)
        rand_cycles = run_sim_extended(
            bars, SD, HS, MAX_FADES, MAX_LEVELS, MAX_CONTRACT_SIZE,
            signal_arrays=signals, filter_fn=ab_filter,
            hold_check_fn=rand_hold
        )
        rand_m = cycle_metrics(rand_cycles)
        random_ers.append(rand_m["er"])
        beats = "BEATS" if d2_m["er"] > rand_m["er"] else "LOSES"
        print(f"    Seed {seed}: {rand_m['n']} cyc, E[R]=${rand_m['er']:.2f} -- {beats} ({time.time()-t1:.0f}s)")

    max_random = max(random_ers)
    margin = d2_m["er"] - max_random
    beats_all = d2_m["er"] > max_random

    print(f"\n  d2_avg3 hold E[R]: ${d2_m['er']:.2f}")
    print(f"  Max random E[R]:   ${max_random:.2f}")
    print(f"  Margin:            ${margin:.2f}")
    print(f"  SANITY CHECK: {'PASS' if beats_all else 'FAIL'}")

    # =========================================================================
    # PART 2: Combined — entry gate C + in-trade d2_avg3 hold
    # =========================================================================
    print(f"\n{'='*70}")
    print("PART 2: Combined — entry gate C (d2 neutral +/-0.5) + d2_avg3 hold")
    print(f"{'='*70}")

    entry_gate_c = make_ab_plus_d2_neutral(0.5)

    # Entry gate C alone (no hold)
    print("\n  Running entry gate C alone...")
    t1 = time.time()
    gc_cycles = run_sim_filtered(
        bars, SD, HS, MAX_FADES, MAX_LEVELS, MAX_CONTRACT_SIZE,
        signal_arrays=signals, filter_fn=entry_gate_c
    )
    gc_m = cycle_metrics(gc_cycles)
    print(f"    {gc_m['n']} cycles, E[R]=${gc_m['er']:.2f} ({time.time()-t1:.0f}s)")

    # Combined: entry gate C + d2_avg3 hold
    print("\n  Running entry gate C + d2_avg3 hold...")
    t1 = time.time()
    combo_cycles = run_sim_extended(
        bars, SD, HS, MAX_FADES, MAX_LEVELS, MAX_CONTRACT_SIZE,
        signal_arrays=signals, filter_fn=entry_gate_c,
        hold_check_fn=d2_hold
    )
    combo_m = cycle_metrics(combo_cycles)
    print(f"    {combo_m['n']} cycles, E[R]=${combo_m['er']:.2f} ({time.time()-t1:.0f}s)")

    # Also: d2_avg3 hold only (A+B filter, no entry gate) — already computed above

    # Summary table
    print(f"\n  {'Config':<40} {'Cyc':>5} {'E[R]':>8} {'dER':>8} {'WR':>5} {'SR':>5}")
    print(f"  {'-'*75}")
    for label, m in [
        ("A+B baseline (no gate, no hold)", bl_m),
        ("Entry gate C only (d2 neutral +/-0.5)", gc_m),
        ("In-trade hold only (d2_avg3)", d2_m),
        ("Combined (gate C + d2_avg3 hold)", combo_m),
    ]:
        delta = m["er"] - bl_m["er"]
        print(f"  {label:<40} {m['n']:>5} ${m['er']:>7.2f} ${delta:>+7.2f} "
              f"{m['wr']:>5.0%} {m['sr']:>5.0%}")

    # Per-week for combined
    print(f"\n  Per-week: Combined (gate C + d2_avg3 hold)")
    combo_weekly = per_week(combo_cycles)
    bl_weekly = per_week(bl_cycles)

    print(f"  {'Week':<10} {'BL':>5} {'F':>5} {'BL_ER':>8} {'F_ER':>8} {'dER':>8}")
    print(f"  {'-'*55}")

    weeks_improved = 0
    for wk in sorted(bl_weekly.keys()):
        m_bl = bl_weekly[wk]
        m_f = combo_weekly.get(wk, {"n": 0, "er": 0, "wr": 0, "sr": 0, "pnl": 0})
        delta = m_f["er"] - m_bl["er"]
        improved = delta > 0
        if improved: weeks_improved += 1
        mark = "UP" if improved else "DN"
        print(f"  {wk:<10} {m_bl['n']:>5} {m_f['n']:>5} ${m_bl['er']:>7.2f} "
              f"${m_f['er']:>7.2f} ${delta:>+7.2f} {mark}")

    all_pos = all(combo_weekly.get(wk, {"er": 0})["er"] >= 0 for wk in bl_weekly)
    print(f"\n  Weeks improved: {weeks_improved}/{len(bl_weekly)}")
    print(f"  All weeks positive: {'YES' if all_pos else 'NO'}")

    # Stacking analysis
    gate_c_delta = gc_m["er"] - bl_m["er"]
    hold_delta = d2_m["er"] - bl_m["er"]
    combo_delta = combo_m["er"] - bl_m["er"]
    sum_individual = gate_c_delta + hold_delta
    interaction = combo_delta - sum_individual

    print(f"\n  Stacking analysis:")
    print(f"    Gate C alone:     +${gate_c_delta:.2f}")
    print(f"    Hold alone:       +${hold_delta:.2f}")
    print(f"    Sum (if independent): +${sum_individual:.2f}")
    print(f"    Combined actual:  +${combo_delta:.2f}")
    print(f"    Interaction:      ${interaction:+.2f} ({'synergy' if interaction > 0 else 'overlap'})")

    print(f"\nRuntime: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
