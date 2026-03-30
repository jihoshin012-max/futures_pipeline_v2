# archetype: rotational
"""
rotational-NQ-scale-detection-step16.py — Track B Step 0: Select test weeks.

Runs Track A's winning config (SD=10 HS=60 depth_1 MCS=2 + chop<0.10
+ dR2<=-0.40 + dSlope<=-2.0) across all P1 weeks, ranks by filtered PnL,
and selects WEAKEST/LOW/MID/GOOD/BEST for Track B testing.

Prompt: rotational-NQ-prompt-fade-confirmation.md
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
run_sim_filtered = _sweep.run_sim_filtered
TICK_SIZE = _sweep.TICK_SIZE
COMMISSION_PER_RT_MINI = _sweep.COMMISSION_PER_RT_MINI


# ---------------------------------------------------------------------------
#  Config — Track A's winning configuration
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
#  Signal precomputation (same as step15)
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
#  Filters (same as step15)
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


# ---------------------------------------------------------------------------
#  Metrics
# ---------------------------------------------------------------------------

def compute_metrics(cycles):
    if not cycles:
        return {"n": 0, "wr": 0.0, "sr": 0.0, "er": 0.0, "pnl": 0.0}
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
    parser = argparse.ArgumentParser(description="Track B Step 0 — Select test weeks")
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
    # Run Track A baseline (chop only) and Track A filtered (chop + dR2 + dSlope)
    # =======================================================================
    print(f"\n{'='*60}")
    print(f"STEP 0: Track A config — full P1 per-week breakdown")
    print(f"Config: SD={SD} HS={HS} depth_1 MCS={MAX_CONTRACT_SIZE}")
    print(f"Filter: chop<{CHOP_THRESHOLD} + dR2<={DR2_MAX} + dSlope<={DSLOPE_MAX}")
    print(f"{'='*60}")

    # --- Chop-only baseline ---
    print(f"\nRunning chop-only baseline...")
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
          f"E[R]=${bl['er']:.2f} | PnL=${bl['pnl']:,.0f} ({time.time()-t1:.0f}s)")

    # --- Track A filtered ---
    print(f"\nRunning Track A filter (chop + dR2 + dSlope)...")
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
    retention = ef["n"] / bl["n"] if bl["n"] > 0 else 0
    print(f"  {ef['n']} cyc ({retention:.0%} ret) | {ef['wr']:.0%} WR | "
          f"{ef['sr']:.0%} SR | E[R]=${ef['er']:.2f} | PnL=${ef['pnl']:,.0f} ({time.time()-t1:.0f}s)")

    # =======================================================================
    # Per-week breakdown — rank by Track A filtered PnL
    # =======================================================================
    all_weeks = sorted(ef_weeks.keys())

    print(f"\n{'Week':<10} {'bl_N':>5} {'ef_N':>5} {'Ret':>5} "
          f"{'bl_WR':>6} {'ef_WR':>6} {'bl_SR':>6} {'ef_SR':>6} "
          f"{'bl_PnL':>10} {'ef_PnL':>10} {'ef_ER':>8}")
    print(f"{'-'*92}")

    week_data = []
    for wk in all_weeks:
        bm = bl_weeks.get(wk, {"n": 0, "wr": 0, "sr": 0, "er": 0, "pnl": 0})
        em = ef_weeks.get(wk, {"n": 0, "wr": 0, "sr": 0, "er": 0, "pnl": 0})
        ret = em["n"] / bm["n"] if bm["n"] > 0 else 0
        print(f"{wk:<10} {bm['n']:>5} {em['n']:>5} {ret:>4.0%} "
              f"{bm['wr']:>5.0%} {em['wr']:>5.0%} "
              f"{bm['sr']:>5.0%} {em['sr']:>5.0%} "
              f"${bm['pnl']:>9,.0f} ${em['pnl']:>9,.0f} ${em['er']:>7.2f}")
        week_data.append({
            "week": wk, "bl_n": bm["n"], "ef_n": em["n"],
            "retention": ret,
            "bl_wr": bm["wr"], "ef_wr": em["wr"],
            "bl_sr": bm["sr"], "ef_sr": em["sr"],
            "bl_pnl": bm["pnl"], "ef_pnl": em["pnl"],
            "bl_er": bm["er"], "ef_er": em["er"],
        })

    # =======================================================================
    # Select 5 test weeks: WEAKEST / LOW / MID / GOOD / BEST
    # =======================================================================
    ranked = sorted(week_data, key=lambda w: w["ef_pnl"])
    n_weeks = len(ranked)

    # Indices: WEAKEST=0, LOW=~25th pctile, MID=~50th, GOOD=~75th, BEST=last
    idx_weakest = 0
    idx_low = max(1, n_weeks // 4)
    idx_mid = n_weeks // 2
    idx_good = min(n_weeks - 2, 3 * n_weeks // 4)
    idx_best = n_weeks - 1

    selections = [
        ("WEAKEST", ranked[idx_weakest]),
        ("LOW", ranked[idx_low]),
        ("MID", ranked[idx_mid]),
        ("GOOD", ranked[idx_good]),
        ("BEST", ranked[idx_best]),
    ]

    print(f"\n{'='*60}")
    print(f"TEST WEEK SELECTION (ranked by Track A filtered PnL)")
    print(f"{'='*60}")
    print(f"\n{'Cat':<10} {'Week':<10} {'ef_N':>5} {'ef_WR':>6} {'ef_SR':>6} {'ef_PnL':>10} {'ef_ER':>8}")
    print(f"{'-'*60}")

    for cat, wd in selections:
        print(f"{cat:<10} {wd['week']:<10} {wd['ef_n']:>5} {wd['ef_wr']:>5.0%} "
              f"{wd['ef_sr']:>5.0%} ${wd['ef_pnl']:>9,.0f} ${wd['ef_er']:>7.2f}")

    # Also show the full ranking for reference
    print(f"\nFull ranking (by ef_pnl, ascending):")
    print(f"{'Rank':>4} {'Week':<10} {'ef_N':>5} {'ef_PnL':>10} {'ef_ER':>8}")
    print(f"{'-'*42}")
    for i, wd in enumerate(ranked):
        marker = ""
        for cat, sel in selections:
            if sel["week"] == wd["week"]:
                marker = f"  <-- {cat}"
        print(f"{i+1:>4} {wd['week']:<10} {wd['ef_n']:>5} ${wd['ef_pnl']:>9,.0f} ${wd['ef_er']:>7.2f}{marker}")

    # =======================================================================
    # Save results
    # =======================================================================
    out_csv = OUTPUT_DIR / "fade-confirm-step0-week-selection.csv"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "rank", "week", "category", "bl_n", "ef_n", "retention",
            "bl_wr", "ef_wr", "bl_sr", "ef_sr",
            "bl_pnl", "ef_pnl", "bl_er", "ef_er",
        ])
        w.writeheader()
        selected_weeks = {s[1]["week"]: s[0] for s in selections}
        for i, wd in enumerate(ranked):
            w.writerow({
                "rank": i + 1,
                "week": wd["week"],
                "category": selected_weeks.get(wd["week"], ""),
                "bl_n": wd["bl_n"], "ef_n": wd["ef_n"],
                "retention": f"{wd['retention']:.4f}",
                "bl_wr": f"{wd['bl_wr']:.4f}", "ef_wr": f"{wd['ef_wr']:.4f}",
                "bl_sr": f"{wd['bl_sr']:.4f}", "ef_sr": f"{wd['ef_sr']:.4f}",
                "bl_pnl": f"{wd['bl_pnl']:.2f}", "ef_pnl": f"{wd['ef_pnl']:.2f}",
                "bl_er": f"{wd['bl_er']:.2f}", "ef_er": f"{wd['ef_er']:.2f}",
            })
    print(f"\nSaved: {out_csv}")

    total = time.time() - t0
    print(f"\nTotal runtime: {total:.0f}s ({total/60:.1f}m)")

    return selections


if __name__ == "__main__":
    main()
