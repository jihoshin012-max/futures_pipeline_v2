# archetype: rotational
"""
rotational-NQ-scale-detection-step15.py — Entry signals Steps 6+7.

Step 6: Full P1 validation with per-week breakdown.
Step 7: Sanity check — random filter at matching retention rate (10 seeds).

Prompt: rotational-NQ-prompt-entry-signals.md
Winner from Step 5: dr2 <= -0.40, dslope <= -2.0

Usage:
    python rotational-NQ-scale-detection-step15.py [--bar-file PATH]
"""
from __future__ import annotations

import csv
import importlib
import random
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
run_sim_filtered = _sweep.run_sim_filtered
TICK_SIZE = _sweep.TICK_SIZE
COMMISSION_PER_RT_MINI = _sweep.COMMISSION_PER_RT_MINI


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

OUTPUT_DIR = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")
NUM_RANDOM_SEEDS = 10


# ---------------------------------------------------------------------------
#  Signal precomputation
# ---------------------------------------------------------------------------

def precompute_signals(bars, bar_size, lookback):
    agg_bars, tick_to_agg = aggregate_to_ntick(bars, bar_size)
    regime = compute_regime_signals(agg_bars, lookback=lookback)
    entry = compute_entry_signals(agg_bars, lookback=lookback)
    return {
        "choppiness": map_signal_to_ticks(regime["choppiness"], tick_to_agg),
        "slope": map_signal_to_ticks(regime["slope"], tick_to_agg),
        "dr2": map_signal_to_ticks(entry["dr2"], tick_to_agg),
        "dslope": map_signal_to_ticks(entry["dslope"], tick_to_agg),
        "tick_to_agg": tick_to_agg,
        "warmup_ticks": {"dominant_scale": 0, "asymmetry": 0},
    }


# ---------------------------------------------------------------------------
#  Filter factories
# ---------------------------------------------------------------------------

def make_chop_filter(chop_max):
    def f(signals, i, direction, step_dist):
        chop = signals["choppiness"][i]
        if np.isnan(chop):
            return True
        return chop < chop_max
    return f


def make_entry_filter(chop_max, dr2_max, dslope_max):
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
        return True
    return f


def make_random_filter(chop_max, keep_rate, seed):
    """Chop filter + random rejection at the same retention rate."""
    rng = random.Random(seed)

    def f(signals, i, direction, step_dist):
        chop = signals["choppiness"][i]
        if np.isnan(chop):
            return True
        if chop >= chop_max:
            return False
        return rng.random() < keep_rate
    return f


# ---------------------------------------------------------------------------
#  Metrics
# ---------------------------------------------------------------------------

def compute_metrics(cycles):
    if not cycles:
        return {"n": 0, "wr": 0, "sr": 0, "er": 0, "pnl": 0}
    n = len(cycles)
    stops = sum(1 for c in cycles if c["exit_type"] == "HARD_STOP")
    net_pnls = []
    for c in cycles:
        comm = COMMISSION_PER_RT_MINI * max(c.get("max_position", 1), 1)
        net_pnls.append(c["pnl_ticks"] * 5.0 - comm)
    wins = sum(1 for p in net_pnls if p >= 0)
    return {"n": n, "wr": wins / n, "sr": stops / n,
            "er": sum(net_pnls) / n, "pnl": sum(net_pnls)}


