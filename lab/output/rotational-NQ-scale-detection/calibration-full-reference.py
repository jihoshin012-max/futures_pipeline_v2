"""Generate Python calibration reference for ATEAM_ROTATION_V3_FULL.

Hybrid approach:
  - Features computed on SC's actual 250-tick bar export (no aggregation mismatch)
  - Simulation runs tick-by-tick on 1-tick data (exact entry/exit prices)
  - Gate decisions use PREVIOUS completed bar's features (matches live ci-1)
  - Tick-to-bar mapping: tick i -> bar i // 250 (SC counts continuously)

Usage:
    python calibration-full-reference.py [--tick-file PATH] [--bar-file PATH]

Output: calibration-full-python.csv
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
from pathlib import Path

import numpy as np

# ---------------------------------------------------------------------------
#  Frozen params (from rotational-NQ-ema-directional-params-frozen.json)
# ---------------------------------------------------------------------------
STEP_DIST     = 10.0
HARD_STOP     = 60.0    # ticks
INITIAL_QTY   = 1
MAX_LEVELS    = 1
MAX_CONTRACT  = 2
MAX_FADES     = 0       # unlimited
TICK_SIZE     = 0.25

CHOP_THRESH   = 0.10
CHOP_LOOKBACK = 3
DR2_THRESH    = -0.40
DSLOPE_THRESH = -2.0
FC_THRESH     = 0.40
D2_NEUTRAL    = 0.5
EMA_PERIOD    = 9

BAR_SIZE      = 250

RTH_OPEN_SEC  = 9 * 3600 + 30 * 60
RTH_CLOSE_SEC = 15 * 3600 + 49 * 60 + 50


# ---------------------------------------------------------------------------
#  Load SC 250-tick bar export
# ---------------------------------------------------------------------------
def load_sc_bars(filepath: str | Path):
    """Load SC 250-tick bar CSV. Date format: M/D/YYYY."""
    close_arr, high_arr, low_arr, range_arr = [], [], [], []

    with open(filepath, "r") as f:
        reader = csv.reader(f)
        next(reader)  # header
        for row in reader:
            if len(row) < 6:
                continue
            h, l, c = float(row[3]), float(row[4]), float(row[5])
            close_arr.append(c)
            high_arr.append(h)
            low_arr.append(l)
            range_arr.append(h - l)

    n = len(close_arr)
    return {
        "n": n,
        "close": np.array(close_arr, dtype=np.float64),
        "high": np.array(high_arr, dtype=np.float64),
        "low": np.array(low_arr, dtype=np.float64),
        "range": np.array(range_arr, dtype=np.float64),
    }


# ---------------------------------------------------------------------------
#  Load 1-tick data
# ---------------------------------------------------------------------------
def load_ticks(filepath: str | Path):
    """Load SC 1-tick CSV. Date format: YYYY-M-D."""
    last_arr, high_arr, low_arr = [], [], []
    tsec_arr, dint_arr, dt_arr = [], [], []

    with open(filepath, "r") as f:
        reader = csv.reader(f)
        next(reader)
        for row in reader:
            if len(row) < 6:
                continue
            date_str = row[0].strip()
            time_str = row[1].strip()
            h, l, c = float(row[3]), float(row[4]), float(row[5])

            parts = time_str.split(":")
            hr, mn = int(parts[0]), int(parts[1])
            sec = int(float(parts[2])) if len(parts) > 2 else 0
            time_sec = hr * 3600 + mn * 60 + sec

            dp = date_str.split("-")
            yr, mo, dy = int(dp[0]), int(dp[1]), int(dp[2])
            date_int = yr * 10000 + mo * 100 + dy

            dt_str = f"{yr:04d}-{mo:02d}-{dy:02d} {hr:02d}:{mn:02d}:{sec:02d}"

            high_arr.append(h)
            low_arr.append(l)
            last_arr.append(c)
            tsec_arr.append(time_sec)
            dint_arr.append(date_int)
            dt_arr.append(dt_str)

    n = len(last_arr)
    return {
        "n": n,
        "high": np.array(high_arr, dtype=np.float32),
        "low": np.array(low_arr, dtype=np.float32),
        "last": np.array(last_arr, dtype=np.float32),
        "time_sec": np.array(tsec_arr, dtype=np.int32),
        "date_int": np.array(dint_arr, dtype=np.int32),
        "datetime": dt_arr,
    }


# ---------------------------------------------------------------------------
#  Compute features on SC 250-tick bars
# ---------------------------------------------------------------------------
def compute_features(sc_bars: dict):
    """Compute all gate features directly on SC's 250-tick bar data."""
    n = sc_bars["n"]
    ac = sc_bars["close"]
    ar = sc_bars["range"]
    ah = sc_bars["high"]
    al = sc_bars["low"]

    # --- Choppiness ---
    chop = np.full(n, -1.0)
    for a in range(CHOP_LOOKBACK - 1, n):
        net_move = abs(ac[a] - ac[a - CHOP_LOOKBACK + 1])
        sum_range = sum(ar[a - CHOP_LOOKBACK + 1 : a + 1])
        chop[a] = (net_move / sum_range) if sum_range > 1e-4 else 0.0

    # --- Linear regression (lb=3) ---
    def linreg3(y0, y1, y2):
        mean_y = (y0 + y1 + y2) / 3.0
        slope = (y2 - y0) / 2.0
        intercept = mean_y - slope
        yh0 = intercept
        yh1 = intercept + slope
        yh2 = intercept + 2 * slope
        ss_res = (y0 - yh0)**2 + (y1 - yh1)**2 + (y2 - yh2)**2
        ss_tot = (y0 - mean_y)**2 + (y1 - mean_y)**2 + (y2 - mean_y)**2
        return slope, (1.0 - ss_res / ss_tot) if ss_tot > 1e-10 else 1.0

    r2 = np.ones(n)
    slope = np.zeros(n)
    dr2 = np.zeros(n)
    dslope = np.zeros(n)

    for a in range(2, n):
        slope[a], r2[a] = linreg3(ac[a-2], ac[a-1], ac[a])
        if a >= 3:
            prev_slope, prev_r2 = linreg3(ac[a-3], ac[a-2], ac[a-1])
            dr2[a] = r2[a] - prev_r2
            dslope[a] = abs(slope[a]) - abs(prev_slope)

    # --- EMA derivatives ---
    ema = np.zeros(n)
    dema = np.zeros(n)
    d2ema = np.zeros(n)
    d2avg3 = np.zeros(n)
    mult = 2.0 / (EMA_PERIOD + 1)

    ema[0] = ac[0]
    for a in range(1, n):
        ema[a] = ac[a] * mult + ema[a-1] * (1.0 - mult)

    for a in range(1, n):
        dema[a] = ema[a] - ema[a-1]
    for a in range(2, n):
        d2ema[a] = dema[a] - dema[a-1]
    for a in range(4, n):
        d2avg3[a] = (d2ema[a] + d2ema[a-1] + d2ema[a-2]) / 3.0

    return {
        "chop": chop,
        "dr2": dr2,
        "dslope": dslope,
        "d2ema9": d2ema,
        "d2avg3": d2avg3,
        "high": ah,
        "low": al,
        "n_bars": n,
    }


