# archetype: rotational
"""
rotational-NQ-scale-detection-step29.py -- Track C2 Step 0: HS sweep.

Week selection: reused from Track C Step 0 (same A+B baseline).
  See lab/output/rotational-NQ-scale-detection/trade-mgmt-step0-week-selection.csv

HS sweep: Run A+B filtered config at HS = 40, 45, 50, 55, 60 across full P1.
Per-week breakdown. If a tighter HS improves E[R] by > 3% over HS=60, adopt it.

Prompt: rotational-NQ-prompt-loss-mitigation-c2.md Step 0
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
#  Config — Track A+B combined (frozen params)
# ---------------------------------------------------------------------------
SD = 10.0
BASELINE_HS = 60.0
HS_SWEEP = [40.0, 45.0, 50.0, 55.0, 60.0]
MAX_LEVELS = 1
MAX_CONTRACT_SIZE = 2
MAX_FADES = 0
BAR_SIZE = 250
LB = 3
CHOP_THRESHOLD = 0.10
DR2_MAX = -0.40
DSLOPE_MAX = -2.0
FC_MAX = 0.40

OUTPUT_DIR = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")


# ---------------------------------------------------------------------------
#  Signal precomputation (same as step23)
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
#  Filter — full A+B stack
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
        return {"n": 0, "wr": 0.0, "sr": 0.0, "er": 0.0, "pnl": 0.0, "pf": 0.0}
    n = len(cycles)
    stops = sum(1 for c in cycles if c["exit_type"] == "HARD_STOP")
    net_pnls = []
    for c in cycles:
        comm = COMMISSION_PER_RT_MINI * max(c.get("max_position", 1), 1)
        net_pnls.append(c["pnl_ticks"] * 5.0 - comm)
    wins = sum(1 for p in net_pnls if p >= 0)
    gross_win = sum(p for p in net_pnls if p >= 0)
    gross_loss = abs(sum(p for p in net_pnls if p < 0))
    pf = gross_win / gross_loss if gross_loss > 0 else float("inf")
    return {"n": n, "wr": wins / n, "sr": stops / n,
            "er": sum(net_pnls) / n, "pnl": sum(net_pnls), "pf": pf}


def weekly_breakdown(cycles):
    weeks = defaultdict(list)
    for c in cycles:
        dt = c["seed_dt"][:10]
        d = datetime.date(int(dt[:4]), int(dt[5:7]), int(dt[8:10]))
        wk = f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"
        weeks[wk].append(c)
    result = {}
    for wk in sorted(weeks.keys()):
        result[wk] = compute_metrics(weeks[wk])
    return result


def stop_stats(cycles):
    """Compute average stop loss and count."""
    stop_pnls = []
    for c in cycles:
        if c["exit_type"] == "HARD_STOP":
            comm = COMMISSION_PER_RT_MINI * max(c.get("max_position", 1), 1)
            stop_pnls.append(c["pnl_ticks"] * 5.0 - comm)
    if not stop_pnls:
        return {"count": 0, "avg_loss": 0.0, "total_loss": 0.0}
    return {"count": len(stop_pnls), "avg_loss": np.mean(stop_pnls),
            "total_loss": sum(stop_pnls)}


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Track C2 Step 0 — HS sweep")
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

    # ===================================================================
    # HS Sweep: run A+B config at each HS level
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"TRACK C2 STEP 0: HS SWEEP")
    print(f"Config: SD={SD} depth_1 MCS={MAX_CONTRACT_SIZE}")
    print(f"Filter: chop<{CHOP_THRESHOLD} + dR2<={DR2_MAX} + dSlope<={DSLOPE_MAX} + fc<{FC_MAX}")
    print(f"HS levels: {HS_SWEEP}")
    print(f"{'='*70}")

    all_results = {}
    all_weekly = {}

    for hs in HS_SWEEP:
        print(f"\n--- HS={hs:.0f} ticks ---")
        t1 = time.time()
        cycles = run_sim_filtered(
            bars, step_dist=SD, hard_stop=hs,
            max_fades=MAX_FADES, max_levels=MAX_LEVELS,
            max_contract_size=MAX_CONTRACT_SIZE,
            signal_arrays=signals, filter_fn=ab_filter,
        )
        m = compute_metrics(cycles)
        ss = stop_stats(cycles)
        wkly = weekly_breakdown(cycles)
        elapsed = time.time() - t1
        print(f"  {m['n']} cyc | {m['wr']:.0%} WR | {m['sr']:.0%} SR | "
              f"E[R]=${m['er']:.2f} | PnL=${m['pnl']:,.0f} | PF={m['pf']:.2f} ({elapsed:.0f}s)")
        print(f"  Stops: {ss['count']} | Avg loss: ${ss['avg_loss']:.2f} | "
              f"Total loss: ${ss['total_loss']:,.0f}")
        all_results[hs] = {"metrics": m, "stop_stats": ss, "cycles": cycles}
        all_weekly[hs] = wkly

    # ===================================================================
    # Summary comparison
    # ===================================================================
    baseline_er = all_results[BASELINE_HS]["metrics"]["er"]

    print(f"\n{'='*70}")
    print(f"HS SWEEP SUMMARY (baseline HS={BASELINE_HS:.0f}, E[R]=${baseline_er:.2f})")
    print(f"{'='*70}")
    print(f"\n{'HS':>4} {'Cycles':>7} {'WR':>5} {'SR':>5} {'E[R]':>8} {'PnL':>12} "
          f"{'PF':>5} {'Avg Stop':>10} {'dE[R]%':>7}")
    print(f"{'-'*70}")
    for hs in HS_SWEEP:
        m = all_results[hs]["metrics"]
        ss = all_results[hs]["stop_stats"]
        delta_pct = (m["er"] - baseline_er) / abs(baseline_er) * 100 if baseline_er != 0 else 0
        marker = " ***" if delta_pct > 3 else ""
        print(f"{hs:>4.0f} {m['n']:>7} {m['wr']:>4.0%} {m['sr']:>4.0%} "
              f"${m['er']:>7.2f} ${m['pnl']:>11,.0f} {m['pf']:>5.2f} "
              f"${ss['avg_loss']:>9.2f} {delta_pct:>+6.1f}%{marker}")

    # ===================================================================
    # Per-week breakdown for each HS
    # ===================================================================
    all_weeks_list = sorted(all_weekly[BASELINE_HS].keys())

    print(f"\n{'='*70}")
    print(f"PER-WEEK BREAKDOWN")
    print(f"{'='*70}")

    for hs in HS_SWEEP:
        wkly = all_weekly[hs]
        print(f"\n--- HS={hs:.0f} ---")
        print(f"{'Week':<10} {'N':>5} {'WR':>5} {'SR':>5} {'E[R]':>8} {'PnL':>10}")
        print(f"{'-'*50}")
        for wk in all_weeks_list:
            wm = wkly.get(wk, {"n": 0, "wr": 0, "sr": 0, "er": 0, "pnl": 0})
            print(f"{wk:<10} {wm['n']:>5} {wm['wr']:>4.0%} {wm['sr']:>4.0%} "
                  f"${wm['er']:>7.2f} ${wm['pnl']:>9,.0f}")

    # ===================================================================
    # Per-week E[R] comparison across HS levels
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"PER-WEEK E[R] COMPARISON")
    print(f"{'='*70}")

    header = f"{'Week':<10}"
    for hs in HS_SWEEP:
        header += f" {'HS='+str(int(hs)):>8}"
    header += f" {'Best HS':>8}"
    print(header)
    print(f"{'-'*(10 + 9*len(HS_SWEEP) + 9)}")

    weeks_best_hs = {}
    for wk in all_weeks_list:
        row = f"{wk:<10}"
        best_hs = BASELINE_HS
        best_er = -float("inf")
        for hs in HS_SWEEP:
            wm = all_weekly[hs].get(wk, {"er": 0})
            er = wm["er"]
            row += f" ${er:>7.2f}"
            if er > best_er:
                best_er = er
                best_hs = hs
        row += f" {int(best_hs):>7}"
        weeks_best_hs[wk] = best_hs
        print(row)

    # Count how many weeks each HS wins
    print(f"\nWeeks where each HS has highest E[R]:")
    for hs in HS_SWEEP:
        count = sum(1 for wk, bh in weeks_best_hs.items() if bh == hs)
        print(f"  HS={hs:.0f}: {count}/{len(all_weeks_list)} weeks")

    # ===================================================================
    # Verdict
    # ===================================================================
    best_hs = max(HS_SWEEP, key=lambda h: all_results[h]["metrics"]["er"])
    best_er = all_results[best_hs]["metrics"]["er"]
    improvement_pct = (best_er - baseline_er) / abs(baseline_er) * 100 if baseline_er != 0 else 0

    print(f"\n{'='*70}")
    print(f"VERDICT")
    print(f"{'='*70}")
    print(f"Best HS: {best_hs:.0f} ticks (E[R]=${best_er:.2f})")
    print(f"Baseline HS: {BASELINE_HS:.0f} ticks (E[R]=${baseline_er:.2f})")
    print(f"Improvement: {improvement_pct:+.1f}%")
    if improvement_pct > 3:
        print(f"PASS — HS={best_hs:.0f} improves E[R] by {improvement_pct:.1f}% > 3%. "
              f"Adopt HS={best_hs:.0f} as new baseline for remaining Track C2 steps.")
    else:
        print(f"No HS improvement > 3%. Keep HS={BASELINE_HS:.0f}. "
              f"Proceed with other approaches.")

    # ===================================================================
    # Save results CSV
    # ===================================================================
    out_path = OUTPUT_DIR / "c2-step0-hs-sweep.csv"
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["hs", "week", "n", "wr", "sr", "er", "pnl", "pf",
                     "stop_count", "avg_stop_loss"])
        for hs in HS_SWEEP:
            # Aggregate row
            m = all_results[hs]["metrics"]
            ss = all_results[hs]["stop_stats"]
            w.writerow([int(hs), "ALL", m["n"], f"{m['wr']:.4f}", f"{m['sr']:.4f}",
                        f"{m['er']:.2f}", f"{m['pnl']:.2f}", f"{m['pf']:.2f}",
                        ss["count"], f"{ss['avg_loss']:.2f}"])
            # Per-week rows
            for wk in all_weeks_list:
                wm = all_weekly[hs].get(wk, {"n": 0, "wr": 0, "sr": 0, "er": 0, "pnl": 0, "pf": 0})
                w.writerow([int(hs), wk, wm["n"], f"{wm['wr']:.4f}", f"{wm['sr']:.4f}",
                            f"{wm['er']:.2f}", f"{wm['pnl']:.2f}", f"{wm['pf']:.2f}",
                            "", ""])
    print(f"\nSaved: {out_path}")

    total_time = time.time() - t0
    print(f"\nTotal runtime: {total_time:.0f}s")


if __name__ == "__main__":
    main()