def weekly_breakdown(cycles):
    import datetime
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
    parser = argparse.ArgumentParser(description="Entry Signals -- Steps 6+7")
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

    # =======================================================================
    # STEP 6: Full P1 validation
    # =======================================================================
    print(f"\n{'='*60}")
    print(f"STEP 6: Full P1 Validation — Per-Week Breakdown")
    print(f"{'='*60}")

    # Baseline (chop only)
    print(f"\nRunning baseline (chop<{CHOP_THRESHOLD})...")
    t1 = time.time()
    bl_cycles = run_sim_filtered(
        bars, step_dist=SD, hard_stop=HS,
        max_fades=MAX_FADES, max_levels=MAX_LEVELS,
        max_contract_size=MAX_CONTRACT_SIZE,
        signal_arrays=signals, filter_fn=make_chop_filter(CHOP_THRESHOLD),
    )
    bl = compute_metrics(bl_cycles)
    bl_weeks = weekly_breakdown(bl_cycles)
    print(f"  {bl['n']} cyc | {bl['wr']:.0%} WR | {bl['sr']:.0%} SR | "
          f"E[R]=${bl['er']:.2f} ({time.time()-t1:.0f}s)")

    # Entry filter
    print(f"\nRunning entry filter (chop<{CHOP_THRESHOLD} + dr2<={DR2_MAX} + dslope<={DSLOPE_MAX})...")
    t1 = time.time()
    ef_cycles = run_sim_filtered(
        bars, step_dist=SD, hard_stop=HS,
        max_fades=MAX_FADES, max_levels=MAX_LEVELS,
        max_contract_size=MAX_CONTRACT_SIZE,
        signal_arrays=signals,
        filter_fn=make_entry_filter(CHOP_THRESHOLD, DR2_MAX, DSLOPE_MAX),
    )
    ef = compute_metrics(ef_cycles)
    ef_weeks = weekly_breakdown(ef_cycles)
    retention = ef["n"] / bl["n"]
    print(f"  {ef['n']} cyc ({retention:.0%} ret) | {ef['wr']:.0%} WR | "
          f"{ef['sr']:.0%} SR | E[R]=${ef['er']:.2f} ({time.time()-t1:.0f}s)")

    # Per-week comparison
    all_weeks = sorted(set(list(bl_weeks.keys()) + list(ef_weeks.keys())))
    print(f"\n{'Week':<10} {'bl_N':>5} {'ef_N':>5} {'Ret':>5} "
          f"{'bl_WR':>6} {'ef_WR':>6} {'bl_SR':>6} {'ef_SR':>6} "
          f"{'bl_ER':>8} {'ef_ER':>8} {'dER':>8}")
    print(f"{'-'*85}")

    improvement_weeks = 0
    degradation_weeks = 0
    for wk in all_weeks:
        bm = bl_weeks.get(wk, {"n": 0, "wr": 0, "sr": 0, "er": 0})
        em = ef_weeks.get(wk, {"n": 0, "wr": 0, "sr": 0, "er": 0})
        ret = em["n"] / bm["n"] if bm["n"] > 0 else 0
        d_er = em["er"] - bm["er"] if em["n"] > 0 and bm["n"] > 0 else 0
        if d_er > 0:
            improvement_weeks += 1
        elif d_er < 0 and em["n"] > 0:
            degradation_weeks += 1
        print(f"{wk:<10} {bm['n']:>5} {em['n']:>5} {ret:>4.0%} "
              f"{bm['wr']:>5.0%} {em['wr']:>5.0%} "
              f"{bm['sr']:>5.0%} {em['sr']:>5.0%} "
              f"${bm['er']:>7.2f} ${em['er']:>7.2f} ${d_er:>+7.2f}")

    pct_improve = improvement_weeks / len(all_weeks) * 100 if all_weeks else 0
    pct_degrade = degradation_weeks / len(all_weeks) * 100 if all_weeks else 0
    delta_er_pct = (ef["er"] - bl["er"]) / bl["er"] * 100 if bl["er"] else 0

    print(f"\nSummary:")
    print(f"  Total weeks: {len(all_weeks)}")
    print(f"  Weeks improved: {improvement_weeks} ({pct_improve:.0f}%)")
    print(f"  Weeks degraded: {degradation_weeks} ({pct_degrade:.0f}%)")
    print(f"  Pooled E[R] improvement: ${ef['er'] - bl['er']:+.2f} ({delta_er_pct:+.1f}%)")

    # Kill criteria: P1 improvement < 5% E[R] over baseline -> stop
    if delta_er_pct < 5.0:
        print(f"\n  >>> KILL: P1 improvement {delta_er_pct:.1f}% < 5% threshold. STOP.")
        return False
    else:
        print(f"\n  >>> PASS: P1 improvement {delta_er_pct:.1f}% >= 5% threshold.")

    # =======================================================================
    # STEP 7: Sanity check — random filter
    # =======================================================================
    print(f"\n{'='*60}")
    print(f"STEP 7: Sanity Check — Random Filter ({NUM_RANDOM_SEEDS} seeds)")
    print(f"{'='*60}")
    print(f"Target retention: {retention:.2%} (matching entry filter)")

    random_ers = []
    for seed in range(NUM_RANDOM_SEEDS):
        t1 = time.time()
        rf = make_random_filter(CHOP_THRESHOLD, retention, seed)
        rand_cycles = run_sim_filtered(
            bars, step_dist=SD, hard_stop=HS,
            max_fades=MAX_FADES, max_levels=MAX_LEVELS,
            max_contract_size=MAX_CONTRACT_SIZE,
            signal_arrays=signals, filter_fn=rf,
        )
        rm = compute_metrics(rand_cycles)
        random_ers.append(rm["er"])
        print(f"  Seed {seed}: {rm['n']} cyc | E[R]=${rm['er']:.2f} ({time.time()-t1:.0f}s)")

    max_random = max(random_ers)
    mean_random = sum(random_ers) / len(random_ers)
    print(f"\n  Random filter: mean E[R]=${mean_random:.2f}, max=${max_random:.2f}")
    print(f"  Entry filter:  E[R]=${ef['er']:.2f}")
    print(f"  Margin over max random: ${ef['er'] - max_random:+.2f}")

    if ef["er"] > max_random:
        print(f"\n  >>> PASS: Entry filter outperforms all {NUM_RANDOM_SEEDS} random seeds.")
    else:
        print(f"\n  >>> FAIL: Entry filter does NOT outperform max random seed.")
        return False

    # --- Save ---
    out_csv = OUTPUT_DIR / "entry-signals-step6-7-validation.csv"
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["type", "label", "n", "wr", "sr", "er", "pnl"])
        w.writerow(["baseline", "chop_only", bl["n"],
                     f"{bl['wr']:.4f}", f"{bl['sr']:.4f}",
                     f"{bl['er']:.2f}", f"{bl['pnl']:.2f}"])
        w.writerow(["entry_filter", f"dr2<={DR2_MAX}+ds<={DSLOPE_MAX}",
                     ef["n"], f"{ef['wr']:.4f}", f"{ef['sr']:.4f}",
                     f"{ef['er']:.2f}", f"{ef['pnl']:.2f}"])
        for seed, er in enumerate(random_ers):
            w.writerow(["random", f"seed_{seed}", "", "", "", f"{er:.2f}", ""])
    print(f"\nSaved: {out_csv}")
    print(f"Total runtime: {time.time()-t0:.0f}s")
    return True


if __name__ == "__main__":
    passed = main()
    if passed:
        print("\n>>> Steps 6+7 PASS. Ready for Step 8 (handoff to bench).")
    else:
        print("\n*** EXPERIMENT TERMINATED ***")
