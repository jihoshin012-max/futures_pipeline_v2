# archetype: rotational
"""
rotational-NQ-scale-detection-step34.py — Track B2 Steps 1+2: Tag cycles with
EMA features and analyze direction × EMA correlation.

Step 1: Run A+B config on 5 test weeks using official frozen params. At each
        entry bar, compute ema_spread, d_ema9, d_spread, d2_ema9 from completed
        250-tick bars prior to entry. Tag each cycle. Save tagged CSV.

Step 2: For each EMA feature, split by trade direction and compute SR, WR, E[R].
        Per-week breakdown. Test BOTH directions (block against-trend AND block
        with-trend — Track B inversion check).

Kill gate: no direction × EMA combination shows E[R] spread > $10 → stop.

Prompt: rotational-NQ-prompt-ema-directional-b2.md Steps 1-2
Depends on: Step 0 (step33.py) — test week selection.
"""
from __future__ import annotations

import csv
import datetime
import importlib
import sys
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, r"c:\Projects\futures_pipeline\lab")

_engine = importlib.import_module("rotational-NQ-scale-detection-engine")
_sweep = importlib.import_module("rotational-NQ-scale-detection-sweep")
load_bars_extended = _engine.load_bars_extended
aggregate_to_ntick = _engine.aggregate_to_ntick
compute_regime_signals = _engine.compute_regime_signals
compute_entry_signals = _engine.compute_entry_signals
map_signal_to_ticks = _engine.map_signal_to_ticks
run_sim_filtered = _sweep.run_sim_filtered


# ---------------------------------------------------------------------------
#  Config — from frozen params
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

COMM = 3.50  # per RT per mini
TV = 5.0     # tick value

TEST_WEEKS = {
    "2025-W40": "WEAKEST",
    "2025-W48": "LOW",
    "2025-W41": "MID",
    "2025-W46": "GOOD",
    "2025-W47": "BEST",
}

OUTPUT_DIR = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")
DATA_FILE = r"C:\Projects\futures_pipeline\data\NQ-1tick-calibration.csv"


# ---------------------------------------------------------------------------
#  EMA computation
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
#  Signal precomputation (A+B filter + EMA features)
# ---------------------------------------------------------------------------
def precompute_signals(bars, bar_size, lookback):
    agg_bars, tick_to_agg = aggregate_to_ntick(bars, bar_size)
    regime = compute_regime_signals(agg_bars, lookback=lookback)
    entry = compute_entry_signals(agg_bars, lookback=lookback)

    n_agg = agg_bars["n"]
    a_high = agg_bars["high"]
    a_low = agg_bars["low"]
    last = bars["last"]

    # Previous bar H/L/range for fade_confirm
    prev_high = np.full(n_agg, np.nan, dtype=np.float64)
    prev_low = np.full(n_agg, np.nan, dtype=np.float64)
    prev_range = np.full(n_agg, np.nan, dtype=np.float64)
    for ai in range(1, n_agg):
        prev_high[ai] = float(a_high[ai - 1])
        prev_low[ai] = float(a_low[ai - 1])
        rng = float(a_high[ai - 1]) - float(a_low[ai - 1])
        prev_range[ai] = rng if rng > 0 else np.nan

    # EMA features on completed agg bars
    close_agg = np.array(agg_bars["last"], dtype=np.float64)
    ema9_agg = ema(close_agg, 9)
    ema21_agg = ema(close_agg, 21)

    spread_agg = np.full(n_agg, np.nan, dtype=np.float64)
    d_ema9_agg = np.full(n_agg, np.nan, dtype=np.float64)
    d_spread_agg = np.full(n_agg, np.nan, dtype=np.float64)
    d2_ema9_agg = np.full(n_agg, np.nan, dtype=np.float64)

    for i in range(n_agg):
        if not np.isnan(ema9_agg[i]) and not np.isnan(ema21_agg[i]):
            spread_agg[i] = ema9_agg[i] - ema21_agg[i]
    for i in range(1, n_agg):
        if not np.isnan(spread_agg[i]) and not np.isnan(spread_agg[i - 1]):
            d_spread_agg[i] = spread_agg[i] - spread_agg[i - 1]
        if not np.isnan(ema9_agg[i]) and not np.isnan(ema9_agg[i - 1]):
            d_ema9_agg[i] = ema9_agg[i] - ema9_agg[i - 1]
    for i in range(2, n_agg):
        if not np.isnan(d_ema9_agg[i]) and not np.isnan(d_ema9_agg[i - 1]):
            d2_ema9_agg[i] = d_ema9_agg[i] - d_ema9_agg[i - 1]

    return {
        # A+B filter signals
        "choppiness": map_signal_to_ticks(regime["choppiness"], tick_to_agg),
        "dr2": map_signal_to_ticks(entry["dr2"], tick_to_agg),
        "dslope": map_signal_to_ticks(entry["dslope"], tick_to_agg),
        "prev_high": prev_high[tick_to_agg],
        "prev_low": prev_low[tick_to_agg],
        "prev_range": prev_range[tick_to_agg],
        "last": last,
        # EMA features (from completed bars — causal)
        "ema_spread": map_signal_to_ticks(spread_agg, tick_to_agg),
        "d_ema9": map_signal_to_ticks(d_ema9_agg, tick_to_agg),
        "d_spread": map_signal_to_ticks(d_spread_agg, tick_to_agg),
        "d2_ema9": map_signal_to_ticks(d2_ema9_agg, tick_to_agg),
        # Required by filter injection
        "warmup_ticks": {"dominant_scale": 0, "asymmetry": 0},
    }


