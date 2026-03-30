# archetype: rotational
"""
rotational-NQ-scale-detection-stress.py -- Lab-phase stress test suite.

Runs on filtered P1 cycles (choppiness filter, SD=10 HS=60 depth_1).
Tests:
  1. Threshold sensitivity (chop 0.05 to 0.15)
  2. Lookback sensitivity (lb 2, 3, 4, 5)
  3. Historical drawdown analysis
  4. Serial correlation check
  5. Bootstrap Monte Carlo (10K paths)
  6. Reshuffling Monte Carlo (10K paths)
  7. WR compression stress test
  8. Slippage sensitivity
  9. Kelly sizing

Usage:
    python rotational-NQ-scale-detection-stress.py [--bar-file PATH]
"""
from __future__ import annotations

import importlib
import math
import time
from collections import defaultdict
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

TICK_VALUE = 5.0
COMMISSION_PER_RT = 3.50
SD = 10.0
HS = 60.0
MAX_LEVELS = 1
MAX_CONTRACT_SIZE = 2
MAX_FADES = 0
BAR_SIZE = 250


# ---------------------------------------------------------------------------
#  Cycle PnL helper
# ---------------------------------------------------------------------------

def net_pnl(c):
    pos = max(c.get("max_position", 1), 1)
    return c["pnl_ticks"] * TICK_VALUE - pos * COMMISSION_PER_RT


def enrich_cycles(cycles):
    for c in cycles:
        c["net_pnl"] = net_pnl(c)
        c["date"] = c["seed_dt"][:10] if "seed_dt" in c else ""
        c["depth"] = c.get("depth", 0)
        c["config_mcs"] = MAX_CONTRACT_SIZE


# ---------------------------------------------------------------------------
#  Filter helpers
# ---------------------------------------------------------------------------

def make_chop_filter(chop_max, slope_max=None):
    def filter_fn(signals, i, direction, step_dist):
        chop = signals["choppiness"][i]
        if np.isnan(chop):
            return True
        if chop >= chop_max:
            return False
        if slope_max is not None:
            slope = signals["slope"][i]
            if np.isnan(slope):
                return True
            if abs(slope) >= slope_max:
                return False
        return True
    return filter_fn


def precompute_signals(bars, bar_size, lookback):
    agg_bars, tick_to_agg = aggregate_to_ntick(bars, bar_size)
    features = compute_regime_signals(agg_bars, lookback=lookback)
    return {
        "choppiness": map_signal_to_ticks(features["choppiness"], tick_to_agg),
        "slope": map_signal_to_ticks(features["slope"], tick_to_agg),
        "warmup_ticks": {"dominant_scale": 0, "asymmetry": 0},
    }


def run_filtered(bars, signals, chop_max):
    filter_fn = make_chop_filter(chop_max)
    cycles = run_sim_filtered(
        bars, step_dist=SD, hard_stop=HS,
        max_fades=MAX_FADES, max_levels=MAX_LEVELS,
        max_contract_size=MAX_CONTRACT_SIZE,
        signal_arrays=signals, filter_fn=filter_fn,
    )
    enrich_cycles(cycles)
    return cycles


# ---------------------------------------------------------------------------
#  Stress test functions (adapted from lp_stress_test.py)
# ---------------------------------------------------------------------------

def historical_drawdown(cycles):
    pnls = np.array([c["net_pnl"] for c in cycles])
    n = len(pnls)
    cum = np.cumsum(pnls)
    peak = np.maximum.accumulate(cum)
    dd = peak - cum
    max_dd = float(np.max(dd))
    total = float(cum[-1])
    cl = 0; max_cl = 0; cw = 0; max_cw = 0
    for p in pnls:
        if p < 0: cl += 1; cw = 0; max_cl = max(max_cl, cl)
        else: cw += 1; cl = 0; max_cw = max(max_cw, cw)
    return {
        "total_pnl": total, "max_dd": max_dd,
        "profit_dd_ratio": total / max_dd if max_dd > 0 else 0,
        "max_consec_losses": max_cl, "max_consec_wins": max_cw, "n": n,
    }


def serial_correlation(cycles, max_lag=5):
    pnls = np.array([c["net_pnl"] for c in cycles])
    n = len(pnls); mean = np.mean(pnls)
    denom = np.sum((pnls - mean) ** 2)
    thresh = 2.0 / math.sqrt(n)
    results = []
    for lag in range(1, max_lag + 1):
        if lag >= n: break
        numer = np.sum((pnls[:-lag] - mean) * (pnls[lag:] - mean))
        r = numer / denom if denom > 0 else 0
        results.append({"lag": lag, "r": round(float(r), 4),
                       "sig": abs(r) > thresh, "thresh": round(thresh, 4)})
    return results


