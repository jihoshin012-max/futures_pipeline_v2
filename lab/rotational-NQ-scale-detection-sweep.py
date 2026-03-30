# archetype: rotational
"""
lp_sweep_filtered.py — Forked sweep runner with signal filter injection.

Forks run_sim(), build_configs(), compute_metrics() from lp_sweep.py.
The locked originals are NOT modified. This file adds:
  - Filter injection at entry, reversal re-entry, and optional add points
  - 6 filter factory functions (ATR, range/disp, ZZ median, dominant scale,
    cycle health, asymmetry)
  - Comparison sweep: baseline + N filtered variants on same config grid

Usage:
    python lp_sweep_filtered.py [--bar-file PATH] [--output-dir PATH]
                                [--bar-size 250] [--filters all]
"""
from __future__ import annotations

import csv
import math
import time
from collections import deque
from pathlib import Path

import numpy as np

import importlib
_engine = importlib.import_module("rotational-NQ-scale-detection-engine")
load_bars_extended = _engine.load_bars_extended
precompute_all_signals = _engine.precompute_all_signals


# ---------------------------------------------------------------------------
#  Fixed params (identical to lp_sweep.py)
# ---------------------------------------------------------------------------
INITIAL_QTY = 1
TICK_SIZE = 0.25
COMMISSION_PER_RT_MINI = 3.50
MLL = 2000.0

RTH_OPEN_SEC  = 9 * 3600 + 30 * 60
RTH_CLOSE_SEC = 15 * 3600 + 49 * 60 + 50


# ---------------------------------------------------------------------------
#  Config builder (identical to lp_sweep.py)
# ---------------------------------------------------------------------------
def build_configs() -> list[dict]:
    configs = []
    config_id = 0
    STEP_DISTS = [10.0, 15.0, 20.0, 25.0, 30.0, 50.0]
    DEPTH_CONFIGS = [
        ("depth_0", 1, 1, 0),
        ("depth_1", 2, 1, 1),
        ("depth_2", 4, 2, 2),
        ("depth_3", 8, 3, 3),
    ]
    HS_MULTIPLIERS_MARTINGALE = [1.0, 1.25, 1.5, 2.0, 2.5, 3.0]

    for sd in STEP_DISTS:
        sd_ticks = sd / TICK_SIZE
        for label, mcs, ml, depth in DEPTH_CONFIGS:
            if depth == 0:
                hs_values = []
                for mult in [0.25, 0.5, 0.75, 1.0, 1.5, 2.0]:
                    hs_values.append(round(sd_ticks * mult))
                hs_values = sorted(set(max(5, v) for v in hs_values))
            else:
                if depth == 1:
                    hs_min = sd / TICK_SIZE
                elif depth == 2:
                    hs_min = sd * 1.5 / TICK_SIZE
                elif depth == 3:
                    hs_min = sd * 1.75 / TICK_SIZE
                else:
                    hs_min = sd * (2.0 - 2.0**(-(depth-1))) / TICK_SIZE
                hs_values = sorted(set(round(hs_min * m) for m in HS_MULTIPLIERS_MARTINGALE))
                hs_values = [v for v in hs_values if v >= 5]

            for hs in hs_values:
                max_loss = mcs * hs * 5.0
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
#  Metrics (identical to lp_sweep.py)
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
#  Forked simulation — with filter injection
# ---------------------------------------------------------------------------
def run_sim_filtered(bars: dict, step_dist: float, hard_stop: float,
                     max_fades: int, max_levels: int, max_contract_size: int,
                     signal_arrays: dict | None = None,
                     filter_fn=None,
                     gate_adds: bool = False,
                     on_cycle_exit=None) -> list[dict]:
    """Forked from lp_sweep.py run_sim() with signal filter injection.

    When filter_fn is None, produces identical output to the original.
    """
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
            "seed_bar": c_start_bar, "exit_bar": i,
            "seed_dt": dt_str[c_start_bar], "exit_dt": dt_str[i],
            "direction": "LONG" if direction == 1 else "SHORT",
            "seed_price": float(last[c_start_bar]),
            "avg_entry_price": saved_avg, "exit_price": float(last[i]),
            "exit_type": exit_type, "depth": c_depth, "max_position": c_peak,
            "pnl_ticks": pnl, "pnl_dollars": pnl * 5.0,
            "bars_held": i - c_start_bar, "mfe_ticks": c_mfe, "mae_ticks": c_mae,
        })
        cycle_id += 1
        # Callback for cycle health filter
        if on_cycle_exit is not None:
            on_cycle_exit(exit_type)

    def fade_blocked(d):
        if max_fades <= 0: return False
        return (d == 1 and fade_long >= max_fades) or (d == -1 and fade_short >= max_fades)

    def update_fade(d):
        nonlocal fade_long, fade_short
        if d == 1: fade_long += 1; fade_short = 0
        else: fade_short += 1; fade_long = 0

    # --- Filter helper ---
    def check_filter(bar_idx, dir_val, sd):
        if filter_fn is None or signal_arrays is None:
            return True
        return filter_fn(signal_arrays, bar_idx, dir_val, sd)

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

        # === ENTRY GATE (seed detection) ===
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
            # >>> FILTER INJECTION: entry gate <<<
            if not check_filter(i, sd, step_dist):
                continue
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

        # === REVERSAL RE-ENTRY GATE ===
        if in_favor:
            saved_avg = avg_entry; pnl = sim_flatten(price)
            record_cycle(i, "REVERSAL", pnl)
            new_dir = -direction
            if fade_blocked(new_dir):
                reset_state(); start_watch(i); continue
            # >>> FILTER INJECTION: reversal re-entry <<<
            if not check_filter(i, new_dir, step_dist):
                reset_state(); start_watch(i); continue
            sim_entry(new_dir, initial_qty, price)
            direction = new_dir; level = 0; anchor = price
            c_start_bar = i; c_depth = 0; c_peak = abs(pos_qty)
            c_mfe = 0.0; c_mae = 0.0; update_fade(new_dir)
            w_start_dt = dt_str[i]; w_start_price = price
            w_start_high = price; w_start_low = price; w_start_bar = i
            continue

        # === ADD GATE (optional) ===
        if against:
            ul = level
            if ul >= max_levels: ul = 0
            aq = int(initial_qty * (2 ** ul) + 0.5)
            ap = abs(pos_qty)
            if ap + aq > max_cs:
                room = max_cs - ap
                if room <= 0: continue
                aq = room; level = 0
            # >>> FILTER INJECTION: add gate (optional) <<<
            if gate_adds and not check_filter(i, direction, step_dist):
                continue
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
#  Filter factories
# ---------------------------------------------------------------------------

