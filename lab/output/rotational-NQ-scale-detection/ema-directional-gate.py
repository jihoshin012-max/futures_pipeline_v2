# archetype: rotational
"""EMA directional gate analysis: does EMA state at entry predict
whether LONG or SHORT trades perform better?

Three-state logic:
  ema_spread > threshold  → EMA says up (test: gate LONGs or SHORTs?)
  ema_spread < -threshold → EMA says down (test: gate LONGs or SHORTs?)
  |ema_spread| <= threshold → neutral (take either direction)

Also tests d_spread and d_ema9 as directional signals.
"""
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
TV = 5.0

SD, HS, MF, ML, MCS = 10.0, 60.0, 0, 1, 2
BS, LB = 250, 3


def ema(data, period):
    out = np.full(len(data), np.nan, dtype=np.float64)
    k = 2.0 / (period + 1)
    first_valid = -1
    for i in range(len(data)):
        if np.isnan(data[i]):
            continue
        if first_valid < 0:
            out[i] = data[i]
            first_valid = i
        else:
            out[i] = data[i] * k + out[i - 1] * (1 - k)
    return out


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
    abs_slope = np.abs(feat["slope"])
    for i in range(1, agg["n"]):
        if not np.isnan(feat["r2"][i]) and not np.isnan(feat["r2"][i - 1]):
            dr2_agg[i] = feat["r2"][i] - feat["r2"][i - 1]
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

    # Compute EMAs on agg bar closes
    close_agg = np.array(agg["last"], dtype=np.float64)
    ema9_agg = ema(close_agg, 9)
    ema21_agg = ema(close_agg, 21)

    spread_agg = np.full(agg["n"], np.nan)
    d_spread_agg = np.full(agg["n"], np.nan)
    d_ema9_agg = np.full(agg["n"], np.nan)
    for i in range(agg["n"]):
        if not np.isnan(ema9_agg[i]) and not np.isnan(ema21_agg[i]):
            spread_agg[i] = ema9_agg[i] - ema21_agg[i]
    for i in range(1, agg["n"]):
        if not np.isnan(spread_agg[i]) and not np.isnan(spread_agg[i - 1]):
            d_spread_agg[i] = spread_agg[i] - spread_agg[i - 1]
        if not np.isnan(ema9_agg[i]) and not np.isnan(ema9_agg[i - 1]):
            d_ema9_agg[i] = ema9_agg[i] - ema9_agg[i - 1]

    return {
        "choppiness": chop,
        "dr2": map_signal_to_ticks(dr2_agg, t2a),
        "dslope": map_signal_to_ticks(dslope_agg, t2a),
        "fade_confirm": fc_ticks,
        "ema_spread": map_signal_to_ticks(spread_agg, t2a),
        "d_spread": map_signal_to_ticks(d_spread_agg, t2a),
        "d_ema9": map_signal_to_ticks(d_ema9_agg, t2a),
        "warmup_ticks": {"dominant_scale": 0, "asymmetry": 0},
    }, agg, t2a


def calc_pnl(cycs):
    return sum(c["pnl_ticks"] * TV - COMM * max(c.get("max_position", 1), 1) for c in cycs)


def group_metrics(cycs, label):
    if not cycs:
        print(f"    {label}: 0 cycles")
        return
    n = len(cycs)
    stops = sum(1 for c in cycs if c["exit_type"] == "HARD_STOP")
    total = calc_pnl(cycs)
    print(f"    {label}: {n} cyc | {100*(n-stops)/n:.0f}% WR | {100*stops/n:.0f}% SR | "
          f"${total:,.0f} | E[R]=${total/n:.2f}")


print("Loading P1...")
t0 = time.time()
bars = load_bars_extended(r"C:\Projects\futures_pipeline\data\NQ-1tick-calibration.csv")
print(f"  {bars['n']} ticks ({time.time()-t0:.0f}s)")

signals, agg, t2a = setup_signals(bars)

print("Running A+B filtered sim...")
cycles = run_sim_filtered(bars, SD, HS, MF, ML, MCS,
                          signal_arrays=signals, filter_fn=make_filter())
print(f"  {len(cycles)} cycles")

# Tag each cycle with EMA state at entry
for c in cycles:
    bar_idx = c["seed_bar"]
    c["ema_spread"] = float(signals["ema_spread"][bar_idx])
    c["d_spread"] = float(signals["d_spread"][bar_idx])
    c["d_ema9"] = float(signals["d_ema9"][bar_idx])

# Filter out cycles with NaN EMA values
cycles = [c for c in cycles if not np.isnan(c["ema_spread"])
          and not np.isnan(c["d_spread"]) and not np.isnan(c["d_ema9"])]
print(f"  {len(cycles)} cycles with valid EMA data")

longs = [c for c in cycles if c["direction"] == "LONG"]
shorts = [c for c in cycles if c["direction"] == "SHORT"]
print(f"  {len(longs)} LONG, {len(shorts)} SHORT")

# === Analysis 1: EMA spread x direction ===
print(f"\n{'='*70}")
print("ANALYSIS 1: ema_spread sign x trade direction")
print("  Does direction alignment with EMA trend predict outcome?")
print(f"{'='*70}")