def bootstrap_mc(cycles, n_paths=10000):
    pnls = np.array([c["net_pnl"] for c in cycles])
    n = len(pnls)
    max_dds = np.empty(n_paths); totals = np.empty(n_paths)
    for p in range(n_paths):
        path = np.random.choice(pnls, size=n, replace=True)
        cum = np.cumsum(path); peak = np.maximum.accumulate(cum)
        max_dds[p] = np.max(peak - cum); totals[p] = cum[-1]
    dd_pcts = np.percentile(max_dds, [50, 75, 90, 95, 99])
    pnl_pcts = np.percentile(totals, [5, 25, 50, 75, 95])
    ruin = {t: float(np.mean(max_dds >= t)) for t in [500, 1000, 1500, 2000, 3000]}
    return {"dd_pcts": dd_pcts, "pnl_pcts": pnl_pcts, "ruin": ruin,
            "dd_worst": float(np.max(max_dds))}


def reshuffling_mc(cycles, n_paths=10000):
    pnls = np.array([c["net_pnl"] for c in cycles])
    n = len(pnls)
    hist_cum = np.cumsum(pnls); hist_peak = np.maximum.accumulate(hist_cum)
    hist_dd = float(np.max(hist_peak - hist_cum))
    max_dds = np.empty(n_paths)
    for p in range(n_paths):
        s = pnls.copy(); np.random.shuffle(s)
        cum = np.cumsum(s); peak = np.maximum.accumulate(cum)
        max_dds[p] = np.max(peak - cum)
    dd_pcts = np.percentile(max_dds, [50, 75, 90, 95, 99])
    pctile = float(np.mean(max_dds <= hist_dd)) * 100
    return {"dd_pcts": dd_pcts, "hist_dd": hist_dd, "hist_pctile": pctile}


def wr_compression(cycles, n_iter=1000):
    pnls = np.array([c["net_pnl"] for c in cycles])
    wins = np.where(pnls >= 0)[0]; n_wins = len(wins)
    mean_loss = float(np.mean(pnls[pnls < 0])) if np.any(pnls < 0) else -100
    results = []
    for red in [0, 2, 5, 8, 10, 15]:
        n_conv = int(n_wins * red / 100)
        pfs = []; dds = []
        for _ in range(n_iter):
            deg = pnls.copy()
            if n_conv > 0:
                idx = np.random.choice(wins, size=n_conv, replace=False)
                deg[idx] = mean_loss
            gw = float(np.sum(deg[deg >= 0])); gl = float(abs(np.sum(deg[deg < 0])))
            pfs.append(gw / gl if gl > 0 else 999)
            cum = np.cumsum(deg); peak = np.maximum.accumulate(cum)
            dds.append(float(np.max(peak - cum)))
        results.append({"red_pct": red,
                       "eff_wr": float(np.mean(pnls >= 0)) * (1 - red / 100),
                       "med_pf": float(np.median(pfs)),
                       "dd_95": float(np.percentile(dds, 95))})
    return results


def slippage_sensitivity(cycles):
    results = []
    for slip in [0, 1, 2, 3, 4, 6]:
        pnls = []
        for c in cycles:
            pos = min(1 * (2 ** c["depth"]), c["config_mcs"])
            gross = c["pnl_ticks"] * TICK_VALUE
            comm = pos * COMMISSION_PER_RT
            slip_cost = pos * slip * TICK_VALUE
            pnls.append(gross - comm - slip_cost)
        pnls = np.array(pnls)
        gw = float(np.sum(pnls[pnls >= 0])); gl = float(abs(np.sum(pnls[pnls < 0])))
        pf = gw / gl if gl > 0 else 999
        cum = np.cumsum(pnls); peak = np.maximum.accumulate(cum)
        results.append({"slip": slip, "pf": round(pf, 2),
                       "er": round(float(np.mean(pnls)), 2),
                       "total": round(float(np.sum(pnls)), 0),
                       "max_dd": round(float(np.max(peak - cum)), 0)})
    return results