SD_LEVELS = [10.0, 15.0, 20.0, 25.0, 30.0, 50.0]


def make_atr_filter(tolerance: float = 0.3, baseline_sd: float = 25.0):
    """Gate entry when ATR-implied SD differs from config SD by > tolerance fraction."""
    def filter_fn(signals, i, direction, step_dist):
        warmup = signals["warmup_ticks"]["atr"]
        if i < warmup:
            return True
        ratio = signals["atr_ratio"][i]
        if np.isnan(ratio):
            return True
        implied_sd = baseline_sd * ratio
        return abs(implied_sd - step_dist) / step_dist <= tolerance
    return filter_fn


def make_range_disp_filter(min_ratio: float = 2.0):
    """Gate entry when range/displacement ratio is below threshold (trending)."""
    def filter_fn(signals, i, direction, step_dist):
        warmup = signals["warmup_ticks"]["range_disp"]
        if i < warmup:
            return True
        val = signals["range_disp"][i]
        if np.isnan(val):
            return True
        return val >= min_ratio
    return filter_fn


def make_zz_median_filter(tolerance_pts: float = 10.0):
    """Gate entry when rolling ZZ median swing size doesn't match config SD."""
    def filter_fn(signals, i, direction, step_dist):
        warmup = signals["warmup_ticks"]["zz_median"]
        if i < warmup:
            return True
        median_swing = signals["zz_median"][i]
        if np.isnan(median_swing):
            return True
        return abs(median_swing - step_dist) <= tolerance_pts
    return filter_fn


def make_dominant_scale_filter():
    """Gate entry when dominant scale doesn't match config SD (±1 level)."""
    def filter_fn(signals, i, direction, step_dist):
        warmup = signals["warmup_ticks"]["dominant_scale"]
        if i < warmup:
            return True
        dom = signals["dominant_scale"][i]
        if np.isnan(dom):
            return True
        # Accept if dominant scale is within ±1 SD level
        if step_dist not in SD_LEVELS:
            return True
        idx = SD_LEVELS.index(step_dist)
        neighbors = set()
        if idx > 0: neighbors.add(SD_LEVELS[idx - 1])
        neighbors.add(SD_LEVELS[idx])
        if idx < len(SD_LEVELS) - 1: neighbors.add(SD_LEVELS[idx + 1])
        return dom in neighbors
    return filter_fn


