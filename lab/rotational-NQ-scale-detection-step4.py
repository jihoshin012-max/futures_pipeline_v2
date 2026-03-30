# archetype: rotational
"""
rotational-NQ-scale-detection-step4.py -- Feature-outcome correlation analysis.

Instead of another binary on/off filter test (Steps 1-3 all failed at that),
this script computes regime features for every baseline cycle and analyzes
which features predict which outcomes.

For each cycle: tag with regime state at entry (slope, R², choppiness, vol
imbalance) and analyze how outcomes (stop rate, PnL, depth) vary by feature.

Goal: identify which strategy parameter each feature is most predictive of,
so targeted interventions can be designed (not just entry gating).

Usage:
    python rotational-NQ-scale-detection-step4.py [--bar-file PATH]
"""
from __future__ import annotations

import csv
import importlib
import time
from pathlib import Path

import numpy as np

# Import from hyphenated filenames
_engine = importlib.import_module("rotational-NQ-scale-detection-engine")
load_bars_extended = _engine.load_bars_extended
aggregate_to_ntick = _engine.aggregate_to_ntick
compute_regime_signals = _engine.compute_regime_signals
map_signal_to_ticks = _engine.map_signal_to_ticks
append_audit = _engine.append_audit
append_journal = _engine.append_journal
REGIME_CHOP = _engine.REGIME_CHOP
REGIME_TREND = _engine.REGIME_TREND
REGIME_UNCLEAR = _engine.REGIME_UNCLEAR

_sweep = importlib.import_module("rotational-NQ-scale-detection-sweep")
run_sim_filtered = _sweep.run_sim_filtered
TICK_SIZE = _sweep.TICK_SIZE
COMMISSION_PER_RT_MINI = _sweep.COMMISSION_PER_RT_MINI

REGIME_LABELS = {REGIME_UNCLEAR: "unclear", REGIME_CHOP: "chop", REGIME_TREND: "trend"}


# ---------------------------------------------------------------------------
#  Test weeks (same as Steps 1-3)
# ---------------------------------------------------------------------------
TEST_WEEKS = {
    "W39": {"start": 20250922, "end": 20250926, "category": "WORST"},
    "W48": {"start": 20251124, "end": 20251128, "category": "BAD"},
    "W45": {"start": 20251103, "end": 20251107, "category": "AVG"},
    "W44": {"start": 20251027, "end": 20251031, "category": "GOOD"},
    "W40": {"start": 20250929, "end": 20251003, "category": "GOOD"},
}

# Lookback windows to compute features at
LOOKBACKS = [3, 5, 8, 12]

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
    for k in ["last", "high", "low", "open", "time_sec", "date_int", "atr",
              "bid_vol", "ask_vol"]:
        if k in bars:
            result[k] = bars[k][indices]
    result["datetime"] = [bars["datetime"][i] for i in indices]
    result["n"] = len(indices)
    return result


def precompute_regime(bars: dict, bar_size: int, lookback: int) -> dict:
    """Compute regime signals on agg bars, map to tick resolution."""
    agg_bars, tick_to_agg = aggregate_to_ntick(bars, bar_size)
    regime_signals = compute_regime_signals(agg_bars, lookback=lookback)

    return {
        "regime": map_signal_to_ticks(regime_signals["regime"].astype(np.float64),
                                       tick_to_agg),
        "r2": map_signal_to_ticks(regime_signals["r2"], tick_to_agg),
        "choppiness": map_signal_to_ticks(regime_signals["choppiness"], tick_to_agg),
        "vol_imbalance": map_signal_to_ticks(regime_signals["vol_imbalance"], tick_to_agg),
        "slope": map_signal_to_ticks(regime_signals["slope"], tick_to_agg),
    }


def tag_cycles(cycles: list[dict], signals: dict) -> list[dict]:
    """Tag each cycle with regime features at entry bar."""
    tagged = []
    for c in cycles:
        bar = c["seed_bar"]
        comm = COMMISSION_PER_RT_MINI * max(c.get("max_position", 2), 1)
        net_pnl = c["pnl_ticks"] * 5.0 - comm

        entry = {
            "cycle_id": c["cycle_id"],
            "seed_bar": bar,
            "seed_dt": c["seed_dt"],
            "exit_dt": c["exit_dt"],
            "direction": c["direction"],
            "exit_type": c["exit_type"],
            "depth": c["depth"],
            "pnl_ticks": c["pnl_ticks"],
            "net_pnl": net_pnl,
            "mfe_ticks": c["mfe_ticks"],
            "mae_ticks": c["mae_ticks"],
            "bars_held": c["bars_held"],
            "is_stop": c["exit_type"] == "HARD_STOP",
            "is_win": net_pnl >= 0,
        }

        # Tag with features at entry
        for feat in ["regime", "r2", "choppiness", "vol_imbalance", "slope"]:
            arr = signals[feat]
            if bar < len(arr):
                entry[feat] = float(arr[bar])
            else:
                entry[feat] = float("nan")

        entry["regime_label"] = REGIME_LABELS.get(int(entry["regime"]), "nan") \
            if not np.isnan(entry["regime"]) else "nan"

        tagged.append(entry)
    return tagged


