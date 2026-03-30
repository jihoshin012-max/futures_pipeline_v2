# archetype: rotational
"""Compute Sharpe, Sortino, Calmar for P1 and P2 (baseline + filtered)."""
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


def make_filter(thresh):
    def f(signals, i, direction, step_dist):
        c = signals["choppiness"][i]
        if np.isnan(c):
            return True
        return c < thresh
    return f


def daily_pnl(cycles):
    days = defaultdict(float)
    for c in cycles:
        dt = c["seed_dt"][:10]
        pos = max(c.get("max_position", 1), 1)
        pnl = c["pnl_ticks"] * TICK_VALUE - COMM * pos
        days[dt] += pnl
    sorted_days = sorted(days.items())
    return np.array([v for _, v in sorted_days])


def compute_ratios(daily_returns, label):
    n_days = len(daily_returns)
    ann_factor = np.sqrt(252)

    mean_daily = np.mean(daily_returns)
    std_daily = np.std(daily_returns, ddof=1)

    # Sharpe (risk-free = 0 for futures)
    sharpe = (mean_daily / std_daily) * ann_factor if std_daily > 0 else 0

    # Sortino (downside deviation only)
    downside = daily_returns[daily_returns < 0]
    downside_std = np.std(downside, ddof=1) if len(downside) > 1 else 0.001
    sortino = (mean_daily / downside_std) * ann_factor if downside_std > 0 else 0

    # Calmar (annualized return / max drawdown)
    total_pnl = np.sum(daily_returns)
    equity = np.cumsum(daily_returns)
    peak = np.maximum.accumulate(equity)
    max_dd = np.max(peak - equity)
    ann_return = total_pnl * (252 / n_days)
    calmar = ann_return / max_dd if max_dd > 0 else 0

    print(f"\n  === {label} ({n_days} trading days) ===")
    print(f"  Total PnL:        ${total_pnl:,.0f}")
    print(f"  Annualized PnL:   ${ann_return:,.0f}")
    print(f"  Daily mean:       ${mean_daily:,.2f}")
    print(f"  Daily std:        ${std_daily:,.2f}")
    print(f"  Max DD:           ${max_dd:,.0f}")
    print(f"  Sharpe:           {sharpe:.2f}")
    print(f"  Sortino:          {sortino:.2f}")
    print(f"  Calmar:           {calmar:.2f}")
    print(f"  Win days:         {np.sum(daily_returns > 0)}/{n_days} "
          f"({np.mean(daily_returns > 0):.0%})")
    print(f"  Worst day:        ${np.min(daily_returns):,.0f}")
    print(f"  Best day:         ${np.max(daily_returns):,.0f}")

    return {"sharpe": sharpe, "sortino": sortino, "calmar": calmar,
            "max_dd": max_dd, "ann_return": ann_return}


def run_period(bar_file, label):
    print(f"\nLoading {label}...")
    bars = load_bars_extended(bar_file)
    agg, t2a = aggregate_to_ntick(bars, BAR_SIZE)
    feat = compute_regime_signals(agg, lookback=LB)
    chop = map_signal_to_ticks(feat["choppiness"], t2a)
    sig = {"choppiness": chop, "warmup_ticks": {"dominant_scale": 0, "asymmetry": 0}}

    print(f"Running {label} baseline + filtered...")
    bl = run_sim_filtered(bars, SD, HS, MAX_FADES, MAX_LEVELS, MAX_CS)
    fl = run_sim_filtered(bars, SD, HS, MAX_FADES, MAX_LEVELS, MAX_CS,
                          signal_arrays=sig, filter_fn=make_filter(0.10))

    r_bl = compute_ratios(daily_pnl(bl), f"{label} BASELINE")
    r_fl = compute_ratios(daily_pnl(fl), f"{label} FILTERED (chop<0.10)")
    return r_bl, r_fl


t0 = time.time()
r_bl_p1, r_fl_p1 = run_period(
    r"C:\Projects\futures_pipeline\data\NQ-1tick-calibration.csv", "P1")
r_bl_p2, r_fl_p2 = run_period(
    r"C:\Projects\futures_pipeline\data\NQ-1tick-holdout.csv", "P2")

print(f"\n{'='*70}")
print("SUMMARY")
print(f"{'='*70}")
fmt = "{:>20} {:>8} {:>8} {:>8} {:>12} {:>10}"
print(fmt.format("", "Sharpe", "Sortino", "Calmar", "Ann PnL", "Max DD"))
for lbl, r in [("P1 Baseline", r_bl_p1), ("P1 Filtered", r_fl_p1),
                ("P2 Baseline", r_bl_p2), ("P2 Filtered", r_fl_p2)]:
    print(fmt.format(lbl, f"{r['sharpe']:.2f}", f"{r['sortino']:.2f}",
                     f"{r['calmar']:.2f}", f"${r['ann_return']:,.0f}",
                     f"${r['max_dd']:,.0f}"))

print(f"\nRuntime: {time.time()-t0:.0f}s")