for label, subset in [("ALL", cycles), ("LONG", longs), ("SHORT", shorts)]:
    spread_pos = [c for c in subset if c["ema_spread"] > 0]
    spread_neg = [c for c in subset if c["ema_spread"] <= 0]
    print(f"\n  {label} trades:")
    group_metrics(spread_pos, "ema_spread > 0 (EMA bullish)")
    group_metrics(spread_neg, "ema_spread <= 0 (EMA bearish)")

# === Analysis 2: Three-state gate with threshold sweep ===
print(f"\n{'='*70}")
print("ANALYSIS 2: Three-state EMA gate (threshold sweep)")
print("  spread > thresh: EMA up zone")
print("  spread < -thresh: EMA down zone")
print("  |spread| <= thresh: neutral zone (either direction)")
print(f"{'='*70}")

spread_vals = np.array([abs(c["ema_spread"]) for c in cycles])
thresholds = [0.5, 1.0, 2.0, 3.0, 5.0]

for thresh in thresholds:
    ema_up = [c for c in cycles if c["ema_spread"] > thresh]
    ema_down = [c for c in cycles if c["ema_spread"] < -thresh]
    neutral = [c for c in cycles if abs(c["ema_spread"]) <= thresh]

    # Within each zone, split by direction
    up_longs = [c for c in ema_up if c["direction"] == "LONG"]
    up_shorts = [c for c in ema_up if c["direction"] == "SHORT"]
    down_longs = [c for c in ema_down if c["direction"] == "LONG"]
    down_shorts = [c for c in ema_down if c["direction"] == "SHORT"]
    neut_longs = [c for c in neutral if c["direction"] == "LONG"]
    neut_shorts = [c for c in neutral if c["direction"] == "SHORT"]

    print(f"\n  Threshold: +/-{thresh} pts")
    print(f"  EMA UP zone ({len(ema_up)} cycles):")
    group_metrics(up_longs, "LONG (with EMA)")
    group_metrics(up_shorts, "SHORT (against EMA)")
    print(f"  EMA DOWN zone ({len(ema_down)} cycles):")
    group_metrics(down_longs, "LONG (against EMA)")
    group_metrics(down_shorts, "SHORT (with EMA)")
    print(f"  NEUTRAL zone ({len(neutral)} cycles):")
    group_metrics(neut_longs, "LONG")
    group_metrics(neut_shorts, "SHORT")

# === Analysis 3: d_spread (widening/narrowing) x direction ===
print(f"\n{'='*70}")
print("ANALYSIS 3: d_spread x direction")
print("  d_spread > 0 = spread widening (trend strengthening)")
print("  d_spread < 0 = spread narrowing (trend weakening)")
print(f"{'='*70}")

for label, subset in [("LONG", longs), ("SHORT", shorts)]:
    widen = [c for c in subset if c["d_spread"] > 0]
    narrow = [c for c in subset if c["d_spread"] <= 0]
    print(f"\n  {label} trades:")
    group_metrics(widen, "d_spread > 0 (widening)")
    group_metrics(narrow, "d_spread <= 0 (narrowing)")

# === Analysis 4: d_ema9 slope x direction ===
print(f"\n{'='*70}")
print("ANALYSIS 4: d_ema9 (short EMA slope) x direction")
print("  d_ema9 > 0 = short EMA rising")
print("  d_ema9 < 0 = short EMA falling")
print(f"{'='*70}")

for label, subset in [("LONG", longs), ("SHORT", shorts)]:
    rising = [c for c in subset if c["d_ema9"] > 0]
    falling = [c for c in subset if c["d_ema9"] <= 0]
    print(f"\n  {label} trades:")
    group_metrics(rising, "d_ema9 > 0 (EMA9 rising)")
    group_metrics(falling, "d_ema9 <= 0 (EMA9 falling)")

# === Analysis 5: Combined — "with trend" vs "against trend" ===
print(f"\n{'='*70}")
print("ANALYSIS 5: With EMA trend vs Against EMA trend")
print("  With = LONG when spread>0 OR SHORT when spread<0")
print("  Against = LONG when spread<0 OR SHORT when spread>0")
print(f"{'='*70}")

with_trend = [c for c in cycles
              if (c["direction"] == "LONG" and c["ema_spread"] > 0)
              or (c["direction"] == "SHORT" and c["ema_spread"] <= 0)]
against_trend = [c for c in cycles
                 if (c["direction"] == "LONG" and c["ema_spread"] <= 0)
                 or (c["direction"] == "SHORT" and c["ema_spread"] > 0)]

group_metrics(with_trend, "WITH EMA trend")
group_metrics(against_trend, "AGAINST EMA trend")

# Per-week breakdown for with/against
import datetime

def weekly_breakdown(cycs, label):
    weeks = defaultdict(list)
    for c in cycs:
        dt = c["seed_dt"][:10]
        d = datetime.date(int(dt[:4]), int(dt[5:7]), int(dt[8:10]))
        wk = f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"
        pnl = c["pnl_ticks"] * TV - COMM * max(c.get("max_position", 1), 1)
        weeks[wk].append(pnl)

    print(f"\n  Per-week E[R] for {label}:")
    for wk in sorted(weeks.keys()):
        pnls = weeks[wk]
        n = len(pnls)
        er = sum(pnls) / n
        print(f"    {wk}: {n} cyc, E[R]=${er:.2f}")

weekly_breakdown(with_trend, "WITH EMA trend")
weekly_breakdown(against_trend, "AGAINST EMA trend")

print(f"\nRuntime: {time.time()-t0:.0f}s")
