# archetype: rotational
"""
rotational-NQ-scale-detection-step13.py — Entry signals Step 4.

Step 4: Retroactive filter (5 test weeks).
Tag cycles with entry feature values and compute PnL for subsets passing
various thresholds on dr2 and dslope. No sim re-run — retroactive only.
Per-week breakdown required.

Prompt: rotational-NQ-prompt-entry-signals.md
Survivors from Step 3: dr2, dslope.

Usage:
    python rotational-NQ-scale-detection-step13.py
"""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

import numpy as np

COMMISSION_PER_RT_MINI = 3.50

OUTPUT_DIR = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")
CYCLE_CSV = OUTPUT_DIR / "regime-direction-tagged-cycles.csv"

WEEK_ORDER = ["W39", "W43", "W46", "W47", "W50"]
WEEK_CATS = {"W39": "LOW", "W43": "WEAKEST", "W46": "GOOD",
             "W47": "BEST", "W50": "MID"}


# ---------------------------------------------------------------------------
#  Load
# ---------------------------------------------------------------------------

def load_cycles():
    cycles = []
    with open(CYCLE_CSV, "r") as f:
        for row in csv.DictReader(f):
            c = {
                "week": row["week"],
                "exit_type": row["exit_type"],
                "max_position": int(row["max_position"]),
                "pnl_ticks": float(row["pnl_ticks"]),
            }
            comm = COMMISSION_PER_RT_MINI * max(c["max_position"], 1)
            c["net_pnl"] = c["pnl_ticks"] * 5.0 - comm
            for feat in ["dr2", "dslope"]:
                v = row.get(f"entry_{feat}", "")
                c[feat] = float(v) if v not in ("", "None") else None
            cycles.append(c)
    return cycles


def metrics(cycles):
    if not cycles:
        return {"n": 0, "wr": 0, "sr": 0, "er": 0, "pnl": 0}
    n = len(cycles)
    wins = sum(1 for c in cycles if c["net_pnl"] >= 0)
    stops = sum(1 for c in cycles if c["exit_type"] == "HARD_STOP")
    pnl = sum(c["net_pnl"] for c in cycles)
    return {"n": n, "wr": wins / n, "sr": stops / n, "er": pnl / n, "pnl": pnl}


# ---------------------------------------------------------------------------
#  Threshold sweep
# ---------------------------------------------------------------------------

def sweep_single_feature(cycles, feat, thresholds, direction="<="):
    """Retroactively filter cycles by a single feature threshold."""
    results = []
    baseline = metrics(cycles)
    for thresh in thresholds:
        if direction == "<=":
            filtered = [c for c in cycles
                        if c[feat] is not None and c[feat] <= thresh]
            label = f"{feat}<={thresh:.2f}"
        else:
            filtered = [c for c in cycles
                        if c[feat] is not None and c[feat] >= thresh]
            label = f"{feat}>={thresh:.2f}"
        m = metrics(filtered)
        retention = m["n"] / baseline["n"] if baseline["n"] else 0
        results.append({"label": label, "threshold": thresh,
                         "retention": retention, **m})
    return results


