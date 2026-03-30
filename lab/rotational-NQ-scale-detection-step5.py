# archetype: rotational
"""
rotational-NQ-scale-detection-step5.py -- Steps 5+6: Feature discovery on SD=10 baseline.

Computes derived features (slope, R2, choppiness, vol_imbalance) at each
cycle entry bar on 250-tick aggregated data. Then analyzes feature-outcome
correlations individually.

Baseline: SD=10 HS=60 depth_1 MCS=2 (config_id 8).
Test weeks: W42(worst), W50(bad), W45(avg), W46(good), W48(good).

Usage:
    python rotational-NQ-scale-detection-step5.py [--bar-file PATH]
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

LOOKBACKS = [3, 5, 8, 12]

SD = 10.0
HS = 60.0
MAX_LEVELS = 1
MAX_CONTRACT_SIZE = 2
MAX_FADES = 0
BAR_SIZE = 250


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


def tag_cycles_with_features(cycles, feature_signals, tick_to_agg):
    """Add feature values at entry bar to each cycle record."""
    for c in cycles:
        entry_bar = c["seed_bar"]
        if entry_bar < len(tick_to_agg):
            agg_i = tick_to_agg[entry_bar]
            for feat_name in ["slope", "r2", "choppiness", "vol_imbalance",
                              "signed_price_vol", "cum_delta", "bar_duration",
                              "skew", "kurtosis", "entropy", "regime"]:
                arr = feature_signals.get(feat_name)
                if arr is not None and agg_i < len(arr):
                    val = arr[agg_i]
                    c[f"feat_{feat_name}"] = float(val) if not np.isnan(val) else None
                else:
                    c[f"feat_{feat_name}"] = None
        # Compute net PnL
        max_pos = max(c.get("max_position", 1), 1)
        comm = COMMISSION_PER_RT_MINI * max_pos
        c["net_pnl"] = c["pnl_ticks"] * 5.0 - comm
        c["is_stop"] = 1 if c["exit_type"] == "HARD_STOP" else 0
        c["is_d1_stop"] = 1 if (c["exit_type"] == "HARD_STOP" and c["depth"] >= 1) else 0


def bucket_analysis(cycles, feature_name, bucket_edges, label=""):
    """Analyze outcomes by feature buckets."""
    buckets = defaultdict(lambda: {"n": 0, "stops": 0, "d1_stops": 0, "pnl": 0.0,
                                    "d1_total": 0})
    for c in cycles:
        val = c.get(f"feat_{feature_name}")
        if val is None:
            continue
        # Find bucket
        bucket_label = f">={bucket_edges[-1]}"
        for i in range(len(bucket_edges) - 1):
            if val < bucket_edges[i + 1]:
                bucket_label = f"{bucket_edges[i]:.2f}-{bucket_edges[i+1]:.2f}"
                break
        b = buckets[bucket_label]
        b["n"] += 1
        b["stops"] += c["is_stop"]
        b["d1_stops"] += c["is_d1_stop"]
        b["pnl"] += c["net_pnl"]
        if c["depth"] >= 1:
            b["d1_total"] += 1

    if not buckets:
        return

    print(f"\n  {label} -- {feature_name}:")
    print(f"  {'Bucket':>20} {'N':>6} {'SR':>6} {'d1 SR':>7} {'Avg $/cyc':>10}")
    print(f"  {'-'*52}")
    for bk in sorted(buckets.keys()):
        b = buckets[bk]
        sr = b["stops"] / b["n"] if b["n"] > 0 else 0
        d1_sr = b["d1_stops"] / b["d1_total"] if b["d1_total"] > 0 else 0
        avg_pnl = b["pnl"] / b["n"] if b["n"] > 0 else 0
        print(f"  {bk:>20} {b['n']:>6} {sr:>6.0%} {d1_sr:>7.0%} {avg_pnl:>10.0f}")


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Scale Detection -- Step 5+6")
    parser.add_argument("--bar-file", type=str,
                        default=r"C:\Projects\futures_pipeline\data\NQ-1tick-calibration.csv")
    args = parser.parse_args()

    print(f"Loading bars from {args.bar_file}...")
    t0 = time.time()
    all_bars = load_bars_extended(args.bar_file)
    print(f"Loaded {all_bars['n']} bars in {time.time()-t0:.1f}s")

    out_dir = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")
    out_dir.mkdir(parents=True, exist_ok=True)

    for lb in LOOKBACKS:
        print(f"\n{'='*70}")
        print(f"LOOKBACK = {lb} agg bars")
        print(f"{'='*70}")

        all_tagged_cycles = []

        for week_name, week_cfg in TEST_WEEKS.items():
            week_bars = extract_week(all_bars, week_cfg["start"], week_cfg["end"])

            # Aggregate to 250-tick
            agg_bars, tick_to_agg = aggregate_to_ntick(week_bars, BAR_SIZE)

            # Compute regime features on agg bars
            features = compute_regime_signals(agg_bars, lookback=lb)

            # Run baseline sim
            cycles = run_sim_filtered(
                week_bars, step_dist=SD, hard_stop=HS,
                max_fades=MAX_FADES, max_levels=MAX_LEVELS,
                max_contract_size=MAX_CONTRACT_SIZE,
            )

            # Tag cycles with features
            tag_cycles_with_features(cycles, features, tick_to_agg)

            # Add week metadata
            for c in cycles:
                c["week"] = week_name
                c["category"] = week_cfg["category"]

            n = len(cycles)
            stops = sum(c["is_stop"] for c in cycles)
            total_pnl = sum(c["net_pnl"] for c in cycles)
            sr = stops / n if n > 0 else 0
            avg_pnl = total_pnl / n if n > 0 else 0
            print(f"\n  {week_name} ({week_cfg['category']}): {n} cyc, {sr:.0%} SR, ${avg_pnl:.0f}/cyc, ${total_pnl:,.0f} total")

            all_tagged_cycles.extend(cycles)

        # --- Step 6: Feature-outcome analysis ---
        print(f"\n{'='*70}")
        print(f"STEP 6: Feature-outcome analysis (lb={lb})")
        print(f"{'='*70}")

        # R2 buckets
        bucket_analysis(all_tagged_cycles, "r2",
                       [0.0, 0.10, 0.20, 0.30, 0.50, 0.70, 1.01],
                       f"lb={lb}")

        # Choppiness buckets
        bucket_analysis(all_tagged_cycles, "choppiness",
                       [0.0, 0.05, 0.10, 0.20, 0.35, 0.50, 1.01],
                       f"lb={lb}")

        # Vol imbalance buckets
        bucket_analysis(all_tagged_cycles, "vol_imbalance",
                       [0.0, 0.05, 0.10, 0.15, 0.25, 0.40, 1.01],
                       f"lb={lb}")

        # Slope (absolute value) buckets
        abs_slopes = [abs(c.get("feat_slope", 0) or 0) for c in all_tagged_cycles]
        if abs_slopes:
            p50 = np.median(abs_slopes)
            p75 = np.percentile(abs_slopes, 75)
            p90 = np.percentile(abs_slopes, 90)
            # Add abs_slope to cycles for bucketing
            for c in all_tagged_cycles:
                s = c.get("feat_slope")
                c["feat_abs_slope"] = abs(s) if s is not None else None
            bucket_analysis(all_tagged_cycles, "abs_slope",
                           [0.0, p50/2, p50, p75, p90, p90*2],
                           f"lb={lb}")

        # Signed price volume buckets
        bucket_analysis(all_tagged_cycles, "signed_price_vol",
                       [0.0, 0.10, 0.20, 0.30, 0.50, 0.70, 1.01],
                       f"lb={lb}")

        # Cumulative delta buckets (data-dependent, use percentiles)
        cd_vals = [c.get("feat_cum_delta") for c in all_tagged_cycles
                   if c.get("feat_cum_delta") is not None]
        if cd_vals:
            cd_arr = np.array(cd_vals)
            cd_p25 = np.percentile(cd_arr, 25)
            cd_p50 = np.percentile(cd_arr, 50)
            cd_p75 = np.percentile(cd_arr, 75)
            cd_p90 = np.percentile(cd_arr, 90)
            bucket_analysis(all_tagged_cycles, "cum_delta",
                           [0.0, cd_p25, cd_p50, cd_p75, cd_p90, cd_p90*2],
                           f"lb={lb}")

        # Bar duration buckets
        dur_vals = [c.get("feat_bar_duration") for c in all_tagged_cycles
                    if c.get("feat_bar_duration") is not None]
        if dur_vals:
            dur_arr = np.array(dur_vals)
            dur_p25 = np.percentile(dur_arr, 25)
            dur_p50 = np.percentile(dur_arr, 50)
            dur_p75 = np.percentile(dur_arr, 75)
            dur_p90 = np.percentile(dur_arr, 90)
            bucket_analysis(all_tagged_cycles, "bar_duration",
                           [0.0, dur_p25, dur_p50, dur_p75, dur_p90, dur_p90*2],
                           f"lb={lb}")

        # Skew buckets (absolute value — direction of skew)
        bucket_analysis(all_tagged_cycles, "skew",
                       [-3.0, -1.0, -0.5, -0.2, 0.2, 0.5, 1.0, 3.01],
                       f"lb={lb}")

        # Kurtosis buckets (excess kurtosis — 0 = normal, positive = fat tails)
        bucket_analysis(all_tagged_cycles, "kurtosis",
                       [-2.0, -1.0, 0.0, 1.0, 2.0, 5.0, 20.01],
                       f"lb={lb}")

        # Entropy buckets (0-1 normalized — 0 = predictable, 1 = max uncertainty)
        bucket_analysis(all_tagged_cycles, "entropy",
                       [0.0, 0.3, 0.5, 0.6, 0.7, 0.8, 0.9, 1.01],
                       f"lb={lb}")

        # Regime label breakdown
        regime_stats = defaultdict(lambda: {"n": 0, "stops": 0, "pnl": 0.0,
                                             "d1_total": 0, "d1_stops": 0})
        for c in all_tagged_cycles:
            r = c.get("feat_regime")
            if r is None:
                continue
            label = {0: "unclear", 1: "chop", 2: "trend"}.get(int(r), "?")
            s = regime_stats[label]
            s["n"] += 1
            s["stops"] += c["is_stop"]
            s["pnl"] += c["net_pnl"]
            if c["depth"] >= 1:
                s["d1_total"] += 1
                s["d1_stops"] += c["is_d1_stop"]

        print(f"\n  Regime breakdown (lb={lb}):")
        print(f"  {'Regime':>10} {'N':>6} {'SR':>6} {'d1 SR':>7} {'Avg $/cyc':>10}")
        print(f"  {'-'*42}")
        for label in ["chop", "unclear", "trend"]:
            s = regime_stats.get(label, {"n": 0, "stops": 0, "pnl": 0, "d1_total": 0, "d1_stops": 0})
            if s["n"] == 0:
                continue
            sr = s["stops"] / s["n"]
            d1_sr = s["d1_stops"] / s["d1_total"] if s["d1_total"] > 0 else 0
            avg = s["pnl"] / s["n"]
            print(f"  {label:>10} {s['n']:>6} {sr:>6.0%} {d1_sr:>7.0%} {avg:>10.0f}")

        # Bad vs good week split
        for cat_group, cat_label in [
            (["WORST", "BAD"], "Bad weeks"),
            (["GOOD"], "Good weeks"),
            (["AVG"], "Avg weeks"),
        ]:
            cat_cycles = [c for c in all_tagged_cycles if c["category"] in cat_group]
            if not cat_cycles:
                continue
            print(f"\n  --- {cat_label} only (lb={lb}) ---")
            bucket_analysis(cat_cycles, "r2",
                           [0.0, 0.10, 0.20, 0.30, 0.50, 0.70, 1.01],
                           f"{cat_label}")
            bucket_analysis(cat_cycles, "choppiness",
                           [0.0, 0.05, 0.10, 0.20, 0.35, 0.50, 1.01],
                           f"{cat_label}")
            bucket_analysis(cat_cycles, "vol_imbalance",
                           [0.0, 0.05, 0.10, 0.15, 0.25, 0.40, 1.01],
                           f"{cat_label}")
            bucket_analysis(cat_cycles, "signed_price_vol",
                           [0.0, 0.10, 0.20, 0.30, 0.50, 0.70, 1.01],
                           f"{cat_label}")
            bucket_analysis(cat_cycles, "kurtosis",
                           [-2.0, -1.0, 0.0, 1.0, 2.0, 5.0, 20.01],
                           f"{cat_label}")
            bucket_analysis(cat_cycles, "entropy",
                           [0.0, 0.3, 0.5, 0.6, 0.7, 0.8, 0.9, 1.01],
                           f"{cat_label}")

        # Save tagged cycles
        out_path = out_dir / f"step5c-tagged-cycles-sd10-lb{lb}.csv"
        fields = ["week", "category", "cycle_id", "seed_dt", "exit_dt", "direction",
                  "exit_type", "depth", "max_position", "pnl_ticks", "net_pnl",
                  "is_stop", "is_d1_stop", "mfe_ticks", "mae_ticks",
                  "feat_slope", "feat_r2", "feat_choppiness",
                  "feat_vol_imbalance", "feat_signed_price_vol",
                  "feat_cum_delta", "feat_bar_duration",
                  "feat_skew", "feat_kurtosis", "feat_entropy", "feat_regime"]
        with open(out_path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            for c in all_tagged_cycles:
                row = {}
                for k in fields:
                    v = c.get(k, "")
                    if isinstance(v, float):
                        row[k] = f"{v:.4f}" if abs(v) < 100 else f"{v:.2f}"
                    else:
                        row[k] = v
                w.writerow(row)
        print(f"\n  Saved: {out_path}")

    # --- Audit + Journal ---
    total = time.time() - t0

    append_audit("STEP_5_6_COMPLETE",
                 f"Feature discovery on SD=10 HS=60 baseline. 5 weeks, lookbacks {LOOKBACKS}. "
                 f"Runtime: {total:.0f}s.")

    journal_text = f"""## Step 5+6: Feature discovery on SD=10 HS=60 baseline

**Baseline:** SD=10 HS=60 depth_1 MCS=2 (config_id 8)
**Data:** W42(worst), W50(bad), W45(avg), W46(good), W48(good)
**Lookbacks:** {LOOKBACKS} (agg bars)
**Features:** slope, R2, choppiness, vol_imbalance, regime label

See `lab/output/rotational-NQ-scale-detection/step5-tagged-cycles-sd10-lb*.csv` for full per-cycle data.

Runtime: {total:.0f}s
"""
    append_journal(journal_text)

    print(f"\nStep 5+6 complete: {total:.0f}s ({total/60:.1f}m)")


if __name__ == "__main__":
    main()
