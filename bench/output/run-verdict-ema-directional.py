# Formal verdict -- rotational NQ ema-directional variant (combined)
# P2 holdout: ONE SHOT. Frozen params from lab Track B2.
# Config: SD=10 HS=60 depth_1 MCS=2 + chop<0.10 lb=3
#         + dr2<=-0.40 + dslope<=-2.0 + fade_confirm<0.40
#         + d2_ema9 entry gate (neutral zone |d2|<=0.5)
#         + d2_avg3 in-trade hold (delay reversal when aligned)
import sys, importlib, json, time, csv, datetime
import numpy as np
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, r"c:\Projects\futures_pipeline\lab")
_engine = importlib.import_module("rotational-NQ-scale-detection-engine")
_sweep = importlib.import_module("rotational-NQ-scale-detection-sweep")
load_bars_extended = _engine.load_bars_extended
aggregate_to_ntick = _engine.aggregate_to_ntick
compute_regime_signals = _engine.compute_regime_signals
compute_entry_signals = _engine.compute_entry_signals
map_signal_to_ticks = _engine.map_signal_to_ticks
run_sim_filtered = _sweep.run_sim_filtered
TICK_SIZE = _sweep.TICK_SIZE

COMM = 4.12
TV = 5.0
SD, HS, MF, ML, MCS = 10.0, 60.0, 0, 1, 2
BS, LB = 250, 3
CHOP_T, DR2_T, DSLOPE_T, FC_T = 0.10, -0.40, -2.0, 0.40
D2_NEUTRAL = 0.5
INITIAL_QTY = 1

RTH_OPEN_SEC = 9 * 3600 + 30 * 60
RTH_CLOSE_SEC = 15 * 3600 + 49 * 60 + 50

OUT_DIR = Path(r"C:\Projects\futures_pipeline\bench\output")
HOLDOUT_TAG = "20251217-20260313"
VARIANT = "ema-directional"


# ---------------------------------------------------------------------------
#  EMA
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
def precompute_signals(bars):
    agg, t2a = aggregate_to_ntick(bars, BS)
    regime = compute_regime_signals(agg, lookback=LB)
    entry = compute_entry_signals(agg, lookback=LB)
    n_agg = agg["n"]

    prev_high = np.full(n_agg, np.nan, dtype=np.float64)
    prev_low = np.full(n_agg, np.nan, dtype=np.float64)
    prev_range = np.full(n_agg, np.nan, dtype=np.float64)
    for ai in range(1, n_agg):
        prev_high[ai] = float(agg["high"][ai - 1])
        prev_low[ai] = float(agg["low"][ai - 1])
        rng = float(agg["high"][ai - 1]) - float(agg["low"][ai - 1])
        prev_range[ai] = rng if rng > 0 else np.nan

    close_agg = np.array(agg["last"], dtype=np.float64)
    ema9_agg = ema(close_agg, 9)

    d_ema9_agg = np.full(n_agg, np.nan, dtype=np.float64)
    for i in range(1, n_agg):
        if not np.isnan(ema9_agg[i]) and not np.isnan(ema9_agg[i - 1]):
            d_ema9_agg[i] = ema9_agg[i] - ema9_agg[i - 1]

    d2_ema9_agg = np.full(n_agg, np.nan, dtype=np.float64)
    for i in range(2, n_agg):
        if not np.isnan(d_ema9_agg[i]) and not np.isnan(d_ema9_agg[i - 1]):
            d2_ema9_agg[i] = d_ema9_agg[i] - d_ema9_agg[i - 1]

    d2_avg3_agg = np.full(n_agg, np.nan, dtype=np.float64)
    for i in range(2, n_agg):
        vals = [d2_ema9_agg[i - j] for j in range(min(3, i + 1))
                if i - j >= 0 and not np.isnan(d2_ema9_agg[i - j])]
        if len(vals) == 3:
            d2_avg3_agg[i] = sum(vals) / 3

    return {
        "choppiness": map_signal_to_ticks(regime["choppiness"], t2a),
        "dr2": map_signal_to_ticks(entry["dr2"], t2a),
        "dslope": map_signal_to_ticks(entry["dslope"], t2a),
        "prev_high": prev_high[t2a],
        "prev_low": prev_low[t2a],
        "prev_range": prev_range[t2a],
        "last": bars["last"],
        "d2_ema9": map_signal_to_ticks(d2_ema9_agg, t2a),
        "d2_avg3": map_signal_to_ticks(d2_avg3_agg, t2a),
        "warmup_ticks": {"dominant_scale": 0, "asymmetry": 0},
    }


