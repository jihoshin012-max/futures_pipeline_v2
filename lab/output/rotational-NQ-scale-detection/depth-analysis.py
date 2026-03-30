# archetype: rotational
"""Analyze depth_0 vs depth_1 split on A+B filtered population."""
import sys, importlib
import numpy as np

sys.path.insert(0, r"c:\Projects\futures_pipeline\lab")

_engine = importlib.import_module("rotational-NQ-scale-detection-engine")
_sweep = importlib.import_module("rotational-NQ-scale-detection-sweep")
load_bars_extended = _engine.load_bars_extended
aggregate_to_ntick = _engine.aggregate_to_ntick
compute_regime_signals = _engine.compute_regime_signals
map_signal_to_ticks = _engine.map_signal_to_ticks
run_sim_filtered = _sweep.run_sim_filtered
COMM = 4.12
TV = 5.0

SD, HS, MF, ML, MCS = 10.0, 60.0, 0, 1, 2
BS, LB = 250, 3


def make_filter():
    def f(s, i, d, sd):
        c = s["choppiness"][i]
        if np.isnan(c):
            return True
        if c >= 0.10:
            return False
        dr2 = s.get("dr2", np.zeros(1))[i] if "dr2" in s else 0
        dslope = s.get("dslope", np.zeros(1))[i] if "dslope" in s else 0
        fc = s.get("fade_confirm", np.zeros(1))[i] if "fade_confirm" in s else 0
        if np.isnan(dr2) or np.isnan(dslope) or np.isnan(fc):
            return True
        if dr2 > -0.40:
            return False
        if dslope > -2.0:
            return False
        if fc >= 0.40:
            return False
        return True
    return f


def calc_pnl(cycs):
    return sum(c["pnl_ticks"] * TV - COMM * max(c.get("max_position", 1), 1) for c in cycs)


print("Loading P1...")
bars = load_bars_extended(r"C:\Projects\futures_pipeline\data\NQ-1tick-calibration.csv")
agg, t2a = aggregate_to_ntick(bars, BS)
feat = compute_regime_signals(agg, lookback=LB)

chop = map_signal_to_ticks(feat["choppiness"], t2a)

# Compute dr2 and dslope
dr2_agg = np.full(agg["n"], np.nan)
dslope_agg = np.full(agg["n"], np.nan)
for i in range(1, agg["n"]):
    if not np.isnan(feat["r2"][i]) and not np.isnan(feat["r2"][i - 1]):
        dr2_agg[i] = feat["r2"][i] - feat["r2"][i - 1]
    if not np.isnan(np.abs(feat["slope"])[i]) and not np.isnan(np.abs(feat["slope"])[i - 1]):
        dslope_agg[i] = np.abs(feat["slope"])[i] - np.abs(feat["slope"])[i - 1]

# fade_confirm
n = bars["n"]
last = bars["last"]
fc_ticks = np.full(n, np.nan)
for i in range(n):
    a = t2a[i]
    if a > 0:
        rng = agg["high"][a - 1] - agg["low"][a - 1]
        if rng > 0.001:
            fc_ticks[i] = (float(last[i]) - agg["low"][a - 1]) / rng

signals = {
    "choppiness": chop,
    "dr2": map_signal_to_ticks(dr2_agg, t2a),
    "dslope": map_signal_to_ticks(dslope_agg, t2a),
    "fade_confirm": fc_ticks,
    "warmup_ticks": {"dominant_scale": 0, "asymmetry": 0},
}

print("Running A+B filtered sim...")
cycles = run_sim_filtered(bars, SD, HS, MF, ML, MCS,
                          signal_arrays=signals, filter_fn=make_filter())

d0 = [c for c in cycles if c["depth"] == 0]
d1 = [c for c in cycles if c["depth"] > 0]
d0_stops = [c for c in d0 if c["exit_type"] == "HARD_STOP"]
d1_stops = [c for c in d1 if c["exit_type"] == "HARD_STOP"]
d0_revs = [c for c in d0 if c["exit_type"] == "REVERSAL"]
d1_revs = [c for c in d1 if c["exit_type"] == "REVERSAL"]

total = len(cycles)
print(f"\nTotal cycles: {total}")
print(f"\n=== DEPTH SPLIT ===")
print(f"Depth 0: {len(d0)} cycles ({100*len(d0)/total:.0f}%)")
print(f"  Reversals: {len(d0_revs)} ({100*len(d0_revs)/len(d0):.0f}% WR)" if d0 else "")
print(f"  Stops: {len(d0_stops)}")
print(f"  PnL: ${calc_pnl(d0):,.0f} (E[R]=${calc_pnl(d0)/len(d0):.2f})" if d0 else "")

print(f"\nDepth 1: {len(d1)} cycles ({100*len(d1)/total:.0f}%)")
print(f"  Reversals: {len(d1_revs)} ({100*len(d1_revs)/len(d1):.0f}% WR)" if d1 else "")
print(f"  Stops: {len(d1_stops)}")
print(f"  PnL: ${calc_pnl(d1):,.0f} (E[R]=${calc_pnl(d1)/len(d1):.2f})" if d1 else "")

print(f"\n=== LOSS ATTRIBUTION ===")
print(f"Depth 0 stop losses: ${calc_pnl(d0_stops):,.0f} ({len(d0_stops)} stops)")
print(f"Depth 1 stop losses: ${calc_pnl(d1_stops):,.0f} ({len(d1_stops)} stops)")
print(f"Depth 0 reversal wins: ${calc_pnl(d0_revs):,.0f}")
print(f"Depth 1 reversal wins: ${calc_pnl(d1_revs):,.0f}")

# Avg loss per stop
if d0_stops:
    print(f"\nAvg loss per d0 stop: ${calc_pnl(d0_stops)/len(d0_stops):,.0f}")
if d1_stops:
    print(f"Avg loss per d1 stop: ${calc_pnl(d1_stops)/len(d1_stops):,.0f}")

print(f"\n=== WHAT IF NO ADD (max_levels=0, max_cs=1) ===")
cycles_noadd = run_sim_filtered(bars, SD, HS, MF, 0, 1,
                                signal_arrays=signals, filter_fn=make_filter())
noadd_pnl = calc_pnl(cycles_noadd)
noadd_stops = sum(1 for c in cycles_noadd if c["exit_type"] == "HARD_STOP")
noadd_n = len(cycles_noadd)
noadd_wr = 100 * (noadd_n - noadd_stops) / noadd_n
print(f"Cycles: {noadd_n}")
print(f"WR: {noadd_wr:.0f}%")
print(f"SR: {100*noadd_stops/noadd_n:.0f}%")
print(f"PnL: ${noadd_pnl:,.0f} (E[R]=${noadd_pnl/noadd_n:.2f})")
baseline_pnl = calc_pnl(cycles)
print(f"\nvs A+B baseline: ${baseline_pnl:,.0f} (E[R]=${baseline_pnl/total:.2f})")
print(f"Delta: ${noadd_pnl - baseline_pnl:+,.0f} ({100*(noadd_pnl/baseline_pnl - 1):+.1f}%)")
