# archetype: rotational
"""
rotational-NQ-scale-detection-step19.py — Track B Step 3: Redundancy check.

Check pairwise correlation between the 4 strong features from Step 2.
Keep the stronger of redundant pairs or combine if independent.

Prompt: rotational-NQ-prompt-fade-confirmation.md
Depends on: Step 2 (step18.py) — kill gate passed.
"""
from __future__ import annotations

import csv
from pathlib import Path

import numpy as np

OUTPUT_DIR = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")
CYCLE_CSV = OUTPUT_DIR / "fade-confirm-tagged-cycles.csv"

COMMISSION_PER_RT_MINI = 3.50


def load_cycles():
    cycles = []
    with open(CYCLE_CSV, "r") as f:
        for row in csv.DictReader(f):
            c = {"exit_type": row["exit_type"],
                 "max_position": int(row["max_position"]),
                 "pnl_ticks": float(row["pnl_ticks"]),
                 "week": row["week"]}
            c["net_pnl"] = c["pnl_ticks"] * 5.0 - COMMISSION_PER_RT_MINI * max(c["max_position"], 1)
            for feat in ["fade_confirm", "flow_confirm", "direction_bars", "fade_speed"]:
                v = row.get(feat, "")
                c[feat] = float(v) if v not in ("", "None") else None
            cycles.append(c)
    return cycles


