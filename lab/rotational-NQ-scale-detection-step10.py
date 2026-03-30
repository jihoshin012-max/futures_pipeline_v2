# archetype: rotational
"""
rotational-NQ-scale-detection-step10.py — Entry signals Steps 0+1.

Step 0: Verify on_bar_in_trade callback produces identical cycle output.
Step 1: Run chop-filtered sweep on 5 test weeks, tag each cycle with
        6 regime features at entry bar, export per-cycle and per-bar CSVs.

Prompt: rotational-NQ-prompt-entry-signals.md
Baseline: SD=10 HS=60 depth_1 MCS=2, chop<0.10 lb=3, 250-tick bars.

Usage:
    python rotational-NQ-scale-detection-step10.py [--bar-file PATH]
"""
from __future__ import annotations

import csv
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

# 5 test weeks from prompt (YYYYMMDD ranges, Monday-Friday)
TEST_WEEKS = [
    ("W43", 20251020, 20251024, "WEAKEST"),
    ("W39", 20250922, 20250926, "LOW"),
    ("W50", 20251208, 20251212, "MID"),
    ("W46", 20251110, 20251114, "GOOD"),
    ("W47", 20251117, 20251121, "BEST"),
]

OUTPUT_DIR = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")

# Entry signal feature names (Track A: first 4, Track B uses all 6)
ENTRY_FEATURES = [
    "signed_chop", "dchop", "d2chop", "signed_slope", "dr2", "dslope",
]


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------

def precompute_signals(bars, bar_size, lookback):
    """Precompute chop filter signals + entry regime features."""
    agg_bars, tick_to_agg = aggregate_to_ntick(bars, bar_size)

    # Chop filter signals (existing — for gating)
    regime = compute_regime_signals(agg_bars, lookback=lookback)
    chop_ticks = map_signal_to_ticks(regime["choppiness"], tick_to_agg)
    slope_ticks = map_signal_to_ticks(regime["slope"], tick_to_agg)

    # Entry signals (new)
    entry = compute_entry_signals(agg_bars, lookback=lookback)
    entry_ticks = {}
    for feat in ENTRY_FEATURES:
        entry_ticks[feat] = map_signal_to_ticks(entry[feat], tick_to_agg)

    return {
        "choppiness": chop_ticks,
        "slope": slope_ticks,
        **entry_ticks,
        "tick_to_agg": tick_to_agg,
        "warmup_ticks": {"dominant_scale": 0, "asymmetry": 0},
    }


def make_chop_filter(chop_max):
    def filter_fn(signals, i, direction, step_dist):
        chop = signals["choppiness"][i]
        if np.isnan(chop):
            return True
        return chop < chop_max
    return filter_fn


def slice_bars(bars, date_start, date_end):
    """Return bar dict sliced to date_start..date_end (inclusive, YYYYMMDD ints)."""
    dint = bars["date_int"]
    mask = (dint >= date_start) & (dint <= date_end)
    idx = np.where(mask)[0]
    if len(idx) == 0:
        return None
    s, e = idx[0], idx[-1] + 1
    sliced = {}
    for k, v in bars.items():
        if k == "n":
            sliced["n"] = e - s
        elif isinstance(v, np.ndarray):
            sliced[k] = v[s:e]
        else:
            sliced[k] = v[s:e] if hasattr(v, '__getitem__') else v
    return sliced


def compute_metrics(cycles):
    if not cycles:
        return {"cycles": 0, "wins": 0, "wr": 0, "stops": 0, "sr": 0,
                "pnl": 0.0, "er": 0.0}
    n = len(cycles)
    stops = sum(1 for c in cycles if c["exit_type"] == "HARD_STOP")
    net_pnls = []
    for c in cycles:
        comm = COMMISSION_PER_RT_MINI * max(c.get("max_position", 1), 1)
        net_pnls.append(c["pnl_ticks"] * 5.0 - comm)
    wins = sum(1 for p in net_pnls if p >= 0)
    total_pnl = sum(net_pnls)
    return {
        "cycles": n, "wins": wins, "wr": wins / n if n else 0,
        "stops": stops, "sr": stops / n if n else 0,
        "pnl": total_pnl, "er": total_pnl / n if n else 0,
    }


# ---------------------------------------------------------------------------
#  Step 0: Verify on_bar_in_trade callback is inert
# ---------------------------------------------------------------------------