def sweep_combined(cycles, dr2_thresh, dslope_thresh):
    """Retroactively filter by both dr2 and dslope."""
    filtered = [c for c in cycles
                if c["dr2"] is not None and c["dslope"] is not None
                and c["dr2"] <= dr2_thresh and c["dslope"] <= dslope_thresh]
    return filtered


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def main():
    print(f"Loading cycles from {CYCLE_CSV}...")
    cycles = load_cycles()
    print(f"Loaded {len(cycles)} cycles")

    bl = metrics(cycles)
    print(f"Baseline: {bl['n']} cyc | {bl['wr']:.0%} WR | {bl['sr']:.0%} SR | "
          f"E[R]=${bl['er']:.2f}")

    # --- dr2 thresholds ---
    # dr2 is mostly negative (falling R2 = good). More negative = better.
    # Sweep: keep cycles where dr2 <= threshold
    dr2_thresholds = [0.0, -0.10, -0.20, -0.30, -0.40, -0.50, -0.60, -0.70, -0.80]

    print(f"\n{'='*60}")
    print(f"DR2 THRESHOLD SWEEP (keep dr2 <= thresh)")
    print(f"{'='*60}")
    print(f"{'Threshold':>10} {'N':>5} {'Ret':>6} {'WR':>6} {'SR':>6} {'E[R]':>8} {'PnL':>10}")
    print(f"{'-'*55}")
    dr2_results = sweep_single_feature(cycles, "dr2", dr2_thresholds, "<=")
    for r in dr2_results:
        print(f"{r['threshold']:>10.2f} {r['n']:>5} {r['retention']:>5.0%} "
              f"{r['wr']:>5.0%} {r['sr']:>5.0%} ${r['er']:>7.2f} ${r['pnl']:>9,.0f}")

    # --- dslope thresholds ---
    # dslope is mostly negative (falling abs_slope = good). More negative = better.
    dslope_thresholds = [0.0, -1.0, -2.0, -3.0, -4.0, -5.0, -6.0, -8.0]

    print(f"\n{'='*60}")
    print(f"DSLOPE THRESHOLD SWEEP (keep dslope <= thresh)")
    print(f"{'='*60}")
    print(f"{'Threshold':>10} {'N':>5} {'Ret':>6} {'WR':>6} {'SR':>6} {'E[R]':>8} {'PnL':>10}")
    print(f"{'-'*55}")
    dslope_results = sweep_single_feature(cycles, "dslope", dslope_thresholds, "<=")
    for r in dslope_results:
        print(f"{r['threshold']:>10.2f} {r['n']:>5} {r['retention']:>5.0%} "
              f"{r['wr']:>5.0%} {r['sr']:>5.0%} ${r['er']:>7.2f} ${r['pnl']:>9,.0f}")

    # --- Combined sweep: best dr2 x dslope thresholds ---
    print(f"\n{'='*60}")
    print(f"COMBINED SWEEP: dr2 x dslope (top candidates)")
    print(f"{'='*60}")

    # Pick a few promising thresholds from each
    dr2_cands = [-0.20, -0.30, -0.40, -0.50]
    dslope_cands = [-2.0, -3.0, -4.0, -5.0]

    print(f"{'dr2':>8} {'dslope':>8} {'N':>5} {'Ret':>6} {'WR':>6} {'SR':>6} "
          f"{'E[R]':>8} {'dER':>8} {'PnL':>10}")
    print(f"{'-'*72}")

    combo_results = []
    for dr2_t in dr2_cands:
        for ds_t in dslope_cands:
            filtered = sweep_combined(cycles, dr2_t, ds_t)
            m = metrics(filtered)
            retention = m["n"] / bl["n"] if bl["n"] else 0
            delta_er = m["er"] - bl["er"]
            combo_results.append({
                "dr2_thresh": dr2_t, "dslope_thresh": ds_t,
                "retention": retention, "delta_er": delta_er, **m,
            })
            print(f"{dr2_t:>8.2f} {ds_t:>8.1f} {m['n']:>5} {retention:>5.0%} "
                  f"{m['wr']:>5.0%} {m['sr']:>5.0%} ${m['er']:>7.2f} "
                  f"${delta_er:>+7.2f} ${m['pnl']:>9,.0f}")

    # --- Per-week breakdown for top 3 combined filters ---
    # Sort by E[R] with minimum 50% retention
    viable = [r for r in combo_results if r["retention"] >= 0.50 and r["n"] >= 100]
    viable.sort(key=lambda r: r["er"], reverse=True)
    top3 = viable[:3]

    print(f"\n{'='*60}")
    print(f"PER-WEEK BREAKDOWN: Top 3 combined filters (>= 50% retention)")
    print(f"{'='*60}")

    for rank, combo in enumerate(top3, 1):
        dr2_t = combo["dr2_thresh"]
        ds_t = combo["dslope_thresh"]
        label = f"dr2<={dr2_t:.2f} + dslope<={ds_t:.1f}"
        print(f"\n  #{rank}: {label}  |  Pooled: {combo['n']} cyc "
              f"({combo['retention']:.0%} ret) E[R]=${combo['er']:.2f} "
              f"(d${combo['delta_er']:+.2f})")
        print(f"  {'Week':<6} {'Cat':<8} {'N':>5} {'WR':>6} {'SR':>6} "
              f"{'E[R]':>8} {'bl_ER':>8} {'dER':>8}")
        print(f"  {'-'*60}")

        for wk in WEEK_ORDER:
            wk_all = [c for c in cycles if c["week"] == wk]
            wk_filtered = [c for c in wk_all
                           if c["dr2"] is not None and c["dslope"] is not None
                           and c["dr2"] <= dr2_t and c["dslope"] <= ds_t]
            m_f = metrics(wk_filtered)
            m_bl = metrics(wk_all)
            d_er = m_f["er"] - m_bl["er"] if m_f["n"] > 0 else 0
            cat = WEEK_CATS[wk]
            if m_f["n"] > 0:
                print(f"  {wk:<6} {cat:<8} {m_f['n']:>5} {m_f['wr']:>5.0%} "
                      f"{m_f['sr']:>5.0%} ${m_f['er']:>7.2f} "
                      f"${m_bl['er']:>7.2f} ${d_er:>+7.2f}")
            else:
                print(f"  {wk:<6} {cat:<8}     0    --     --       --  "
                      f"${m_bl['er']:>7.2f}       --")

    # --- Save retroactive CSV ---
    retro_csv = OUTPUT_DIR / "entry-signals-retroactive.csv"
    with open(retro_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["filter", "n", "retention", "wr", "sr", "er", "pnl",
                     "delta_er"])
        w.writerow(["baseline", bl["n"], 1.0, f"{bl['wr']:.4f}",
                     f"{bl['sr']:.4f}", f"{bl['er']:.2f}",
                     f"{bl['pnl']:.2f}", "0.00"])
        for r in dr2_results:
            d = r["er"] - bl["er"]
            w.writerow([r["label"], r["n"], f"{r['retention']:.4f}",
                        f"{r['wr']:.4f}", f"{r['sr']:.4f}",
                        f"{r['er']:.2f}", f"{r['pnl']:.2f}", f"{d:.2f}"])
        for r in dslope_results:
            d = r["er"] - bl["er"]
            w.writerow([r["label"], r["n"], f"{r['retention']:.4f}",
                        f"{r['wr']:.4f}", f"{r['sr']:.4f}",
                        f"{r['er']:.2f}", f"{r['pnl']:.2f}", f"{d:.2f}"])
        for r in combo_results:
            label = f"dr2<={r['dr2_thresh']:.2f}+dslope<={r['dslope_thresh']:.1f}"
            w.writerow([label, r["n"], f"{r['retention']:.4f}",
                        f"{r['wr']:.4f}", f"{r['sr']:.4f}",
                        f"{r['er']:.2f}", f"{r['pnl']:.2f}",
                        f"{r['delta_er']:.2f}"])
    print(f"\nSaved: {retro_csv}")


if __name__ == "__main__":
    main()
