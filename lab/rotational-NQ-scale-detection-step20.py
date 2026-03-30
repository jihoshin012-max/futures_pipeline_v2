# archetype: rotational
"""
rotational-NQ-scale-detection-step20.py — Track B Step 4: Retroactive filter.

Sweep thresholds for fade_confirm and fade_speed (survivors from Step 3).
Compute PnL for subsets passing various thresholds. No sim re-run.
Per-week breakdown — must improve ALL weeks.

IMPORTANT: Track A already filtered to 6,624 cycles (64% retention from
chop-only 10,312). Track B must target HIGH retention (>80% of Track A's
6,624) to stay above the H2 statistical gate of 5,000 cycles.

Prompt: rotational-NQ-prompt-fade-confirmation.md
Depends on: Step 3 (step19.py) — survivors: fade_confirm, fade_speed.
"""
from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path

import numpy as np

COMMISSION_PER_RT_MINI = 3.50
OUTPUT_DIR = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")
CYCLE_CSV = OUTPUT_DIR / "fade-confirm-tagged-cycles.csv"


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
            }
            c["net_pnl"] = c["pnl_ticks"] * 5.0 - COMMISSION_PER_RT_MINI * max(c["max_position"], 1)
            for feat in ["fade_confirm", "fade_speed"]:
                v = row.get(feat, "")
                c[feat] = float(v) if v not in ("", "None") else None
            cycles.append(c)
    return cycles


def metrics(cyc_list):
    if not cyc_list:
        return {"n": 0, "wr": 0.0, "sr": 0.0, "er": 0.0, "pnl": 0.0}
    n = len(cyc_list)
    wins = sum(1 for c in cyc_list if c["net_pnl"] >= 0)
    stops = sum(1 for c in cyc_list if c["exit_type"] == "HARD_STOP")
    pnl = sum(c["net_pnl"] for c in cyc_list)
    return {"n": n, "wr": wins / n, "sr": stops / n, "er": pnl / n, "pnl": pnl}


def per_week_metrics(cyc_list):
    weeks = defaultdict(list)
    for c in cyc_list:
        weeks[c["week"]].append(c)
    return {wk: metrics(wc) for wk, wc in sorted(weeks.items())}


