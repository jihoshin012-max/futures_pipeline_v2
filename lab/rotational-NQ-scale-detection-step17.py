# archetype: rotational
"""
rotational-NQ-scale-detection-step17.py — Track B Step 1: Compute and tag
fade confirmation features.

Runs Track A's winning config on 5 test weeks. At each entry bar, computes
5 fade confirmation features from PREVIOUS COMPLETED agg bars. Tags each
cycle with: fade_confirm, avg_range_decay, flow_confirm, direction_bars,
fade_speed.

Prompt: rotational-NQ-prompt-fade-confirmation.md
Depends on: Step 0 (step16.py) — test week selection.
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
#  Config — Track A's winning configuration
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

# Test weeks from Step 0
TEST_WEEKS = {
    "2025-W40": "WEAKEST",
    "2025-W49": "LOW",
    "2025-W44": "MID",
    "2025-W46": "GOOD",
    "2025-W47": "BEST",
}

OUTPUT_DIR = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")


# ---------------------------------------------------------------------------
#  Signal precomputation (same as step16)
# ---------------------------------------------------------------------------

def precompute_signals(bars, bar_size, lookback):
    agg_bars, tick_to_agg = aggregate_to_ntick(bars, bar_size)
    regime = compute_regime_signals(agg_bars, lookback=lookback)
    entry = compute_entry_signals(agg_bars, lookback=lookback)
    return {
        "choppiness": map_signal_to_ticks(regime["choppiness"], tick_to_agg),
        "slope": map_signal_to_ticks(regime["slope"], tick_to_agg),
        "dr2": map_signal_to_ticks(entry["dr2"], tick_to_agg),
        "dslope": map_signal_to_ticks(entry["dslope"], tick_to_agg),
        "tick_to_agg": tick_to_agg,
        "warmup_ticks": {"dominant_scale": 0, "asymmetry": 0},
        # Agg bar arrays for fade confirmation features
        "agg_bars": agg_bars,
    }


# ---------------------------------------------------------------------------
#  Track A entry filter (same as step16)
# ---------------------------------------------------------------------------

def make_entry_filter(chop_max, dr2_max, dslope_max):
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


# ---------------------------------------------------------------------------
#  Fade confirmation feature computation
# ---------------------------------------------------------------------------

def compute_fade_features(cycles, signals):
    """Compute 5 fade confirmation features for each cycle at entry time.

    All features use COMPLETED bars PRIOR to the entry bar.
    The entry bar (tick_to_agg[seed_bar]) is incomplete at entry time.
    """
    agg = signals["agg_bars"]
    tick_to_agg = signals["tick_to_agg"]

    a_open = agg["open"]
    a_high = agg["high"]
    a_low = agg["low"]
    a_close = agg["last"]
    a_tsec = agg["time_sec"]
    a_dint = agg["date_int"]
    a_bid_vol = agg["bid_vol"]
    a_ask_vol = agg["ask_vol"]
    n_agg = agg["n"]

    for c in cycles:
        seed_bar = c["seed_bar"]
        entry_price = c["seed_price"]
        direction = 1 if c["direction"] == "LONG" else -1

        # Which agg bar does this tick belong to?
        agg_idx = int(tick_to_agg[seed_bar])

        # Previous COMPLETED agg bar = agg_idx - 1
        # (agg_idx is the current bar being formed, not yet complete)
        prev = agg_idx - 1

        # Need at least 3 completed bars before entry for all features
        if prev < 3:
            c["fade_confirm"] = None
            c["range_decay_1"] = None
            c["avg_range_decay"] = None
            c["flow_confirm"] = None
            c["direction_bars"] = None
            c["fade_speed"] = None
            continue

        # ---------------------------------------------------------------
        # 1. Close position (fade_confirm)
        # ---------------------------------------------------------------
        prev_high = float(a_high[prev])
        prev_low = float(a_low[prev])
        prev_range = prev_high - prev_low

        if prev_range == 0.0:
            fc = 0.5  # zero-range bar guard
        elif direction == 1:  # LONG
            fc = (entry_price - prev_low) / prev_range
        else:  # SHORT
            fc = (prev_high - entry_price) / prev_range
        c["fade_confirm"] = round(fc, 4)

        # ---------------------------------------------------------------
        # 2. Range decay
        # ---------------------------------------------------------------
        # Single bar: range[prev] / range[prev-1]
        r_prev = float(a_high[prev]) - float(a_low[prev])
        r_prev2 = float(a_high[prev - 1]) - float(a_low[prev - 1])
        if r_prev2 > 0:
            c["range_decay_1"] = round(r_prev / r_prev2, 4)
        else:
            c["range_decay_1"] = None

        # Multi-bar average: mean of range[k]/range[k-1] for k in [prev-1, prev]
        rd_sum = 0.0
        rd_count = 0
        for k in range(prev - 1, prev + 1):  # k = prev-1, prev
            if k >= 1:
                rk = float(a_high[k]) - float(a_low[k])
                rk_prev = float(a_high[k - 1]) - float(a_low[k - 1])
                if rk_prev > 0:
                    rd_sum += rk / rk_prev
                    rd_count += 1
        c["avg_range_decay"] = round(rd_sum / rd_count, 4) if rd_count > 0 else None

        # ---------------------------------------------------------------
        # 3. Volume shift (flow_confirm)
        # ---------------------------------------------------------------
        ask_v = float(a_ask_vol[prev])
        bid_v = float(a_bid_vol[prev])
        total_v = ask_v + bid_v
        if total_v > 0:
            delta = (ask_v - bid_v) / total_v
            # Direction-relative
            if direction == 1:
                c["flow_confirm"] = round(delta, 4)
            else:
                c["flow_confirm"] = round(-delta, 4)
        else:
            c["flow_confirm"] = None

        # ---------------------------------------------------------------
        # 4. Consecutive bar direction (direction_bars)
        # ---------------------------------------------------------------
        count = 0
        for k in range(prev - 2, prev + 1):  # bars [prev-2, prev-1, prev]
            if k >= 1:
                if direction == 1:
                    if float(a_close[k]) > float(a_close[k - 1]):
                        count += 1
                else:
                    if float(a_close[k]) < float(a_close[k - 1]):
                        count += 1
        c["direction_bars"] = count

        # ---------------------------------------------------------------
        # 5. Directional speed (fade_speed)
        # ---------------------------------------------------------------
        # Bar duration: time from start of prev bar to start of next bar
        # Use time_sec difference. Handle session boundary.
        if int(a_dint[prev]) == int(a_dint[prev + 1]) if prev + 1 < n_agg else False:
            bar_dur = float(a_tsec[prev + 1]) - float(a_tsec[prev])
        elif prev >= 1 and int(a_dint[prev]) == int(a_dint[prev - 1]):
            # Fallback: use duration of previous bar gap as proxy
            bar_dur = float(a_tsec[prev]) - float(a_tsec[prev - 1])
        else:
            bar_dur = 0.0

        displacement = float(a_close[prev]) - float(a_open[prev])

        if bar_dur > 0:
            speed = displacement / bar_dur  # pts per second, signed
            # Direction-relative
            if direction == 1:
                c["fade_speed"] = round(speed, 6)
            else:
                c["fade_speed"] = round(-speed, 6)
        else:
            c["fade_speed"] = None

    return cycles


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
    parser = argparse.ArgumentParser(description="Track B Step 1 — Compute and tag fade features")
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

    # =======================================================================
    # Run Track A filtered sim on full P1
    # =======================================================================
    print(f"\nRunning Track A filter on full P1...")
    t1 = time.time()
    all_cycles = run_sim_filtered(
        bars, step_dist=SD, hard_stop=HS,
        max_fades=MAX_FADES, max_levels=MAX_LEVELS,
        max_contract_size=MAX_CONTRACT_SIZE,
        signal_arrays=signals,
        filter_fn=make_entry_filter(CHOP_THRESHOLD, DR2_MAX, DSLOPE_MAX),
    )
    m = compute_metrics(all_cycles)
    print(f"  {m['n']} cycles | {m['wr']:.0%} WR | {m['sr']:.0%} SR | "
          f"E[R]=${m['er']:.2f} ({time.time()-t1:.0f}s)")

    # =======================================================================
    # Filter to 5 test weeks only
    # =======================================================================
    week_cycles = weekly_breakdown(all_cycles)
    test_cycles = []
    for wk, cat in sorted(TEST_WEEKS.items()):
        wk_cyc = week_cycles.get(wk, [])
        print(f"  {wk} ({cat}): {len(wk_cyc)} cycles")
        for c in wk_cyc:
            c["week"] = wk
            c["week_cat"] = cat
        test_cycles.extend(wk_cyc)

    print(f"\nTotal test cycles: {len(test_cycles)}")

    # =======================================================================
    # Compute fade confirmation features
    # =======================================================================
    print(f"\nComputing fade confirmation features...")
    t1 = time.time()
    test_cycles = compute_fade_features(test_cycles, signals)
    print(f"  done ({time.time()-t1:.1f}s)")

    # Summary stats per feature
    features = ["fade_confirm", "range_decay_1", "avg_range_decay",
                 "flow_confirm", "direction_bars", "fade_speed"]
    print(f"\nFeature summary (non-null values):")
    for feat in features:
        vals = [c[feat] for c in test_cycles if c[feat] is not None]
        if vals:
            arr = np.array(vals)
            print(f"  {feat:20s}: N={len(vals):>5}  "
                  f"min={np.min(arr):>8.4f}  P25={np.percentile(arr,25):>8.4f}  "
                  f"med={np.median(arr):>8.4f}  P75={np.percentile(arr,75):>8.4f}  "
                  f"max={np.max(arr):>8.4f}")
        else:
            print(f"  {feat:20s}: no data")

    # =======================================================================
    # Save tagged cycles
    # =======================================================================
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_csv = OUTPUT_DIR / "fade-confirm-tagged-cycles.csv"

    fieldnames = [
        "week", "week_cat", "cycle_id", "seed_dt", "exit_dt",
        "direction", "seed_price", "exit_price", "exit_type",
        "depth", "max_position", "pnl_ticks", "pnl_dollars",
        "bars_held", "mfe_ticks", "mae_ticks",
        "fade_confirm", "range_decay_1", "avg_range_decay",
        "flow_confirm", "direction_bars", "fade_speed",
    ]

    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for c in test_cycles:
            row = {}
            for k in fieldnames:
                v = c.get(k)
                if v is None:
                    row[k] = ""
                elif isinstance(v, float):
                    row[k] = f"{v:.6f}" if k == "fade_speed" else f"{v:.4f}"
                else:
                    row[k] = v
            w.writerow(row)

    print(f"\nSaved: {out_csv}")
    print(f"Total runtime: {time.time()-t0:.0f}s ({(time.time()-t0)/60:.1f}m)")


if __name__ == "__main__":
    main()
