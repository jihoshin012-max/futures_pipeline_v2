# archetype: rotational
"""
rotational-NQ-scale-detection-step35.py — Track B2 Steps 3+4: Threshold
optimization, redundancy check, and retroactive filter on 5 test weeks.

Step 3:
  - Sweep ema_spread thresholds (0.5, 1.0, 2.0, 3.0, 5.0) for blocking
    against-trend entries. Find threshold maximizing E[R] with >77% retention.
  - Also test d_ema9 and d2_ema9 as directional gates (binary, no threshold).
  - Compute pairwise correlations (Pearson) between features. If rho > 0.7,
    keep the stronger.
  - d2_ema9 is a refinement of d_ema9 — test combined gate.

Step 4:
  - Apply winning EMA gate retroactively on 5 test weeks.
  - Per-week breakdown — must improve ALL weeks.

Prompt: rotational-NQ-prompt-ema-directional-b2.md Steps 3-4
Depends on: Steps 1-2 (step34.py) — tagged cycles + kill gate PASSED.
"""
from __future__ import annotations

import csv
import datetime
import importlib
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, r"c:\Projects\futures_pipeline\lab")

_engine = importlib.import_module("rotational-NQ-scale-detection-engine")
_sweep = importlib.import_module("rotational-NQ-scale-detection-sweep")
load_bars_extended = _engine.load_bars_extended
aggregate_to_ntick = _engine.aggregate_to_ntick
compute_regime_signals = _engine.compute_regime_signals
compute_entry_signals = _engine.compute_entry_signals
map_signal_to_ticks = _engine.map_signal_to_ticks
run_sim_filtered = _sweep.run_sim_filtered


# ---------------------------------------------------------------------------
#  Config
# ---------------------------------------------------------------------------
SD = 10.0
HS = 60.0
MAX_LEVELS = 1
MAX_CONTRACT_SIZE = 2
MAX_FADES = 0
BAR_SIZE = 250
LB = 3

CHOP_THRESHOLD = 0.10
DR2_MAX = -0.40
DSLOPE_MAX = -2.0
FC_MAX = 0.40

COMM = 3.50
TV = 5.0
BASELINE_CYCLES = 6496
BASELINE_ER = 78.16

TEST_WEEKS = {
    "2025-W40": "WEAKEST",
    "2025-W48": "LOW",
    "2025-W41": "MID",
    "2025-W46": "GOOD",
    "2025-W47": "BEST",
}

OUTPUT_DIR = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")
DATA_FILE = r"C:\Projects\futures_pipeline\data\NQ-1tick-calibration.csv"


# ---------------------------------------------------------------------------
#  EMA computation
# ---------------------------------------------------------------------------
def ema(data, period):
    out = np.full(len(data), np.nan, dtype=np.float64)
    k = 2.0 / (period + 1)
    first_valid = -1
    for i in range(len(data)):
        if np.isnan(data[i]):
            continue
        if first_valid < 0:
            out[i] = data[i]
            first_valid = i
        else:
            out[i] = data[i] * k + out[i - 1] * (1 - k)
    return out


