# archetype: rotational
"""
rotational-NQ-scale-detection-p2.py -- P2 holdout validation (ONE SHOT).

Runs the choppiness filter (chop<0.10, lb=3, 250-tick bars) on P2 holdout data.
This is a frozen-config, one-shot test per pipeline rules.

Config frozen from P1:
  - SD=10, HS=60, depth_1, MCS=2
  - Filter: choppiness < 0.10 at lb=3 on 250-tick agg bars

Usage:
    python rotational-NQ-scale-detection-p2.py [--bar-file PATH]
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

TICK_VALUE = 5.0
COMMISSION_PER_RT = 3.50
SD = 10.0
HS = 60.0
MAX_LEVELS = 1
MAX_CONTRACT_SIZE = 2
MAX_FADES = 0
BAR_SIZE = 250
LB = 3
CHOP_THRESHOLD = 0.10


def make_chop_filter(chop_max):
    def filter_fn(signals, i, direction, step_dist):
        chop = signals["choppiness"][i]
        if np.isnan(chop):
            return True
        return chop < chop_max
    return filter_fn


def compute_metrics(cycles):
    if not cycles:
        return {"cycles": 0, "wr": 0, "sr": 0, "pnl": 0.0, "er": 0.0}
    n = len(cycles)
    stops = sum(1 for c in cycles if c["exit_type"] == "HARD_STOP")
    net_pnls = []
    for c in cycles:
        pos = max(c.get("max_position", 1), 1)
        net_pnls.append(c["pnl_ticks"] * TICK_VALUE - pos * COMMISSION_PER_RT)
    wins = sum(1 for p in net_pnls if p >= 0)
    total_pnl = sum(net_pnls)
    return {"cycles": n, "wr": wins / n, "sr": stops / n,
            "pnl": total_pnl, "er": total_pnl / n}


def weekly_breakdown(cycles):
    import datetime
    weeks = defaultdict(list)
    for c in cycles:
        dt = c["seed_dt"][:10]
        d = datetime.date(int(dt[:4]), int(dt[5:7]), int(dt[8:10]))
        wk = f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"
        weeks[wk].append(c)
    return {wk: compute_metrics(wcs) for wk, wcs in sorted(weeks.items())}


def main():
    import argparse
    parser = argparse.ArgumentParser(description="P2 Holdout Validation (ONE SHOT)")
    parser.add_argument("--bar-file", type=str,
                        default=r"C:\Projects\futures_pipeline\data\NQ-1tick-holdout.csv")
    args = parser.parse_args()

    print(f"*** P2 HOLDOUT VALIDATION -- ONE SHOT ***")
    print(f"Config: SD={SD} HS={HS} depth_1 MCS={MAX_CONTRACT_SIZE}")
    print(f"Filter: choppiness < {CHOP_THRESHOLD} at lb={LB} on {BAR_SIZE}-tick bars")
    print(f"Data: {args.bar_file}")

    print(f"\nLoading bars...")
    t0 = time.time()
    bars = load_bars_extended(args.bar_file)
    print(f"Loaded {bars['n']} bars in {time.time()-t0:.1f}s")
    print(f"Date range: {bars['date_int'][0]} to {bars['date_int'][-1]}")

    # Precompute signals
    print(f"\nPrecomputing choppiness signals...")
    t1 = time.time()
    agg_bars, tick_to_agg = aggregate_to_ntick(bars, BAR_SIZE)
    features = compute_regime_signals(agg_bars, lookback=LB)
    signals = {
        "choppiness": map_signal_to_ticks(features["choppiness"], tick_to_agg),
        "warmup_ticks": {"dominant_scale": 0, "asymmetry": 0},
    }
    print(f"  done ({time.time()-t1:.1f}s)")

    # Baseline
    print(f"\nRunning BASELINE (P2)...")
    t1 = time.time()
    bl_cycles = run_sim_filtered(bars, SD, HS, MAX_FADES, MAX_LEVELS, MAX_CONTRACT_SIZE)
    bl = compute_metrics(bl_cycles)
    print(f"  {bl['cycles']} cyc | {bl['wr']:.0%} WR | {bl['sr']:.0%} SR | "
          f"${bl['pnl']:,.0f} | E[R]=${bl['er']:.2f} ({time.time()-t1:.0f}s)")

    # Filtered
    print(f"\nRunning FILTERED (chop<{CHOP_THRESHOLD})...")
    t1 = time.time()
    filter_fn = make_chop_filter(CHOP_THRESHOLD)
    fl_cycles = run_sim_filtered(
        bars, SD, HS, MAX_FADES, MAX_LEVELS, MAX_CONTRACT_SIZE,
        signal_arrays=signals, filter_fn=filter_fn,
    )
    fl = compute_metrics(fl_cycles)
    retention = fl["cycles"] / bl["cycles"] if bl["cycles"] > 0 else 0
    print(f"  {fl['cycles']} cyc ({retention:.0%} ret) | {fl['wr']:.0%} WR | {fl['sr']:.0%} SR | "
          f"${fl['pnl']:,.0f} | E[R]=${fl['er']:.2f} ({time.time()-t1:.0f}s)")

    # Per-week breakdown
    print(f"\n{'='*70}")
    print(f"PER-WEEK BREAKDOWN")
    print(f"{'='*70}")

    bl_wk = weekly_breakdown(bl_cycles)
    fl_wk = weekly_breakdown(fl_cycles)

    print(f"\n  {'Week':>8} {'BL Cyc':>7} {'FL Cyc':>7} {'Ret':>5} "
          f"{'BL SR':>6} {'FL SR':>6} {'BL PnL':>10} {'FL PnL':>10} {'dPnL':>10}")
    print(f"  {'-'*80}")

    bl_neg = 0; fl_neg = 0
    for wk in sorted(set(list(bl_wk.keys()) + list(fl_wk.keys()))):
        bw = bl_wk.get(wk, {"cycles": 0, "sr": 0, "pnl": 0})
        fw = fl_wk.get(wk, {"cycles": 0, "sr": 0, "pnl": 0})
        ret = fw["cycles"] / bw["cycles"] if bw["cycles"] > 0 else 0
        dpnl = fw["pnl"] - bw["pnl"]
        if bw["pnl"] < 0: bl_neg += 1
        if fw["pnl"] < 0: fl_neg += 1
        print(f"  {wk:>8} {bw['cycles']:>7} {fw['cycles']:>7} {ret:>5.0%} "
              f"{bw['sr']:>6.0%} {fw['sr']:>6.0%} "
              f"${bw['pnl']:>9,.0f} ${fw['pnl']:>9,.0f} ${dpnl:>+9,.0f}")

    print(f"\n  Negative weeks: baseline={bl_neg}, filtered={fl_neg}")

    # Summary
    print(f"\n{'='*70}")
    print(f"P2 HOLDOUT SUMMARY")
    print(f"{'='*70}")
    print(f"\n  {'':>15} {'Cycles':>8} {'WR':>6} {'SR':>6} {'Total PnL':>12} {'E[R]':>8}")
    print(f"  {'-'*58}")
    print(f"  {'P1 Baseline':>15} {18148:>8} {0.76:>6.0%} {0.24:>6.0%} ${'56,810':>11} ${'3.13':>7}")
    print(f"  {'P1 Filtered':>15} {10468:>8} {0.82:>6.0%} {0.18:>6.0%} ${'580,071':>11} ${'55.41':>7}")
    print(f"  {'P2 Baseline':>15} {bl['cycles']:>8} {bl['wr']:>6.0%} {bl['sr']:>6.0%} ${bl['pnl']:>11,.0f} ${bl['er']:>7.2f}")
    print(f"  {'P2 Filtered':>15} {fl['cycles']:>8} {fl['wr']:>6.0%} {fl['sr']:>6.0%} ${fl['pnl']:>11,.0f} ${fl['er']:>7.2f}")

    # Verdict
    p2_filter_positive = fl["pnl"] > 0
    p2_filter_better = fl["er"] > bl["er"]
    p2_wr_above_76 = fl["wr"] >= 0.76  # stress test vulnerability threshold
    p2_sr_improved = fl["sr"] < bl["sr"]

    print(f"\n  Filter PnL positive: {p2_filter_positive}")
    print(f"  Filter E[R] > baseline E[R]: {p2_filter_better}")
    print(f"  Filter WR >= 76% (stress test threshold): {p2_wr_above_76}")
    print(f"  Filter SR < baseline SR: {p2_sr_improved}")

    if p2_filter_positive and p2_wr_above_76:
        verdict = "PASS"
    elif p2_filter_positive:
        verdict = "WEAK PASS -- positive but WR below stress threshold"
    else:
        verdict = "FAIL"

    print(f"\n  *** P2 VERDICT: {verdict} ***")

    # Save
    out_dir = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "p2-holdout-results.csv"
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["period", "filter", "cycles", "wr", "sr", "total_pnl", "er"])
        w.writerow(["P1", "baseline", 18148, 0.76, 0.24, 56810, 3.13])
        w.writerow(["P1", "chop<0.10", 10468, 0.82, 0.18, 580071, 55.41])
        w.writerow(["P2", "baseline", bl["cycles"], round(bl["wr"], 4),
                    round(bl["sr"], 4), round(bl["pnl"], 0), round(bl["er"], 2)])
        w.writerow(["P2", "chop<0.10", fl["cycles"], round(fl["wr"], 4),
                    round(fl["sr"], 4), round(fl["pnl"], 0), round(fl["er"], 2)])
    print(f"\nResults saved: {out_path}")

    # Audit + Journal
    total = time.time() - t0
    append_audit("P2_HOLDOUT_COMPLETE",
                 f"P2 holdout: SD=10 HS=60 chop<0.10 lb=3. "
                 f"BL: {bl['cycles']} cyc, ${bl['pnl']:,.0f}. "
                 f"Filtered: {fl['cycles']} cyc, ${fl['pnl']:,.0f}. "
                 f"Verdict: {verdict}. Runtime: {total:.0f}s.")

    journal_text = f"""## P2 Holdout Validation (ONE SHOT)

**Config:** SD=10 HS=60 depth_1 MCS=2 + choppiness < 0.10 at lb=3
**Data:** NQ-1tick-holdout.csv (2025-12-17 to 2026-03-13)

### Results

| | Cycles | WR | SR | Total PnL | E[R] |
|---|---|---|---|---|---|
| P1 Baseline | 18,148 | 76% | 24% | $56,810 | $3.13 |
| P1 Filtered | 10,468 | 82% | 18% | $580,071 | $55.41 |
| P2 Baseline | {bl['cycles']:,} | {bl['wr']:.0%} | {bl['sr']:.0%} | ${bl['pnl']:,.0f} | ${bl['er']:.2f} |
| P2 Filtered | {fl['cycles']:,} | {fl['wr']:.0%} | {fl['sr']:.0%} | ${fl['pnl']:,.0f} | ${fl['er']:.2f} |

Negative weeks: baseline={bl_neg}, filtered={fl_neg}

### Verdict: {verdict}

Runtime: {total:.0f}s
"""
    append_journal(journal_text)

    print(f"\nP2 validation complete: {total:.0f}s ({total/60:.1f}m)")


if __name__ == "__main__":
    main()