def step0_verify(bars):
    """Run sim with callback=None and callback=recording_fn, compare cycles."""
    print("=" * 60)
    print("STEP 0: Verify on_bar_in_trade callback inertness")
    print("=" * 60)

    signals = precompute_signals(bars, BAR_SIZE, LB)
    filter_fn = make_chop_filter(CHOP_THRESHOLD)

    # Run A: no callback (original behavior)
    cycles_a = run_sim_filtered(
        bars, step_dist=SD, hard_stop=HS,
        max_fades=MAX_FADES, max_levels=MAX_LEVELS,
        max_contract_size=MAX_CONTRACT_SIZE,
        signal_arrays=signals, filter_fn=filter_fn,
    )

    # Run B: with recording callback
    intrade_records = []

    def recording_fn(bar_idx, cycle_id, bar_offset, price, direction,
                     pnl_ticks, mfe_ticks, mae_ticks):
        intrade_records.append((bar_idx, cycle_id, bar_offset, price,
                                direction, pnl_ticks, mfe_ticks, mae_ticks))

    cycles_b = run_sim_filtered(
        bars, step_dist=SD, hard_stop=HS,
        max_fades=MAX_FADES, max_levels=MAX_LEVELS,
        max_contract_size=MAX_CONTRACT_SIZE,
        signal_arrays=signals, filter_fn=filter_fn,
        on_bar_in_trade=recording_fn,
    )

    # Compare cycle-level output
    assert len(cycles_a) == len(cycles_b), (
        f"Cycle count mismatch: {len(cycles_a)} vs {len(cycles_b)}")

    mismatches = 0
    compare_keys = ["cycle_id", "seed_dt", "exit_dt", "direction",
                    "seed_price", "avg_entry_price", "exit_price",
                    "exit_type", "depth", "max_position",
                    "pnl_ticks", "pnl_dollars", "bars_held",
                    "mfe_ticks", "mae_ticks"]
    for i, (ca, cb) in enumerate(zip(cycles_a, cycles_b)):
        for k in compare_keys:
            va, vb = ca[k], cb[k]
            if isinstance(va, float):
                if abs(va - vb) > 1e-9:
                    print(f"  MISMATCH cycle {i} key={k}: {va} vs {vb}")
                    mismatches += 1
            elif va != vb:
                print(f"  MISMATCH cycle {i} key={k}: {va} vs {vb}")
                mismatches += 1

    if mismatches == 0:
        print(f"  PASS: {len(cycles_a)} cycles identical. "
              f"Callback recorded {len(intrade_records)} in-trade bar snapshots.")
    else:
        print(f"  FAIL: {mismatches} mismatches across {len(cycles_a)} cycles")
        raise AssertionError(f"Step 0 verification failed: {mismatches} mismatches")

    return True


# ---------------------------------------------------------------------------
#  Step 1: Tag cycles with entry features, export CSVs
# ---------------------------------------------------------------------------

