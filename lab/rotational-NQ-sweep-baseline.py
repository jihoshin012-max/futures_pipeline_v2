# archetype: rotational
"""LP Sweep v2 — baseline sweep for rotation/martingale strategy.

Three-step baseline across SD × Depth × HardStop:
  - Depth 0 (MCS=1): pure rotation, no adds
  - Depth 1 (MCS=2): 1 martingale add
  - Depth 2 (MCS=4): 2 martingale adds
  - Depth 3 (MCS=8): 3 martingale adds

HardStop is derived from SD and depth — minimum threshold for adds to fire,
then multiples above minimum to test recovery room.

Full unconstrained sweep — no MLL/eval/funded filtering during run.
Each config tagged with viability post-hoc. Prop firm constraints applied
as filters on saved results.

Saves all cycle-level data for post-processing (30-min blocks, regime analysis).

Commission: $3.50 per round-turn mini contract.

Usage:
    python lp_sweep.py [--bar-file PATH] [--output-dir PATH]
"""

from __future__ import annotations

import csv
import math
import time
from pathlib import Path

import numpy as np


# ---------------------------------------------------------------------------
#  Fixed params
# ---------------------------------------------------------------------------
INITIAL_QTY = 1
TICK_SIZE = 0.25
COMMISSION_PER_RT_MINI = 3.50  # confirmed by user
MLL = 2000.0  # LucidFlex 50K max loss limit

# RTH boundaries (ET)
RTH_OPEN_SEC = 9 * 3600 + 30 * 60       # 09:30:00
RTH_CLOSE_SEC = 15 * 3600 + 49 * 60 + 50  # 15:49:50


# ---------------------------------------------------------------------------
#  Config builder — baseline grid
# ---------------------------------------------------------------------------
def build_configs() -> list[dict]:
    """Build baseline sweep configurations.

    For each SD × depth, HS is derived from the minimum threshold for that depth,
    then tested at multiples above minimum.

    Depth 0: HS values below and around SD_ticks (pure rotation, stop only)
    Depth 1+: HS at min, 1.25x, 1.5x, 2x, 2.5x, 3x of minimum
    """
    configs = []
    config_id = 0

    STEP_DISTS = [10.0, 15.0, 20.0, 25.0, 30.0, 50.0]

    # Depth configs: (label, max_contract_size, max_levels, depth_for_hs_calc)
    DEPTH_CONFIGS = [
        ("depth_0", 1, 1, 0),   # pure rotation
        ("depth_1", 2, 1, 1),   # 1 add
        ("depth_2", 4, 2, 2),   # 2 adds
        ("depth_3", 8, 3, 3),   # 3 adds
    ]

    HS_MULTIPLIERS_MARTINGALE = [1.0, 1.25, 1.5, 2.0, 2.5, 3.0]

    for sd in STEP_DISTS:
        sd_ticks = sd / TICK_SIZE

        for label, mcs, ml, depth in DEPTH_CONFIGS:

            if depth == 0:
                # Pure rotation: test HS at fractions and multiples of SD_ticks
                # These configs never add — HS just controls stop distance
                hs_values = []
                for mult in [0.25, 0.5, 0.75, 1.0, 1.5, 2.0]:
                    hs_values.append(round(sd_ticks * mult))
                # Deduplicate and sort
                hs_values = sorted(set(max(5, v) for v in hs_values))
            else:
                # Martingale: HS minimum from math doc
                # Add k+1 requires HS >= d * (2 - 2^(-k)) / ticksize
                # For depth adds to all fire, need HS >= d * (2 - 2^(-(depth-1))) / ticksize
                if depth == 1:
                    hs_min = sd / TICK_SIZE  # = sd_ticks
                elif depth == 2:
                    hs_min = sd * 1.5 / TICK_SIZE
                elif depth == 3:
                    hs_min = sd * 1.75 / TICK_SIZE
                else:
                    hs_min = sd * (2.0 - 2.0**(-(depth-1))) / TICK_SIZE

                hs_values = sorted(set(
                    round(hs_min * m) for m in HS_MULTIPLIERS_MARTINGALE
                ))
                hs_values = [v for v in hs_values if v >= 5]

            for hs in hs_values:
                max_loss = mcs * hs * 5.0  # max_position × HS_ticks × $5/tick
                configs.append({
                    "config_id": config_id,
                    "label": label,
                    "step_dist": sd,
                    "hard_stop": float(hs),
                    "max_levels": ml,
                    "max_contract_size": mcs,
                    "max_fades": 0,
                    "max_loss_dollar": max_loss,
                    "mll_pct": max_loss / MLL * 100,
                    "eval_viable": max_loss <= MLL and mcs <= 4,
                    "funded_t1_viable": max_loss <= MLL and mcs <= 2,
                    "funded_t3_viable": max_loss <= MLL and mcs <= 4,
                })
                config_id += 1

    return configs


