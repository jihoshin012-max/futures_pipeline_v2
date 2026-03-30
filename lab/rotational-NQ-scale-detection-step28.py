# archetype: rotational
"""
rotational-NQ-scale-detection-step28.py -- Track C Steps 5-7: Fork, verify, live sim.

Step 5: Fork sweep into run_sim_managed with skip-add + break-even hooks.
        Verify identity when hooks disabled.
Step 6: Test each action in isolation + combined on 5 test weeks.
Step 7: Full P1 validation with per-week breakdown.

Prompt: rotational-NQ-prompt-trade-management-c.md Steps 5-7
"""
from __future__ import annotations

import csv
import datetime
import importlib
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

_engine = importlib.import_module("rotational-NQ-scale-detection-engine")
load_bars_extended = _engine.load_bars_extended
aggregate_to_ntick = _engine.aggregate_to_ntick
compute_regime_signals = _engine.compute_regime_signals
compute_entry_signals = _engine.compute_entry_signals
map_signal_to_ticks = _engine.map_signal_to_ticks

_sweep = importlib.import_module("rotational-NQ-scale-detection-sweep")
TICK_SIZE = _sweep.TICK_SIZE
COMMISSION_PER_RT_MINI = _sweep.COMMISSION_PER_RT_MINI

# Also import original run_sim_filtered for identity verification
run_sim_filtered_orig = _sweep.run_sim_filtered


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
INITIAL_QTY = 1

RTH_OPEN_SEC = 9 * 3600 + 30 * 60
RTH_CLOSE_SEC = 15 * 3600 + 49 * 60 + 50

TEST_WEEKS = {
    "2025-W40": "WEAKEST",
    "2025-W41": "MID",
    "2025-W46": "GOOD",
    "2025-W47": "BEST",
    "2025-W48": "LOW",
}

OUTPUT_DIR = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")


# ---------------------------------------------------------------------------
#  Forked simulation with management hooks
# ---------------------------------------------------------------------------

