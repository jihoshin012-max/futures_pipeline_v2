# archetype: rotational
"""
rotational-NQ-scale-detection-step3.py -- Phase 1, Step 3: Asymmetry gate.

Gates entries when completion asymmetry (long vs short ratio) exceeds threshold.
High |asymmetry| = trending market = rotations are one-sided.

This tests whether W39's problem was directional (trending) rather than
scale mismatch (Step 2b showed scale was correct at 25-30pt).

Usage:
    python rotational-NQ-scale-detection-step3.py [--bar-file PATH]
"""
from __future__ import annotations

import csv
import importlib
import time
from collections import defaultdict
from pathlib import Path

import numpy as np
import numba as nb

_engine = importlib.import_module("rotational-NQ-scale-detection-engine")
load_bars_extended = _engine.load_bars_extended
aggregate_to_ntick = _engine.aggregate_to_ntick
assign_session_ids = _engine.assign_session_ids
zigzag = _engine.zigzag
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
#  Config
# ---------------------------------------------------------------------------
TEST_WEEKS = {
    "W39": {"start": 20250922, "end": 20250926, "category": "WORST"},
    "W48": {"start": 20251124, "end": 20251128, "category": "BAD"},
    "W45": {"start": 20251103, "end": 20251107, "category": "AVG"},
    "W44": {"start": 20251027, "end": 20251031, "category": "GOOD"},
    "W40": {"start": 20250929, "end": 20251003, "category": "GOOD"},
}

# Asymmetry thresholds to test: gate when |asymmetry| > threshold
ASYM_THRESHOLDS = [0.3, 0.5, 0.7]

# Time-based window for completion counting (agg bars)
# Using multiple to see sensitivity
ASYM_WINDOWS = [250, 500]

# ZZ threshold for asymmetry computation
ZZ_THRESHOLD = 15.0  # use 15pt since Step 2b showed it reads the right scale

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
        raise ValueError(f"No bars for {date_start}-{date_end}")
    result = {}
    for k in ["last", "high", "low", "open", "time_sec", "date_int", "atr"]:
        result[k] = bars[k][indices]
    result["datetime"] = [bars["datetime"][i] for i in indices]
    result["n"] = len(indices)
    return result


@nb.njit(cache=True)
def _count_long_short_time_window(swing_idx, swing_dir, n_bars, window):
    """Count long and short completions in a time-based rolling window."""
    n_swings = len(swing_idx)
    longs = np.zeros(n_bars, dtype=np.int32)
    shorts = np.zeros(n_bars, dtype=np.int32)

    if n_swings == 0:
        return longs, shorts

    fifo_bar = np.empty(n_swings, dtype=np.int64)
    fifo_dir = np.empty(n_swings, dtype=np.int8)
    fifo_head = 0
    fifo_tail = 0
    sp = 0
    cur_long = 0
    cur_short = 0

    for i in range(n_bars):
        while sp < n_swings and swing_idx[sp] <= i:
            fifo_bar[fifo_tail] = swing_idx[sp]
            fifo_dir[fifo_tail] = swing_dir[sp]
            fifo_tail += 1
            if swing_dir[sp] == 1:
                cur_long += 1
            else:
                cur_short += 1
            sp += 1

        cutoff = i - window
        while fifo_head < fifo_tail and fifo_bar[fifo_head] < cutoff:
            d = fifo_dir[fifo_head]
            fifo_head += 1
            if d == 1:
                cur_long -= 1
            else:
                cur_short -= 1

        longs[i] = cur_long
        shorts[i] = cur_short

    return longs, shorts