def main():
    cycles = load_cycles()
    print(f"Loaded {len(cycles)} cycles")

    features = ["fade_confirm", "flow_confirm", "direction_bars", "fade_speed"]

    # =======================================================================
    # Pairwise Pearson correlation
    # =======================================================================
    # Build arrays (only cycles where both features are non-null)
    print(f"\n{'='*60}")
    print(f"PAIRWISE PEARSON CORRELATION")
    print(f"{'='*60}")
    print(f"\n{'Pair':<35} {'N':>5} {'r':>8} {'|r|':>6}")
    print(f"{'-'*58}")

    for i, f1 in enumerate(features):
        for f2 in features[i+1:]:
            vals = [(c[f1], c[f2]) for c in cycles
                    if c[f1] is not None and c[f2] is not None]
            if not vals:
                print(f"{f1} vs {f2:<20} no data")
                continue
            a1 = np.array([v[0] for v in vals])
            a2 = np.array([v[1] for v in vals])
            r = np.corrcoef(a1, a2)[0, 1]
            print(f"{f1} vs {f2:<20} {len(vals):>5} {r:>8.3f} {abs(r):>6.3f}")

    # =======================================================================
    # Spearman rank correlation (more robust for non-linear)
    # =======================================================================
    from scipy import stats
    print(f"\n{'='*60}")
    print(f"PAIRWISE SPEARMAN RANK CORRELATION")
    print(f"{'='*60}")
    print(f"\n{'Pair':<35} {'N':>5} {'rho':>8} {'|rho|':>6} {'p':>10}")
    print(f"{'-'*68}")

    for i, f1 in enumerate(features):
        for f2 in features[i+1:]:
            vals = [(c[f1], c[f2]) for c in cycles
                    if c[f1] is not None and c[f2] is not None]
            if not vals:
                continue
            a1 = np.array([v[0] for v in vals])
            a2 = np.array([v[1] for v in vals])
            rho, p = stats.spearmanr(a1, a2)
            print(f"{f1} vs {f2:<20} {len(vals):>5} {rho:>8.3f} {abs(rho):>6.3f} {p:>10.2e}")

    # =======================================================================
    # Conditional independence: does adding feature B improve prediction
    # when feature A is already in the filter?
    # =======================================================================
    print(f"\n{'='*60}")
    print(f"CONDITIONAL VALUE: E[R] when both features are favorable vs unfavorable")
    print(f"{'='*60}")

    # Define "favorable" thresholds based on Step 2 findings (inverted)
    # fade_confirm: low is good → < 0.3 favorable
    # fade_speed: negative is good → < 0.0 favorable
    # flow_confirm: negative is good → < -0.1 favorable
    # direction_bars: low is good → <= 1 favorable

    fav = {
        "fade_confirm": lambda v: v < 0.3,
        "fade_speed": lambda v: v < 0.0,
        "flow_confirm": lambda v: v < -0.1,
        "direction_bars": lambda v: v <= 1,
    }
    unfav = {
        "fade_confirm": lambda v: v >= 0.5,
        "fade_speed": lambda v: v >= 0.3,
        "flow_confirm": lambda v: v >= 0.1,
        "direction_bars": lambda v: v >= 2,
    }

    def metrics_for(cyc_list):
        if not cyc_list:
            return 0, 0.0, 0.0, 0.0
        n = len(cyc_list)
        stops = sum(1 for c in cyc_list if c["exit_type"] == "HARD_STOP")
        pnls = [c["net_pnl"] for c in cyc_list]
        return n, sum(1 for p in pnls if p >= 0) / n, stops / n, sum(pnls) / n

    print(f"\n{'Combo':<40} {'N':>5} {'WR':>6} {'SR':>6} {'E[R]':>8}")
    print(f"{'-'*68}")

    # All favorable
    for i, f1 in enumerate(features):
        for f2 in features[i+1:]:
            # Both favorable
            both_fav = [c for c in cycles
                        if c[f1] is not None and c[f2] is not None
                        and fav[f1](c[f1]) and fav[f2](c[f2])]
            n, wr, sr, er = metrics_for(both_fav)
            label = f"{f1}+ + {f2}+"
            print(f"{label:<40} {n:>5} {wr:>5.0%} {sr:>5.0%} ${er:>7.2f}")

            # f1 favorable, f2 unfavorable
            f1_fav_f2_unfav = [c for c in cycles
                               if c[f1] is not None and c[f2] is not None
                               and fav[f1](c[f1]) and unfav[f2](c[f2])]
            n, wr, sr, er = metrics_for(f1_fav_f2_unfav)
            label = f"{f1}+ + {f2}-"
            print(f"{label:<40} {n:>5} {wr:>5.0%} {sr:>5.0%} ${er:>7.2f}")

            # f1 unfavorable, f2 favorable
            f1_unfav_f2_fav = [c for c in cycles
                               if c[f1] is not None and c[f2] is not None
                               and unfav[f1](c[f1]) and fav[f2](c[f2])]
            n, wr, sr, er = metrics_for(f1_unfav_f2_fav)
            label = f"{f1}- + {f2}+"
            print(f"{label:<40} {n:>5} {wr:>5.0%} {sr:>5.0%} ${er:>7.2f}")

            # Both unfavorable
            both_unfav = [c for c in cycles
                          if c[f1] is not None and c[f2] is not None
                          and unfav[f1](c[f1]) and unfav[f2](c[f2])]
            n, wr, sr, er = metrics_for(both_unfav)
            label = f"{f1}- + {f2}-"
            print(f"{label:<40} {n:>5} {wr:>5.0%} {sr:>5.0%} ${er:>7.2f}")
            print()

    # =======================================================================
    # Single-feature baselines for comparison
    # =======================================================================
    print(f"\n{'='*60}")
    print(f"SINGLE-FEATURE BASELINES (favorable vs unfavorable)")
    print(f"{'='*60}")
    print(f"\n{'Feature':<20} {'Cond':<6} {'N':>5} {'WR':>6} {'SR':>6} {'E[R]':>8}")
    print(f"{'-'*55}")
    for feat in features:
        fav_cyc = [c for c in cycles if c[feat] is not None and fav[feat](c[feat])]
        n, wr, sr, er = metrics_for(fav_cyc)
        print(f"{feat:<20} {'fav':<6} {n:>5} {wr:>5.0%} {sr:>5.0%} ${er:>7.2f}")
        unfav_cyc = [c for c in cycles if c[feat] is not None and unfav[feat](c[feat])]
        n, wr, sr, er = metrics_for(unfav_cyc)
        print(f"{feat:<20} {'unfav':<6} {n:>5} {wr:>5.0%} {sr:>5.0%} ${er:>7.2f}")

    print(f"\nDone.")


if __name__ == "__main__":
    main()