# ---------------------------------------------------------------------------
#  Simulation: 1-tick prices + SC bar features (prevBar)
# ---------------------------------------------------------------------------
def run_full_sim(ticks: dict, features: dict) -> list[dict]:
    """Run rotation sim tick-by-tick. Gate features from prev completed SC bar."""
    n = ticks["n"]
    last = ticks["last"]
    high_arr = ticks["high"]
    low_arr = ticks["low"]
    tsec = ticks["time_sec"]
    dint = ticks["date_int"]
    dt = ticks["datetime"]

    n_bars = features["n_bars"]
    f_chop = features["chop"]
    f_dr2 = features["dr2"]
    f_dsl = features["dslope"]
    f_d2 = features["d2ema9"]
    f_d2a3 = features["d2avg3"]
    f_hi = features["high"]
    f_lo = features["low"]

    def get_bar(tick_idx):
        """Map tick index to SC bar index."""
        return min(tick_idx // BAR_SIZE, n_bars - 1)

    def get_prev_bar(tick_idx):
        """Previous completed bar for gate decisions."""
        b = get_bar(tick_idx)
        return max(b - 1, 0)

    # State
    anchor = 0.0; watch_p = 0.0; watch_hi = 0.0; watch_lo = 0.0
    direction = 0; level = 0; fade_l = 0; fade_s = 0
    pos_qty = 0; avg_entry = 0.0; total_cost = 0.0
    hold_active = 0; hold_count = 0

    cycle_id = 0
    ws_dt = ""; ws_price = 0.0; ws_high = 0.0; ws_low = 0.0; ws_bar = 0
    cs_bar = 0; c_depth = 0; c_peak = 0; c_mfe = 0.0; c_mae = 0.0
    saved_avg = 0.0; rth_active = False
    cycles: list[dict] = []

    def reset():
        nonlocal anchor, direction, level, watch_p, watch_hi, watch_lo, hold_active
        anchor = 0.0; direction = 0; level = 0
        watch_p = 0.0; watch_hi = 0.0; watch_lo = 0.0; hold_active = 0

    def start_watch(i):
        nonlocal ws_dt, ws_price, ws_high, ws_low, ws_bar
        nonlocal c_depth, c_peak, c_mfe, c_mae, hold_active, hold_count
        ws_dt = dt[i]; ws_price = float(last[i])
        ws_high = float(last[i]); ws_low = float(last[i])
        ws_bar = i; c_depth = 0; c_peak = 0; c_mfe = 0.0; c_mae = 0.0
        hold_active = 0; hold_count = 0

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
                pnl = (price - avg_entry) / TICK_SIZE * abs(pos_qty)
            else:
                pnl = (avg_entry - price) / TICK_SIZE * abs(pos_qty)
        pos_qty = 0; avg_entry = 0.0; total_cost = 0.0
        return pnl

    def record_cycle(i, exit_type, pnl):
        nonlocal cycle_id
        cycles.append({
            "cycle_id": cycle_id,
            "watch_start_dt": ws_dt, "watch_price": ws_price,
            "watch_high": ws_high, "watch_low": ws_low,
            "watch_bars": cs_bar - ws_bar if cs_bar > ws_bar else 0,
            "seed_dt": dt[cs_bar], "exit_dt": dt[i],
            "direction": "LONG" if direction == 1 else "SHORT",
            "seed_price": float(last[cs_bar]),
            "avg_entry_price": saved_avg, "exit_price": float(last[i]),
            "exit_type": exit_type, "depth": c_depth, "max_position": c_peak,
            "pnl_ticks": round(pnl, 2), "pnl_dollars": round(pnl * 5.0, 2),
            "bars_held": i - cs_bar, "mfe_ticks": round(c_mfe, 2),
            "mae_ticks": round(c_mae, 2), "hold_count": hold_count,
        })
        cycle_id += 1

    def fade_blocked(d):
        if MAX_FADES <= 0:
            return False
        return (d == 1 and fade_l >= MAX_FADES) or (d == -1 and fade_s >= MAX_FADES)

    def update_fade(d):
        nonlocal fade_l, fade_s
        if d == 1: fade_l += 1; fade_s = 0
        else: fade_s += 1; fade_l = 0

    def check_gates(tick_idx, d, price):
        """Check all entry gates using prev completed SC bar features."""
        pb = get_prev_bar(tick_idx)

        # Gate 1: chop
        if f_chop[pb] >= 0 and f_chop[pb] >= CHOP_THRESH:
            return False
        # Gate 2: dR2/dSlope
        if f_dr2[pb] > DR2_THRESH or f_dsl[pb] > DSLOPE_THRESH:
            return False
        # Gate 3: fade_confirm (uses prev completed bar's high/low)
        if pb >= 1:
            prev_h = float(f_hi[pb])
            prev_l = float(f_lo[pb])
            rng = prev_h - prev_l
            if rng < 1e-4:
                fc = 0.5
            elif d == 1:
                fc = (price - prev_l) / rng
            else:
                fc = (prev_h - price) / rng
            if fc >= FC_THRESH:
                return False
        # Gate 4: d2_ema9
        d2 = f_d2[pb]
        if d2 > D2_NEUTRAL and d == -1:
            return False
        if d2 < -D2_NEUTRAL and d == 1:
            return False
        return True

    # --- Main loop ---
    for i in range(n):
        price = float(last[i])
        time_sec = int(tsec[i])

        # Session
        if RTH_OPEN_SEC <= time_sec <= RTH_CLOSE_SEC:
            if not rth_active:
                rth_active = True
                if pos_qty != 0:
                    saved_avg = avg_entry
                    sim_flatten(price)
                reset()
                fade_l = 0; fade_s = 0
                start_watch(i)
        else:
            if rth_active and time_sec > RTH_CLOSE_SEC:
                rth_active = False
            continue
        # EOD
        if time_sec >= RTH_CLOSE_SEC:
            if pos_qty != 0:
                hold_active = 0
                saved_avg = avg_entry
                pnl = sim_flatten(price)
                record_cycle(i, "EOD_FLATTEN", pnl)
                reset()
            elif watch_p != 0:
                reset()
            rth_active = False
            continue

        # MFE/MAE
        if pos_qty != 0:
            if pos_qty > 0:
                exc = (price - avg_entry) / TICK_SIZE
            else:
                exc = (avg_entry - price) / TICK_SIZE
            if exc > c_mfe: c_mfe = exc
            if -exc > c_mae: c_mae = -exc
            if pos_qty > 0:
                hi_exc = (float(high_arr[i]) - avg_entry) / TICK_SIZE
                lo_exc = (float(low_arr[i]) - avg_entry) / TICK_SIZE
            else:
                hi_exc = (avg_entry - float(low_arr[i])) / TICK_SIZE
                lo_exc = (avg_entry - float(high_arr[i])) / TICK_SIZE
            if hi_exc > c_mfe: c_mfe = hi_exc
            if -lo_exc > c_mae: c_mae = -lo_exc

        # Hard stop
        if pos_qty != 0 and HARD_STOP > 0:
            if pos_qty > 0:
                unreal = (avg_entry - price) / TICK_SIZE
            else:
                unreal = (price - avg_entry) / TICK_SIZE
            if unreal >= HARD_STOP:
                hold_active = 0
                saved_avg = avg_entry
                pnl = sim_flatten(price)
                record_cycle(i, "HARD_STOP", pnl)
                reset(); start_watch(i)
                continue

        # Watching
        if pos_qty == 0 and anchor == 0.0:
            if watch_p == 0.0:
                watch_p = price; watch_hi = price; watch_lo = price
                if not ws_dt: start_watch(i)
                continue
            if price > watch_hi: watch_hi = price
            if price < watch_lo: watch_lo = price
            if price > ws_high: ws_high = price
            if price < ws_low: ws_low = price

            pull_hi = watch_hi - price
            pull_lo = price - watch_lo
            seed_dir = 0
            if pull_hi >= STEP_DIST and pull_lo >= STEP_DIST:
                seed_dir = 1 if pull_hi >= pull_lo else -1
            elif pull_hi >= STEP_DIST: seed_dir = 1
            elif pull_lo >= STEP_DIST: seed_dir = -1
            else: continue

            if fade_blocked(seed_dir):
                seed_dir = -seed_dir
                other = (pull_hi >= STEP_DIST) if seed_dir == 1 else (pull_lo >= STEP_DIST)
                if not other or fade_blocked(seed_dir): continue

            if not check_gates(i, seed_dir, price):
                continue

            sim_entry(seed_dir, INITIAL_QTY, price)
            direction = seed_dir; level = 0; anchor = price; watch_p = 0.0
            cs_bar = i; c_depth = 0; c_peak = abs(pos_qty)
            c_mfe = 0.0; c_mae = 0.0; hold_active = 0; hold_count = 0
            update_fade(seed_dir)
            continue

        # In position
        if pos_qty == 0:
            reset(); start_watch(i); continue

        up_move = price - anchor
        dn_move = anchor - price
        in_favor = (up_move >= STEP_DIST) if direction == 1 else (dn_move >= STEP_DIST)
        against = (dn_move >= STEP_DIST) if direction == 1 else (up_move >= STEP_DIST)

        # Reversal trigger
        if in_favor:
            pb = get_prev_bar(i)
            d2a3 = float(f_d2a3[pb])
            aligned = (d2a3 > 0.0) if direction == 1 else (d2a3 <= 0.0)
            if aligned:
                anchor = price
                hold_active = 1
                hold_count += 1
                continue

            hold_active = 0
            saved_avg = avg_entry
            pnl = sim_flatten(price)
            record_cycle(i, "REVERSAL", pnl)

            new_dir = -direction
            if fade_blocked(new_dir):
                reset(); start_watch(i); continue
            if not check_gates(i, new_dir, price):
                reset(); start_watch(i); continue

            sim_entry(new_dir, INITIAL_QTY, price)
            direction = new_dir; level = 0; anchor = price
            cs_bar = i; c_depth = 0; c_peak = abs(pos_qty)
            c_mfe = 0.0; c_mae = 0.0; hold_active = 0; hold_count = 0
            update_fade(new_dir)
            ws_dt = dt[i]; ws_price = price; ws_high = price; ws_low = price; ws_bar = i
            continue

        # D2 exit
        if hold_active:
            pb = get_prev_bar(i)
            d2a3 = float(f_d2a3[pb])
            flipped = (d2a3 <= 0.0) if direction == 1 else (d2a3 > 0.0)
            if flipped:
                hold_active = 0
                saved_avg = avg_entry
                pnl = sim_flatten(price)
                record_cycle(i, "D2_EXIT", pnl)
                reset(); start_watch(i)
                continue

        # Add
        if against:
            use_level = level
            if use_level >= MAX_LEVELS: use_level = 0
            add_qty = int(INITIAL_QTY * (2 ** use_level) + 0.5)
            abs_pos = abs(pos_qty)
            if abs_pos + add_qty > MAX_CONTRACT:
                room = MAX_CONTRACT - abs_pos
                if room <= 0: continue
                add_qty = room
                level = 0
            sim_entry(direction, add_qty, price)
            level += 1
            if level >= MAX_LEVELS: level = 0
            anchor = price
            c_depth += 1
            if abs(pos_qty) > c_peak: c_peak = abs(pos_qty)
            continue

    # End of data
    if pos_qty != 0 and n > 0:
        saved_avg = avg_entry
        pnl = sim_flatten(float(last[n-1]))
        record_cycle(n-1, "DATA_END", pnl)

    return cycles


# ---------------------------------------------------------------------------
#  Output
# ---------------------------------------------------------------------------
def write_cycles_csv(cycles: list[dict], path: Path):
    cols = [
        "cycle_id", "watch_start_dt", "watch_price", "watch_high", "watch_low",
        "watch_bars", "seed_dt", "exit_dt", "direction", "seed_price",
        "avg_entry_price", "exit_price", "exit_type", "depth", "max_position",
        "pnl_ticks", "pnl_dollars", "bars_held", "mfe_ticks", "mae_ticks",
        "hold_count",
    ]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for c in cycles:
            row = {k: c.get(k, "") for k in cols}
            for fk in ("watch_price", "watch_high", "watch_low", "seed_price",
                        "avg_entry_price", "exit_price", "pnl_ticks",
                        "pnl_dollars", "mfe_ticks", "mae_ticks"):
                if fk in row and isinstance(row[fk], float):
                    row[fk] = f"{row[fk]:.2f}"
            w.writerow(row)
    print(f"Wrote {len(cycles)} cycles to {path}")


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(description="Calibration reference (SC bars + 1-tick sim)")
    parser.add_argument("--tick-file", type=str,
                        default=r"C:\Projects\futures_pipeline\data\NQ-1tick-calibration-1day.csv")
    parser.add_argument("--bar-file", type=str,
                        default=r"C:\Projects\futures_pipeline\data\NQ-250tick-calibration.csv")
    parser.add_argument("--output-dir", type=str,
                        default=r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")
    args = parser.parse_args()

    tick_path = Path(args.tick_file)
    bar_path = Path(args.bar_file)
    if not tick_path.exists():
        print(f"ERROR: Cannot find tick data at {tick_path}"); sys.exit(1)
    if not bar_path.exists():
        print(f"ERROR: Cannot find bar data at {bar_path}"); sys.exit(1)

    print(f"Loading SC 250-tick bars from {bar_path}...")
    sc_bars = load_sc_bars(bar_path)
    print(f"Loaded {sc_bars['n']} bars")

    print(f"Loading 1-tick data from {tick_path}...")
    ticks = load_ticks(tick_path)
    print(f"Loaded {ticks['n']} ticks")

    # Verify alignment
    expected_bars = ticks["n"] // BAR_SIZE
    print(f"Expected bars for {ticks['n']} ticks: ~{expected_bars} "
          f"(SC has {sc_bars['n']} total, using first {expected_bars + 1})")

    print("Computing features on SC bars...")
    features = compute_features(sc_bars)

    print("Running simulation...")
    cycles = run_full_sim(ticks, features)

    # Summary
    wins = sum(1 for c in cycles if c["pnl_ticks"] >= 0)
    losses = len(cycles) - wins
    total_pnl = sum(c["pnl_ticks"] for c in cycles)
    holds = sum(c["hold_count"] for c in cycles)
    d2_exits = sum(1 for c in cycles if c["exit_type"] == "D2_EXIT")
    print(f"Result: {len(cycles)} cycles ({wins}W/{losses}L), "
          f"PnL={total_pnl:.1f}t, {holds} holds, {d2_exits} D2 exits")

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    write_cycles_csv(cycles, out_dir / "calibration-full-python.csv")


if __name__ == "__main__":
    main()
