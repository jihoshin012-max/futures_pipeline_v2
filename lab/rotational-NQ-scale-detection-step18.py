# archetype: rotational
"""
rotational-NQ-scale-detection-step18.py — Track B Step 2: Entry correlation
analysis with kill gate.

For each fade confirmation feature, bucket cycles by feature value at entry
and compute SR, WR, avg $/cyc per bucket. Per-week breakdown.

Kill gate: dies if no feature shows SR spread > 3pt.

Prompt: rotational-NQ-prompt-fade-confirmation.md
Depends on: Step 1 (step17.py) — tagged cycles.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

import numpy as np

COMMISSION_PER_RT_MINI = 3.50
OUTPUT_DIR = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")
CYCLE_CSV = OUTPUT_DIR / "fade-confirm-tagged-cycles.csv"


# ---------------------------------------------------------------------------
#  Load
# ---------------------------------------------------------------------------

def load_cycles():
    cycles = []
    with open(CYCLE_CSV, "r") as f:
        for row in csv.DictReader(f):
            c = {
                "week": row["week"],
                "week_cat": row["week_cat"],
                "exit_type": row["exit_type"],
                "max_position": int(row["max_position"]),
                "pnl_ticks": float(row["pnl_ticks"]),
                "direction": row["direction"],
            }
            comm = COMMISSION_PER_RT_MINI * max(c["max_position"], 1)
            c["net_pnl"] = c["pnl_ticks"] * 5.0 - comm
            for feat in ["fade_confirm", "range_decay_1", "avg_range_decay",
                         "flow_confirm", "direction_bars", "fade_speed"]:
                v = row.get(feat, "")
                c[feat] = float(v) if v not in ("", "None") else None
            cycles.append(c)
    return cycles


def metrics(cycles):
    if not cycles:
        return {"n": 0, "wr": 0.0, "sr": 0.0, "er": 0.0, "pnl": 0.0}
    n = len(cycles)
    wins = sum(1 for c in cycles if c["net_pnl"] >= 0)
    stops = sum(1 for c in cycles if c["exit_type"] == "HARD_STOP")
    pnl = sum(c["net_pnl"] for c in cycles)
    return {"n": n, "wr": wins / n, "sr": stops / n, "er": pnl / n, "pnl": pnl}


# ---------------------------------------------------------------------------
#  Bucketing
# ---------------------------------------------------------------------------

def bucket_analysis(cycles, feat, buckets, labels):
    """Bucket cycles by feature value and compute metrics per bucket.

    buckets: list of (lo, hi) tuples defining bucket boundaries.
    labels: list of string labels for each bucket.
    """
    results = []
    for (lo, hi), label in zip(buckets, labels):
        in_bucket = [c for c in cycles
                     if c[feat] is not None and lo <= c[feat] < hi]
        m = metrics(in_bucket)
        m["label"] = label
        results.append(m)
    return results


def bucket_analysis_discrete(cycles, feat, values, labels):
    """Bucket by exact discrete values."""
    results = []
    for val, label in zip(values, labels):
        in_bucket = [c for c in cycles
                     if c[feat] is not None and c[feat] == val]
        m = metrics(in_bucket)
        m["label"] = label
        results.append(m)
    return results


def per_week_bucket(cycles, feat, buckets, labels):
    """Bucket analysis broken down by week."""
    weeks = defaultdict(list)
    for c in cycles:
        weeks[c["week"]].append(c)

    week_results = {}
    for wk in sorted(weeks.keys()):
        week_results[wk] = bucket_analysis(weeks[wk], feat, buckets, labels)
    return week_results


# ---------------------------------------------------------------------------
#  Feature definitions
# ---------------------------------------------------------------------------

FEATURE_CONFIGS = {
    "fade_confirm": {
        "buckets": [(-999, 0.0), (0.0, 0.3), (0.3, 0.5), (0.5, 0.7), (0.7, 999)],
        "labels": ["<0.0", "0.0-0.3", "0.3-0.5", "0.5-0.7", ">=0.7"],
        "hypothesis": "High fade_confirm (>0.7) outperforms low (<0.3)",
    },
    "range_decay_1": {
        "buckets": [(-999, 0.6), (0.6, 0.8), (0.8, 1.0), (1.0, 1.2), (1.2, 999)],
        "labels": ["<0.6", "0.6-0.8", "0.8-1.0", "1.0-1.2", ">=1.2"],
        "hypothesis": "range_decay < 0.8 (momentum fading) outperforms > 1.2",
    },
    "avg_range_decay": {
        "buckets": [(-999, 0.8), (0.8, 0.9), (0.9, 1.0), (1.0, 1.1), (1.1, 1.2), (1.2, 999)],
        "labels": ["<0.8", "0.8-0.9", "0.9-1.0", "1.0-1.1", "1.1-1.2", ">=1.2"],
        "hypothesis": "avg_range_decay < 0.8 (momentum fading) outperforms > 1.2",
    },
    "flow_confirm": {
        "buckets": [(-999, -0.3), (-0.3, -0.1), (-0.1, 0.1), (0.1, 0.3), (0.3, 999)],
        "labels": ["<-0.3", "-0.3--0.1", "-0.1-0.1", "0.1-0.3", ">=0.3"],
        "hypothesis": "Positive flow_confirm (buyers for LONG) outperforms negative",
    },
    "fade_speed": {
        "buckets": [(-999, -1.0), (-1.0, -0.3), (-0.3, 0.0), (0.0, 0.3), (0.3, 1.0), (1.0, 999)],
        "labels": ["<-1.0", "-1.0--0.3", "-0.3-0.0", "0.0-0.3", "0.3-1.0", ">=1.0"],
        "hypothesis": "High fade_speed (pullback reversing) outperforms low (pullback continuing)",
    },
}


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def main():
    print("Loading tagged cycles...")
    cycles = load_cycles()
    print(f"  {len(cycles)} cycles loaded")

    baseline = metrics(cycles)
    print(f"\nBaseline: {baseline['n']} cyc | {baseline['wr']:.0%} WR | "
          f"{baseline['sr']:.0%} SR | E[R]=${baseline['er']:.2f}")

    # Track kill gate results
    max_sr_spread = {}

    # =======================================================================
    # Continuous features
    # =======================================================================
    for feat, cfg in FEATURE_CONFIGS.items():
        print(f"\n{'='*70}")
        print(f"FEATURE: {feat}")
        print(f"Hypothesis: {cfg['hypothesis']}")
        print(f"{'='*70}")

        # Pooled analysis
        results = bucket_analysis(cycles, feat, cfg["buckets"], cfg["labels"])

        print(f"\n{'Bucket':<15} {'N':>5} {'WR':>6} {'SR':>6} {'E[R]':>8} {'PnL':>10}")
        print(f"{'-'*55}")
        srs = []
        ers = []
        for r in results:
            if r["n"] > 0:
                print(f"{r['label']:<15} {r['n']:>5} {r['wr']:>5.0%} "
                      f"{r['sr']:>5.0%} ${r['er']:>7.2f} ${r['pnl']:>9,.0f}")
                srs.append(r["sr"])
                ers.append(r["er"])

        sr_spread = (max(srs) - min(srs)) * 100 if len(srs) > 1 else 0
        er_spread = max(ers) - min(ers) if len(ers) > 1 else 0
        print(f"\n  SR spread: {sr_spread:.1f}pt | E[R] spread: ${er_spread:.2f}")
        max_sr_spread[feat] = sr_spread

        # Per-week breakdown
        print(f"\n  Per-week breakdown:")
        week_results = per_week_bucket(cycles, feat, cfg["buckets"], cfg["labels"])
        for wk in sorted(week_results.keys()):
            cat = next((c["week_cat"] for c in cycles if c["week"] == wk), "")
            print(f"\n  {wk} ({cat}):")
            print(f"    {'Bucket':<15} {'N':>5} {'WR':>6} {'SR':>6} {'E[R]':>8}")
            for r in week_results[wk]:
                if r["n"] > 0:
                    print(f"    {r['label']:<15} {r['n']:>5} {r['wr']:>5.0%} "
                          f"{r['sr']:>5.0%} ${r['er']:>7.2f}")

    # =======================================================================
    # Discrete feature: direction_bars
    # =======================================================================
    feat = "direction_bars"
    print(f"\n{'='*70}")
    print(f"FEATURE: {feat}")
    print(f"Hypothesis: 2+ of last 3 bars in fade direction outperforms 0")
    print(f"{'='*70}")

    discrete_vals = [0.0, 1.0, 2.0, 3.0]
    discrete_labels = ["0", "1", "2", "3"]
    results = bucket_analysis_discrete(cycles, feat, discrete_vals, discrete_labels)

    print(f"\n{'Value':<10} {'N':>5} {'WR':>6} {'SR':>6} {'E[R]':>8} {'PnL':>10}")
    print(f"{'-'*50}")
    srs = []
    ers = []
    for r in results:
        if r["n"] > 0:
            print(f"{r['label']:<10} {r['n']:>5} {r['wr']:>5.0%} "
                  f"{r['sr']:>5.0%} ${r['er']:>7.2f} ${r['pnl']:>9,.0f}")
            srs.append(r["sr"])
            ers.append(r["er"])

    sr_spread = (max(srs) - min(srs)) * 100 if len(srs) > 1 else 0
    er_spread = max(ers) - min(ers) if len(ers) > 1 else 0
    print(f"\n  SR spread: {sr_spread:.1f}pt | E[R] spread: ${er_spread:.2f}")
    max_sr_spread[feat] = sr_spread

    # Per-week breakdown
    print(f"\n  Per-week breakdown:")
    weeks = defaultdict(list)
    for c in cycles:
        weeks[c["week"]].append(c)
    for wk in sorted(weeks.keys()):
        cat = next((c["week_cat"] for c in cycles if c["week"] == wk), "")
        print(f"\n  {wk} ({cat}):")
        print(f"    {'Value':<10} {'N':>5} {'WR':>6} {'SR':>6} {'E[R]':>8}")
        wk_results = bucket_analysis_discrete(weeks[wk], feat, discrete_vals, discrete_labels)
        for r in wk_results:
            if r["n"] > 0:
                print(f"    {r['label']:<10} {r['n']:>5} {r['wr']:>5.0%} "
                      f"{r['sr']:>5.0%} ${r['er']:>7.2f}")

    # =======================================================================
    # Kill gate
    # =======================================================================
    print(f"\n{'='*70}")
    print(f"KILL GATE: Any feature with SR spread > 3pt?")
    print(f"{'='*70}")
    print(f"\n{'Feature':<20} {'SR spread':>10} {'Pass?':>6}")
    print(f"{'-'*40}")
    any_pass = False
    for feat, spread in max_sr_spread.items():
        passed = spread > 3.0
        if passed:
            any_pass = True
        print(f"{feat:<20} {spread:>9.1f}pt {'YES' if passed else 'no':>6}")

    if any_pass:
        print(f"\n>>> KILL GATE: PASS — at least one feature shows SR spread > 3pt")
    else:
        print(f"\n>>> KILL GATE: FAIL — no feature shows SR spread > 3pt. STOP.")

    print(f"\nDone.")


if __name__ == "__main__":
    main()
