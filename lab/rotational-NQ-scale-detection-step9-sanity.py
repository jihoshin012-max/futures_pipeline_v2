# archetype: rotational
"""
rotational-NQ-scale-detection-step9-sanity.py -- Sanity check: random filter.

Runs a random entry filter at the same retention rate as chop<0.10 (~58%)
to verify the improvement comes from choppiness, not from the act of
skipping entries and re-entering.

If random filter also shows massive improvement: the effect is from sim
re-entry mechanics, not choppiness signal.
If random filter doesn't help: choppiness is genuinely selecting better windows.

Runs 10 random seeds for statistical confidence.

Usage:
    python rotational-NQ-scale-detection-step9-sanity.py [--bar-file PATH]
"""
from __future__ import annotations

import csv
import importlib
import time
from pathlib import Path

import numpy as np

_engine = importlib.import_module("rotational-NQ-scale-detection-engine")
load_bars_extended = _engine.load_bars_extended
aggregate_to_ntick = _engine.aggregate_to_ntick
compute_regime_signals = _engine.compute_regime_signals
map_signal_to_ticks = _engine.map_signal_to_ticks
append_audit = _engine.append_audit
append_journal = _engine.append_journal

_sweep = importlib.import_module("rotational-NQ-scale-detection-sweep")
run_sim_filtered = _sweep.run_sim_filtered
TICK_SIZE = _sweep.TICK_SIZE
COMMISSION_PER_RT_MINI = _sweep.COMMISSION_PER_RT_MINI

SD = 10.0
HS = 60.0
MAX_LEVELS = 1
MAX_CONTRACT_SIZE = 2
MAX_FADES = 0
BAR_SIZE = 250

N_SEEDS = 10
TARGET_PASS_RATE = 0.58  # match chop<0.10 retention


def make_random_filter(n_ticks, pass_rate, seed):
    """Create a random filter that passes ~pass_rate fraction of bars."""
    rng = np.random.RandomState(seed)
    mask = rng.random(n_ticks) < pass_rate

    def filter_fn(signals, i, direction, step_dist):
        if i < len(mask):
            return bool(mask[i])
        return True
    return filter_fn


def compute_metrics(cycles):
    if not cycles:
        return {"cycles": 0, "wr": 0, "sr": 0, "pnl": 0.0, "er": 0.0}
    n = len(cycles)
    stops = sum(1 for c in cycles if c["exit_type"] == "HARD_STOP")
    net_pnls = []
    for c in cycles:
        comm = COMMISSION_PER_RT_MINI * max(c.get("max_position", 1), 1)
        net_pnls.append(c["pnl_ticks"] * 5.0 - comm)
    wins = sum(1 for p in net_pnls if p >= 0)
    total_pnl = sum(net_pnls)
    return {
        "cycles": n, "wr": wins / n, "sr": stops / n,
        "pnl": total_pnl, "er": total_pnl / n,
    }


