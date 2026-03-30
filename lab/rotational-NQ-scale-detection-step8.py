# archetype: rotational
"""
rotational-NQ-scale-detection-step8.py -- Step 8: Live sim with choppiness filter.

Wires the choppiness (+ optional slope) filter into the actual sim as an
entry gate. Verifies that the retroactive Step 7 results hold when the
filter actually changes the entry sequence.

Baseline: SD=10 HS=60 depth_1 MCS=2.
Filter: choppiness < threshold at lb=3 on 250-tick agg bars.

Usage:
    python rotational-NQ-scale-detection-step8.py [--bar-file PATH]
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
assign_session_ids = _engine.assign_session_ids
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
TEST_WEEKS = {
    "W42": {"start": 20251013, "end": 20251017, "category": "WORST"},
    "W50": {"start": 20251208, "end": 20251212, "category": "BAD"},
    "W45": {"start": 20251103, "end": 20251107, "category": "AVG"},
    "W46": {"start": 20251110, "end": 20251114, "category": "GOOD"},
    "W48": {"start": 20251124, "end": 20251128, "category": "GOOD"},
}

SD = 10.0
HS = 60.0
MAX_LEVELS = 1
MAX_CONTRACT_SIZE = 2
MAX_FADES = 0
BAR_SIZE = 250
LB = 3

# Filter candidates to test
FILTER_CANDIDATES = [
    ("chop<0.10", {"chop_max": 0.10, "slope_max": None}),
    ("chop<0.15", {"chop_max": 0.15, "slope_max": None}),
    ("chop<0.20+slope<3.0", {"chop_max": 0.20, "slope_max": 3.0}),
    ("chop<0.10+slope<3.0", {"chop_max": 0.10, "slope_max": 3.0}),
]


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

def extract_week(bars, date_start, date_end):
    dint = bars["date_int"]
    mask = (dint >= date_start) & (dint <= date_end)
    indices = np.where(mask)[0]
    if len(indices) == 0:
        raise ValueError(f"No bars for {date_start}-{date_end}")
    result = {}
    for k in ["last", "high", "low", "open", "time_sec", "date_int", "atr",
              "bid_vol", "ask_vol"]:
        if k in bars:
            result[k] = bars[k][indices]
    result["datetime"] = [bars["datetime"][i] for i in indices]
    result["n"] = len(indices)
    return result


def precompute_chop_signals(bars, bar_size, lookback):
    """Compute choppiness and slope on agg bars, map to ticks."""
    n_ticks = bars["n"]
    agg_bars, tick_to_agg = aggregate_to_ntick(bars, bar_size)
    features = compute_regime_signals(agg_bars, lookback=lookback)

    # Map to tick resolution
    chop_ticks = map_signal_to_ticks(features["choppiness"], tick_to_agg)
    slope_ticks = map_signal_to_ticks(features["slope"], tick_to_agg)

    return {
        "choppiness": chop_ticks,
        "slope": slope_ticks,
        "warmup_ticks": {"dominant_scale": 0, "asymmetry": 0},
    }


def make_chop_filter(chop_max, slope_max=None):
    """Entry gate: allow only when choppiness < threshold (and optionally slope < threshold)."""
    def filter_fn(signals, i, direction, step_dist):
        chop = signals["choppiness"][i]
        if np.isnan(chop):
            return True  # warmup
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


def compute_week_metrics(cycles):
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


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Scale Detection -- Step 8")
    parser.add_argument("--bar-file", type=str,
                        default=r"C:\Projects\futures_pipeline\data\NQ-1tick-calibration.csv")
    args = parser.parse_args()

    print(f"Loading bars from {args.bar_file}...")
    t0 = time.time()
    all_bars = load_bars_extended(args.bar_file)
    print(f"Loaded {all_bars['n']} bars in {time.time()-t0:.1f}s")

    all_results = []

    for week_name, week_cfg in TEST_WEEKS.items():
        print(f"\n{'='*70}")
        print(f"WEEK: {week_name} ({week_cfg['start']}-{week_cfg['end']}) -- {week_cfg['category']}")
        print(f"{'='*70}")

        week_bars = extract_week(all_bars, week_cfg["start"], week_cfg["end"])
        print(f"  {week_bars['n']} bars")

        # Precompute signals
        signals = precompute_chop_signals(week_bars, BAR_SIZE, LB)

        # Baseline (no filter)
        baseline_cycles = run_sim_filtered(
            week_bars, step_dist=SD, hard_stop=HS,
            max_fades=MAX_FADES, max_levels=MAX_LEVELS,
            max_contract_size=MAX_CONTRACT_SIZE,
        )
        bl = compute_week_metrics(baseline_cycles)
        print(f"\n  BASELINE: {bl['cycles']} cyc | {bl['wr']:.0%} WR | "
              f"{bl['sr']:.0%} SR | ${bl['pnl']:,.0f}")

        all_results.append({"week": week_name, "category": week_cfg["category"],
                           "filter": "baseline", **bl})

        # Filtered runs
        for cand_name, params in FILTER_CANDIDATES:
            filter_fn = make_chop_filter(params["chop_max"], params["slope_max"])
            filtered_cycles = run_sim_filtered(
                week_bars, step_dist=SD, hard_stop=HS,
                max_fades=MAX_FADES, max_levels=MAX_LEVELS,
                max_contract_size=MAX_CONTRACT_SIZE,
                signal_arrays=signals, filter_fn=filter_fn,
            )
            fl = compute_week_metrics(filtered_cycles)
            retention = fl["cycles"] / bl["cycles"] if bl["cycles"] > 0 else 0
            delta_pnl = fl["pnl"] - bl["pnl"]

            print(f"  {cand_name:>25}: {fl['cycles']:>5} cyc ({retention:.0%} ret) | "
                  f"{fl['wr']:.0%} WR | {fl['sr']:.0%} SR | "
                  f"${fl['pnl']:,.0f} (d${delta_pnl:+,.0f})")

            all_results.append({"week": week_name, "category": week_cfg["category"],
                               "filter": cand_name, "retention": retention,
                               "delta_pnl": delta_pnl, **fl})

    # --- Summary ---
    print(f"\n{'='*70}")
    print(f"STEP 8 SUMMARY -- Live sim with choppiness filter")
    print(f"{'='*70}")
    print(f"\n  {'Week':>6} {'Cat':>6} {'Filter':>25} {'Cyc':>5} {'Ret':>5} "
          f"{'SR':>5} {'PnL':>10} {'dPnL':>10}")
    print(f"  {'-'*78}")

    for r in all_results:
        ret_str = f"{r.get('retention', 1.0):.0%}" if r["filter"] != "baseline" else "--"
        dpnl_str = f"${r.get('delta_pnl', 0):+,.0f}" if r["filter"] != "baseline" else "--"
        print(f"  {r['week']:>6} {r['category']:>6} {r['filter']:>25} {r['cycles']:>5} "
              f"{ret_str:>5} {r['sr']:.0%}  ${r['pnl']:>9,.0f} {dpnl_str:>10}")

    # --- Compare Step 7 (retroactive) vs Step 8 (live sim) ---
    print(f"\n{'='*70}")
    print(f"STEP 7 vs STEP 8 COMPARISON")
    print(f"{'='*70}")
    print(f"  If numbers match, the filter has no sequence-dependent side effects.")
    print(f"  If they differ, skipping entries changed the subsequent entry pattern.")

    # --- Save ---
    out_dir = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "step8-live-sim-results.csv"
    fields = ["week", "category", "filter", "cycles", "wins", "wr", "stops", "sr",
              "pnl", "er", "retention", "delta_pnl"]
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in all_results:
            w.writerow({k: round(v, 4) if isinstance(v, float) else v
                        for k, v in r.items() if k in fields})
    print(f"\nResults saved: {out_path}")

    # --- Audit + Journal ---
    total = time.time() - t0

    # Build journal summary
    table_lines = []
    table_lines.append("| Week | Cat | BL PnL | chop<0.10 | chop<0.20+slope<3.0 |")
    table_lines.append("|---|---|---|---|---|")
    for week_name in TEST_WEEKS:
        bl_pnl = [r for r in all_results if r["week"] == week_name and r["filter"] == "baseline"][0]["pnl"]
        c10 = [r for r in all_results if r["week"] == week_name and r["filter"] == "chop<0.10"]
        c20s = [r for r in all_results if r["week"] == week_name and r["filter"] == "chop<0.20+slope<3.0"]
        c10_str = f"${c10[0]['pnl']:,.0f} ({c10[0].get('retention',0):.0%})" if c10 else "--"
        c20s_str = f"${c20s[0]['pnl']:,.0f} ({c20s[0].get('retention',0):.0%})" if c20s else "--"
        cat = TEST_WEEKS[week_name]["category"]
        table_lines.append(f"| {week_name} | {cat} | ${bl_pnl:,.0f} | {c10_str} | {c20s_str} |")

    # Check pass/fail
    pass_all = True
    for cand_name, _ in FILTER_CANDIDATES:
        bad = [r for r in all_results if r["filter"] == cand_name and r["category"] in ("WORST", "BAD")]
        good = [r for r in all_results if r["filter"] == cand_name and r["category"] == "GOOD"]
        for r in bad:
            if r["pnl"] < 0:
                pass_all = False
        for r in good:
            bl_pnl = [x for x in all_results if x["week"] == r["week"] and x["filter"] == "baseline"][0]["pnl"]
            if bl_pnl > 0 and r["pnl"] < bl_pnl * 0.5:
                pass_all = False

    verdict = "PASS" if pass_all else "FAIL"

    append_audit("STEP_8_COMPLETE",
                 f"Live sim with choppiness filter on SD=10 HS=60. "
                 f"Verdict: {verdict}. Runtime: {total:.0f}s.")

    journal_text = f"""## Step 8: Live sim with choppiness filter

**Method:** Actual sim re-run with choppiness filter wired as entry gate (not retroactive).
**Baseline:** SD=10 HS=60 depth_1 MCS=2
**Filter lookback:** lb={LB} on 250-tick agg bars
**Candidates:** {[c[0] for c in FILTER_CANDIDATES]}

### Per-week results (live sim)

{chr(10).join(table_lines)}

### Verdict: {verdict}

Comparison to Step 7 retroactive analysis: see output for whether numbers match.

Data: `step8-live-sim-results.csv`
Runtime: {total:.0f}s
"""
    append_journal(journal_text)

    print(f"\nStep 8 complete: {total:.0f}s ({total/60:.1f}m)")


if __name__ == "__main__":
    main()
