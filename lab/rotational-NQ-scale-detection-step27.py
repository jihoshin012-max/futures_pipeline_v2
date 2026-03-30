# archetype: rotational
"""
rotational-NQ-scale-detection-step27.py -- Track C Step 4: Loss replay analysis.

For each management action, replay all cycles in the 5 test weeks and compute
net benefit = (savings on stops) - (cost on prematurely cut winners).

Actions:
  1. Early exit: signed_chop_vs_pos < threshold AND/OR mae_proximity > threshold
  2. Skip add: mae_increment > threshold at add bar (agg_bar with add event)
  3. Tighten stop: sweep tighten levels 30-50 ticks with choppiness trigger
  4. Break-even stop: MFE > N ticks -> move stop to 0

Prompt: rotational-NQ-prompt-trade-management-c.md Step 4
"""
from __future__ import annotations

import csv
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

OUTPUT_DIR = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")

HARD_STOP_TICKS = 60.0
TICK_VALUE = 5.0
COMMISSION_PER_RT_MINI = 3.50

TEST_WEEKS = {
    "2025-W40": "WEAKEST",
    "2025-W41": "MID",
    "2025-W46": "GOOD",
    "2025-W47": "BEST",
    "2025-W48": "LOW",
}


def load_bars(path):
    bars = []
    with open(path, newline="") as f:
        for r in csv.DictReader(f):
            r["cycle_id"] = int(r["cycle_id"])
            r["agg_bar_offset"] = int(r["agg_bar_offset"])
            r["direction"] = int(r["direction"])
            for k in ["price", "pnl_ticks", "mfe_ticks", "mae_ticks",
                       "signed_chop", "dchop", "d2chop", "signed_slope",
                       "dr2", "dslope", "r2", "choppiness", "slope_abs",
                       "agg_bar_range"]:
                v = r[k]
                r[k] = float(v) if v != "" else np.nan
            bars.append(r)
    return bars


def load_cycles(path):
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


def enrich_bars(bars_by_cycle, cycle_map):
    """Add derived features to bar snapshots."""
    for cid, blist in bars_by_cycle.items():
        c = cycle_map[cid]
        dir_sign = 1 if c["direction"] == "LONG" else -1
        max_pos = c["max_position"]
        prev_mae = 0.0
        entry_range = None
        for b in blist:
            abo = b["agg_bar_offset"]
            b["mfe_rate"] = b["mfe_ticks"] / (abo + 1) if abo >= 0 else np.nan
            b["mae_proximity"] = b["mae_ticks"] / HARD_STOP_TICKS
            b["mae_increment"] = b["mae_ticks"] - prev_mae
            prev_mae = b["mae_ticks"]
            b["current_favor_ticks"] = b["pnl_ticks"] / max_pos if max_pos > 0 else 0
            if not np.isnan(b["signed_chop"]):
                b["signed_chop_vs_pos"] = b["signed_chop"] * dir_sign
            else:
                b["signed_chop_vs_pos"] = np.nan
            # Range ratio
            if entry_range is None and not np.isnan(b["agg_bar_range"]):
                entry_range = b["agg_bar_range"]
            if entry_range and entry_range > 0 and not np.isnan(b["agg_bar_range"]):
                b["range_ratio"] = b["agg_bar_range"] / entry_range
            else:
                b["range_ratio"] = np.nan


def net_pnl(cycle):
    comm = COMMISSION_PER_RT_MINI * max(cycle.get("max_position", 1), 1)
    return cycle["pnl_ticks"] * TICK_VALUE - comm


# ---------------------------------------------------------------------------
#  EARLY EXIT REPLAY
# ---------------------------------------------------------------------------

