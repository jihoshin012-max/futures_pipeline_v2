# archetype: rotational
"""No-add P2 test: max_levels=0, max_cs=1, A+B filters."""
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

SD, HS, MF = 10.0, 60.0, 0
BS, LB = 250, 3


def make_filter():
    def f(s, i, d, sd):
        c = s["choppiness"][i]
        if np.isnan(c):
            return True
        if c >= 0.10:
            return False
        dr2 = s["dr2"][i]
        dslope = s["dslope"][i]
        fc = s["fade_confirm"][i]
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


def setup_signals(bars):
    agg, t2a = aggregate_to_ntick(bars, BS)
    feat = compute_regime_signals(agg, lookback=LB)
    chop = map_signal_to_ticks(feat["choppiness"], t2a)

    dr2_agg = np.full(agg["n"], np.nan)
    dslope_agg = np.full(agg["n"], np.nan)
    for i in range(1, agg["n"]):
        if not np.isnan(feat["r2"][i]) and not np.isnan(feat["r2"][i - 1]):
            dr2_agg[i] = feat["r2"][i] - feat["r2"][i - 1]
        abs_slope = np.abs(feat["slope"])
        if not np.isnan(abs_slope[i]) and not np.isnan(abs_slope[i - 1]):
            dslope_agg[i] = abs_slope[i] - abs_slope[i - 1]

    n = bars["n"]
    fc_ticks = np.full(n, np.nan)
    for i in range(n):
        a = t2a[i]
        if a > 0:
            rng = agg["high"][a - 1] - agg["low"][a - 1]
            if rng > 0.001:
                fc_ticks[i] = (float(bars["last"][i]) - agg["low"][a - 1]) / rng

    return {
        "choppiness": chop,
        "dr2": map_signal_to_ticks(dr2_agg, t2a),
        "dslope": map_signal_to_ticks(dslope_agg, t2a),
        "fade_confirm": fc_ticks,
        "warmup_ticks": {"dominant_scale": 0, "asymmetry": 0},
    }


def calc_pnl(cycs):
    return sum(c["pnl_ticks"] * TV - COMM * max(c.get("max_position", 1), 1) for c in cycs)


def metrics(cycs, label):
    n = len(cycs)
    stops = sum(1 for c in cycs if c["exit_type"] == "HARD_STOP")
    wins = sum(1 for c in cycs if c["exit_type"] != "HARD_STOP")
    total = calc_pnl(cycs)
    print(f"  {label}: {n} cyc | {100*wins/n:.0f}% WR | {100*stops/n:.0f}% SR | "
          f"${total:,.0f} | E[R]=${total/n:.2f}")
    return n, wins, stops, total


# === P1 ===
print("Loading P1...")
bars_p1 = load_bars_extended(r"C:\Projects\futures_pipeline\data\NQ-1tick-calibration.csv")
sig_p1 = setup_signals(bars_p1)

print("\nP1 — A+B with depth_1 (baseline):")
bl_p1 = run_sim_filtered(bars_p1, SD, HS, MF, 1, 2, signal_arrays=sig_p1, filter_fn=make_filter())
bl_p1_n, _, _, bl_p1_pnl = metrics(bl_p1, "depth_1")

print("P1 — A+B with NO ADD:")
na_p1 = run_sim_filtered(bars_p1, SD, HS, MF, 0, 1, signal_arrays=sig_p1, filter_fn=make_filter())
na_p1_n, _, _, na_p1_pnl = metrics(na_p1, "no_add ")

# === P2 ===
print("\nLoading P2...")
bars_p2 = load_bars_extended(r"C:\Projects\futures_pipeline\data\NQ-1tick-holdout.csv")
sig_p2 = setup_signals(bars_p2)

print("\nP2 — A+B with depth_1 (baseline):")
bl_p2 = run_sim_filtered(bars_p2, SD, HS, MF, 1, 2, signal_arrays=sig_p2, filter_fn=make_filter())
bl_p2_n, _, _, bl_p2_pnl = metrics(bl_p2, "depth_1")

print("P2 — A+B with NO ADD:")
na_p2 = run_sim_filtered(bars_p2, SD, HS, MF, 0, 1, signal_arrays=sig_p2, filter_fn=make_filter())
na_p2_n, _, _, na_p2_pnl = metrics(na_p2, "no_add ")

# === Summary ===
print(f"\n{'='*60}")
print("SUMMARY")
print(f"{'='*60}")
fmt = "{:>15} {:>8} {:>12} {:>8}"
print(fmt.format("", "Cycles", "Total PnL", "E[R]"))
print(fmt.format("P1 depth_1", str(bl_p1_n), f"${bl_p1_pnl:,.0f}", f"${bl_p1_pnl/bl_p1_n:.2f}"))
print(fmt.format("P1 no_add", str(na_p1_n), f"${na_p1_pnl:,.0f}", f"${na_p1_pnl/na_p1_n:.2f}"))
print(fmt.format("P2 depth_1", str(bl_p2_n), f"${bl_p2_pnl:,.0f}", f"${bl_p2_pnl/bl_p2_n:.2f}"))
print(fmt.format("P2 no_add", str(na_p2_n), f"${na_p2_pnl:,.0f}", f"${na_p2_pnl/na_p2_n:.2f}"))
print(f"\nP1 delta: ${na_p1_pnl - bl_p1_pnl:+,.0f} ({100*(na_p1_pnl/bl_p1_pnl - 1):+.1f}%)")
print(f"P2 delta: ${na_p2_pnl - bl_p2_pnl:+,.0f} ({100*(na_p2_pnl/bl_p2_pnl - 1):+.1f}%)")