# ---------------------------------------------------------------------------
#  Signal precomputation
# ---------------------------------------------------------------------------
def precompute_signals(bars, bar_size, lookback):
    agg_bars, tick_to_agg = aggregate_to_ntick(bars, bar_size)
    regime = compute_regime_signals(agg_bars, lookback=lookback)
    entry = compute_entry_signals(agg_bars, lookback=lookback)

    n_agg = agg_bars["n"]
    a_high = agg_bars["high"]
    a_low = agg_bars["low"]
    last = bars["last"]

    prev_high = np.full(n_agg, np.nan, dtype=np.float64)
    prev_low = np.full(n_agg, np.nan, dtype=np.float64)
    prev_range = np.full(n_agg, np.nan, dtype=np.float64)
    for ai in range(1, n_agg):
        prev_high[ai] = float(a_high[ai - 1])
        prev_low[ai] = float(a_low[ai - 1])
        rng = float(a_high[ai - 1]) - float(a_low[ai - 1])
        prev_range[ai] = rng if rng > 0 else np.nan

    close_agg = np.array(agg_bars["last"], dtype=np.float64)
    ema9_agg = ema(close_agg, 9)
    ema21_agg = ema(close_agg, 21)

    spread_agg = np.full(n_agg, np.nan, dtype=np.float64)
    d_ema9_agg = np.full(n_agg, np.nan, dtype=np.float64)
    d_spread_agg = np.full(n_agg, np.nan, dtype=np.float64)
    d2_ema9_agg = np.full(n_agg, np.nan, dtype=np.float64)

    for i in range(n_agg):
        if not np.isnan(ema9_agg[i]) and not np.isnan(ema21_agg[i]):
            spread_agg[i] = ema9_agg[i] - ema21_agg[i]
    for i in range(1, n_agg):
        if not np.isnan(spread_agg[i]) and not np.isnan(spread_agg[i - 1]):
            d_spread_agg[i] = spread_agg[i] - spread_agg[i - 1]
        if not np.isnan(ema9_agg[i]) and not np.isnan(ema9_agg[i - 1]):
            d_ema9_agg[i] = ema9_agg[i] - ema9_agg[i - 1]
    for i in range(2, n_agg):
        if not np.isnan(d_ema9_agg[i]) and not np.isnan(d_ema9_agg[i - 1]):
            d2_ema9_agg[i] = d_ema9_agg[i] - d_ema9_agg[i - 1]

    return {
        "choppiness": map_signal_to_ticks(regime["choppiness"], tick_to_agg),
        "dr2": map_signal_to_ticks(entry["dr2"], tick_to_agg),
        "dslope": map_signal_to_ticks(entry["dslope"], tick_to_agg),
        "prev_high": prev_high[tick_to_agg],
        "prev_low": prev_low[tick_to_agg],
        "prev_range": prev_range[tick_to_agg],
        "last": last,
        "ema_spread": map_signal_to_ticks(spread_agg, tick_to_agg),
        "d_ema9": map_signal_to_ticks(d_ema9_agg, tick_to_agg),
        "d_spread": map_signal_to_ticks(d_spread_agg, tick_to_agg),
        "d2_ema9": map_signal_to_ticks(d2_ema9_agg, tick_to_agg),
        "warmup_ticks": {"dominant_scale": 0, "asymmetry": 0},
    }


# ---------------------------------------------------------------------------
#  A+B filter
# ---------------------------------------------------------------------------
def make_ab_filter():
    def f(signals, i, direction, step_dist):
        chop = signals["choppiness"][i]
        if np.isnan(chop):
            return True
        if chop >= CHOP_THRESHOLD:
            return False
        dr2 = signals["dr2"][i]
        if np.isnan(dr2):
            return True
        if dr2 > DR2_MAX:
            return False
        ds = signals["dslope"][i]
        if np.isnan(ds):
            return True
        if ds > DSLOPE_MAX:
            return False
        prev_range = signals["prev_range"][i]
        if np.isnan(prev_range):
            return True
        entry_price = float(signals["last"][i])
        if direction == 1:
            fc = (entry_price - float(signals["prev_low"][i])) / prev_range
        else:
            fc = (float(signals["prev_high"][i]) - entry_price) / prev_range
        return fc < FC_MAX
    return f


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------
def get_week(seed_dt: str) -> str:
    dt = seed_dt[:10]
    d = datetime.date(int(dt[:4]), int(dt[5:7]), int(dt[8:10]))
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def net_pnl(c: dict) -> float:
    return c["pnl_ticks"] * TV - COMM * max(c.get("max_position", 1), 1)


def metrics(cyc_list):
    if not cyc_list:
        return {"n": 0, "wr": 0.0, "sr": 0.0, "er": 0.0, "pnl": 0.0}
    n = len(cyc_list)
    wins = sum(1 for c in cyc_list if c["_net_pnl"] >= 0)
    stops = sum(1 for c in cyc_list if c["exit_type"] == "HARD_STOP")
    pnl = sum(c["_net_pnl"] for c in cyc_list)
    return {"n": n, "wr": wins / n, "sr": stops / n, "er": pnl / n, "pnl": pnl}


def print_row(label, m, baseline_er=None):
    extra = ""
    if baseline_er and m["n"] > 0:
        delta = m["er"] - baseline_er
        pct = delta / baseline_er * 100 if baseline_er else 0
        retention = m["n"] / BASELINE_CYCLES * 100
        extra = f" | d=${delta:+.2f} ({pct:+.1f}%) | ret={retention:.0f}%"
    print(f"  {label:<40} {m['n']:>5} cyc | {m['wr']:.0%} WR | {m['sr']:.0%} SR | "
          f"E[R]=${m['er']:>8.2f} | PnL=${m['pnl']:>10,.0f}{extra}")


