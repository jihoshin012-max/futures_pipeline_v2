# archetype: rotational
"""
rotational-NQ-scale-detection-step37.py — Track B2 extension: in-trade d2_ema9
analysis. Does cumulative d2 during the trade predict outcome?

For each A+B filtered cycle, compute:
  1. cum_d2: sum of d2_ema9 from entry bar to exit bar (net acceleration)
  2. d2_avg_3: rolling mean of d2_ema9 over last 3 agg bars at exit
  3. d2_avg_5: rolling mean of d2_ema9 over last 5 agg bars at exit
  4. d2_streak: consecutive bars of same d2 sign at exit
  5. d2_flip: did d2 sign flip relative to entry during the trade?

Split by direction alignment (cum_d2 aligned vs misaligned) and compute
E[R], WR, SR. Also split by exit type (REVERSAL vs HARD_STOP) to see
if deteriorating d2 predicts stops.

No sim fork needed — uses on_bar_in_trade callback to track d2 during trade.

Prompt: rotational-NQ-prompt-ema-directional-b2.md (extension)
"""
from __future__ import annotations

import datetime
import importlib
import sys
import time
from collections import defaultdict

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
#  Config
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

COMM = 3.50
TV = 5.0

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
#  Signal precomputation
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
        prev_high[ai] = float(a_high[ai - 1])
        prev_low[ai] = float(a_low[ai - 1])
        rng = float(a_high[ai - 1]) - float(a_low[ai - 1])
        prev_range[ai] = rng if rng > 0 else np.nan

    close_agg = np.array(agg_bars["last"], dtype=np.float64)
    ema9_agg = ema(close_agg, 9)

    # d_ema9 on agg bars
    d_ema9_agg = np.full(n_agg, np.nan, dtype=np.float64)
    for i in range(1, n_agg):
        if not np.isnan(ema9_agg[i]) and not np.isnan(ema9_agg[i - 1]):
            d_ema9_agg[i] = ema9_agg[i] - ema9_agg[i - 1]

    # d2_ema9 on agg bars
    d2_ema9_agg = np.full(n_agg, np.nan, dtype=np.float64)
    for i in range(2, n_agg):
        if not np.isnan(d_ema9_agg[i]) and not np.isnan(d_ema9_agg[i - 1]):
            d2_ema9_agg[i] = d_ema9_agg[i] - d_ema9_agg[i - 1]

    # d2_avg_3 and d2_avg_5 on agg bars
    d2_avg3_agg = np.full(n_agg, np.nan, dtype=np.float64)
    d2_avg5_agg = np.full(n_agg, np.nan, dtype=np.float64)
    for i in range(n_agg):
        # avg 3
        if i >= 2:
            vals = [d2_ema9_agg[i - j] for j in range(3)
                    if not np.isnan(d2_ema9_agg[i - j])]
            if len(vals) == 3:
                d2_avg3_agg[i] = sum(vals) / 3
        # avg 5
        if i >= 4:
            vals = [d2_ema9_agg[i - j] for j in range(5)
                    if not np.isnan(d2_ema9_agg[i - j])]
            if len(vals) == 5:
                d2_avg5_agg[i] = sum(vals) / 5

    # Map all to tick resolution
    d2_tick = map_signal_to_ticks(d2_ema9_agg, tick_to_agg)
    d2_avg3_tick = map_signal_to_ticks(d2_avg3_agg, tick_to_agg)
    d2_avg5_tick = map_signal_to_ticks(d2_avg5_agg, tick_to_agg)

    return {
        "choppiness": map_signal_to_ticks(regime["choppiness"], tick_to_agg),
        "dr2": map_signal_to_ticks(entry["dr2"], tick_to_agg),
        "dslope": map_signal_to_ticks(entry["dslope"], tick_to_agg),
        "prev_high": prev_high[tick_to_agg],
        "prev_low": prev_low[tick_to_agg],
        "prev_range": prev_range[tick_to_agg],
        "last": last,
        "d2_ema9": d2_tick,
        "d2_avg3": d2_avg3_tick,
        "d2_avg5": d2_avg5_tick,
        "tick_to_agg": tick_to_agg,
        "d2_ema9_agg": d2_ema9_agg,
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
#  In-trade d2 tracker
# ---------------------------------------------------------------------------
class D2Tracker:
    """Track d2_ema9 values during each trade via on_bar_in_trade callback."""

    def __init__(self, d2_tick, tick_to_agg, d2_agg):
        self.d2_tick = d2_tick
        self.tick_to_agg = tick_to_agg
        self.d2_agg = d2_agg

        # Per-cycle state
        self.current_cycle = -1
        self.entry_d2 = np.nan
        self.entry_agg = -1
        self.cum_d2 = 0.0
        self.d2_count = 0
        self.last_agg_seen = -1
        self.d2_sign_at_entry = 0  # +1 or -1
        self.flipped = False
        self.streak = 0
        self.streak_sign = 0

        # Results per cycle
        self.cycle_data = {}

    def on_bar(self, bar_idx, cycle_id, bar_offset, price,
               direction, pnl_ticks, mfe_ticks, mae_ticks):
        agg_idx = self.tick_to_agg[bar_idx]

        if cycle_id != self.current_cycle:
            # New cycle — initialize
            self.current_cycle = cycle_id
            self.entry_d2 = float(self.d2_tick[bar_idx])
            self.entry_agg = agg_idx
            self.cum_d2 = 0.0
            self.d2_count = 0
            self.last_agg_seen = -1
            self.flipped = False
            self.streak = 0
            self.streak_sign = 0
            if not np.isnan(self.entry_d2):
                self.d2_sign_at_entry = 1 if self.entry_d2 > 0 else -1
            else:
                self.d2_sign_at_entry = 0

        # Only accumulate when we enter a new agg bar
        if agg_idx != self.last_agg_seen and agg_idx > self.entry_agg:
            self.last_agg_seen = agg_idx
            d2_val = float(self.d2_agg[agg_idx])
            if not np.isnan(d2_val):
                self.cum_d2 += d2_val
                self.d2_count += 1

                # Streak tracking
                cur_sign = 1 if d2_val > 0 else -1
                if cur_sign == self.streak_sign:
                    self.streak += 1
                else:
                    self.streak = 1
                    self.streak_sign = cur_sign

                # Flip detection
                if self.d2_sign_at_entry != 0 and cur_sign != self.d2_sign_at_entry:
                    self.flipped = True

        # Store latest state for this cycle
        self.cycle_data[cycle_id] = {
            "entry_d2": self.entry_d2,
            "cum_d2": self.cum_d2,
            "d2_bars": self.d2_count,
            "d2_avg": self.cum_d2 / self.d2_count if self.d2_count > 0 else 0.0,
            "flipped": self.flipped,
            "streak": self.streak,
            "exit_d2_avg3": float(self.d2_tick[bar_idx]),  # will be overwritten
        }

    def finalize(self, cycles, signals):
        """Attach d2 tracking data and exit-time features to cycles."""
        for c in cycles:
            cid = c["cycle_id"]
            exit_bar = c["exit_bar"]

            # Exit-time features
            c["exit_d2"] = float(signals["d2_ema9"][exit_bar])
            c["exit_d2_avg3"] = float(signals["d2_avg3"][exit_bar])
            c["exit_d2_avg5"] = float(signals["d2_avg5"][exit_bar])

            if cid in self.cycle_data:
                d = self.cycle_data[cid]
                c["entry_d2"] = d["entry_d2"]
                c["cum_d2"] = d["cum_d2"]
                c["d2_bars"] = d["d2_bars"]
                c["d2_avg_intrade"] = d["d2_avg"]
                c["d2_flipped"] = d["flipped"]
                c["d2_streak"] = d["streak"]
            else:
                # No in-trade bars (instant exit)
                c["entry_d2"] = float(signals["d2_ema9"][c["seed_bar"]])
                c["cum_d2"] = 0.0
                c["d2_bars"] = 0
                c["d2_avg_intrade"] = 0.0
                c["d2_flipped"] = False
                c["d2_streak"] = 0


# ---------------------------------------------------------------------------
#  Helpers
# ---------------------------------------------------------------------------
def net_pnl(c):
    return c["pnl_ticks"] * TV - COMM * max(c.get("max_position", 1), 1)


def metrics(cycs):
    if not cycs:
        return {"n": 0, "wr": 0.0, "sr": 0.0, "er": 0.0, "pnl": 0.0}
    n = len(cycs)
    pnls = [net_pnl(c) for c in cycs]
    total = sum(pnls)
    wins = sum(1 for p in pnls if p >= 0)
    stops = sum(1 for c in cycs if c["exit_type"] == "HARD_STOP")
    return {"n": n, "wr": wins/n, "sr": stops/n, "er": total/n, "pnl": total}


def print_m(label, cycs, indent=4):
    pre = " " * indent
    m = metrics(cycs)
    if m["n"] == 0:
        print(f"{pre}{label}: 0 cycles")
        return
    print(f"{pre}{label}: {m['n']} cyc | {m['wr']:.0%} WR | {m['sr']:.0%} SR | "
          f"E[R]=${m['er']:.2f} | PnL=${m['pnl']:,.0f}")


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------
def main():
    print("=" * 70)
    print("Track B2 Extension: In-trade d2_ema9 analysis")
    print("=" * 70)

    print("\nLoading P1 data...")
    t0 = time.time()
    bars = load_bars_extended(DATA_FILE)
    print(f"  {bars['n']} ticks ({time.time()-t0:.0f}s)")

    print("\nPrecomputing signals...")
    t1 = time.time()
    signals = precompute_signals(bars, BAR_SIZE, LB)
    print(f"  Done ({time.time()-t1:.0f}s)")

    # Run sim with in-trade tracking
    print("\nRunning A+B filtered sim with d2 tracking...")
    t1 = time.time()
    tracker = D2Tracker(signals["d2_ema9"], signals["tick_to_agg"],
                        signals["d2_ema9_agg"])
    cycles = run_sim_filtered(
        bars, SD, HS, MAX_FADES, MAX_LEVELS, MAX_CONTRACT_SIZE,
        signal_arrays=signals, filter_fn=make_ab_filter(),
        on_bar_in_trade=tracker.on_bar
    )
    tracker.finalize(cycles, signals)
    print(f"  {len(cycles)} cycles ({time.time()-t1:.0f}s)")

    # Filter to valid d2
    valid = [c for c in cycles if not np.isnan(c["entry_d2"])]
    print(f"  {len(valid)} with valid entry d2")

    longs = [c for c in valid if c["direction"] == "LONG"]
    shorts = [c for c in valid if c["direction"] == "SHORT"]
    print(f"  {len(longs)} LONG, {len(shorts)} SHORT")

    # =========================================================================
    # ANALYSIS 1: cum_d2 alignment with trade direction
    # =========================================================================
    print(f"\n{'='*70}")
    print("ANALYSIS 1: Cumulative d2 alignment during trade")
    print("  Aligned: cum_d2 > 0 for LONG, cum_d2 <= 0 for SHORT")
    print("  Misaligned: cum_d2 <= 0 for LONG, cum_d2 > 0 for SHORT")
    print(f"{'='*70}")

    for label, subset in [("ALL", valid), ("LONG", longs), ("SHORT", shorts)]:
        aligned = [c for c in subset
                   if (c["direction"] == "LONG" and c["cum_d2"] > 0)
                   or (c["direction"] == "SHORT" and c["cum_d2"] <= 0)]
        misaligned = [c for c in subset
                      if (c["direction"] == "LONG" and c["cum_d2"] <= 0)
                      or (c["direction"] == "SHORT" and c["cum_d2"] > 0)]
        zero_bars = [c for c in subset if c["d2_bars"] == 0]

        print(f"\n  {label} trades:")
        print_m("cum_d2 ALIGNED", aligned)
        print_m("cum_d2 MISALIGNED", misaligned)
        if zero_bars:
            print_m("no in-trade bars (instant)", zero_bars)

    # =========================================================================
    # ANALYSIS 2: cum_d2 alignment by exit type
    # =========================================================================
    print(f"\n{'='*70}")
    print("ANALYSIS 2: Does misaligned cum_d2 predict HARD_STOP?")
    print(f"{'='*70}")

    for label, subset in [("LONG", longs), ("SHORT", shorts)]:
        aligned = [c for c in subset
                   if (c["direction"] == "LONG" and c["cum_d2"] > 0)
                   or (c["direction"] == "SHORT" and c["cum_d2"] <= 0)]
        misaligned = [c for c in subset
                      if (c["direction"] == "LONG" and c["cum_d2"] <= 0)
                      or (c["direction"] == "SHORT" and c["cum_d2"] > 0)]

        a_stops = sum(1 for c in aligned if c["exit_type"] == "HARD_STOP")
        m_stops = sum(1 for c in misaligned if c["exit_type"] == "HARD_STOP")
        a_sr = a_stops / len(aligned) * 100 if aligned else 0
        m_sr = m_stops / len(misaligned) * 100 if misaligned else 0

        print(f"\n  {label}:")
        print(f"    Aligned:    {len(aligned)} cyc, {a_stops} stops ({a_sr:.1f}% SR)")
        print(f"    Misaligned: {len(misaligned)} cyc, {m_stops} stops ({m_sr:.1f}% SR)")

    # =========================================================================
    # ANALYSIS 3: d2 flip detection
    # =========================================================================
    print(f"\n{'='*70}")
    print("ANALYSIS 3: d2 sign flip during trade")
    print("  Did d2 change sign relative to entry during the trade?")
    print(f"{'='*70}")

    for label, subset in [("ALL", valid), ("LONG", longs), ("SHORT", shorts)]:
        flipped = [c for c in subset if c["d2_flipped"]]
        stayed = [c for c in subset if not c["d2_flipped"]]

        print(f"\n  {label} trades:")
        print_m("d2 stayed same sign", stayed)
        print_m("d2 flipped sign", flipped)

    # =========================================================================
    # ANALYSIS 4: d2_avg (smoothed) at exit
    # =========================================================================
    print(f"\n{'='*70}")
    print("ANALYSIS 4: Smoothed d2 at exit (d2_avg3, d2_avg5)")
    print("  Does exit-time smoothed d2 differentiate outcomes?")
    print(f"{'='*70}")

    for avg_name in ["exit_d2_avg3", "exit_d2_avg5"]:
        label = avg_name.replace("exit_", "")
        print(f"\n  --- {label} ---")

        for dir_label, subset in [("LONG", longs), ("SHORT", shorts)]:
            has_val = [c for c in subset if not np.isnan(c[avg_name])]
            aligned = [c for c in has_val
                       if (c["direction"] == "LONG" and c[avg_name] > 0)
                       or (c["direction"] == "SHORT" and c[avg_name] <= 0)]
            misaligned = [c for c in has_val
                          if (c["direction"] == "LONG" and c[avg_name] <= 0)
                          or (c["direction"] == "SHORT" and c[avg_name] > 0)]

            print(f"\n  {dir_label} trades ({len(has_val)} with valid {label}):")
            print_m(f"{label} ALIGNED at exit", aligned)
            print_m(f"{label} MISALIGNED at exit", misaligned)

    # =========================================================================
    # ANALYSIS 5: d2_avg in-trade (mean d2 over trade duration)
    # =========================================================================
    print(f"\n{'='*70}")
    print("ANALYSIS 5: Mean d2 during trade (d2_avg_intrade)")
    print("  Average d2_ema9 across all agg bars while in position")
    print(f"{'='*70}")

    for label, subset in [("LONG", longs), ("SHORT", shorts)]:
        has_bars = [c for c in subset if c["d2_bars"] > 0]
        aligned = [c for c in has_bars
                   if (c["direction"] == "LONG" and c["d2_avg_intrade"] > 0)
                   or (c["direction"] == "SHORT" and c["d2_avg_intrade"] <= 0)]
        misaligned = [c for c in has_bars
                      if (c["direction"] == "LONG" and c["d2_avg_intrade"] <= 0)
                      or (c["direction"] == "SHORT" and c["d2_avg_intrade"] > 0)]

        print(f"\n  {label} trades ({len(has_bars)} with in-trade d2 data):")
        print_m("d2_avg_intrade ALIGNED", aligned)
        print_m("d2_avg_intrade MISALIGNED", misaligned)

    # =========================================================================
    # ANALYSIS 6: Quantile splits on cum_d2
    # =========================================================================
    print(f"\n{'='*70}")
    print("ANALYSIS 6: cum_d2 quantile splits")
    print("  Is the relationship monotonic? More cum_d2 = better?")
    print(f"{'='*70}")

    for label, subset in [("LONG", longs), ("SHORT", shorts)]:
        has_bars = [c for c in subset if c["d2_bars"] > 0]
        if not has_bars:
            continue

        # For SHORTs, negate cum_d2 so "aligned" is always positive
        if label == "SHORT":
            vals = [-c["cum_d2"] for c in has_bars]
        else:
            vals = [c["cum_d2"] for c in has_bars]

        arr = np.array(vals)
        q25, q50, q75 = np.percentile(arr, [25, 50, 75])

        buckets = [
            (f"Q1 (cum_d2 < {q25:.2f})", [c for c, v in zip(has_bars, vals) if v < q25]),
            (f"Q2 ({q25:.2f} to {q50:.2f})", [c for c, v in zip(has_bars, vals) if q25 <= v < q50]),
            (f"Q3 ({q50:.2f} to {q75:.2f})", [c for c, v in zip(has_bars, vals) if q50 <= v < q75]),
            (f"Q4 (cum_d2 >= {q75:.2f})", [c for c, v in zip(has_bars, vals) if v >= q75]),
        ]

        print(f"\n  {label} trades (directional cum_d2, higher = more aligned):")
        for bname, bcycs in buckets:
            print_m(bname, bcycs)

    # =========================================================================
    # SUMMARY
    # =========================================================================
    print(f"\n{'='*70}")
    print("SUMMARY: Is there in-trade signal?")
    print(f"{'='*70}")

    # Key comparison
    all_aligned = [c for c in valid
                   if (c["direction"] == "LONG" and c["cum_d2"] > 0)
                   or (c["direction"] == "SHORT" and c["cum_d2"] <= 0)]
    all_misaligned = [c for c in valid
                      if (c["direction"] == "LONG" and c["cum_d2"] <= 0)
                      or (c["direction"] == "SHORT" and c["cum_d2"] > 0)]

    m_a = metrics(all_aligned)
    m_m = metrics(all_misaligned)
    spread = m_a["er"] - m_m["er"]
    print(f"\n  cum_d2 ALIGNED:    {m_a['n']} cyc, E[R]=${m_a['er']:.2f}, SR={m_a['sr']:.0%}")
    print(f"  cum_d2 MISALIGNED: {m_m['n']} cyc, E[R]=${m_m['er']:.2f}, SR={m_m['sr']:.0%}")
    print(f"  SPREAD: ${spread:.2f}")

    if abs(spread) > 10:
        print(f"\n  Signal detected (spread > $10). Worth investigating as in-trade exit.")
    else:
        print(f"\n  Weak or no signal (spread <= $10). In-trade d2 not useful for exits.")

    print(f"\nRuntime: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
