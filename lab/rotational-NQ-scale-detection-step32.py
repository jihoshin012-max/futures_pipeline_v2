# archetype: rotational
"""
rotational-NQ-scale-detection-step32.py -- Track C2 Steps 5-7: Fork sim with
partial profit, verify, live sim on test weeks, full P1 validation.

Step 5: Fork run_sim_filtered into run_sim_partial. Verify identity when
        partial_mfe_ticks=0 (disabled).
Step 6: Test each N (3,4,5,6,7 pts) in isolation on 5 test weeks.
Step 7: Full P1 validation with per-week breakdown.

Partial profit mechanic:
- depth_1 trade reaches N pts of MFE from avg_entry
- Close 1 of 2 contracts (lock in PnL)
- Arm break-even stop on remaining contract
- Remaining exits at reversal, break-even, or hard stop

Prompt: rotational-NQ-prompt-loss-mitigation-c2.md Steps 5-7
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

PARTIAL_N_PTS = [3, 4, 5, 6, 7]

TEST_WEEKS = {
    "2025-W40": "WEAKEST",
    "2025-W41": "MID",
    "2025-W46": "GOOD",
    "2025-W47": "BEST",
    "2025-W48": "LOW",
}

OUTPUT_DIR = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")


# ---------------------------------------------------------------------------
#  Forked simulation with partial profit
# ---------------------------------------------------------------------------

def run_sim_partial(bars: dict, step_dist: float, hard_stop: float,
                    max_fades: int, max_levels: int, max_contract_size: int,
                    signal_arrays: dict | None = None,
                    filter_fn=None,
                    partial_mfe_ticks: float = 0.0,
                    ) -> list[dict]:
    """Forked from run_sim_filtered with partial profit mechanics.

    partial_mfe_ticks: when > 0, enables partial profit on depth_1 trades.
        When MFE from avg_entry reaches this threshold:
        - Close 1 of 2 contracts (record partial PnL)
        - Arm break-even stop on remaining 1 contract
        - Remaining contract exits at reversal, break-even, or hard stop
    When partial_mfe_ticks == 0: identical to run_sim_filtered.
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

    # Partial profit state
    partial_fired = False
    partial_pnl_ticks = 0.0  # realized PnL from closed contract
    be_armed = False  # break-even stop active on remaining contract

    def reset_state():
        nonlocal anchor, direction, level, watch_price, watch_high, watch_low
        nonlocal partial_fired, partial_pnl_ticks, be_armed
        anchor = 0.0; direction = 0; level = 0
        watch_price = 0.0; watch_high = 0.0; watch_low = 0.0
        partial_fired = False; partial_pnl_ticks = 0.0; be_armed = False

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

    def sim_partial_close(price, contracts_to_close):
        """Close some contracts, return realized PnL ticks for closed portion."""
        nonlocal pos_qty, total_cost
        if pos_qty == 0:
            return 0.0
        abs_qty = abs(pos_qty)
        close_qty = min(contracts_to_close, abs_qty)
        if pos_qty > 0:
            pnl = (price - avg_entry) / tick_size * close_qty
        else:
            pnl = (avg_entry - price) / tick_size * close_qty
        # Reduce position
        sign = 1 if pos_qty > 0 else -1
        pos_qty = sign * (abs_qty - close_qty)
        total_cost = avg_entry * abs(pos_qty) if pos_qty != 0 else 0.0
        # avg_entry stays the same
        return pnl

    def record_cycle(i, exit_type, pnl):
        nonlocal cycle_id
        total_pnl = pnl + partial_pnl_ticks
        # max_position tracks the peak position during the cycle
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
            "pnl_ticks": total_pnl, "pnl_dollars": total_pnl * 5.0,
            "bars_held": i - c_start_bar, "mfe_ticks": c_mfe, "mae_ticks": c_mae,
            "partial_fired": partial_fired,
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

            # --- PARTIAL PROFIT CHECK ---
            if (partial_mfe_ticks > 0 and not partial_fired
                    and c_depth >= 1 and abs(pos_qty) >= 2
                    and c_mfe >= partial_mfe_ticks):
                # Close 1 contract at current price
                partial_pnl_ticks = sim_partial_close(price, 1)
                partial_fired = True
                be_armed = True

            # --- BREAK-EVEN STOP (remaining contract after partial) ---
            if be_armed and pos_qty != 0 and exc <= 0:
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
            partial_fired = False; partial_pnl_ticks = 0.0; be_armed = False
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
            partial_fired = False; partial_pnl_ticks = 0.0; be_armed = False
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
            if signal_arrays and filter_fn:
                pass  # No add gate filtering in this fork
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
        prev = ai - 1
        prev_high[ai] = float(a_high[prev])
        prev_low[ai] = float(a_low[prev])
        rng = float(a_high[prev]) - float(a_low[prev])
        prev_range[ai] = rng if rng > 0 else np.nan

    prev_high_tick = prev_high[tick_to_agg]
    prev_low_tick = prev_low[tick_to_agg]
    prev_range_tick = prev_range[tick_to_agg]

    return {
        "choppiness": map_signal_to_ticks(regime["choppiness"], tick_to_agg),
        "slope": map_signal_to_ticks(regime["slope"], tick_to_agg),
        "dr2": map_signal_to_ticks(entry["dr2"], tick_to_agg),
        "dslope": map_signal_to_ticks(entry["dslope"], tick_to_agg),
        "tick_to_agg": tick_to_agg,
        "warmup_ticks": {"dominant_scale": 0, "asymmetry": 0},
        "prev_high": prev_high_tick,
        "prev_low": prev_low_tick,
        "prev_range": prev_range_tick,
        "last": last,
    }