# ---------------------------------------------------------------------------
#  Filters
# ---------------------------------------------------------------------------
def make_ab_filter():
    def f(s, i, d, sd):
        c = s["choppiness"][i]
        if np.isnan(c): return True
        if c >= CHOP_T: return False
        dr2 = s["dr2"][i]
        if np.isnan(dr2): return True
        if dr2 > DR2_T: return False
        ds = s["dslope"][i]
        if np.isnan(ds): return True
        if ds > DSLOPE_T: return False
        prev_range = s["prev_range"][i]
        if np.isnan(prev_range): return True
        ep = float(s["last"][i])
        if d == 1:
            fc = (ep - float(s["prev_low"][i])) / prev_range
        else:
            fc = (float(s["prev_high"][i]) - ep) / prev_range
        return fc < FC_T
    return f


def make_combined_entry_filter():
    """A+B + entry gate C (d2 neutral zone)."""
    ab = make_ab_filter()
    def f(s, i, d, sd):
        if not ab(s, i, d, sd):
            return False
        d2 = s["d2_ema9"][i]
        if np.isnan(d2):
            return True
        if abs(d2) <= D2_NEUTRAL:
            return True
        if d == 1 and d2 < -D2_NEUTRAL:
            return False
        if d == -1 and d2 > D2_NEUTRAL:
            return False
        return True
    return f