def main():
    cycles = load_cycles()
    print(f"Loaded {len(cycles)} cycles")

    baseline = metrics(cycles)
    bl_weeks = per_week_metrics(cycles)
    print(f"\nBaseline: {baseline['n']} cyc | {baseline['wr']:.0%} WR | "
          f"{baseline['sr']:.0%} SR | E[R]=${baseline['er']:.2f} | PnL=${baseline['pnl']:,.0f}")

    # Track A full P1: 6,624 cycles. H2 gate: 5,000.
    # Retention must be > 5000/6624 = 75.5% of Track A.
    # On the 5 test weeks we have 3,226 cycles.
    # Proportional minimum: 3226 * 0.755 = 2,436 cycles.
    min_cycles_proportional = int(3226 * 5000 / 6624)
    print(f"  Min cycles (proportional H2 gate): {min_cycles_proportional}")

    # ===================================================================
    # Single feature sweeps
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"SINGLE FEATURE: fade_confirm (BLOCK when >= threshold)")
    print(f"  Inverted: low values are favorable")
    print(f"{'='*70}")

    fc_thresholds = [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 1.0]
    print(f"\n{'Thresh':<8} {'N':>5} {'Ret':>5} {'WR':>6} {'SR':>6} {'E[R]':>8} {'PnL':>10}")
    print(f"{'-'*55}")

    for thresh in fc_thresholds:
        filtered = [c for c in cycles
                    if c["fade_confirm"] is not None and c["fade_confirm"] < thresh]
        m = metrics(filtered)
        ret = m["n"] / baseline["n"] if baseline["n"] > 0 else 0
        flag = " *" if m["n"] < min_cycles_proportional else ""
        print(f"<{thresh:<6.1f} {m['n']:>5} {ret:>4.0%} {m['wr']:>5.0%} "
              f"{m['sr']:>5.0%} ${m['er']:>7.2f} ${m['pnl']:>9,.0f}{flag}")

    # Per-week for promising thresholds
    for thresh in [0.5, 0.6, 0.7]:
        filtered = [c for c in cycles
                    if c["fade_confirm"] is not None and c["fade_confirm"] < thresh]
        wm = per_week_metrics(filtered)
        print(f"\n  fade_confirm < {thresh} per-week:")
        print(f"    {'Week':<10} {'Cat':<10} {'N':>5} {'BL_ER':>8} {'Filt_ER':>8} {'dER':>8} {'Ret':>5}")
        all_improved = True
        for wk in sorted(wm.keys()):
            cat = next((c["week_cat"] for c in cycles if c["week"] == wk), "")
            bm = bl_weeks.get(wk, {"er": 0, "n": 0})
            fm = wm[wk]
            d = fm["er"] - bm["er"]
            ret = fm["n"] / bm["n"] if bm["n"] > 0 else 0
            if d < 0:
                all_improved = False
            print(f"    {wk:<10} {cat:<10} {fm['n']:>5} ${bm['er']:>7.2f} "
                  f"${fm['er']:>7.2f} ${d:>7.2f} {ret:>4.0%}")
        print(f"    All improved: {all_improved}")

    print(f"\n{'='*70}")
    print(f"SINGLE FEATURE: fade_speed (BLOCK when >= threshold)")
    print(f"  Inverted: negative values are favorable")
    print(f"{'='*70}")

    fs_thresholds = [-0.5, -0.3, -0.1, 0.0, 0.1, 0.3, 0.5, 1.0]
    print(f"\n{'Thresh':<8} {'N':>5} {'Ret':>5} {'WR':>6} {'SR':>6} {'E[R]':>8} {'PnL':>10}")
    print(f"{'-'*55}")

    for thresh in fs_thresholds:
        filtered = [c for c in cycles
                    if c["fade_speed"] is not None and c["fade_speed"] < thresh]
        m = metrics(filtered)
        ret = m["n"] / baseline["n"] if baseline["n"] > 0 else 0
        flag = " *" if m["n"] < min_cycles_proportional else ""
        print(f"<{thresh:<6.1f} {m['n']:>5} {ret:>4.0%} {m['wr']:>5.0%} "
              f"{m['sr']:>5.0%} ${m['er']:>7.2f} ${m['pnl']:>9,.0f}{flag}")

    for thresh in [0.0, 0.3, 0.5]:
        filtered = [c for c in cycles
                    if c["fade_speed"] is not None and c["fade_speed"] < thresh]
        wm = per_week_metrics(filtered)
        print(f"\n  fade_speed < {thresh} per-week:")
        print(f"    {'Week':<10} {'Cat':<10} {'N':>5} {'BL_ER':>8} {'Filt_ER':>8} {'dER':>8} {'Ret':>5}")
        all_improved = True
        for wk in sorted(wm.keys()):
            cat = next((c["week_cat"] for c in cycles if c["week"] == wk), "")
            bm = bl_weeks.get(wk, {"er": 0, "n": 0})
            fm = wm[wk]
            d = fm["er"] - bm["er"]
            ret = fm["n"] / bm["n"] if bm["n"] > 0 else 0
            if d < 0:
                all_improved = False
            print(f"    {wk:<10} {cat:<10} {fm['n']:>5} ${bm['er']:>7.2f} "
                  f"${fm['er']:>7.2f} ${d:>7.2f} {ret:>4.0%}")
        print(f"    All improved: {all_improved}")

    # ===================================================================
    # Combined: fade_confirm + fade_speed
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"COMBINED: fade_confirm < X AND fade_speed < Y")
    print(f"{'='*70}")

    combos = [
        (0.5, 0.3), (0.5, 0.5), (0.5, 1.0),
        (0.6, 0.3), (0.6, 0.5), (0.6, 1.0),
        (0.7, 0.3), (0.7, 0.5), (0.7, 1.0),
        (0.8, 0.3), (0.8, 0.5), (0.8, 1.0),
        (1.0, 0.3), (1.0, 0.5), (1.0, 1.0),
    ]

    print(f"\n{'FC<':<6} {'FS<':<6} {'N':>5} {'Ret':>5} {'WR':>6} {'SR':>6} {'E[R]':>8} {'PnL':>10}")
    print(f"{'-'*60}")

    best_combo = None
    best_er = baseline["er"]

    for fc_t, fs_t in combos:
        filtered = [c for c in cycles
                    if c["fade_confirm"] is not None and c["fade_confirm"] < fc_t
                    and c["fade_speed"] is not None and c["fade_speed"] < fs_t]
        m = metrics(filtered)
        ret = m["n"] / baseline["n"] if baseline["n"] > 0 else 0
        flag = " *" if m["n"] < min_cycles_proportional else ""
        print(f"<{fc_t:<5.1f} <{fs_t:<5.1f} {m['n']:>5} {ret:>4.0%} {m['wr']:>5.0%} "
              f"{m['sr']:>5.0%} ${m['er']:>7.2f} ${m['pnl']:>9,.0f}{flag}")

        if m["n"] >= min_cycles_proportional and m["er"] > best_er:
            best_er = m["er"]
            best_combo = (fc_t, fs_t)

    # Per-week for best combos
    promising_combos = [(0.5, 0.3), (0.5, 0.5), (0.7, 0.3), (0.7, 0.5), (1.0, 0.3)]
    for fc_t, fs_t in promising_combos:
        filtered = [c for c in cycles
                    if c["fade_confirm"] is not None and c["fade_confirm"] < fc_t
                    and c["fade_speed"] is not None and c["fade_speed"] < fs_t]
        wm = per_week_metrics(filtered)
        if not wm:
            continue
        m = metrics(filtered)
        ret = m["n"] / baseline["n"]
        print(f"\n  fc<{fc_t} + fs<{fs_t} per-week ({m['n']} cyc, {ret:.0%} ret, E[R]=${m['er']:.2f}):")
        print(f"    {'Week':<10} {'Cat':<10} {'N':>5} {'BL_ER':>8} {'Filt_ER':>8} {'dER':>8} {'Ret':>5}")
        all_improved = True
        for wk in sorted(wm.keys()):
            cat = next((c["week_cat"] for c in cycles if c["week"] == wk), "")
            bm = bl_weeks.get(wk, {"er": 0, "n": 0})
            fm = wm[wk]
            d = fm["er"] - bm["er"]
            wk_ret = fm["n"] / bm["n"] if bm["n"] > 0 else 0
            if d < 0:
                all_improved = False
            print(f"    {wk:<10} {cat:<10} {fm['n']:>5} ${bm['er']:>7.2f} "
                  f"${fm['er']:>7.2f} ${d:>7.2f} {wk_ret:>4.0%}")
        print(f"    All improved: {all_improved}")

    # ===================================================================
    # Blocked cycles profile
    # ===================================================================
    if best_combo:
        fc_t, fs_t = best_combo
        print(f"\n{'='*70}")
        print(f"BEST COMBO: fc<{fc_t} + fs<{fs_t}")
        print(f"{'='*70}")
        passed = [c for c in cycles
                  if c["fade_confirm"] is not None and c["fade_confirm"] < fc_t
                  and c["fade_speed"] is not None and c["fade_speed"] < fs_t]
        blocked = [c for c in cycles if c not in passed]
        pm = metrics(passed)
        bm = metrics(blocked)
        print(f"  Passed:  {pm['n']} cyc | {pm['wr']:.0%} WR | {pm['sr']:.0%} SR | E[R]=${pm['er']:.2f}")
        print(f"  Blocked: {bm['n']} cyc | {bm['wr']:.0%} WR | {bm['sr']:.0%} SR | E[R]=${bm['er']:.2f}")

    # Save
    out_csv = OUTPUT_DIR / "fade-confirm-retroactive.csv"
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["filter", "n", "retention", "wr", "sr", "er", "pnl"])
        w.writerow(["baseline", baseline["n"], 1.0, f"{baseline['wr']:.4f}",
                     f"{baseline['sr']:.4f}", f"{baseline['er']:.2f}", f"{baseline['pnl']:.2f}"])
        for fc_t, fs_t in combos:
            filtered = [c for c in cycles
                        if c["fade_confirm"] is not None and c["fade_confirm"] < fc_t
                        and c["fade_speed"] is not None and c["fade_speed"] < fs_t]
            m = metrics(filtered)
            ret = m["n"] / baseline["n"]
            w.writerow([f"fc<{fc_t}+fs<{fs_t}", m["n"], f"{ret:.4f}",
                        f"{m['wr']:.4f}", f"{m['sr']:.4f}", f"{m['er']:.2f}", f"{m['pnl']:.2f}"])
    print(f"\nSaved: {out_csv}")
    print(f"\nDone.")


if __name__ == "__main__":
    main()