def make_asymmetry_filter(max_asymmetry: float = 0.6):
    """Gate entry when completion asymmetry exceeds threshold (trending)."""
    def filter_fn(signals, i, direction, step_dist):
        warmup = signals["warmup_ticks"]["asymmetry"]
        if i < warmup:
            return True
        asym = signals["asymmetry"][i]
        return abs(asym) <= max_asymmetry
    return filter_fn


class CycleHealthFilter:
    """Strategy-feedback filter: gate entry when recent stop rate is too high."""

    def __init__(self, window: int = 20, max_stop_rate: float = 0.5):
        self.window = window
        self.max_stop_rate = max_stop_rate
        self.outcomes: deque = deque(maxlen=window)

    def __call__(self, signals, i, direction, step_dist):
        if len(self.outcomes) < self.window:
            return True
        stop_count = sum(1 for o in self.outcomes if o)
        return stop_count / len(self.outcomes) <= self.max_stop_rate

    def record_outcome(self, exit_type: str):
        self.outcomes.append(exit_type == "HARD_STOP")

    def reset(self):
        self.outcomes.clear()


# ---------------------------------------------------------------------------
#  Sweep runner
# ---------------------------------------------------------------------------

def run_filtered_sweep(bar_file: str, output_dir: str,
                       bar_size: int = 250,
                       filter_names: list[str] | None = None):
    """Run baseline + filtered sweeps and save comparison results."""

    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # --- Load bars ---
    print(f"Loading bars from {bar_file}...")
    t0 = time.time()
    bars = load_bars_extended(bar_file)
    t_load = time.time() - t0
    print(f"Loaded {bars['n']} bars in {t_load:.1f}s")

    # --- Precompute signals ---
    print(f"\nPrecomputing signals on {bar_size}-tick bars...")
    signals = precompute_all_signals(bars, bar_size=bar_size)

    # --- Build configs ---
    configs = build_configs()
    print(f"\nSweep grid: {len(configs)} configurations")

    # --- Define filters ---
    all_filters: dict[str, tuple] = {
        # name: (filter_fn, on_cycle_exit_callback_or_None)
        "atr_scale":       (make_atr_filter(tolerance=0.3, baseline_sd=25.0), None),
        "range_disp":      (make_range_disp_filter(min_ratio=2.0), None),
        "zz_median":       (make_zz_median_filter(tolerance_pts=10.0), None),
        "dominant_scale":  (make_dominant_scale_filter(), None),
        "asymmetry":       (make_asymmetry_filter(max_asymmetry=0.6), None),
        # cycle_health is special: stateful, needs per-config reset + callback
        "cycle_health":    (None, None),  # handled separately
    }

    if filter_names is None or filter_names == ["all"]:
        filter_names = list(all_filters.keys())

    # --- Baseline sweep ---
    print(f"\n{'='*60}")
    print(f"Running BASELINE (no filter)...")
    print(f"{'='*60}")
    baseline_results = []
    t_sweep = time.time()
    for idx, cfg in enumerate(configs):
        cycles = run_sim_filtered(bars, cfg["step_dist"], cfg["hard_stop"],
                                   cfg["max_fades"], cfg["max_levels"],
                                   cfg["max_contract_size"])
        metrics = compute_metrics(cycles, cfg)
        baseline_results.append(metrics)
        if (idx + 1) % 20 == 0 or idx == len(configs) - 1:
            print(f"  [{idx+1:3d}/{len(configs)}] ({time.time()-t_sweep:.0f}s)")
    _save_results(out_dir / "sweep_baseline.csv", baseline_results)
    print(f"Baseline complete: {time.time()-t_sweep:.0f}s")

    # --- Filtered sweeps ---
    all_filtered: dict[str, list[dict]] = {}

    for fname in filter_names:
        print(f"\n{'='*60}")
        print(f"Running filter: {fname}")
        print(f"{'='*60}")
        t_f = time.time()
        f_results = []

        for idx, cfg in enumerate(configs):
            if fname == "cycle_health":
                ch = CycleHealthFilter(window=20, max_stop_rate=0.5)
                cycles = run_sim_filtered(
                    bars, cfg["step_dist"], cfg["hard_stop"],
                    cfg["max_fades"], cfg["max_levels"], cfg["max_contract_size"],
                    signal_arrays=signals, filter_fn=ch,
                    on_cycle_exit=ch.record_outcome,
                )
            else:
                fn, _ = all_filters[fname]
                cycles = run_sim_filtered(
                    bars, cfg["step_dist"], cfg["hard_stop"],
                    cfg["max_fades"], cfg["max_levels"], cfg["max_contract_size"],
                    signal_arrays=signals, filter_fn=fn,
                )
            metrics = compute_metrics(cycles, cfg)
            f_results.append(metrics)
            if (idx + 1) % 20 == 0 or idx == len(configs) - 1:
                print(f"  [{idx+1:3d}/{len(configs)}] ({time.time()-t_f:.0f}s)")

        _save_results(out_dir / f"sweep_{fname}.csv", f_results)
        all_filtered[fname] = f_results
        print(f"{fname} complete: {time.time()-t_f:.0f}s")

    # --- Save comparison ---
    _save_comparison(out_dir / "sweep_comparison.csv",
                     baseline_results, all_filtered, configs)

    total = time.time() - t0
    print(f"\n{'='*60}")
    print(f"All sweeps complete: {total:.0f}s ({total/60:.1f}m)")
    print(f"Results in: {out_dir}")


