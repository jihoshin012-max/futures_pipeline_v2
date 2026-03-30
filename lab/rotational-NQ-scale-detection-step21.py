# archetype: rotational
"""
rotational-NQ-scale-detection-step21.py — Track B Step 5: Live sim on 5 test weeks.

Wire fade_confirm (and optionally fade_speed) into the existing sweep as
additional entry gates, stacked after chop + dR2/dSlope. Compare against
Track A baseline. Per-week breakdown.

Prompt: rotational-NQ-prompt-fade-confirmation.md
Depends on: Step 4 (step20.py) — promising thresholds identified.
"""
from __future__ import annotations

import csv
import datetime
import importlib
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

_engine = importlib.import_module("rotational-NQ-scale-detection-engine")
load_bars_extended = _engine.load_bars_extended
aggregate_to_ntick = _engine.aggregate_to_ntick
compute_regime_signals = _engine.compute_regime_signals
compute_entry_signals = _engine.compute_entry_signals
map_signal_to_ticks = _engine.map_signal_to_ticks

_sweep = importlib.import_module("rotational-NQ-scale-detection-sweep")
run_sim_filtered = _sweep.run_sim_filtered
TICK_SIZE = _sweep.TICK_SIZE
COMMISSION_PER_RT_MINI = _sweep.COMMISSION_PER_RT_MINI


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

TEST_WEEKS = {
    "2025-W40": "WEAKEST",
    "2025-W44": "MID",
    "2025-W46": "GOOD",
    "2025-W47": "BEST",
    "2025-W49": "LOW",
}

OUTPUT_DIR = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")


# ---------------------------------------------------------------------------
#  Signal precomputation
# ---------------------------------------------------------------------------

def precompute_signals(bars, bar_size, lookback):
    agg_bars, tick_to_agg = aggregate_to_ntick(bars, bar_size)
    regime = compute_regime_signals(agg_bars, lookback=lookback)
    entry = compute_entry_signals(agg_bars, lookback=lookback)

    # Pre-compute fade_confirm and fade_speed per tick for filter use.
    # fade_confirm at tick i = position of price[i] within prev completed agg bar's range.
    # fade_speed at tick i = (close - open) / duration of prev completed agg bar.
    # "prev completed" = tick_to_agg[i] - 1 (current agg bar is incomplete).

    n_ticks = bars["n"]
    n_agg = agg_bars["n"]
    a_high = agg_bars["high"]
    a_low = agg_bars["low"]
    a_open = agg_bars["open"]
    a_close = agg_bars["last"]
    a_tsec = agg_bars["time_sec"]
    a_dint = agg_bars["date_int"]
    last = bars["last"]

    # Pre-compute per-agg-bar values
    # For fade_confirm, we need prev bar's high/low at each tick.
    # For fade_speed, we need (close-open)/duration of prev bar.
    # These don't depend on direction — we'll handle direction in the filter.

    # Prev bar range arrays (indexed by agg bar index)
    prev_high = np.full(n_agg, np.nan, dtype=np.float64)
    prev_low = np.full(n_agg, np.nan, dtype=np.float64)
    prev_range = np.full(n_agg, np.nan, dtype=np.float64)
    prev_displacement = np.full(n_agg, np.nan, dtype=np.float64)  # close - open
    prev_duration = np.full(n_agg, np.nan, dtype=np.float64)

    for ai in range(1, n_agg):
        prev = ai - 1
        prev_high[ai] = float(a_high[prev])
        prev_low[ai] = float(a_low[prev])
        rng = float(a_high[prev]) - float(a_low[prev])
        prev_range[ai] = rng if rng > 0 else np.nan
        prev_displacement[ai] = float(a_close[prev]) - float(a_open[prev])
        # Duration: time from start of prev bar to start of current bar
        if int(a_dint[ai]) == int(a_dint[prev]):
            dur = float(a_tsec[ai]) - float(a_tsec[prev])
            prev_duration[ai] = dur if dur > 0 else np.nan
        # else: session boundary, leave as nan

    # Map to ticks
    prev_high_tick = prev_high[tick_to_agg]
    prev_low_tick = prev_low[tick_to_agg]
    prev_range_tick = prev_range[tick_to_agg]
    prev_disp_tick = prev_displacement[tick_to_agg]
    prev_dur_tick = prev_duration[tick_to_agg]

    return {
        "choppiness": map_signal_to_ticks(regime["choppiness"], tick_to_agg),
        "slope": map_signal_to_ticks(regime["slope"], tick_to_agg),
        "dr2": map_signal_to_ticks(entry["dr2"], tick_to_agg),
        "dslope": map_signal_to_ticks(entry["dslope"], tick_to_agg),
        "tick_to_agg": tick_to_agg,
        "warmup_ticks": {"dominant_scale": 0, "asymmetry": 0},
        # Fade confirmation arrays (tick-level)
        "prev_high": prev_high_tick,
        "prev_low": prev_low_tick,
        "prev_range": prev_range_tick,
        "prev_disp": prev_disp_tick,
        "prev_dur": prev_dur_tick,
        "last": last,  # tick prices for fade_confirm computation
    }