# ---------------------------------------------------------------------------
#  Numpy bar loader
# ---------------------------------------------------------------------------
def load_bars_numpy(filepath: str) -> dict:
    print("  Counting rows...")
    with open(filepath, "r") as f:
        n_rows = sum(1 for _ in f) - 1

    print(f"  Allocating arrays for {n_rows} rows...")
    last_arr = np.empty(n_rows, dtype=np.float32)
    high_arr = np.empty(n_rows, dtype=np.float32)
    low_arr = np.empty(n_rows, dtype=np.float32)
    time_sec_arr = np.empty(n_rows, dtype=np.int32)
    date_int_arr = np.empty(n_rows, dtype=np.int32)
    dt_strings: list[str] = []

    print("  Parsing CSV...")
    idx = 0
    with open(filepath, "r") as f:
        next(f)
        for line in f:
            parts = line.split(",", 6)
            if len(parts) < 6:
                continue
            date_str = parts[0].strip()
            time_str = parts[1].strip()
            c = float(parts[5])
            h = float(parts[3])
            l = float(parts[4])

            tparts = time_str.split(":")
            hr = int(tparts[0])
            mn = int(tparts[1])
            sec = int(float(tparts[2])) if len(tparts) > 2 else 0
            tsec = hr * 3600 + mn * 60 + sec

            dparts = date_str.split("-")
            yr = int(dparts[0])
            mo = int(dparts[1])
            dy = int(dparts[2])
            dint = yr * 10000 + mo * 100 + dy

            last_arr[idx] = c
            high_arr[idx] = h
            low_arr[idx] = l
            time_sec_arr[idx] = tsec
            date_int_arr[idx] = dint
            dt_strings.append(f"{yr:04d}-{mo:02d}-{dy:02d} {hr:02d}:{mn:02d}:{sec:02d}")
            idx += 1

            if idx % 5_000_000 == 0:
                print(f"    {idx / 1_000_000:.0f}M rows parsed...")

    if idx < n_rows:
        last_arr = last_arr[:idx]
        high_arr = high_arr[:idx]
        low_arr = low_arr[:idx]
        time_sec_arr = time_sec_arr[:idx]
        date_int_arr = date_int_arr[:idx]

    return {
        "last": last_arr, "high": high_arr, "low": low_arr,
        "time_sec": time_sec_arr, "date_int": date_int_arr,
        "datetime": dt_strings, "n": idx,
    }


