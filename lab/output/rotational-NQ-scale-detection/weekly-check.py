# archetype: rotational
"""Check weekly breakdown with chop filter to verify test week categories."""
import sys, importlib, datetime
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
TV = 5.0

SD, HS, MF, ML, MCS = 10.0, 60.0, 0, 1, 2
BS, LB = 250, 3


def make_filter(t):
    def f(s, i, d, sd):
        c = s["choppiness"][i]
        if np.isnan(c):
            return True
        return c < t
    return f


print("Loading P1...")
bars = load_bars_extended(r"C:\Projects\futures_pipeline\data\NQ-1tick-calibration.csv")
agg, t2a = aggregate_to_ntick(bars, BS)
feat = compute_regime_signals(agg, lookback=LB)
chop = map_signal_to_ticks(feat["choppiness"], t2a)
sig = {"choppiness": chop, "warmup_ticks": {"dominant_scale": 0, "asymmetry": 0}}

print("Running filtered sim...")
cycles = run_sim_filtered(bars, SD, HS, MF, ML, MCS,
                          signal_arrays=sig, filter_fn=make_filter(0.10))

# Weekly breakdown
weeks = defaultdict(list)
for c in cycles:
    dt = c["seed_dt"][:10]
    d = datetime.date(int(dt[:4]), int(dt[5:7]), int(dt[8:10]))
    wk = f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"
    pnl = c["pnl_ticks"] * TV - COMM * max(c.get("max_position", 1), 1)
    stops = 1 if c["exit_type"] == "HARD_STOP" else 0
    weeks[wk].append((pnl, stops))

results = []
for wk in sorted(weeks.keys()):
    entries = weeks[wk]
    pnls = [e[0] for e in entries]
    n = len(pnls)
    wins = sum(1 for p in pnls if p >= 0)
    stops = sum(e[1] for e in entries)
    total = sum(pnls)
    results.append((wk, n, wins / n, stops / n, total, total / n))

results_sorted = sorted(results, key=lambda x: x[4])

test_wks = {"2025-W42", "2025-W50", "2025-W45", "2025-W46", "2025-W48"}

print(f"\n{'Week':>8} {'Cyc':>5} {'WR':>5} {'SR':>5} {'PnL':>10} {'E[R]':>8}  Cat")
print("-" * 60)
for wk, n, wr, sr, total, er in results_sorted:
    marker = ""
    if wk in test_wks:
        marker = " <<"
        if wk == "2025-W42":
            marker += " WORST"
        elif wk == "2025-W50":
            marker += " BAD"
        elif wk == "2025-W45":
            marker += " AVG"
        elif wk == "2025-W46":
            marker += " GOOD"
        elif wk == "2025-W48":
            marker += " GOOD"
    print(f"{wk:>8} {n:>5} {wr:>5.0%} {sr:>5.0%} ${total:>9,.0f} ${er:>7.2f}{marker}")

all_pnls = [r[4] for r in results]
neg = sum(1 for p in all_pnls if p < 0)
print(f"\nTotal weeks: {len(results)}")
print(f"Negative weeks: {neg}")

print(f"\nTest weeks ranking:")
for i, (wk, n, wr, sr, total, er) in enumerate(results_sorted):
    if wk in test_wks:
        pct = (i + 1) / len(results) * 100
        print(f"  {wk}: rank {i+1}/{len(results)} ({pct:.0f}th percentile), ${total:,.0f}")
