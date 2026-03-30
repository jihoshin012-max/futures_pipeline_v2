# archetype: rotational
"""
rotational-NQ-scale-detection-step24.py — Track C Step 1: Compute and tag.

Runs A+B combined config on 5 test weeks with on_bar_in_trade callback.
Records per-250-tick-bar snapshots with regime features and raw trade data.
Also outputs per-cycle summary with entry features and outcome.

Prompt: rotational-NQ-prompt-trade-management-c.md Step 1
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

TEST_WEEKS = {
    "2025-W40": "WEAKEST",
    "2025-W41": "MID",
    "2025-W46": "GOOD",
    "2025-W47": "BEST",
    "2025-W48": "LOW",
}

OUTPUT_DIR = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")


# ---------------------------------------------------------------------------
#  Signal precomputation (same as step23)
# ---------------------------------------------------------------------------

def precompute_signals(bars, bar_size, lookback):
    agg_bars, tick_to_agg = aggregate_to_ntick(bars, bar_size)
    regime = compute_regime_signals(agg_bars, lookback=lookback)
    entry = compute_entry_signals(agg_bars, lookback=lookback)

    n_ticks = bars["n"]
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

    return {
        "choppiness": map_signal_to_ticks(regime["choppiness"], tick_to_agg),
        "slope": map_signal_to_ticks(regime["slope"], tick_to_agg),
        "dr2": map_signal_to_ticks(entry["dr2"], tick_to_agg),
        "dslope": map_signal_to_ticks(entry["dslope"], tick_to_agg),
        "tick_to_agg": tick_to_agg,
        "warmup_ticks": {"dominant_scale": 0, "asymmetry": 0},
        "prev_high": prev_high[tick_to_agg],
        "prev_low": prev_low[tick_to_agg],
        "prev_range": prev_range[tick_to_agg],
        "last": last,
        # Agg-bar level regime signals (for in-trade lookup)
        "agg_signed_chop": entry["signed_chop"],
        "agg_dchop": entry["dchop"],
        "agg_d2chop": entry["d2chop"],
        "agg_signed_slope": entry["signed_slope"],
        "agg_dr2": entry["dr2"],
        "agg_dslope": entry["dslope"],
        "agg_r2": entry["r2"],
        "agg_choppiness": entry["choppiness"],
        "agg_slope_abs": entry["slope_abs"],
        # Agg bar metadata for range computation
        "agg_high": agg_bars["high"].astype(np.float64),
        "agg_low": agg_bars["low"].astype(np.float64),
    }


# ---------------------------------------------------------------------------
#  A+B Filter (same as step23)
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
#  In-trade bar recorder (downsample to agg-bar resolution)
# ---------------------------------------------------------------------------

class InTradeRecorder:
    """Captures one row per agg-bar boundary during each trade.

    The callback fires per tick. We detect when tick_to_agg[bar_idx] changes
    and record a snapshot at the LAST tick of each agg bar (most up-to-date
    MFE/MAE/PnL for that bar).
    """

    def __init__(self, tick_to_agg, signals):
        self.tick_to_agg = tick_to_agg
        self.signals = signals
        self.bar_rows = []  # list of dicts (per agg bar snapshots)

        # State tracking
        self._prev_cycle_id = -1
        self._prev_agg_idx = -1
        self._agg_bar_offset = 0  # how many agg bars into the trade
        self._entry_agg_idx = -1
        # Pending snapshot (updated per tick, flushed on agg bar change)
        self._pending = None

    def __call__(self, bar_idx, cycle_id, bar_offset, price, direction,
                 pnl_ticks, mfe_ticks, mae_ticks):
        agg_idx = int(self.tick_to_agg[bar_idx])

        # New cycle started
        if cycle_id != self._prev_cycle_id:
            # Flush last pending snapshot from previous cycle
            if self._pending is not None:
                self.bar_rows.append(self._pending)
                self._pending = None
            self._prev_cycle_id = cycle_id
            self._entry_agg_idx = agg_idx
            self._agg_bar_offset = 0
            self._prev_agg_idx = agg_idx

        # Agg bar boundary crossed — flush previous agg bar's snapshot
        if agg_idx != self._prev_agg_idx:
            if self._pending is not None:
                self.bar_rows.append(self._pending)
            self._agg_bar_offset += 1
            self._prev_agg_idx = agg_idx

        # Build/update pending snapshot for current agg bar
        sigs = self.signals
        self._pending = {
            "cycle_id": cycle_id,
            "agg_bar_offset": self._agg_bar_offset,
            "agg_idx": agg_idx,
            "tick_bar_offset": bar_offset,
            "price": price,
            "direction": direction,
            "pnl_ticks": pnl_ticks,
            "mfe_ticks": mfe_ticks,
            "mae_ticks": mae_ticks,
            # Regime signals at this agg bar
            "signed_chop": _safe(sigs["agg_signed_chop"], agg_idx),
            "dchop": _safe(sigs["agg_dchop"], agg_idx),
            "d2chop": _safe(sigs["agg_d2chop"], agg_idx),
            "signed_slope": _safe(sigs["agg_signed_slope"], agg_idx),
            "dr2": _safe(sigs["agg_dr2"], agg_idx),
            "dslope": _safe(sigs["agg_dslope"], agg_idx),
            "r2": _safe(sigs["agg_r2"], agg_idx),
            "choppiness": _safe(sigs["agg_choppiness"], agg_idx),
            "slope_abs": _safe(sigs["agg_slope_abs"], agg_idx),
            # Bar range for range_ratio computation
            "agg_bar_range": float(sigs["agg_high"][agg_idx]) - float(sigs["agg_low"][agg_idx]),
        }

    def flush(self):
        """Flush the final pending snapshot."""
        if self._pending is not None:
            self.bar_rows.append(self._pending)
            self._pending = None


def _safe(arr, idx):
    """Safe array access returning None for NaN."""
    v = arr[idx]
    return None if np.isnan(v) else float(v)


# ---------------------------------------------------------------------------
#  Helpers
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


def cycle_week(c):
    dt = c["seed_dt"][:10]
    d = datetime.date(int(dt[:4]), int(dt[5:7]), int(dt[8:10]))
    return f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------

def main():
    import argparse
    parser = argparse.ArgumentParser(description="Track C Step 1 — Compute and tag")
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
    # Run A+B combined with in-trade recording
    # ===================================================================
    print(f"\n{'='*70}")
    print(f"TRACK C STEP 1: Instrumented sim with in-trade bar recording")
    print(f"Filter: chop<{CHOP_THRESHOLD} + dR2<={DR2_MAX} + dSlope<={DSLOPE_MAX} + fc<{FC_MAX}")
    print(f"Test weeks: {', '.join(sorted(TEST_WEEKS.keys()))}")
    print(f"{'='*70}")

    ab_filter = make_ab_filter(CHOP_THRESHOLD, DR2_MAX, DSLOPE_MAX, FC_MAX)
    recorder = InTradeRecorder(signals["tick_to_agg"], signals)

    t1 = time.time()
    all_cycles = run_sim_filtered(
        bars, step_dist=SD, hard_stop=HS,
        max_fades=MAX_FADES, max_levels=MAX_LEVELS,
        max_contract_size=MAX_CONTRACT_SIZE,
        signal_arrays=signals, filter_fn=ab_filter,
        on_bar_in_trade=recorder,
    )
    recorder.flush()
    print(f"  {len(all_cycles)} total cycles, {len(recorder.bar_rows)} agg-bar snapshots ({time.time()-t1:.0f}s)")

    # ===================================================================
    # Filter to test weeks
    # ===================================================================
    test_cycles = [c for c in all_cycles if cycle_week(c) in TEST_WEEKS]
    test_cycle_ids = set(c["cycle_id"] for c in test_cycles)
    test_bars = [r for r in recorder.bar_rows if r["cycle_id"] in test_cycle_ids]

    print(f"\n  Test week cycles: {len(test_cycles)}")
    print(f"  Test week agg-bar snapshots: {len(test_bars)}")

    # Per-week breakdown
    weeks = defaultdict(list)
    for c in test_cycles:
        weeks[cycle_week(c)].append(c)

    print(f"\n  {'Week':<10} {'Cat':<8} {'N':>5} {'WR':>5} {'SR':>5} {'E[R]':>8} {'Stops':>5}")
    print(f"  {'-'*55}")
    for wk in sorted(weeks.keys()):
        cat = TEST_WEEKS[wk]
        m = compute_metrics(weeks[wk])
        stops = sum(1 for c in weeks[wk] if c["exit_type"] == "HARD_STOP")
        print(f"  {wk:<10} {cat:<8} {m['n']:>5} {m['wr']:>4.0%} {m['sr']:>4.0%} ${m['er']:>7.2f} {stops:>5}")

    # Per-cycle bar count distribution
    bars_per_cycle = defaultdict(int)
    for r in test_bars:
        bars_per_cycle[r["cycle_id"]] += 1
    bpc = sorted(bars_per_cycle.values())
    if bpc:
        print(f"\n  Agg bars per cycle: min={bpc[0]}, median={bpc[len(bpc)//2]}, "
              f"max={bpc[-1]}, total={sum(bpc)}")

    # ===================================================================
    # Save per-cycle summary (tagged cycles)
    # ===================================================================
    out_cycles = OUTPUT_DIR / "trade-mgmt-tagged-cycles.csv"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    cycle_fields = [
        "cycle_id", "week", "category", "seed_dt", "exit_dt", "direction",
        "seed_price", "exit_price", "exit_type", "depth", "max_position",
        "pnl_ticks", "pnl_dollars", "bars_held", "mfe_ticks", "mae_ticks",
        "agg_bars_held",
    ]

    with open(out_cycles, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cycle_fields)
        w.writeheader()
        for c in test_cycles:
            cid = c["cycle_id"]
            n_agg = bars_per_cycle.get(cid, 0)
            wk = cycle_week(c)
            comm = COMMISSION_PER_RT_MINI * max(c.get("max_position", 1), 1)
            w.writerow({
                "cycle_id": cid, "week": wk, "category": TEST_WEEKS.get(wk, ""),
                "seed_dt": c["seed_dt"], "exit_dt": c["exit_dt"],
                "direction": c["direction"],
                "seed_price": c["seed_price"], "exit_price": c["exit_price"],
                "exit_type": c["exit_type"], "depth": c["depth"],
                "max_position": c["max_position"],
                "pnl_ticks": c["pnl_ticks"],
                "pnl_dollars": c["pnl_ticks"] * 5.0 - comm,
                "bars_held": c["bars_held"],
                "mfe_ticks": c["mfe_ticks"], "mae_ticks": c["mae_ticks"],
                "agg_bars_held": n_agg,
            })
    print(f"\n  Saved per-cycle: {out_cycles}")

    # ===================================================================
    # Save per-agg-bar snapshots (in-trade bars)
    # ===================================================================
    out_bars = OUTPUT_DIR / "trade-mgmt-intrade-bars.csv"
    bar_fields = [
        "cycle_id", "agg_bar_offset", "agg_idx", "tick_bar_offset",
        "price", "direction", "pnl_ticks", "mfe_ticks", "mae_ticks",
        "signed_chop", "dchop", "d2chop", "signed_slope",
        "dr2", "dslope", "r2", "choppiness", "slope_abs",
        "agg_bar_range",
    ]

    with open(out_bars, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=bar_fields)
        w.writeheader()
        for r in test_bars:
            row = {}
            for k in bar_fields:
                v = r[k]
                if v is None:
                    row[k] = ""
                elif isinstance(v, float):
                    row[k] = f"{v:.6f}" if abs(v) < 100 else f"{v:.2f}"
                else:
                    row[k] = v
            w.writerow(row)
    print(f"  Saved per-bar: {out_bars}")

    total = time.time() - t0
    print(f"\nTotal runtime: {total:.0f}s ({total/60:.1f}m)")


if __name__ == "__main__":
    main()