# ---------------------------------------------------------------------------
#  Output helpers
# ---------------------------------------------------------------------------

RESULT_FIELDS = [
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


def _save_results(path: Path, results: list[dict]):
    results_sorted = sorted(results, key=lambda r: r["prop_score"], reverse=True)
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=RESULT_FIELDS)
        w.writeheader()
        for r in results_sorted:
            w.writerow({k: r[k] for k in RESULT_FIELDS})
    print(f"  Saved: {path}")


def _save_comparison(path: Path, baseline: list[dict],
                     filtered: dict[str, list[dict]], configs: list[dict]):
    """Save side-by-side comparison: baseline vs each filter for every config."""
    # Index baseline by config_id
    bl_by_id = {r["config_id"]: r for r in baseline}

    fields = ["config_id", "label", "step_dist", "hard_stop", "max_contract_size"]
    compare_metrics = ["cycle_count", "win_rate", "net_er", "sigma", "prop_score",
                       "kelly_r", "hard_stop_count", "reversal_count"]

    header = list(fields)
    # Baseline columns
    for m in compare_metrics:
        header.append(f"bl_{m}")
    # Per-filter columns
    for fname in sorted(filtered.keys()):
        for m in compare_metrics:
            header.append(f"{fname}_{m}")
        header.append(f"{fname}_retention")
        header.append(f"{fname}_delta_net_er")
        header.append(f"{fname}_delta_prop_score")
        header.append(f"{fname}_delta_stop_rate")

    rows = []
    for cfg in configs:
        cid = cfg["config_id"]
        bl = bl_by_id[cid]
        row = {k: cfg[k] for k in fields}
        for m in compare_metrics:
            row[f"bl_{m}"] = bl[m]

        for fname in sorted(filtered.keys()):
            fl_by_id = {r["config_id"]: r for r in filtered[fname]}
            fl = fl_by_id[cid]
            for m in compare_metrics:
                row[f"{fname}_{m}"] = fl[m]
            # Derived comparison metrics
            bl_cc = bl["cycle_count"] if bl["cycle_count"] > 0 else 1
            row[f"{fname}_retention"] = round(fl["cycle_count"] / bl_cc, 4)
            row[f"{fname}_delta_net_er"] = round(fl["net_er"] - bl["net_er"], 2)
            row[f"{fname}_delta_prop_score"] = round(fl["prop_score"] - bl["prop_score"], 6)
            bl_sr = bl["hard_stop_count"] / bl_cc if bl_cc > 0 else 0
            fl_cc = fl["cycle_count"] if fl["cycle_count"] > 0 else 1
            fl_sr = fl["hard_stop_count"] / fl_cc if fl_cc > 0 else 0
            row[f"{fname}_delta_stop_rate"] = round(fl_sr - bl_sr, 4)
        rows.append(row)

    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=header)
        w.writeheader()
        for row in rows:
            w.writerow(row)
    print(f"  Saved comparison: {path}")


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------
def main():
    import argparse
    parser = argparse.ArgumentParser(description="LP Sweep — Filtered")
    parser.add_argument("--bar-file", type=str,
                        default=r"C:\Projects\futures_pipeline\data\NQ-1tick-calibration.csv")
    parser.add_argument("--output-dir", type=str,
                        default=r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")
    parser.add_argument("--bar-size", type=int, default=250)
    parser.add_argument("--filters", type=str, default="all",
                        help="Comma-separated filter names, or 'all'")
    args = parser.parse_args()

    filter_names = None if args.filters == "all" else args.filters.split(",")
    run_filtered_sweep(args.bar_file, args.output_dir,
                       bar_size=args.bar_size, filter_names=filter_names)


if __name__ == "__main__":
    main()