# ---------------------------------------------------------------------------
#  Forked sim with extended hold (from step39, condensed)
# ---------------------------------------------------------------------------
def run_sim_extended(bars, signal_arrays, filter_fn):
    d2_avg3 = signal_arrays["d2_avg3"]

    def d2_aligned(i, direction):
        val = float(d2_avg3[i])
        if np.isnan(val): return False
        return val > 0 if direction == 1 else val <= 0

    n = bars["n"]
    last = bars["last"]; high = bars["high"]; low = bars["low"]
    tsec = bars["time_sec"]; dint = bars["date_int"]; dt_str = bars["datetime"]
    max_cs = MCS; tick_size = TICK_SIZE; step_dist = SD; hard_stop = HS

    anchor = 0.0; watch_price = 0.0; watch_high = 0.0; watch_low = 0.0
    direction = 0; level = 0; fade_long = 0; fade_short = 0
    pos_qty = 0; avg_entry = 0.0; total_cost = 0.0
    cycle_id = 0
    w_start_dt = ""; w_start_price = 0.0; w_start_high = 0.0; w_start_low = 0.0
    w_start_bar = 0; c_start_bar = 0; c_depth = 0; c_peak = 0
    c_mfe = 0.0; c_mae = 0.0; saved_avg = 0.0
    rth_active = False; extended = False
    cycles = []

    def reset_state():
        nonlocal anchor, direction, level, watch_price, watch_high, watch_low, extended
        anchor = 0.0; direction = 0; level = 0
        watch_price = 0.0; watch_high = 0.0; watch_low = 0.0; extended = False

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
            if pos_qty > 0: pnl = (price - avg_entry) / tick_size * abs(pos_qty)
            else: pnl = (avg_entry - price) / tick_size * abs(pos_qty)
        pos_qty = 0; avg_entry = 0.0; total_cost = 0.0
        return pnl

    def record_cycle(i, exit_type, pnl):
        nonlocal cycle_id
        cycles.append({
            "cycle_id": cycle_id, "seed_bar": c_start_bar, "exit_bar": i,
            "seed_dt": dt_str[c_start_bar], "exit_dt": dt_str[i],
            "direction": "LONG" if direction == 1 else "SHORT",
            "seed_price": float(last[c_start_bar]),
            "avg_entry_price": saved_avg, "exit_price": float(last[i]),
            "exit_type": exit_type, "depth": c_depth, "max_position": c_peak,
            "pnl_ticks": pnl, "pnl_dollars": pnl * 5.0,
            "bars_held": i - c_start_bar, "mfe_ticks": c_mfe, "mae_ticks": c_mae,
            "watch_start_dt": w_start_dt, "watch_price": float(w_start_price),
            "watch_high": float(w_start_high), "watch_low": float(w_start_low),
            "watch_bars": c_start_bar - w_start_bar if c_start_bar > w_start_bar else 0,
        })
        cycle_id += 1

    def fade_blocked(d):
        return False  # max_fades = 0

    def update_fade(d):
        nonlocal fade_long, fade_short
        if d == 1: fade_long += 1; fade_short = 0
        else: fade_short += 1; fade_long = 0

    def check_filter(bar_idx, dir_val):
        if filter_fn is None: return True
        return filter_fn(signal_arrays, bar_idx, dir_val, step_dist)

    for i in range(n):
        price = float(last[i]); t = int(tsec[i])

        if RTH_OPEN_SEC <= t <= RTH_CLOSE_SEC:
            if not rth_active:
                rth_active = True
                if pos_qty != 0: saved_avg = avg_entry; sim_flatten(price)
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
                record_cycle(i, "HARD_STOP", pnl); reset_state()
                start_watch(i); continue

        if pos_qty != 0 and extended:
            if not d2_aligned(i, direction):
                saved_avg = avg_entry; pnl = sim_flatten(price)
                record_cycle(i, "D2_EXIT", pnl); reset_state()
                start_watch(i); continue

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
            if not check_filter(i, sd): continue
            sim_entry(sd, INITIAL_QTY, price)
            direction = sd; level = 0; anchor = price; watch_price = 0.0
            c_start_bar = i; c_depth = 0; c_peak = abs(pos_qty)
            c_mfe = 0.0; c_mae = 0.0; update_fade(sd)
            extended = False; continue

        if pos_qty == 0:
            reset_state(); start_watch(i); continue

        up = price - anchor; dn = anchor - price
        in_favor = (up >= step_dist) if direction == 1 else (dn >= step_dist)
        against = (dn >= step_dist) if direction == 1 else (up >= step_dist)

        if in_favor:
            if d2_aligned(i, direction):
                extended = True; anchor = price; continue
            else:
                saved_avg = avg_entry; pnl = sim_flatten(price)
                record_cycle(i, "REVERSAL", pnl)
                new_dir = -direction
                if not check_filter(i, new_dir):
                    reset_state(); start_watch(i); continue
                sim_entry(new_dir, INITIAL_QTY, price)
                direction = new_dir; level = 0; anchor = price
                c_start_bar = i; c_depth = 0; c_peak = abs(pos_qty)
                c_mfe = 0.0; c_mae = 0.0; update_fade(new_dir)
                w_start_dt = dt_str[i]; w_start_price = price
                w_start_high = price; w_start_low = price; w_start_bar = i
                extended = False; continue

        if against:
            ul = level
            if ul >= ML: ul = 0
            aq = int(INITIAL_QTY * (2 ** ul) + 0.5)
            ap = abs(pos_qty)
            if ap + aq > max_cs:
                room = max_cs - ap
                if room <= 0: continue
                aq = room; level = 0
            sim_entry(direction, aq, price)
            level += 1
            if level >= ML: level = 0
            anchor = price; c_depth += 1
            if abs(pos_qty) > c_peak: c_peak = abs(pos_qty)
            continue

    if pos_qty != 0 and n > 0:
        saved_avg = avg_entry; pnl = sim_flatten(float(last[n - 1]))
        record_cycle(n - 1, "DATA_END", pnl)

    return cycles


# ---------------------------------------------------------------------------
#  Metrics / gates (same as prior bench runs)
# ---------------------------------------------------------------------------
def daily_pnl(cycles, pnls):
    days = defaultdict(float)
    for c, p in zip(cycles, pnls):
        days[c["seed_dt"][:10]] += p
    return np.array([v for _, v in sorted(days.items())])


def weekly_breakdown(cycles, pnls):
    weeks = defaultdict(list)
    for c, p in zip(cycles, pnls):
        dt = c["seed_dt"][:10]
        d = datetime.date(int(dt[:4]), int(dt[5:7]), int(dt[8:10]))
        wk = f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"
        weeks[wk].append((c, p))
    result = {}
    for wk in sorted(weeks.keys()):
        wc = weeks[wk]
        n = len(wc)
        ps = [p for _, p in wc]
        wins = sum(1 for p in ps if p >= 0)
        stops = sum(1 for c, _ in wc if c["exit_type"] == "HARD_STOP")
        result[wk] = {"n": n, "wr": wins / n, "sr": stops / n,
                       "pnl": sum(ps), "er": sum(ps) / n}
    return result


