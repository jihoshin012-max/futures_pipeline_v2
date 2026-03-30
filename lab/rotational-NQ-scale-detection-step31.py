# archetype: rotational
"""
rotational-NQ-scale-detection-step31.py -- Track C2 Step 4: Mechanical rules replay.

Two rules:
1. Partial profit (depth_1 only): at N pts in favor, close 1 of 2 contracts,
   arm break-even stop on remainder. Sweep N = 3, 4, 5, 6, 7 pts.
2. Adaptive stop: scale HS based on fade_confirm at entry.
   adaptive_hs = clamp(base_hs - (fc / 0.40) * (base_hs - min_hs), min_hs, base_hs)

Replay on 5 test weeks. Per-week net benefit. Must be positive on ALL weeks.

Prompt: rotational-NQ-prompt-loss-mitigation-c2.md Step 4
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
FC_MAX = 0.40

PARTIAL_N_PTS = [3, 4, 5, 6, 7]  # pts in favor for partial close
ADAPTIVE_BASE_HS = 60.0
ADAPTIVE_MIN_HS = 40.0

TEST_WEEKS = {
    "2025-W40": "WEAKEST",
    "2025-W41": "MID",
    "2025-W46": "GOOD",
    "2025-W47": "BEST",
    "2025-W48": "LOW",
}

OUTPUT_DIR = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")


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
        prev = ai - 1
        prev_high[ai] = float(a_high[prev])
        prev_low[ai] = float(a_low[prev])
        rng = float(a_high[prev]) - float(a_low[prev])
        prev_range[ai] = rng if rng > 0 else np.nan

    prev_high_tick = prev_high[tick_to_agg]
    prev_low_tick = prev_low[tick_to_agg]
    prev_range_tick = prev_range[tick_to_agg]

    return {
        "choppiness": map_signal_to_ticks(regime["choppiness"], tick_to_agg),
        "slope": map_signal_to_ticks(regime["slope"], tick_to_agg),
        "dr2": map_signal_to_ticks(entry["dr2"], tick_to_agg),
        "dslope": map_signal_to_ticks(entry["dslope"], tick_to_agg),
        "tick_to_agg": tick_to_agg,
        "warmup_ticks": {"dominant_scale": 0, "asymmetry": 0},
        "prev_high": prev_high_tick,
        "prev_low": prev_low_tick,
        "prev_range": prev_range_tick,
        "last": last,
    }


# ---------------------------------------------------------------------------
#  Filter
# ---------------------------------------------------------------------------

def make_ab_filter(chop_max, dr2_max, dslope_max, fc_max):
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
        prev_range = signals["prev_range"][i]
        if np.isnan(prev_range):
            return True
        entry_price = float(signals["last"][i])
        if direction == 1:
            fc = (entry_price - float(signals["prev_low"][i])) / prev_range
        else:
            fc = (float(signals["prev_high"][i]) - entry_price) / prev_range
        return fc < fc_max
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


def get_week(c):
    dt = c["seed_dt"][:10]
    d = datetime.date(int(dt[:4]), int(dt[5:7]), int(dt[8:10]))
    return f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"


# ---------------------------------------------------------------------------
#  Compute fade_confirm for each cycle
# ---------------------------------------------------------------------------

def tag_fade_confirm(cycles, signals):
    """Add fade_confirm to each cycle dict."""
    for c in cycles:
        seed = c["seed_bar"]
        pr = signals["prev_range"][seed]
        if np.isnan(pr) or pr <= 0:
            c["fade_confirm"] = 0.5  # neutral (guard)
            continue
        entry_price = float(signals["last"][seed])
        if c["direction"] == "LONG":
            fc = (entry_price - float(signals["prev_low"][seed])) / pr
        else:
            fc = (float(signals["prev_high"][seed]) - entry_price) / pr
        c["fade_confirm"] = fc


# ---------------------------------------------------------------------------
#  Partial profit replay
# ---------------------------------------------------------------------------

def replay_partial_profit(cycles, n_pts):
    """Replay partial profit at N pts for depth_1 trades.

    Assumptions:
    - Only depth_1 trades (2 contracts) are eligible.
    - If MFE >= N_ticks from avg_entry, partial fires:
      - HARD_STOP: 1 contract at +N_ticks, 1 contract at break-even (0 ticks)
        New pnl_ticks = N_ticks (total for both contracts)
      - REVERSAL: 1 contract at +N_ticks, 1 contract at original reversal
        (conservative: remaining reaches reversal without break-even trigger)
        New pnl_ticks = N_ticks + original_pnl_ticks / 2
    - If MFE < N_ticks or depth_0: unchanged.
    """
    n_ticks = n_pts / TICK_SIZE
    results = []

    for c in cycles:
        comm = COMMISSION_PER_RT_MINI * max(c.get("max_position", 1), 1)
        orig_net = c["pnl_ticks"] * 5.0 - comm
        depth = c.get("depth", 0)
        mfe = c["mfe_ticks"]
        exit_type = c["exit_type"]

        if depth >= 1 and mfe >= n_ticks:
            # Partial fires
            if exit_type == "HARD_STOP":
                # 1 contract at +N_ticks, 1 at break-even (0)
                new_pnl_ticks = n_ticks
                new_net = new_pnl_ticks * 5.0 - comm
            elif exit_type == "REVERSAL":
                # 1 contract at +N_ticks, 1 at original per-contract reversal
                per_contract = c["pnl_ticks"] / 2.0
                new_pnl_ticks = n_ticks + per_contract
                new_net = new_pnl_ticks * 5.0 - comm
            else:
                # EOD_FLATTEN etc — leave unchanged
                new_net = orig_net

            results.append({
                "cycle_id": c["cycle_id"],
                "week": get_week(c),
                "exit_type": exit_type,
                "depth": depth,
                "mfe_ticks": mfe,
                "orig_net": orig_net,
                "new_net": new_net,
                "delta": new_net - orig_net,
                "fired": True,
            })
        else:
            results.append({
                "cycle_id": c["cycle_id"],
                "week": get_week(c),
                "exit_type": exit_type,
                "depth": depth,
                "mfe_ticks": mfe,
                "orig_net": orig_net,
                "new_net": orig_net,
                "delta": 0.0,
                "fired": False,
            })

    return results


# ---------------------------------------------------------------------------
#  Adaptive stop replay
# ---------------------------------------------------------------------------

def compute_adaptive_hs(fade_confirm, base_hs=60.0, min_hs=40.0):
    """Compute adaptive hard stop from fade_confirm."""
    raw = base_hs - (fade_confirm / 0.40) * (base_hs - min_hs)
    return max(min_hs, min(raw, base_hs))


def replay_adaptive_stop(cycles):
    """Replay adaptive stop based on fade_confirm.

    For each cycle:
    - Compute adaptive_hs from fade_confirm at entry
    - If MAE >= adaptive_hs and exit_type == HARD_STOP:
        Savings: earlier stop = smaller loss
        New pnl = -adaptive_hs * max_position (in ticks)
    - If MAE >= adaptive_hs and exit_type == REVERSAL:
        Cost: trade would have been stopped instead of winning
        New pnl = -adaptive_hs * max_position (in ticks)
    - If MAE < adaptive_hs: outcome unchanged
    """
    results = []

    for c in cycles:
        comm = COMMISSION_PER_RT_MINI * max(c.get("max_position", 1), 1)
        orig_net = c["pnl_ticks"] * 5.0 - comm
        fc = c.get("fade_confirm", 0.5)
        adaptive_hs = compute_adaptive_hs(fc)
        mae = c["mae_ticks"]
        exit_type = c["exit_type"]
        max_pos = c.get("max_position", 1)

        if mae >= adaptive_hs and adaptive_hs < HS:
            # Would have stopped at adaptive_hs
            new_pnl_ticks = -adaptive_hs * max_pos
            new_net = new_pnl_ticks * 5.0 - comm
            action = "STOPPED_EARLIER" if exit_type == "HARD_STOP" else "TURNED_TO_STOP"
        else:
            new_net = orig_net
            action = "UNCHANGED"

        results.append({
            "cycle_id": c["cycle_id"],
            "week": get_week(c),
            "exit_type": exit_type,
            "fade_confirm": fc,
            "adaptive_hs": adaptive_hs,
            "mae_ticks": mae,
            "max_position": max_pos,
            "orig_net": orig_net,
            "new_net": new_net,
            "delta": new_net - orig_net,
            "action": action,
        })

    return results


# ---------------------------------------------------------------------------
#  Aggregation helpers
# ---------------------------------------------------------------------------

def summarize_by_week(results, test_weeks):
    """Group results by week, compute net benefit."""
    weekly = defaultdict(lambda: {"saves": 0.0, "costs": 0.0, "net": 0.0,
                                   "n_fired": 0, "n_total": 0})
    for r in results:
        wk = r["week"]
        if wk not in test_weeks:
            continue
        w = weekly[wk]
        w["n_total"] += 1
        delta = r["delta"]
        if delta > 0:
            w["saves"] += delta
            w["n_fired"] += 1
        elif delta < 0:
            w["costs"] += abs(delta)
            w["n_fired"] += 1
        w["net"] += delta

    return {wk: dict(weekly[wk]) for wk in sorted(weekly.keys())}


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Track C2 Step 4 — Mechanical rules replay")
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

    ab_filter = make_ab_filter(CHOP_THRESHOLD, DR2_MAX, DSLOPE_MAX, FC_MAX)

    # Run sim
    print(f"\nRunning A+B sim on full P1...")
    t1 = time.time()
    all_cycles = run_sim_filtered(
        bars, step_dist=SD, hard_stop=HS,
        max_fades=MAX_FADES, max_levels=MAX_LEVELS,
        max_contract_size=MAX_CONTRACT_SIZE,
        signal_arrays=signals, filter_fn=ab_filter,
    )
    m = compute_metrics(all_cycles)
    print(f"  {m['n']} cyc | {m['wr']:.0%} WR | {m['sr']:.0%} SR | "
          f"E[R]=${m['er']:.2f} ({time.time()-t1:.0f}s)")

    # Filter to test weeks
    test_cycles = [c for c in all_cycles if get_week(c) in TEST_WEEKS]
    print(f"  Test weeks: {len(test_cycles)} cycles")

    # Tag fade_confirm
    tag_fade_confirm(test_cycles, signals)

    # ===================================================================
    # Depth composition
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"DEPTH COMPOSITION (test weeks)")
    print(f"{'='*70}")

    d0_count = sum(1 for c in test_cycles if c["depth"] == 0)
    d1_count = sum(1 for c in test_cycles if c["depth"] >= 1)
    d0_stops = sum(1 for c in test_cycles if c["depth"] == 0 and c["exit_type"] == "HARD_STOP")
    d1_stops = sum(1 for c in test_cycles if c["depth"] >= 1 and c["exit_type"] == "HARD_STOP")
    print(f"  depth_0: {d0_count} ({d0_count/len(test_cycles):.0%}) — {d0_stops} stops")
    print(f"  depth_1: {d1_count} ({d1_count/len(test_cycles):.0%}) — {d1_stops} stops")

    # MFE distribution for depth_1 trades
    d1_mfes = [c["mfe_ticks"] for c in test_cycles if c["depth"] >= 1]
    if d1_mfes:
        print(f"\n  depth_1 MFE distribution (ticks from avg_entry):")
        for pct in [10, 25, 50, 75, 90]:
            print(f"    P{pct}: {np.percentile(d1_mfes, pct):.1f} ticks "
                  f"({np.percentile(d1_mfes, pct) * TICK_SIZE:.2f} pts)")

    # MFE for depth_1 HARD_STOP specifically
    d1_stop_mfes = [c["mfe_ticks"] for c in test_cycles
                     if c["depth"] >= 1 and c["exit_type"] == "HARD_STOP"]
    if d1_stop_mfes:
        print(f"\n  depth_1 HARD_STOP MFE distribution:")
        for pct in [10, 25, 50, 75, 90]:
            print(f"    P{pct}: {np.percentile(d1_stop_mfes, pct):.1f} ticks "
                  f"({np.percentile(d1_stop_mfes, pct) * TICK_SIZE:.2f} pts)")

    # ===================================================================
    # Partial profit replay
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"PARTIAL PROFIT REPLAY (depth_1 only)")
    print(f"{'='*70}")
    print(f"\nAssumptions:")
    print(f"  - HARD_STOP with MFE >= N: 1 contract at +N_ticks, 1 at break-even")
    print(f"  - REVERSAL with MFE >= N: 1 contract at +N_ticks, 1 at original reversal")
    print(f"  - depth_0 or MFE < N: unchanged")

    for n_pts in PARTIAL_N_PTS:
        n_ticks = n_pts / TICK_SIZE
        results = replay_partial_profit(test_cycles, n_pts)
        weekly = summarize_by_week(results, TEST_WEEKS)

        fired = [r for r in results if r["fired"]]
        fired_stops = [r for r in fired if r["exit_type"] == "HARD_STOP"]
        fired_revs = [r for r in fired if r["exit_type"] == "REVERSAL"]

        total_delta = sum(r["delta"] for r in results if r["week"] in TEST_WEEKS)
        all_positive = all(w["net"] >= 0 for w in weekly.values())

        print(f"\n  --- N = {n_pts} pts ({n_ticks:.0f} ticks) ---")
        print(f"  Fired: {len(fired)}/{len(test_cycles)} "
              f"({len(fired_stops)} stops, {len(fired_revs)} reversals)")
        print(f"  Total delta: ${total_delta:,.0f}")
        print(f"  All weeks positive: {'YES' if all_positive else 'NO'}")

        print(f"  {'Week':<10} {'Cat':>8} {'Saves':>8} {'Costs':>8} {'Net':>8} {'Fired':>6}")
        print(f"  {'-'*52}")
        for wk in sorted(weekly.keys()):
            w = weekly[wk]
            cat = TEST_WEEKS.get(wk, "")
            print(f"  {wk:<10} {cat:>8} ${w['saves']:>7,.0f} ${w['costs']:>7,.0f} "
                  f"${w['net']:>7,.0f} {w['n_fired']:>5}")

    # ===================================================================
    # Adaptive stop replay
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"ADAPTIVE STOP REPLAY")
    print(f"base_hs={ADAPTIVE_BASE_HS}, min_hs={ADAPTIVE_MIN_HS}")
    print(f"adaptive_hs = clamp(base - (fc/0.40) * (base-min), min, base)")
    print(f"{'='*70}")

    # Show fade_confirm distribution
    fcs = [c["fade_confirm"] for c in test_cycles]
    print(f"\n  fade_confirm distribution:")
    for pct in [10, 25, 50, 75, 90]:
        fc_val = np.percentile(fcs, pct)
        ahs = compute_adaptive_hs(fc_val)
        print(f"    P{pct}: fc={fc_val:.3f} -> adaptive_hs={ahs:.1f}")

    # Run replay
    results = replay_adaptive_stop(test_cycles)
    weekly = summarize_by_week(results, TEST_WEEKS)

    stopped_earlier = [r for r in results if r["action"] == "STOPPED_EARLIER"]
    turned_to_stop = [r for r in results if r["action"] == "TURNED_TO_STOP"]
    total_delta = sum(r["delta"] for r in results if r["week"] in TEST_WEEKS)
    all_positive = all(w["net"] >= 0 for w in weekly.values())

    print(f"\n  STOPPED_EARLIER (saves): {len(stopped_earlier)}")
    if stopped_earlier:
        avg_save = np.mean([r["delta"] for r in stopped_earlier])
        print(f"    Avg savings: ${avg_save:,.2f}")
    print(f"  TURNED_TO_STOP (costs): {len(turned_to_stop)}")
    if turned_to_stop:
        avg_cost = np.mean([r["delta"] for r in turned_to_stop])
        print(f"    Avg cost: ${avg_cost:,.2f}")
    print(f"  Total delta: ${total_delta:,.0f}")
    print(f"  All weeks positive: {'YES' if all_positive else 'NO'}")

    print(f"\n  {'Week':<10} {'Cat':>8} {'Saves':>8} {'Costs':>8} {'Net':>8}")
    print(f"  {'-'*44}")
    for wk in sorted(weekly.keys()):
        w = weekly[wk]
        cat = TEST_WEEKS.get(wk, "")
        print(f"  {wk:<10} {cat:>8} ${w['saves']:>7,.0f} ${w['costs']:>7,.0f} "
              f"${w['net']:>7,.0f}")

    # ===================================================================
    # Adaptive stop by fade_confirm bucket
    # ===================================================================
    print(f"\n  Adaptive stop detail by fade_confirm bucket:")
    fc_buckets = [(0.0, 0.10), (0.10, 0.20), (0.20, 0.30), (0.30, 0.40)]
    print(f"  {'FC range':<12} {'N':>5} {'Avg HS':>7} {'Stops':>5} "
          f"{'Earlier':>7} {'Turned':>7} {'Net $':>8}")
    print(f"  {'-'*60}")
    for lo, hi in fc_buckets:
        bucket = [r for r in results if lo <= r["fade_confirm"] < hi
                  and r["week"] in TEST_WEEKS]
        if not bucket:
            continue
        avg_hs = np.mean([r["adaptive_hs"] for r in bucket])
        stops = sum(1 for r in bucket if r["exit_type"] == "HARD_STOP")
        earlier = sum(1 for r in bucket if r["action"] == "STOPPED_EARLIER")
        turned = sum(1 for r in bucket if r["action"] == "TURNED_TO_STOP")
        net = sum(r["delta"] for r in bucket)
        print(f"  [{lo:.2f},{hi:.2f})  {len(bucket):>5} {avg_hs:>6.1f} {stops:>5} "
              f"{earlier:>7} {turned:>7} ${net:>7,.0f}")

    # ===================================================================
    # Summary
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"STEP 4 SUMMARY")
    print(f"{'='*70}")

    # Find best partial N
    best_partial_n = None
    best_partial_delta = 0
    for n_pts in PARTIAL_N_PTS:
        results = replay_partial_profit(test_cycles, n_pts)
        weekly = summarize_by_week(results, TEST_WEEKS)
        total = sum(w["net"] for w in weekly.values())
        all_pos = all(w["net"] >= 0 for w in weekly.values())
        if all_pos and total > best_partial_delta:
            best_partial_delta = total
            best_partial_n = n_pts

    # Adaptive stop summary
    adapt_results = replay_adaptive_stop(test_cycles)
    adapt_weekly = summarize_by_week(adapt_results, TEST_WEEKS)
    adapt_total = sum(w["net"] for w in adapt_weekly.values())
    adapt_all_pos = all(w["net"] >= 0 for w in adapt_weekly.values())

    print(f"\n  Partial profit:")
    if best_partial_n is not None:
        print(f"    Best N: {best_partial_n} pts | Net: ${best_partial_delta:,.0f} | All weeks positive: YES")
        print(f"    → Proceeds to Step 5")
    else:
        print(f"    No N value positive on all weeks → DEAD")

    print(f"\n  Adaptive stop:")
    print(f"    Net: ${adapt_total:,.0f} | All weeks positive: {'YES' if adapt_all_pos else 'NO'}")
    if adapt_all_pos:
        print(f"    → Proceeds to Step 5")
    else:
        print(f"    → DEAD (not all weeks positive)")

    total_time = time.time() - t0
    print(f"\nTotal runtime: {total_time:.0f}s")


if __name__ == "__main__":
    main()
