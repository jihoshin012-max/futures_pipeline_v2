# archetype: rotational
"""
rotational-NQ-scale-detection-step30.py -- Track C2 Steps 1-2: Session context
features + entry correlation analysis.

Step 1: Run A+B config on 5 test weeks. At each entry, compute:
  - tick_rate_ratio (market speed vs session average)
  - session_range_ratio (session range / ATR at open)
  - price_displacement (entry price vs session midpoint / ATR)
  - sr_acceleration (recent stop rate - session stop rate)

Step 2: Bucket cycles by each feature, compute SR/WR/avg $/cyc.
  Test both directions. Kill gate: SR spread > 3pt for at least one feature.

Prompt: rotational-NQ-prompt-loss-mitigation-c2.md Steps 1-2
"""
from __future__ import annotations

import csv
import datetime
import importlib
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

_engine = importlib.import_module("rotational-NQ-scale-detection-engine")
load_bars_extended = _engine.load_bars_extended
aggregate_to_ntick = _engine.aggregate_to_ntick
compute_regime_signals = _engine.compute_regime_signals
compute_entry_signals = _engine.compute_entry_signals
map_signal_to_ticks = _engine.map_signal_to_ticks

_sweep = importlib.import_module("rotational-NQ-scale-detection-sweep")
run_sim_filtered = _sweep.run_sim_filtered
TICK_SIZE = _sweep.TICK_SIZE
COMMISSION_PER_RT_MINI = _sweep.COMMISSION_PER_RT_MINI


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

RTH_OPEN_SEC = 9 * 3600 + 30 * 60
RTH_CLOSE_SEC = 15 * 3600 + 49 * 60 + 50
WARMUP_BARS = 20  # Min RTH agg bars before tick_rate_ratio is valid

TEST_WEEKS = {
    "2025-W40": "WEAKEST",
    "2025-W41": "MID",
    "2025-W46": "GOOD",
    "2025-W47": "BEST",
    "2025-W48": "LOW",
}

OUTPUT_DIR = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")


# ---------------------------------------------------------------------------
#  Signal precomputation (same as step23/29)
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
        prev = ai - 1
        prev_high[ai] = float(a_high[prev])
        prev_low[ai] = float(a_low[prev])
        rng = float(a_high[prev]) - float(a_low[prev])
        prev_range[ai] = rng if rng > 0 else np.nan

    prev_high_tick = prev_high[tick_to_agg]
    prev_low_tick = prev_low[tick_to_agg]
    prev_range_tick = prev_range[tick_to_agg]

    return {
        "choppiness": map_signal_to_ticks(regime["choppiness"], tick_to_agg),
        "slope": map_signal_to_ticks(regime["slope"], tick_to_agg),
        "dr2": map_signal_to_ticks(entry["dr2"], tick_to_agg),
        "dslope": map_signal_to_ticks(entry["dslope"], tick_to_agg),
        "tick_to_agg": tick_to_agg,
        "warmup_ticks": {"dominant_scale": 0, "asymmetry": 0},
        "prev_high": prev_high_tick,
        "prev_low": prev_low_tick,
        "prev_range": prev_range_tick,
        "last": last,
    }, agg_bars, tick_to_agg


# ---------------------------------------------------------------------------
#  Filter
# ---------------------------------------------------------------------------

def make_ab_filter(chop_max, dr2_max, dslope_max, fc_max):
    def f(signals, i, direction, step_dist):
        chop = signals["choppiness"][i]
        if np.isnan(chop):
            return True
        if chop >= chop_max:
            return False
        dr2 = signals["dr2"][i]
        if np.isnan(dr2):
            return True
        if dr2 > dr2_max:
            return False
        ds = signals["dslope"][i]
        if np.isnan(ds):
            return True
        if ds > dslope_max:
            return False
        prev_range = signals["prev_range"][i]
        if np.isnan(prev_range):
            return True
        entry_price = float(signals["last"][i])
        if direction == 1:
            fc = (entry_price - float(signals["prev_low"][i])) / prev_range
        else:
            fc = (float(signals["prev_high"][i]) - entry_price) / prev_range
        return fc < fc_max
    return f


# ---------------------------------------------------------------------------
#  Metrics
# ---------------------------------------------------------------------------

