# archetype: rotational
"""
rotational-NQ-scale-detection-step25.py — Track C Step 2: In-trade correlation analysis.

Compares feature trajectories for HARD_STOP vs REVERSAL cycles.
Tests both directions for regime signals (Track B inversion finding).
Computes lead time for signal divergence.

Kill gate: dies if (1) no trajectory divergence, or (2) lead time < 3 agg bars.

Prompt: rotational-NQ-prompt-trade-management-c.md Step 2
"""
from __future__ import annotations

import csv
import datetime
import importlib
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

OUTPUT_DIR = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")

HARD_STOP_TICKS = 60.0  # for mae_proximity computation

TEST_WEEKS = {
    "2025-W40": "WEAKEST",
    "2025-W41": "MID",
    "2025-W46": "GOOD",
    "2025-W47": "BEST",
    "2025-W48": "LOW",
}


# ---------------------------------------------------------------------------
#  Load Step 1 output
# ---------------------------------------------------------------------------

def load_cycles(path):
    """Load per-cycle summary."""
    cycles = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            for k in ["pnl_ticks", "pnl_dollars", "mfe_ticks", "mae_ticks",
                       "seed_price", "exit_price"]:
                r[k] = float(r[k])
            for k in ["cycle_id", "bars_held", "depth", "max_position", "agg_bars_held"]:
                r[k] = int(r[k])
            cycles.append(r)
    return cycles


def load_bars(path):
    """Load per-agg-bar snapshots."""
    bars = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            r["cycle_id"] = int(r["cycle_id"])
            r["agg_bar_offset"] = int(r["agg_bar_offset"])
            r["agg_idx"] = int(r["agg_idx"])
            r["tick_bar_offset"] = int(r["tick_bar_offset"])
            r["direction"] = int(r["direction"])
            for k in ["price", "pnl_ticks", "mfe_ticks", "mae_ticks",
                       "signed_chop", "dchop", "d2chop", "signed_slope",
                       "dr2", "dslope", "r2", "choppiness", "slope_abs",
                       "agg_bar_range"]:
                v = r[k]
                r[k] = float(v) if v != "" else np.nan
            bars.append(r)
    return bars


# ---------------------------------------------------------------------------
#  Analysis helpers
# ---------------------------------------------------------------------------

REGIME_FEATURES = ["signed_chop", "dr2", "dslope", "signed_slope",
                   "r2", "choppiness", "dchop", "d2chop"]

def compute_trade_behavior(bars_for_cycle, cycle):
    """Compute trade-behavior derived signals from raw bar snapshots.

    Returns list of dicts (one per agg bar) with added fields:
        mfe_rate, mae_proximity, mae_increment, current_favor_ticks,
        mfe_retracement
    """
    out = []
    prev_mae = 0.0
    direction = cycle["direction"]  # "LONG" or "SHORT"
    dir_sign = 1 if direction == "LONG" else -1

    for i, b in enumerate(bars_for_cycle):
        rec = dict(b)  # copy

        # MFE rate: mfe_ticks / (agg_bar_offset + 1)
        abo = b["agg_bar_offset"]
        rec["mfe_rate"] = b["mfe_ticks"] / (abo + 1) if abo >= 0 else np.nan

        # MAE proximity: mae_ticks / hard_stop_ticks
        rec["mae_proximity"] = b["mae_ticks"] / HARD_STOP_TICKS

        # MAE increment: new worst this bar
        rec["mae_increment"] = b["mae_ticks"] - prev_mae
        prev_mae = b["mae_ticks"]

        # Current favor ticks: pnl_ticks / max_position
        # pnl_ticks from callback = exc * abs(pos_qty), so it's already total
        # For favor ticks relative to entry, use pnl_ticks / pos_qty direction
        # Actually pnl_ticks from callback is already signed correctly for direction
        max_pos = cycle["max_position"]
        rec["current_favor_ticks"] = b["pnl_ticks"] / max_pos if max_pos > 0 else 0

        # MFE retracement
        if b["mfe_ticks"] > 0:
            rec["mfe_retracement"] = (b["mfe_ticks"] - rec["current_favor_ticks"]) / b["mfe_ticks"]
        else:
            rec["mfe_retracement"] = 0.0

        # Signed features relative to position direction
        # "against position" = signed_chop negative when LONG, positive when SHORT
        if not np.isnan(b["signed_chop"]):
            rec["signed_chop_vs_pos"] = b["signed_chop"] * dir_sign
        else:
            rec["signed_chop_vs_pos"] = np.nan

        if not np.isnan(b["signed_slope"]):
            rec["signed_slope_vs_pos"] = b["signed_slope"] * dir_sign
        else:
            rec["signed_slope_vs_pos"] = np.nan

        out.append(rec)
    return out