def replay_early_exit(cycles, bars_by_cycle, cycle_map, chop_thresh, mae_prox_thresh):
    """For each cycle: if signed_chop_vs_pos < chop_thresh AND mae_proximity > mae_prox_thresh
    at any agg bar, what would the PnL be if we exited at that bar's price?"""
    results = {"stop_savings": [], "winner_costs": [], "fired_on_stops": 0,
               "fired_on_winners": 0, "total_stops": 0, "total_winners": 0}

    for c in cycles:
        cid = c["cycle_id"]
        blist = bars_by_cycle.get(cid, [])
        is_stop = c["exit_type"] == "HARD_STOP"
        actual_pnl = net_pnl(c)
        max_pos = c["max_position"]
        comm = COMMISSION_PER_RT_MINI * max(max_pos, 1)

        if is_stop:
            results["total_stops"] += 1
        else:
            results["total_winners"] += 1

        # Find first bar where signal fires
        fired = False
        for b in blist:
            sc = b.get("signed_chop_vs_pos", np.nan)
            mp = b.get("mae_proximity", 0)
            if np.isnan(sc):
                continue
            if sc < chop_thresh and mp > mae_prox_thresh:
                # Exit at this bar's price
                exit_pnl_ticks = b["current_favor_ticks"] * max_pos
                alt_pnl = exit_pnl_ticks * TICK_VALUE - comm
                fired = True

                if is_stop:
                    savings = alt_pnl - actual_pnl  # positive = saved money
                    results["stop_savings"].append(savings)
                    results["fired_on_stops"] += 1
                else:
                    cost = actual_pnl - alt_pnl  # positive = lost money
                    results["winner_costs"].append(cost)
                    results["fired_on_winners"] += 1
                break

    return results


# ---------------------------------------------------------------------------
#  SKIP ADD REPLAY
# ---------------------------------------------------------------------------

def replay_skip_add(cycles, bars_by_cycle, cycle_map, mae_incr_thresh):
    """At the add point (~10pts against, max_position > 1), check mae_increment.
    If > threshold, would have stayed at 1 contract instead of 2.

    For depth_1: add happens when price moves step_dist against entry.
    The add bar is approximately where mae_ticks first reaches step_dist (40 ticks for 10pts).
    """
    results = {"stop_savings": [], "winner_costs": [], "fired_on_stops": 0,
               "fired_on_winners": 0, "total_adds": 0}

    for c in cycles:
        if c["max_position"] <= 1:
            continue  # no add on this cycle
        results["total_adds"] += 1

        cid = c["cycle_id"]
        blist = bars_by_cycle.get(cid, [])
        is_stop = c["exit_type"] == "HARD_STOP"
        actual_pnl = net_pnl(c)

        # Find the bar closest to the add point (mae_ticks ~ 40 = step_dist in ticks)
        # Step dist = 10pts = 40 ticks
        add_bar = None
        for b in blist:
            if b["mae_ticks"] >= 38:  # approximately at add point
                add_bar = b
                break

        if add_bar is None:
            continue

        mae_incr = add_bar.get("mae_increment", 0)
        if mae_incr <= mae_incr_thresh:
            continue  # signal didn't fire, add proceeds normally

        # Signal fired -> skip add (stay at 1 contract)
        # For HARD_STOP: loss with 1 contract = -(hard_stop_ticks * 5 + comm_1)
        # vs actual loss with 2 contracts
        comm_1 = COMMISSION_PER_RT_MINI * 1
        comm_2 = COMMISSION_PER_RT_MINI * 2

        if is_stop:
            # With add: lost at avg_entry with 2 contracts
            # Without add: lost at entry price with 1 contract, stop at same tick distance
            # Approximate: with 1 contract, loss = -(hard_stop_ticks * 5 + comm_1)
            # This is approximate — actual loss depends on avg_entry vs d0 entry
            alt_pnl = -(HARD_STOP_TICKS * TICK_VALUE) - comm_1
            savings = alt_pnl - actual_pnl  # savings is positive when alt is less negative
            results["stop_savings"].append(savings)
            results["fired_on_stops"] += 1
        else:
            # Winner with add: actual_pnl includes 2-contract profit
            # Winner without add: 1 contract * pnl_per_contract
            # Approximate: pnl_per_contract = pnl_ticks / max_pos, then * 1 * 5 - comm_1
            pnl_per_contract = c["pnl_ticks"] / c["max_position"]
            alt_pnl = pnl_per_contract * TICK_VALUE - comm_1
            cost = actual_pnl - alt_pnl  # cost of foregone profit
            results["winner_costs"].append(cost)
            results["fired_on_winners"] += 1

    return results