# ---------------------------------------------------------------------------
#  Filter
# ---------------------------------------------------------------------------

def make_ab_filter(chop_max, dr2_max, dslope_max, fc_max):
    def f(signals, i, direction, step_dist):
        chop = signals["choppiness"][i]
        if np.isnan(chop):
            return True
        if chop >= chop_max:
            return False
        dr2 = signals["dr2"][i]
        if np.isnan(dr2):
            return True
        if dr2 > dr2_max:
            return False
        ds = signals["dslope"][i]
        if np.isnan(ds):
            return True
        if ds > dslope_max:
            return False
        prev_range = signals["prev_range"][i]
        if np.isnan(prev_range):
            return True
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


def get_week(c):
    dt = c["seed_dt"][:10]
    d = datetime.date(int(dt[:4]), int(dt[5:7]), int(dt[8:10]))
    return f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Track C2 Steps 5-7")
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
    # Step 5: Fork verification
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"STEP 5: FORK VERIFICATION (partial_mfe_ticks=0 should match baseline)")
    print(f"{'='*70}")

    # Baseline from original sweep
    t1 = time.time()
    bl_cycles = run_sim_filtered_orig(
        bars, step_dist=SD, hard_stop=HS,
        max_fades=MAX_FADES, max_levels=MAX_LEVELS,
        max_contract_size=MAX_CONTRACT_SIZE,
        signal_arrays=signals, filter_fn=ab_filter,
    )
    bl = compute_metrics(bl_cycles)
    print(f"  Original: {bl['n']} cyc | E[R]=${bl['er']:.2f} | PnL=${bl['pnl']:,.0f} ({time.time()-t1:.0f}s)")

    # Fork with partial disabled
    t1 = time.time()
    fork_cycles = run_sim_partial(
        bars, step_dist=SD, hard_stop=HS,
        max_fades=MAX_FADES, max_levels=MAX_LEVELS,
        max_contract_size=MAX_CONTRACT_SIZE,
        signal_arrays=signals, filter_fn=ab_filter,
        partial_mfe_ticks=0.0,
    )
    fk = compute_metrics(fork_cycles)
    print(f"  Fork:     {fk['n']} cyc | E[R]=${fk['er']:.2f} | PnL=${fk['pnl']:,.0f} ({time.time()-t1:.0f}s)")

    # Verify match
    match_n = bl["n"] == fk["n"]
    match_pnl = abs(bl["pnl"] - fk["pnl"]) < 0.01
    if match_n and match_pnl:
        print(f"  PASS -- identity verified (N match: {match_n}, PnL match: {match_pnl})")
    else:
        print(f"  FAIL -- N match: {match_n}, PnL match: {match_pnl}")
        print(f"  Delta N: {fk['n'] - bl['n']}, Delta PnL: ${fk['pnl'] - bl['pnl']:,.2f}")
        return

    # ===================================================================
    # Step 6: Live sim on 5 test weeks (each N in isolation)
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"STEP 6: LIVE SIM ON TEST WEEKS (partial profit)")
    print(f"{'='*70}")

    bl_weeks = weekly_breakdown(bl_cycles)

    header = (f"{'N pts':>5} {'Cycles':>7} {'WR':>5} {'SR':>5} {'BE%':>5} "
              f"{'E[R]':>8} {'PnL':>12} {'PF':>5} {'dE[R]%':>7} {'Wks+':>5}")
    print(f"\n  POOLED TEST WEEKS:")
    print(f"  {header}")
    print(f"  {'-'*75}")

    # Baseline for test weeks
    bl_test = [c for c in bl_cycles if get_week(c) in TEST_WEEKS]
    bl_test_m = compute_metrics(bl_test)
    print(f"  {'BL':>5} {bl_test_m['n']:>7} {bl_test_m['wr']:>4.0%} {bl_test_m['sr']:>4.0%} "
          f"{'':>5} ${bl_test_m['er']:>7.2f} ${bl_test_m['pnl']:>11,.0f} {bl_test_m['pf']:>5.2f}")

    best_n = None
    best_er = bl_test_m["er"]
    all_results = {}

    for n_pts in PARTIAL_N_PTS:
        n_ticks = n_pts / TICK_SIZE
        t1 = time.time()
        cycles = run_sim_partial(
            bars, step_dist=SD, hard_stop=HS,
            max_fades=MAX_FADES, max_levels=MAX_LEVELS,
            max_contract_size=MAX_CONTRACT_SIZE,
            signal_arrays=signals, filter_fn=ab_filter,
            partial_mfe_ticks=n_ticks,
        )
        # Filter to test weeks
        test_cyc = [c for c in cycles if get_week(c) in TEST_WEEKS]
        tm = compute_metrics(test_cyc)
        wkly = weekly_breakdown(test_cyc)

        # Count breakeven exits
        be_count = sum(1 for c in test_cyc if c["exit_type"] == "BREAKEVEN")
        be_pct = be_count / tm["n"] if tm["n"] > 0 else 0

        delta_pct = (tm["er"] - bl_test_m["er"]) / abs(bl_test_m["er"]) * 100 if bl_test_m["er"] != 0 else 0
        weeks_improved = sum(1 for wk in TEST_WEEKS
                             if wkly.get(wk, {"er": 0})["er"] > bl_weeks.get(wk, {"er": 0})["er"])

        print(f"  {n_pts:>5} {tm['n']:>7} {tm['wr']:>4.0%} {tm['sr']:>4.0%} {be_pct:>4.0%} "
              f"${tm['er']:>7.2f} ${tm['pnl']:>11,.0f} {tm['pf']:>5.2f} {delta_pct:>+6.1f}% {weeks_improved:>4}/5")

        all_results[n_pts] = {"metrics": tm, "weekly": wkly, "cycles": cycles,
                              "be_pct": be_pct, "delta_pct": delta_pct}

        if tm["er"] > best_er:
            best_er = tm["er"]
            best_n = n_pts

    # Per-week detail
    print(f"\n  PER-WEEK E[R] COMPARISON:")
    header = f"  {'Week':<10} {'Cat':>8} {'BL':>8}"
    for n_pts in PARTIAL_N_PTS:
        header += f" {'N='+str(n_pts):>8}"
    print(header)
    print(f"  {'-'*(10+9+9*len(PARTIAL_N_PTS))}")
    for wk in sorted(TEST_WEEKS.keys()):
        cat = TEST_WEEKS[wk]
        row = f"  {wk:<10} {cat:>8}"
        bl_er = bl_weeks.get(wk, {"er": 0})["er"]
        row += f" ${bl_er:>7.2f}"
        for n_pts in PARTIAL_N_PTS:
            wm = all_results[n_pts]["weekly"].get(wk, {"er": 0})
            row += f" ${wm['er']:>7.2f}"
        print(row)

    # Per-week PnL
    print(f"\n  PER-WEEK PnL COMPARISON:")
    header = f"  {'Week':<10} {'Cat':>8} {'BL':>10}"
    for n_pts in PARTIAL_N_PTS:
        header += f" {'N='+str(n_pts):>10}"
    print(header)
    print(f"  {'-'*(10+9+11*len(PARTIAL_N_PTS)+11)}")
    for wk in sorted(TEST_WEEKS.keys()):
        cat = TEST_WEEKS[wk]
        row = f"  {wk:<10} {cat:>8}"
        bl_pnl = bl_weeks.get(wk, {"pnl": 0})["pnl"]
        row += f" ${bl_pnl:>9,.0f}"
        for n_pts in PARTIAL_N_PTS:
            wm = all_results[n_pts]["weekly"].get(wk, {"pnl": 0})
            row += f" ${wm['pnl']:>9,.0f}"
        print(row)

    # ===================================================================
    # Step 7: Full P1 validation (best N from Step 6, and all candidates)
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"STEP 7: FULL P1 VALIDATION")
    print(f"{'='*70}")

    print(f"\n  {'N pts':>5} {'Cycles':>7} {'WR':>5} {'SR':>5} {'BE%':>5} "
          f"{'E[R]':>8} {'PnL':>12} {'PF':>5} {'dE[R]%':>7} {'Wks+':>6}")
    print(f"  {'-'*80}")
    print(f"  {'BL':>5} {bl['n']:>7} {bl['wr']:>4.0%} {bl['sr']:>4.0%} "
          f"{'':>5} ${bl['er']:>7.2f} ${bl['pnl']:>11,.0f} {bl['pf']:>5.2f}")

    all_weeks = sorted(bl_weeks.keys())
    full_results = {}

    for n_pts in PARTIAL_N_PTS:
        # Use full P1 cycles already computed (all_results has full cycles)
        full_cyc = all_results[n_pts]["cycles"]
        fm = compute_metrics(full_cyc)
        fwkly = weekly_breakdown(full_cyc)

        be_count = sum(1 for c in full_cyc if c["exit_type"] == "BREAKEVEN")
        be_pct = be_count / fm["n"] if fm["n"] > 0 else 0
        delta_pct = (fm["er"] - bl["er"]) / abs(bl["er"]) * 100 if bl["er"] != 0 else 0
        weeks_improved = sum(1 for wk in all_weeks
                             if fwkly.get(wk, {"er": 0})["er"] > bl_weeks.get(wk, {"er": 0})["er"])

        print(f"  {n_pts:>5} {fm['n']:>7} {fm['wr']:>4.0%} {fm['sr']:>4.0%} {be_pct:>4.0%} "
              f"${fm['er']:>7.2f} ${fm['pnl']:>11,.0f} {fm['pf']:>5.2f} "
              f"{delta_pct:>+6.1f}% {weeks_improved:>4}/{len(all_weeks)}")

        full_results[n_pts] = {"metrics": fm, "weekly": fwkly, "be_pct": be_pct,
                               "delta_pct": delta_pct}

    # Per-week for full P1
    print(f"\n  FULL P1 PER-WEEK E[R]:")
    header = f"  {'Week':<10} {'BL':>8}"
    for n_pts in PARTIAL_N_PTS:
        header += f" {'N='+str(n_pts):>8}"
    print(header)
    print(f"  {'-'*(10+9+9*len(PARTIAL_N_PTS))}")
    for wk in all_weeks:
        row = f"  {wk:<10}"
        bl_er = bl_weeks.get(wk, {"er": 0})["er"]
        row += f" ${bl_er:>7.2f}"
        for n_pts in PARTIAL_N_PTS:
            wm = full_results[n_pts]["weekly"].get(wk, {"er": 0})
            row += f" ${wm['er']:>7.2f}"
        print(row)

    # ===================================================================
    # Verdict
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"VERDICT")
    print(f"{'='*70}")

    for n_pts in PARTIAL_N_PTS:
        fr = full_results[n_pts]
        status = "PASS" if fr["delta_pct"] > 3 else "MARGINAL" if fr["delta_pct"] > 0 else "FAIL"
        print(f"  N={n_pts}: E[R]=${fr['metrics']['er']:.2f} ({fr['delta_pct']:+.1f}%) "
              f"BE={fr['be_pct']:.0%} -> {status}")

    # Save results
    out_path = OUTPUT_DIR / "c2-step7-full-p1-results.csv"
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["n_pts", "week", "cycles", "wr", "sr", "be_pct", "er", "pnl", "pf"])
        # Baseline aggregate
        w.writerow(["BL", "ALL", bl["n"], f"{bl['wr']:.4f}", f"{bl['sr']:.4f}",
                     "0", f"{bl['er']:.2f}", f"{bl['pnl']:.2f}", f"{bl['pf']:.2f}"])
        for wk in all_weeks:
            wm = bl_weeks.get(wk, {"n": 0, "wr": 0, "sr": 0, "er": 0, "pnl": 0, "pf": 0})
            w.writerow(["BL", wk, wm["n"], f"{wm['wr']:.4f}", f"{wm['sr']:.4f}",
                         "0", f"{wm['er']:.2f}", f"{wm['pnl']:.2f}", f"{wm['pf']:.2f}"])
        for n_pts in PARTIAL_N_PTS:
            fr = full_results[n_pts]
            fm = fr["metrics"]
            w.writerow([n_pts, "ALL", fm["n"], f"{fm['wr']:.4f}", f"{fm['sr']:.4f}",
                         f"{fr['be_pct']:.4f}", f"{fm['er']:.2f}", f"{fm['pnl']:.2f}",
                         f"{fm['pf']:.2f}"])
            for wk in all_weeks:
                wm = fr["weekly"].get(wk, {"n": 0, "wr": 0, "sr": 0, "er": 0, "pnl": 0, "pf": 0})
                w.writerow([n_pts, wk, wm["n"], f"{wm['wr']:.4f}", f"{wm['sr']:.4f}",
                             "", f"{wm['er']:.2f}", f"{wm['pnl']:.2f}", f"{wm['pf']:.2f}"])
    print(f"\nSaved: {out_path}")

    total_time = time.time() - t0
    print(f"\nTotal runtime: {total_time:.0f}s")


if __name__ == "__main__":
    main()
