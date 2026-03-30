# archetype: rotational
"""
rotational-NQ-scale-detection-step36.py — Track B2 Steps 5-7: Live sim with
EMA directional gate, full P1 validation, and sanity check.

Step 5: Wire EMA gate into sweep as direction-aware entry filter.
        Test on 5 test weeks vs A+B baseline. Per-week breakdown.
        Gate variants:
          A) d2_ema9 asymmetric: block LONGs when d2_ema9 <= 0 only
             (keeps SHORTs when d2>0 since they're still profitable)
             Expected retention ~85%
          B) d2_ema9 symmetric: block both against-trend directions
             (for comparison, expected retention ~69%)
          C) d2_ema9 with neutral zone: |d2| <= threshold -> allow both
          D) d_ema9 asymmetric: block LONGs when d_ema9 <= 0 only

Step 6: Full P1 validation with winning gate. Per-week breakdown.

Step 7: Sanity check — random directional blocking at matching retention
        rate (10 seeds). Must outperform all seeds.

Prompt: rotational-NQ-prompt-ema-directional-b2.md Steps 5-7
"""
from __future__ import annotations

import csv
import datetime
import importlib
import random
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
    d2_ema9_agg = np.full(n_agg, np.nan, dtype=np.float64)

    for i in range(n_agg):
        if not np.isnan(ema9_agg[i]) and not np.isnan(ema21_agg[i]):
            spread_agg[i] = ema9_agg[i] - ema21_agg[i]
    for i in range(1, n_agg):
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
        "d_ema9": map_signal_to_ticks(d_ema9_agg, tick_to_agg),
        "d2_ema9": map_signal_to_ticks(d2_ema9_agg, tick_to_agg),
        "warmup_ticks": {"dominant_scale": 0, "asymmetry": 0},
    }


# ---------------------------------------------------------------------------
#  Filter builders
# ---------------------------------------------------------------------------
def make_ab_filter():
    """A+B baseline: chop + dR2/dSlope + fade_confirm."""
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


def make_ab_plus_d2_asymmetric():
    """A+B + block LONGs when d2_ema9 <= 0 (asymmetric — SHORTs unaffected)."""
    ab = make_ab_filter()
    def f(signals, i, direction, step_dist):
        if not ab(signals, i, direction, step_dist):
            return False
        # Only gate LONGs
        if direction == 1:  # LONG
            d2 = signals["d2_ema9"][i]
            if np.isnan(d2):
                return True  # warmup — allow
            if d2 <= 0:
                return False  # EMA curvature down — block LONG
        return True
    return f


def make_ab_plus_d2_symmetric():
    """A+B + block both directions against d2_ema9 trend."""
    ab = make_ab_filter()
    def f(signals, i, direction, step_dist):
        if not ab(signals, i, direction, step_dist):
            return False
        d2 = signals["d2_ema9"][i]
        if np.isnan(d2):
            return True
        if direction == 1 and d2 <= 0:  # LONG against curvature
            return False
        if direction == -1 and d2 > 0:  # SHORT against curvature
            return False
        return True
    return f


def make_ab_plus_d2_neutral_zone(neutral_thresh):
    """A+B + d2_ema9 directional gate with neutral zone."""
    ab = make_ab_filter()
    def f(signals, i, direction, step_dist):
        if not ab(signals, i, direction, step_dist):
            return False
        d2 = signals["d2_ema9"][i]
        if np.isnan(d2):
            return True
        if abs(d2) <= neutral_thresh:
            return True  # neutral zone — allow both
        if direction == 1 and d2 < -neutral_thresh:
            return False
        if direction == -1 and d2 > neutral_thresh:
            return False
        return True
    return f


def make_ab_plus_d_ema9_asymmetric():
    """A+B + block LONGs when d_ema9 <= 0 (asymmetric)."""
    ab = make_ab_filter()
    def f(signals, i, direction, step_dist):
        if not ab(signals, i, direction, step_dist):
            return False
        if direction == 1:
            d = signals["d_ema9"][i]
            if np.isnan(d):
                return True
            if d <= 0:
                return False
        return True
    return f