# ---------------------------------------------------------------------------
#  TIGHTEN STOP REPLAY
# ---------------------------------------------------------------------------

def replay_tighten_stop(cycles, bars_by_cycle, cycle_map, chop_thresh, tighten_to):
    """If choppiness > chop_thresh mid-trade, tighten stop from 60 to tighten_to ticks.
    Check whether the trade would have stopped at the tighter level."""
    results = {"stop_savings": [], "winner_costs": [],
               "fired": 0, "total": len(cycles),
               "tighter_stops_on_stops": 0, "tighter_stops_on_winners": 0}

    for c in cycles:
        cid = c["cycle_id"]
        blist = bars_by_cycle.get(cid, [])
        is_stop = c["exit_type"] == "HARD_STOP"
        actual_pnl = net_pnl(c)
        comm = COMMISSION_PER_RT_MINI * max(c.get("max_position", 1), 1)

        # Check if signal fires on any bar
        signal_fired = False
        for b in blist:
            chop = b.get("choppiness", np.nan)
            if not np.isnan(chop) and chop > chop_thresh:
                signal_fired = True
                break

        if not signal_fired:
            continue
        results["fired"] += 1

        # After signal fires, check if mae_ticks ever reaches tighten_to
        # (i.e., would the trade have been stopped at the tighter level?)
        tighter_hit = False
        tighter_bar = None
        signal_active = False
        for b in blist:
            chop = b.get("choppiness", np.nan)
            if not signal_active:
                if not np.isnan(chop) and chop > chop_thresh:
                    signal_active = True
            if signal_active and b["mae_ticks"] >= tighten_to:
                tighter_hit = True
                tighter_bar = b
                break

        if not tighter_hit:
            continue  # trade survived even at tighter level

        # Tighter stop hit
        exit_pnl_ticks = -(tighten_to) * c["max_position"]
        alt_pnl = exit_pnl_ticks * TICK_VALUE - comm

        if is_stop:
            # Would have stopped earlier at smaller loss
            savings = alt_pnl - actual_pnl
            results["stop_savings"].append(savings)
            results["tighter_stops_on_stops"] += 1
        else:
            # Would have turned a winner into a loss
            cost = actual_pnl - alt_pnl
            results["winner_costs"].append(cost)
            results["tighter_stops_on_winners"] += 1

    return results


# ---------------------------------------------------------------------------
#  BREAK-EVEN STOP REPLAY
# ---------------------------------------------------------------------------

def replay_breakeven_stop(cycles, bars_by_cycle, cycle_map, mfe_threshold):
    """Once MFE > threshold, move stop to 0 (entry price).
    Check whether the trade would have been stopped at breakeven."""
    results = {"stop_savings": [], "winner_costs": [],
               "fired": 0, "total": len(cycles),
               "be_stops_on_stops": 0, "be_stops_on_winners": 0}

    for c in cycles:
        cid = c["cycle_id"]
        blist = bars_by_cycle.get(cid, [])
        is_stop = c["exit_type"] == "HARD_STOP"
        actual_pnl = net_pnl(c)
        comm = COMMISSION_PER_RT_MINI * max(c.get("max_position", 1), 1)

        # Check if MFE ever exceeds threshold
        be_armed = False
        for b in blist:
            if not be_armed and b["mfe_ticks"] >= mfe_threshold:
                be_armed = True
            if be_armed:
                # Check if price returned to entry (current_favor_ticks <= 0)
                if b["current_favor_ticks"] <= 0:
                    # Stopped at breakeven
                    alt_pnl = -comm  # 0 PnL minus commission
                    results["fired"] += 1

                    if is_stop:
                        savings = alt_pnl - actual_pnl
                        results["stop_savings"].append(savings)
                        results["be_stops_on_stops"] += 1
                    else:
                        cost = actual_pnl - alt_pnl
                        results["winner_costs"].append(cost)
                        results["be_stops_on_winners"] += 1
                    break

    return results