def precompute_asymmetry_signals(bars, bar_size, zz_threshold, asym_window):
    """Compute per-bar asymmetry from zigzag completions.

    asymmetry = (long - short) / (long + short), range [-1, 1].
    Positive = more longs completing (bullish trend).
    Negative = more shorts completing (bearish trend).
    Near zero = balanced rotations.
    """
    n_ticks = bars["n"]
    agg_bars, tick_to_agg = aggregate_to_ntick(bars, bar_size)
    n_agg = agg_bars["n"]
    agg_sids = assign_session_ids(agg_bars["date_int"])
    agg_prices = agg_bars["last"].astype(np.float64)

    # Run zigzag
    zz_idx, zz_price, zz_dir, zz_sid = zigzag(agg_prices, agg_sids, zz_threshold)

    # Count long/short in time window
    lc, sc = _count_long_short_time_window(zz_idx, zz_dir, n_agg, asym_window)

    # Compute asymmetry per agg bar
    asym_agg = np.zeros(n_agg, dtype=np.float64)
    for i in range(n_agg):
        total = lc[i] + sc[i]
        if total > 0:
            asym_agg[i] = (lc[i] - sc[i]) / total

    # Map to ticks
    asym_ticks = map_signal_to_ticks(asym_agg, tick_to_agg)

    # Find warmup
    warmup_tick = 0
    for i in range(n_ticks):
        total = lc[tick_to_agg[i]] + sc[tick_to_agg[i]]
        if total >= 4:  # need at least a few completions
            warmup_tick = i
            break

    return {
        "asymmetry": asym_ticks,
        "warmup_ticks": {"asymmetry": warmup_tick},
        "n_swings": len(zz_idx),
        "tick_to_agg": tick_to_agg,
    }


def make_asymmetry_filter(max_asymmetry):
    """Gate entry when |asymmetry| > threshold."""
    def filter_fn(signals, i, direction, step_dist):
        warmup = signals["warmup_ticks"]["asymmetry"]
        if i < warmup:
            return True
        asym = signals["asymmetry"][i]
        return abs(asym) <= max_asymmetry
    return filter_fn