def run_sim_managed(bars: dict, step_dist: float, hard_stop: float,
                    max_fades: int, max_levels: int, max_contract_size: int,
                    signal_arrays: dict | None = None,
                    filter_fn=None,
                    # Management hooks
                    skip_add_fn=None,
                    breakeven_mfe: float = 0.0,
                    ) -> list[dict]:
    """Forked from run_sim_filtered with management hooks.

    skip_add_fn: callable(mae_ticks, prev_agg_mae, agg_bar_offset) -> bool.
        If returns True, skip the add (stay at 1 contract).
        None = no skip-add management (original behavior).

    breakeven_mfe: float > 0 to enable break-even stop.
        Once MFE exceeds this threshold, the effective hard stop becomes 0
        (exit if price returns to entry = current_favor_ticks <= 0).
        0 = disabled (original behavior).
    """
    n = bars["n"]
    last = bars["last"]
    high = bars["high"]
    low = bars["low"]
    tsec = bars["time_sec"]
    dint = bars["date_int"]
    dt_str = bars["datetime"]

    initial_qty = INITIAL_QTY
    max_cs = max_contract_size
    tick_size = TICK_SIZE

    anchor = 0.0; watch_price = 0.0; watch_high = 0.0; watch_low = 0.0
    direction = 0; level = 0; fade_long = 0; fade_short = 0
    pos_qty = 0; avg_entry = 0.0; total_cost = 0.0

    cycle_id = 0
    w_start_dt = ""; w_start_price = 0.0; w_start_high = 0.0; w_start_low = 0.0
    w_start_bar = 0; c_start_bar = 0; c_depth = 0; c_peak = 0
    c_mfe = 0.0; c_mae = 0.0; saved_avg = 0.0
    rth_active = False
    cycles: list[dict] = []

    # Management state
    be_armed = False  # break-even stop armed
    tick_to_agg = signal_arrays["tick_to_agg"] if signal_arrays else None
    prev_agg_idx = -1  # track agg bar transitions for mae_increment
    prev_agg_mae = 0.0  # MAE at previous agg bar boundary
    agg_bar_offset = 0  # agg bars into the trade

    def reset_state():
        nonlocal anchor, direction, level, watch_price, watch_high, watch_low
        nonlocal be_armed, prev_agg_idx, prev_agg_mae, agg_bar_offset
        anchor = 0.0; direction = 0; level = 0
        watch_price = 0.0; watch_high = 0.0; watch_low = 0.0
        be_armed = False; prev_agg_idx = -1; prev_agg_mae = 0.0; agg_bar_offset = 0

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
            if pos_qty > 0:
                pnl = (price - avg_entry) / tick_size * abs(pos_qty)
            else:
                pnl = (avg_entry - price) / tick_size * abs(pos_qty)
        pos_qty = 0; avg_entry = 0.0; total_cost = 0.0
        return pnl

    def record_cycle(i, exit_type, pnl):
        nonlocal cycle_id
        cycles.append({
            "cycle_id": cycle_id,
            "watch_start_dt": w_start_dt, "watch_price": float(w_start_price),
            "watch_high": float(w_start_high), "watch_low": float(w_start_low),
            "watch_bars": c_start_bar - w_start_bar if c_start_bar > w_start_bar else 0,
            "seed_bar": c_start_bar, "exit_bar": i,
            "seed_dt": dt_str[c_start_bar], "exit_dt": dt_str[i],
            "direction": "LONG" if direction == 1 else "SHORT",
            "seed_price": float(last[c_start_bar]),
            "avg_entry_price": saved_avg, "exit_price": float(last[i]),
            "exit_type": exit_type, "depth": c_depth, "max_position": c_peak,
            "pnl_ticks": pnl, "pnl_dollars": pnl * 5.0,
            "bars_held": i - c_start_bar, "mfe_ticks": c_mfe, "mae_ticks": c_mae,
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
        if filter_fn is None or signal_arrays is None:
            return True
        return filter_fn(signal_arrays, bar_idx, dir_val, sd)

    for i in range(n):
        price = float(last[i]); t = int(tsec[i]); d = int(dint[i])

        if RTH_OPEN_SEC <= t <= RTH_CLOSE_SEC:
            if not rth_active:
                rth_active = True
                if pos_qty != 0:
                    saved_avg = avg_entry; sim_flatten(price)
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

            # --- Track agg bar transitions for mae_increment ---
            if tick_to_agg is not None:
                cur_agg = int(tick_to_agg[i])
                if prev_agg_idx < 0:
                    prev_agg_idx = cur_agg
                elif cur_agg != prev_agg_idx:
                    prev_agg_mae = c_mae  # snapshot MAE at bar boundary
                    prev_agg_idx = cur_agg
                    agg_bar_offset += 1

            # --- BREAK-EVEN STOP ---
            if breakeven_mfe > 0 and pos_qty != 0:
                if not be_armed and c_mfe >= breakeven_mfe:
                    be_armed = True
                if be_armed and exc <= 0:
                    # Price returned to or past entry -> breakeven exit
                    saved_avg = avg_entry; pnl = sim_flatten(price)
                    record_cycle(i, "BREAKEVEN", pnl); reset_state(); start_watch(i)
                    continue

        if pos_qty != 0 and hard_stop > 0.0:
            if pos_qty > 0: unreal = (avg_entry - price) / tick_size
            else: unreal = (price - avg_entry) / tick_size
            if unreal >= hard_stop:
                saved_avg = avg_entry; pnl = sim_flatten(price)
                record_cycle(i, "HARD_STOP", pnl); reset_state(); start_watch(i)
                continue

        # === ENTRY GATE ===
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
            if not check_filter(i, sd, step_dist):
                continue
            sim_entry(sd, initial_qty, price)
            direction = sd; level = 0; anchor = price; watch_price = 0.0
            c_start_bar = i; c_depth = 0; c_peak = abs(pos_qty)
            c_mfe = 0.0; c_mae = 0.0; update_fade(sd)
            # Init management state for new trade
            be_armed = False
            if tick_to_agg is not None:
                prev_agg_idx = int(tick_to_agg[i])
            prev_agg_mae = 0.0; agg_bar_offset = 0
            continue

        if pos_qty == 0:
            reset_state(); start_watch(i); continue

        up = price - anchor; dn = anchor - price
        in_favor = (up >= step_dist) if direction == 1 else (dn >= step_dist)
        against = (dn >= step_dist) if direction == 1 else (up >= step_dist)

        # === REVERSAL ===
        if in_favor:
            saved_avg = avg_entry; pnl = sim_flatten(price)
            record_cycle(i, "REVERSAL", pnl)
            new_dir = -direction
            if fade_blocked(new_dir):
                reset_state(); start_watch(i); continue
            if not check_filter(i, new_dir, step_dist):
                reset_state(); start_watch(i); continue
            sim_entry(new_dir, initial_qty, price)
            direction = new_dir; level = 0; anchor = price
            c_start_bar = i; c_depth = 0; c_peak = abs(pos_qty)
            c_mfe = 0.0; c_mae = 0.0; update_fade(new_dir)
            w_start_dt = dt_str[i]; w_start_price = price
            w_start_high = price; w_start_low = price; w_start_bar = i
            be_armed = False
            if tick_to_agg is not None:
                prev_agg_idx = int(tick_to_agg[i])
            prev_agg_mae = 0.0; agg_bar_offset = 0
            continue

        # === ADD GATE ===
        if against:
            ul = level
            if ul >= max_levels: ul = 0
            aq = int(initial_qty * (2 ** ul) + 0.5)
            ap = abs(pos_qty)
            if ap + aq > max_cs:
                room = max_cs - ap
                if room <= 0: continue
                aq = room; level = 0

            # --- SKIP ADD HOOK ---
            if skip_add_fn is not None:
                mae_incr = c_mae - prev_agg_mae
                if skip_add_fn(c_mae, mae_incr, agg_bar_offset):
                    # Skip the add, but still update anchor
                    anchor = price
                    continue

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
#  Signal precomputation (same as step23)
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
    return {
        "choppiness": map_signal_to_ticks(regime["choppiness"], tick_to_agg),
        "slope": map_signal_to_ticks(regime["slope"], tick_to_agg),
        "dr2": map_signal_to_ticks(entry["dr2"], tick_to_agg),
        "dslope": map_signal_to_ticks(entry["dslope"], tick_to_agg),
        "tick_to_agg": tick_to_agg,
        "warmup_ticks": {"dominant_scale": 0, "asymmetry": 0},
        "prev_high": prev_high[tick_to_agg],
        "prev_low": prev_low[tick_to_agg],
        "prev_range": prev_range[tick_to_agg],
        "last": last,
    }


# ---------------------------------------------------------------------------
#  A+B filter
# ---------------------------------------------------------------------------

def make_ab_filter(chop_max, dr2_max, dslope_max, fc_max):
    def f(signals, i, direction, step_dist):
        chop = signals["choppiness"][i]
        if np.isnan(chop): return True
        if chop >= chop_max: return False
        dr2 = signals["dr2"][i]
        if np.isnan(dr2): return True
        if dr2 > dr2_max: return False
        ds = signals["dslope"][i]
        if np.isnan(ds): return True
        if ds > dslope_max: return False
        prev_range = signals["prev_range"][i]
        if np.isnan(prev_range): return True
        entry_price = float(signals["last"][i])
        if direction == 1:
            fc = (entry_price - float(signals["prev_low"][i])) / prev_range
        else:
            fc = (float(signals["prev_high"][i]) - entry_price) / prev_range
        return fc < fc_max
    return f


# ---------------------------------------------------------------------------
#  Metrics
# ---------------------------------------------------------------------------

def compute_metrics(cycles):
    if not cycles:
        return {"n": 0, "wr": 0.0, "sr": 0.0, "er": 0.0, "pnl": 0.0, "pf": 0.0}
    n = len(cycles)
    stops = sum(1 for c in cycles if c["exit_type"] == "HARD_STOP")
    net_pnls = []
    for c in cycles:
        comm = COMMISSION_PER_RT_MINI * max(c.get("max_position", 1), 1)
        net_pnls.append(c["pnl_ticks"] * 5.0 - comm)
    wins = sum(1 for p in net_pnls if p >= 0)
    gross_win = sum(p for p in net_pnls if p >= 0)
    gross_loss = abs(sum(p for p in net_pnls if p < 0))
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
    return {"n": n, "wr": wins / n, "sr": stops / n,
            "er": sum(net_pnls) / n, "pnl": sum(net_pnls), "pf": pf}


def weekly_breakdown(cycles):
    weeks = defaultdict(list)
    for c in cycles:
        dt = c["seed_dt"][:10]
        d = datetime.date(int(dt[:4]), int(dt[5:7]), int(dt[8:10]))
        wk = f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"
        weeks[wk].append(c)
    result = {}
    for wk in sorted(weeks.keys()):
        result[wk] = compute_metrics(weeks[wk])
    return result


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Track C Steps 5-7")
    parser.add_argument("--bar-file", type=str,
                        default=r"C:\Projects\futures_pipeline\data\NQ-1tick-calibration.csv")
    args = parser.parse_args()

    print(f"Loading bars from {args.bar_file}...")
    t0 = time.time()
    bars = load_bars_extended(args.bar_file)
    print(f"Loaded {bars['n']} bars in {time.time()-t0:.1f}s")

    print(f"\nPrecomputing signals (lb={LB}, bar_size={BAR_SIZE})...")
    t1 = time.time()
    signals = precompute_signals(bars, BAR_SIZE, LB)
    print(f"  done ({time.time()-t1:.1f}s)")

    ab_filter = make_ab_filter(CHOP_THRESHOLD, DR2_MAX, DSLOPE_MAX, FC_MAX)

    # ===================================================================
    # STEP 5: Verify identity with management disabled
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"STEP 5: Fork verification (management disabled)")
    print(f"{'='*70}")

    t1 = time.time()
    baseline_cycles = run_sim_filtered_orig(
        bars, step_dist=SD, hard_stop=HS,
        max_fades=MAX_FADES, max_levels=MAX_LEVELS,
        max_contract_size=MAX_CONTRACT_SIZE,
        signal_arrays=signals, filter_fn=ab_filter,
    )
    print(f"  Original: {len(baseline_cycles)} cycles ({time.time()-t1:.0f}s)")

    t1 = time.time()
    managed_disabled = run_sim_managed(
        bars, step_dist=SD, hard_stop=HS,
        max_fades=MAX_FADES, max_levels=MAX_LEVELS,
        max_contract_size=MAX_CONTRACT_SIZE,
        signal_arrays=signals, filter_fn=ab_filter,
        skip_add_fn=None, breakeven_mfe=0.0,
    )
    print(f"  Managed (disabled): {len(managed_disabled)} cycles ({time.time()-t1:.0f}s)")

    if len(baseline_cycles) != len(managed_disabled):
        print(f"  FAIL: Cycle count mismatch: {len(baseline_cycles)} vs {len(managed_disabled)}")
        return

    mismatches = 0
    for a, b in zip(baseline_cycles, managed_disabled):
        if abs(a["pnl_ticks"] - b["pnl_ticks"]) > 0.01:
            mismatches += 1
    if mismatches == 0:
        print(f"  PASS: All {len(baseline_cycles)} cycles match")
    else:
        print(f"  FAIL: {mismatches} PnL mismatches")
        return

    bl = compute_metrics(baseline_cycles)
    print(f"  Baseline: {bl['n']} cyc | {bl['wr']:.0%} WR | {bl['sr']:.0%} SR | "
          f"E[R]=${bl['er']:.2f} | PnL=${bl['pnl']:,.0f}")

    # ===================================================================
    # STEP 6: Live sim with each action individually + combined
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"STEP 6: Live sim with management (full P1)")
    print(f"{'='*70}")

    # --- Skip add only ---
    def skip_add_mae5(mae_ticks, mae_incr, agg_bar_offset):
        return mae_incr > 5

    print(f"\n--- Skip add only (mae_incr > 5) ---")
    t1 = time.time()
    sa_cycles = run_sim_managed(
        bars, step_dist=SD, hard_stop=HS,
        max_fades=MAX_FADES, max_levels=MAX_LEVELS,
        max_contract_size=MAX_CONTRACT_SIZE,
        signal_arrays=signals, filter_fn=ab_filter,
        skip_add_fn=skip_add_mae5, breakeven_mfe=0.0,
    )
    sa = compute_metrics(sa_cycles)
    sa_weeks = weekly_breakdown(sa_cycles)
    print(f"  {sa['n']} cyc | {sa['wr']:.0%} WR | {sa['sr']:.0%} SR | "
          f"E[R]=${sa['er']:.2f} | PnL=${sa['pnl']:,.0f} | PF={sa['pf']:.2f} ({time.time()-t1:.0f}s)")

    # --- Break-even only ---
    print(f"\n--- Break-even stop only (MFE > 10 ticks) ---")
    t1 = time.time()
    be_cycles = run_sim_managed(
        bars, step_dist=SD, hard_stop=HS,
        max_fades=MAX_FADES, max_levels=MAX_LEVELS,
        max_contract_size=MAX_CONTRACT_SIZE,
        signal_arrays=signals, filter_fn=ab_filter,
        skip_add_fn=None, breakeven_mfe=10.0,
    )
    be = compute_metrics(be_cycles)
    be_weeks = weekly_breakdown(be_cycles)
    print(f"  {be['n']} cyc | {be['wr']:.0%} WR | {be['sr']:.0%} SR | "
          f"E[R]=${be['er']:.2f} | PnL=${be['pnl']:,.0f} | PF={be['pf']:.2f} ({time.time()-t1:.0f}s)")

    # --- Combined ---
    print(f"\n--- Combined (skip add + break-even) ---")
    t1 = time.time()
    combo_cycles = run_sim_managed(
        bars, step_dist=SD, hard_stop=HS,
        max_fades=MAX_FADES, max_levels=MAX_LEVELS,
        max_contract_size=MAX_CONTRACT_SIZE,
        signal_arrays=signals, filter_fn=ab_filter,
        skip_add_fn=skip_add_mae5, breakeven_mfe=10.0,
    )
    combo = compute_metrics(combo_cycles)
    combo_weeks = weekly_breakdown(combo_cycles)
    print(f"  {combo['n']} cyc | {combo['wr']:.0%} WR | {combo['sr']:.0%} SR | "
          f"E[R]=${combo['er']:.2f} | PnL=${combo['pnl']:,.0f} | PF={combo['pf']:.2f} ({time.time()-t1:.0f}s)")

    # ===================================================================
    # STEP 7: Per-week breakdown (full P1)
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"STEP 7: Full P1 per-week breakdown")
    print(f"{'='*70}")

    bl_weeks = weekly_breakdown(baseline_cycles)
    all_weeks = sorted(bl_weeks.keys())

    configs = [
        ("Baseline (A+B)", bl_weeks),
        ("Skip add", sa_weeks),
        ("Break-even", be_weeks),
        ("Combined", combo_weeks),
    ]

    for config_name, wks in configs:
        print(f"\n  --- {config_name} ---")
        agg = compute_metrics(baseline_cycles if config_name == "Baseline (A+B)"
                              else sa_cycles if config_name == "Skip add"
                              else be_cycles if config_name == "Break-even"
                              else combo_cycles)
        print(f"  Agg: {agg['n']} cyc | {agg['wr']:.0%} WR | {agg['sr']:.0%} SR | "
              f"E[R]=${agg['er']:.2f} | PnL=${agg['pnl']:,.0f} | PF={agg['pf']:.2f}")

    print(f"\n  {'Week':<10} {'BL_ER':>8} {'SA_ER':>8} {'BE_ER':>8} {'Combo_ER':>8} "
          f"{'SA_d%':>7} {'BE_d%':>7} {'C_d%':>7}")
    print(f"  {'-'*75}")

    all_positive_sa = True
    all_positive_be = True
    all_positive_combo = True
    weeks_improved_sa = 0
    weeks_improved_be = 0
    weeks_improved_combo = 0

    for wk in all_weeks:
        bm = bl_weeks.get(wk, {"er": 0, "pnl": 0, "n": 0})
        sm = sa_weeks.get(wk, {"er": 0, "pnl": 0, "n": 0})
        bem = be_weeks.get(wk, {"er": 0, "pnl": 0, "n": 0})
        cm = combo_weeks.get(wk, {"er": 0, "pnl": 0, "n": 0})

        sa_delta = (sm["er"] - bm["er"]) / bm["er"] * 100 if bm["er"] > 0 else 0
        be_delta = (bem["er"] - bm["er"]) / bm["er"] * 100 if bm["er"] > 0 else 0
        c_delta = (cm["er"] - bm["er"]) / bm["er"] * 100 if bm["er"] > 0 else 0

        marker = ""
        if wk in TEST_WEEKS:
            marker = f" [{TEST_WEEKS[wk]}]"

        print(f"  {wk:<10} ${bm['er']:>7.2f} ${sm['er']:>7.2f} ${bem['er']:>7.2f} ${cm['er']:>7.2f} "
              f"{sa_delta:>+6.0f}% {be_delta:>+6.0f}% {c_delta:>+6.0f}%{marker}")

        if sm["er"] < bm["er"]: all_positive_sa = False
        else: weeks_improved_sa += 1
        if bem["er"] < bm["er"]: all_positive_be = False
        else: weeks_improved_be += 1
        if cm["er"] < bm["er"]: all_positive_combo = False
        else: weeks_improved_combo += 1

    n_weeks = len(all_weeks)

    print(f"\n  Summary:")
    print(f"  {'Config':<20} {'Weeks improved':>15} {'All positive':>12}")
    print(f"  {'-'*50}")
    print(f"  {'Skip add':<20} {weeks_improved_sa:>3}/{n_weeks:<11} {'Y' if all_positive_sa else 'N':>12}")
    print(f"  {'Break-even':<20} {weeks_improved_be:>3}/{n_weeks:<11} {'Y' if all_positive_be else 'N':>12}")
    print(f"  {'Combined':<20} {weeks_improved_combo:>3}/{n_weeks:<11} {'Y' if all_positive_combo else 'N':>12}")

    # ===================================================================
    # Kill gate: improvement only in pooled stats?
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"KILL GATES")
    print(f"{'='*70}")

    for name, wks, metric in [("Skip add", sa_weeks, sa),
                               ("Break-even", be_weeks, be),
                               ("Combined", combo_weeks, combo)]:
        pooled_delta = (metric["er"] - bl["er"]) / bl["er"] * 100
        print(f"\n  {name}: pooled E[R] delta = {pooled_delta:+.1f}%")
        if pooled_delta < 5:
            print(f"    WARNING: < 5% improvement. May not justify C++ complexity.")

    # Save results
    out_csv = OUTPUT_DIR / "trade-management-step7-results.csv"
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "week", "bl_n", "bl_er", "bl_pnl",
            "sa_n", "sa_er", "sa_pnl",
            "be_n", "be_er", "be_pnl",
            "combo_n", "combo_er", "combo_pnl",
        ])
        w.writeheader()
        for wk in all_weeks:
            bm = bl_weeks.get(wk, {"n": 0, "er": 0, "pnl": 0})
            sm = sa_weeks.get(wk, {"n": 0, "er": 0, "pnl": 0})
            bem = be_weeks.get(wk, {"n": 0, "er": 0, "pnl": 0})
            cm = combo_weeks.get(wk, {"n": 0, "er": 0, "pnl": 0})
            w.writerow({
                "week": wk, "bl_n": bm["n"], "bl_er": f"{bm['er']:.2f}", "bl_pnl": f"{bm['pnl']:.2f}",
                "sa_n": sm["n"], "sa_er": f"{sm['er']:.2f}", "sa_pnl": f"{sm['pnl']:.2f}",
                "be_n": bem["n"], "be_er": f"{bem['er']:.2f}", "be_pnl": f"{bem['pnl']:.2f}",
                "combo_n": cm["n"], "combo_er": f"{cm['er']:.2f}", "combo_pnl": f"{cm['pnl']:.2f}",
            })
    print(f"\n  Saved: {out_csv}")

    total = time.time() - t0
    print(f"\nTotal runtime: {total:.0f}s ({total/60:.1f}m)")


if __name__ == "__main__":
    main()
