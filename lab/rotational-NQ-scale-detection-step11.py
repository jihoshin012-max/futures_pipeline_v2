# archetype: rotational
"""
rotational-NQ-scale-detection-step11.py — Entry signals Step 2.

Step 2: Entry correlation analysis.
For each of 6 regime features at entry, bucket cycles by feature value,
compute SR, WR, avg $/cyc per bucket. Per-week breakdown.
Check monotonic relationships, directional alignment, kill gate.

Kill gate: Dies if no feature shows SR spread > 3pt at entry.

Prompt: rotational-NQ-prompt-entry-signals.md
Data: regime-direction-tagged-cycles.csv from Step 1 (step10).

Usage:
    python rotational-NQ-scale-detection-step11.py
"""
from __future__ import annotations

import csv
import statistics
from collections import defaultdict
from pathlib import Path

import numpy as np

COMMISSION_PER_RT_MINI = 3.50

OUTPUT_DIR = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")
CYCLE_CSV = OUTPUT_DIR / "regime-direction-tagged-cycles.csv"

ENTRY_FEATURES = [
    "signed_chop", "dchop", "d2chop", "signed_slope", "dr2", "dslope",
]

# Directional features: sign matters relative to trade direction
DIRECTIONAL_FEATURES = {"signed_chop", "signed_slope"}


# ---------------------------------------------------------------------------
#  Load tagged cycles
# ---------------------------------------------------------------------------

