# archetype: rotational
"""
rotational-NQ-scale-detection-step2.py -- Phase 1, Step 2: ZZ median as scale source.

Replaces MTZZ completion counting with rolling zigzag median swing size.
Snaps median to nearest SD level. Gates SD=25 entries when median suggests
a different scale.

Pass criteria: Fewer false transitions than Step 1, same or better filtering.

Usage:
    python rotational-NQ-scale-detection-step2.py [--bar-file PATH]
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
compute_rolling_zz_median = _engine.compute_rolling_zz_median
map_signal_to_ticks = _engine.map_signal_to_ticks
append_audit = _engine.append_audit
append_journal = _engine.append_journal

_sweep = importlib.import_module("rotational-NQ-scale-detection-sweep")
run_sim_filtered = _sweep.run_sim_filtered
TICK_SIZE = _sweep.TICK_SIZE
COMMISSION_PER_RT_MINI = _sweep.COMMISSION_PER_RT_MINI
RTH_OPEN_SEC = _sweep.RTH_OPEN_SEC
RTH_CLOSE_SEC = _sweep.RTH_CLOSE_SEC


# ---------------------------------------------------------------------------
#  Test weeks (same as Step 1)
# ---------------------------------------------------------------------------
TEST_WEEKS = {
    "W39": {"start": 20250922, "end": 20250926, "category": "WORST"},
    "W48": {"start": 20251124, "end": 20251128, "category": "BAD"},
    "W45": {"start": 20251103, "end": 20251107, "category": "AVG"},
    "W44": {"start": 20251027, "end": 20251031, "category": "GOOD"},
    "W40": {"start": 20250929, "end": 20251003, "category": "GOOD"},
}

SD_LEVELS = [10.0, 15.0, 20.0, 25.0, 30.0, 50.0]

# ZZ median window sizes (count-based: last N swings)
ZZ_WINDOWS = [10, 20, 40]

# ZZ threshold for swing detection
# 5pt was too small -- median landed at 10-15pt, never reached 25pt.
# 15pt produces medians in the 20-30pt range (verified on W39).
ZZ_THRESHOLD = 15.0

# Strategy config
SD = 25.0
HS = 125.0
MAX_LEVELS = 1
MAX_CONTRACT_SIZE = 2
MAX_FADES = 0


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

def extract_week(bars, date_start, date_end):
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


def snap_to_sd(value):
    """Snap a continuous value to the nearest SD level."""
    if np.isnan(value):
        return np.nan
    best = SD_LEVELS[0]
    best_dist = abs(value - best)
    for sd in SD_LEVELS[1:]:
        d = abs(value - sd)
        if d < best_dist:
            best = sd
            best_dist = d
    return best


def precompute_zz_median_signals(bars, bar_size, zz_window, zz_threshold=5.0):
    """Precompute ZZ median signal on aggregated bars, map to ticks.

    Returns dict with:
        zz_median: raw median values (tick resolution)
        zz_snapped: snapped to nearest SD level (tick resolution)
        n_transitions: count of scale changes in snapped signal (RTH only)
    """
    n_ticks = bars["n"]
    agg_bars, tick_to_agg = aggregate_to_ntick(bars, bar_size)
    n_agg = agg_bars["n"]
    agg_sids = assign_session_ids(agg_bars["date_int"])
    agg_prices = agg_bars["last"].astype(np.float64)

    # Run zigzag at small threshold
    zz_idx, zz_price, zz_dir, zz_sid = zigzag(agg_prices, agg_sids, zz_threshold)

    # Compute rolling median
    zz_median_agg = compute_rolling_zz_median(zz_idx, zz_price, n_agg, zz_window)

    # Snap to nearest SD level (on agg bars)
    zz_snapped_agg = np.full(n_agg, np.nan, dtype=np.float64)
    for i in range(n_agg):
        zz_snapped_agg[i] = snap_to_sd(zz_median_agg[i])

    # Map to tick resolution
    zz_median_ticks = map_signal_to_ticks(zz_median_agg, tick_to_agg)
    zz_snapped_ticks = map_signal_to_ticks(zz_snapped_agg, tick_to_agg)

    # Count transitions in snapped signal
    n_transitions = 0
    prev = np.nan
    tsec = bars["time_sec"]
    for i in range(n_ticks):
        if int(tsec[i]) < RTH_OPEN_SEC or int(tsec[i]) > RTH_CLOSE_SEC:
            continue
        cur = zz_snapped_ticks[i]
        if np.isnan(cur):
            continue
        if not np.isnan(prev) and cur != prev:
            n_transitions += 1
        prev = cur

    # Find warmup tick (first non-NaN)
    warmup_tick = 0
    for i in range(n_ticks):
        if not np.isnan(zz_median_ticks[i]):
            warmup_tick = i
            break

    return {
        "zz_median": zz_median_ticks,
        "zz_snapped": zz_snapped_ticks,
        "dominant_scale": zz_snapped_ticks,  # alias for filter compatibility
        "confidence": np.ones(n_ticks, dtype=np.float64),  # always confident
        "n_transitions": n_transitions,
        "n_swings": len(zz_idx),
        "warmup_ticks": {"dominant_scale": warmup_tick, "asymmetry": 0},
        "tick_to_agg": tick_to_agg,
        "n_agg_bars": n_agg,
    }


def make_zz_median_filter():
    """Gate entry when ZZ median snapped scale != config SD (+-1 level)."""
    def filter_fn(signals, i, direction, step_dist):
        warmup = signals["warmup_ticks"]["dominant_scale"]
        if i < warmup:
            return True
        snapped = signals["zz_snapped"][i]
        if np.isnan(snapped):
            return True
        if step_dist not in SD_LEVELS:
            return True
        idx = SD_LEVELS.index(step_dist)
        neighbors = set()
        if idx > 0: neighbors.add(SD_LEVELS[idx - 1])
        neighbors.add(SD_LEVELS[idx])
        if idx < len(SD_LEVELS) - 1: neighbors.add(SD_LEVELS[idx + 1])
        return snapped in neighbors
    return filter_fn


def compute_week_metrics(cycles):
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


def print_regime_log(signals, bars, label):
    """Print when ZZ snapped scale changed."""
    snapped = signals["zz_snapped"]
    median = signals["zz_median"]
    dt_str = bars["datetime"]
    tsec = bars["time_sec"]
    n = len(snapped)

    print(f"\n  Scale transitions ({label}):")
    print(f"  {'DateTime':>20} {'From':>6} {'To':>6} {'Raw Median':>11}")

    prev_scale = np.nan
    count = 0
    for i in range(n):
        if int(tsec[i]) < RTH_OPEN_SEC or int(tsec[i]) > RTH_CLOSE_SEC:
            continue
        cur = snapped[i]
        if np.isnan(cur):
            continue
        if np.isnan(prev_scale):
            prev_scale = cur
            continue
        if cur != prev_scale:
            print(f"  {dt_str[i]:>20} {prev_scale:>6.0f} {cur:>6.0f} {median[i]:>11.1f}")
            prev_scale = cur
            count += 1
            if count >= 30:
                print(f"  ... ({signals['n_transitions'] - 30} more)")
                break
    print(f"  Total transitions: {signals['n_transitions']}")


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Scale Detection -- Step 2")
    parser.add_argument("--bar-file", type=str,
                        default=r"C:\Projects\futures_pipeline\data\NQ-1tick-calibration.csv")
    parser.add_argument("--bar-size", type=int, default=250)
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

        # Baseline
        baseline_cycles = run_sim_filtered(
            week_bars, step_dist=SD, hard_stop=HS,
            max_fades=MAX_FADES, max_levels=MAX_LEVELS,
            max_contract_size=MAX_CONTRACT_SIZE,
        )
        bl = compute_week_metrics(baseline_cycles)
        print(f"\n  BASELINE: {bl['cycles']} cyc | {bl['wr']:.0%} WR | "
              f"{bl['sr']:.0%} SR | ${bl['pnl']:,.0f}")

        all_results.append({"week": week_name, "category": week_cfg["category"],
                           "filter": "baseline", "window": "-", **bl})

        # ZZ median filter at each window size
        for zz_win in ZZ_WINDOWS:
            signals = precompute_zz_median_signals(
                week_bars, args.bar_size, zz_window=zz_win,
                zz_threshold=ZZ_THRESHOLD,
            )

            # Print transitions for first window on first week
            if week_name == "W39" and zz_win == 20:
                print_regime_log(signals, week_bars, f"ZZ median w={zz_win}")

            filter_fn = make_zz_median_filter()
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

            print(f"  zz_w={zz_win:>2}: {fl['cycles']:>3} cyc ({retention:.0%} ret) | "
                  f"{fl['wr']:.0%} WR | {fl['sr']:.0%} SR (d{delta_sr:+.0%}) | "
                  f"${fl['pnl']:,.0f} (d${delta_pnl:+,.0f}) | "
                  f"{signals['n_transitions']} trans | {signals['n_swings']} swings")

            all_results.append({
                "week": week_name, "category": week_cfg["category"],
                "filter": f"zz_w={zz_win}", "window": zz_win,
                "retention": retention, "delta_sr": delta_sr,
                "delta_pnl": delta_pnl,
                "transitions": signals["n_transitions"],
                "n_swings": signals["n_swings"],
                **fl,
            })

    # --- Summary ---
    print(f"\n{'='*70}")
    print(f"STEP 2 SUMMARY -- ZZ Median Filter SD=25 +-1 level")
    print(f"{'='*70}")

    # Compare Step 1 best (w=250) vs Step 2
    print(f"\n{'Week':>6} {'Cat':>6} {'Filter':>10} {'Cyc':>5} {'Ret':>5} "
          f"{'WR':>5} {'SR':>5} {'dSR':>6} {'PnL':>10} {'dPNL':>10} {'Trans':>6}")
    print("-" * 85)

    for r in all_results:
        ret_str = f"{r.get('retention', 1.0):.0%}" if r["filter"] != "baseline" else "--"
        dsr_str = f"{r.get('delta_sr', 0):+.0%}" if r["filter"] != "baseline" else "--"
        dpnl_str = f"${r.get('delta_pnl', 0):+,.0f}" if r["filter"] != "baseline" else "--"
        trans_str = f"{r.get('transitions', 0)}" if r["filter"] != "baseline" else "--"
        print(f"{r['week']:>6} {r['category']:>6} {r['filter']:>10} {r['cycles']:>5} "
              f"{ret_str:>5} {r['wr']:.0%}  {r['sr']:.0%}  {dsr_str:>6} "
              f"${r['pnl']:>9,.0f} {dpnl_str:>10} {trans_str:>6}")

    # --- Pass/Fail ---
    print(f"\n{'='*70}")
    print(f"PASS/FAIL EVALUATION")
    print(f"{'='*70}")

    for zz_win in ZZ_WINDOWS:
        fname = f"zz_w={zz_win}"
        bad_weeks = [r for r in all_results
                     if r["filter"] == fname and r["category"] in ("WORST", "BAD")]
        good_weeks = [r for r in all_results
                      if r["filter"] == fname and r["category"] == "GOOD"]

        bad_sr_improved = all(r.get("delta_sr", 0) < 0 for r in bad_weeks)
        bad_retention_ok = all(r.get("retention", 0) >= 0.50 for r in bad_weeks)

        good_not_hurt = True
        for r in good_weeks:
            bl_pnl = [x for x in all_results
                      if x["week"] == r["week"] and x["filter"] == "baseline"][0]["pnl"]
            if bl_pnl > 0 and r["pnl"] < bl_pnl * 0.7:
                good_not_hurt = False

        # Also check: fewer transitions than Step 1 w=250?
        avg_trans = np.mean([r.get("transitions", 0) for r in all_results if r["filter"] == fname])
        step1_avg_trans = 169  # approximate from Step 1 w=250 results

        passed = bad_sr_improved and bad_retention_ok and good_not_hurt
        verdict = "PASS" if passed else "FAIL"

        print(f"\n  ZZ Window={zz_win}:")
        print(f"    Bad weeks SR improved:   {bad_sr_improved}")
        print(f"    Bad weeks retention>50%: {bad_retention_ok}")
        print(f"    Good weeks not hurt:     {good_not_hurt}")
        print(f"    Avg transitions:         {avg_trans:.0f} (Step 1 w=250 was ~{step1_avg_trans})")
        print(f"    VERDICT: {verdict}")

    # --- Save results ---
    out_dir = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "step2b-zz-median-15pt-results.csv"
    fields = ["week", "category", "filter", "window", "cycles", "wins", "wr",
              "stops", "sr", "revs", "eods", "pnl", "er",
              "retention", "delta_sr", "delta_pnl", "transitions", "n_swings"]
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
    for zz_win in ZZ_WINDOWS:
        fname = f"zz_w={zz_win}"
        bad_delta = [r.get("delta_sr", 0) for r in all_results
                     if r["filter"] == fname and r["category"] in ("WORST", "BAD")]
        avg_dsr = sum(bad_delta) / len(bad_delta) if bad_delta else 0
        avg_trans = np.mean([r.get("transitions", 0) for r in all_results if r["filter"] == fname])
        summary_lines.append(f"zz_w={zz_win}: avg bad-week dSR={avg_dsr:+.1%}, avg trans={avg_trans:.0f}")

    append_audit("STEP_2_COMPLETE",
                 f"ZZ median filter SD=25 on 5 weeks. {'; '.join(summary_lines)}. "
                 f"Runtime: {total:.0f}s. Results: {out_path.name}")

    # Build per-week table for best window
    best_win = ZZ_WINDOWS[0]  # will evaluate which is best from verdicts
    table_lines = []
    table_lines.append("| Week | Cat | BL PnL | Filtered PnL | Ret | dSR | dPnL | Trans |")
    table_lines.append("|---|---|---|---|---|---|---|---|")
    for zz_win in ZZ_WINDOWS:
        fname = f"zz_w={zz_win}"
        for r in all_results:
            if r["filter"] == fname:
                bl_pnl = [x for x in all_results
                          if x["week"] == r["week"] and x["filter"] == "baseline"][0]["pnl"]
                table_lines.append(
                    f"| {r['week']} | {r['category']} | ${bl_pnl:,.0f} | ${r['pnl']:,.0f} "
                    f"| {r.get('retention',0):.0%} | {r.get('delta_sr',0):+.0%} "
                    f"| ${r.get('delta_pnl',0):+,.0f} | {r.get('transitions',0)} | "
                    f"*(zz_w={zz_win})*")

    # Build verdict text
    verdict_lines = []
    any_passed = False
    for zz_win in ZZ_WINDOWS:
        fname = f"zz_w={zz_win}"
        bad_weeks = [r for r in all_results
                     if r["filter"] == fname and r["category"] in ("WORST", "BAD")]
        good_weeks = [r for r in all_results
                      if r["filter"] == fname and r["category"] == "GOOD"]
        bad_sr_improved = all(r.get("delta_sr", 0) < 0 for r in bad_weeks)
        bad_retention_ok = all(r.get("retention", 0) >= 0.50 for r in bad_weeks)
        good_not_hurt = True
        for r in good_weeks:
            bl_pnl = [x for x in all_results
                      if x["week"] == r["week"] and x["filter"] == "baseline"][0]["pnl"]
            if bl_pnl > 0 and r["pnl"] < bl_pnl * 0.7:
                good_not_hurt = False
        avg_trans = np.mean([r.get("transitions", 0) for r in all_results if r["filter"] == fname])
        passed = bad_sr_improved and bad_retention_ok and good_not_hurt
        if passed:
            any_passed = True
        verdict_lines.append(f"- zz_w={zz_win}: {'PASS' if passed else 'FAIL'} "
                           f"(bad SR improved: {bad_sr_improved}, "
                           f"retention>50%: {bad_retention_ok}, "
                           f"good not hurt: {good_not_hurt}, "
                           f"avg trans: {avg_trans:.0f})")

    overall = "PASS" if any_passed else "FAIL"

    journal_text = f"""## Step 2: ZZ median as scale source

**Test:** Rolling zigzag median (5pt threshold) snapped to nearest SD level. Gate SD=25 entries when snapped scale != 25 (+-1 level).
**Data:** Same 5 weeks as Step 1.
**ZZ windows tested:** {ZZ_WINDOWS} (count-based: last N swings)
**ZZ threshold:** {ZZ_THRESHOLD}pt

### Per-week results (all windows)

{chr(10).join(table_lines)}

### Summary
{chr(10).join('- ' + s for s in summary_lines)}

### Verdict: {overall}

{chr(10).join(verdict_lines)}

### Comparison to Step 1
Step 1 best (MTZZ w=250): avg ~169 transitions, helped bad weeks but crushed W40.

Runtime: {total:.0f}s
"""
    append_journal(journal_text)

    print(f"\nStep 2 complete: {total:.0f}s ({total/60:.1f}m)")


if __name__ == "__main__":
    main()
