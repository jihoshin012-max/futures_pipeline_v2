# archetype: rotational
"""
rotational-NQ-scale-detection-step26.py — Track C Step 3: Application mapping + redundancy check.

Maps features to management actions. Checks pairwise redundancy.
Selects the minimal feature set for each action.

Prompt: rotational-NQ-prompt-trade-management-c.md Step 3
"""
from __future__ import annotations

import csv
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

OUTPUT_DIR = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")

HARD_STOP_TICKS = 60.0


def load_bars(path):
    bars = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            r["cycle_id"] = int(r["cycle_id"])
            r["agg_bar_offset"] = int(r["agg_bar_offset"])
            r["direction"] = int(r["direction"])
            for k in ["price", "pnl_ticks", "mfe_ticks", "mae_ticks",
                       "signed_chop", "dchop", "d2chop", "signed_slope",
                       "dr2", "dslope", "r2", "choppiness", "slope_abs",
                       "agg_bar_range"]:
                v = r[k]
                r[k] = float(v) if v != "" else np.nan
            bars.append(r)
    return bars


def load_cycles(path):
    cycles = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            for k in ["pnl_ticks", "pnl_dollars", "mfe_ticks", "mae_ticks",
                       "seed_price", "exit_price"]:
                r[k] = float(r[k])
            for k in ["cycle_id", "bars_held", "depth", "max_position", "agg_bars_held"]:
                r[k] = int(r[k])
            cycles.append(r)
    return cycles