def compute_metrics(cycles):
    if not cycles:
        return {"n": 0, "wr": 0.0, "sr": 0.0, "er": 0.0, "pnl": 0.0}
    n = len(cycles)
    stops = sum(1 for c in cycles if c["exit_type"] == "HARD_STOP")
    net_pnls = []
    for c in cycles:
        comm = COMMISSION_PER_RT_MINI * max(c.get("max_position", 1), 1)
        net_pnls.append(c["pnl_ticks"] * 5.0 - comm)
    wins = sum(1 for p in net_pnls if p >= 0)
    return {"n": n, "wr": wins / n, "sr": stops / n,
            "er": sum(net_pnls) / n, "pnl": sum(net_pnls)}


def weekly_breakdown(cycles):
    weeks = defaultdict(list)
    for c in cycles:
        dt = c["seed_dt"][:10]
        d = datetime.date(int(dt[:4]), int(dt[5:7]), int(dt[8:10]))
        wk = f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"
        weeks[wk].append(c)
    return {wk: cycles_list for wk, cycles_list in sorted(weeks.items())}


# ---------------------------------------------------------------------------
#  Session context feature computation
# ---------------------------------------------------------------------------

def compute_session_context(cycles, agg_bars, tick_to_agg, bars):
    """Tag each cycle with session context features at entry time.

    All features are computed from completed bars/cycles — no look-ahead.
    """
    n_agg = agg_bars["n"]
    a_high = agg_bars["high"].astype(np.float64)
    a_low = agg_bars["low"].astype(np.float64)
    a_close = agg_bars["last"].astype(np.float64)
    a_tsec = agg_bars["time_sec"]
    a_dint = agg_bars["date_int"]
    a_atr = agg_bars["atr"].astype(np.float64)

    last_tick = bars["last"].astype(np.float64)
    tsec_tick = bars["time_sec"]

    # Precompute agg bar durations (seconds between consecutive bars)
    bar_dur = np.full(n_agg, np.nan, dtype=np.float64)
    for i in range(1, n_agg):
        if a_dint[i] == a_dint[i - 1]:
            dur = int(a_tsec[i]) - int(a_tsec[i - 1])
            if dur > 0:
                bar_dur[i] = float(dur)

    # Precompute tick rate per agg bar: 250 ticks / duration_seconds
    tick_rate = np.full(n_agg, np.nan, dtype=np.float64)
    for i in range(n_agg):
        if not np.isnan(bar_dur[i]) and bar_dur[i] > 0:
            tick_rate[i] = BAR_SIZE / bar_dur[i]

    # Build per-session structures (session = unique date_int)
    # For each agg bar, track session high/low/atr_at_open/rth_bar_count
    sessions = {}  # date_int -> {high, low, atr_at_open, tick_rates: [], rth_bars: int}

    # Pre-scan to build session data
    for ai in range(n_agg):
        d = int(a_dint[ai])
        t = int(a_tsec[ai])
        if t < RTH_OPEN_SEC or t > RTH_CLOSE_SEC:
            continue
        if d not in sessions:
            sessions[d] = {
                "high": float(a_high[ai]),
                "low": float(a_low[ai]),
                "atr_at_open": float(a_atr[ai]) if not np.isnan(a_atr[ai]) else np.nan,
                "tick_rates": [],
                "rth_bars": 0,
            }
        s = sessions[d]
        h = float(a_high[ai])
        l = float(a_low[ai])
        if h > s["high"]:
            s["high"] = h
        if l < s["low"]:
            s["low"] = l
        s["rth_bars"] += 1
        if not np.isnan(tick_rate[ai]):
            s["tick_rates"].append(tick_rate[ai])

    # Now for each cycle, compute features at entry time
    # We need rolling session state up to the entry agg bar
    # Rebuild incrementally per session

    # Sort cycles by seed_bar for sequential processing
    sorted_cycles = sorted(cycles, key=lambda c: c["seed_bar"])

    # Build incremental session state indexed by agg bar
    # For each agg bar in RTH, store cumulative session state
    agg_session_state = {}  # ai -> {session_high, session_low, atr_at_open,
    #                                  avg_tick_rate, rth_bar_count}
    current_session_date = -1
    sess_high = 0.0
    sess_low = 0.0
    sess_atr_open = np.nan
    sess_tick_rates = []
    sess_rth_count = 0

    for ai in range(n_agg):
        d = int(a_dint[ai])
        t = int(a_tsec[ai])
        if t < RTH_OPEN_SEC or t > RTH_CLOSE_SEC:
            continue

        if d != current_session_date:
            # New session
            current_session_date = d
            sess_high = float(a_high[ai])
            sess_low = float(a_low[ai])
            sess_atr_open = float(a_atr[ai]) if not np.isnan(a_atr[ai]) else np.nan
            sess_tick_rates = []
            sess_rth_count = 0

        h = float(a_high[ai])
        l = float(a_low[ai])
        if h > sess_high:
            sess_high = h
        if l < sess_low:
            sess_low = l
        sess_rth_count += 1
        if not np.isnan(tick_rate[ai]):
            sess_tick_rates.append(tick_rate[ai])

        avg_tr = np.mean(sess_tick_rates) if sess_tick_rates else np.nan

        agg_session_state[ai] = {
            "session_high": sess_high,
            "session_low": sess_low,
            "atr_at_open": sess_atr_open,
            "avg_tick_rate": avg_tr,
            "rth_bar_count": sess_rth_count,
        }

    # Build stop rate tracking: for sr_acceleration we need completed cycles
    # sorted by seed_bar. Track per-session stop counts.
    cycle_outcomes = []  # (seed_bar, is_stop, session_date)
    for c in sorted_cycles:
        dt_str = c["seed_dt"][:10]
        d = datetime.date(int(dt_str[:4]), int(dt_str[5:7]), int(dt_str[8:10]))
        session_date = int(d.strftime("%Y%m%d"))
        is_stop = 1 if c["exit_type"] == "HARD_STOP" else 0
        cycle_outcomes.append((c["seed_bar"], is_stop, session_date))

    # Tag cycles
    tagged = []
    for ci, c in enumerate(sorted_cycles):
        seed_bar = c["seed_bar"]
        agg_idx = int(tick_to_agg[seed_bar])
        entry_price = float(last_tick[seed_bar])

        # Find the PREVIOUS completed agg bar (not the entry bar)
        prev_agg = agg_idx - 1

        # Get session state at entry agg bar (includes all bars up to agg_idx)
        # Use state at prev_agg to avoid look-ahead from current bar
        state = agg_session_state.get(agg_idx)
        if state is None:
            # Entry is outside RTH or no state — skip tagging
            tagged.append({**c, "tick_rate_ratio": np.nan,
                           "session_range_ratio": np.nan,
                           "price_displacement": np.nan,
                           "sr_acceleration": np.nan})
            continue

        rth_bars = state["rth_bar_count"]
        atr_open = state["atr_at_open"]
        sess_high = state["session_high"]
        sess_low = state["session_low"]
        avg_tr = state["avg_tick_rate"]

        # --- Feature 1: tick_rate_ratio ---
        # Use prev completed bar's tick rate / session avg tick rate
        if prev_agg >= 0 and not np.isnan(tick_rate[prev_agg]) and \
           not np.isnan(avg_tr) and avg_tr > 0 and rth_bars >= WARMUP_BARS:
            trr = tick_rate[prev_agg] / avg_tr
        else:
            trr = np.nan  # warmup or no data

        # --- Feature 2: session_range_ratio ---
        if not np.isnan(atr_open) and atr_open > 0:
            sess_range = sess_high - sess_low
            srr = sess_range / atr_open
        else:
            srr = np.nan

        # --- Feature 3: price_displacement ---
        if not np.isnan(atr_open) and atr_open > 0 and sess_high > sess_low:
            sess_mid = (sess_high + sess_low) / 2.0
            pd = (entry_price - sess_mid) / atr_open
        else:
            pd = np.nan

        # --- Feature 4: sr_acceleration ---
        # recent_sr (last 20 completed cycles) - session_sr (all cycles this session)
        dt_str = c["seed_dt"][:10]
        d = datetime.date(int(dt_str[:4]), int(dt_str[5:7]), int(dt_str[8:10]))
        session_date = int(d.strftime("%Y%m%d"))

        # Count completed cycles before this one in this session
        session_stops = 0
        session_total = 0
        recent_stops = 0
        recent_total = 0
        recent_window = 20

        for j in range(ci - 1, -1, -1):
            oc = cycle_outcomes[j]
            if oc[2] != session_date:
                break
            session_total += 1
            session_stops += oc[1]
            if session_total <= recent_window:
                recent_total += 1
                recent_stops += oc[1]

        if session_total >= 10 and recent_total >= 5:
            session_sr = session_stops / session_total
            recent_sr = recent_stops / recent_total
            sra = recent_sr - session_sr
        else:
            sra = np.nan

        tagged.append({
            **c,
            "tick_rate_ratio": trr,
            "session_range_ratio": srr,
            "price_displacement": pd,
            "sr_acceleration": sra,
        })

    return tagged