# ---------------------------------------------------------------------------
#  Simulation
# ---------------------------------------------------------------------------
def run_sim(bars: dict, step_dist: float, hard_stop: float,
            max_fades: int, max_levels: int, max_contract_size: int) -> list[dict]:
    n = bars["n"]
    last = bars["last"]
    high = bars["high"]
    low = bars["low"]
    tsec = bars["time_sec"]
    dint = bars["date_int"]
    dt_str = bars["datetime"]

    initial_qty = INITIAL_QTY
    max_cs = max_contract_size
    tick_size = TICK_SIZE

    anchor = 0.0; watch_price = 0.0; watch_high = 0.0; watch_low = 0.0
    direction = 0; level = 0; fade_long = 0; fade_short = 0
    pos_qty = 0; avg_entry = 0.0; total_cost = 0.0

    cycle_id = 0
    w_start_dt = ""; w_start_price = 0.0; w_start_high = 0.0; w_start_low = 0.0
    w_start_bar = 0; c_start_bar = 0; c_depth = 0; c_peak = 0
    c_mfe = 0.0; c_mae = 0.0; saved_avg = 0.0
    rth_active = False
    cycles: list[dict] = []

    def reset_state():
        nonlocal anchor, direction, level, watch_price, watch_high, watch_low
        anchor = 0.0; direction = 0; level = 0
        watch_price = 0.0; watch_high = 0.0; watch_low = 0.0

    def start_watch(i):
        nonlocal w_start_dt, w_start_price, w_start_high, w_start_low, w_start_bar
        nonlocal c_depth, c_peak, c_mfe, c_mae
        w_start_dt = dt_str[i]; w_start_price = last[i]
        w_start_high = last[i]; w_start_low = last[i]
        w_start_bar = i; c_depth = 0; c_peak = 0; c_mfe = 0.0; c_mae = 0.0

    def sim_entry(d, qty, price):
        nonlocal pos_qty, avg_entry, total_cost
        if pos_qty == 0:
            pos_qty = d * qty; avg_entry = price; total_cost = price * qty
        else:
            total_cost += price * qty; pos_qty += d * qty
            avg_entry = total_cost / abs(pos_qty)

    def sim_flatten(price):
        nonlocal pos_qty, avg_entry, total_cost
        pnl = 0.0
        if pos_qty != 0:
            if pos_qty > 0:
                pnl = (price - avg_entry) / tick_size * abs(pos_qty)
            else:
                pnl = (avg_entry - price) / tick_size * abs(pos_qty)
        pos_qty = 0; avg_entry = 0.0; total_cost = 0.0
        return pnl

    def record_cycle(i, exit_type, pnl):
        nonlocal cycle_id
        cycles.append({
            "cycle_id": cycle_id,
            "watch_start_dt": w_start_dt, "watch_price": float(w_start_price),
            "watch_high": float(w_start_high), "watch_low": float(w_start_low),
            "watch_bars": c_start_bar - w_start_bar if c_start_bar > w_start_bar else 0,
            "seed_dt": dt_str[c_start_bar], "exit_dt": dt_str[i],
            "direction": "LONG" if direction == 1 else "SHORT",
            "seed_price": float(last[c_start_bar]),
            "avg_entry_price": saved_avg, "exit_price": float(last[i]),
            "exit_type": exit_type, "depth": c_depth, "max_position": c_peak,
            "pnl_ticks": pnl, "pnl_dollars": pnl * 5.0,
            "bars_held": i - c_start_bar, "mfe_ticks": c_mfe, "mae_ticks": c_mae,
        })
        cycle_id += 1

    def fade_blocked(d):
        if max_fades <= 0: return False
        return (d == 1 and fade_long >= max_fades) or (d == -1 and fade_short >= max_fades)

    def update_fade(d):
        nonlocal fade_long, fade_short
        if d == 1: fade_long += 1; fade_short = 0
        else: fade_short += 1; fade_long = 0

    for i in range(n):
        price = float(last[i]); t = int(tsec[i]); d = int(dint[i])

        if RTH_OPEN_SEC <= t <= RTH_CLOSE_SEC:
            if not rth_active:
                rth_active = True
                if pos_qty != 0:
                    saved_avg = avg_entry; sim_flatten(price)
                reset_state(); fade_long = 0; fade_short = 0; start_watch(i)
        else:
            if rth_active and t > RTH_CLOSE_SEC: rth_active = False
            continue

        if t >= RTH_CLOSE_SEC:
            if pos_qty != 0:
                saved_avg = avg_entry; pnl = sim_flatten(price)
                record_cycle(i, "EOD_FLATTEN", pnl); reset_state()
            elif watch_price != 0.0: reset_state()
            rth_active = False; continue

        if pos_qty != 0:
            if pos_qty > 0:
                hi_exc = (float(high[i]) - avg_entry) / tick_size
                lo_exc = (float(low[i]) - avg_entry) / tick_size
            else:
                hi_exc = (avg_entry - float(low[i])) / tick_size
                lo_exc = (avg_entry - float(high[i])) / tick_size
            if hi_exc > c_mfe: c_mfe = hi_exc
            if -lo_exc > c_mae: c_mae = -lo_exc
            if pos_qty > 0: exc = (price - avg_entry) / tick_size
            else: exc = (avg_entry - price) / tick_size
            if exc > c_mfe: c_mfe = exc
            if -exc > c_mae: c_mae = -exc

        if pos_qty != 0 and hard_stop > 0.0:
            if pos_qty > 0: unreal = (avg_entry - price) / tick_size
            else: unreal = (price - avg_entry) / tick_size
            if unreal >= hard_stop:
                saved_avg = avg_entry; pnl = sim_flatten(price)
                record_cycle(i, "HARD_STOP", pnl); reset_state(); start_watch(i)
                continue

        if pos_qty == 0 and anchor == 0.0:
            if watch_price == 0.0:
                watch_price = price; watch_high = price; watch_low = price
                if not w_start_dt: start_watch(i)
                continue
            if price > watch_high: watch_high = price
            if price < watch_low: watch_low = price
            if price > w_start_high: w_start_high = price
            if price < w_start_low: w_start_low = price
            pfh = watch_high - price; pfl = price - watch_low
            sd = 0
            if pfh >= step_dist and pfl >= step_dist:
                sd = 1 if pfh >= pfl else -1
            elif pfh >= step_dist: sd = 1
            elif pfl >= step_dist: sd = -1
            else: continue
            if fade_blocked(sd):
                sd = -sd
                other = (pfh >= step_dist) if sd == 1 else (pfl >= step_dist)
                if not other or fade_blocked(sd): continue
            sim_entry(sd, initial_qty, price)
            direction = sd; level = 0; anchor = price; watch_price = 0.0
            c_start_bar = i; c_depth = 0; c_peak = abs(pos_qty)
            c_mfe = 0.0; c_mae = 0.0; update_fade(sd)
            continue

        if pos_qty == 0:
            reset_state(); start_watch(i); continue

        up = price - anchor; dn = anchor - price
        in_favor = (up >= step_dist) if direction == 1 else (dn >= step_dist)
        against = (dn >= step_dist) if direction == 1 else (up >= step_dist)

        if in_favor:
            saved_avg = avg_entry; pnl = sim_flatten(price)
            record_cycle(i, "REVERSAL", pnl)
            new_dir = -direction
            if fade_blocked(new_dir):
                reset_state(); start_watch(i); continue
            sim_entry(new_dir, initial_qty, price)
            direction = new_dir; level = 0; anchor = price
            c_start_bar = i; c_depth = 0; c_peak = abs(pos_qty)
            c_mfe = 0.0; c_mae = 0.0; update_fade(new_dir)
            w_start_dt = dt_str[i]; w_start_price = price
            w_start_high = price; w_start_low = price; w_start_bar = i
            continue

        if against:
            ul = level
            if ul >= max_levels: ul = 0
            aq = int(initial_qty * (2 ** ul) + 0.5)
            ap = abs(pos_qty)
            if ap + aq > max_cs:
                room = max_cs - ap
                if room <= 0: continue
                aq = room; level = 0
            sim_entry(direction, aq, price)
            level += 1
            if level >= max_levels: level = 0
            anchor = price; c_depth += 1
            if abs(pos_qty) > c_peak: c_peak = abs(pos_qty)
            continue

    if pos_qty != 0 and n > 0:
        saved_avg = avg_entry; pnl = sim_flatten(float(last[n - 1]))
        record_cycle(n - 1, "DATA_END", pnl)

    return cycles