def bucket_analysis(tagged_cycles: list[dict], feature: str,
                    edges: list[float]) -> list[dict]:
    """Bucket cycles by feature value and compute stats per bucket."""
    buckets = []
    labels = []
    for i in range(len(edges) - 1):
        labels.append(f"[{edges[i]:.2f}, {edges[i+1]:.2f})")
        buckets.append([])

    for c in tagged_cycles:
        val = c[feature]
        if np.isnan(val):
            continue
        for i in range(len(edges) - 1):
            if edges[i] <= val < edges[i + 1]:
                buckets[i].append(c)
                break
        else:
            # Handle val == last edge (inclusive upper bound for last bucket)
            if val == edges[-1] and len(buckets) > 0:
                buckets[-1].append(c)

    results = []
    for label, group in zip(labels, buckets):
        n = len(group)
        if n == 0:
            results.append({"bucket": label, "n": 0})
            continue
        stops = sum(1 for c in group if c["is_stop"])
        wins = sum(1 for c in group if c["is_win"])
        pnl = sum(c["net_pnl"] for c in group)
        d0 = sum(1 for c in group if c["depth"] == 0)
        d1 = sum(1 for c in group if c["depth"] >= 1)
        d1_stops = sum(1 for c in group if c["depth"] >= 1 and c["is_stop"])
        results.append({
            "bucket": label, "n": n,
            "stop_rate": stops / n,
            "win_rate": wins / n,
            "avg_pnl": pnl / n,
            "total_pnl": pnl,
            "d0_pct": d0 / n,
            "d1_pct": d1 / n,
            "d1_stop_rate": d1_stops / d1 if d1 > 0 else 0,
            "avg_mfe": sum(c["mfe_ticks"] for c in group) / n,
            "avg_mae": sum(c["mae_ticks"] for c in group) / n,
        })
    return results


def regime_analysis(tagged_cycles: list[dict]) -> list[dict]:
    """Group cycles by regime label and compute stats."""
    groups = {}
    for c in tagged_cycles:
        label = c["regime_label"]
        if label == "nan":
            continue
        groups.setdefault(label, []).append(c)

    results = []
    for label in ["chop", "unclear", "trend"]:
        group = groups.get(label, [])
        n = len(group)
        if n == 0:
            results.append({"regime": label, "n": 0})
            continue
        stops = sum(1 for c in group if c["is_stop"])
        wins = sum(1 for c in group if c["is_win"])
        pnl = sum(c["net_pnl"] for c in group)
        d0 = sum(1 for c in group if c["depth"] == 0)
        d1 = sum(1 for c in group if c["depth"] >= 1)
        d1_stops = sum(1 for c in group if c["depth"] >= 1 and c["is_stop"])
        results.append({
            "regime": label, "n": n,
            "stop_rate": stops / n,
            "win_rate": wins / n,
            "avg_pnl": pnl / n,
            "total_pnl": pnl,
            "d0_pct": d0 / n,
            "d1_pct": d1 / n,
            "d1_stop_rate": d1_stops / d1 if d1 > 0 else 0,
            "avg_mfe": sum(c["mfe_ticks"] for c in group) / n,
            "avg_mae": sum(c["mae_ticks"] for c in group) / n,
        })
    return results


def print_bucket_table(title: str, results: list[dict]):
    print(f"\n  {title}")
    print(f"  {'Bucket':>20} {'N':>5} {'SR':>6} {'WR':>6} {'Avg$':>8} "
          f"{'d0%':>5} {'d1%':>5} {'d1SR':>6} {'MFE':>6} {'MAE':>6}")
    print(f"  {'-'*85}")
    for r in results:
        if r["n"] == 0:
            print(f"  {r['bucket']:>20} {0:>5}")
            continue
        print(f"  {r.get('bucket', r.get('regime', '')):>20} {r['n']:>5} "
              f"{r['stop_rate']:>5.0%} {r['win_rate']:>5.0%} "
              f"${r['avg_pnl']:>7,.0f} "
              f"{r['d0_pct']:>4.0%} {r['d1_pct']:>4.0%} "
              f"{r['d1_stop_rate']:>5.0%} "
              f"{r['avg_mfe']:>5.0f} {r['avg_mae']:>5.0f}")