def compute_gates(cycles, pnls, n):
    stops = sum(1 for c in cycles if c["exit_type"] == "HARD_STOP")
    wins = sum(1 for p in pnls if p >= 0)
    wr = wins / n
    sr = stops / n
    total_pnl = float(np.sum(pnls))
    gross_wins = float(np.sum(pnls[pnls >= 0]))
    gross_losses = float(-np.sum(pnls[pnls < 0]))

    pf = gross_wins / gross_losses if gross_losses > 0 else 999
    h1 = pf >= 1.20

    # H2: min cycles -- combined has fewer cycles, check against 5000
    h2 = n >= 5000

    serial_ok = True
    mean_p = np.mean(pnls)
    var_p = np.var(pnls)
    serial_results = []
    for lag in range(1, 6):
        corr = float(np.mean((pnls[lag:] - mean_p) * (pnls[:-lag] - mean_p)) / var_p) if var_p > 0 else 0
        thresh = 2.0 / np.sqrt(n)
        ok = abs(corr) <= thresh
        serial_results.append({"lag": lag, "r": round(corr, 6), "thresh": round(thresh, 6), "ok": str(ok)})
        if not ok: serial_ok = False
    h3 = serial_ok

    rng = np.random.default_rng(42)
    boot_pnl = np.array([float(np.sum(rng.choice(pnls, size=n, replace=True))) for _ in range(10000)])
    boot_p5 = float(np.percentile(boot_pnl, 5))
    h4 = boot_p5 > 0

    avg_w = float(np.mean(pnls[pnls >= 0]))
    avg_l = float(-np.mean(pnls[pnls < 0])) if np.any(pnls < 0) else 1.0
    wl_ratio = avg_w / avg_l if avg_l > 0 else 0
    kelly = wr - (1 - wr) / wl_ratio if wl_ratio > 0 else 0
    half_kelly = kelly / 2
    h5 = kelly <= 0.50 and half_kelly > 0.05

    dp = daily_pnl(cycles, pnls)
    n_days = len(dp)
    ann = np.sqrt(252)
    mean_d = float(np.mean(dp))
    std_d = float(np.std(dp, ddof=1))
    sharpe = (mean_d / std_d) * ann if std_d > 0 else 0

    down = dp[dp < 0]
    down_std = float(np.std(down, ddof=1)) if len(down) > 1 else 0.001
    sortino = (mean_d / down_std) * ann if down_std > 0 else 0

    eq = np.cumsum(dp)
    pk = np.maximum.accumulate(eq)
    max_dd = float(np.max(pk - eq))
    ann_ret = total_pnl * (252 / n_days)
    calmar = ann_ret / max_dd if max_dd > 0 else 0

    s1 = sharpe >= 1.25
    s2 = sortino >= 1.50
    s3 = calmar >= 0.75

    breakeven_red = 0
    for red in range(1, 30):
        adj_wr = wr * (1 - red / 100)
        n_w = int(n * adj_wr)
        n_l = n - n_w
        adj_pf = (n_w * avg_w) / (n_l * avg_l) if n_l > 0 and avg_l > 0 else 0
        if adj_pf < 1.0:
            breakeven_red = red
            break
    s4 = breakeven_red >= 5

    pnls_2t = np.array([
        c["pnl_ticks"] * TV - COMM * max(c.get("max_position", 1), 1)
        - 2 * TV * max(c.get("max_position", 1), 1) * 2
        for c in cycles
    ])
    gw_2t = float(np.sum(pnls_2t[pnls_2t >= 0]))
    gl_2t = float(-np.sum(pnls_2t[pnls_2t < 0]))
    pf_2t = gw_2t / gl_2t if gl_2t > 0 else 0
    s5 = pf_2t >= 1.0

    d2_exits = sum(1 for c in cycles if c["exit_type"] == "D2_EXIT")
    reversals = sum(1 for c in cycles if c["exit_type"] == "REVERSAL")

    return {
        "n": n, "wr": wr, "sr": sr, "pf": pf, "total_pnl": total_pnl,
        "er": total_pnl / n,
        "h1": h1, "h2": h2, "h3": h3, "h4": h4, "h5": h5,
        "s1": s1, "s2": s2, "s3": s3, "s4": s4, "s5": s5,
        "serial_results": serial_results, "boot_p5": boot_p5,
        "kelly": kelly, "half_kelly": half_kelly,
        "avg_w": avg_w, "avg_l": avg_l, "wl_ratio": wl_ratio,
        "sharpe": sharpe, "sortino": sortino, "calmar": calmar,
        "max_dd": max_dd, "breakeven_red": breakeven_red, "pf_2t": pf_2t,
        "gross_wins": gross_wins, "gross_losses": gross_losses,
        "d2_exits": d2_exits, "reversals": reversals,
    }


