# archetype: rotational
"""
rotational-NQ-scale-detection-step9.py -- Step 9: Full P1 validation.

Runs the choppiness filter on the ENTIRE P1 period (~18K cycles) to
validate that the 5-week test results are not overfit.

Baseline: SD=10 HS=60 depth_1 MCS=2.
Best filter candidates from Step 8.

Usage:
    python rotational-NQ-scale-detection-step9.py [--bar-file PATH]
"""
from __future__ import annotations

import csv
import importlib
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

_engine = importlib.import_module("rotational-NQ-scale-detection-engine")
load_bars_extended = _engine.load_bars_extended
aggregate_to_ntick = _engine.aggregate_to_ntick
compute_regime_signals = _engine.compute_regime_signals
map_signal_to_ticks = _engine.map_signal_to_ticks
append_audit = _engine.append_audit
append_journal = _engine.append_journal

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

FILTER_CANDIDATES = [
    ("chop<0.10", {"chop_max": 0.10, "slope_max": None}),
    ("chop<0.15", {"chop_max": 0.15, "slope_max": None}),
    ("chop<0.20+slope<3.0", {"chop_max": 0.20, "slope_max": 3.0}),
]


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

def precompute_chop_signals(bars, bar_size, lookback):
    n_ticks = bars["n"]
    agg_bars, tick_to_agg = aggregate_to_ntick(bars, bar_size)
    features = compute_regime_signals(agg_bars, lookback=lookback)
    chop_ticks = map_signal_to_ticks(features["choppiness"], tick_to_agg)
    slope_ticks = map_signal_to_ticks(features["slope"], tick_to_agg)
    return {
        "choppiness": chop_ticks,
        "slope": slope_ticks,
        "warmup_ticks": {"dominant_scale": 0, "asymmetry": 0},
    }


def make_chop_filter(chop_max, slope_max=None):
    def filter_fn(signals, i, direction, step_dist):
        chop = signals["choppiness"][i]
        if np.isnan(chop):
            return True
        if chop >= chop_max:
            return False
        if slope_max is not None:
            slope = signals["slope"][i]
            if np.isnan(slope):
                return True
            if abs(slope) >= slope_max:
                return False
        return True
    return filter_fn


def compute_metrics(cycles):
    if not cycles:
        return {"cycles": 0, "wins": 0, "wr": 0, "stops": 0, "sr": 0,
                "pnl": 0.0, "er": 0.0}
    n = len(cycles)
    stops = sum(1 for c in cycles if c["exit_type"] == "HARD_STOP")
    net_pnls = []
    for c in cycles:
        comm = COMMISSION_PER_RT_MINI * max(c.get("max_position", 1), 1)
        net_pnls.append(c["pnl_ticks"] * 5.0 - comm)
    wins = sum(1 for p in net_pnls if p >= 0)
    total_pnl = sum(net_pnls)
    return {
        "cycles": n, "wins": wins, "wr": wins / n,
        "stops": stops, "sr": stops / n,
        "pnl": total_pnl, "er": total_pnl / n,
    }