# ---------------------------------------------------------------------------
#  Print helpers
# ---------------------------------------------------------------------------

def print_action_results(name, results, cycles_in_week):
    total_savings = sum(results["stop_savings"]) if results["stop_savings"] else 0
    total_costs = sum(results["winner_costs"]) if results["winner_costs"] else 0
    net = total_savings - total_costs

    print(f"\n  {name}:")
    print(f"    Fired on stops: {results.get('fired_on_stops', results.get('tighter_stops_on_stops', results.get('be_stops_on_stops', 0)))}"
          f"/{results.get('total_stops', results.get('total', len(cycles_in_week)))}")
    print(f"    Fired on winners: {results.get('fired_on_winners', results.get('tighter_stops_on_winners', results.get('be_stops_on_winners', 0)))}"
          f" (winner-touch rate: {results.get('fired_on_winners', results.get('tighter_stops_on_winners', results.get('be_stops_on_winners', 0)))/max(1,len(cycles_in_week) - results.get('total_stops', 0)):.0%})")
    if results["stop_savings"]:
        print(f"    Avg savings on stops: ${np.mean(results['stop_savings']):+.2f}")
        print(f"    Total savings: ${total_savings:+,.0f}")
    if results["winner_costs"]:
        print(f"    Avg cost on winners: ${np.mean(results['winner_costs']):+.2f}")
        print(f"    Total cost: ${total_costs:+,.0f}")
    print(f"    NET BENEFIT: ${net:+,.0f}")
    return net


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def main():
    t0 = time.time()

    print("Loading Step 1 data...")
    cycles = load_cycles(OUTPUT_DIR / "trade-mgmt-tagged-cycles.csv")
    bars = load_bars(OUTPUT_DIR / "trade-mgmt-intrade-bars.csv")

    cycle_map = {c["cycle_id"]: c for c in cycles}
    bars_by_cycle = defaultdict(list)
    for b in bars:
        bars_by_cycle[b["cycle_id"]].append(b)

    enrich_bars(bars_by_cycle, cycle_map)

    # Group by week
    weeks = defaultdict(list)
    for c in cycles:
        weeks[c["week"]].append(c)

    # ===================================================================
    # EARLY EXIT REPLAY
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"EARLY EXIT REPLAY")
    print(f"{'='*70}")

    # Sweep thresholds
    chop_thresholds = [-0.30, -0.25, -0.20, -0.15, -0.10]
    mae_prox_thresholds = [0.30, 0.40, 0.50]

    print(f"\n{'Chop<':<8} {'MAE>':<6} | {'Net/wk':>8} {'Saves':>8} {'Costs':>8} "
          f"{'Fire%S':>7} {'Fire%W':>7} {'AllWks':>6}")
    print(f"{'-'*70}")

    best_ee_config = None
    best_ee_net = -1e9

    for ct in chop_thresholds:
        for mt in mae_prox_thresholds:
            week_nets = []
            total_fire_stops = 0
            total_fire_winners = 0
            total_stops = 0
            total_winners = 0

            for wk in sorted(TEST_WEEKS.keys()):
                wk_cycles = weeks[wk]
                r = replay_early_exit(wk_cycles, bars_by_cycle, cycle_map, ct, mt)
                net = sum(r["stop_savings"]) - sum(r["winner_costs"])
                week_nets.append(net)
                total_fire_stops += r["fired_on_stops"]
                total_fire_winners += r["fired_on_winners"]
                total_stops += r["total_stops"]
                total_winners += r["total_winners"]

            all_positive = all(n >= 0 for n in week_nets)
            avg_net = np.mean(week_nets)
            fire_s = total_fire_stops / max(1, total_stops)
            fire_w = total_fire_winners / max(1, total_winners)
            marker = " PASS" if all_positive and fire_w < 0.20 else ""

            print(f"{ct:<8.2f} {mt:<6.2f} | ${avg_net:>7,.0f} ${sum(s for n, s in zip(week_nets, week_nets)):>7,.0f} "
                  f"  {fire_s:>5.0%}   {fire_w:>5.0%}   {'Y' if all_positive else 'N':>4}{marker}")

            if all_positive and fire_w < 0.20 and avg_net > best_ee_net:
                best_ee_net = avg_net
                best_ee_config = (ct, mt)

    # Detail the best config per week
    if best_ee_config:
        ct, mt = best_ee_config
        print(f"\n  Best early exit config: chop<{ct:.2f} AND mae_prox>{mt:.2f}")
        for wk in sorted(TEST_WEEKS.keys()):
            wk_cycles = weeks[wk]
            r = replay_early_exit(wk_cycles, bars_by_cycle, cycle_map, ct, mt)
            net = print_action_results(f"W{wk[-2:]} ({TEST_WEEKS[wk]})", r, wk_cycles)

    # ===================================================================
    # SKIP ADD REPLAY
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"SKIP ADD REPLAY")
    print(f"{'='*70}")

    mae_incr_thresholds = [5, 8, 10, 12, 15, 20]

    print(f"\n{'MAE_incr>':<10} | {'Net/wk':>8} {'Fire%':>6} {'AllWks':>6}")
    print(f"{'-'*40}")

    best_sa_config = None
    best_sa_net = -1e9

    for mit in mae_incr_thresholds:
        week_nets = []
        total_fired = 0
        total_adds = 0

        for wk in sorted(TEST_WEEKS.keys()):
            wk_cycles = weeks[wk]
            r = replay_skip_add(wk_cycles, bars_by_cycle, cycle_map, mit)
            net = sum(r["stop_savings"]) - sum(r["winner_costs"])
            week_nets.append(net)
            total_fired += r["fired_on_stops"] + r["fired_on_winners"]
            total_adds += r["total_adds"]

        all_positive = all(n >= 0 for n in week_nets)
        avg_net = np.mean(week_nets)
        fire_rate = total_fired / max(1, total_adds)
        marker = " PASS" if all_positive else ""

        print(f"{mit:<10} | ${avg_net:>7,.0f}  {fire_rate:>5.0%}   {'Y' if all_positive else 'N':>4}{marker}")

        if all_positive and avg_net > best_sa_net:
            best_sa_net = avg_net
            best_sa_config = mit

    if best_sa_config:
        print(f"\n  Best skip-add config: mae_increment > {best_sa_config}")
        for wk in sorted(TEST_WEEKS.keys()):
            wk_cycles = weeks[wk]
            r = replay_skip_add(wk_cycles, bars_by_cycle, cycle_map, best_sa_config)
            net = print_action_results(f"W{wk[-2:]} ({TEST_WEEKS[wk]})", r, wk_cycles)

    # ===================================================================
    # TIGHTEN STOP REPLAY
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"TIGHTEN STOP REPLAY")
    print(f"{'='*70}")

    chop_tighten_thresholds = [0.25, 0.30, 0.35, 0.40]
    tighten_levels = [30, 35, 40, 45, 50]

    print(f"\n{'Chop>':<8} {'Tighten':<8} | {'Net/wk':>8} {'AllWks':>6}")
    print(f"{'-'*40}")

    best_ts_config = None
    best_ts_net = -1e9

    for cht in chop_tighten_thresholds:
        for tl in tighten_levels:
            week_nets = []
            for wk in sorted(TEST_WEEKS.keys()):
                wk_cycles = weeks[wk]
                r = replay_tighten_stop(wk_cycles, bars_by_cycle, cycle_map, cht, tl)
                net = sum(r["stop_savings"]) - sum(r["winner_costs"])
                week_nets.append(net)

            all_positive = all(n >= 0 for n in week_nets)
            avg_net = np.mean(week_nets)
            marker = " PASS" if all_positive else ""

            print(f"{cht:<8.2f} {tl:<8} | ${avg_net:>7,.0f}   {'Y' if all_positive else 'N':>4}{marker}")

            if all_positive and avg_net > best_ts_net:
                best_ts_net = avg_net
                best_ts_config = (cht, tl)

    if best_ts_config:
        cht, tl = best_ts_config
        print(f"\n  Best tighten config: chop>{cht:.2f} -> tighten to {tl} ticks")
        for wk in sorted(TEST_WEEKS.keys()):
            wk_cycles = weeks[wk]
            r = replay_tighten_stop(wk_cycles, bars_by_cycle, cycle_map, cht, tl)
            net = print_action_results(f"W{wk[-2:]} ({TEST_WEEKS[wk]})", r, wk_cycles)

    # ===================================================================
    # BREAK-EVEN STOP REPLAY
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"BREAK-EVEN STOP REPLAY")
    print(f"{'='*70}")

    be_thresholds = [10, 15, 20, 25, 30]

    print(f"\n{'MFE>':<8} | {'Net/wk':>8} {'AllWks':>6}")
    print(f"{'-'*30}")

    best_be_config = None
    best_be_net = -1e9

    for bet in be_thresholds:
        week_nets = []
        for wk in sorted(TEST_WEEKS.keys()):
            wk_cycles = weeks[wk]
            r = replay_breakeven_stop(wk_cycles, bars_by_cycle, cycle_map, bet)
            net = sum(r["stop_savings"]) - sum(r["winner_costs"])
            week_nets.append(net)

        all_positive = all(n >= 0 for n in week_nets)
        avg_net = np.mean(week_nets)
        marker = " PASS" if all_positive else ""

        print(f"{bet:<8} | ${avg_net:>7,.0f}   {'Y' if all_positive else 'N':>4}{marker}")

        if all_positive and avg_net > best_be_net:
            best_be_net = avg_net
            best_be_config = bet

    if best_be_config:
        print(f"\n  Best break-even config: MFE > {best_be_config} ticks")
        for wk in sorted(TEST_WEEKS.keys()):
            wk_cycles = weeks[wk]
            r = replay_breakeven_stop(wk_cycles, bars_by_cycle, cycle_map, best_be_config)
            net = print_action_results(f"W{wk[-2:]} ({TEST_WEEKS[wk]})", r, wk_cycles)

    # ===================================================================
    # SUMMARY
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"STEP 4 SUMMARY")
    print(f"{'='*70}")

    actions = []
    if best_ee_config:
        actions.append(("Early exit", f"chop<{best_ee_config[0]:.2f} + mae>{best_ee_config[1]:.2f}", best_ee_net))
    if best_sa_config:
        actions.append(("Skip add", f"mae_incr>{best_sa_config}", best_sa_net))
    if best_ts_config:
        actions.append(("Tighten stop", f"chop>{best_ts_config[0]:.2f} -> {best_ts_config[1]}t", best_ts_net))
    if best_be_config:
        actions.append(("Break-even", f"MFE>{best_be_config}t", best_be_net))

    print(f"\n  {'Action':<15} {'Config':<30} {'Avg Net/wk':>12}")
    print(f"  {'-'*60}")
    for name, config, net in actions:
        print(f"  {name:<15} {config:<30} ${net:>11,.0f}")

    if not actions:
        print(f"\n  NO ACTIONS PASSED. All net benefits negative on at least one week.")
        print(f"  STUDY KILLED at Step 4.")

    total = time.time() - t0
    print(f"\nRuntime: {total:.0f}s")


if __name__ == "__main__":
    main()