# ---------------------------------------------------------------------------
#  Gate functions (retroactive — applied to tagged cycles, not sim)
# ---------------------------------------------------------------------------
def gate_ema_spread(c, threshold):
    """Block against-trend. Allow with-trend + neutral."""
    spread = c["_ema_spread"]
    direction = c["direction"]
    if spread > threshold and direction == "SHORT":
        return False  # EMA says up, SHORT is against
    if spread < -threshold and direction == "LONG":
        return False  # EMA says down, LONG is against
    return True


def gate_d_ema9(c):
    """Block against-trend based on d_ema9 sign."""
    d = c["_d_ema9"]
    direction = c["direction"]
    if d > 0 and direction == "SHORT":
        return False  # EMA9 rising, SHORT is against
    if d <= 0 and direction == "LONG":
        return False  # EMA9 falling, LONG is against
    return True


def gate_d2_ema9(c):
    """Block against-trend based on d2_ema9 sign."""
    d2 = c["_d2_ema9"]
    if np.isnan(d2):
        return True  # warmup — allow
    direction = c["direction"]
    if d2 > 0 and direction == "SHORT":
        return False  # curvature up, SHORT is against
    if d2 <= 0 and direction == "LONG":
        return False  # curvature down, LONG is against
    return True


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 70)
    print("Track B2 Steps 3+4: Threshold optimization + retroactive filter")
    print("=" * 70)

    # Load and tag all P1 cycles (same as step34)
    print("\nLoading P1 data...")
    t0 = time.time()
    bars = load_bars_extended(DATA_FILE)
    print(f"  {bars['n']} ticks ({time.time()-t0:.0f}s)")

    print("\nPrecomputing signals...")
    t1 = time.time()
    signals = precompute_signals(bars, BAR_SIZE, LB)
    print(f"  Done ({time.time()-t1:.0f}s)")

    print("\nRunning A+B filtered sim...")
    t1 = time.time()
    all_cycles = run_sim_filtered(
        bars, SD, HS, MAX_FADES, MAX_LEVELS, MAX_CONTRACT_SIZE,
        signal_arrays=signals, filter_fn=make_ab_filter()
    )
    print(f"  {len(all_cycles)} total cycles ({time.time()-t1:.0f}s)")

    # Tag
    for c in all_cycles:
        bar_idx = c["seed_bar"]
        c["_ema_spread"] = float(signals["ema_spread"][bar_idx])
        c["_d_ema9"] = float(signals["d_ema9"][bar_idx])
        c["_d_spread"] = float(signals["d_spread"][bar_idx])
        c["_d2_ema9"] = float(signals["d2_ema9"][bar_idx])
        c["_net_pnl"] = net_pnl(c)
        c["_week"] = get_week(c["seed_dt"])

    cycles = [c for c in all_cycles
              if not np.isnan(c["_ema_spread"]) and not np.isnan(c["_d_ema9"])]
    print(f"  {len(cycles)} valid cycles")

    # =========================================================================
    # STEP 3: Threshold optimization + redundancy
    # =========================================================================
    print(f"\n{'='*70}")
    print("STEP 3A: ema_spread threshold sweep (block against-trend)")
    print(f"{'='*70}")
    print(f"\n  Baseline: {BASELINE_CYCLES} cycles, E[R]=${BASELINE_ER}")
    print(f"  Min retention: 77% = {int(BASELINE_CYCLES * 0.77)} cycles")

    m_bl = metrics(cycles)
    print_row("A+B baseline (all)", m_bl)

    thresholds = [0.5, 1.0, 2.0, 3.0, 5.0]
    best_gate = None
    best_er = m_bl["er"]

    for thresh in thresholds:
        kept = [c for c in cycles if gate_ema_spread(c, thresh)]
        m = metrics(kept)
        retention = m["n"] / BASELINE_CYCLES
        viable = retention >= 0.77
        marker = " <<<" if m["er"] > best_er and viable else ""
        if m["er"] > best_er and viable:
            best_er = m["er"]
            best_gate = ("ema_spread", thresh)
        print_row(f"ema_spread ±{thresh} (block against){' *' if not viable else ''}",
                  m, BASELINE_ER)
        if not viable:
            print(f"    *** BELOW 77% retention — not viable")

    # d_ema9 gate
    print(f"\n{'='*70}")
    print("STEP 3B: d_ema9 gate (block against-trend)")
    print(f"{'='*70}")

    kept_d = [c for c in cycles if gate_d_ema9(c)]
    m_d = metrics(kept_d)
    retention_d = m_d["n"] / BASELINE_CYCLES
    print_row("d_ema9 (block against)", m_d, BASELINE_ER)
    if retention_d < 0.77:
        print(f"    *** BELOW 77% retention — not viable")
    elif m_d["er"] > best_er:
        best_er = m_d["er"]
        best_gate = ("d_ema9", 0)

    # d2_ema9 gate
    print(f"\n{'='*70}")
    print("STEP 3C: d2_ema9 gate (block against-trend)")
    print(f"{'='*70}")

    kept_d2 = [c for c in cycles if gate_d2_ema9(c)]
    m_d2 = metrics(kept_d2)
    retention_d2 = m_d2["n"] / BASELINE_CYCLES
    print_row("d2_ema9 (block against)", m_d2, BASELINE_ER)
    if retention_d2 < 0.77:
        print(f"    *** BELOW 77% retention — not viable")
    elif m_d2["er"] > best_er:
        best_er = m_d2["er"]
        best_gate = ("d2_ema9", 0)

    # Combined: d_ema9 + d2_ema9
    print(f"\n{'='*70}")
    print("STEP 3D: Combined gates")
    print(f"{'='*70}")

    # d_ema9 AND d2_ema9
    kept_combo1 = [c for c in cycles if gate_d_ema9(c) and gate_d2_ema9(c)]
    m_c1 = metrics(kept_combo1)
    print_row("d_ema9 AND d2_ema9", m_c1, BASELINE_ER)
    if m_c1["n"] / BASELINE_CYCLES < 0.77:
        print(f"    *** BELOW 77% retention — not viable")

    # ema_spread + d_ema9
    for thresh in [1.0, 2.0, 3.0]:
        kept_combo2 = [c for c in cycles
                       if gate_ema_spread(c, thresh) and gate_d_ema9(c)]
        m_c2 = metrics(kept_combo2)
        print_row(f"ema_spread ±{thresh} AND d_ema9", m_c2, BASELINE_ER)
        if m_c2["n"] / BASELINE_CYCLES < 0.77:
            print(f"    *** BELOW 77% retention — not viable")

    # ema_spread + d2_ema9
    for thresh in [1.0, 2.0, 3.0]:
        kept_combo3 = [c for c in cycles
                       if gate_ema_spread(c, thresh) and gate_d2_ema9(c)]
        m_c3 = metrics(kept_combo3)
        print_row(f"ema_spread ±{thresh} AND d2_ema9", m_c3, BASELINE_ER)
        if m_c3["n"] / BASELINE_CYCLES < 0.77:
            print(f"    *** BELOW 77% retention — not viable")

    # Redundancy check: pairwise correlations
    print(f"\n{'='*70}")
    print("STEP 3E: Feature redundancy (Pearson correlation)")
    print(f"{'='*70}")

    # Build feature vectors (aligned to cycle-level)
    spreads = np.array([c["_ema_spread"] for c in cycles])
    d_ema9s = np.array([c["_d_ema9"] for c in cycles])
    d_spreads = np.array([c["_d_spread"] for c in cycles])
    d2_valid_mask = np.array([not np.isnan(c["_d2_ema9"]) for c in cycles])
    d2_ema9s = np.array([c["_d2_ema9"] if not np.isnan(c["_d2_ema9"]) else 0.0
                          for c in cycles])

    # Convert to directional alignment score for correlation
    # +1 if feature says "with trend", -1 if "against trend"
    dirs = np.array([1.0 if c["direction"] == "LONG" else -1.0 for c in cycles])
    align_spread = np.sign(spreads) * dirs  # positive = aligned
    align_d_ema9 = np.sign(d_ema9s) * dirs
    align_d2_ema9 = np.sign(d2_ema9s) * dirs

    pairs = [
        ("ema_spread vs d_ema9", spreads, d_ema9s),
        ("ema_spread vs d_spread", spreads, d_spreads),
        ("ema_spread vs d2_ema9", spreads[d2_valid_mask], d2_ema9s[d2_valid_mask]),
        ("d_ema9 vs d_spread", d_ema9s, d_spreads),
        ("d_ema9 vs d2_ema9", d_ema9s[d2_valid_mask], d2_ema9s[d2_valid_mask]),
        ("d_spread vs d2_ema9", d_spreads[d2_valid_mask], d2_ema9s[d2_valid_mask]),
    ]

    # Also directional alignment correlations
    align_pairs = [
        ("align(ema_spread) vs align(d_ema9)", align_spread, align_d_ema9),
        ("align(ema_spread) vs align(d2_ema9)",
         align_spread[d2_valid_mask], align_d2_ema9[d2_valid_mask]),
        ("align(d_ema9) vs align(d2_ema9)",
         align_d_ema9[d2_valid_mask], align_d2_ema9[d2_valid_mask]),
    ]

    print("\n  Raw feature correlations:")
    for name, a, b in pairs:
        mask = ~np.isnan(a) & ~np.isnan(b)
        if mask.sum() < 10:
            print(f"    {name}: insufficient data")
            continue
        rho = np.corrcoef(a[mask], b[mask])[0, 1]
        flag = " *** REDUNDANT" if abs(rho) > 0.7 else ""
        print(f"    {name}: rho={rho:.3f}{flag}")

    print("\n  Directional alignment correlations:")
    for name, a, b in align_pairs:
        mask = ~np.isnan(a) & ~np.isnan(b)
        if mask.sum() < 10:
            continue
        rho = np.corrcoef(a[mask], b[mask])[0, 1]
        flag = " *** REDUNDANT" if abs(rho) > 0.7 else ""
        print(f"    {name}: rho={rho:.3f}{flag}")

    # Agreement rate: how often do gates agree?
    print("\n  Gate agreement rates:")
    for thresh in [1.0, 2.0, 3.0]:
        agree_spread_d = sum(1 for c in cycles
                             if gate_ema_spread(c, thresh) == gate_d_ema9(c))
        agree_spread_d2 = sum(1 for c in cycles
                              if gate_ema_spread(c, thresh) == gate_d2_ema9(c))
        agree_d_d2 = sum(1 for c in cycles
                         if gate_d_ema9(c) == gate_d2_ema9(c))
        n = len(cycles)
        print(f"    ema_spread(±{thresh}) vs d_ema9: {agree_spread_d/n:.0%}")
        print(f"    ema_spread(±{thresh}) vs d2_ema9: {agree_spread_d2/n:.0%}")
        print(f"    d_ema9 vs d2_ema9: {agree_d_d2/n:.0%}")

    # =========================================================================
    # STEP 3 Summary
    # =========================================================================
    print(f"\n{'='*70}")
    print("STEP 3 SUMMARY: Best gate selection")
    print(f"{'='*70}")
    if best_gate:
        print(f"  Best viable gate: {best_gate[0]} (thresh={best_gate[1]})")
        print(f"  Best E[R]: ${best_er:.2f} (baseline ${BASELINE_ER})")
    else:
        print(f"  No gate improved over baseline while maintaining >77% retention")

    # =========================================================================
    # STEP 4: Retroactive filter on 5 test weeks
    # =========================================================================
    print(f"\n{'='*70}")
    print("STEP 4: Retroactive filter on 5 test weeks")
    print(f"{'='*70}")

    test_cycles = [c for c in cycles if c["_week"] in TEST_WEEKS]
    print(f"\n  {len(test_cycles)} cycles in 5 test weeks")

    # Test all viable gates
    gates_to_test = []

    # ema_spread gates that passed retention
    for thresh in thresholds:
        kept = [c for c in cycles if gate_ema_spread(c, thresh)]
        if len(kept) / BASELINE_CYCLES >= 0.77:
            gates_to_test.append((f"ema_spread ±{thresh}",
                                  lambda c, t=thresh: gate_ema_spread(c, t)))

    # d_ema9
    if retention_d >= 0.77:
        gates_to_test.append(("d_ema9", gate_d_ema9))

    # d2_ema9
    if retention_d2 >= 0.77:
        gates_to_test.append(("d2_ema9", gate_d2_ema9))

    for gate_name, gate_fn in gates_to_test:
        print(f"\n  --- {gate_name} ---")

        # Per-week breakdown: baseline vs filtered
        print(f"  {'Week':<10} {'Cat':<8} {'BL_cyc':>6} {'F_cyc':>6} {'Ret':>5} "
              f"{'BL_ER':>8} {'F_ER':>8} {'dER':>8} {'BL_SR':>5} {'F_SR':>5}")
        print(f"  {'-'*80}")

        all_improved = True
        for wk in sorted(TEST_WEEKS.keys()):
            cat = TEST_WEEKS[wk]
            bl_cycs = [c for c in test_cycles if c["_week"] == wk]
            f_cycs = [c for c in bl_cycs if gate_fn(c)]
            m_bl = metrics(bl_cycs)
            m_f = metrics(f_cycs)
            delta_er = m_f["er"] - m_bl["er"] if m_f["n"] > 0 else 0
            ret = m_f["n"] / m_bl["n"] if m_bl["n"] > 0 else 0
            improved = delta_er > 0
            if not improved:
                all_improved = False
            marker = "  UP" if improved else "  DN"
            print(f"  {wk:<10} {cat:<8} {m_bl['n']:>6} {m_f['n']:>6} {ret:>5.0%} "
                  f"${m_bl['er']:>7.2f} ${m_f['er']:>7.2f} ${delta_er:>+7.2f} "
                  f"{m_bl['sr']:>5.0%} {m_f['sr']:>5.0%}{marker}")

        # Pooled test weeks
        f_test = [c for c in test_cycles if gate_fn(c)]
        m_bl_all = metrics(test_cycles)
        m_f_all = metrics(f_test)
        print(f"\n  Test weeks pooled:")
        print_row("Baseline", m_bl_all)
        print_row("Filtered", m_f_all, m_bl_all["er"])
        print(f"  All weeks improved: {'YES' if all_improved else 'NO'}")

    # =========================================================================
    # STEP 4: Full P1 per-week for the best gate
    # =========================================================================
    print(f"\n{'='*70}")
    print("STEP 4B: Full P1 per-week breakdown for ALL viable gates")
    print(f"{'='*70}")

    # Save retroactive results
    retro_rows = []

    for gate_name, gate_fn in gates_to_test:
        print(f"\n  --- {gate_name} (full P1) ---")
        print(f"  {'Week':<10} {'BL_cyc':>6} {'F_cyc':>6} {'Ret':>5} "
              f"{'BL_ER':>8} {'F_ER':>8} {'dER':>8} {'Improved':>8}")
        print(f"  {'-'*70}")

        weeks_improved = 0
        weeks_total = 0
        all_weeks_positive = True

        for wk in sorted(set(c["_week"] for c in cycles)):
            bl_cycs = [c for c in cycles if c["_week"] == wk]
            f_cycs = [c for c in bl_cycs if gate_fn(c)]
            m_bl = metrics(bl_cycs)
            m_f = metrics(f_cycs)
            delta_er = m_f["er"] - m_bl["er"] if m_f["n"] > 0 else 0
            ret = m_f["n"] / m_bl["n"] if m_bl["n"] > 0 else 0
            improved = delta_er > 0
            if improved:
                weeks_improved += 1
            if m_f["er"] < 0:
                all_weeks_positive = False
            weeks_total += 1
            print(f"  {wk:<10} {m_bl['n']:>6} {m_f['n']:>6} {ret:>5.0%} "
                  f"${m_bl['er']:>7.2f} ${m_f['er']:>7.2f} ${delta_er:>+7.2f} "
                  f"{'UP' if improved else 'DN'}")

            retro_rows.append({
                "gate": gate_name, "week": wk,
                "bl_cycles": m_bl["n"], "f_cycles": m_f["n"],
                "retention": round(ret, 4),
                "bl_er": round(m_bl["er"], 2), "f_er": round(m_f["er"], 2),
                "delta_er": round(delta_er, 2),
                "bl_sr": round(m_bl["sr"], 4), "f_sr": round(m_f["sr"], 4),
            })

        # Pooled
        f_all = [c for c in cycles if gate_fn(c)]
        m_all_f = metrics(f_all)
        m_all_bl = metrics(cycles)
        delta = m_all_f["er"] - m_all_bl["er"]
        pct = delta / m_all_bl["er"] * 100
        ret_all = m_all_f["n"] / BASELINE_CYCLES * 100
        print(f"\n  P1 pooled: {m_all_f['n']} cycles ({ret_all:.0f}% retention), "
              f"E[R]=${m_all_f['er']:.2f} (d=${delta:+.2f}, {pct:+.1f}%)")
        print(f"  Weeks improved: {weeks_improved}/{weeks_total}")
        print(f"  All weeks positive: {'YES' if all_weeks_positive else 'NO'}")

    # Save CSV
    out_path = OUTPUT_DIR / "b2-ema-directional-retroactive.csv"
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "gate", "week", "bl_cycles", "f_cycles", "retention",
            "bl_er", "f_er", "delta_er", "bl_sr", "f_sr"])
        w.writeheader()
        for row in retro_rows:
            w.writerow(row)
    print(f"\n  Saved: {out_path}")

    print(f"\nRuntime: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