def weekly_breakdown(cycles):
    """Group cycles by ISO week and compute per-week metrics."""
    import datetime
    weeks = defaultdict(list)
    for c in cycles:
        dt = c["seed_dt"][:10]
        d = datetime.date(int(dt[:4]), int(dt[5:7]), int(dt[8:10]))
        wk = f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"
        weeks[wk].append(c)

    results = {}
    for wk in sorted(weeks.keys()):
        m = compute_metrics(weeks[wk])
        results[wk] = m
    return results


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Scale Detection -- Step 9: Full P1")
    parser.add_argument("--bar-file", type=str,
                        default=r"C:\Projects\futures_pipeline\data\NQ-1tick-calibration.csv")
    args = parser.parse_args()

    print(f"Loading bars from {args.bar_file}...")
    t0 = time.time()
    bars = load_bars_extended(args.bar_file)
    print(f"Loaded {bars['n']} bars in {time.time()-t0:.1f}s")

    # Precompute signals on FULL dataset
    print(f"\nPrecomputing choppiness signals (lb={LB}, bar_size={BAR_SIZE})...")
    t1 = time.time()
    signals = precompute_chop_signals(bars, BAR_SIZE, LB)
    print(f"  done ({time.time()-t1:.1f}s)")

    # Baseline (no filter) -- full P1
    print(f"\nRunning BASELINE (full P1)...")
    t1 = time.time()
    baseline_cycles = run_sim_filtered(
        bars, step_dist=SD, hard_stop=HS,
        max_fades=MAX_FADES, max_levels=MAX_LEVELS,
        max_contract_size=MAX_CONTRACT_SIZE,
    )
    bl = compute_metrics(baseline_cycles)
    print(f"  {bl['cycles']} cyc | {bl['wr']:.0%} WR | {bl['sr']:.0%} SR | "
          f"${bl['pnl']:,.0f} | E[R]=${bl['er']:.2f} ({time.time()-t1:.0f}s)")

    bl_weekly = weekly_breakdown(baseline_cycles)

    all_results = [{"filter": "baseline", **bl}]

    # Filtered runs
    for cand_name, params in FILTER_CANDIDATES:
        print(f"\nRunning {cand_name}...")
        t1 = time.time()
        filter_fn = make_chop_filter(params["chop_max"], params["slope_max"])
        filtered_cycles = run_sim_filtered(
            bars, step_dist=SD, hard_stop=HS,
            max_fades=MAX_FADES, max_levels=MAX_LEVELS,
            max_contract_size=MAX_CONTRACT_SIZE,
            signal_arrays=signals, filter_fn=filter_fn,
        )
        fl = compute_metrics(filtered_cycles)
        retention = fl["cycles"] / bl["cycles"] if bl["cycles"] > 0 else 0
        delta_pnl = fl["pnl"] - bl["pnl"]
        print(f"  {fl['cycles']} cyc ({retention:.0%} ret) | {fl['wr']:.0%} WR | "
              f"{fl['sr']:.0%} SR | ${fl['pnl']:,.0f} (d${delta_pnl:+,.0f}) | "
              f"E[R]=${fl['er']:.2f} ({time.time()-t1:.0f}s)")

        fl_weekly = weekly_breakdown(filtered_cycles)

        all_results.append({"filter": cand_name, "retention": retention,
                           "delta_pnl": delta_pnl, **fl})

        # Per-week comparison
        print(f"\n  Per-week: {cand_name}")
        print(f"  {'Week':>8} {'BL Cyc':>7} {'FL Cyc':>7} {'Ret':>5} "
              f"{'BL SR':>6} {'FL SR':>6} {'BL PnL':>10} {'FL PnL':>10} {'dPnL':>10}")
        print(f"  {'-'*80}")

        neg_weeks_bl = 0
        neg_weeks_fl = 0
        for wk in sorted(bl_weekly.keys()):
            bw = bl_weekly[wk]
            fw = fl_weekly.get(wk, {"cycles": 0, "sr": 0, "pnl": 0})
            ret = fw["cycles"] / bw["cycles"] if bw["cycles"] > 0 else 0
            dpnl = fw["pnl"] - bw["pnl"]
            if bw["pnl"] < 0: neg_weeks_bl += 1
            if fw["pnl"] < 0: neg_weeks_fl += 1
            print(f"  {wk:>8} {bw['cycles']:>7} {fw['cycles']:>7} {ret:>5.0%} "
                  f"{bw['sr']:>6.0%} {fw['sr']:>6.0%} "
                  f"${bw['pnl']:>9,.0f} ${fw['pnl']:>9,.0f} ${dpnl:>+9,.0f}")

        print(f"\n  Negative weeks: baseline={neg_weeks_bl}, filtered={neg_weeks_fl}")

    # --- Summary ---
    print(f"\n{'='*70}")
    print(f"STEP 9 SUMMARY -- Full P1 validation")
    print(f"{'='*70}")
    print(f"\n  {'Filter':>25} {'Cyc':>7} {'Ret':>5} {'WR':>5} {'SR':>5} "
          f"{'Total PnL':>12} {'E[R]':>8}")
    print(f"  {'-'*70}")
    for r in all_results:
        ret_str = f"{r.get('retention', 1.0):.0%}" if r["filter"] != "baseline" else "--"
        print(f"  {r['filter']:>25} {r['cycles']:>7} {ret_str:>5} {r['wr']:.0%}  "
              f"{r['sr']:.0%}  ${r['pnl']:>11,.0f} ${r['er']:>7.2f}")

    # --- Save ---
    out_dir = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "step9-full-p1-results.csv"
    fields = ["filter", "cycles", "wins", "wr", "stops", "sr", "pnl", "er",
              "retention", "delta_pnl"]
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in all_results:
            w.writerow({k: round(v, 4) if isinstance(v, float) else v
                        for k, v in r.items() if k in fields})
    print(f"\nResults saved: {out_path}")

    # --- Audit + Journal ---
    total = time.time() - t0

    # Summary for journal
    table_lines = []
    table_lines.append("| Filter | Cycles | Ret | WR | SR | Total PnL | E[R] |")
    table_lines.append("|---|---|---|---|---|---|---|")
    for r in all_results:
        ret_str = f"{r.get('retention', 1.0):.0%}" if r["filter"] != "baseline" else "--"
        table_lines.append(
            f"| {r['filter']} | {r['cycles']:,} | {ret_str} | {r['wr']:.0%} | "
            f"{r['sr']:.0%} | ${r['pnl']:,.0f} | ${r['er']:.2f} |")

    append_audit("STEP_9_COMPLETE",
                 f"Full P1 validation SD=10 HS=60 with choppiness filter. "
                 f"Baseline: {bl['cycles']} cyc, ${bl['pnl']:,.0f}. "
                 f"Runtime: {total:.0f}s.")

    journal_text = f"""## Step 9: Full P1 validation

**Method:** Choppiness filter on entire P1 period (not just 5 test weeks).
**Baseline:** SD=10 HS=60 depth_1 MCS=2
**Filter lookback:** lb={LB} on {BAR_SIZE}-tick agg bars

### Full P1 results

{chr(10).join(table_lines)}

### Per-week details

See console output and `step9-full-p1-results.csv`.

Runtime: {total:.0f}s
"""
    append_journal(journal_text)

    print(f"\nStep 9 complete: {total:.0f}s ({total/60:.1f}m)")


if __name__ == "__main__":
    main()