# ---------------------------------------------------------------------------
#  Step 2: Correlation analysis
# ---------------------------------------------------------------------------

FEATURES = ["tick_rate_ratio", "session_range_ratio", "price_displacement",
            "sr_acceleration"]


def analyze_feature(tagged_cycles, feature_name, n_buckets=5):
    """Bucket cycles by feature quintiles. Return per-bucket stats."""
    # Filter valid
    valid = [(c, c[feature_name]) for c in tagged_cycles
             if not np.isnan(c[feature_name])]
    if len(valid) < 50:
        return None

    values = [v for _, v in valid]
    # Compute quintile boundaries
    boundaries = np.percentile(values, np.linspace(0, 100, n_buckets + 1))
    boundaries[0] = -np.inf
    boundaries[-1] = np.inf

    buckets = defaultdict(list)
    for c, v in valid:
        for b in range(n_buckets):
            if boundaries[b] <= v < boundaries[b + 1]:
                buckets[b].append(c)
                break
        else:
            buckets[n_buckets - 1].append(c)

    results = []
    for b in range(n_buckets):
        bc = buckets[b]
        if not bc:
            continue
        n = len(bc)
        stops = sum(1 for c in bc if c["exit_type"] == "HARD_STOP")
        net_pnls = [c["pnl_ticks"] * 5.0 - COMMISSION_PER_RT_MINI * max(c.get("max_position", 1), 1)
                     for c in bc]
        wins = sum(1 for p in net_pnls if p >= 0)
        avg_val = np.mean([c[feature_name] for c in bc])
        results.append({
            "bucket": b + 1,
            "n": n,
            "avg_value": avg_val,
            "lo": boundaries[b] if boundaries[b] != -np.inf else min(c[feature_name] for c in bc),
            "hi": boundaries[b + 1] if boundaries[b + 1] != np.inf else max(c[feature_name] for c in bc),
            "wr": wins / n,
            "sr": stops / n,
            "er": sum(net_pnls) / n,
            "pnl": sum(net_pnls),
        })

    return results