# ---------------------------------------------------------------------------
#  Filter factories
# ---------------------------------------------------------------------------

def make_track_a_filter(chop_max, dr2_max, dslope_max):
    """Track A filter only (baseline for comparison)."""
    def f(signals, i, direction, step_dist):
        chop = signals["choppiness"][i]
        if np.isnan(chop):
            return True
        if chop >= chop_max:
            return False
        dr2 = signals["dr2"][i]
        if np.isnan(dr2):
            return True
        if dr2 > dr2_max:
            return False
        ds = signals["dslope"][i]
        if np.isnan(ds):
            return True
        if ds > dslope_max:
            return False
        return True
    return f


def make_fade_confirm_filter(chop_max, dr2_max, dslope_max, fc_max):
    """Track A + fade_confirm gate."""
    def f(signals, i, direction, step_dist):
        # Track A gates first
        chop = signals["choppiness"][i]
        if np.isnan(chop):
            return True
        if chop >= chop_max:
            return False
        dr2 = signals["dr2"][i]
        if np.isnan(dr2):
            return True
        if dr2 > dr2_max:
            return False
        ds = signals["dslope"][i]
        if np.isnan(ds):
            return True
        if ds > dslope_max:
            return False

        # Fade confirm gate
        prev_range = signals["prev_range"][i]
        if np.isnan(prev_range):
            return True
        entry_price = float(signals["last"][i])
        if direction == 1:  # LONG
            fc = (entry_price - float(signals["prev_low"][i])) / prev_range
        else:  # SHORT
            fc = (float(signals["prev_high"][i]) - entry_price) / prev_range
        return fc < fc_max
    return f


def make_combined_filter(chop_max, dr2_max, dslope_max, fc_max, fs_max):
    """Track A + fade_confirm + fade_speed gate."""
    def f(signals, i, direction, step_dist):
        # Track A gates
        chop = signals["choppiness"][i]
        if np.isnan(chop):
            return True
        if chop >= chop_max:
            return False
        dr2 = signals["dr2"][i]
        if np.isnan(dr2):
            return True
        if dr2 > dr2_max:
            return False
        ds = signals["dslope"][i]
        if np.isnan(ds):
            return True
        if ds > dslope_max:
            return False

        # Fade confirm gate
        prev_range = signals["prev_range"][i]
        if np.isnan(prev_range):
            return True
        entry_price = float(signals["last"][i])
        if direction == 1:
            fc = (entry_price - float(signals["prev_low"][i])) / prev_range
        else:
            fc = (float(signals["prev_high"][i]) - entry_price) / prev_range
        if fc >= fc_max:
            return False

        # Fade speed gate
        prev_dur = signals["prev_dur"][i]
        if np.isnan(prev_dur):
            return True
        displacement = float(signals["prev_disp"][i])
        speed = displacement / prev_dur
        if direction == 1:
            fade_speed = speed
        else:
            fade_speed = -speed
        return fade_speed < fs_max
    return f


# ---------------------------------------------------------------------------
#  Metrics
# ---------------------------------------------------------------------------