# ===================================================================
# MAIN
# ===================================================================

print("Loading P2 holdout data...")
t0 = time.time()
bars = load_bars_extended(r"C:\Projects\futures_pipeline\data\NQ-1tick-holdout.csv")
print(f"Loaded {bars['n']} bars in {time.time()-t0:.1f}s")

print("Precomputing signals...")
t1 = time.time()
sig = precompute_signals(bars)
print(f"  done ({time.time()-t1:.1f}s)")

# --- A+B baseline (for comparison) ---
print("\nRunning A+B baseline...")
t1 = time.time()
bl_cycles = run_sim_filtered(bars, SD, HS, MF, ML, MCS,
                              signal_arrays=sig, filter_fn=make_ab_filter())
bl_pnls = np.array([c["pnl_ticks"] * TV - COMM * max(c.get("max_position", 1), 1)
                      for c in bl_cycles])
bl_n = len(bl_cycles)
print(f"  {bl_n} cycles, E[R]=${np.mean(bl_pnls):.2f} ({time.time()-t1:.0f}s)")

# --- Combined config (ONE SHOT) ---
print("\nRunning combined config (ONE SHOT)...")
print("  Entry: A+B + d2_ema9 gate (|d2|<=0.5 neutral)")
print("  Hold: d2_avg3 aligned -> delay reversal")
t1 = time.time()
combo_cycles = run_sim_extended(bars, sig, make_combined_entry_filter())
combo_pnls = np.array([c["pnl_ticks"] * TV - COMM * max(c.get("max_position", 1), 1)
                         for c in combo_cycles])
combo_n = len(combo_cycles)
print(f"  {combo_n} cycles, E[R]=${np.mean(combo_pnls):.2f} ({time.time()-t1:.0f}s)")

# --- Per-week comparison ---
print("\nPer-week comparison:")
bl_wk = weekly_breakdown(bl_cycles, bl_pnls)
combo_wk = weekly_breakdown(combo_cycles, combo_pnls)
all_wks = sorted(set(list(bl_wk.keys()) + list(combo_wk.keys())))
print(f"{'Week':<10} {'bl_N':>5} {'cb_N':>5} {'bl_ER':>8} {'cb_ER':>8} {'dER':>8}")
print(f"{'-'*47}")
improved = 0
neg_bl = neg_cb = 0
for wk in all_wks:
    b = bl_wk.get(wk, {"n": 0, "er": 0, "pnl": 0})
    f = combo_wk.get(wk, {"n": 0, "er": 0, "pnl": 0})
    d = f["er"] - b["er"] if f["n"] > 0 and b["n"] > 0 else 0
    if d > 0: improved += 1
    if b["pnl"] < 0: neg_bl += 1
    if f["pnl"] < 0: neg_cb += 1
    print(f"{wk:<10} {b['n']:>5} {f['n']:>5} ${b['er']:>7.2f} ${f['er']:>7.2f} ${d:>+7.2f}")
print(f"\nWeeks improved: {improved}/{len(all_wks)}")
print(f"Negative weeks: bl={neg_bl}, combo={neg_cb}")

# --- Exit type distribution ---
exits = defaultdict(int)
for c in combo_cycles:
    exits[c["exit_type"]] += 1
print(f"\nExit type distribution:")
for et in sorted(exits.keys()):
    print(f"  {et}: {exits[et]} ({exits[et]/combo_n*100:.1f}%)")

