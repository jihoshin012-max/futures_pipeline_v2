# archetype: rotational
"""
rotational-NQ-scale-detection-step33.py — Track B2 Step 0: Select test weeks.

Run A+B config (chop < 0.10, dR2 <= -0.40, dSlope <= -2.0, fade_confirm < 0.40)
across all P1 weeks. Rank by filtered PnL. Select WEAKEST/LOW/MID/GOOD/BEST.

Uses official frozen params from rotational-NQ-fade-confirm-params-frozen.json.
Does NOT reimplement the A+B filter inline — uses the engine's signal precomputation
and the sweep's filter injection.

Prompt: rotational-NQ-prompt-ema-directional-b2.md Step 0
"""
from __future__ import annotations

import csv
import datetime
import importlib
import sys
import time
from collections import defaultdict
from pathlib import Path

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

# ---------------------------------------------------------------------------
#  Config — from frozen params
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

COMM = 3.50  # per RT per mini
TV = 5.0     # tick value

OUTPUT_DIR = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")
DATA_FILE = r"C:\Projects\futures_pipeline\data\NQ-1tick-calibration.csv"


# ---------------------------------------------------------------------------
#  Signal precomputation (same pattern as step32)
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
        "dr2": map_signal_to_ticks(entry["dr2"], tick_to_agg),
        "dslope": map_signal_to_ticks(entry["dslope"], tick_to_agg),
        "warmup_ticks": {"dominant_scale": 0, "asymmetry": 0},
        "prev_high": prev_high_tick,
        "prev_low": prev_low_tick,
        "prev_range": prev_range_tick,
        "last": last,
    }


# ---------------------------------------------------------------------------
#  A+B filter
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


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 70)
    print("Track B2 Step 0: Select test weeks")
    print("  Config: SD=10 HS=60 depth_1 MCS=2")
    print("  Filter: chop<0.10 + dR2<=-0.40 + dSlope<=-2.0 + fade_confirm<0.40")
    print("=" * 70)

    # Load data
    print("\nLoading P1 data...")
    t0 = time.time()
    bars = load_bars_extended(DATA_FILE)
    print(f"  {bars['n']} ticks ({time.time()-t0:.0f}s)")

    # Precompute signals
    print("\nPrecomputing signals...")
    t1 = time.time()
    signals = precompute_signals(bars, BAR_SIZE, LB)
    print(f"  Done ({time.time()-t1:.0f}s)")

    # Run sim with A+B filter
    print("\nRunning A+B filtered sim...")
    t1 = time.time()
    cycles = run_sim_filtered(
        bars, SD, HS, MAX_FADES, MAX_LEVELS, MAX_CONTRACT_SIZE,
        signal_arrays=signals, filter_fn=make_ab_filter()
    )
    print(f"  {len(cycles)} cycles ({time.time()-t1:.0f}s)")

    # Group by week
    weeks: dict[str, list[dict]] = defaultdict(list)
    for c in cycles:
        dt = c["seed_dt"][:10]
        d = datetime.date(int(dt[:4]), int(dt[5:7]), int(dt[8:10]))
        iso = d.isocalendar()
        wk = f"{iso[0]}-W{iso[1]:02d}"
        c["_net_pnl"] = c["pnl_ticks"] * TV - COMM * max(c.get("max_position", 1), 1)
        weeks[wk].append(c)

    # Compute per-week metrics
    print(f"\n{'Week':<10} {'Cycles':>6} {'WR':>5} {'SR':>5} {'E[R]':>10} {'PnL':>12}")
    print("-" * 55)

    week_stats = []
    for wk in sorted(weeks.keys()):
        cycs = weeks[wk]
        n = len(cycs)
        wins = sum(1 for c in cycs if c["_net_pnl"] >= 0)
        stops = sum(1 for c in cycs if c["exit_type"] == "HARD_STOP")
        total_pnl = sum(c["_net_pnl"] for c in cycs)
        er = total_pnl / n if n > 0 else 0
        wr = wins / n if n > 0 else 0
        sr = stops / n if n > 0 else 0
        week_stats.append({
            "week": wk, "cycles": n, "wr": wr, "sr": sr,
            "er": er, "pnl": total_pnl,
        })
        print(f"{wk:<10} {n:>6} {wr:>5.0%} {sr:>5.0%} ${er:>9.2f} ${total_pnl:>11,.0f}")

    # Rank by PnL and select 5 representative weeks
    ranked = sorted(week_stats, key=lambda w: w["pnl"])
    n_weeks = len(ranked)

    # Select: WEAKEST (rank 0), LOW (25th pct), MID (50th pct), GOOD (75th pct), BEST (last)
    indices = {
        "WEAKEST": 0,
        "LOW": max(1, n_weeks // 4),
        "MID": n_weeks // 2,
        "GOOD": min(n_weeks - 2, 3 * n_weeks // 4),
        "BEST": n_weeks - 1,
    }

    # Ensure no duplicates
    selected_idx = set()
    test_weeks = {}
    for cat in ["WEAKEST", "LOW", "MID", "GOOD", "BEST"]:
        idx = indices[cat]
        while idx in selected_idx:
            idx += 1
        selected_idx.add(idx)
        test_weeks[ranked[idx]["week"]] = cat

    print(f"\n{'='*55}")
    print("SELECTED TEST WEEKS (ranked by PnL)")
    print(f"{'='*55}")
    print(f"{'Cat':<10} {'Week':<10} {'Cycles':>6} {'WR':>5} {'SR':>5} {'E[R]':>10} {'PnL':>12}")
    print("-" * 60)
    for cat in ["WEAKEST", "LOW", "MID", "GOOD", "BEST"]:
        wk = [w for w, c in test_weeks.items() if c == cat][0]
        s = next(w for w in week_stats if w["week"] == wk)
        print(f"{cat:<10} {wk:<10} {s['cycles']:>6} {s['wr']:>5.0%} {s['sr']:>5.0%} "
              f"${s['er']:>9.2f} ${s['pnl']:>11,.0f}")

    # Print dict for copy-paste into next step
    print(f"\nTEST_WEEKS = {{")
    for cat in ["WEAKEST", "LOW", "MID", "GOOD", "BEST"]:
        wk = [w for w, c in test_weeks.items() if c == cat][0]
        print(f'    "{wk}": "{cat}",')
    print("}")

    # Totals
    total_cycles = sum(s["cycles"] for s in week_stats)
    total_pnl = sum(s["pnl"] for s in week_stats)
    overall_er = total_pnl / total_cycles if total_cycles > 0 else 0
    print(f"\nP1 totals: {total_cycles} cycles, E[R]=${overall_er:.2f}, PnL=${total_pnl:,.0f}")

    # Save per-week results
    out_path = OUTPUT_DIR / "b2-step0-weekly-ranking.csv"
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["week", "cycles", "wr", "sr", "er", "pnl", "category"])
        w.writeheader()
        for s in ranked:
            cat = test_weeks.get(s["week"], "")
            w.writerow({**s, "wr": round(s["wr"], 4), "sr": round(s["sr"], 4),
                         "er": round(s["er"], 2), "pnl": round(s["pnl"], 2),
                         "category": cat})
    print(f"\nSaved: {out_path}")
    print(f"Runtime: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