def main():
    import argparse
    parser = argparse.ArgumentParser(description="Sanity check: random filter")
    parser.add_argument("--bar-file", type=str,
                        default=r"C:\Projects\futures_pipeline\data\NQ-1tick-calibration.csv")
    args = parser.parse_args()

    print(f"Loading bars...")
    t0 = time.time()
    bars = load_bars_extended(args.bar_file)
    print(f"Loaded {bars['n']} bars in {time.time()-t0:.1f}s")

    n_ticks = bars["n"]

    # Baseline
    print(f"\nRunning BASELINE...")
    baseline_cycles = run_sim_filtered(
        bars, step_dist=SD, hard_stop=HS,
        max_fades=MAX_FADES, max_levels=MAX_LEVELS,
        max_contract_size=MAX_CONTRACT_SIZE,
    )
    bl = compute_metrics(baseline_cycles)
    print(f"  Baseline: {bl['cycles']} cyc | {bl['wr']:.0%} WR | {bl['sr']:.0%} SR | "
          f"${bl['pnl']:,.0f} | E[R]=${bl['er']:.2f}")

    # Choppiness filter (for comparison)
    print(f"\nRunning chop<0.10 filter...")
    agg_bars, tick_to_agg = aggregate_to_ntick(bars, BAR_SIZE)
    features = compute_regime_signals(agg_bars, lookback=3)
    chop_ticks = map_signal_to_ticks(features["choppiness"], tick_to_agg)
    signals = {
        "choppiness": chop_ticks,
        "warmup_ticks": {"dominant_scale": 0, "asymmetry": 0},
    }

    def chop_filter(signals, i, direction, step_dist):
        chop = signals["choppiness"][i]
        if np.isnan(chop):
            return True
        return chop < 0.10

    chop_cycles = run_sim_filtered(
        bars, step_dist=SD, hard_stop=HS,
        max_fades=MAX_FADES, max_levels=MAX_LEVELS,
        max_contract_size=MAX_CONTRACT_SIZE,
        signal_arrays=signals, filter_fn=chop_filter,
    )
    ch = compute_metrics(chop_cycles)
    print(f"  chop<0.10: {ch['cycles']} cyc | {ch['wr']:.0%} WR | {ch['sr']:.0%} SR | "
          f"${ch['pnl']:,.0f} | E[R]=${ch['er']:.2f}")

    # Random filters
    print(f"\nRunning {N_SEEDS} random filters (target pass rate={TARGET_PASS_RATE:.0%})...")
    random_results = []
    for seed in range(N_SEEDS):
        dummy_signals = {"warmup_ticks": {"dominant_scale": 0, "asymmetry": 0}}
        filter_fn = make_random_filter(n_ticks, TARGET_PASS_RATE, seed)
        rand_cycles = run_sim_filtered(
            bars, step_dist=SD, hard_stop=HS,
            max_fades=MAX_FADES, max_levels=MAX_LEVELS,
            max_contract_size=MAX_CONTRACT_SIZE,
            signal_arrays=dummy_signals, filter_fn=filter_fn,
        )
        rm = compute_metrics(rand_cycles)
        random_results.append(rm)
        print(f"  seed={seed}: {rm['cycles']:>6} cyc | {rm['wr']:.0%} WR | {rm['sr']:.0%} SR | "
              f"${rm['pnl']:>10,.0f} | E[R]=${rm['er']:>7.2f}")

    # Summary
    print(f"\n{'='*70}")
    print(f"SANITY CHECK SUMMARY")
    print(f"{'='*70}")

    rand_pnls = [r["pnl"] for r in random_results]
    rand_ers = [r["er"] for r in random_results]
    rand_cycles = [r["cycles"] for r in random_results]
    rand_srs = [r["sr"] for r in random_results]

    print(f"\n  {'':>15} {'Cycles':>8} {'WR':>6} {'SR':>6} {'Total PnL':>12} {'E[R]':>8}")
    print(f"  {'-'*58}")
    print(f"  {'Baseline':>15} {bl['cycles']:>8} {bl['wr']:>6.0%} {bl['sr']:>6.0%} "
          f"${bl['pnl']:>11,.0f} ${bl['er']:>7.2f}")
    print(f"  {'chop<0.10':>15} {ch['cycles']:>8} {ch['wr']:>6.0%} {ch['sr']:>6.0%} "
          f"${ch['pnl']:>11,.0f} ${ch['er']:>7.2f}")
    print(f"  {'Random avg':>15} {np.mean(rand_cycles):>8.0f} {np.mean([r['wr'] for r in random_results]):>6.0%} "
          f"{np.mean(rand_srs):>6.0%} "
          f"${np.mean(rand_pnls):>11,.0f} ${np.mean(rand_ers):>7.2f}")
    print(f"  {'Random min':>15} {min(rand_cycles):>8} {'':>6} {max(rand_srs):>6.0%} "
          f"${min(rand_pnls):>11,.0f} ${min(rand_ers):>7.2f}")
    print(f"  {'Random max':>15} {max(rand_cycles):>8} {'':>6} {min(rand_srs):>6.0%} "
          f"${max(rand_pnls):>11,.0f} ${max(rand_ers):>7.2f}")

    # Verdict
    chop_better_than_all_random = ch["pnl"] > max(rand_pnls)
    chop_er_vs_rand_avg = ch["er"] / np.mean(rand_ers) if np.mean(rand_ers) > 0 else float('inf')

    print(f"\n  chop<0.10 PnL > all random seeds: {chop_better_than_all_random}")
    print(f"  chop<0.10 E[R] / random avg E[R]: {chop_er_vs_rand_avg:.1f}x")

    if np.mean(rand_pnls) > bl["pnl"] * 3:
        verdict = "FAIL -- random filter also helps massively. Effect is from re-entry mechanics."
    elif chop_better_than_all_random:
        verdict = "PASS -- choppiness outperforms all random seeds. Signal is real."
    else:
        verdict = "UNCLEAR -- choppiness helps but overlaps with random range."

    print(f"\n  VERDICT: {verdict}")

    # Audit + Journal
    total = time.time() - t0
    append_audit("SANITY_CHECK_COMPLETE",
                 f"Random filter sanity check. Baseline: ${bl['pnl']:,.0f}. "
                 f"chop<0.10: ${ch['pnl']:,.0f}. Random avg: ${np.mean(rand_pnls):,.0f}. "
                 f"Verdict: {verdict.split(' -- ')[0]}. Runtime: {total:.0f}s.")

    journal_text = f"""## Step 9 sanity check: random filter comparison

**Question:** Is the improvement from choppiness, or from the act of skipping entries and re-entering?

**Method:** Ran {N_SEEDS} random filters at {TARGET_PASS_RATE:.0%} pass rate (matching chop<0.10 retention).

### Results

| | Cycles | SR | Total PnL | E[R] |
|---|---|---|---|---|
| Baseline | {bl['cycles']:,} | {bl['sr']:.0%} | ${bl['pnl']:,.0f} | ${bl['er']:.2f} |
| chop<0.10 | {ch['cycles']:,} | {ch['sr']:.0%} | ${ch['pnl']:,.0f} | ${ch['er']:.2f} |
| Random avg | {np.mean(rand_cycles):,.0f} | {np.mean(rand_srs):.0%} | ${np.mean(rand_pnls):,.0f} | ${np.mean(rand_ers):.2f} |
| Random min | {min(rand_cycles):,} | | ${min(rand_pnls):,.0f} | ${min(rand_ers):.2f} |
| Random max | {max(rand_cycles):,} | | ${max(rand_pnls):,.0f} | ${max(rand_ers):.2f} |

### Verdict: {verdict}

Runtime: {total:.0f}s
"""
    append_journal(journal_text)

    print(f"\nSanity check complete: {total:.0f}s ({total/60:.1f}m)")


if __name__ == "__main__":
    main()