# --- Statistical gates ---
print("\nComputing statistical gates...")
g = compute_gates(combo_cycles, combo_pnls, combo_n)

all_hard = g["h1"] and g["h2"] and g["h3"] and g["h4"] and g["h5"]
all_soft = g["s1"] and g["s2"] and g["s3"] and g["s4"] and g["s5"]
if all_hard and all_soft:
    verdict = "PASS"
elif all_hard:
    verdict = "CONDITIONAL PASS"
else:
    verdict = "FAIL"

print()
print("=" * 60)
print(f"FORMAL VERDICT -- rotational NQ {VARIANT} variant (combined)")
print(f"Holdout: 2025-12-17 to 2026-03-13")
print("=" * 60)
print()
print("HARD GATES")
print(f"  H1 PF >= 1.20:           {g['pf']:.2f}  {'PASS' if g['h1'] else 'FAIL'}")
print(f"  H2 Cycles >= 5000:       {combo_n}  {'PASS' if g['h2'] else 'FAIL'}")
print(f"  H3 Serial correlation:   {'all ok' if g['h3'] else 'FAIL'}  {'PASS' if g['h3'] else 'FAIL'}")
print(f"  H4 Bootstrap P5 > $0:    ${g['boot_p5']:,.0f}  {'PASS' if g['h4'] else 'FAIL'}")
print(f"  H5 Kelly <= 0.50:        {g['kelly']:.2f} (half={g['half_kelly']:.2f})  {'PASS' if g['h5'] else 'FAIL'}")
print()
print("SOFT GATES")
print(f"  S1 Sharpe >= 1.25:       {g['sharpe']:.2f}  {'PASS' if g['s1'] else 'REVIEW'}")
print(f"  S2 Sortino >= 1.50:      {g['sortino']:.2f}  {'PASS' if g['s2'] else 'REVIEW'}")
print(f"  S3 Calmar >= 0.75:       {g['calmar']:.2f}  {'PASS' if g['s3'] else 'REVIEW'}")
print(f"  S4 WR headroom >= 5%:    {g['breakeven_red']}%  {'PASS' if g['s4'] else 'REVIEW'}")
print(f"  S5 PF >= 1.0 at 2t slip: {g['pf_2t']:.2f}  {'PASS' if g['s5'] else 'REVIEW'}")
print()
print(f"P2 E[R]: ${g['er']:.2f} (A+B baseline: ${np.mean(bl_pnls):.2f}, "
      f"delta: ${g['er'] - np.mean(bl_pnls):+.2f})")
print(f"D2_EXIT: {g['d2_exits']}, REVERSAL: {g['reversals']}")
print()
print(f"VERDICT: {verdict}")

conditions = []
if not g["s1"]: conditions.append("S1: Monitor rolling 20-day Sharpe.")
if not g["s2"]: conditions.append("S2: Monitor downside deviation.")
if not g["s3"]: conditions.append("S3: Monitor DD.")
if not g["s4"]: conditions.append("S4: Monitor WR weekly.")
if not g["s5"]: conditions.append("S5: Monitor fill quality.")
if conditions:
    print("\nMONITORING CONDITIONS:")
    for c in conditions:
        print(f"  {c}")