def kelly_sizing(cycles):
    pnls = np.array([c["net_pnl"] for c in cycles])
    wins = pnls[pnls >= 0]; losses = pnls[pnls < 0]
    wr = len(wins) / len(pnls) if len(pnls) > 0 else 0
    aw = float(np.mean(wins)) if len(wins) > 0 else 0
    al = float(abs(np.mean(losses))) if len(losses) > 0 else 1
    wl = aw / al if al > 0 else 999
    k = wr - (1 - wr) / wl if wl > 0 else 0
    return {"wr": round(wr, 4), "avg_win": round(aw, 2), "avg_loss": round(al, 2),
            "wl_ratio": round(wl, 2), "full_kelly": round(k, 4),
            "half_kelly": round(k / 2, 4), "quarter_kelly": round(k / 4, 4)}


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Stress Test Suite")
    parser.add_argument("--bar-file", type=str,
                        default=r"C:\Projects\futures_pipeline\data\NQ-1tick-calibration.csv")
    args = parser.parse_args()

    print(f"Loading bars...")
    t0 = time.time()
    bars = load_bars_extended(args.bar_file)
    print(f"Loaded {bars['n']} bars in {time.time()-t0:.1f}s")

    # ===== TEST 1: Threshold sensitivity =====
    print(f"\n{'='*70}")
    print(f"TEST 1: THRESHOLD SENSITIVITY (lb=3)")
    print(f"{'='*70}")
    signals_lb3 = precompute_signals(bars, BAR_SIZE, 3)
    print(f"\n  {'Threshold':>10} {'Cycles':>7} {'Ret':>5} {'WR':>5} {'SR':>5} {'E[R]':>8} {'Total PnL':>12}")
    print(f"  {'-'*55}")

    # Baseline
    bl_cycles = run_sim_filtered(bars, SD, HS, MAX_FADES, MAX_LEVELS, MAX_CONTRACT_SIZE)
    enrich_cycles(bl_cycles)
    bl_pnl = sum(c["net_pnl"] for c in bl_cycles)
    bl_n = len(bl_cycles)
    print(f"  {'baseline':>10} {bl_n:>7} {'--':>5} {sum(1 for c in bl_cycles if c['net_pnl']>=0)/bl_n:>5.0%} "
          f"{sum(1 for c in bl_cycles if c['exit_type']=='HARD_STOP')/bl_n:>5.0%} "
          f"${bl_pnl/bl_n:>7.2f} ${bl_pnl:>11,.0f}")

    thresh_results = {}
    for chop_thresh in [0.05, 0.06, 0.07, 0.08, 0.09, 0.10, 0.11, 0.12, 0.13, 0.14, 0.15]:
        cycles = run_filtered(bars, signals_lb3, chop_thresh)
        n = len(cycles)
        if n == 0: continue
        pnl = sum(c["net_pnl"] for c in cycles)
        sr = sum(1 for c in cycles if c["exit_type"] == "HARD_STOP") / n
        wr = sum(1 for c in cycles if c["net_pnl"] >= 0) / n
        ret = n / bl_n
        print(f"  {chop_thresh:>10.2f} {n:>7} {ret:>5.0%} {wr:>5.0%} {sr:>5.0%} "
              f"${pnl/n:>7.2f} ${pnl:>11,.0f}")
        thresh_results[chop_thresh] = {"n": n, "pnl": pnl, "er": pnl/n, "sr": sr}

    # ===== TEST 2: Lookback sensitivity =====
    print(f"\n{'='*70}")
    print(f"TEST 2: LOOKBACK SENSITIVITY (chop<0.10)")
    print(f"{'='*70}")
    print(f"\n  {'Lookback':>10} {'Cycles':>7} {'Ret':>5} {'WR':>5} {'SR':>5} {'E[R]':>8} {'Total PnL':>12}")
    print(f"  {'-'*55}")

    for lb in [2, 3, 4, 5]:
        signals = precompute_signals(bars, BAR_SIZE, lb)
        cycles = run_filtered(bars, signals, 0.10)
        n = len(cycles)
        if n == 0: continue
        pnl = sum(c["net_pnl"] for c in cycles)
        sr = sum(1 for c in cycles if c["exit_type"] == "HARD_STOP") / n
        wr = sum(1 for c in cycles if c["net_pnl"] >= 0) / n
        ret = n / bl_n
        print(f"  {lb:>10} {n:>7} {ret:>5.0%} {wr:>5.0%} {sr:>5.0%} "
              f"${pnl/n:>7.2f} ${pnl:>11,.0f}")

    # ===== Use chop<0.10 lb=3 for remaining tests =====
    print(f"\n{'='*70}")
    print(f"TESTS 3-9 on chop<0.10 lb=3 filtered cycles")
    print(f"{'='*70}")
    cycles = run_filtered(bars, signals_lb3, 0.10)
    print(f"  {len(cycles)} filtered cycles")

    # TEST 3: Historical drawdown
    print(f"\n--- TEST 3: Historical Drawdown ---")
    hd = historical_drawdown(cycles)
    print(f"  Total PnL: ${hd['total_pnl']:,.0f}")
    print(f"  Max DD: ${hd['max_dd']:,.0f}")
    print(f"  Profit/DD ratio: {hd['profit_dd_ratio']:.1f}")
    print(f"  Max consec losses: {hd['max_consec_losses']}")
    print(f"  Max consec wins: {hd['max_consec_wins']}")

    # TEST 4: Serial correlation
    print(f"\n--- TEST 4: Serial Correlation ---")
    sc = serial_correlation(cycles)
    for s in sc:
        sig_str = " ***" if s["sig"] else ""
        print(f"  lag={s['lag']}: r={s['r']:.4f} (thresh={s['thresh']:.4f}){sig_str}")

    # TEST 5: Bootstrap MC
    print(f"\n--- TEST 5: Bootstrap Monte Carlo (10K paths) ---")
    bmc = bootstrap_mc(cycles)
    print(f"  DD percentiles: P50=${bmc['dd_pcts'][0]:,.0f} P75=${bmc['dd_pcts'][1]:,.0f} "
          f"P90=${bmc['dd_pcts'][2]:,.0f} P95=${bmc['dd_pcts'][3]:,.0f} P99=${bmc['dd_pcts'][4]:,.0f}")
    print(f"  PnL percentiles: P5=${bmc['pnl_pcts'][0]:,.0f} P25=${bmc['pnl_pcts'][1]:,.0f} "
          f"P50=${bmc['pnl_pcts'][2]:,.0f} P75=${bmc['pnl_pcts'][3]:,.0f} P95=${bmc['pnl_pcts'][4]:,.0f}")
    print(f"  Ruin probabilities:")
    for t, p in bmc["ruin"].items():
        print(f"    DD >= ${t:,}: {p:.1%}")

    # TEST 6: Reshuffling MC
    print(f"\n--- TEST 6: Reshuffling Monte Carlo (10K paths) ---")
    rmc = reshuffling_mc(cycles)
    print(f"  Historical DD: ${rmc['hist_dd']:,.0f} (at {rmc['hist_pctile']:.0f}th percentile)")
    print(f"  Reshuffled DD: P50=${rmc['dd_pcts'][0]:,.0f} P90=${rmc['dd_pcts'][2]:,.0f} "
          f"P95=${rmc['dd_pcts'][3]:,.0f}")

    # TEST 7: WR compression
    print(f"\n--- TEST 7: WR Compression ---")
    wrc = wr_compression(cycles)
    print(f"  {'Red%':>5} {'Eff WR':>7} {'Med PF':>7} {'DD 95':>8}")
    for w in wrc:
        print(f"  {w['red_pct']:>5}% {w['eff_wr']:>7.0%} {w['med_pf']:>7.2f} ${w['dd_95']:>7,.0f}")

    # TEST 8: Slippage
    print(f"\n--- TEST 8: Slippage Sensitivity ---")
    ss = slippage_sensitivity(cycles)
    print(f"  {'Slip(t)':>8} {'PF':>6} {'E[R]':>8} {'Total':>10} {'Max DD':>8}")
    for s in ss:
        print(f"  {s['slip']:>8} {s['pf']:>6.2f} ${s['er']:>7.2f} ${s['total']:>9,.0f} ${s['max_dd']:>7,.0f}")

    # TEST 9: Kelly
    print(f"\n--- TEST 9: Kelly Sizing ---")
    ks = kelly_sizing(cycles)
    print(f"  Win rate: {ks['wr']:.1%}")
    print(f"  Avg win: ${ks['avg_win']:.2f}, Avg loss: ${ks['avg_loss']:.2f}")
    print(f"  W/L ratio: {ks['wl_ratio']:.2f}")
    print(f"  Full Kelly: {ks['full_kelly']:.4f}")
    print(f"  Half Kelly: {ks['half_kelly']:.4f}")
    print(f"  Quarter Kelly: {ks['quarter_kelly']:.4f}")

    # --- Audit + Journal ---
    total = time.time() - t0
    append_audit("STRESS_TEST_COMPLETE",
                 f"Stress test suite on SD=10 HS=60 chop<0.10 lb=3. "
                 f"Runtime: {total:.0f}s.")

    journal_text = f"""## Stress test suite on chop<0.10 lb=3

See console output for full results. Runtime: {total:.0f}s.
"""
    append_journal(journal_text)

    print(f"\nAll stress tests complete: {total:.0f}s ({total/60:.1f}m)")


if __name__ == "__main__":
    main()
