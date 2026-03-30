# archetype: rotational
"""
rotational-NQ-scale-detection-step14.py — Entry signals Step 5.

Step 5: Live sim (5 test weeks).
Wire dr2 + dslope into the actual sweep as entry gates alongside chop filter.
Compare against chop-only baseline. Per-week breakdown.

Top candidate from Step 4: dr2 <= -0.40, dslope <= -2.0

Prompt: rotational-NQ-prompt-entry-signals.md

Usage:
    python rotational-NQ-scale-detection-step14.py [--bar-file PATH]
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

WEEK_ORDER = ["W39", "W43", "W46", "W47", "W50"]
WEEK_CATS = {"W39": "LOW", "W43": "WEAKEST", "W46": "GOOD",
             "W47": "BEST", "W50": "MID"}
WEEK_DATES = {
    "W39": (20250922, 20250926),
    "W43": (20251020, 20251024),
    "W46": (20251110, 20251114),
    "W47": (20251117, 20251121),
    "W50": (20251208, 20251212),
}

OUTPUT_DIR = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")

# Combined filter candidates from Step 4 (top 3 + baseline)
FILTER_CONFIGS = [
    ("chop_only", {"chop_max": 0.10, "dr2_max": None, "dslope_max": None}),
    ("dr2<=-0.20+ds<=-2.0", {"chop_max": 0.10, "dr2_max": -0.20, "dslope_max": -2.0}),
    ("dr2<=-0.30+ds<=-2.0", {"chop_max": 0.10, "dr2_max": -0.30, "dslope_max": -2.0}),
    ("dr2<=-0.40+ds<=-2.0", {"chop_max": 0.10, "dr2_max": -0.40, "dslope_max": -2.0}),
]


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

def make_entry_filter(chop_max, dr2_max=None, dslope_max=None):
    def filter_fn(signals, i, direction, step_dist):
        chop = signals["choppiness"][i]
        if np.isnan(chop):
            return True
        if chop >= chop_max:
            return False
        if dr2_max is not None:
            dr2 = signals["dr2"][i]
            if np.isnan(dr2):
                return True
            if dr2 > dr2_max:
                return False
        if dslope_max is not None:
            ds = signals["dslope"][i]
            if np.isnan(ds):
                return True
            if ds > dslope_max:
                return False
        return True
    return filter_fn


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


def weekly_cycles(cycles):
    weeks = defaultdict(list)
    for c in cycles:
        dt = c["seed_dt"][:10].replace("-", "")
        d = int(dt)
        for wk, (ds, de) in WEEK_DATES.items():
            if ds <= d <= de:
                weeks[wk].append(c)
                break
    return weeks


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Entry Signals -- Step 5: Live Sim")
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

    # --- Run all filter configs ---
    all_results = {}
    for name, params in FILTER_CONFIGS:
        print(f"\n{'='*60}")
        print(f"Running: {name}")
        print(f"{'='*60}")
        t1 = time.time()
        filter_fn = make_entry_filter(
            params["chop_max"], params.get("dr2_max"), params.get("dslope_max"))
        cycles = run_sim_filtered(
            bars, step_dist=SD, hard_stop=HS,
            max_fades=MAX_FADES, max_levels=MAX_LEVELS,
            max_contract_size=MAX_CONTRACT_SIZE,
            signal_arrays=signals, filter_fn=filter_fn,
        )
        m = compute_metrics(cycles)
        print(f"  Total: {m['n']} cyc | {m['wr']:.0%} WR | {m['sr']:.0%} SR | "
              f"E[R]=${m['er']:.2f} | ${m['pnl']:,.0f} ({time.time()-t1:.0f}s)")

        # Per-week
        wk_data = weekly_cycles(cycles)
        wk_metrics = {}
        for wk in WEEK_ORDER:
            wm = compute_metrics(wk_data.get(wk, []))
            wk_metrics[wk] = wm

        all_results[name] = {"total": m, "weeks": wk_metrics, "cycles": cycles}

    # --- Comparison table ---
    bl = all_results["chop_only"]

    print(f"\n{'='*60}")
    print(f"STEP 5 COMPARISON: Live Sim vs Chop-Only Baseline")
    print(f"{'='*60}")

    # Pooled comparison
    print(f"\nPOOLED (5 test weeks):")
    print(f"{'Filter':<25} {'N':>5} {'Ret':>6} {'WR':>6} {'SR':>6} "
          f"{'E[R]':>8} {'dER':>8} {'PnL':>10}")
    print(f"{'-'*78}")
    for name, _ in FILTER_CONFIGS:
        r = all_results[name]
        m = r["total"]
        retention = m["n"] / bl["total"]["n"] if bl["total"]["n"] else 0
        d_er = m["er"] - bl["total"]["er"]
        print(f"{name:<25} {m['n']:>5} {retention:>5.0%} {m['wr']:>5.0%} "
              f"{m['sr']:>5.0%} ${m['er']:>7.2f} ${d_er:>+7.2f} ${m['pnl']:>9,.0f}")

    # Per-week comparison for each filter
    for name, _ in FILTER_CONFIGS:
        if name == "chop_only":
            continue
        r = all_results[name]
        print(f"\n{name} — Per-week:")
        print(f"  {'Week':<6} {'Cat':<8} {'N':>5} {'WR':>6} {'SR':>6} "
              f"{'E[R]':>8} {'bl_ER':>8} {'dER':>8} {'dPnL':>10}")
        print(f"  {'-'*72}")
        for wk in WEEK_ORDER:
            m_f = r["weeks"][wk]
            m_bl = bl["weeks"][wk]
            cat = WEEK_CATS[wk]
            d_er = m_f["er"] - m_bl["er"] if m_f["n"] > 0 else 0
            d_pnl = m_f["pnl"] - m_bl["pnl"]
            if m_f["n"] > 0:
                print(f"  {wk:<6} {cat:<8} {m_f['n']:>5} {m_f['wr']:>5.0%} "
                      f"{m_f['sr']:>5.0%} ${m_f['er']:>7.2f} "
                      f"${m_bl['er']:>7.2f} ${d_er:>+7.2f} ${d_pnl:>+9,.0f}")
            else:
                print(f"  {wk:<6} {cat:<8}     0    --     --       -- "
                      f"${m_bl['er']:>7.2f}       --         --")

    # --- Retroactive vs Live comparison ---
    print(f"\n{'='*60}")
    print(f"RETROACTIVE vs LIVE comparison (dr2<=-0.40+ds<=-2.0)")
    print(f"{'='*60}")
    retro_n = 2705; retro_er = 70.30  # from step 4
    live = all_results.get("dr2<=-0.40+ds<=-2.0", {}).get("total", {})
    if live:
        print(f"  Retroactive: {retro_n} cyc, E[R]=${retro_er:.2f}")
        print(f"  Live sim:    {live['n']} cyc, E[R]=${live['er']:.2f}")
        diff = live["er"] - retro_er
        print(f"  Delta:       {live['n'] - retro_n} cyc, ${diff:+.2f}/cyc")
        if abs(diff) < 5.0:
            print(f"  Verdict: CONSISTENT (delta < $5)")
        else:
            print(f"  Verdict: DIVERGENT — investigate")

    # --- Save ---
    out_csv = OUTPUT_DIR / "entry-signals-step5-livesim.csv"
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["filter", "scope", "n", "retention", "wr", "sr", "er",
                     "delta_er", "pnl"])
        for name, _ in FILTER_CONFIGS:
            r = all_results[name]
            m = r["total"]
            ret = m["n"] / bl["total"]["n"] if bl["total"]["n"] else 0
            d_er = m["er"] - bl["total"]["er"]
            w.writerow([name, "pooled", m["n"], f"{ret:.4f}",
                        f"{m['wr']:.4f}", f"{m['sr']:.4f}",
                        f"{m['er']:.2f}", f"{d_er:.2f}", f"{m['pnl']:.2f}"])
            for wk in WEEK_ORDER:
                wm = r["weeks"][wk]
                wbl = bl["weeks"][wk]
                d = wm["er"] - wbl["er"] if wm["n"] > 0 else 0
                w.writerow([name, wk, wm["n"], "",
                            f"{wm['wr']:.4f}" if wm["n"] else "",
                            f"{wm['sr']:.4f}" if wm["n"] else "",
                            f"{wm['er']:.2f}" if wm["n"] else "",
                            f"{d:.2f}", f"{wm['pnl']:.2f}"])
    print(f"\nSaved: {out_csv}")
    print(f"Total runtime: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
