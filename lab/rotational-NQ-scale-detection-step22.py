# archetype: rotational
"""
rotational-NQ-scale-detection-step22.py — Track B Steps 6+7: Full P1 validation
and sanity check.

Step 6: Per-week breakdown for both candidates across ALL 12 P1 weeks.
Step 7: Random filter comparison at matching retention rates (10 seeds).

Prompt: rotational-NQ-prompt-fade-confirmation.md
Depends on: Step 5 (step21.py) — live sim results.
"""
from __future__ import annotations

import csv
import datetime
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


# ---------------------------------------------------------------------------
#  Signal precomputation (from step21)
# ---------------------------------------------------------------------------

def precompute_signals(bars, bar_size, lookback):
    agg_bars, tick_to_agg = aggregate_to_ntick(bars, bar_size)
    regime = compute_regime_signals(agg_bars, lookback=lookback)
    entry = compute_entry_signals(agg_bars, lookback=lookback)

    n_agg = agg_bars["n"]
    a_high = agg_bars["high"]
    a_low = agg_bars["low"]
    a_open = agg_bars["open"]
    a_close = agg_bars["last"]
    a_tsec = agg_bars["time_sec"]
    a_dint = agg_bars["date_int"]

    prev_high = np.full(n_agg, np.nan, dtype=np.float64)
    prev_low = np.full(n_agg, np.nan, dtype=np.float64)
    prev_range = np.full(n_agg, np.nan, dtype=np.float64)
    prev_displacement = np.full(n_agg, np.nan, dtype=np.float64)
    prev_duration = np.full(n_agg, np.nan, dtype=np.float64)

    for ai in range(1, n_agg):
        prev = ai - 1
        prev_high[ai] = float(a_high[prev])
        prev_low[ai] = float(a_low[prev])
        rng = float(a_high[prev]) - float(a_low[prev])
        prev_range[ai] = rng if rng > 0 else np.nan
        prev_displacement[ai] = float(a_close[prev]) - float(a_open[prev])
        if int(a_dint[ai]) == int(a_dint[prev]):
            dur = float(a_tsec[ai]) - float(a_tsec[prev])
            prev_duration[ai] = dur if dur > 0 else np.nan

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
        "prev_disp": prev_displacement[tick_to_agg],
        "prev_dur": prev_duration[tick_to_agg],
        "last": bars["last"],
    }


# ---------------------------------------------------------------------------
#  Filters
# ---------------------------------------------------------------------------

def make_track_a_filter(chop_max, dr2_max, dslope_max):
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
        return True
    return f


def make_fade_confirm_filter(chop_max, dr2_max, dslope_max, fc_max):
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


def make_combined_filter(chop_max, dr2_max, dslope_max, fc_max, fs_max):
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
        if fc >= fc_max: return False
        prev_dur = signals["prev_dur"][i]
        if np.isnan(prev_dur): return True
        displacement = float(signals["prev_disp"][i])
        speed = displacement / prev_dur
        fade_speed = speed if direction == 1 else -speed
        return fade_speed < fs_max
    return f


def make_random_filter(pass_rate, seed):
    rng = random.Random(seed)
    def f(signals, i, direction, step_dist):
        return rng.random() < pass_rate
    return f


# ---------------------------------------------------------------------------
#  Metrics
# ---------------------------------------------------------------------------

def compute_metrics(cycles):
    if not cycles:
        return {"n": 0, "wr": 0.0, "sr": 0.0, "er": 0.0, "pnl": 0.0}
    n = len(cycles)
    stops = sum(1 for c in cycles if c["exit_type"] == "HARD_STOP")
    net_pnls = [c["pnl_ticks"] * 5.0 - COMMISSION_PER_RT_MINI * max(c.get("max_position", 1), 1)
                for c in cycles]
    wins = sum(1 for p in net_pnls if p >= 0)
    return {"n": n, "wr": wins / n, "sr": stops / n,
            "er": sum(net_pnls) / n, "pnl": sum(net_pnls)}