def load_tagged_cycles():
    cycles = []
    with open(CYCLE_CSV, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            c = {}
            c["cycle_id"] = int(row["cycle_id"])
            c["week"] = row["week"]
            c["category"] = row["category"]
            c["direction"] = row["direction"]
            c["exit_type"] = row["exit_type"]
            c["pnl_ticks"] = float(row["pnl_ticks"])
            c["pnl_dollars"] = float(row["pnl_dollars"])
            c["max_position"] = int(row["max_position"])
            c["mfe_ticks"] = float(row["mfe_ticks"])
            c["mae_ticks"] = float(row["mae_ticks"])
            c["depth"] = int(row["depth"])
            # Net PnL
            comm = COMMISSION_PER_RT_MINI * max(c["max_position"], 1)
            c["net_pnl"] = c["pnl_ticks"] * 5.0 - comm

            # Entry features
            for feat in ENTRY_FEATURES:
                key = f"entry_{feat}"
                val = row.get(key, "")
                c[key] = float(val) if val not in ("", "None", None) else None
            cycles.append(c)
    return cycles


# ---------------------------------------------------------------------------
#  Bucketing & metrics
# ---------------------------------------------------------------------------

def compute_bucket_metrics(cycles):
    """Compute WR, SR, avg $/cyc for a list of cycles."""
    if not cycles:
        return {"n": 0, "wr": None, "sr": None, "er": None, "pnl": 0}
    n = len(cycles)
    wins = sum(1 for c in cycles if c["net_pnl"] >= 0)
    stops = sum(1 for c in cycles if c["exit_type"] == "HARD_STOP")
    total_pnl = sum(c["net_pnl"] for c in cycles)
    return {
        "n": n,
        "wr": wins / n,
        "sr": stops / n,
        "er": total_pnl / n,
        "pnl": total_pnl,
    }


def quantile_buckets(cycles, feat_key, n_buckets=5):
    """Bucket cycles into quantiles by feature value."""
    valid = [(c, c[feat_key]) for c in cycles if c[feat_key] is not None]
    if not valid:
        return []
    valid.sort(key=lambda x: x[1])
    vals = [v for _, v in valid]

    # Compute quantile boundaries
    boundaries = []
    for i in range(1, n_buckets):
        idx = int(len(vals) * i / n_buckets)
        boundaries.append(vals[idx])

    buckets = [[] for _ in range(n_buckets)]
    for c, v in valid:
        placed = False
        for bi, b in enumerate(boundaries):
            if v <= b:
                buckets[bi].append(c)
                placed = True
                break
        if not placed:
            buckets[-1].append(c)

    # Label each bucket by its range
    result = []
    for bi in range(n_buckets):
        if not buckets[bi]:
            continue
        bvals = [c[feat_key] for c in buckets[bi]]
        lo, hi = min(bvals), max(bvals)
        label = f"Q{bi+1} [{lo:.4f}, {hi:.4f}]"
        m = compute_bucket_metrics(buckets[bi])
        m["label"] = label
        m["q"] = bi + 1
        m["lo"] = lo
        m["hi"] = hi
        result.append(m)
    return result


def directional_alignment_buckets(cycles, feat_key):
    """For directional features: bucket by sign relative to trade direction.

    aligned = LONG + positive OR SHORT + negative
    opposed = LONG + negative OR SHORT + positive
    near_zero = abs(val) < 0.005
    """
    aligned = []
    opposed = []
    near_zero = []

    for c in cycles:
        val = c[feat_key]
        if val is None:
            continue
        if abs(val) < 0.005:
            near_zero.append(c)
        elif (c["direction"] == "LONG" and val > 0) or \
             (c["direction"] == "SHORT" and val < 0):
            aligned.append(c)
        else:
            opposed.append(c)

    result = []
    for label, group in [("ALIGNED", aligned), ("NEAR_ZERO", near_zero),
                          ("OPPOSED", opposed)]:
        m = compute_bucket_metrics(group)
        m["label"] = label
        result.append(m)
    return result


# ---------------------------------------------------------------------------
#  Analysis
# ---------------------------------------------------------------------------

def analyze_feature(cycles, feat_name, per_week=True):
    """Full analysis for one feature: quantile buckets + directional if applicable."""
    feat_key = f"entry_{feat_name}"
    print(f"\n{'-'*60}")
    print(f"  FEATURE: {feat_name}")
    print(f"{'-'*60}")

    # Distribution stats
    vals = [c[feat_key] for c in cycles if c[feat_key] is not None]
    if not vals:
        print("  No valid values — skipping")
        return None
    print(f"  N={len(vals)} | range=[{min(vals):.6f}, {max(vals):.6f}] | "
          f"mean={np.mean(vals):.6f} | std={np.std(vals):.6f}")

    # --- Quantile buckets (pooled) ---
    print(f"\n  Quantile buckets (pooled):")
    print(f"  {'Bucket':<28} {'N':>5} {'WR':>6} {'SR':>6} {'E[R]':>8}")
    print(f"  {'-'*55}")
    buckets = quantile_buckets(cycles, feat_key, n_buckets=5)
    sr_values = []
    er_values = []
    for b in buckets:
        print(f"  {b['label']:<28} {b['n']:>5} {b['wr']:>5.0%} {b['sr']:>5.0%} "
              f"${b['er']:>7.2f}")
        sr_values.append(b["sr"])
        er_values.append(b["er"])

    # SR spread
    sr_spread = (max(sr_values) - min(sr_values)) * 100 if sr_values else 0
    er_spread = max(er_values) - min(er_values) if er_values else 0
    # Monotonicity: Spearman rank correlation of bucket index vs metric
    from scipy.stats import spearmanr
    if len(buckets) >= 3:
        qs = [b["q"] for b in buckets]
        srs = [b["sr"] for b in buckets]
        ers = [b["er"] for b in buckets]
        rho_sr, p_sr = spearmanr(qs, srs)
        rho_er, p_er = spearmanr(qs, ers)
    else:
        rho_sr = rho_er = p_sr = p_er = float("nan")

    print(f"\n  SR spread: {sr_spread:.1f}pt | E[R] spread: ${er_spread:.2f}")
    print(f"  Monotonicity (Spearman): SR rho={rho_sr:.3f} p={p_sr:.3f} | "
          f"E[R] rho={rho_er:.3f} p={p_er:.3f}")

    # --- Directional alignment (if applicable) ---
    dir_result = None
    if feat_name in DIRECTIONAL_FEATURES:
        print(f"\n  Directional alignment (pooled):")
        print(f"  {'Group':<15} {'N':>5} {'WR':>6} {'SR':>6} {'E[R]':>8}")
        print(f"  {'-'*42}")
        dir_result = directional_alignment_buckets(cycles, feat_key)
        for b in dir_result:
            if b["n"] > 0:
                print(f"  {b['label']:<15} {b['n']:>5} {b['wr']:>5.0%} "
                      f"{b['sr']:>5.0%} ${b['er']:>7.2f}")
            else:
                print(f"  {b['label']:<15} {b['n']:>5}   —      —       —")

    # --- Per-week breakdown ---
    if per_week:
        weeks = sorted(set(c["week"] for c in cycles))
        print(f"\n  Per-week quantile Q1 vs Q5:")
        print(f"  {'Week':<6} {'Cat':<8} {'Q1_SR':>6} {'Q5_SR':>6} {'dSR':>6} "
              f"{'Q1_ER':>8} {'Q5_ER':>8} {'dER':>8}")
        print(f"  {'-'*64}")
        week_sr_spreads = []
        for wk in weeks:
            wk_cycles = [c for c in cycles if c["week"] == wk]
            cat = wk_cycles[0]["category"] if wk_cycles else ""
            wb = quantile_buckets(wk_cycles, feat_key, n_buckets=5)
            if len(wb) >= 2:
                q1 = wb[0]
                q5 = wb[-1]
                d_sr = (q5["sr"] - q1["sr"]) * 100
                d_er = q5["er"] - q1["er"]
                week_sr_spreads.append(d_sr)
                print(f"  {wk:<6} {cat:<8} {q1['sr']:>5.0%} {q5['sr']:>5.0%} "
                      f"{d_sr:>+5.1f} ${q1['er']:>7.2f} ${q5['er']:>7.2f} "
                      f"${d_er:>+7.2f}")
            else:
                print(f"  {wk:<6} {cat:<8}   insufficient data")

    return {
        "feature": feat_name,
        "sr_spread_pt": sr_spread,
        "er_spread": er_spread,
        "rho_sr": rho_sr,
        "p_sr": p_sr,
        "rho_er": rho_er,
        "p_er": p_er,
        "directional": dir_result,
        "buckets": buckets,
    }


# ---------------------------------------------------------------------------
#  Kill gate
# ---------------------------------------------------------------------------

def kill_gate(results):
    """Check if any feature passes the SR spread > 3pt threshold."""
    print(f"\n{'='*60}")
    print(f"KILL GATE: SR spread > 3pt required")
    print(f"{'='*60}")
    print(f"  {'Feature':<15} {'SR_spread':>10} {'ER_spread':>10} {'rho_SR':>8} {'rho_ER':>8} {'Verdict':>8}")
    print(f"  {'-'*62}")

    any_pass = False
    for r in results:
        if r is None:
            continue
        verdict = "PASS" if r["sr_spread_pt"] > 3.0 else "FAIL"
        if verdict == "PASS":
            any_pass = True
        print(f"  {r['feature']:<15} {r['sr_spread_pt']:>9.1f}pt "
              f"${r['er_spread']:>9.2f} {r['rho_sr']:>+7.3f} {r['rho_er']:>+7.3f} "
              f"  {verdict}")

    print()
    if any_pass:
        print("  >>> KILL GATE: PASS — at least one feature shows signal. Proceed to Step 3.")
    else:
        print("  >>> KILL GATE: FAIL — no feature shows SR spread > 3pt. STOP.")
        print("  Record findings and terminate experiment.")
    return any_pass


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def main():
    print(f"Loading tagged cycles from {CYCLE_CSV}...")
    cycles = load_tagged_cycles()
    print(f"Loaded {len(cycles)} cycles")

    # Sanity check: reproduce baseline metrics
    pooled = compute_bucket_metrics(cycles)
    print(f"Pooled: {pooled['n']} cyc | {pooled['wr']:.0%} WR | {pooled['sr']:.0%} SR | "
          f"${pooled['pnl']:,.0f} | E[R]=${pooled['er']:.2f}")

    results = []
    for feat in ENTRY_FEATURES:
        r = analyze_feature(cycles, feat, per_week=True)
        results.append(r)

    passed = kill_gate(results)

    # Save summary CSV
    summary_csv = OUTPUT_DIR / "entry-signals-step2-summary.csv"
    with open(summary_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["feature", "sr_spread_pt", "er_spread", "rho_sr", "p_sr",
                     "rho_er", "p_er", "kill_gate"])
        for r in results:
            if r is None:
                continue
            w.writerow([r["feature"], f"{r['sr_spread_pt']:.2f}",
                        f"{r['er_spread']:.2f}", f"{r['rho_sr']:.4f}",
                        f"{r['p_sr']:.4f}", f"{r['rho_er']:.4f}",
                        f"{r['p_er']:.4f}",
                        "PASS" if r["sr_spread_pt"] > 3.0 else "FAIL"])
    print(f"\nSaved: {summary_csv}")

    return passed


if __name__ == "__main__":
    passed = main()
    if not passed:
        print("\n*** EXPERIMENT TERMINATED AT KILL GATE ***")