def analyze_feature_per_week(tagged_cycles, feature_name, n_buckets=3):
    """Analyze feature by terciles, per week."""
    weeks = defaultdict(list)
    for c in tagged_cycles:
        dt = c["seed_dt"][:10]
        d = datetime.date(int(dt[:4]), int(dt[5:7]), int(dt[8:10]))
        wk = f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"
        if wk in TEST_WEEKS:
            weeks[wk].append(c)

    per_week = {}
    for wk in sorted(weeks.keys()):
        result = analyze_feature(weeks[wk], feature_name, n_buckets)
        if result:
            per_week[wk] = result
    return per_week


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Track C2 Steps 1-2")
    parser.add_argument("--bar-file", type=str,
                        default=r"C:\Projects\futures_pipeline\data\NQ-1tick-calibration.csv")
    args = parser.parse_args()

    print(f"Loading bars from {args.bar_file}...")
    t0 = time.time()
    bars = load_bars_extended(args.bar_file)
    print(f"Loaded {bars['n']} bars in {time.time()-t0:.1f}s")

    print(f"\nPrecomputing signals (lb={LB}, bar_size={BAR_SIZE})...")
    t1 = time.time()
    signals, agg_bars, tick_to_agg = precompute_signals(bars, BAR_SIZE, LB)
    print(f"  done ({time.time()-t1:.1f}s)")

    ab_filter = make_ab_filter(CHOP_THRESHOLD, DR2_MAX, DSLOPE_MAX, FC_MAX)

    # ===================================================================
    # Step 1: Run A+B sim, tag cycles with session context features
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"TRACK C2 STEP 1: Compute session context features")
    print(f"{'='*70}")

    print(f"\nRunning A+B sim on full P1...")
    t1 = time.time()
    all_cycles = run_sim_filtered(
        bars, step_dist=SD, hard_stop=HS,
        max_fades=MAX_FADES, max_levels=MAX_LEVELS,
        max_contract_size=MAX_CONTRACT_SIZE,
        signal_arrays=signals, filter_fn=ab_filter,
    )
    m = compute_metrics(all_cycles)
    print(f"  {m['n']} cyc | {m['wr']:.0%} WR | {m['sr']:.0%} SR | "
          f"E[R]=${m['er']:.2f} ({time.time()-t1:.0f}s)")

    # Filter to 5 test weeks
    test_cycles = []
    for c in all_cycles:
        dt = c["seed_dt"][:10]
        d = datetime.date(int(dt[:4]), int(dt[5:7]), int(dt[8:10]))
        wk = f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"
        if wk in TEST_WEEKS:
            test_cycles.append(c)
    print(f"  Test weeks: {len(test_cycles)} cycles from {len(TEST_WEEKS)} weeks")

    print(f"\nComputing session context features...")
    t1 = time.time()
    tagged = compute_session_context(test_cycles, agg_bars, tick_to_agg, bars)
    print(f"  done ({time.time()-t1:.1f}s)")

    # Coverage stats
    for feat in FEATURES:
        valid = sum(1 for c in tagged if not np.isnan(c[feat]))
        print(f"  {feat}: {valid}/{len(tagged)} valid ({valid/len(tagged):.0%})")

    # Save tagged cycles
    out_path = OUTPUT_DIR / "c2-session-context-tagged-cycles.csv"
    fieldnames = list(tagged[0].keys()) if tagged else []
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for c in tagged:
            row = {}
            for k, v in c.items():
                if isinstance(v, float) and np.isnan(v):
                    row[k] = ""
                else:
                    row[k] = v
            w.writerow(row)
    print(f"\nSaved: {out_path}")

    # ===================================================================
    # Step 2: Entry correlation analysis
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"TRACK C2 STEP 2: Entry correlation analysis")
    print(f"{'='*70}")

    max_sr_spread = 0.0
    best_feature = None

    for feat in FEATURES:
        print(f"\n--- {feat} ---")
        result = analyze_feature(tagged, feat, n_buckets=5)
        if result is None:
            print(f"  Insufficient data (< 50 valid)")
            continue

        sr_low = result[0]["sr"]
        sr_high = result[-1]["sr"]
        sr_spread = abs(sr_high - sr_low)

        print(f"  {'Bucket':>6} {'N':>5} {'Avg Val':>8} {'WR':>5} {'SR':>5} "
              f"{'E[R]':>8} {'PnL':>10}")
        print(f"  {'-'*55}")
        for r in result:
            print(f"  Q{r['bucket']:>5} {r['n']:>5} {r['avg_value']:>8.3f} "
                  f"{r['wr']:>4.0%} {r['sr']:>4.0%} "
                  f"${r['er']:>7.2f} ${r['pnl']:>9,.0f}")

        print(f"\n  SR spread (Q1 vs Q5): {sr_spread:.1%} ({sr_spread*100:.1f}pt)")
        print(f"  Direction: {'LOW predicts stops' if sr_low > sr_high else 'HIGH predicts stops'}")

        if sr_spread > max_sr_spread:
            max_sr_spread = sr_spread
            best_feature = feat

        # Per-week breakdown (terciles)
        pw = analyze_feature_per_week(tagged, feat, n_buckets=3)
        if pw:
            print(f"\n  Per-week tercile breakdown:")
            for wk in sorted(pw.keys()):
                cat = TEST_WEEKS.get(wk, "")
                tercs = pw[wk]
                sr_vals = [t["sr"] for t in tercs]
                spread = max(sr_vals) - min(sr_vals) if sr_vals else 0
                parts = " | ".join(f"T{t['bucket']}:{t['sr']:.0%}" for t in tercs)
                print(f"    {wk} ({cat:>8}): {parts} | spread={spread:.0%}")

    # ===================================================================
    # Kill gate
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"KILL GATE — Session context features")
    print(f"{'='*70}")
    print(f"Best feature: {best_feature}")
    print(f"Max SR spread: {max_sr_spread:.1%} ({max_sr_spread*100:.1f}pt)")

    if max_sr_spread > 0.03:
        print(f"PASS — SR spread {max_sr_spread:.1%} > 3pt. Session context proceeds to Step 3.")
    else:
        print(f"FAIL — No feature shows SR spread > 3pt. Skip Steps 3. "
              f"Proceed to Step 4 (mechanical rules).")

    total_time = time.time() - t0
    print(f"\nTotal runtime: {total_time:.0f}s")


if __name__ == "__main__":
    main()
