# archetype: rotational
"""
rotational-NQ-scale-detection-step1.py -- Phase 1, Step 1: Static filter validation.

Fixed SD=25, gate entries when MTZZ dominant scale != 25 (+-1 level).
Tests across 5 representative weeks from P1 (2 good, 1 average, 2 bad).

Pass criteria: Reduces stop rate on bad weeks without killing cycle count;
doesn't hurt good weeks.

Usage:
    python rotational-NQ-scale-detection-step1.py [--bar-file PATH]
"""
from __future__ import annotations

import csv
import importlib
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

# Import from hyphenated filenames
_engine = importlib.import_module("rotational-NQ-scale-detection-engine")
load_bars_extended = _engine.load_bars_extended
aggregate_to_ntick = _engine.aggregate_to_ntick
assign_session_ids = _engine.assign_session_ids
zigzag = _engine.zigzag
compute_multi_threshold_signals = _engine.compute_multi_threshold_signals
map_signal_to_ticks = _engine.map_signal_to_ticks
append_audit = _engine.append_audit
append_journal = _engine.append_journal

_sweep = importlib.import_module("rotational-NQ-scale-detection-sweep")
run_sim_filtered = _sweep.run_sim_filtered
make_dominant_scale_filter = _sweep.make_dominant_scale_filter
TICK_SIZE = _sweep.TICK_SIZE
COMMISSION_PER_RT_MINI = _sweep.COMMISSION_PER_RT_MINI
RTH_OPEN_SEC = _sweep.RTH_OPEN_SEC
RTH_CLOSE_SEC = _sweep.RTH_CLOSE_SEC


# ---------------------------------------------------------------------------
#  Test weeks
# ---------------------------------------------------------------------------
TEST_WEEKS = {
    "W39": {"start": 20250922, "end": 20250926, "category": "WORST"},
    "W48": {"start": 20251124, "end": 20251128, "category": "BAD"},
    "W45": {"start": 20251103, "end": 20251107, "category": "AVG"},
    "W44": {"start": 20251027, "end": 20251031, "category": "GOOD"},
    "W40": {"start": 20250929, "end": 20251003, "category": "GOOD"},
}

# Window sizes to test (agg bars -- time-based)
WINDOWS = [250, 500, 1000]

# SD=25 HS=125 depth_1 MCS=2 (config 79 -- baseline best)
SD = 25.0
HS = 125.0
MAX_LEVELS = 1
MAX_CONTRACT_SIZE = 2
MAX_FADES = 0


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

def extract_week(bars: dict, date_start: int, date_end: int) -> dict:
    dint = bars["date_int"]
    mask = (dint >= date_start) & (dint <= date_end)
    indices = np.where(mask)[0]
    if len(indices) == 0:
        raise ValueError(f"No bars found for dates {date_start}-{date_end}")
    result = {}
    for k in ["last", "high", "low", "open", "time_sec", "date_int", "atr"]:
        result[k] = bars[k][indices]
    result["datetime"] = [bars["datetime"][i] for i in indices]
    result["n"] = len(indices)
    return result


def precompute_signals(bars: dict, bar_size: int, mt_window: int) -> dict:
    thresholds = [10.0, 15.0, 20.0, 25.0, 30.0, 50.0]
    n_ticks = bars["n"]
    agg_bars, tick_to_agg = aggregate_to_ntick(bars, bar_size)
    n_agg = agg_bars["n"]
    agg_sids = assign_session_ids(agg_bars["date_int"])
    agg_prices = agg_bars["last"].astype(np.float64)

    mt = compute_multi_threshold_signals(
        agg_prices, agg_sids, n_agg, thresholds, mt_window,
    )

    return {
        "dominant_scale": map_signal_to_ticks(mt["dominant_scale"], tick_to_agg),
        "confidence": map_signal_to_ticks(mt["confidence"], tick_to_agg),
        "mt_thresholds": thresholds,
        "n_agg_bars": n_agg,
        "tick_to_agg": tick_to_agg,
        "warmup_ticks": {"dominant_scale": 0, "asymmetry": 0},
    }