# ---------------------------------------------------------------------------
#  Metrics
# ---------------------------------------------------------------------------
def compute_commission(depth: int, max_contract_size: int) -> float:
    pos = min(INITIAL_QTY * (2 ** depth), max_contract_size)
    return pos * COMMISSION_PER_RT_MINI


def compute_metrics(cycles: list[dict], config: dict) -> dict:
    mcs = config["max_contract_size"]
    if not cycles:
        return {
            **config, "cycle_count": 0, "win_count": 0, "loss_count": 0,
            "win_rate": 0.0, "gross_er": 0.0, "net_er": 0.0, "sigma": 0.0,
            "max_consec_losses": 0, "p_pass_eval": 0.0, "p_pass_funded": 0.0,
            "prop_score": 0.0, "kelly_r": 0.0,
            "total_gross_pnl_ticks": 0.0, "total_net_pnl_dollars": 0.0,
            "depth_0_count": 0, "depth_1_count": 0, "depth_2_count": 0, "depth_3_count": 0,
            "reversal_count": 0, "hard_stop_count": 0, "eod_flatten_count": 0,
        }

    pnl_net = []; pnl_gross = []
    depth_counts = {0: 0, 1: 0, 2: 0, 3: 0}
    exit_counts = {"REVERSAL": 0, "HARD_STOP": 0, "EOD_FLATTEN": 0, "DATA_END": 0}

    for c in cycles:
        gross = c["pnl_ticks"] * 5.0
        comm = compute_commission(c["depth"], mcs)
        pnl_gross.append(gross)
        pnl_net.append(gross - comm)
        d = min(c["depth"], 3)
        depth_counts[d] = depth_counts.get(d, 0) + 1
        exit_counts[c["exit_type"]] = exit_counts.get(c["exit_type"], 0) + 1

    n = len(cycles)
    gross_er = sum(pnl_gross) / n
    net_er = sum(pnl_net) / n

    if n > 1:
        variance = sum((p - net_er) ** 2 for p in pnl_net) / (n - 1)
        sigma = math.sqrt(variance)
    else:
        sigma = 0.0

    wins = sum(1 for p in pnl_net if p >= 0)
    losses = n - wins

    max_consec = 0; consec = 0
    for p in pnl_net:
        if p < 0: consec += 1; max_consec = max(max_consec, consec)
        else: consec = 0

    D_eval, T_eval = 2000.0, 3000.0
    D_funded, T_funded = 2000.0, 1000.0
    p_pass_eval = 0.0; p_pass_funded = 0.0; prop_score = 0.0; kelly_r = 0.0

    if sigma > 0 and net_er != 0:
        sig2 = sigma ** 2
        try:
            en = math.exp(-2.0 * net_er * D_eval / sig2)
            ed = math.exp(-2.0 * net_er * (D_eval + T_eval) / sig2)
            if abs(1.0 - ed) > 1e-12:
                p_pass_eval = max(0.0, min(1.0, (1.0 - en) / (1.0 - ed)))
        except OverflowError:
            p_pass_eval = 1.0 if net_er > 0 else 0.0
        try:
            en = math.exp(-2.0 * net_er * D_funded / sig2)
            ed = math.exp(-2.0 * net_er * (D_funded + T_funded) / sig2)
            if abs(1.0 - ed) > 1e-12:
                p_pass_funded = max(0.0, min(1.0, (1.0 - en) / (1.0 - ed)))
        except OverflowError:
            p_pass_funded = 1.0 if net_er > 0 else 0.0
        prop_score = (net_er / sigma) * math.sqrt(D_eval / T_eval)
        kelly_r = 0.2 * D_eval * net_er / sig2

    return {
        **config,
        "cycle_count": n, "win_count": wins, "loss_count": losses,
        "win_rate": wins / n,
        "gross_er": round(gross_er, 2), "net_er": round(net_er, 2),
        "sigma": round(sigma, 2), "max_consec_losses": max_consec,
        "p_pass_eval": round(p_pass_eval, 6), "p_pass_funded": round(p_pass_funded, 6),
        "prop_score": round(prop_score, 6), "kelly_r": round(kelly_r, 6),
        "total_gross_pnl_ticks": round(sum(c["pnl_ticks"] for c in cycles), 2),
        "total_net_pnl_dollars": round(sum(pnl_net), 2),
        "depth_0_count": depth_counts.get(0, 0),
        "depth_1_count": depth_counts.get(1, 0),
        "depth_2_count": depth_counts.get(2, 0),
        "depth_3_count": depth_counts.get(3, 0),
        "reversal_count": exit_counts.get("REVERSAL", 0),
        "hard_stop_count": exit_counts.get("HARD_STOP", 0),
        "eod_flatten_count": exit_counts.get("EOD_FLATTEN", 0),
    }


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------
def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="LP Sweep v2 — baseline sweep")
    parser.add_argument("--bar-file", type=str,
                        default=r"C:\Projects\futures_pipeline\data\NQ-1tick-calibration.csv")
    parser.add_argument("--output-dir", type=str,
                        default=r"C:\Projects\futures_pipeline\lab\output")
    args = parser.parse_args()

    print(f"Loading bars from {args.bar_file}...")
    t0 = time.time()
    bars = load_bars_numpy(args.bar_file)
    t_load = time.time() - t0
    print(f"Loaded {bars['n']} bars in {t_load:.1f}s")

    configs = build_configs()

    # Summary
    by_label = {}
    for c in configs:
        by_label.setdefault(c["label"], []).append(c)
    print(f"\nSweep grid: {len(configs)} configurations")
    for label in ["depth_0", "depth_1", "depth_2", "depth_3"]:
        if label in by_label:
            v = sum(1 for c in by_label[label] if c["eval_viable"])
            print(f"  {label}: {len(by_label[label])} configs ({v} eval-viable)")

    print(f"\nRunning {len(configs)} configurations...")
    results = []
    all_cycles: list[dict] = []  # all cycles from all configs for post-processing

    for idx, cfg in enumerate(configs):
        t1 = time.time()
        cycles = run_sim(bars, cfg["step_dist"], cfg["hard_stop"],
                         cfg["max_fades"], cfg["max_levels"], cfg["max_contract_size"])

        # Tag each cycle with config_id for post-processing
        for c in cycles:
            c["config_id"] = cfg["config_id"]
            c["config_label"] = cfg["label"]
            c["config_sd"] = cfg["step_dist"]
            c["config_hs"] = cfg["hard_stop"]
            c["config_mcs"] = cfg["max_contract_size"]
        all_cycles.extend(cycles)

        metrics = compute_metrics(cycles, cfg)
        results.append(metrics)
        elapsed = time.time() - t1

        ev = "E" if cfg["eval_viable"] else " "
        f1 = "F1" if cfg["funded_t1_viable"] else "  "
        print(f"  [{idx+1:3d}/{len(configs)}] {cfg['label']:>7s} "
              f"SD={cfg['step_dist']:5.1f} HS={cfg['hard_stop']:5.0f} "
              f"MCS={cfg['max_contract_size']:1d} -> "
              f"{metrics['cycle_count']:5d} cyc "
              f"(d0={metrics['depth_0_count']:4d} d1={metrics['depth_1_count']:4d} "
              f"d2={metrics['depth_2_count']:4d} d3={metrics['depth_3_count']:4d}) "
              f"E[R]=${metrics['net_er']:7.2f} PS={metrics['prop_score']:8.4f} "
              f"[{ev}|{f1}] ({elapsed:.1f}s)")

    results.sort(key=lambda r: r["prop_score"], reverse=True)

    # Write summary results
    out_dir = Path(args.output_dir)
    out_path = out_dir / "sweep_results.csv"
    fieldnames = [
        "config_id", "label", "step_dist", "hard_stop", "max_levels",
        "max_contract_size", "max_fades",
        "max_loss_dollar", "mll_pct", "eval_viable", "funded_t1_viable", "funded_t3_viable",
        "cycle_count", "win_count", "loss_count", "win_rate",
        "gross_er", "net_er", "sigma", "max_consec_losses",
        "p_pass_eval", "p_pass_funded", "prop_score", "kelly_r",
        "total_gross_pnl_ticks", "total_net_pnl_dollars",
        "depth_0_count", "depth_1_count", "depth_2_count", "depth_3_count",
        "reversal_count", "hard_stop_count", "eod_flatten_count",
    ]
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in results:
            w.writerow({k: r[k] for k in fieldnames})

    # Write all cycle-level data
    cycles_path = out_dir / "sweep_all_cycles.csv"
    cycle_fields = [
        "config_id", "config_label", "config_sd", "config_hs", "config_mcs",
        "cycle_id", "watch_start_dt", "watch_price", "watch_high", "watch_low",
        "watch_bars", "seed_dt", "exit_dt", "direction",
        "seed_price", "avg_entry_price", "exit_price", "exit_type",
        "depth", "max_position", "pnl_ticks", "pnl_dollars",
        "bars_held", "mfe_ticks", "mae_ticks",
    ]
    print(f"\nWriting {len(all_cycles)} cycles to {cycles_path}...")
    with open(cycles_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cycle_fields)
        w.writeheader()
        for c in all_cycles:
            row = {}
            for k in cycle_fields:
                v = c.get(k, "")
                if isinstance(v, float):
                    row[k] = f"{v:.2f}"
                else:
                    row[k] = v
            w.writerow(row)

    print(f"Results written to {out_path}")
    print(f"Cycle data written to {cycles_path}")

    # Summary per depth
    for label in ["depth_0", "depth_1", "depth_2", "depth_3"]:
        step_results = [r for r in results if r["label"] == label]
        if not step_results:
            continue
        positive = [r for r in step_results if r["net_er"] > 0]
        print(f"\n--- {label.upper()} ({len(step_results)} configs, {len(positive)} positive E[R]) ---")
        print(f"{'SD':>5} {'HS':>5} {'MCS':>4} {'$MaxLoss':>8} {'Cycles':>7} {'WinRate':>8} "
              f"{'E[R]':>8} {'Sigma':>8} {'PropScore':>10} {'P_pass_E':>9} {'d0':>5} {'d1':>5} {'d2':>5} {'d3':>5}")
        print("-" * 115)
        top = sorted(step_results, key=lambda r: r["prop_score"], reverse=True)[:8]
        for r in top:
            print(f"{r['step_dist']:5.0f} {r['hard_stop']:5.0f} {r['max_contract_size']:4d} "
                  f"${r['max_loss_dollar']:7.0f} "
                  f"{r['cycle_count']:7d} {r['win_rate']:8.1%} "
                  f"{r['net_er']:8.2f} {r['sigma']:8.2f} {r['prop_score']:10.4f} "
                  f"{r['p_pass_eval']:9.4f} "
                  f"{r['depth_0_count']:5d} {r['depth_1_count']:5d} "
                  f"{r['depth_2_count']:5d} {r['depth_3_count']:5d}")

    total_time = time.time() - t0
    print(f"\nTotal time: {total_time:.0f}s ({total_time/60:.1f}m)")


if __name__ == "__main__":
    main()