# ---------------------------------------------------------------------------
#  A+B filter
# ---------------------------------------------------------------------------
def make_ab_filter():
    def f(signals, i, direction, step_dist):
        chop = signals["choppiness"][i]
        if np.isnan(chop):
            return True
        if chop >= CHOP_THRESHOLD:
            return False
        dr2 = signals["dr2"][i]
        if np.isnan(dr2):
            return True
        if dr2 > DR2_MAX:
            return False
        ds = signals["dslope"][i]
        if np.isnan(ds):
            return True
        if ds > DSLOPE_MAX:
            return False
        prev_range = signals["prev_range"][i]
        if np.isnan(prev_range):
            return True
        entry_price = float(signals["last"][i])
        if direction == 1:
            fc = (entry_price - float(signals["prev_low"][i])) / prev_range
        else:
            fc = (float(signals["prev_high"][i]) - entry_price) / prev_range
        return fc < FC_MAX
    return f


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------
def get_week(seed_dt: str) -> str:
    dt = seed_dt[:10]
    d = datetime.date(int(dt[:4]), int(dt[5:7]), int(dt[8:10]))
    iso = d.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def net_pnl(c: dict) -> float:
    return c["pnl_ticks"] * TV - COMM * max(c.get("max_position", 1), 1)


def metrics(cyc_list: list[dict]) -> dict:
    if not cyc_list:
        return {"n": 0, "wr": 0.0, "sr": 0.0, "er": 0.0, "pnl": 0.0}
    n = len(cyc_list)
    wins = sum(1 for c in cyc_list if c["_net_pnl"] >= 0)
    stops = sum(1 for c in cyc_list if c["exit_type"] == "HARD_STOP")
    pnl = sum(c["_net_pnl"] for c in cyc_list)
    return {"n": n, "wr": wins / n, "sr": stops / n, "er": pnl / n, "pnl": pnl}


def print_metrics(label: str, cycs: list[dict], indent: int = 4):
    m = metrics(cycs)
    prefix = " " * indent
    if m["n"] == 0:
        print(f"{prefix}{label}: 0 cycles")
        return m
    print(f"{prefix}{label}: {m['n']} cyc | {m['wr']:.0%} WR | {m['sr']:.0%} SR | "
          f"E[R]=${m['er']:.2f} | PnL=${m['pnl']:,.0f}")
    return m


