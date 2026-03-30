# archetype: rotational
"""
rotational-NQ-scale-detection-step7.py -- Step 7: Pairwise feature combinations.

Tests whether combining choppiness + slope + R2 (the three features with signal)
produces stronger outcome separation than any single feature.

Uses the tagged cycle CSVs from Step 5c (lb=3, which showed the strongest signal).

Usage:
    python rotational-NQ-scale-detection-step7.py
"""
from __future__ import annotations

import csv
import importlib
from collections import defaultdict
from pathlib import Path

import numpy as np

_engine = importlib.import_module("rotational-NQ-scale-detection-engine")
append_audit = _engine.append_audit
append_journal = _engine.append_journal


# ---------------------------------------------------------------------------
#  Load tagged cycles
# ---------------------------------------------------------------------------

def load_tagged_cycles(path):
    with open(path) as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for k in ["pnl_ticks", "net_pnl", "mfe_ticks", "mae_ticks",
                   "feat_slope", "feat_r2", "feat_choppiness",
                   "feat_vol_imbalance", "feat_signed_price_vol",
                   "feat_cum_delta", "feat_bar_duration",
                   "feat_skew", "feat_kurtosis", "feat_entropy"]:
            try:
                r[k] = float(r[k]) if r.get(k, "") != "" else None
            except (ValueError, TypeError):
                r[k] = None
        for k in ["is_stop", "is_d1_stop", "depth", "max_position"]:
            try:
                r[k] = int(r[k]) if r.get(k, "") != "" else 0
            except (ValueError, TypeError):
                r[k] = 0
    return rows


def bucket_2d(cycles, feat_a, feat_b, thresh_a, thresh_b, label=""):
    """2D bucket analysis: above/below threshold for two features."""
    groups = {
        f"lo_{feat_a}+lo_{feat_b}": [],
        f"lo_{feat_a}+hi_{feat_b}": [],
        f"hi_{feat_a}+lo_{feat_b}": [],
        f"hi_{feat_a}+hi_{feat_b}": [],
    }
    for c in cycles:
        va = c.get(f"feat_{feat_a}")
        vb = c.get(f"feat_{feat_b}")
        if va is None or vb is None:
            continue
        a_lo = abs(va) < thresh_a if feat_a == "slope" else va < thresh_a
        b_lo = abs(vb) < thresh_b if feat_b == "slope" else vb < thresh_b
        key_a = f"lo_{feat_a}" if a_lo else f"hi_{feat_a}"
        key_b = f"lo_{feat_b}" if b_lo else f"hi_{feat_b}"
        groups[f"{key_a}+{key_b}"].append(c)

    print(f"\n  {label} -- {feat_a} (thresh={thresh_a}) x {feat_b} (thresh={thresh_b}):")
    print(f"  {'Group':>30} {'N':>6} {'SR':>6} {'d1 SR':>7} {'Avg $/cyc':>10}")
    print(f"  {'-'*62}")
    for gname in sorted(groups.keys()):
        gc = groups[gname]
        n = len(gc)
        if n == 0:
            continue
        stops = sum(c["is_stop"] for c in gc)
        d1_stops = sum(c["is_d1_stop"] for c in gc)
        d1_total = sum(1 for c in gc if c["depth"] >= 1)
        pnl = sum(c["net_pnl"] for c in gc)
        sr = stops / n
        d1_sr = d1_stops / d1_total if d1_total > 0 else 0
        avg = pnl / n
        print(f"  {gname:>30} {n:>6} {sr:>6.0%} {d1_sr:>7.0%} {avg:>10.0f}")


