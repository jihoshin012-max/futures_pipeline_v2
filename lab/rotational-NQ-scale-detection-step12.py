# archetype: rotational
"""
rotational-NQ-scale-detection-step12.py — Entry signals Step 3.

Step 3: Redundancy check.
Correlation matrix between the 4 passing features (dchop, d2chop, dr2, dslope).
If highly correlated (|r| > 0.7), keep only the stronger one (higher SR spread).

Prompt: rotational-NQ-prompt-entry-signals.md
Data: regime-direction-tagged-cycles.csv from Step 1.

Usage:
    python rotational-NQ-scale-detection-step12.py
"""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

OUTPUT_DIR = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")
CYCLE_CSV = OUTPUT_DIR / "regime-direction-tagged-cycles.csv"

# Only the 4 features that passed the kill gate
PASSING_FEATURES = ["dchop", "d2chop", "dr2", "dslope"]

# SR spreads from Step 2 (for ranking)
SR_SPREADS = {
    "dchop": 4.9,
    "d2chop": 5.4,
    "dr2": 9.5,
    "dslope": 5.9,
}

COMMISSION_PER_RT_MINI = 3.50


# ---------------------------------------------------------------------------
#  Load
# ---------------------------------------------------------------------------

def load_feature_matrix():
    """Load entry feature values and net PnL for all cycles."""
    vals = {f: [] for f in PASSING_FEATURES}
    net_pnls = []
    with open(CYCLE_CSV, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            max_pos = int(row["max_position"])
            comm = COMMISSION_PER_RT_MINI * max(max_pos, 1)
            net_pnl = float(row["pnl_ticks"]) * 5.0 - comm
            net_pnls.append(net_pnl)
            for feat in PASSING_FEATURES:
                v = row.get(f"entry_{feat}", "")
                vals[feat].append(float(v) if v not in ("", "None") else np.nan)

    # Build matrix (N x 4)
    n = len(net_pnls)
    matrix = np.empty((n, len(PASSING_FEATURES)), dtype=np.float64)
    for j, feat in enumerate(PASSING_FEATURES):
        matrix[:, j] = vals[feat]
    return matrix, np.array(net_pnls), n


# ---------------------------------------------------------------------------
#  Analysis
# ---------------------------------------------------------------------------

def main():
    print(f"Loading features from {CYCLE_CSV}...")
    matrix, net_pnls, n = load_feature_matrix()
    print(f"Loaded {n} cycles, {len(PASSING_FEATURES)} features")

    # --- Pairwise Pearson correlation ---
    print(f"\n{'='*60}")
    print(f"PAIRWISE PEARSON CORRELATION (entry feature values)")
    print(f"{'='*60}")

    # Drop rows with any NaN
    valid_mask = ~np.isnan(matrix).any(axis=1)
    m = matrix[valid_mask]
    pnl_valid = net_pnls[valid_mask]
    print(f"Valid rows (no NaN): {len(m)} / {n}")

    corr = np.corrcoef(m.T)

    print(f"\n{'':>12}", end="")
    for f in PASSING_FEATURES:
        print(f"{f:>10}", end="")
    print()
    for i, fi in enumerate(PASSING_FEATURES):
        print(f"{fi:>12}", end="")
        for j, fj in enumerate(PASSING_FEATURES):
            r = corr[i, j]
            marker = " *" if i != j and abs(r) > 0.7 else "  "
            print(f"{r:>8.3f}{marker}", end="")
        print()

    # --- Feature-to-PnL correlation ---
    print(f"\n{'='*60}")
    print(f"FEATURE-TO-PNL CORRELATION")
    print(f"{'='*60}")
    print(f"{'Feature':<12} {'r(PnL)':>8} {'SR_spread':>10} {'Rank':>6}")
    print(f"{'-'*38}")
    pnl_corrs = {}
    for j, feat in enumerate(PASSING_FEATURES):
        r = np.corrcoef(m[:, j], pnl_valid)[0, 1]
        pnl_corrs[feat] = r

    ranked = sorted(PASSING_FEATURES,
                    key=lambda f: SR_SPREADS[f], reverse=True)
    for rank, feat in enumerate(ranked, 1):
        j = PASSING_FEATURES.index(feat)
        r = pnl_corrs[feat]
        print(f"{feat:<12} {r:>+7.4f} {SR_SPREADS[feat]:>9.1f}pt {rank:>5}")

    # --- Redundancy decisions ---
    print(f"\n{'='*60}")
    print(f"REDUNDANCY ANALYSIS")
    print(f"{'='*60}")

    redundant_pairs = []
    for i in range(len(PASSING_FEATURES)):
        for j in range(i + 1, len(PASSING_FEATURES)):
            r = corr[i, j]
            fi, fj = PASSING_FEATURES[i], PASSING_FEATURES[j]
            if abs(r) > 0.7:
                # Keep the one with higher SR spread
                keep = fi if SR_SPREADS[fi] >= SR_SPREADS[fj] else fj
                drop = fj if keep == fi else fi
                redundant_pairs.append((fi, fj, r, keep, drop))
                print(f"  {fi} <-> {fj}: r={r:.3f} (|r|>{0.7}) -> "
                      f"KEEP {keep} (SR={SR_SPREADS[keep]:.1f}pt), "
                      f"DROP {drop} (SR={SR_SPREADS[drop]:.1f}pt)")

    if not redundant_pairs:
        print("  No pairs with |r| > 0.7 — all features are independent.")

    # --- Determine surviving features ---
    dropped = set()
    for _, _, _, _, drop in redundant_pairs:
        dropped.add(drop)
    survivors = [f for f in ranked if f not in dropped]

    print(f"\n{'='*60}")
    print(f"SURVIVING FEATURES (ordered by SR spread)")
    print(f"{'='*60}")
    for feat in survivors:
        j = PASSING_FEATURES.index(feat)
        r_pnl = pnl_corrs[feat]
        print(f"  {feat:<12} SR_spread={SR_SPREADS[feat]:.1f}pt  "
              f"r(PnL)={r_pnl:+.4f}")
    if dropped:
        print(f"\n  Dropped (redundant): {', '.join(sorted(dropped))}")

    # --- Save ---
    summary_csv = OUTPUT_DIR / "entry-signals-step3-redundancy.csv"
    with open(summary_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["feat_i", "feat_j", "pearson_r", "redundant",
                     "keep", "drop"])
        for i in range(len(PASSING_FEATURES)):
            for j in range(i + 1, len(PASSING_FEATURES)):
                fi, fj = PASSING_FEATURES[i], PASSING_FEATURES[j]
                r = corr[i, j]
                is_red = abs(r) > 0.7
                keep = ""
                drop = ""
                if is_red:
                    keep = fi if SR_SPREADS[fi] >= SR_SPREADS[fj] else fj
                    drop = fj if keep == fi else fi
                w.writerow([fi, fj, f"{r:.4f}", is_red, keep, drop])
    print(f"\nSaved: {summary_csv}")

    return survivors


if __name__ == "__main__":
    survivors = main()