def make_ab_plus_random_block(block_rate, seed_val):
    """A+B + random directional blocking at given rate for sanity check."""
    ab = make_ab_filter()
    rng = random.Random(seed_val)
    def f(signals, i, direction, step_dist):
        if not ab(signals, i, direction, step_dist):
            return False
        if rng.random() < block_rate:
            return False
        return True
    return f


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------
def get_week(seed_dt: str) -> str:
    dt = seed_dt[:10]
    d = datetime.date(int(dt[:4]), int(dt[5:7]), int(dt[8:10]))
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def cycle_metrics(cycles):
    if not cycles:
        return {"n": 0, "wr": 0.0, "sr": 0.0, "er": 0.0, "pnl": 0.0}
    n = len(cycles)
    pnls = []
    stops = 0
    for c in cycles:
        net = c["pnl_ticks"] * TV - COMM * max(c.get("max_position", 1), 1)
        pnls.append(net)
        if c["exit_type"] == "HARD_STOP":
            stops += 1
    total = sum(pnls)
    wins = sum(1 for p in pnls if p >= 0)
    return {"n": n, "wr": wins/n, "sr": stops/n, "er": total/n, "pnl": total}


def per_week(cycles):
    weeks = defaultdict(list)
    for c in cycles:
        wk = get_week(c["seed_dt"])
        weeks[wk].append(c)
    result = {}
    for wk in sorted(weeks.keys()):
        result[wk] = cycle_metrics(weeks[wk])
    return result