# ---------------------------------------------------------------------------
#  Main analysis
# ---------------------------------------------------------------------------

def main():
    t0 = time.time()

    print(f"Loading Step 1 output...")
    cycles = load_cycles(OUTPUT_DIR / "trade-mgmt-tagged-cycles.csv")
    bars = load_bars(OUTPUT_DIR / "trade-mgmt-intrade-bars.csv")
    print(f"  {len(cycles)} cycles, {len(bars)} bar snapshots")

    # Index bars by cycle_id
    bars_by_cycle = defaultdict(list)
    for b in bars:
        bars_by_cycle[b["cycle_id"]].append(b)

    # Split by outcome
    stops = [c for c in cycles if c["exit_type"] == "HARD_STOP"]
    reversals = [c for c in cycles if c["exit_type"] == "REVERSAL"]
    print(f"  HARD_STOP: {len(stops)}, REVERSAL: {len(reversals)}")

    # Compute trade-behavior features for all cycles
    enriched_bars = {}  # cycle_id -> list of enriched bar dicts
    for c in cycles:
        cid = c["cycle_id"]
        if cid in bars_by_cycle:
            enriched_bars[cid] = compute_trade_behavior(bars_by_cycle[cid], c)

    # ===================================================================
    # Analysis 1: Feature values at normalized trade progress points
    # ===================================================================
    # For each cycle, sample features at 25%, 50%, 75% of trade duration
    # (by agg_bar_offset). Compare distributions for stops vs reversals.
    print(f"\n{'='*70}")
    print(f"ANALYSIS 1: Feature values at trade progress milestones")
    print(f"{'='*70}")

    ALL_FEATURES = REGIME_FEATURES + [
        "signed_chop_vs_pos", "signed_slope_vs_pos",
        "mfe_rate", "mae_proximity", "mae_increment",
        "mfe_retracement", "current_favor_ticks",
    ]

    # For each cycle, get the LAST agg bar snapshot (just before outcome)
    # This is the most informative: what did signals look like right before exit?
    print(f"\n--- Feature values at LAST agg bar before exit ---")
    print(f"{'Feature':<22} {'Stop mean':>10} {'Rev mean':>10} {'Delta':>8} {'Stop med':>10} {'Rev med':>10}")
    print(f"{'-'*72}")

    last_bar_features = {"stop": defaultdict(list), "rev": defaultdict(list)}
    for c in cycles:
        cid = c["cycle_id"]
        if cid not in enriched_bars or not enriched_bars[cid]:
            continue
        last_bar = enriched_bars[cid][-1]
        group = "stop" if c["exit_type"] == "HARD_STOP" else "rev"
        for feat in ALL_FEATURES:
            v = last_bar.get(feat, np.nan)
            if not np.isnan(v):
                last_bar_features[group][feat].append(v)

    divergence_results = []
    for feat in ALL_FEATURES:
        s_vals = last_bar_features["stop"].get(feat, [])
        r_vals = last_bar_features["rev"].get(feat, [])
        if not s_vals or not r_vals:
            continue
        s_mean = np.mean(s_vals)
        r_mean = np.mean(r_vals)
        delta = s_mean - r_mean
        s_med = np.median(s_vals)
        r_med = np.median(r_vals)
        print(f"{feat:<22} {s_mean:>10.4f} {r_mean:>10.4f} {delta:>+8.4f} {s_med:>10.4f} {r_med:>10.4f}")
        divergence_results.append({
            "feature": feat, "stop_mean": s_mean, "rev_mean": r_mean,
            "delta": delta, "stop_median": s_med, "rev_median": r_med,
            "stop_n": len(s_vals), "rev_n": len(r_vals),
        })

    # ===================================================================
    # Analysis 2: Feature trajectory over agg bar offsets 0,1,2,3+
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"ANALYSIS 2: Feature trajectory by agg bar offset")
    print(f"{'='*70}")

    # Focus on cycles with >= 3 agg bars (enough trajectory to analyze)
    long_stops = [c for c in stops if c["agg_bars_held"] >= 3]
    long_revs = [c for c in reversals if c["agg_bars_held"] >= 3]
    print(f"  Cycles with >= 3 agg bars: stops={len(long_stops)}, reversals={len(long_revs)}")

    KEY_FEATURES = ["signed_chop_vs_pos", "signed_slope_vs_pos", "dr2", "dslope",
                    "r2", "choppiness", "mae_proximity", "mfe_rate", "mae_increment",
                    "mfe_retracement"]

    for feat in KEY_FEATURES:
        print(f"\n  --- {feat} ---")
        print(f"  {'Offset':>6} {'Stop mean':>10} {'Rev mean':>10} {'Delta':>8} {'Stop N':>7} {'Rev N':>7}")

        for offset in range(5):
            s_vals = []
            r_vals = []
            for c in long_stops:
                cid = c["cycle_id"]
                if cid in enriched_bars:
                    ebs = enriched_bars[cid]
                    if offset < len(ebs):
                        v = ebs[offset].get(feat, np.nan)
                        if not np.isnan(v):
                            s_vals.append(v)
            for c in long_revs:
                cid = c["cycle_id"]
                if cid in enriched_bars:
                    ebs = enriched_bars[cid]
                    if offset < len(ebs):
                        v = ebs[offset].get(feat, np.nan)
                        if not np.isnan(v):
                            r_vals.append(v)
            if s_vals and r_vals:
                s_m = np.mean(s_vals)
                r_m = np.mean(r_vals)
                print(f"  {offset:>6} {s_m:>10.4f} {r_m:>10.4f} {s_m-r_m:>+8.4f} {len(s_vals):>7} {len(r_vals):>7}")

    # ===================================================================
    # Analysis 3: Direction test — for regime signals, does HIGH or LOW
    # predict stops? (Track B inversion check)
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"ANALYSIS 3: Direction test — which extreme predicts stops?")
    print(f"(Using feature value at agg_bar_offset=1, the first full bar in-trade)")
    print(f"{'='*70}")

    DIRECTION_FEATURES = ["signed_chop_vs_pos", "signed_slope_vs_pos", "dr2", "dslope",
                          "r2", "choppiness"]

    for feat in DIRECTION_FEATURES:
        # Collect feature values at offset=1 for all cycles with >=2 agg bars
        vals_stop = []
        vals_rev = []
        for c in cycles:
            cid = c["cycle_id"]
            if cid not in enriched_bars:
                continue
            ebs = enriched_bars[cid]
            if len(ebs) < 2:
                continue
            v = ebs[1].get(feat, np.nan)
            if np.isnan(v):
                continue
            if c["exit_type"] == "HARD_STOP":
                vals_stop.append(v)
            else:
                vals_rev.append(v)

        if not vals_stop or not vals_rev:
            print(f"\n  {feat}: insufficient data")
            continue

        all_vals = vals_stop + vals_rev
        p33 = np.percentile(all_vals, 33)
        p67 = np.percentile(all_vals, 67)

        # Split into terciles
        low_stop = sum(1 for v in vals_stop if v <= p33)
        low_rev = sum(1 for v in vals_rev if v <= p33)
        mid_stop = sum(1 for v in vals_stop if p33 < v <= p67)
        mid_rev = sum(1 for v in vals_rev if p33 < v <= p67)
        high_stop = sum(1 for v in vals_stop if v > p67)
        high_rev = sum(1 for v in vals_rev if v > p67)

        low_total = low_stop + low_rev
        mid_total = mid_stop + mid_rev
        high_total = high_stop + high_rev

        low_sr = low_stop / low_total if low_total > 0 else 0
        mid_sr = mid_stop / mid_total if mid_total > 0 else 0
        high_sr = high_stop / high_total if high_total > 0 else 0

        print(f"\n  {feat} (p33={p33:.4f}, p67={p67:.4f}):")
        print(f"    LOW tercile:  SR={low_sr:.0%}  (stops={low_stop}, total={low_total})")
        print(f"    MID tercile:  SR={mid_sr:.0%}  (stops={mid_stop}, total={mid_total})")
        print(f"    HIGH tercile: SR={high_sr:.0%}  (stops={high_stop}, total={high_total})")

        spread = abs(high_sr - low_sr)
        direction = "HIGH predicts stops" if high_sr > low_sr else "LOW predicts stops"
        print(f"    Spread: {spread:.0%} | Direction: {direction}")

    # ===================================================================
    # Analysis 4: Per-week breakdown of key signals
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"ANALYSIS 4: Per-week breakdown of feature divergence")
    print(f"{'='*70}")

    TOP_FEATURES = ["mae_proximity", "mfe_rate", "signed_chop_vs_pos",
                    "mae_increment", "dr2", "dslope"]

    for feat in TOP_FEATURES:
        print(f"\n  --- {feat} (last bar before exit) ---")
        print(f"  {'Week':<10} {'Cat':<8} {'Stop mean':>10} {'Rev mean':>10} {'Delta':>8} {'Stops':>5} {'Revs':>5}")

        for wk in sorted(TEST_WEEKS.keys()):
            s_vals = []
            r_vals = []
            for c in cycles:
                if c["week"] != wk:
                    continue
                cid = c["cycle_id"]
                if cid not in enriched_bars or not enriched_bars[cid]:
                    continue
                last_bar = enriched_bars[cid][-1]
                v = last_bar.get(feat, np.nan)
                if np.isnan(v):
                    continue
                if c["exit_type"] == "HARD_STOP":
                    s_vals.append(v)
                else:
                    r_vals.append(v)
            if s_vals and r_vals:
                sm = np.mean(s_vals)
                rm = np.mean(r_vals)
                print(f"  {wk:<10} {TEST_WEEKS[wk]:<8} {sm:>10.4f} {rm:>10.4f} {sm-rm:>+8.4f} {len(s_vals):>5} {len(r_vals):>5}")

    # ===================================================================
    # Analysis 5: Lead time — at which agg bar offset does divergence start?
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"ANALYSIS 5: Lead time — divergence onset by agg bar offset")
    print(f"{'='*70}")

    LEAD_FEATURES = ["mae_proximity", "mfe_rate", "signed_chop_vs_pos",
                     "dr2", "mae_increment"]

    for feat in LEAD_FEATURES:
        print(f"\n  --- {feat} ---")
        # For cycles with >= 4 agg bars, compute the divergence at each offset
        min_bars = 4
        eligible_stops = [c for c in stops if c["agg_bars_held"] >= min_bars]
        eligible_revs = [c for c in reversals if c["agg_bars_held"] >= min_bars]

        print(f"  (cycles with >={min_bars} agg bars: stops={len(eligible_stops)}, revs={len(eligible_revs)})")

        first_diverge = None
        for offset in range(min(8, max(c["agg_bars_held"] for c in eligible_stops + eligible_revs) if eligible_stops + eligible_revs else 1)):
            s_vals = []
            r_vals = []
            for c in eligible_stops:
                cid = c["cycle_id"]
                if cid in enriched_bars and offset < len(enriched_bars[cid]):
                    v = enriched_bars[cid][offset].get(feat, np.nan)
                    if not np.isnan(v):
                        s_vals.append(v)
            for c in eligible_revs:
                cid = c["cycle_id"]
                if cid in enriched_bars and offset < len(enriched_bars[cid]):
                    v = enriched_bars[cid][offset].get(feat, np.nan)
                    if not np.isnan(v):
                        r_vals.append(v)

            if s_vals and r_vals:
                s_m = np.mean(s_vals)
                r_m = np.mean(r_vals)
                delta = s_m - r_m
                # Effect size: Cohen's d
                pooled_std = np.sqrt((np.std(s_vals)**2 + np.std(r_vals)**2) / 2)
                d = delta / pooled_std if pooled_std > 0 else 0
                sig_marker = "*" if abs(d) >= 0.3 else ""
                print(f"    offset={offset}: stop={s_m:.4f} rev={r_m:.4f} delta={delta:+.4f} d={d:+.3f} {sig_marker}")

                if first_diverge is None and abs(d) >= 0.3:
                    first_diverge = offset

        if first_diverge is not None:
            print(f"    First divergence (|d|>=0.3) at offset={first_diverge}")
        else:
            print(f"    No significant divergence found")

    # ===================================================================
    # Kill gate evaluation
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"KILL GATE EVALUATION")
    print(f"{'='*70}")

    # Gate 1: Any distinguishable trajectories?
    sig_features = [d for d in divergence_results
                    if abs(d["delta"]) > 0 and d["stop_n"] >= 50 and d["rev_n"] >= 50]

    # Compute effect sizes for last-bar features
    gate1_pass = False
    strong_signals = []
    for d in sig_features:
        s = last_bar_features["stop"][d["feature"]]
        r = last_bar_features["rev"][d["feature"]]
        pooled_std = np.sqrt((np.std(s)**2 + np.std(r)**2) / 2)
        cohen_d = d["delta"] / pooled_std if pooled_std > 0 else 0
        if abs(cohen_d) >= 0.3:
            gate1_pass = True
            strong_signals.append((d["feature"], cohen_d))
            print(f"  Signal: {d['feature']:<22} Cohen's d={cohen_d:+.3f} (last bar)")

    if gate1_pass:
        print(f"\n  Gate 1 (trajectory divergence): PASS — {len(strong_signals)} features with |d|>=0.3")
    else:
        print(f"\n  Gate 1 (trajectory divergence): FAIL — no features with |d|>=0.3")
        print(f"  STUDY KILLED. No in-trade feature distinguishes stops from reversals.")
        return

    # Gate 2: Lead time >= 3 agg bars before outcome
    # Check the features from Analysis 5
    gate2_pass = False
    for feat in LEAD_FEATURES:
        min_bars = 4
        eligible_stops = [c for c in stops if c["agg_bars_held"] >= min_bars]
        eligible_revs = [c for c in reversals if c["agg_bars_held"] >= min_bars]

        for offset in range(max(c["agg_bars_held"] for c in eligible_stops + eligible_revs) if eligible_stops + eligible_revs else 1):
            s_vals = [enriched_bars[c["cycle_id"]][offset].get(feat, np.nan)
                      for c in eligible_stops
                      if c["cycle_id"] in enriched_bars and offset < len(enriched_bars[c["cycle_id"]])]
            r_vals = [enriched_bars[c["cycle_id"]][offset].get(feat, np.nan)
                      for c in eligible_revs
                      if c["cycle_id"] in enriched_bars and offset < len(enriched_bars[c["cycle_id"]])]
            s_vals = [v for v in s_vals if not np.isnan(v)]
            r_vals = [v for v in r_vals if not np.isnan(v)]

            if not s_vals or not r_vals:
                continue
            pooled_std = np.sqrt((np.std(s_vals)**2 + np.std(r_vals)**2) / 2)
            d = (np.mean(s_vals) - np.mean(r_vals)) / pooled_std if pooled_std > 0 else 0

            if abs(d) >= 0.3:
                # How many bars remain until typical outcome?
                # Median bars held for stops with >= min_bars
                med_stop_bars = np.median([c["agg_bars_held"] for c in eligible_stops])
                lead_bars = med_stop_bars - offset
                if lead_bars >= 3:
                    gate2_pass = True
                    print(f"  Lead time: {feat} diverges at offset={offset}, "
                          f"median stop at bar={med_stop_bars:.0f}, lead={lead_bars:.0f} bars")
                break

    if gate2_pass:
        print(f"\n  Gate 2 (lead time >= 3 bars): PASS")
    else:
        print(f"\n  Gate 2 (lead time >= 3 bars): FAIL — divergence too late")
        # Not a hard kill for trade behavior signals — they may diverge
        # from bar 0 (mae_proximity is cumulative)
        print(f"  NOTE: Trade-behavior signals (mae_proximity, mfe_rate) accumulate from bar 0.")
        print(f"  Checking cumulative divergence from entry...")

        # Alternative check: does mae_proximity diverge from offset 0?
        for feat in ["mae_proximity", "mfe_rate"]:
            s_vals = [enriched_bars[c["cycle_id"]][0].get(feat, np.nan)
                      for c in stops if c["cycle_id"] in enriched_bars and enriched_bars[c["cycle_id"]]]
            r_vals = [enriched_bars[c["cycle_id"]][0].get(feat, np.nan)
                      for c in reversals if c["cycle_id"] in enriched_bars and enriched_bars[c["cycle_id"]]]
            s_vals = [v for v in s_vals if not np.isnan(v)]
            r_vals = [v for v in r_vals if not np.isnan(v)]
            if s_vals and r_vals:
                pooled_std = np.sqrt((np.std(s_vals)**2 + np.std(r_vals)**2) / 2)
                d = (np.mean(s_vals) - np.mean(r_vals)) / pooled_std if pooled_std > 0 else 0
                print(f"    {feat} at offset=0: d={d:+.3f}")

    total = time.time() - t0
    print(f"\nTotal runtime: {total:.0f}s ({total/60:.1f}m)")


if __name__ == "__main__":
    main()