def weekly_breakdown(cycles):
    weeks = defaultdict(list)
    for c in cycles:
        dt = c["seed_dt"][:10]
        d = datetime.date(int(dt[:4]), int(dt[5:7]), int(dt[8:10]))
        wk = f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"
        weeks[wk].append(c)
    return dict(sorted(weeks.items()))


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--bar-file", type=str,
                        default=r"C:\Projects\futures_pipeline\data\NQ-1tick-calibration.csv")
    args = parser.parse_args()

    print(f"Loading bars from {args.bar_file}...")
    t0 = time.time()
    bars = load_bars_extended(args.bar_file)
    print(f"Loaded {bars['n']} bars in {time.time()-t0:.1f}s")

    print(f"\nPrecomputing signals...")
    t1 = time.time()
    signals = precompute_signals(bars, BAR_SIZE, LB)
    print(f"  done ({time.time()-t1:.1f}s)")

    # ===================================================================
    # Step 6: Full P1 per-week breakdown
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"STEP 6: Full P1 validation — per-week breakdown")
    print(f"{'='*70}")

    # Track A baseline
    print(f"\nRunning Track A baseline...")
    t1 = time.time()
    bl_cyc = run_sim_filtered(
        bars, step_dist=SD, hard_stop=HS,
        max_fades=MAX_FADES, max_levels=MAX_LEVELS,
        max_contract_size=MAX_CONTRACT_SIZE,
        signal_arrays=signals,
        filter_fn=make_track_a_filter(CHOP_THRESHOLD, DR2_MAX, DSLOPE_MAX),
    )
    bl_m = compute_metrics(bl_cyc)
    bl_weeks = {wk: compute_metrics(wc) for wk, wc in weekly_breakdown(bl_cyc).items()}
    print(f"  {bl_m['n']} cyc | E[R]=${bl_m['er']:.2f} ({time.time()-t1:.0f}s)")

    # Candidates
    candidates = [
        ("fc<0.4", make_fade_confirm_filter(CHOP_THRESHOLD, DR2_MAX, DSLOPE_MAX, 0.4)),
        ("fc<0.7+fs<0.3", make_combined_filter(CHOP_THRESHOLD, DR2_MAX, DSLOPE_MAX, 0.7, 0.3)),
    ]

    cand_results = {}
    for label, filt in candidates:
        print(f"\nRunning {label}...")
        t1 = time.time()
        cyc = run_sim_filtered(
            bars, step_dist=SD, hard_stop=HS,
            max_fades=MAX_FADES, max_levels=MAX_LEVELS,
            max_contract_size=MAX_CONTRACT_SIZE,
            signal_arrays=signals, filter_fn=filt,
        )
        m = compute_metrics(cyc)
        wk_data = {wk: compute_metrics(wc) for wk, wc in weekly_breakdown(cyc).items()}
        cand_results[label] = {"metrics": m, "weeks": wk_data, "cycles": cyc}
        ret = m["n"] / bl_m["n"]
        print(f"  {m['n']} cyc ({ret:.0%} ret) | {m['wr']:.0%} WR | {m['sr']:.0%} SR | "
              f"E[R]=${m['er']:.2f} | PnL=${m['pnl']:,.0f} ({time.time()-t1:.0f}s)")

    # Per-week tables
    all_weeks = sorted(bl_weeks.keys())
    for label in cand_results:
        cr = cand_results[label]
        print(f"\n  {label} — per-week:")
        print(f"    {'Week':<10} {'BL_N':>5} {'F_N':>5} {'Ret':>5} "
              f"{'BL_ER':>8} {'F_ER':>8} {'dER':>8} {'dER%':>6}")
        print(f"    {'-'*62}")
        improved = 0
        degraded = 0
        for wk in all_weeks:
            bm = bl_weeks.get(wk, {"n": 0, "er": 0})
            fm = cr["weeks"].get(wk, {"n": 0, "er": 0})
            d = fm["er"] - bm["er"] if fm["n"] > 0 else 0
            dpct = (d / bm["er"] * 100) if bm["er"] != 0 else 0
            ret = fm["n"] / bm["n"] if bm["n"] > 0 else 0
            if d >= 0:
                improved += 1
            else:
                degraded += 1
            print(f"    {wk:<10} {bm['n']:>5} {fm['n']:>5} {ret:>4.0%} "
                  f"${bm['er']:>7.2f} ${fm['er']:>7.2f} ${d:>7.2f} {dpct:>5.1f}%")
        print(f"    Improved: {improved}/12 | Degraded: {degraded}/12")

    # ===================================================================
    # Step 7: Random filter comparison
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"STEP 7: Sanity check — random filter at matching retention")
    print(f"{'='*70}")

    for label in cand_results:
        cr = cand_results[label]
        target_retention = cr["metrics"]["n"] / bl_m["n"]
        # Random filter pass rate needs to be calibrated
        # The actual pass rate that produces the target retention
        # Start with target_retention as pass_rate and adjust
        print(f"\n  {label}: target retention = {target_retention:.2%} "
              f"({cr['metrics']['n']} / {bl_m['n']})")

        random_ers = []
        random_cycles = []
        for seed in range(10):
            cyc = run_sim_filtered(
                bars, step_dist=SD, hard_stop=HS,
                max_fades=MAX_FADES, max_levels=MAX_LEVELS,
                max_contract_size=MAX_CONTRACT_SIZE,
                signal_arrays=signals,
                filter_fn=make_random_filter(target_retention, seed),
            )
            m = compute_metrics(cyc)
            random_ers.append(m["er"])
            random_cycles.append(m["n"])

        avg_er = np.mean(random_ers)
        max_er = max(random_ers)
        min_er = min(random_ers)
        avg_n = np.mean(random_cycles)

        print(f"  Random (10 seeds): avg E[R]=${avg_er:.2f} | "
              f"min=${min_er:.2f} | max=${max_er:.2f} | avg N={avg_n:.0f}")
        print(f"  {label}: E[R]=${cr['metrics']['er']:.2f}")
        margin = cr["metrics"]["er"] - max_er
        beats_all = cr["metrics"]["er"] > max_er
        print(f"  Beats all random seeds: {beats_all} (margin=${margin:.2f})")
        if beats_all:
            print(f"  >>> SANITY CHECK PASS")
        else:
            print(f"  >>> SANITY CHECK FAIL")

    # ===================================================================
    # Kill gate: P1 improvement >= 5%
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"KILL GATE: P1 improvement >= 5% E[R] over Track A baseline")
    print(f"{'='*70}")
    for label in cand_results:
        cr = cand_results[label]
        delta_pct = (cr["metrics"]["er"] - bl_m["er"]) / bl_m["er"] * 100
        passed = delta_pct >= 5.0
        print(f"  {label}: E[R] ${bl_m['er']:.2f} -> ${cr['metrics']['er']:.2f} "
              f"(+{delta_pct:.1f}%) {'PASS' if passed else 'FAIL'}")

    total = time.time() - t0
    print(f"\nTotal runtime: {total:.0f}s ({total/60:.1f}m)")


if __name__ == "__main__":
    main()