def print_comparison(label, bl_metrics, f_metrics, baseline_er):
    n_bl = bl_metrics["n"]
    n_f = f_metrics["n"]
    ret = n_f / n_bl * 100 if n_bl > 0 else 0
    delta = f_metrics["er"] - bl_metrics["er"]
    pct = delta / baseline_er * 100 if baseline_er else 0
    print(f"  {label}")
    print(f"    Baseline: {n_bl} cyc, {bl_metrics['wr']:.0%} WR, {bl_metrics['sr']:.0%} SR, "
          f"E[R]=${bl_metrics['er']:.2f}, PnL=${bl_metrics['pnl']:,.0f}")
    print(f"    Filtered: {n_f} cyc, {f_metrics['wr']:.0%} WR, {f_metrics['sr']:.0%} SR, "
          f"E[R]=${f_metrics['er']:.2f}, PnL=${f_metrics['pnl']:,.0f}")
    print(f"    Retention: {ret:.0f}% | dER: ${delta:+.2f} ({pct:+.1f}%)")


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 70)
    print("Track B2 Steps 5-7: Live sim + P1 validation + sanity check")
    print("=" * 70)

    print("\nLoading P1 data...")
    t0 = time.time()
    bars = load_bars_extended(DATA_FILE)
    print(f"  {bars['n']} ticks ({time.time()-t0:.0f}s)")

    print("\nPrecomputing signals...")
    t1 = time.time()
    signals = precompute_signals(bars, BAR_SIZE, LB)
    print(f"  Done ({time.time()-t1:.0f}s)")

    # =========================================================================
    # STEP 5: Live sim on test weeks
    # =========================================================================
    print(f"\n{'='*70}")
    print("STEP 5: Live sim — EMA directional gate variants")
    print(f"{'='*70}")

    # Baseline: A+B only
    print("\nRunning A+B baseline...")
    t1 = time.time()
    bl_cycles = run_sim_filtered(
        bars, SD, HS, MAX_FADES, MAX_LEVELS, MAX_CONTRACT_SIZE,
        signal_arrays=signals, filter_fn=make_ab_filter()
    )
    bl_m = cycle_metrics(bl_cycles)
    bl_weekly = per_week(bl_cycles)
    print(f"  {bl_m['n']} cycles, E[R]=${bl_m['er']:.2f} ({time.time()-t1:.0f}s)")

    # Gate variants
    variants = [
        ("A) d2_ema9 asymmetric (block LONG when d2<=0)",
         make_ab_plus_d2_asymmetric()),
        ("B) d2_ema9 symmetric (block both against-trend)",
         make_ab_plus_d2_symmetric()),
        ("C) d2_ema9 neutral zone |d2|<=0.5",
         make_ab_plus_d2_neutral_zone(0.5)),
        ("D) d2_ema9 neutral zone |d2|<=1.0",
         make_ab_plus_d2_neutral_zone(1.0)),
        ("E) d_ema9 asymmetric (block LONG when d<=0)",
         make_ab_plus_d_ema9_asymmetric()),
    ]

    results = {}
    for name, filter_fn in variants:
        print(f"\n  Running: {name}...")
        t1 = time.time()
        f_cycles = run_sim_filtered(
            bars, SD, HS, MAX_FADES, MAX_LEVELS, MAX_CONTRACT_SIZE,
            signal_arrays=signals, filter_fn=filter_fn
        )
        f_m = cycle_metrics(f_cycles)
        f_weekly = per_week(f_cycles)
        print(f"    {f_m['n']} cycles, E[R]=${f_m['er']:.2f} ({time.time()-t1:.0f}s)")

        results[name] = {"cycles": f_cycles, "metrics": f_m, "weekly": f_weekly}

    # Compare on test weeks
    print(f"\n{'='*70}")
    print("STEP 5 RESULTS: Per-week comparison (test weeks)")
    print(f"{'='*70}")

    for name in results:
        f_weekly = results[name]["weekly"]
        f_m = results[name]["metrics"]
        ret = f_m["n"] / bl_m["n"] * 100

        print(f"\n  === {name} ===")
        print(f"  P1 total: {f_m['n']} cyc ({ret:.0f}% ret), E[R]=${f_m['er']:.2f}")

        print(f"\n  {'Week':<10} {'Cat':<8} {'BL':>5} {'F':>5} {'Ret':>5} "
              f"{'BL_ER':>8} {'F_ER':>8} {'dER':>8} {'BL_SR':>5} {'F_SR':>5}")
        print(f"  {'-'*75}")

        weeks_improved = 0
        weeks_total = 0
        for wk in sorted(bl_weekly.keys()):
            m_bl = bl_weekly[wk]
            m_f = f_weekly.get(wk, {"n": 0, "wr": 0, "sr": 0, "er": 0, "pnl": 0})
            delta = m_f["er"] - m_bl["er"]
            r = m_f["n"] / m_bl["n"] * 100 if m_bl["n"] > 0 else 0
            improved = delta > 0
            if improved:
                weeks_improved += 1
            weeks_total += 1
            cat = TEST_WEEKS.get(wk, "")
            mark = "UP" if improved else "DN"
            print(f"  {wk:<10} {cat:<8} {m_bl['n']:>5} {m_f['n']:>5} {r:>4.0f}% "
                  f"${m_bl['er']:>7.2f} ${m_f['er']:>7.2f} ${delta:>+7.2f} "
                  f"{m_bl['sr']:>5.0%} {m_f['sr']:>5.0%} {mark}")

        print(f"\n  Weeks improved: {weeks_improved}/{weeks_total}")
        all_pos = all(f_weekly.get(wk, {"er": 0})["er"] >= 0 for wk in bl_weekly)
        print(f"  All weeks positive: {'YES' if all_pos else 'NO'}")

    # =========================================================================
    # STEP 6: Full P1 validation — identify best gate
    # =========================================================================
    print(f"\n{'='*70}")
    print("STEP 6: Gate selection summary")
    print(f"{'='*70}")

    print(f"\n  {'Gate':<50} {'Cycles':>6} {'Ret':>5} {'E[R]':>8} {'dER':>8} {'WR':>5} {'SR':>5}")
    print(f"  {'-'*90}")
    print(f"  {'A+B baseline':<50} {bl_m['n']:>6} {'100%':>5} ${bl_m['er']:>7.2f} {'--':>8} "
          f"{bl_m['wr']:>5.0%} {bl_m['sr']:>5.0%}")

    best_name = None
    best_er = bl_m["er"]
    for name in results:
        f_m = results[name]["metrics"]
        ret = f_m["n"] / bl_m["n"] * 100
        delta = f_m["er"] - bl_m["er"]
        viable = ret >= 77
        marker = " *" if not viable else ""
        print(f"  {name:<50} {f_m['n']:>6} {ret:>4.0f}% ${f_m['er']:>7.2f} ${delta:>+7.2f} "
              f"{f_m['wr']:>5.0%} {f_m['sr']:>5.0%}{marker}")
        if viable and f_m["er"] > best_er:
            best_er = f_m["er"]
            best_name = name

    if best_name is None:
        # Check if any gate with >10% E[R] improvement exists regardless of retention
        for name in results:
            f_m = results[name]["metrics"]
            delta_pct = (f_m["er"] - bl_m["er"]) / bl_m["er"] * 100
            ret = f_m["n"] / bl_m["n"] * 100
            if delta_pct > 10 and ret >= 70:
                if best_name is None or f_m["er"] > best_er:
                    best_er = f_m["er"]
                    best_name = name
        if best_name:
            print(f"\n  * Below 77% retention, but significant E[R] improvement")

    if best_name:
        print(f"\n  >>> Best gate: {best_name}")
        print(f"  >>> E[R]: ${best_er:.2f} (baseline ${bl_m['er']:.2f})")
    else:
        print(f"\n  >>> No gate meets both retention and improvement criteria")

    # =========================================================================
    # STEP 7: Sanity check — random directional blocking
    # =========================================================================
    print(f"\n{'='*70}")
    print("STEP 7: Sanity check — random directional blocking")
    print(f"{'='*70}")

    if best_name:
        best_m = results[best_name]["metrics"]
        block_rate = 1.0 - (best_m["n"] / bl_m["n"])
        print(f"\n  Gate: {best_name}")
        print(f"  Gate E[R]: ${best_m['er']:.2f}, block rate: {block_rate:.1%}")
        print(f"\n  Running 10 random seeds at same block rate...")

        random_ers = []
        for seed in range(10):
            rand_filter = make_ab_plus_random_block(block_rate, seed)
            rand_cycles = run_sim_filtered(
                bars, SD, HS, MAX_FADES, MAX_LEVELS, MAX_CONTRACT_SIZE,
                signal_arrays=signals, filter_fn=rand_filter
            )
            rand_m = cycle_metrics(rand_cycles)
            random_ers.append(rand_m["er"])
            beats = "BEATS" if best_m["er"] > rand_m["er"] else "LOSES"
            print(f"    Seed {seed}: {rand_m['n']} cyc, E[R]=${rand_m['er']:.2f} — {beats}")

        max_random = max(random_ers)
        margin = best_m["er"] - max_random
        beats_all = best_m["er"] > max_random

        print(f"\n  Gate E[R]: ${best_m['er']:.2f}")
        print(f"  Max random E[R]: ${max_random:.2f}")
        print(f"  Margin: ${margin:.2f}")
        print(f"  Sanity check: {'PASS' if beats_all else 'FAIL'} "
              f"{'- beats all 10 seeds' if beats_all else '- random can match'}")
    else:
        print("  No gate to test — skipping sanity check")

    # =========================================================================
    # Summary
    # =========================================================================
    print(f"\n{'='*70}")
    print("TRACK B2 SUMMARY")
    print(f"{'='*70}")

    if best_name and best_name in results:
        best_m = results[best_name]["metrics"]
        delta = best_m["er"] - bl_m["er"]
        pct = delta / bl_m["er"] * 100
        ret = best_m["n"] / bl_m["n"] * 100
        print(f"  Best gate: {best_name}")
        print(f"  E[R]: ${best_m['er']:.2f} (baseline ${bl_m['er']:.2f}, d=${delta:+.2f}, {pct:+.1f}%)")
        print(f"  Retention: {ret:.0f}% ({best_m['n']} of {bl_m['n']} cycles)")
        print(f"  WR: {best_m['wr']:.0%} (baseline {bl_m['wr']:.0%})")
        print(f"  SR: {best_m['sr']:.0%} (baseline {bl_m['sr']:.0%})")

        # Check success criteria
        er_pass = pct > 10
        ret_pass = ret >= 77
        print(f"\n  Success criteria:")
        print(f"    E[R] improvement > 10%: {'PASS' if er_pass else 'FAIL'} ({pct:+.1f}%)")
        print(f"    Retention > 77%: {'PASS' if ret_pass else 'FAIL'} ({ret:.0f}%)")

        f_weekly = results[best_name]["weekly"]
        all_pos = all(f_weekly.get(wk, {"er": 0})["er"] >= 0 for wk in bl_weekly)
        weeks_improved = sum(1 for wk in bl_weekly
                            if f_weekly.get(wk, {"er": 0})["er"] > bl_weekly[wk]["er"])
        print(f"    All weeks positive: {'PASS' if all_pos else 'FAIL'}")
        print(f"    Weeks improved: {weeks_improved}/{len(bl_weekly)}")
    else:
        print("  No viable gate found. Track B2 FAILS.")
        print("  The EMA directional signal is real but requires blocking too many")
        print("  trades to be useful as a filter — retention drops below viable levels.")

    print(f"\nRuntime: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