def main():
    t0 = time.time()

    print("Loading Step 1 data...")
    cycles = load_cycles(OUTPUT_DIR / "trade-mgmt-tagged-cycles.csv")
    bars = load_bars(OUTPUT_DIR / "trade-mgmt-intrade-bars.csv")

    cycle_map = {c["cycle_id"]: c for c in cycles}

    # Index bars by cycle
    bars_by_cycle = defaultdict(list)
    for b in bars:
        bars_by_cycle[b["cycle_id"]].append(b)

    # ===================================================================
    # Compute derived features for all bars
    # ===================================================================
    # Enrich each bar with trade-behavior signals + position-relative signals
    for cid, blist in bars_by_cycle.items():
        c = cycle_map[cid]
        dir_sign = 1 if c["direction"] == "LONG" else -1
        prev_mae = 0.0
        max_pos = c["max_position"]
        for b in blist:
            abo = b["agg_bar_offset"]
            b["mfe_rate"] = b["mfe_ticks"] / (abo + 1) if abo >= 0 else np.nan
            b["mae_proximity"] = b["mae_ticks"] / HARD_STOP_TICKS
            b["mae_increment"] = b["mae_ticks"] - prev_mae
            prev_mae = b["mae_ticks"]
            b["current_favor_ticks"] = b["pnl_ticks"] / max_pos if max_pos > 0 else 0
            if b["mfe_ticks"] > 0:
                b["mfe_retracement"] = (b["mfe_ticks"] - b["current_favor_ticks"]) / b["mfe_ticks"]
            else:
                b["mfe_retracement"] = 0.0
            if not np.isnan(b["signed_chop"]):
                b["signed_chop_vs_pos"] = b["signed_chop"] * dir_sign
            else:
                b["signed_chop_vs_pos"] = np.nan
            if not np.isnan(b["signed_slope"]):
                b["signed_slope_vs_pos"] = b["signed_slope"] * dir_sign
            else:
                b["signed_slope_vs_pos"] = np.nan

    # ===================================================================
    # Pairwise correlation (Spearman) between features at offset=1
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"STEP 3: Pairwise redundancy check (Spearman rho at offset=1)")
    print(f"{'='*70}")

    FEATURES = [
        "signed_chop_vs_pos", "signed_slope_vs_pos",
        "mae_proximity", "mfe_rate", "mae_increment", "mfe_retracement",
    ]

    # Collect feature vectors at offset=1
    vectors = {f: [] for f in FEATURES}
    valid_cids = []

    for cid, blist in bars_by_cycle.items():
        if len(blist) < 2:
            continue
        b = blist[1]  # offset=1
        all_valid = True
        for f in FEATURES:
            v = b.get(f, np.nan)
            if np.isnan(v):
                all_valid = False
                break
        if all_valid:
            valid_cids.append(cid)
            for f in FEATURES:
                vectors[f].append(b[f])

    print(f"\n  Cycles with valid offset=1 data: {len(valid_cids)}")

    # Compute Spearman correlations
    from scipy import stats as sp_stats

    n_f = len(FEATURES)
    rho_matrix = np.zeros((n_f, n_f))
    for i in range(n_f):
        for j in range(n_f):
            if i == j:
                rho_matrix[i, j] = 1.0
            elif i < j:
                rho, _ = sp_stats.spearmanr(vectors[FEATURES[i]], vectors[FEATURES[j]])
                rho_matrix[i, j] = rho
                rho_matrix[j, i] = rho

    print(f"\n  {'':>22}", end="")
    for f in FEATURES:
        print(f" {f[:10]:>10}", end="")
    print()
    for i, f in enumerate(FEATURES):
        print(f"  {f:<22}", end="")
        for j in range(n_f):
            v = rho_matrix[i, j]
            marker = "*" if abs(v) >= 0.7 and i != j else " "
            print(f" {v:>9.2f}{marker}", end="")
        print()

    # ===================================================================
    # Feature-to-action mapping
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"FEATURE-TO-ACTION MAPPING")
    print(f"{'='*70}")

    print("""
    EARLY EXIT:
      Primary: signed_chop_vs_pos (d=-2.78, 31pt SR spread, diverges at offset=1)
      Alternative: signed_slope_vs_pos (d=-2.23, 30pt SR spread)
      Support: mae_proximity (tautological alone, but useful as compound trigger)

      Trigger logic: Exit when signed_chop_vs_pos < threshold AND
                     mae_proximity > threshold (confirming actual adverse movement)

    SKIP ADD:
      Primary: mae_increment (d=+1.34, diverges at offset=2)
      Support: mfe_rate (d=-1.60, diverges at offset=3)

      Trigger logic: At the add point (10pts against entry), check whether
                     mae_increment is high or mfe_rate is low. If the trade
                     is hitting new worsts rapidly, skip the martingale add.

    TIGHTEN STOP:
      Primary: choppiness (unsigned, d=+0.77 at last bar)
      Alternative: range expansion (agg_bar_range ratio)

      Trigger logic: If choppiness or range_ratio exceed threshold mid-trade,
                     reduce hard stop from 60 to N ticks.
      NOTE: weak effect size (d=0.77) — may not survive loss replay.

    BREAK-EVEN STOP:
      Mechanical: MFE > N ticks -> move stop to entry price.
      No feature needed. Test standalone in Step 6.
    """)

    # ===================================================================
    # Redundancy clusters
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"REDUNDANCY CLUSTERS")
    print(f"{'='*70}")

    # Identify high-correlation pairs
    print(f"\n  Pairs with |rho| >= 0.5:")
    pairs = []
    for i in range(n_f):
        for j in range(i + 1, n_f):
            rho = rho_matrix[i, j]
            if abs(rho) >= 0.5:
                print(f"    {FEATURES[i]} <-> {FEATURES[j]}: rho={rho:.3f}")
                pairs.append((FEATURES[i], FEATURES[j], rho))

    print(f"\n  Decision:")
    print(f"    - signed_chop_vs_pos and signed_slope_vs_pos: if rho >= 0.7, keep only signed_chop_vs_pos (stronger d)")
    print(f"    - mae_proximity and mfe_retracement: if rho >= 0.7, keep only mae_proximity (simpler)")
    print(f"    - mae_increment and mfe_rate: if rho low (<0.5), both can coexist for skip-add")

    # ===================================================================
    # Also correlate at offset=1 between regime and trade-behavior
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"CROSS-GROUP CORRELATIONS (regime vs trade-behavior at offset=1)")
    print(f"{'='*70}")

    regime_feats = ["signed_chop_vs_pos", "signed_slope_vs_pos"]
    trade_feats = ["mae_proximity", "mfe_rate", "mae_increment", "mfe_retracement"]

    for rf in regime_feats:
        for tf in trade_feats:
            rho, _ = sp_stats.spearmanr(vectors[rf], vectors[tf])
            marker = "**" if abs(rho) >= 0.5 else ""
            print(f"  {rf:<22} <-> {tf:<18}: rho={rho:+.3f} {marker}")

    # ===================================================================
    # Final feature selection for Step 4
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"FEATURE SELECTION FOR STEP 4 (LOSS REPLAY)")
    print(f"{'='*70}")

    print("""
    EARLY EXIT candidates (test both thresholds and combinations):
      1. signed_chop_vs_pos < threshold (primary)
      2. signed_chop_vs_pos < threshold AND mae_proximity > threshold (compound)

    SKIP ADD candidates:
      1. mae_increment > threshold at add bar
      2. mfe_rate < threshold at add bar

    TIGHTEN STOP candidates:
      1. choppiness > threshold (weak — may fail)

    BREAK-EVEN STOP:
      1. mfe_ticks > N (mechanical, no feature)
    """)

    total = time.time() - t0
    print(f"\nRuntime: {total:.0f}s")


if __name__ == "__main__":
    main()
