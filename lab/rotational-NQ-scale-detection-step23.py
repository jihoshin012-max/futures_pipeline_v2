# archetype: rotational
"""
rotational-NQ-scale-detection-step23.py — Track C Step 0: Select test weeks.

Runs Track A+B combined config (SD=10 HS=60 depth_1 MCS=2 + chop<0.10
+ dR2<=-0.40 + dSlope<=-2.0 + fade_confirm<0.40) across all P1 weeks,
ranks by filtered PnL, and selects WEAKEST/LOW/MID/GOOD/BEST for Track C.

Also verifies on_bar_in_trade callback is functional by running a small
instrumented test on the WEAKEST week.

Prompt: rotational-NQ-prompt-trade-management-c.md
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
#  Config — Track A+B combined (frozen params)
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

OUTPUT_DIR = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")


# ---------------------------------------------------------------------------
#  Signal precomputation (includes fade_confirm — same as step21)
# ---------------------------------------------------------------------------

def precompute_signals(bars, bar_size, lookback):
    agg_bars, tick_to_agg = aggregate_to_ntick(bars, bar_size)
    regime = compute_regime_signals(agg_bars, lookback=lookback)
    entry = compute_entry_signals(agg_bars, lookback=lookback)

    n_ticks = bars["n"]
    n_agg = agg_bars["n"]
    a_high = agg_bars["high"]
    a_low = agg_bars["low"]
    a_open = agg_bars["open"]
    a_close = agg_bars["last"]
    a_tsec = agg_bars["time_sec"]
    a_dint = agg_bars["date_int"]
    last = bars["last"]

    # Prev bar range arrays for fade_confirm (indexed by agg bar)
    prev_high = np.full(n_agg, np.nan, dtype=np.float64)
    prev_low = np.full(n_agg, np.nan, dtype=np.float64)
    prev_range = np.full(n_agg, np.nan, dtype=np.float64)

    for ai in range(1, n_agg):
        prev = ai - 1
        prev_high[ai] = float(a_high[prev])
        prev_low[ai] = float(a_low[prev])
        rng = float(a_high[prev]) - float(a_low[prev])
        prev_range[ai] = rng if rng > 0 else np.nan

    # Map to ticks
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
        # Fade confirm arrays
        "prev_high": prev_high_tick,
        "prev_low": prev_low_tick,
        "prev_range": prev_range_tick,
        "last": last,
        # In-trade regime signals (agg-bar level, for Step 1 callback)
        "agg_signed_chop": entry["signed_chop"],
        "agg_dchop": entry["dchop"],
        "agg_d2chop": entry["d2chop"],
        "agg_signed_slope": entry["signed_slope"],
        "agg_dr2": entry["dr2"],
        "agg_dslope": entry["dslope"],
        "agg_r2": entry["r2"],
        "agg_choppiness": entry["choppiness"],
        "agg_slope_abs": entry["slope_abs"],
    }


# ---------------------------------------------------------------------------
#  Filter — full A+B stack
# ---------------------------------------------------------------------------

def make_ab_filter(chop_max, dr2_max, dslope_max, fc_max):
    """Track A + B combined: chop + dR2 + dSlope + fade_confirm."""
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
        # fade_confirm gate
        prev_range = signals["prev_range"][i]
        if np.isnan(prev_range):
            return True
        entry_price = float(signals["last"][i])
        if direction == 1:  # LONG
            fc = (entry_price - float(signals["prev_low"][i])) / prev_range
        else:  # SHORT
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
    result = {}
    for wk in sorted(weeks.keys()):
        result[wk] = compute_metrics(weeks[wk])
    return result


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Track C Step 0 — Select test weeks")
    parser.add_argument("--bar-file", type=str,
                        default=r"C:\Projects\futures_pipeline\data\NQ-1tick-calibration.csv")
    args = parser.parse_args()

    print(f"Loading bars from {args.bar_file}...")
    t0 = time.time()
    bars = load_bars_extended(args.bar_file)
    print(f"Loaded {bars['n']} bars in {time.time()-t0:.1f}s")

    print(f"\nPrecomputing signals (lb={LB}, bar_size={BAR_SIZE})...")
    t1 = time.time()
    signals = precompute_signals(bars, BAR_SIZE, LB)
    print(f"  done ({time.time()-t1:.1f}s)")

    # ===================================================================
    # Run Track A baseline (for comparison) and Track A+B combined
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"TRACK C STEP 0: A+B combined config — full P1 per-week breakdown")
    print(f"Config: SD={SD} HS={HS} depth_1 MCS={MAX_CONTRACT_SIZE}")
    print(f"Filter: chop<{CHOP_THRESHOLD} + dR2<={DR2_MAX} + dSlope<={DSLOPE_MAX} + fc<{FC_MAX}")
    print(f"{'='*70}")

    # --- Track A baseline (chop + dR2 + dSlope only) ---
    def track_a_filter(sigs, i, direction, step_dist):
        chop = sigs["choppiness"][i]
        if np.isnan(chop):
            return True
        if chop >= CHOP_THRESHOLD:
            return False
        dr2 = sigs["dr2"][i]
        if np.isnan(dr2):
            return True
        if dr2 > DR2_MAX:
            return False
        ds = sigs["dslope"][i]
        if np.isnan(ds):
            return True
        if ds > DSLOPE_MAX:
            return False
        return True

    print(f"\nRunning Track A baseline (chop + dR2 + dSlope)...")
    t1 = time.time()
    bl_cycles = run_sim_filtered(
        bars, step_dist=SD, hard_stop=HS,
        max_fades=MAX_FADES, max_levels=MAX_LEVELS,
        max_contract_size=MAX_CONTRACT_SIZE,
        signal_arrays=signals, filter_fn=track_a_filter,
    )
    bl = compute_metrics(bl_cycles)
    bl_weeks = weekly_breakdown(bl_cycles)
    print(f"  {bl['n']} cyc | {bl['wr']:.0%} WR | {bl['sr']:.0%} SR | "
          f"E[R]=${bl['er']:.2f} | PnL=${bl['pnl']:,.0f} ({time.time()-t1:.0f}s)")

    # --- Track A+B combined ---
    print(f"\nRunning Track A+B combined (chop + dR2 + dSlope + fc<{FC_MAX})...")
    t1 = time.time()
    ab_filter = make_ab_filter(CHOP_THRESHOLD, DR2_MAX, DSLOPE_MAX, FC_MAX)
    ab_cycles = run_sim_filtered(
        bars, step_dist=SD, hard_stop=HS,
        max_fades=MAX_FADES, max_levels=MAX_LEVELS,
        max_contract_size=MAX_CONTRACT_SIZE,
        signal_arrays=signals, filter_fn=ab_filter,
    )
    ab = compute_metrics(ab_cycles)
    ab_weeks = weekly_breakdown(ab_cycles)
    retention = ab["n"] / bl["n"] if bl["n"] > 0 else 0
    print(f"  {ab['n']} cyc ({retention:.0%} ret) | {ab['wr']:.0%} WR | "
          f"{ab['sr']:.0%} SR | E[R]=${ab['er']:.2f} | PnL=${ab['pnl']:,.0f} ({time.time()-t1:.0f}s)")

    # ===================================================================
    # Gross loss breakdown (verify the prompt's claim about stops)
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"GROSS LOSS BREAKDOWN (Track A+B combined)")
    print(f"{'='*70}")
    stop_losses = []
    rev_losses = []
    rev_wins = []
    eod_pnls = []
    for c in ab_cycles:
        comm = COMMISSION_PER_RT_MINI * max(c.get("max_position", 1), 1)
        net = c["pnl_ticks"] * 5.0 - comm
        if c["exit_type"] == "HARD_STOP":
            stop_losses.append(net)
        elif c["exit_type"] == "EOD_FLATTEN":
            eod_pnls.append(net)
        else:  # REVERSAL
            if net >= 0:
                rev_wins.append(net)
            else:
                rev_losses.append(net)
    print(f"  HARD_STOP cycles: {len(stop_losses)}")
    print(f"    Avg loss: ${np.mean(stop_losses):.2f}" if stop_losses else "    N/A")
    print(f"    Total loss: ${sum(stop_losses):,.0f}" if stop_losses else "")
    print(f"  REVERSAL wins: {len(rev_wins)}")
    print(f"    Avg win: ${np.mean(rev_wins):.2f}" if rev_wins else "    N/A")
    print(f"    Total: ${sum(rev_wins):,.0f}" if rev_wins else "")
    print(f"  REVERSAL losses: {len(rev_losses)}")
    print(f"    Avg loss: ${np.mean(rev_losses):.2f}" if rev_losses else "    N/A")
    print(f"    Total: ${sum(rev_losses):,.0f}" if rev_losses else "")
    print(f"  EOD_FLATTEN: {len(eod_pnls)}")
    if eod_pnls:
        print(f"    Avg: ${np.mean(eod_pnls):.2f}")
    gross_loss_stops = abs(sum(stop_losses)) if stop_losses else 0
    gross_loss_total = gross_loss_stops + abs(sum(rev_losses)) + abs(sum(p for p in eod_pnls if p < 0))
    print(f"\n  Stops as % of gross losses: {gross_loss_stops/gross_loss_total:.0%}" if gross_loss_total > 0 else "")

    # ===================================================================
    # Per-week breakdown
    # ===================================================================
    all_weeks = sorted(ab_weeks.keys())

    print(f"\n{'Week':<10} {'bl_N':>5} {'ab_N':>5} {'Ret':>5} "
          f"{'bl_WR':>6} {'ab_WR':>6} {'bl_SR':>6} {'ab_SR':>6} "
          f"{'bl_PnL':>10} {'ab_PnL':>10} {'ab_ER':>8}")
    print(f"{'-'*92}")

    week_data = []
    for wk in all_weeks:
        bm = bl_weeks.get(wk, {"n": 0, "wr": 0, "sr": 0, "er": 0, "pnl": 0})
        am = ab_weeks.get(wk, {"n": 0, "wr": 0, "sr": 0, "er": 0, "pnl": 0})
        ret = am["n"] / bm["n"] if bm["n"] > 0 else 0
        print(f"{wk:<10} {bm['n']:>5} {am['n']:>5} {ret:>4.0%} "
              f"{bm['wr']:>5.0%} {am['wr']:>5.0%} "
              f"{bm['sr']:>5.0%} {am['sr']:>5.0%} "
              f"${bm['pnl']:>9,.0f} ${am['pnl']:>9,.0f} ${am['er']:>7.2f}")
        week_data.append({
            "week": wk, "bl_n": bm["n"], "ab_n": am["n"],
            "retention": ret,
            "bl_wr": bm["wr"], "ab_wr": am["wr"],
            "bl_sr": bm["sr"], "ab_sr": am["sr"],
            "bl_pnl": bm["pnl"], "ab_pnl": am["pnl"],
            "bl_er": bm["er"], "ab_er": am["er"],
        })

    # ===================================================================
    # Select 5 test weeks: WEAKEST / LOW / MID / GOOD / BEST
    # ===================================================================
    ranked = sorted(week_data, key=lambda w: w["ab_pnl"])
    n_weeks = len(ranked)

    idx_weakest = 0
    idx_low = max(1, n_weeks // 4)
    idx_mid = n_weeks // 2
    idx_good = min(n_weeks - 2, 3 * n_weeks // 4)
    idx_best = n_weeks - 1

    selections = [
        ("WEAKEST", ranked[idx_weakest]),
        ("LOW", ranked[idx_low]),
        ("MID", ranked[idx_mid]),
        ("GOOD", ranked[idx_good]),
        ("BEST", ranked[idx_best]),
    ]

    print(f"\n{'='*70}")
    print(f"TEST WEEK SELECTION (ranked by A+B combined PnL)")
    print(f"{'='*70}")
    print(f"\n{'Cat':<10} {'Week':<10} {'ab_N':>5} {'ab_WR':>6} {'ab_SR':>6} {'ab_PnL':>10} {'ab_ER':>8}")
    print(f"{'-'*60}")

    for cat, wd in selections:
        print(f"{cat:<10} {wd['week']:<10} {wd['ab_n']:>5} {wd['ab_wr']:>5.0%} "
              f"{wd['ab_sr']:>5.0%} ${wd['ab_pnl']:>9,.0f} ${wd['ab_er']:>7.2f}")

    # Full ranking for reference
    print(f"\nFull ranking (by ab_pnl, ascending):")
    print(f"{'Rank':>4} {'Week':<10} {'ab_N':>5} {'ab_PnL':>10} {'ab_ER':>8}")
    print(f"{'-'*42}")
    for i, wd in enumerate(ranked):
        marker = ""
        for cat, sel in selections:
            if sel["week"] == wd["week"]:
                marker = f"  <-- {cat}"
        print(f"{i+1:>4} {wd['week']:<10} {wd['ab_n']:>5} ${wd['ab_pnl']:>9,.0f} ${wd['ab_er']:>7.2f}{marker}")

    # ===================================================================
    # on_bar_in_trade callback verification (WEAKEST week only)
    # ===================================================================
    weakest_week = selections[0][1]["week"]
    print(f"\n{'='*70}")
    print(f"ON_BAR_IN_TRADE CALLBACK VERIFICATION (week={weakest_week})")
    print(f"{'='*70}")

    bar_records = []
    def on_bar_callback(bar_idx, cycle_id, bar_offset, price, direction, pnl_ticks, mfe_ticks, mae_ticks):
        bar_records.append({
            "bar_idx": bar_idx, "cycle_id": cycle_id, "bar_offset": bar_offset,
            "price": price, "direction": direction,
            "pnl_ticks": pnl_ticks, "mfe_ticks": mfe_ticks, "mae_ticks": mae_ticks,
        })

    # Run on WEAKEST week only by filtering bars
    # Determine week date range from the cycle data
    weakest_cycles_ref = [c for c in ab_cycles
                          if datetime.date(int(c["seed_dt"][:4]),
                                           int(c["seed_dt"][5:7]),
                                           int(c["seed_dt"][8:10])
                          ).isocalendar()[1] == int(weakest_week.split("W")[1])
                          and c["seed_dt"][:4] == weakest_week.split("-")[0]]

    # Run full sim with callback (we'll filter callback data to weakest week later)
    # Actually — for verification, just run on the full data with callback and count
    # Only need to confirm the callback fires and captures expected fields.
    # Run a single quick pass with callback.
    t1 = time.time()
    test_cycles = run_sim_filtered(
        bars, step_dist=SD, hard_stop=HS,
        max_fades=MAX_FADES, max_levels=MAX_LEVELS,
        max_contract_size=MAX_CONTRACT_SIZE,
        signal_arrays=signals, filter_fn=ab_filter,
        on_bar_in_trade=on_bar_callback,
    )
    print(f"  Sim with callback: {len(test_cycles)} cycles, {len(bar_records)} in-trade bar records ({time.time()-t1:.0f}s)")

    # Verify cycle identity (callback should not change cycle output)
    if len(test_cycles) != len(ab_cycles):
        print(f"  WARNING: Callback changed cycle count! {len(test_cycles)} vs {len(ab_cycles)}")
    else:
        mismatches = sum(1 for a, b in zip(ab_cycles, test_cycles)
                         if a["pnl_ticks"] != b["pnl_ticks"])
        if mismatches == 0:
            print(f"  PASS: Cycle identity verified (all {len(test_cycles)} cycles match)")
        else:
            print(f"  WARNING: {mismatches} PnL mismatches with callback enabled")

    # Show callback field summary
    if bar_records:
        sample = bar_records[0]
        print(f"  Callback fields: {list(sample.keys())}")
        # Show stats per cycle
        cycle_ids = set(r["cycle_id"] for r in bar_records)
        bars_per_cycle = [sum(1 for r in bar_records if r["cycle_id"] == cid) for cid in list(cycle_ids)[:100]]
        print(f"  Bars per cycle (first 100): min={min(bars_per_cycle)}, "
              f"max={max(bars_per_cycle)}, median={sorted(bars_per_cycle)[len(bars_per_cycle)//2]}")

    # ===================================================================
    # Regime signal availability check at bar level
    # ===================================================================
    print(f"\n  Regime signal check (agg_bar level):")
    tick_to_agg = signals["tick_to_agg"]
    for sig_name in ["agg_signed_chop", "agg_dr2", "agg_dslope", "agg_signed_slope",
                     "agg_r2", "agg_choppiness"]:
        arr = signals[sig_name]
        valid = np.sum(~np.isnan(arr))
        print(f"    {sig_name}: {valid}/{len(arr)} valid agg bars ({valid/len(arr):.0%})")

    # ===================================================================
    # Save results
    # ===================================================================
    out_csv = OUTPUT_DIR / "trade-mgmt-step0-week-selection.csv"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(out_csv, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=[
            "rank", "week", "category", "bl_n", "ab_n", "retention",
            "bl_wr", "ab_wr", "bl_sr", "ab_sr",
            "bl_pnl", "ab_pnl", "bl_er", "ab_er",
        ])
        w.writeheader()
        selected_weeks = {s[1]["week"]: s[0] for s in selections}
        for i, wd in enumerate(ranked):
            w.writerow({
                "rank": i + 1,
                "week": wd["week"],
                "category": selected_weeks.get(wd["week"], ""),
                "bl_n": wd["bl_n"], "ab_n": wd["ab_n"],
                "retention": f"{wd['retention']:.4f}",
                "bl_wr": f"{wd['bl_wr']:.4f}", "ab_wr": f"{wd['ab_wr']:.4f}",
                "bl_sr": f"{wd['bl_sr']:.4f}", "ab_sr": f"{wd['ab_sr']:.4f}",
                "bl_pnl": f"{wd['bl_pnl']:.2f}", "ab_pnl": f"{wd['ab_pnl']:.2f}",
                "bl_er": f"{wd['bl_er']:.2f}", "ab_er": f"{wd['ab_er']:.2f}",
            })
    print(f"\nSaved: {out_csv}")

    total = time.time() - t0
    print(f"\nTotal runtime: {total:.0f}s ({total/60:.1f}m)")

    return selections


if __name__ == "__main__":
    main()