def print_regime_table(title: str, results: list[dict]):
    print(f"\n  {title}")
    print(f"  {'Regime':>10} {'N':>5} {'SR':>6} {'WR':>6} {'Avg$':>8} {'Total$':>10} "
          f"{'d0%':>5} {'d1%':>5} {'d1SR':>6} {'MFE':>6} {'MAE':>6}")
    print(f"  {'-'*90}")
    for r in results:
        if r["n"] == 0:
            print(f"  {r['regime']:>10} {0:>5}")
            continue
        print(f"  {r['regime']:>10} {r['n']:>5} "
              f"{r['stop_rate']:>5.0%} {r['win_rate']:>5.0%} "
              f"${r['avg_pnl']:>7,.0f} ${r['total_pnl']:>9,.0f} "
              f"{r['d0_pct']:>4.0%} {r['d1_pct']:>4.0%} "
              f"{r['d1_stop_rate']:>5.0%} "
              f"{r['avg_mfe']:>5.0f} {r['avg_mae']:>5.0f}")


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Scale Detection -- Step 4: Feature-Outcome Analysis")
    parser.add_argument("--bar-file", type=str,
                        default=r"C:\Projects\futures_pipeline\data\NQ-1tick-calibration.csv")
    parser.add_argument("--bar-size", type=int, default=250)
    args = parser.parse_args()

    print(f"Loading bars from {args.bar_file}...")
    t0 = time.time()
    all_bars = load_bars_extended(args.bar_file)
    print(f"Loaded {all_bars['n']} bars in {time.time()-t0:.1f}s")

    # Collect all tagged cycles across weeks for aggregate analysis
    all_tagged_by_lb: dict[int, list[dict]] = {lb: [] for lb in LOOKBACKS}

    # Also collect per-week summaries
    week_summaries = []

    for week_name, week_cfg in TEST_WEEKS.items():
        print(f"\n{'='*70}")
        print(f"WEEK: {week_name} ({week_cfg['start']}-{week_cfg['end']}) -- {week_cfg['category']}")
        print(f"{'='*70}")

        week_bars = extract_week(all_bars, week_cfg["start"], week_cfg["end"])
        print(f"  {week_bars['n']} bars")

        # Run baseline sim (no filter)
        baseline_cycles = run_sim_filtered(
            week_bars, step_dist=SD, hard_stop=HS,
            max_fades=MAX_FADES, max_levels=MAX_LEVELS,
            max_contract_size=MAX_CONTRACT_SIZE,
        )
        n_cycles = len(baseline_cycles)
        stops = sum(1 for c in baseline_cycles if c["exit_type"] == "HARD_STOP")
        total_pnl = sum(c["pnl_ticks"] * 5.0 - COMMISSION_PER_RT_MINI * max(c.get("max_position", 2), 1)
                        for c in baseline_cycles)
        print(f"  Baseline: {n_cycles} cycles, {stops/n_cycles:.0%} SR, ${total_pnl:,.0f}")

        for lookback in LOOKBACKS:
            signals = precompute_regime(week_bars, args.bar_size, lookback)
            tagged = tag_cycles(baseline_cycles, signals)

            # Add week metadata
            for t in tagged:
                t["week"] = week_name
                t["category"] = week_cfg["category"]
                t["lookback"] = lookback

            all_tagged_by_lb[lookback].extend(tagged)

            # Per-week regime breakdown
            regime_res = regime_analysis(tagged)
            print_regime_table(f"Regime breakdown (lb={lookback})", regime_res)

            week_summaries.append({
                "week": week_name, "category": week_cfg["category"],
                "lookback": lookback, "n_cycles": n_cycles,
                "regime_breakdown": regime_res,
            })

    # --- Aggregate analysis across all weeks ---
    print(f"\n{'='*70}")
    print(f"AGGREGATE ANALYSIS -- All 5 weeks combined")
    print(f"{'='*70}")

    # Feature bucket edges
    r2_edges = [0.0, 0.1, 0.2, 0.3, 0.5, 0.7, 1.01]
    chop_edges = [0.0, 0.05, 0.10, 0.20, 0.35, 0.50, 1.01]
    vol_edges = [0.0, 0.05, 0.10, 0.15, 0.25, 0.40, 1.01]

    for lookback in LOOKBACKS:
        tagged = all_tagged_by_lb[lookback]
        print(f"\n{'='*70}")
        print(f"LOOKBACK = {lookback} agg bars ({len(tagged)} total cycles)")
        print(f"{'='*70}")

        # Regime label analysis
        regime_res = regime_analysis(tagged)
        print_regime_table("By regime label", regime_res)

        # R² buckets
        r2_res = bucket_analysis(tagged, "r2", r2_edges)
        print_bucket_table("By R² at entry", r2_res)

        # Choppiness buckets
        chop_res = bucket_analysis(tagged, "choppiness", chop_edges)
        print_bucket_table("By choppiness ratio at entry", chop_res)

        # Vol imbalance buckets
        vol_res = bucket_analysis(tagged, "vol_imbalance", vol_edges)
        print_bucket_table("By vol imbalance at entry", vol_res)

        # --- Bad weeks vs good weeks regime comparison ---
        print(f"\n  --- Bad weeks (W39, W48) vs Good weeks (W44, W40) ---")
        bad = [c for c in tagged if c["category"] in ("WORST", "BAD")]
        good = [c for c in tagged if c["category"] == "GOOD"]

        if bad:
            bad_regime = regime_analysis(bad)
            print_regime_table("Bad weeks by regime", bad_regime)
        if good:
            good_regime = regime_analysis(good)
            print_regime_table("Good weeks by regime", good_regime)

    # --- Save all tagged cycles as CSV ---
    out_dir = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")
    out_dir.mkdir(parents=True, exist_ok=True)

    # Save one CSV per lookback
    for lookback in LOOKBACKS:
        tagged = all_tagged_by_lb[lookback]
        out_path = out_dir / f"step4-tagged-cycles-lb{lookback}.csv"
        fields = ["week", "category", "lookback", "cycle_id", "seed_dt", "exit_dt",
                  "direction", "exit_type", "depth", "pnl_ticks", "net_pnl",
                  "mfe_ticks", "mae_ticks", "bars_held", "is_stop", "is_win",
                  "regime", "regime_label", "r2", "choppiness", "vol_imbalance", "slope"]
        with open(out_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            for c in tagged:
                w.writerow({k: round(v, 6) if isinstance(v, float) else v
                            for k, v in c.items() if k in fields})
        print(f"\nSaved: {out_path}")

    # --- Journal entry ---
    total = time.time() - t0

    # Build summary for journal
    journal_lines = []
    for lookback in LOOKBACKS:
        tagged = all_tagged_by_lb[lookback]
        regime_res = regime_analysis(tagged)
        regime_str = ", ".join(
            f"{r['regime']}: {r['n']} ({r['stop_rate']:.0%} SR, ${r['avg_pnl']:+,.0f}/cyc)"
            for r in regime_res if r["n"] > 0
        )
        journal_lines.append(f"- lb={lookback}: {regime_str}")

    # Find most predictive feature
    best_separation = None
    for lookback in LOOKBACKS:
        tagged = all_tagged_by_lb[lookback]
        regime_res = regime_analysis(tagged)
        # Look for stop rate spread between regimes
        srs = {r["regime"]: r["stop_rate"] for r in regime_res if r["n"] >= 10}
        if "chop" in srs and "trend" in srs:
            spread = srs["trend"] - srs["chop"]
            if best_separation is None or spread > best_separation[1]:
                best_separation = (lookback, spread, srs)

    sep_note = ""
    if best_separation:
        lb, spread, srs = best_separation
        sep_note = (f"\n**Best regime separation:** lb={lb}, "
                   f"chop SR={srs.get('chop', 0):.0%}, "
                   f"trend SR={srs.get('trend', 0):.0%}, "
                   f"spread={spread:+.0%}")

    journal_text = f"""## Step 4: Feature-outcome correlation analysis

**Approach:** Instead of another on/off filter test, computed regime features (slope, R², choppiness, signed vol delta) at each baseline cycle's entry bar and analyzed how outcomes vary by feature.
**Data:** Same 5 weeks as Steps 1-3. SD=25 HS=125 depth_1.
**Lookbacks tested:** {LOOKBACKS} (agg bars)

### Regime breakdown (all weeks combined)

{chr(10).join(journal_lines)}
{sep_note}

### Feature bucket analysis

See `lab/output/rotational-NQ-scale-detection/step4-tagged-cycles-lb*.csv` for full per-cycle data with features.

Runtime: {total:.0f}s
"""
    append_audit("STEP_4_COMPLETE",
                 f"Feature-outcome correlation analysis on 5 weeks. "
                 f"Runtime: {total:.0f}s.")
    append_journal(journal_text)

    print(f"\nStep 4 complete: {total:.0f}s ({total/60:.1f}m)")


if __name__ == "__main__":
    main()