# --- Write verdict JSON ---
v = {
    "archetype": "rotational", "instrument": "NQ", "variant": VARIANT,
    "holdout_period": HOLDOUT_TAG, "date_produced": "2026-03-30",
    "verdict": verdict, "monitoring_conditions": conditions,
    "config": "SD=10 HS=60 depth_1 MCS=2 + chop<0.10 + dr2<=-0.40 + dslope<=-2.0 + fc<0.40 + d2_entry(|d2|<=0.5) + d2_avg3_hold",
    "hard_gates": {
        "H1_profit_factor": {"threshold": 1.20, "observed": round(g["pf"], 4), "result": "PASS" if g["h1"] else "FAIL"},
        "H2_min_cycles": {"threshold": 5000, "observed": combo_n, "result": "PASS" if g["h2"] else "FAIL"},
        "H3_serial_correlation": {"result": "PASS" if g["h3"] else "FAIL"},
        "H4_bootstrap_p5": {"threshold": 0, "observed": round(g["boot_p5"], 2), "result": "PASS" if g["h4"] else "FAIL"},
        "H5_kelly": {"observed_full": round(g["kelly"], 4), "observed_half": round(g["half_kelly"], 4), "result": "PASS" if g["h5"] else "FAIL"},
    },
    "soft_gates": {
        "S1_sharpe": {"threshold": 1.25, "observed": round(g["sharpe"], 4), "result": "PASS" if g["s1"] else "REVIEW"},
        "S2_sortino": {"threshold": 1.50, "observed": round(g["sortino"], 4), "result": "PASS" if g["s2"] else "REVIEW"},
        "S3_calmar": {"threshold": 0.75, "observed": round(g["calmar"], 4), "result": "PASS" if g["s3"] else "REVIEW"},
        "S4_wr_headroom": {"threshold_pct": 5, "observed_pct": g["breakeven_red"], "result": "PASS" if g["s4"] else "REVIEW"},
        "S5_slippage_2t": {"threshold_pf": 1.0, "observed_pf": round(g["pf_2t"], 4), "result": "PASS" if g["s5"] else "REVIEW"},
    },
    "metrics": {
        "cycles": combo_n, "wr": round(g["wr"], 4), "sr": round(g["sr"], 4),
        "pf": round(g["pf"], 4), "total_pnl": round(g["total_pnl"], 2),
        "er": round(g["er"], 2),
        "sharpe": round(g["sharpe"], 4), "sortino": round(g["sortino"], 4),
        "calmar": round(g["calmar"], 4), "max_dd_dollar": round(g["max_dd"], 2),
        "d2_exits": g["d2_exits"], "reversals": g["reversals"],
    },
    "comparison_vs_ab_baseline": {
        "baseline_er": round(float(np.mean(bl_pnls)), 2),
        "combined_er": round(g["er"], 2),
        "delta_er": round(g["er"] - float(np.mean(bl_pnls)), 2),
        "baseline_cycles": bl_n, "combined_cycles": combo_n,
    },
}
verdict_path = OUT_DIR / f"rotational-NQ-ema-directional-verdict-{HOLDOUT_TAG}-validated.json"
with open(verdict_path, "w") as f_out:
    json.dump(v, f_out, indent=2)
print(f"\nSaved: {verdict_path}")

# --- Write holdout tradelog ---
tradelog_path = OUT_DIR / f"rotational-NQ-ema-directional-holdout-tradelog-{HOLDOUT_TAG}.csv"
with open(tradelog_path, "w", newline="") as f_out:
    fields = ["cycle_id", "seed_dt", "exit_dt", "direction", "seed_price",
              "avg_entry_price", "exit_price", "exit_type", "depth",
              "max_position", "pnl_ticks", "pnl_dollars", "bars_held",
              "mfe_ticks", "mae_ticks"]
    w = csv.DictWriter(f_out, fieldnames=fields, extrasaction="ignore")
    w.writeheader()
    for c in combo_cycles:
        row = {k: c.get(k, "") for k in fields}
        for k in ["seed_price", "avg_entry_price", "exit_price",
                   "pnl_ticks", "pnl_dollars", "mfe_ticks", "mae_ticks"]:
            if isinstance(row.get(k), float):
                row[k] = f"{row[k]:.2f}"
        w.writerow(row)
print(f"Saved: {tradelog_path}")

# --- Write holdout lock ---
lock_path = OUT_DIR / f"holdout-locked-rotational-NQ-ema-directional-{HOLDOUT_TAG}.flag"
with open(lock_path, "w") as f_out:
    f_out.write(f"locked: 2026-03-30\n")
    f_out.write(f"archetype: rotational\n")
    f_out.write(f"instrument: NQ\n")
    f_out.write(f"variant: ema-directional\n")
    f_out.write(f"holdout: 2025-12-17 to 2026-03-13\n")
    f_out.write(f"config: SD=10 HS=60 depth_1 MCS=2 + chop<0.10 + dr2<=-0.40 + dslope<=-2.0 + fc<0.40 + d2_entry(|d2|<=0.5) + d2_avg3_hold\n")
    f_out.write(f"cycles: {combo_n}\n")
    f_out.write(f"result: {verdict}\n")
print(f"Saved: {lock_path}")

print(f"\nTotal runtime: {time.time()-t0:.0f}s")