def per_week_breakdown(cycs: list[dict], label: str):
    weeks = defaultdict(list)
    for c in cycs:
        weeks[c["_week"]].append(c)
    print(f"\n    Per-week E[R] for {label}:")
    for wk in sorted(weeks.keys()):
        m = metrics(weeks[wk])
        cat = TEST_WEEKS.get(wk, "")
        print(f"      {wk} ({cat:>8}): {m['n']:>4} cyc, E[R]=${m['er']:>8.2f}, "
              f"WR={m['wr']:.0%}, SR={m['sr']:.0%}")


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 70)
    print("Track B2 Steps 1+2: Tag cycles with EMA features + correlation analysis")
    print("=" * 70)

    # Load data
    print("\nLoading P1 data...")
    t0 = time.time()
    bars = load_bars_extended(DATA_FILE)
    print(f"  {bars['n']} ticks ({time.time()-t0:.0f}s)")

    # Precompute signals
    print("\nPrecomputing signals (A+B + EMA features)...")
    t1 = time.time()
    signals = precompute_signals(bars, BAR_SIZE, LB)
    print(f"  Done ({time.time()-t1:.0f}s)")

    # Run sim with A+B filter on ALL P1 (we tag all, analyze test weeks)
    print("\nRunning A+B filtered sim...")
    t1 = time.time()
    all_cycles = run_sim_filtered(
        bars, SD, HS, MAX_FADES, MAX_LEVELS, MAX_CONTRACT_SIZE,
        signal_arrays=signals, filter_fn=make_ab_filter()
    )
    print(f"  {len(all_cycles)} total cycles ({time.time()-t1:.0f}s)")

    # Tag each cycle with EMA features + week + net PnL
    valid_cycles = []
    for c in all_cycles:
        bar_idx = c["seed_bar"]
        c["_ema_spread"] = float(signals["ema_spread"][bar_idx])
        c["_d_ema9"] = float(signals["d_ema9"][bar_idx])
        c["_d_spread"] = float(signals["d_spread"][bar_idx])
        c["_d2_ema9"] = float(signals["d2_ema9"][bar_idx])
        c["_net_pnl"] = net_pnl(c)
        c["_week"] = get_week(c["seed_dt"])
        if (not np.isnan(c["_ema_spread"]) and not np.isnan(c["_d_ema9"])
                and not np.isnan(c["_d_spread"])):
            valid_cycles.append(c)

    print(f"  {len(valid_cycles)} cycles with valid EMA data")

    # Filter to test weeks
    test_cycles = [c for c in valid_cycles if c["_week"] in TEST_WEEKS]
    all_valid = valid_cycles  # keep full P1 for reference
    print(f"  {len(test_cycles)} cycles in 5 test weeks")

    # =========================================================================
    # STEP 1: Save tagged cycles
    # =========================================================================
    print(f"\n{'='*70}")
    print("STEP 1: Save tagged cycles")
    print(f"{'='*70}")

    out_path = OUTPUT_DIR / "b2-ema-directional-tagged-cycles.csv"
    fields = ["week", "week_cat", "cycle_id", "direction", "exit_type",
              "max_position", "pnl_ticks", "net_pnl",
              "ema_spread", "d_ema9", "d_spread", "d2_ema9",
              "seed_dt", "seed_bar"]
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for c in test_cycles:
            w.writerow({
                "week": c["_week"],
                "week_cat": TEST_WEEKS[c["_week"]],
                "cycle_id": c["cycle_id"],
                "direction": c["direction"],
                "exit_type": c["exit_type"],
                "max_position": c["max_position"],
                "pnl_ticks": round(c["pnl_ticks"], 2),
                "net_pnl": round(c["_net_pnl"], 2),
                "ema_spread": round(c["_ema_spread"], 4),
                "d_ema9": round(c["_d_ema9"], 4),
                "d_spread": round(c["_d_spread"], 4),
                "d2_ema9": round(c["_d2_ema9"], 4) if not np.isnan(c["_d2_ema9"]) else "",
                "seed_dt": c["seed_dt"],
                "seed_bar": c["seed_bar"],
            })
    print(f"  Saved {len(test_cycles)} tagged cycles to {out_path}")

    # =========================================================================
    # STEP 2: Direction × EMA correlation analysis
    # =========================================================================
    print(f"\n{'='*70}")
    print("STEP 2: Direction × EMA correlation analysis")
    print(f"{'='*70}")

    # --- Use ALL P1 cycles for the correlation analysis (more statistical power) ---
    cycles = all_valid
    longs = [c for c in cycles if c["direction"] == "LONG"]
    shorts = [c for c in cycles if c["direction"] == "SHORT"]
    print(f"\n  Full P1: {len(cycles)} valid cycles ({len(longs)} LONG, {len(shorts)} SHORT)")

    # === Analysis 1: ema_spread sign × direction ===
    print(f"\n  {'='*60}")
    print("  ANALYSIS 1: ema_spread sign × trade direction")
    print(f"  {'='*60}")

    for label, subset in [("ALL", cycles), ("LONG", longs), ("SHORT", shorts)]:
        print(f"\n  {label} trades:")
        pos = [c for c in subset if c["_ema_spread"] > 0]
        neg = [c for c in subset if c["_ema_spread"] <= 0]
        print_metrics("ema_spread > 0 (EMA bullish)", pos)
        print_metrics("ema_spread <= 0 (EMA bearish)", neg)

    # === Analysis 2: Three-state gate with threshold sweep ===
    print(f"\n  {'='*60}")
    print("  ANALYSIS 2: Three-state EMA gate (threshold sweep)")
    print(f"  {'='*60}")

    thresholds = [0.5, 1.0, 2.0, 3.0, 5.0]
    best_spread = 0.0
    best_thresh = 0.0

    for thresh in thresholds:
        ema_up = [c for c in cycles if c["_ema_spread"] > thresh]
        ema_down = [c for c in cycles if c["_ema_spread"] < -thresh]
        neutral = [c for c in cycles if abs(c["_ema_spread"]) <= thresh]

        up_longs = [c for c in ema_up if c["direction"] == "LONG"]
        up_shorts = [c for c in ema_up if c["direction"] == "SHORT"]
        down_longs = [c for c in ema_down if c["direction"] == "LONG"]
        down_shorts = [c for c in ema_down if c["direction"] == "SHORT"]

        print(f"\n  Threshold: ±{thresh} pts")
        print(f"  EMA UP zone ({len(ema_up)} cycles):")
        m_ul = print_metrics("LONG  (WITH EMA)", up_longs)
        m_us = print_metrics("SHORT (AGAINST EMA)", up_shorts)
        print(f"  EMA DOWN zone ({len(ema_down)} cycles):")
        m_dl = print_metrics("LONG  (AGAINST EMA)", down_longs)
        m_ds = print_metrics("SHORT (WITH EMA)", down_shorts)
        print(f"  NEUTRAL zone ({len(neutral)} cycles):")
        print_metrics("LONG", [c for c in neutral if c["direction"] == "LONG"])
        print_metrics("SHORT", [c for c in neutral if c["direction"] == "SHORT"])

        # Compute with-trend vs against-trend E[R] spread
        with_trend = up_longs + down_shorts
        against_trend = up_shorts + down_longs
        m_with = metrics(with_trend)
        m_against = metrics(against_trend)
        spread_er = m_with["er"] - m_against["er"]
        print(f"\n    >>> WITH-TREND:    {m_with['n']:>4} cyc, E[R]=${m_with['er']:.2f}")
        print(f"    >>> AGAINST-TREND: {m_against['n']:>4} cyc, E[R]=${m_against['er']:.2f}")
        print(f"    >>> SPREAD: ${spread_er:.2f}")

        # INVERSION CHECK: what if we block WITH-trend instead?
        print(f"    >>> INVERSION: block with-trend keeps {m_against['n']} cyc E[R]=${m_against['er']:.2f}")
        print(f"    >>> INVERSION: block against-trend keeps {m_with['n']} cyc E[R]=${m_with['er']:.2f}")

        if abs(spread_er) > abs(best_spread):
            best_spread = spread_er
            best_thresh = thresh

    # === Analysis 3: d_ema9 × direction ===
    print(f"\n  {'='*60}")
    print("  ANALYSIS 3: d_ema9 (EMA9 slope) × direction")
    print(f"  {'='*60}")

    for label, subset in [("LONG", longs), ("SHORT", shorts)]:
        rising = [c for c in subset if c["_d_ema9"] > 0]
        falling = [c for c in subset if c["_d_ema9"] <= 0]
        print(f"\n  {label} trades:")
        m_r = print_metrics("d_ema9 > 0 (EMA9 rising)", rising)
        m_f = print_metrics("d_ema9 <= 0 (EMA9 falling)", falling)
        spread = m_r["er"] - m_f["er"]
        print(f"    Spread: ${spread:.2f}")

    # d_ema9 with-trend vs against-trend
    d_with = [c for c in cycles
              if (c["direction"] == "LONG" and c["_d_ema9"] > 0)
              or (c["direction"] == "SHORT" and c["_d_ema9"] <= 0)]
    d_against = [c for c in cycles
                 if (c["direction"] == "LONG" and c["_d_ema9"] <= 0)
                 or (c["direction"] == "SHORT" and c["_d_ema9"] > 0)]
    m_dw = metrics(d_with)
    m_da = metrics(d_against)
    print(f"\n    d_ema9 WITH-TREND:    {m_dw['n']:>4} cyc, E[R]=${m_dw['er']:.2f}")
    print(f"    d_ema9 AGAINST-TREND: {m_da['n']:>4} cyc, E[R]=${m_da['er']:.2f}")
    print(f"    d_ema9 SPREAD: ${m_dw['er'] - m_da['er']:.2f}")

    # === Analysis 4: d_spread × direction ===
    print(f"\n  {'='*60}")
    print("  ANALYSIS 4: d_spread (spread widening/narrowing) × direction")
    print(f"  {'='*60}")

    for label, subset in [("LONG", longs), ("SHORT", shorts)]:
        widen = [c for c in subset if c["_d_spread"] > 0]
        narrow = [c for c in subset if c["_d_spread"] <= 0]
        print(f"\n  {label} trades:")
        m_w = print_metrics("d_spread > 0 (widening)", widen)
        m_n = print_metrics("d_spread <= 0 (narrowing)", narrow)
        print(f"    Spread: ${m_w['er'] - m_n['er']:.2f}")

    # === Analysis 5: d2_ema9 × direction ===
    print(f"\n  {'='*60}")
    print("  ANALYSIS 5: d2_ema9 (EMA9 curvature) × direction")
    print(f"  {'='*60}")

    # Filter to valid d2_ema9
    d2_valid = [c for c in cycles if not np.isnan(c["_d2_ema9"])]
    d2_longs = [c for c in d2_valid if c["direction"] == "LONG"]
    d2_shorts = [c for c in d2_valid if c["direction"] == "SHORT"]
    print(f"  {len(d2_valid)} cycles with valid d2_ema9")

    for label, subset in [("LONG", d2_longs), ("SHORT", d2_shorts)]:
        accel = [c for c in subset if c["_d2_ema9"] > 0]
        decel = [c for c in subset if c["_d2_ema9"] <= 0]
        print(f"\n  {label} trades:")
        m_a = print_metrics("d2_ema9 > 0 (accelerating)", accel)
        m_d = print_metrics("d2_ema9 <= 0 (decelerating)", decel)
        print(f"    Spread: ${m_a['er'] - m_d['er']:.2f}")

    # d2_ema9 with-trend (accel up for LONG, decel/accel down for SHORT)
    d2_with = [c for c in d2_valid
               if (c["direction"] == "LONG" and c["_d2_ema9"] > 0)
               or (c["direction"] == "SHORT" and c["_d2_ema9"] <= 0)]
    d2_against = [c for c in d2_valid
                  if (c["direction"] == "LONG" and c["_d2_ema9"] <= 0)
                  or (c["direction"] == "SHORT" and c["_d2_ema9"] > 0)]
    m_d2w = metrics(d2_with)
    m_d2a = metrics(d2_against)
    print(f"\n    d2_ema9 WITH-TREND:    {m_d2w['n']:>4} cyc, E[R]=${m_d2w['er']:.2f}")
    print(f"    d2_ema9 AGAINST-TREND: {m_d2a['n']:>4} cyc, E[R]=${m_d2a['er']:.2f}")
    print(f"    d2_ema9 SPREAD: ${m_d2w['er'] - m_d2a['er']:.2f}")

    # === Analysis 6: Per-week breakdown for best features ===
    print(f"\n  {'='*60}")
    print("  ANALYSIS 6: Per-week breakdown — ema_spread with/against trend")
    print(f"  {'='*60}")

    # Use best threshold from Analysis 2
    print(f"\n  Using ema_spread threshold: ±{best_thresh} pts")
    for thresh in [best_thresh]:
        with_trend = [c for c in cycles
                      if (c["direction"] == "LONG" and c["_ema_spread"] > thresh)
                      or (c["direction"] == "SHORT" and c["_ema_spread"] < -thresh)]
        against_trend = [c for c in cycles
                         if (c["direction"] == "LONG" and c["_ema_spread"] < -thresh)
                         or (c["direction"] == "SHORT" and c["_ema_spread"] > thresh)]
        neutral = [c for c in cycles if abs(c["_ema_spread"]) <= thresh]

        per_week_breakdown(with_trend, f"WITH-TREND (ema_spread ±{thresh})")
        per_week_breakdown(against_trend, f"AGAINST-TREND (ema_spread ±{thresh})")
        per_week_breakdown(neutral, f"NEUTRAL (|ema_spread| <= {thresh})")

    # Also per-week for d_ema9
    print(f"\n  Per-week breakdown — d_ema9 with/against trend")
    per_week_breakdown(d_with, "d_ema9 WITH-TREND")
    per_week_breakdown(d_against, "d_ema9 AGAINST-TREND")

    # === KILL GATE CHECK ===
    print(f"\n  {'='*70}")
    print("  KILL GATE: direction × EMA E[R] spread > $10?")
    print(f"  {'='*70}")

    # Check all features
    checks = []

    # ema_spread with best threshold
    with_trend = [c for c in cycles
                  if (c["direction"] == "LONG" and c["_ema_spread"] > best_thresh)
                  or (c["direction"] == "SHORT" and c["_ema_spread"] < -best_thresh)]
    against_trend = [c for c in cycles
                     if (c["direction"] == "LONG" and c["_ema_spread"] < -best_thresh)
                     or (c["direction"] == "SHORT" and c["_ema_spread"] > best_thresh)]
    m_w = metrics(with_trend)
    m_a = metrics(against_trend)
    spread1 = m_w["er"] - m_a["er"]
    checks.append(("ema_spread", best_thresh, spread1, m_w["n"], m_a["n"]))

    # d_ema9
    spread2 = m_dw["er"] - m_da["er"]
    checks.append(("d_ema9", 0, spread2, m_dw["n"], m_da["n"]))

    # d2_ema9
    spread3 = m_d2w["er"] - m_d2a["er"]
    checks.append(("d2_ema9", 0, spread3, m_d2w["n"], m_d2a["n"]))

    any_pass = False
    for feat, thresh, spread, n_with, n_against in checks:
        status = "PASS" if abs(spread) > 10 else "FAIL"
        if abs(spread) > 10:
            any_pass = True
        direction = "with>against" if spread > 0 else "INVERTED (against>with)"
        print(f"    {feat} (thresh={thresh}): spread=${spread:.2f} "
              f"({n_with} with, {n_against} against) — {direction} — {status}")

    if not any_pass:
        print(f"\n  *** KILL GATE TRIGGERED: No feature shows E[R] spread > $10 ***")
        print(f"  *** Track B2 DIES here. Record findings and stop. ***")
    else:
        print(f"\n  Kill gate PASSED. At least one feature shows spread > $10.")

    print(f"\nRuntime: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
