# archetype: rotational
"""P2 stress test suite — SC-aligned aggregation."""
import sys, importlib, time
import numpy as np
from collections import defaultdict

sys.path.insert(0, r"c:\Projects\futures_pipeline\lab")

_engine = importlib.import_module("rotational-NQ-scale-detection-engine")
_sweep = importlib.import_module("rotational-NQ-scale-detection-sweep")

load_bars_extended = _engine.load_bars_extended
aggregate_to_ntick = _engine.aggregate_to_ntick
compute_regime_signals = _engine.compute_regime_signals
map_signal_to_ticks = _engine.map_signal_to_ticks
run_sim_filtered = _sweep.run_sim_filtered
COMM = 4.12
TICK_VALUE = 5.0

SD, HS, MAX_FADES, MAX_LEVELS, MAX_CS = 10.0, 60.0, 0, 1, 2
BAR_SIZE, LB = 250, 3

def make_signals(chop_arr):
    return {"choppiness": chop_arr, "warmup_ticks": {"dominant_scale": 0, "asymmetry": 0}}

def make_filter(thresh):
    def f(signals, i, direction, step_dist):
        c = signals["choppiness"][i]
        if np.isnan(c): return True
        return c < thresh
    return f

def get_pnls(cycles, slip_ticks=0):
    pnls = []
    for c in cycles:
        pos = max(c.get("max_position", 1), 1)
        raw = c["pnl_ticks"] * TICK_VALUE - COMM * pos
        raw -= slip_ticks * TICK_VALUE * pos * 2
        pnls.append(raw)
    return np.array(pnls)

# Load data
print("Loading P2 holdout data...")
t0 = time.time()
bars = load_bars_extended(r"C:\Projects\futures_pipeline\data\NQ-1tick-holdout.csv")
print(f"  {bars['n']} ticks ({time.time()-t0:.0f}s)")

print("Aggregating (SC-aligned)...")
agg_bars, tick_to_agg = aggregate_to_ntick(bars, BAR_SIZE)
features = compute_regime_signals(agg_bars, lookback=LB)
chop_ticks = map_signal_to_ticks(features["choppiness"], tick_to_agg)
signals_base = make_signals(chop_ticks)

print("Running filtered baseline (chop<0.10)...")
fl_cycles = run_sim_filtered(bars, SD, HS, MAX_FADES, MAX_LEVELS, MAX_CS,
                              signal_arrays=signals_base, filter_fn=make_filter(0.10))
pnls = get_pnls(fl_cycles)
n = len(fl_cycles)
wr = np.mean(pnls >= 0)
sr = np.mean([c["exit_type"] == "HARD_STOP" for c in fl_cycles])
print(f"  {n} cyc | {wr:.0%} WR | {sr:.0%} SR | ${np.sum(pnls):,.0f} | E[R]=${np.mean(pnls):.2f}")

# ==================================================================
print(f"\n{'='*70}")
print("TEST 1: THRESHOLD SENSITIVITY")
print(f"{'='*70}")
for thresh in [0.05, 0.07, 0.08, 0.09, 0.10, 0.11, 0.12, 0.13, 0.15, 0.20]:
    cyc = run_sim_filtered(bars, SD, HS, MAX_FADES, MAX_LEVELS, MAX_CS,
                           signal_arrays=signals_base, filter_fn=make_filter(thresh))
    p = get_pnls(cyc)
    nc = len(cyc)
    print(f"  thresh={thresh:.2f}: {nc:>6} cyc | {np.mean(p>=0):.0%} WR | "
          f"${np.sum(p):>10,.0f} | E[R]=${np.mean(p):>6.2f}")

# ==================================================================
print(f"\n{'='*70}")
print("TEST 2: LOOKBACK SENSITIVITY")
print(f"{'='*70}")
for lb in [2, 3, 4, 5, 8]:
    feat_lb = compute_regime_signals(agg_bars, lookback=lb)
    chop_lb = map_signal_to_ticks(feat_lb["choppiness"], tick_to_agg)
    sig_lb = make_signals(chop_lb)
    cyc = run_sim_filtered(bars, SD, HS, MAX_FADES, MAX_LEVELS, MAX_CS,
                           signal_arrays=sig_lb, filter_fn=make_filter(0.10))
    p = get_pnls(cyc)
    print(f"  lb={lb}: {len(cyc):>6} cyc | {np.mean(p>=0):.0%} WR | "
          f"${np.sum(p):>10,.0f} | E[R]=${np.mean(p):>6.2f}")

# ==================================================================
print(f"\n{'='*70}")
print("TEST 3: HISTORICAL DRAWDOWN")
print(f"{'='*70}")
equity = np.cumsum(pnls)
peak = np.maximum.accumulate(equity)
dd = peak - equity
max_dd = np.max(dd)
print(f"  Max drawdown: ${max_dd:,.0f}")
print(f"  Profit/DD ratio: {np.sum(pnls)/max_dd:.1f}")
cur_w = cur_l = max_w = max_l = 0
for p in pnls:
    if p >= 0:
        cur_w += 1; cur_l = 0
    else:
        cur_l += 1; cur_w = 0
    max_w = max(max_w, cur_w)
    max_l = max(max_l, cur_l)