def multi_threshold_analysis(cycles, conditions, label=""):
    """Test a specific multi-condition filter: how many pass, and what are their outcomes?"""
    passed = []
    blocked = []
    for c in cycles:
        all_pass = True
        for feat_name, op, thresh in conditions:
            val = c.get(f"feat_{feat_name}")
            if val is None:
                all_pass = False
                break
            if feat_name == "slope":
                val = abs(val)
            if op == "<" and val >= thresh:
                all_pass = False
                break
            elif op == ">=" and val < thresh:
                all_pass = False
                break
        if all_pass:
            passed.append(c)
        else:
            blocked.append(c)

    def stats(group):
        if not group:
            return {"n": 0, "sr": 0, "d1_sr": 0, "avg_pnl": 0, "total_pnl": 0}
        n = len(group)
        stops = sum(c["is_stop"] for c in group)
        d1_stops = sum(c["is_d1_stop"] for c in group)
        d1_total = sum(1 for c in group if c["depth"] >= 1)
        pnl = sum(c["net_pnl"] for c in group)
        return {
            "n": n,
            "sr": stops / n,
            "d1_sr": d1_stops / d1_total if d1_total > 0 else 0,
            "avg_pnl": pnl / n,
            "total_pnl": pnl,
        }

    p = stats(passed)
    b = stats(blocked)
    total = len(cycles)
    retention = p["n"] / total if total > 0 else 0

    cond_str = " AND ".join(f"{f}{'(abs)' if f=='slope' else ''}{op}{t}" for f, op, t in conditions)
    print(f"\n  {label}: {cond_str}")
    print(f"  {'':>10} {'N':>6} {'Ret':>5} {'SR':>6} {'d1 SR':>7} {'Avg $':>8} {'Total $':>10}")
    print(f"  {'Passed':>10} {p['n']:>6} {retention:>5.0%} {p['sr']:>6.0%} {p['d1_sr']:>7.0%} {p['avg_pnl']:>8.0f} {p['total_pnl']:>10,.0f}")
    print(f"  {'Blocked':>10} {b['n']:>6} {'':>5} {b['sr']:>6.0%} {b['d1_sr']:>7.0%} {b['avg_pnl']:>8.0f} {b['total_pnl']:>10,.0f}")

    return p, b, retention


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def main():
    data_dir = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")
    lb = 3

    # Load tagged cycles
    path = data_dir / f"step5c-tagged-cycles-sd10-lb{lb}.csv"
    print(f"Loading {path}...")
    cycles = load_tagged_cycles(path)
    print(f"Loaded {len(cycles)} cycles")

    # Split by week category
    bad_cycles = [c for c in cycles if c["category"] in ("WORST", "BAD")]
    good_cycles = [c for c in cycles if c["category"] == "GOOD"]
    avg_cycles = [c for c in cycles if c["category"] == "AVG"]

    print(f"  Bad: {len(bad_cycles)}, Good: {len(good_cycles)}, Avg: {len(avg_cycles)}")

    # --- 2D Buckets ---
    print(f"\n{'='*70}")
    print(f"2D BUCKET ANALYSIS (lb={lb})")
    print(f"{'='*70}")

    # Choppiness x Slope
    bucket_2d(cycles, "choppiness", "slope", 0.15, 3.0, "All weeks")
    bucket_2d(bad_cycles, "choppiness", "slope", 0.15, 3.0, "Bad weeks")
    bucket_2d(good_cycles, "choppiness", "slope", 0.15, 3.0, "Good weeks")

    # Choppiness x R2
    bucket_2d(cycles, "choppiness", "r2", 0.15, 0.30, "All weeks")
    bucket_2d(bad_cycles, "choppiness", "r2", 0.15, 0.30, "Bad weeks")
    bucket_2d(good_cycles, "choppiness", "r2", 0.15, 0.30, "Good weeks")

    # Slope x R2
    bucket_2d(cycles, "slope", "r2", 3.0, 0.30, "All weeks")

    # --- Multi-condition filter candidates ---
    print(f"\n{'='*70}")
    print(f"MULTI-CONDITION FILTER CANDIDATES (lb={lb})")
    print(f"{'='*70}")

    # Single-feature baselines for comparison
    print(f"\n--- Single feature baselines ---")
    multi_threshold_analysis(cycles, [("choppiness", "<", 0.10)], "All")
    multi_threshold_analysis(bad_cycles, [("choppiness", "<", 0.10)], "Bad")
    multi_threshold_analysis(good_cycles, [("choppiness", "<", 0.10)], "Good")

    multi_threshold_analysis(cycles, [("slope", "<", 2.0)], "All")
    multi_threshold_analysis(bad_cycles, [("slope", "<", 2.0)], "Bad")
    multi_threshold_analysis(good_cycles, [("slope", "<", 2.0)], "Good")

    # Pairwise combinations
    print(f"\n--- Choppiness + Slope ---")
    multi_threshold_analysis(cycles,
        [("choppiness", "<", 0.10), ("slope", "<", 3.0)], "All")
    multi_threshold_analysis(bad_cycles,
        [("choppiness", "<", 0.10), ("slope", "<", 3.0)], "Bad")
    multi_threshold_analysis(good_cycles,
        [("choppiness", "<", 0.10), ("slope", "<", 3.0)], "Good")

    print(f"\n--- Choppiness + R2 ---")
    multi_threshold_analysis(cycles,
        [("choppiness", "<", 0.10), ("r2", "<", 0.30)], "All")
    multi_threshold_analysis(bad_cycles,
        [("choppiness", "<", 0.10), ("r2", "<", 0.30)], "Bad")
    multi_threshold_analysis(good_cycles,
        [("choppiness", "<", 0.10), ("r2", "<", 0.30)], "Good")

    print(f"\n--- Slope + R2 ---")
    multi_threshold_analysis(cycles,
        [("slope", "<", 2.0), ("r2", "<", 0.30)], "All")
    multi_threshold_analysis(bad_cycles,
        [("slope", "<", 2.0), ("r2", "<", 0.30)], "Bad")
    multi_threshold_analysis(good_cycles,
        [("slope", "<", 2.0), ("r2", "<", 0.30)], "Good")

    # Triple combination
    print(f"\n--- All three: Choppiness + Slope + R2 ---")
    multi_threshold_analysis(cycles,
        [("choppiness", "<", 0.10), ("slope", "<", 3.0), ("r2", "<", 0.30)], "All")
    multi_threshold_analysis(bad_cycles,
        [("choppiness", "<", 0.10), ("slope", "<", 3.0), ("r2", "<", 0.30)], "Bad")
    multi_threshold_analysis(good_cycles,
        [("choppiness", "<", 0.10), ("slope", "<", 3.0), ("r2", "<", 0.30)], "Good")

    # Looser thresholds
    print(f"\n--- Looser: choppiness < 0.20 + slope < 3.0 ---")
    multi_threshold_analysis(cycles,
        [("choppiness", "<", 0.20), ("slope", "<", 3.0)], "All")
    multi_threshold_analysis(bad_cycles,
        [("choppiness", "<", 0.20), ("slope", "<", 3.0)], "Bad")
    multi_threshold_analysis(good_cycles,
        [("choppiness", "<", 0.20), ("slope", "<", 3.0)], "Good")

    # Tighter thresholds
    print(f"\n--- Tighter: choppiness < 0.05 + slope < 2.0 ---")
    multi_threshold_analysis(cycles,
        [("choppiness", "<", 0.05), ("slope", "<", 2.0)], "All")
    multi_threshold_analysis(bad_cycles,
        [("choppiness", "<", 0.05), ("slope", "<", 2.0)], "Bad")
    multi_threshold_analysis(good_cycles,
        [("choppiness", "<", 0.05), ("slope", "<", 2.0)], "Good")

    # --- Per-week breakdown for best candidates ---
    print(f"\n{'='*70}")
    print(f"PER-WEEK BREAKDOWN -- best candidates")
    print(f"{'='*70}")

    weeks = sorted(set(c["week"] for c in cycles))
    candidates = [
        ("chop<0.10", [("choppiness", "<", 0.10)]),
        ("slope<2.0", [("slope", "<", 2.0)]),
        ("chop<0.10+slope<3.0", [("choppiness", "<", 0.10), ("slope", "<", 3.0)]),
        ("chop<0.20+slope<3.0", [("choppiness", "<", 0.20), ("slope", "<", 3.0)]),
    ]

    print(f"\n  {'Week':>6} {'Cat':>6} {'Filter':>25} {'N':>5} {'Ret':>5} "
          f"{'SR':>5} {'Avg $':>7} {'Total $':>10}")
    print(f"  {'-'*75}")

    for week in weeks:
        wk_cycles = [c for c in cycles if c["week"] == week]
        cat = wk_cycles[0]["category"] if wk_cycles else "?"
        # Baseline
        n = len(wk_cycles)
        stops = sum(c["is_stop"] for c in wk_cycles)
        pnl = sum(c["net_pnl"] for c in wk_cycles)
        print(f"  {week:>6} {cat:>6} {'baseline':>25} {n:>5} {'--':>5} "
              f"{stops/n:>5.0%} {pnl/n:>7.0f} {pnl:>10,.0f}")

        for cand_name, conditions in candidates:
            passed = []
            for c in wk_cycles:
                all_pass = True
                for feat_name, op, thresh in conditions:
                    val = c.get(f"feat_{feat_name}")
                    if val is None:
                        all_pass = False; break
                    if feat_name == "slope": val = abs(val)
                    if op == "<" and val >= thresh: all_pass = False; break
                    elif op == ">=" and val < thresh: all_pass = False; break
                if all_pass:
                    passed.append(c)
            pn = len(passed)
            if pn == 0:
                print(f"  {'':>6} {'':>6} {cand_name:>25} {0:>5} {'0%':>5} "
                      f"{'--':>5} {'--':>7} {'$0':>10}")
                continue
            p_stops = sum(c["is_stop"] for c in passed)
            p_pnl = sum(c["net_pnl"] for c in passed)
            ret = pn / n
            print(f"  {'':>6} {'':>6} {cand_name:>25} {pn:>5} {ret:>5.0%} "
                  f"{p_stops/pn:>5.0%} {p_pnl/pn:>7.0f} {p_pnl:>10,.0f}")

    # --- Audit + Journal ---
    append_audit("STEP_7_COMPLETE",
                 f"Pairwise feature combinations on SD=10 lb=3. "
                 f"{len(cycles)} cycles analyzed.")

    print(f"\nStep 7 complete.")


if __name__ == "__main__":
    main()