def make_directional_asymmetry_filter(max_asymmetry):
    """Gate entry only in the counter-trend direction.

    If asymmetry > threshold (bullish), block SHORT entries.
    If asymmetry < -threshold (bearish), block LONG entries.
    Trend-direction entries still allowed.
    """
    def filter_fn(signals, i, direction, step_dist):
        warmup = signals["warmup_ticks"]["asymmetry"]
        if i < warmup:
            return True
        asym = signals["asymmetry"][i]
        if asym > max_asymmetry and direction == -1:  # bullish trend, block shorts
            return False
        if asym < -max_asymmetry and direction == 1:  # bearish trend, block longs
            return False
        return True
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


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Scale Detection -- Step 3")
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
                           "filter": "baseline", "window": "-", "threshold": "-", **bl})

        # Asymmetry filters
        for asym_win in ASYM_WINDOWS:
            signals = precompute_asymmetry_signals(
                week_bars, args.bar_size, ZZ_THRESHOLD, asym_win,
            )

            # Print asymmetry stats for this week
            asym = signals["asymmetry"]
            tsec = week_bars["time_sec"]
            rth_mask = (tsec >= RTH_OPEN_SEC) & (tsec <= RTH_CLOSE_SEC)
            rth_asym = asym[rth_mask]
            valid = rth_asym[~np.isnan(rth_asym)]
            if len(valid) > 0 and week_name == "W39" and asym_win == ASYM_WINDOWS[0]:
                print(f"\n  Asymmetry stats (W39, w={asym_win}):")
                print(f"    min={valid.min():.3f}, max={valid.max():.3f}, "
                      f"mean={valid.mean():.3f}, median={np.median(valid):.3f}")
                pct_above = [np.mean(np.abs(valid) > t) * 100 for t in ASYM_THRESHOLDS]
                for t, p in zip(ASYM_THRESHOLDS, pct_above):
                    print(f"    |asym| > {t}: {p:.1f}% of RTH bars")

            for asym_thresh in ASYM_THRESHOLDS:
                # Test 1: Block both directions when asymmetric
                filter_fn = make_asymmetry_filter(asym_thresh)
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

                label = f"asym<={asym_thresh} w={asym_win}"
                print(f"  {label:>20}: {fl['cycles']:>3} cyc ({retention:.0%} ret) | "
                      f"{fl['wr']:.0%} WR | {fl['sr']:.0%} SR (d{delta_sr:+.0%}) | "
                      f"${fl['pnl']:,.0f} (d${delta_pnl:+,.0f})")

                all_results.append({
                    "week": week_name, "category": week_cfg["category"],
                    "filter": f"both|{asym_thresh}", "window": asym_win,
                    "threshold": asym_thresh,
                    "retention": retention, "delta_sr": delta_sr,
                    "delta_pnl": delta_pnl, **fl,
                })

                # Test 2: Only block counter-trend entries
                dir_filter = make_directional_asymmetry_filter(asym_thresh)
                dir_cycles = run_sim_filtered(
                    week_bars, step_dist=SD, hard_stop=HS,
                    max_fades=MAX_FADES, max_levels=MAX_LEVELS,
                    max_contract_size=MAX_CONTRACT_SIZE,
                    signal_arrays=signals, filter_fn=dir_filter,
                )
                dl = compute_week_metrics(dir_cycles)
                d_retention = dl["cycles"] / bl["cycles"] if bl["cycles"] > 0 else 0
                d_delta_sr = dl["sr"] - bl["sr"]
                d_delta_pnl = dl["pnl"] - bl["pnl"]

                d_label = f"dir|{asym_thresh} w={asym_win}"
                print(f"  {d_label:>20}: {dl['cycles']:>3} cyc ({d_retention:.0%} ret) | "
                      f"{dl['wr']:.0%} WR | {dl['sr']:.0%} SR (d{d_delta_sr:+.0%}) | "
                      f"${dl['pnl']:,.0f} (d${d_delta_pnl:+,.0f})")

                all_results.append({
                    "week": week_name, "category": week_cfg["category"],
                    "filter": f"dir|{asym_thresh}", "window": asym_win,
                    "threshold": asym_thresh,
                    "retention": d_retention, "delta_sr": d_delta_sr,
                    "delta_pnl": d_delta_pnl, **dl,
                })

    # --- Summary ---
    print(f"\n{'='*70}")
    print(f"STEP 3 SUMMARY -- Asymmetry Gate (ZZ {ZZ_THRESHOLD:.0f}pt)")
    print(f"{'='*70}")

    # Show best configs per category
    for filt_type in ["both", "dir"]:
        print(f"\n  --- {filt_type.upper()} direction filter ---")
        print(f"  {'Week':>6} {'Cat':>6} {'Filter':>15} {'Cyc':>5} {'Ret':>5} "
              f"{'WR':>5} {'SR':>5} {'dSR':>6} {'PnL':>10} {'dPnL':>10}")
        print("  " + "-" * 85)
        for r in all_results:
            if r["filter"] == "baseline" or not r["filter"].startswith(filt_type):
                continue
            if r["window"] != ASYM_WINDOWS[0]:  # show only first window in summary
                continue
            ret_str = f"{r.get('retention', 1.0):.0%}"
            dsr_str = f"{r.get('delta_sr', 0):+.0%}"
            dpnl_str = f"${r.get('delta_pnl', 0):+,.0f}"
            print(f"  {r['week']:>6} {r['category']:>6} {r['filter']:>15} {r['cycles']:>5} "
                  f"{ret_str:>5} {r['wr']:.0%}  {r['sr']:.0%}  {dsr_str:>6} "
                  f"${r['pnl']:>9,.0f} {dpnl_str:>10}")

    # --- Pass/Fail ---
    print(f"\n{'='*70}")
    print(f"PASS/FAIL EVALUATION")
    print(f"{'='*70}")

    verdict_lines = []
    any_passed = False
    for filt_type in ["both", "dir"]:
        for asym_thresh in ASYM_THRESHOLDS:
            for asym_win in ASYM_WINDOWS:
                fname = f"{filt_type}|{asym_thresh}"
                bad_weeks = [r for r in all_results
                             if r["filter"] == fname and r["window"] == asym_win
                             and r["category"] in ("WORST", "BAD")]
                good_weeks = [r for r in all_results
                              if r["filter"] == fname and r["window"] == asym_win
                              and r["category"] == "GOOD"]

                if not bad_weeks:
                    continue

                bad_sr_improved = all(r.get("delta_sr", 0) < 0 for r in bad_weeks)
                bad_retention_ok = all(r.get("retention", 0) >= 0.50 for r in bad_weeks)
                good_not_hurt = True
                for r in good_weeks:
                    bl_pnl = [x for x in all_results
                              if x["week"] == r["week"] and x["filter"] == "baseline"][0]["pnl"]
                    if bl_pnl > 0 and r["pnl"] < bl_pnl * 0.7:
                        good_not_hurt = False

                passed = bad_sr_improved and bad_retention_ok and good_not_hurt
                if passed:
                    any_passed = True
                v = f"{filt_type}|{asym_thresh} w={asym_win}: {'PASS' if passed else 'FAIL'}"
                verdict_lines.append(v)
                print(f"  {v} (bad SR: {bad_sr_improved}, ret>50%: {bad_retention_ok}, good ok: {good_not_hurt})")

    overall = "PASS" if any_passed else "FAIL"

    # --- Save ---
    out_dir = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "step3-asymmetry-results.csv"
    fields = ["week", "category", "filter", "window", "threshold", "cycles", "wins", "wr",
              "stops", "sr", "revs", "eods", "pnl", "er",
              "retention", "delta_sr", "delta_pnl"]
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in all_results:
            w.writerow({k: round(v, 4) if isinstance(v, float) else v
                        for k, v in r.items() if k in fields})
    print(f"\nResults saved: {out_path}")

    # --- Audit + Journal ---
    total = time.time() - t0

    # Summary for audit
    summary_parts = []
    for filt_type in ["both", "dir"]:
        for asym_thresh in ASYM_THRESHOLDS:
            fname = f"{filt_type}|{asym_thresh}"
            bad_delta = [r.get("delta_sr", 0) for r in all_results
                         if r["filter"] == fname and r["window"] == ASYM_WINDOWS[0]
                         and r["category"] in ("WORST", "BAD")]
            if bad_delta:
                avg_dsr = sum(bad_delta) / len(bad_delta)
                summary_parts.append(f"{fname}: avg bad dSR={avg_dsr:+.1%}")

    append_audit("STEP_3_COMPLETE",
                 f"Asymmetry gate on 5 weeks. ZZ={ZZ_THRESHOLD:.0f}pt. "
                 f"{'; '.join(summary_parts[:4])}. Verdict: {overall}. "
                 f"Runtime: {total:.0f}s.")

    # Build per-week table for journal (best config from first window)
    table_lines = []
    table_lines.append("| Week | Cat | BL PnL | both|0.3 | dir|0.3 | both|0.5 | dir|0.5 |")
    table_lines.append("|---|---|---|---|---|---|---|")
    for week_name in TEST_WEEKS:
        bl_pnl = [r for r in all_results
                  if r["week"] == week_name and r["filter"] == "baseline"][0]["pnl"]
        cells = [f"${bl_pnl:,.0f}"]
        for fname in ["both|0.3", "dir|0.3", "both|0.5", "dir|0.5"]:
            matched = [r for r in all_results
                       if r["week"] == week_name and r["filter"] == fname
                       and r["window"] == ASYM_WINDOWS[0]]
            if matched:
                r = matched[0]
                cells.append(f"${r['pnl']:,.0f} ({r.get('retention',0):.0%})")
            else:
                cells.append("--")
        cat = TEST_WEEKS[week_name]["category"]
        table_lines.append(f"| {week_name} | {cat} | {' | '.join(cells)} |")

    journal_text = f"""## Step 3: Asymmetry gate

**Test:** Gate entries when |completion asymmetry| > threshold. Two modes: block both directions vs block only counter-trend.
**Data:** Same 5 weeks. ZZ threshold: {ZZ_THRESHOLD:.0f}pt.
**Thresholds:** {ASYM_THRESHOLDS}, Windows: {ASYM_WINDOWS}

### Per-week PnL (w={ASYM_WINDOWS[0]})

{chr(10).join(table_lines)}

### Verdict: {overall}

{chr(10).join('- ' + v for v in verdict_lines)}

Runtime: {total:.0f}s
"""
    append_journal(journal_text)

    print(f"\nStep 3 complete: {total:.0f}s ({total/60:.1f}m)")


if __name__ == "__main__":
    main()