def compute_metrics(cycles):
    if not cycles:
        return {"n": 0, "wr": 0.0, "sr": 0.0, "er": 0.0, "pnl": 0.0}
    n = len(cycles)
    stops = sum(1 for c in cycles if c["exit_type"] == "HARD_STOP")
    net_pnls = []
    for c in cycles:
        comm = COMMISSION_PER_RT_MINI * max(c.get("max_position", 1), 1)
        net_pnls.append(c["pnl_ticks"] * 5.0 - comm)
    wins = sum(1 for p in net_pnls if p >= 0)
    return {"n": n, "wr": wins / n, "sr": stops / n,
            "er": sum(net_pnls) / n, "pnl": sum(net_pnls)}


def weekly_breakdown(cycles):
    weeks = defaultdict(list)
    for c in cycles:
        dt = c["seed_dt"][:10]
        d = datetime.date(int(dt[:4]), int(dt[5:7]), int(dt[8:10]))
        wk = f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"
        weeks[wk].append(c)
    return dict(sorted(weeks.items()))


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Track B Step 5 — Live sim")
    parser.add_argument("--bar-file", type=str,
                        default=r"C:\Projects\futures_pipeline\data\NQ-1tick-calibration.csv")
    args = parser.parse_args()

    print(f"Loading bars from {args.bar_file}...")
    t0 = time.time()
    bars = load_bars_extended(args.bar_file)
    print(f"Loaded {bars['n']} bars in {time.time()-t0:.1f}s")

    print(f"\nPrecomputing signals (lb={LB}, bar_size={BAR_SIZE})...")
    t1 = time.time()
    signals = precompute_signals(bars, BAR_SIZE, LB)
    print(f"  done ({time.time()-t1:.1f}s)")

    # ===================================================================
    # Track A baseline (live sim)
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"TRACK A BASELINE (chop + dR2 + dSlope)")
    print(f"{'='*70}")

    t1 = time.time()
    bl_cycles = run_sim_filtered(
        bars, step_dist=SD, hard_stop=HS,
        max_fades=MAX_FADES, max_levels=MAX_LEVELS,
        max_contract_size=MAX_CONTRACT_SIZE,
        signal_arrays=signals,
        filter_fn=make_track_a_filter(CHOP_THRESHOLD, DR2_MAX, DSLOPE_MAX),
    )
    bl_m = compute_metrics(bl_cycles)
    bl_weeks = weekly_breakdown(bl_cycles)
    print(f"  {bl_m['n']} cyc | {bl_m['wr']:.0%} WR | {bl_m['sr']:.0%} SR | "
          f"E[R]=${bl_m['er']:.2f} | PnL=${bl_m['pnl']:,.0f} ({time.time()-t1:.0f}s)")

    # Filter to test weeks
    bl_test = {wk: compute_metrics(bl_weeks.get(wk, []))
               for wk in sorted(TEST_WEEKS.keys())}

    # ===================================================================
    # Fade confirm variants
    # ===================================================================
    fc_thresholds = [0.4, 0.5, 0.6, 0.7]

    results = []  # (label, full_metrics, week_metrics)

    for fc_t in fc_thresholds:
        label = f"fc<{fc_t}"
        print(f"\n  Running {label}...")
        t1 = time.time()
        cyc = run_sim_filtered(
            bars, step_dist=SD, hard_stop=HS,
            max_fades=MAX_FADES, max_levels=MAX_LEVELS,
            max_contract_size=MAX_CONTRACT_SIZE,
            signal_arrays=signals,
            filter_fn=make_fade_confirm_filter(CHOP_THRESHOLD, DR2_MAX, DSLOPE_MAX, fc_t),
        )
        m = compute_metrics(cyc)
        wk_data = weekly_breakdown(cyc)
        wk_m = {wk: compute_metrics(wk_data.get(wk, [])) for wk in sorted(TEST_WEEKS.keys())}
        results.append((label, m, wk_m))
        print(f"    {m['n']} cyc | {m['wr']:.0%} WR | {m['sr']:.0%} SR | "
              f"E[R]=${m['er']:.2f} ({time.time()-t1:.0f}s)")

    # ===================================================================
    # Combined: fade_confirm + fade_speed
    # ===================================================================
    combos = [(0.7, 0.3), (0.7, 0.5), (0.5, 0.5)]

    for fc_t, fs_t in combos:
        label = f"fc<{fc_t}+fs<{fs_t}"
        print(f"\n  Running {label}...")
        t1 = time.time()
        cyc = run_sim_filtered(
            bars, step_dist=SD, hard_stop=HS,
            max_fades=MAX_FADES, max_levels=MAX_LEVELS,
            max_contract_size=MAX_CONTRACT_SIZE,
            signal_arrays=signals,
            filter_fn=make_combined_filter(CHOP_THRESHOLD, DR2_MAX, DSLOPE_MAX, fc_t, fs_t),
        )
        m = compute_metrics(cyc)
        wk_data = weekly_breakdown(cyc)
        wk_m = {wk: compute_metrics(wk_data.get(wk, [])) for wk in sorted(TEST_WEEKS.keys())}
        results.append((label, m, wk_m))
        print(f"    {m['n']} cyc | {m['wr']:.0%} WR | {m['sr']:.0%} SR | "
              f"E[R]=${m['er']:.2f} ({time.time()-t1:.0f}s)")

    # ===================================================================
    # Summary table
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"SUMMARY: Full P1")
    print(f"{'='*70}")

    print(f"\n{'Filter':<20} {'N':>6} {'Ret':>5} {'WR':>6} {'SR':>6} {'E[R]':>8} {'PnL':>10}")
    print(f"{'-'*65}")
    print(f"{'Track A (baseline)':<20} {bl_m['n']:>6} {'100%':>5} {bl_m['wr']:>5.0%} "
          f"{bl_m['sr']:>5.0%} ${bl_m['er']:>7.2f} ${bl_m['pnl']:>9,.0f}")
    for label, m, _ in results:
        ret = m["n"] / bl_m["n"] if bl_m["n"] > 0 else 0
        print(f"{label:<20} {m['n']:>6} {ret:>4.0%} {m['wr']:>5.0%} "
              f"{m['sr']:>5.0%} ${m['er']:>7.2f} ${m['pnl']:>9,.0f}")

    # ===================================================================
    # Per-week breakdown for test weeks
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"PER-WEEK BREAKDOWN (5 test weeks)")
    print(f"{'='*70}")

    for label, _, wk_m in results:
        print(f"\n  {label}:")
        print(f"    {'Week':<10} {'Cat':<10} {'N':>5} {'BL_ER':>8} {'Filt_ER':>8} "
              f"{'dER':>8} {'Ret':>5}")
        all_improved = True
        for wk in sorted(TEST_WEEKS.keys()):
            cat = TEST_WEEKS[wk]
            bm = bl_test[wk]
            fm = wk_m.get(wk, {"n": 0, "er": 0.0})
            d = fm["er"] - bm["er"] if fm["n"] > 0 else 0
            ret = fm["n"] / bm["n"] if bm["n"] > 0 else 0
            if d < 0:
                all_improved = False
            print(f"    {wk:<10} {cat:<10} {fm['n']:>5} ${bm['er']:>7.2f} "
                  f"${fm['er']:>7.2f} ${d:>7.2f} {ret:>4.0%}")
        print(f"    All improved: {all_improved}")

    # ===================================================================
    # Save
    # ===================================================================
    out_csv = OUTPUT_DIR / "fade-confirm-livesim.csv"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(out_csv, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["filter", "n", "retention", "wr", "sr", "er", "pnl"])
        w.writerow(["Track_A", bl_m["n"], "1.0", f"{bl_m['wr']:.4f}",
                     f"{bl_m['sr']:.4f}", f"{bl_m['er']:.2f}", f"{bl_m['pnl']:.2f}"])
        for label, m, _ in results:
            ret = m["n"] / bl_m["n"]
            w.writerow([label, m["n"], f"{ret:.4f}", f"{m['wr']:.4f}",
                        f"{m['sr']:.4f}", f"{m['er']:.2f}", f"{m['pnl']:.2f}"])
    print(f"\nSaved: {out_csv}")

    total = time.time() - t0
    print(f"\nTotal runtime: {total:.0f}s ({total/60:.1f}m)")


if __name__ == "__main__":
    main()