def step1_tag_and_export(bars):
    """Run chop-filtered sweep on 5 test weeks, tag cycles with entry features."""
    print("\n" + "=" * 60)
    print("STEP 1: Compute and tag — 5 test weeks")
    print("=" * 60)

    # Precompute signals on FULL dataset (needed for correct warmup)
    print(f"\nPrecomputing signals (lb={LB}, bar_size={BAR_SIZE})...")
    t0 = time.time()
    signals = precompute_signals(bars, BAR_SIZE, LB)
    print(f"  done ({time.time()-t0:.1f}s)")

    filter_fn = make_chop_filter(CHOP_THRESHOLD)

    all_tagged_cycles = []
    all_intrade_bars = []
    week_summaries = []

    for wk_name, d_start, d_end, category in TEST_WEEKS:
        print(f"\n--- {wk_name} ({category}): {d_start}-{d_end} ---")

        # Run filtered sim on full data (filter uses signals which need full warmup)
        # Then filter cycles to this week by seed_dt
        intrade_records = []

        def recording_fn(bar_idx, cycle_id, bar_offset, price, direction,
                         pnl_ticks, mfe_ticks, mae_ticks):
            intrade_records.append({
                "bar_idx": bar_idx,
                "cycle_id": cycle_id,
                "bar_offset": bar_offset,
                "price": price,
                "direction": direction,
                "pnl_ticks": pnl_ticks,
                "mfe_ticks": mfe_ticks,
                "mae_ticks": mae_ticks,
            })

        cycles = run_sim_filtered(
            bars, step_dist=SD, hard_stop=HS,
            max_fades=MAX_FADES, max_levels=MAX_LEVELS,
            max_contract_size=MAX_CONTRACT_SIZE,
            signal_arrays=signals, filter_fn=filter_fn,
            on_bar_in_trade=recording_fn,
        )

        # Filter cycles to this week
        week_cycles = []
        for c in cycles:
            dt = c["seed_dt"][:10].replace("-", "")
            d = int(dt)
            if d_start <= d <= d_end:
                week_cycles.append(c)

        # Filter in-trade bars to cycles in this week
        week_cycle_ids = {c["cycle_id"] for c in week_cycles}
        week_intrade = [r for r in intrade_records
                        if r["cycle_id"] in week_cycle_ids]

        # Tag each cycle with entry features at seed_bar
        for c in week_cycles:
            seed_bar = c["seed_bar"]
            c["week"] = wk_name
            c["category"] = category
            for feat in ENTRY_FEATURES:
                val = signals[feat][seed_bar]
                c[f"entry_{feat}"] = float(val) if not np.isnan(val) else None

        # Tag in-trade bars with regime features
        for r in week_intrade:
            bi = r["bar_idx"]
            r["week"] = wk_name
            for feat in ENTRY_FEATURES:
                val = signals[feat][bi]
                r[feat] = float(val) if not np.isnan(val) else None

        m = compute_metrics(week_cycles)
        print(f"  {m['cycles']} cyc | {m['wr']:.0%} WR | {m['sr']:.0%} SR | "
              f"${m['pnl']:,.0f} | E[R]=${m['er']:.2f} | "
              f"{len(week_intrade)} in-trade bars")

        week_summaries.append({"week": wk_name, "category": category, **m})
        all_tagged_cycles.extend(week_cycles)
        all_intrade_bars.extend(week_intrade)

    # --- Export per-cycle CSV ---
    cycle_csv = OUTPUT_DIR / "regime-direction-tagged-cycles.csv"
    cycle_fields = [
        "cycle_id", "week", "category", "seed_dt", "exit_dt", "direction",
        "seed_price", "avg_entry_price", "exit_price", "exit_type",
        "depth", "max_position", "pnl_ticks", "pnl_dollars",
        "bars_held", "mfe_ticks", "mae_ticks",
    ] + [f"entry_{f}" for f in ENTRY_FEATURES]

    with open(cycle_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cycle_fields, extrasaction="ignore")
        w.writeheader()
        for c in all_tagged_cycles:
            row = {k: c.get(k, "") for k in cycle_fields}
            # Format floats
            for k in ["seed_price", "avg_entry_price", "exit_price",
                       "pnl_ticks", "pnl_dollars", "mfe_ticks", "mae_ticks"]:
                if k in row and isinstance(row[k], float):
                    row[k] = f"{row[k]:.4f}"
            for f in ENTRY_FEATURES:
                fk = f"entry_{f}"
                if row[fk] is not None and isinstance(row[fk], float):
                    row[fk] = f"{row[fk]:.6f}"
            w.writerow(row)
    print(f"\nSaved: {cycle_csv} ({len(all_tagged_cycles)} cycles)")

    # --- Export per-bar in-trade CSV ---
    bar_csv = OUTPUT_DIR / "regime-direction-intrade-bars.csv"
    bar_fields = [
        "cycle_id", "week", "bar_idx", "bar_offset", "price", "direction",
        "pnl_ticks", "mfe_ticks", "mae_ticks",
    ] + ENTRY_FEATURES

    with open(bar_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=bar_fields, extrasaction="ignore")
        w.writeheader()
        for r in all_intrade_bars:
            row = {k: r.get(k, "") for k in bar_fields}
            for k in ["price", "pnl_ticks", "mfe_ticks", "mae_ticks"]:
                if k in row and isinstance(row[k], float):
                    row[k] = f"{row[k]:.4f}"
            for f in ENTRY_FEATURES:
                if row[f] is not None and isinstance(row[f], float):
                    row[f] = f"{row[f]:.6f}"
            w.writerow(row)
    print(f"Saved: {bar_csv} ({len(all_intrade_bars)} bars)")

    # --- Summary ---
    print(f"\n{'='*60}")
    print(f"STEP 1 SUMMARY — 5 Test Weeks")
    print(f"{'='*60}")
    print(f"{'Week':<6} {'Cat':<8} {'Cyc':>5} {'WR':>6} {'SR':>6} "
          f"{'PnL':>10} {'E[R]':>8}")
    print("-" * 55)
    for s in week_summaries:
        print(f"{s['week']:<6} {s['category']:<8} {s['cycles']:>5} "
              f"{s['wr']:>5.0%} {s['sr']:>5.0%} "
              f"${s['pnl']:>9,.0f} ${s['er']:>7.2f}")
    pooled = compute_metrics(all_tagged_cycles)
    print("-" * 55)
    print(f"{'POOL':<6} {'ALL':<8} {pooled['cycles']:>5} "
          f"{pooled['wr']:>5.0%} {pooled['sr']:>5.0%} "
          f"${pooled['pnl']:>9,.0f} ${pooled['er']:>7.2f}")

    return all_tagged_cycles, all_intrade_bars


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------
def main():
    import argparse
    parser = argparse.ArgumentParser(
        description="Entry Signals — Steps 0+1: Instrument & Tag")
    parser.add_argument("--bar-file", type=str,
                        default=r"C:\Projects\futures_pipeline\data\NQ-1tick-calibration.csv")
    parser.add_argument("--skip-verify", action="store_true",
                        help="Skip Step 0 verification (for re-runs)")
    args = parser.parse_args()

    print(f"Loading bars from {args.bar_file}...")
    t0 = time.time()
    bars = load_bars_extended(args.bar_file)
    print(f"Loaded {bars['n']} bars in {time.time()-t0:.1f}s")

    if not args.skip_verify:
        step0_verify(bars)

    step1_tag_and_export(bars)

    total = time.time() - t0
    print(f"\nTotal runtime: {total:.0f}s ({total/60:.1f}m)")


if __name__ == "__main__":
    main()