print(f"  Max consecutive wins: {max_w}")
print(f"  Max consecutive losses: {max_l}")

# ==================================================================
print(f"\n{'='*70}")
print("TEST 4: SERIAL CORRELATION")
print(f"{'='*70}")
mean_p = np.mean(pnls)
var_p = np.var(pnls)
for lag in range(1, 6):
    if var_p > 0:
        corr = np.mean((pnls[lag:] - mean_p) * (pnls[:-lag] - mean_p)) / var_p
    else:
        corr = 0
    sig = 2.0 / np.sqrt(n)
    status = "SIG" if abs(corr) > sig else "ok"
    print(f"  Lag {lag}: r={corr:+.4f} (threshold=+/-{sig:.4f}) {status}")

# ==================================================================
print(f"\n{'='*70}")
print("TEST 5: BOOTSTRAP MONTE CARLO (10K paths)")
print(f"{'='*70}")
rng = np.random.default_rng(42)
n_boot = 10000
boot_pnl = np.empty(n_boot)
boot_dd = np.empty(n_boot)
for b in range(n_boot):
    sample = rng.choice(pnls, size=len(pnls), replace=True)
    cum = np.cumsum(sample)
    boot_pnl[b] = cum[-1]
    pk = np.maximum.accumulate(cum)
    boot_dd[b] = np.max(pk - cum)
for label, arr in [("PnL", boot_pnl), ("DD", boot_dd)]:
    p5, p50, p95, p99 = np.percentile(arr, [5, 50, 95, 99])
    print(f"  {label}: P5=${p5:,.0f} P50=${p50:,.0f} P95=${p95:,.0f} P99=${p99:,.0f}")

# ==================================================================
print(f"\n{'='*70}")
print("TEST 6: RESHUFFLING MONTE CARLO")
print(f"{'='*70}")
n_reshuffle = 1000
reshuffle_dd = np.empty(n_reshuffle)
for r in range(n_reshuffle):
    shuffled = rng.permutation(pnls)
    cum = np.cumsum(shuffled)
    pk = np.maximum.accumulate(cum)
    reshuffle_dd[r] = np.max(pk - cum)
pct_rank = np.mean(reshuffle_dd <= max_dd) * 100
print(f"  Historical DD ${max_dd:,.0f} at {pct_rank:.0f}th percentile of reshuffled paths")

# ==================================================================
print(f"\n{'='*70}")
print("TEST 7: WR COMPRESSION")
print(f"{'='*70}")
wins_arr = pnls[pnls >= 0]
losses_arr = pnls[pnls < 0]
avg_win = np.mean(wins_arr) if len(wins_arr) > 0 else 0
avg_loss = np.mean(losses_arr) if len(losses_arr) > 0 else 0
for red in [0, 5, 8, 10, 15]:
    adj_wr = wr * (1 - red / 100)
    n_w = int(n * adj_wr)
    n_l = n - n_w
    adj_pnl = n_w * avg_win + n_l * avg_loss
    pf = (n_w * avg_win) / (-n_l * avg_loss) if n_l > 0 and avg_loss < 0 else 0
    print(f"  {red}% reduction ({adj_wr:.0%} WR): PF={pf:.2f}, total=${adj_pnl:,.0f}")

# ==================================================================
print(f"\n{'='*70}")
print("TEST 8: SLIPPAGE")
print(f"{'='*70}")
for slip in [0, 1, 2, 3, 4, 5, 6]:
    p = get_pnls(fl_cycles, slip_ticks=slip)
    pf_num = np.sum(p[p >= 0])
    pf_den = -np.sum(p[p < 0])
    pf = pf_num / pf_den if pf_den > 0 else 0
    print(f"  {slip}t slip: PF={pf:.2f}, E[R]=${np.mean(p):.2f}, total=${np.sum(p):,.0f}")

# ==================================================================
print(f"\n{'='*70}")
print("TEST 9: KELLY SIZING")
print(f"{'='*70}")
avg_w = np.mean(pnls[pnls >= 0])
avg_l = -np.mean(pnls[pnls < 0]) if np.any(pnls < 0) else 1
wl_ratio = avg_w / avg_l if avg_l > 0 else 0
kelly = wr - (1 - wr) / wl_ratio if wl_ratio > 0 else 0
print(f"  Win rate: {wr:.2%}")
print(f"  Avg win: ${avg_w:.2f}, Avg loss: ${avg_l:.2f}")
print(f"  W/L ratio: {wl_ratio:.2f}")
print(f"  Full Kelly: {kelly:.2f}")
print(f"  Half Kelly: {kelly/2:.2f}")

# ==================================================================
print(f"\n{'='*70}")
print("P2 STRESS TEST COMPLETE")
print(f"{'='*70}")
print(f"  Runtime: {time.time()-t0:.0f}s")