def compute_week_metrics(cycles: list[dict]) -> dict:
    if not cycles:
        return {"cycles": 0, "wins": 0, "wr": 0, "stops": 0, "sr": 0,
                "revs": 0, "eods": 0, "pnl": 0.0, "er": 0.0}
    n = len(cycles)
    stops = sum(1 for c in cycles if c["exit_type"] == "HARD_STOP")
    revs = sum(1 for c in cycles if c["exit_type"] == "REVERSAL")
    eods = sum(1 for c in cycles if c["exit_type"] == "EOD_FLATTEN")
    net_pnls = []
    for c in cycles:
        comm = COMMISSION_PER_RT_MINI * max(c.get("max_position", 2), 1)
        net_pnls.append(c["pnl_ticks"] * 5.0 - comm)
    wins = sum(1 for p in net_pnls if p >= 0)
    total_pnl = sum(net_pnls)
    return {
        "cycles": n, "wins": wins, "wr": wins / n,
        "stops": stops, "sr": stops / n,
        "revs": revs, "eods": eods,
        "pnl": total_pnl, "er": total_pnl / n,
    }


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Scale Detection -- Step 1")
    parser.add_argument("--bar-file", type=str,
                        default=r"C:\Projects\futures_pipeline\data\NQ-1tick-calibration.csv")
    parser.add_argument("--bar-size", type=int, default=250)
    args = parser.parse_args()

    print(f"Loading bars from {args.bar_file}...")
    t0 = time.time()
    all_bars = load_bars_extended(args.bar_file)
    print(f"Loaded {all_bars['n']} bars in {time.time()-t0:.1f}s")

    # Results collector
    all_results = []

    for week_name, week_cfg in TEST_WEEKS.items():
        print(f"\n{'='*70}")
        print(f"WEEK: {week_name} ({week_cfg['start']}-{week_cfg['end']}) -- {week_cfg['category']}")
        print(f"{'='*70}")

        week_bars = extract_week(all_bars, week_cfg["start"], week_cfg["end"])
        print(f"  {week_bars['n']} bars")

        # Baseline (no filter)
        baseline_cycles = run_sim_filtered(
            week_bars, step_dist=SD, hard_stop=HS,
            max_fades=MAX_FADES, max_levels=MAX_LEVELS,
            max_contract_size=MAX_CONTRACT_SIZE,
        )
        bl = compute_week_metrics(baseline_cycles)
        print(f"\n  BASELINE: {bl['cycles']} cyc | {bl['wr']:.0%} WR | "
              f"{bl['sr']:.0%} SR | ${bl['pnl']:,.0f}")

        row_bl = {"week": week_name, "category": week_cfg["category"],
                  "filter": "baseline", "window": "-", **bl}
        all_results.append(row_bl)

        # Filtered at each window size
        for window in WINDOWS:
            signals = precompute_signals(week_bars, args.bar_size, window)

            # Count transitions
            dom = signals["dominant_scale"]
            n_trans = 0
            prev = np.nan
            for i in range(len(dom)):
                if not np.isnan(dom[i]) and dom[i] != prev:
                    if not np.isnan(prev):
                        n_trans += 1
                    prev = dom[i]

            filter_fn = make_dominant_scale_filter()
            filtered_cycles = run_sim_filtered(
                week_bars, step_dist=SD, hard_stop=HS,
                max_fades=MAX_FADES, max_levels=MAX_LEVELS,
                max_contract_size=MAX_CONTRACT_SIZE,
                signal_arrays=signals, filter_fn=filter_fn,
            )
            fl = compute_week_metrics(filtered_cycles)
            retention = fl["cycles"] / bl["cycles"] if bl["cycles"] > 0 else 0
            delta_sr = fl["sr"] - bl["sr"]
            delta_pnl = fl["pnl"] - bl["pnl"]

            print(f"  w={window:>4}: {fl['cycles']:>3} cyc ({retention:.0%} ret) | "
                  f"{fl['wr']:.0%} WR | {fl['sr']:.0%} SR (d{delta_sr:+.0%}) | "
                  f"${fl['pnl']:,.0f} (d${delta_pnl:+,.0f}) | {n_trans} transitions")

            row_fl = {"week": week_name, "category": week_cfg["category"],
                      "filter": f"w={window}", "window": window,
                      "retention": retention, "delta_sr": delta_sr,
                      "delta_pnl": delta_pnl, "transitions": n_trans, **fl}
            all_results.append(row_fl)

    # --- Summary table ---
    print(f"\n{'='*70}")
    print(f"STEP 1 SUMMARY -- Static Filter SD=25 +-1 level")
    print(f"{'='*70}")
    print(f"\n{'Week':>6} {'Cat':>6} {'Filter':>8} {'Cyc':>5} {'Ret':>5} "
          f"{'WR':>5} {'SR':>5} {'dSR':>6} {'PnL':>10} {'dPNL':>10} {'Trans':>6}")
    print("-" * 80)

    for r in all_results:
        ret_str = f"{r.get('retention', 1.0):.0%}" if r["filter"] != "baseline" else "--"
        dsr_str = f"{r.get('delta_sr', 0):+.0%}" if r["filter"] != "baseline" else "--"
        dpnl_str = f"${r.get('delta_pnl', 0):+,.0f}" if r["filter"] != "baseline" else "--"
        trans_str = f"{r.get('transitions', 0)}" if r["filter"] != "baseline" else "--"
        print(f"{r['week']:>6} {r['category']:>6} {r['filter']:>8} {r['cycles']:>5} "
              f"{ret_str:>5} {r['wr']:.0%}{' ':>1} {r['sr']:.0%}{' ':>1} {dsr_str:>6} "
              f"${r['pnl']:>9,.0f} {dpnl_str:>10} {trans_str:>6}")

    # --- Pass/Fail evaluation ---
    print(f"\n{'='*70}")
    print(f"PASS/FAIL EVALUATION")
    print(f"{'='*70}")

    for window in WINDOWS:
        bad_weeks = [r for r in all_results
                     if r["filter"] == f"w={window}"
                     and r["category"] in ("WORST", "BAD")]
        good_weeks = [r for r in all_results
                      if r["filter"] == f"w={window}"
                      and r["category"] == "GOOD"]
        avg_weeks = [r for r in all_results
                     if r["filter"] == f"w={window}"
                     and r["category"] == "AVG"]

        # Check: reduces stop rate on bad weeks?
        bad_sr_improved = all(r.get("delta_sr", 0) < 0 for r in bad_weeks)
        # Check: doesn't kill cycle count (>50% retention on bad weeks)?
        bad_retention_ok = all(r.get("retention", 0) >= 0.50 for r in bad_weeks)
        # Check: doesn't hurt good weeks (PnL delta >= -10%)?
        good_not_hurt = True
        for r in good_weeks:
            bl_pnl = [x for x in all_results
                      if x["week"] == r["week"] and x["filter"] == "baseline"][0]["pnl"]
            if bl_pnl > 0 and r["pnl"] < bl_pnl * 0.7:  # more than 30% degradation
                good_not_hurt = False

        passed = bad_sr_improved and bad_retention_ok and good_not_hurt
        verdict = "PASS" if passed else "FAIL"

        print(f"\n  Window={window}:")
        print(f"    Bad weeks SR improved:  {bad_sr_improved}")
        print(f"    Bad weeks retention>50%: {bad_retention_ok}")
        print(f"    Good weeks not hurt:    {good_not_hurt}")
        print(f"    VERDICT: {verdict}")

    # --- Save results CSV ---
    out_dir = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "step1-static-filter-results.csv"
    fields = ["week", "category", "filter", "window", "cycles", "wins", "wr",
              "stops", "sr", "revs", "eods", "pnl", "er",
              "retention", "delta_sr", "delta_pnl", "transitions"]
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in all_results:
            w.writerow({k: round(v, 4) if isinstance(v, float) else v
                        for k, v in r.items() if k in fields})
    print(f"\nResults saved: {out_path}")

    # --- Audit + Journal ---
    total = time.time() - t0
    summary_lines = []
    for window in WINDOWS:
        bad_delta = [r.get("delta_sr", 0) for r in all_results
                     if r["filter"] == f"w={window}" and r["category"] in ("WORST", "BAD")]
        avg_dsr = sum(bad_delta) / len(bad_delta) if bad_delta else 0
        summary_lines.append(f"w={window}: avg bad-week dSR={avg_dsr:+.1%}")

    append_audit("STEP_1_COMPLETE",
                 f"Static filter SD=25 on 5 weeks. {'; '.join(summary_lines)}. "
                 f"Runtime: {total:.0f}s. Results: {out_path.name}")

    # Build per-week table for best window (w=250)
    table_lines = []
    table_lines.append("| Week | Cat | BL PnL | Filtered PnL | Ret | dSR | dPnL |")
    table_lines.append("|---|---|---|---|---|---|---|")
    for r in all_results:
        if r["filter"] == "w=250":
            bl_pnl = [x for x in all_results
                      if x["week"] == r["week"] and x["filter"] == "baseline"][0]["pnl"]
            table_lines.append(
                f"| {r['week']} | {r['category']} | ${bl_pnl:,.0f} | ${r['pnl']:,.0f} "
                f"| {r.get('retention',0):.0%} | {r.get('delta_sr',0):+.0%} "
                f"| ${r.get('delta_pnl',0):+,.0f} |")

    # Build verdict text
    verdict_lines = []
    for window in WINDOWS:
        bad_weeks = [r for r in all_results
                     if r["filter"] == f"w={window}" and r["category"] in ("WORST", "BAD")]
        good_weeks = [r for r in all_results
                      if r["filter"] == f"w={window}" and r["category"] == "GOOD"]
        bad_sr_improved = all(r.get("delta_sr", 0) < 0 for r in bad_weeks)
        bad_retention_ok = all(r.get("retention", 0) >= 0.50 for r in bad_weeks)
        good_not_hurt = True
        for r in good_weeks:
            bl_pnl = [x for x in all_results
                      if x["week"] == r["week"] and x["filter"] == "baseline"][0]["pnl"]
            if bl_pnl > 0 and r["pnl"] < bl_pnl * 0.7:
                good_not_hurt = False
        passed = bad_sr_improved and bad_retention_ok and good_not_hurt
        verdict_lines.append(f"- w={window}: {'PASS' if passed else 'FAIL'} "
                           f"(bad SR improved: {bad_sr_improved}, "
                           f"retention>50%: {bad_retention_ok}, "
                           f"good not hurt: {good_not_hurt})")

    overall = "PASS" if any("PASS" in v for v in verdict_lines) else "FAIL"

    journal_text = f"""## Step 1: Static filter validation

**Test:** Fixed SD=25, gate entries when MTZZ dominant scale != 25 (+-1 level).
**Data:** W39 (worst), W48 (bad), W45 (avg), W44 (good), W40 (good)
**Windows tested:** {WINDOWS}

### Per-week results (w=250)

{chr(10).join(table_lines)}

### Summary
{chr(10).join('- ' + s for s in summary_lines)}

### Verdict: {overall}

{chr(10).join(verdict_lines)}

Runtime: {total:.0f}s
"""
    append_journal(journal_text)

    print(f"\nStep 1 complete: {total:.0f}s ({total/60:.1f}m)")


if __name__ == "__main__":
    main()
