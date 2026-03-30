# archetype: rotational
"""LP Simulator — Python replication of ATEAM_ROTATION_V3_LP C++ test mode.

Purpose: Exact replication of the C++ batch simulation for calibration gate.
         Must produce identical cycle-level output to ATEAM_LP_TEST_cycles.csv.

Usage:
    python lp_simulator.py [--bar-file PATH] [--step-dist 10] [--hard-stop 60]
                           [--max-levels 2] [--max-contract-size 4] [--max-fades 3]
                           [--initial-qty 1] [--tick-size 0.25]
                           [--output-dir PATH]
"""

from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
#  RTH boundaries (ET)
# ---------------------------------------------------------------------------
RTH_OPEN_SEC = 9 * 3600 + 30 * 60       # 09:30:00
RTH_CLOSE_SEC = 15 * 3600 + 49 * 60 + 50  # 15:49:50


# ---------------------------------------------------------------------------
#  Data structures
# ---------------------------------------------------------------------------
@dataclass
class Bar:
    datetime_str: str
    open: float
    high: float
    low: float
    last: float
    time_sec: int     # seconds since midnight
    date_int: int     # YYYYMMDD


@dataclass
class CycleRecord:
    cycle_id: int = 0
    watch_start_dt: str = ""
    watch_price: float = 0.0
    watch_high: float = 0.0
    watch_low: float = 0.0
    watch_bars: int = 0
    seed_dt: str = ""
    exit_dt: str = ""
    direction: str = ""
    seed_price: float = 0.0
    avg_entry_price: float = 0.0
    exit_price: float = 0.0
    exit_type: str = ""
    depth: int = 0
    max_position: int = 0
    pnl_ticks: float = 0.0
    pnl_dollars: float = 0.0
    bars_held: int = 0
    mfe_ticks: float = 0.0
    mae_ticks: float = 0.0


@dataclass
class EventRecord:
    cycle_id: int = 0
    datetime_str: str = ""
    event: str = ""
    side: str = ""
    price: float = 0.0
    avg_entry_price: float = 0.0
    pos_qty: int = 0
    add_qty: int = 0
    level: int = 0
    pnl_ticks: float = 0.0


# ---------------------------------------------------------------------------
#  Bar loader
# ---------------------------------------------------------------------------
def load_bars(filepath: str | Path) -> list[Bar]:
    """Load bar data from SC-format CSV. Returns list of Bar objects."""
    bars: list[Bar] = []
    with open(filepath, "r") as f:
        reader = csv.reader(f)
        header = next(reader)  # skip header

        for row in reader:
            if len(row) < 6:
                continue
            date_str = row[0].strip()
            time_str = row[1].strip()
            o = float(row[2])
            h = float(row[3])
            l = float(row[4])
            c = float(row[5])

            # Parse time to seconds since midnight
            time_parts = time_str.split(":")
            hr = int(time_parts[0])
            mn = int(time_parts[1])
            sec = int(float(time_parts[2])) if len(time_parts) > 2 else 0
            time_sec = hr * 3600 + mn * 60 + sec

            # Parse date to YYYYMMDD integer
            date_parts = date_str.split("-")
            yr = int(date_parts[0])
            mo = int(date_parts[1])
            dy = int(date_parts[2])
            date_int = yr * 10000 + mo * 100 + dy

            dt_str = f"{yr:04d}-{mo:02d}-{dy:02d} {hr:02d}:{mn:02d}:{sec:02d}"

            bars.append(Bar(
                datetime_str=dt_str,
                open=o, high=h, low=l, last=c,
                time_sec=time_sec, date_int=date_int,
            ))

    return bars


# ---------------------------------------------------------------------------
#  Simulator
# ---------------------------------------------------------------------------
class LPSimulator:
    """Replicates ATEAM_ROTATION_V3_LP C++ test mode logic exactly."""

    def __init__(
        self,
        step_dist: float = 10.0,
        initial_qty: int = 1,
        max_levels: int = 2,
        max_contract_size: int = 4,
        hard_stop: float = 60.0,
        max_fades: int = 3,
        tick_size: float = 0.25,
    ):
        self.step_dist = step_dist
        self.initial_qty = initial_qty
        self.max_levels = max_levels
        self.max_contract_size = max_contract_size
        self.hard_stop = hard_stop
        self.max_fades = max_fades
        self.tick_size = tick_size

        # State
        self.anchor_price: float = 0.0
        self.watch_price: float = 0.0
        self.watch_high: float = 0.0
        self.watch_low: float = 0.0
        self.direction: int = 0       # 1=long, -1=short
        self.level: int = 0
        self.fade_count_long: int = 0
        self.fade_count_short: int = 0
        self.pos_qty: int = 0         # signed position
        self.avg_entry: float = 0.0
        self.total_cost: float = 0.0  # sum(price * qty) for avg calc

        # Per-cycle tracking
        self.cycle_id: int = 0
        self.watch_start_dt: str = ""
        self.watch_start_price: float = 0.0
        self.watch_start_high: float = 0.0
        self.watch_start_low: float = 0.0
        self.watch_start_bar: int = 0
        self.cycle_start_bar: int = 0
        self.cycle_depth: int = 0
        self.cycle_peak_pos: int = 0
        self.cycle_mfe: float = 0.0
        self.cycle_mae: float = 0.0

        # Session tracking
        self.prev_date_int: int = 0
        self.rth_active: bool = False

        # Saved avg entry (captured before flatten)
        self.saved_avg_entry: float = 0.0

        # Output
        self.cycles: list[CycleRecord] = []
        self.events: list[EventRecord] = []

    # -- Helpers --

    def _reset_state(self) -> None:
        self.anchor_price = 0.0
        self.direction = 0
        self.level = 0
        self.watch_price = 0.0
        self.watch_high = 0.0
        self.watch_low = 0.0

    def _fade_blocked(self, d: int) -> bool:
        if self.max_fades <= 0:
            return False
        if d == 1 and self.fade_count_long >= self.max_fades:
            return True
        if d == -1 and self.fade_count_short >= self.max_fades:
            return True
        return False

    def _update_fade_count(self, d: int) -> None:
        if d == 1:
            self.fade_count_long += 1
            self.fade_count_short = 0
        else:
            self.fade_count_short += 1
            self.fade_count_long = 0

    def _add_event(self, bar_idx: int, bars: list[Bar], evt: str, side: str,
                   price: float, avg: float, pq: int, aq: int, lv: int,
                   pnl: float) -> None:
        self.events.append(EventRecord(
            cycle_id=self.cycle_id,
            datetime_str=bars[bar_idx].datetime_str,
            event=evt, side=side, price=price,
            avg_entry_price=avg, pos_qty=pq, add_qty=aq,
            level=lv, pnl_ticks=pnl,
        ))

    def _sim_entry(self, d: int, qty: int, price: float) -> None:
        if self.pos_qty == 0:
            self.pos_qty = d * qty
            self.avg_entry = price
            self.total_cost = price * qty
        else:
            self.total_cost += price * qty
            self.pos_qty += d * qty
            abs_after = abs(self.pos_qty)
            self.avg_entry = self.total_cost / abs_after

    def _sim_flatten(self, price: float) -> float:
        """Flatten position, return P&L in ticks."""
        pnl = 0.0
        if self.pos_qty != 0:
            if self.pos_qty > 0:
                pnl = (price - self.avg_entry) / self.tick_size * abs(self.pos_qty)
            else:
                pnl = (self.avg_entry - price) / self.tick_size * abs(self.pos_qty)
        self.pos_qty = 0
        self.avg_entry = 0.0
        self.total_cost = 0.0
        return pnl

    def _record_cycle(self, bar_idx: int, bars: list[Bar], exit_type: str,
                      pnl_ticks: float) -> None:
        self.cycles.append(CycleRecord(
            cycle_id=self.cycle_id,
            watch_start_dt=self.watch_start_dt,
            watch_price=self.watch_start_price,
            watch_high=self.watch_start_high,
            watch_low=self.watch_start_low,
            watch_bars=(self.cycle_start_bar - self.watch_start_bar
                        if self.cycle_start_bar > self.watch_start_bar else 0),
            seed_dt=bars[self.cycle_start_bar].datetime_str,
            exit_dt=bars[bar_idx].datetime_str,
            direction="LONG" if self.direction == 1 else "SHORT",
            seed_price=bars[self.cycle_start_bar].last,
            avg_entry_price=self.saved_avg_entry,
            exit_price=bars[bar_idx].last,
            exit_type=exit_type,
            depth=self.cycle_depth,
            max_position=self.cycle_peak_pos,
            pnl_ticks=pnl_ticks,
            pnl_dollars=pnl_ticks * 5.0,  # NQ mini: $5/tick
            bars_held=bar_idx - self.cycle_start_bar,
            mfe_ticks=self.cycle_mfe,
            mae_ticks=self.cycle_mae,
        ))
        self.cycle_id += 1

    def _start_new_watch(self, bar_idx: int, bars: list[Bar]) -> None:
        self.watch_start_dt = bars[bar_idx].datetime_str
        self.watch_start_price = bars[bar_idx].last
        self.watch_start_high = bars[bar_idx].last
        self.watch_start_low = bars[bar_idx].last
        self.watch_start_bar = bar_idx
        self.cycle_depth = 0
        self.cycle_peak_pos = 0
        self.cycle_mfe = 0.0
        self.cycle_mae = 0.0

    # -- Main loop --

    def run(self, bars: list[Bar]) -> None:
        """Run simulation over bar data. Results in self.cycles and self.events."""
        n = len(bars)

        for i in range(n):
            price = bars[i].last
            time_sec = bars[i].time_sec
            date_int = bars[i].date_int

            # --- Session boundary detection ---
            if RTH_OPEN_SEC <= time_sec <= RTH_CLOSE_SEC:
                if not self.rth_active:
                    self.rth_active = True
                    # New RTH session — reset
                    if self.pos_qty != 0:
                        self.saved_avg_entry = self.avg_entry
                        pnl = self._sim_flatten(price)
                        side = "LONG" if self.direction == 1 else "SHORT"
                        self._add_event(i, bars, "SESSION_RESET", side,
                                        price, self.saved_avg_entry, 0, 0,
                                        self.level, pnl)
                    self._reset_state()
                    self.fade_count_long = 0
                    self.fade_count_short = 0
                    self._start_new_watch(i, bars)
            else:
                if self.rth_active and time_sec > RTH_CLOSE_SEC:
                    self.rth_active = False
                self.prev_date_int = date_int
                continue

            self.prev_date_int = date_int

            # --- EOD FLATTEN ---
            if time_sec >= RTH_CLOSE_SEC:
                if self.pos_qty != 0:
                    self.saved_avg_entry = self.avg_entry
                    pnl = self._sim_flatten(price)
                    side = "LONG" if self.direction == 1 else "SHORT"
                    self._add_event(i, bars, "EOD_FLATTEN", side,
                                    price, self.saved_avg_entry, 0, 0,
                                    self.level, pnl)
                    self._record_cycle(i, bars, "EOD_FLATTEN", pnl)
                    self._reset_state()
                elif self.watch_price != 0.0:
                    self._reset_state()
                self.rth_active = False
                continue

            # --- Track MFE/MAE if in position ---
            if self.pos_qty != 0:
                if self.pos_qty > 0:
                    hi_exc = (bars[i].high - self.avg_entry) / self.tick_size
                    lo_exc = (bars[i].low - self.avg_entry) / self.tick_size
                else:
                    hi_exc = (self.avg_entry - bars[i].low) / self.tick_size
                    lo_exc = (self.avg_entry - bars[i].high) / self.tick_size

                if hi_exc > self.cycle_mfe:
                    self.cycle_mfe = hi_exc
                if -lo_exc > self.cycle_mae:
                    self.cycle_mae = -lo_exc

                # Also check Last price
                if self.pos_qty > 0:
                    exc = (price - self.avg_entry) / self.tick_size
                else:
                    exc = (self.avg_entry - price) / self.tick_size
                if exc > self.cycle_mfe:
                    self.cycle_mfe = exc
                if -exc > self.cycle_mae:
                    self.cycle_mae = -exc

            # --- HARD STOP CHECK ---
            if self.pos_qty != 0 and self.hard_stop > 0.0:
                if self.pos_qty > 0:
                    unreal_pts = self.avg_entry - price
                else:
                    unreal_pts = price - self.avg_entry
                unreal_ticks = unreal_pts / self.tick_size

                if unreal_ticks >= self.hard_stop:
                    self.saved_avg_entry = self.avg_entry
                    pnl = self._sim_flatten(price)
                    side = "LONG" if self.direction == 1 else "SHORT"
                    self._add_event(i, bars, "HARD_STOP", side,
                                    price, self.saved_avg_entry, 0, 0,
                                    self.level, pnl)
                    self._record_cycle(i, bars, "HARD_STOP", pnl)
                    self._reset_state()
                    self._start_new_watch(i, bars)
                    continue

            # --- WATCHING: flat, looking for seed ---
            if self.pos_qty == 0 and self.anchor_price == 0.0:
                if self.watch_price == 0.0:
                    self.watch_price = price
                    self.watch_high = price
                    self.watch_low = price
                    if not self.watch_start_dt:
                        self._start_new_watch(i, bars)
                    continue

                if price > self.watch_high:
                    self.watch_high = price
                if price < self.watch_low:
                    self.watch_low = price
                if price > self.watch_start_high:
                    self.watch_start_high = price
                if price < self.watch_start_low:
                    self.watch_start_low = price

                pull_from_high = self.watch_high - price
                pull_from_low = price - self.watch_low

                seed_dir = 0
                if pull_from_high >= self.step_dist and pull_from_low >= self.step_dist:
                    seed_dir = 1 if pull_from_high >= pull_from_low else -1
                elif pull_from_high >= self.step_dist:
                    seed_dir = 1
                elif pull_from_low >= self.step_dist:
                    seed_dir = -1
                else:
                    continue

                # Check fade filter
                if self._fade_blocked(seed_dir):
                    seed_dir = -seed_dir
                    other_moved = (
                        (pull_from_high >= self.step_dist) if seed_dir == 1
                        else (pull_from_low >= self.step_dist)
                    )
                    if not other_moved or self._fade_blocked(seed_dir):
                        continue

                # SEED entry
                self._sim_entry(seed_dir, self.initial_qty, price)
                self.direction = seed_dir
                self.level = 0
                self.anchor_price = price
                self.watch_price = 0.0
                self.cycle_start_bar = i
                self.cycle_depth = 0
                self.cycle_peak_pos = abs(self.pos_qty)
                self.cycle_mfe = 0.0
                self.cycle_mae = 0.0
                self._update_fade_count(seed_dir)

                side = "LONG" if seed_dir == 1 else "SHORT"
                self._add_event(i, bars, "SEED", side,
                                price, price, self.pos_qty,
                                self.initial_qty, 0, 0.0)
                continue

            # --- IN POSITION ---
            if self.pos_qty == 0:
                self._reset_state()
                self._start_new_watch(i, bars)
                continue

            up_move = price - self.anchor_price
            down_move = self.anchor_price - price
            in_favor = (up_move >= self.step_dist if self.direction == 1
                        else down_move >= self.step_dist)
            against = (down_move >= self.step_dist if self.direction == 1
                       else up_move >= self.step_dist)

            # REVERSAL
            if in_favor:
                self.saved_avg_entry = self.avg_entry
                pnl = self._sim_flatten(price)
                side = "LONG" if self.direction == 1 else "SHORT"
                self._add_event(i, bars, "REVERSAL", side,
                                price, self.saved_avg_entry, 0, 0,
                                self.level, pnl)
                self._record_cycle(i, bars, "REVERSAL", pnl)

                # Enter opposite
                new_dir = -self.direction
                if self._fade_blocked(new_dir):
                    new_side = "LONG" if new_dir == 1 else "SHORT"
                    self._add_event(i, bars, "FADE_BLOCKED", new_side,
                                    price, 0.0, 0, 0, self.level, 0.0)
                    self._reset_state()
                    self._start_new_watch(i, bars)
                    continue

                self._sim_entry(new_dir, self.initial_qty, price)
                self.direction = new_dir
                self.level = 0
                self.anchor_price = price
                self.cycle_start_bar = i
                self.cycle_depth = 0
                self.cycle_peak_pos = abs(self.pos_qty)
                self.cycle_mfe = 0.0
                self.cycle_mae = 0.0
                self._update_fade_count(new_dir)

                # Update watch tracking for new cycle
                self.watch_start_dt = bars[i].datetime_str
                self.watch_start_price = price
                self.watch_start_high = price
                self.watch_start_low = price
                self.watch_start_bar = i

                new_side = "LONG" if new_dir == 1 else "SHORT"
                self._add_event(i, bars, "REVERSAL_ENTRY", new_side,
                                price, price, self.pos_qty,
                                self.initial_qty, 0, 0.0)
                continue

            # MARTINGALE ADD
            if against:
                use_level = self.level
                if use_level >= self.max_levels:
                    use_level = 0

                add_qty = int(self.initial_qty * (2 ** use_level) + 0.5)
                abs_pos = abs(self.pos_qty)

                if abs_pos + add_qty > self.max_contract_size:
                    room = self.max_contract_size - abs_pos
                    if room <= 0:
                        continue
                    add_qty = room
                    self.level = 0

                self._sim_entry(self.direction, add_qty, price)
                self.level += 1
                if self.level >= self.max_levels:
                    self.level = 0
                self.anchor_price = price
                self.cycle_depth += 1
                if abs(self.pos_qty) > self.cycle_peak_pos:
                    self.cycle_peak_pos = abs(self.pos_qty)

                side = "LONG" if self.direction == 1 else "SHORT"
                self._add_event(i, bars, "ADD", side,
                                price, self.avg_entry, self.pos_qty,
                                add_qty, self.level, 0.0)
                continue

        # --- End of data ---
        if self.pos_qty != 0 and n > 0:
            last_idx = n - 1
            self.saved_avg_entry = self.avg_entry
            pnl = self._sim_flatten(bars[last_idx].last)
            side = "LONG" if self.direction == 1 else "SHORT"
            self._add_event(last_idx, bars, "DATA_END", side,
                            bars[last_idx].last, self.saved_avg_entry, 0, 0,
                            self.level, pnl)
            self._record_cycle(last_idx, bars, "DATA_END", pnl)


# ---------------------------------------------------------------------------
#  CSV writers
# ---------------------------------------------------------------------------
def write_cycles_csv(cycles: list[CycleRecord], filepath: str | Path) -> None:
    with open(filepath, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "cycle_id", "watch_start_dt", "watch_price", "watch_high",
            "watch_low", "watch_bars", "seed_dt", "exit_dt", "direction",
            "seed_price", "avg_entry_price", "exit_price", "exit_type",
            "depth", "max_position", "pnl_ticks", "pnl_dollars",
            "bars_held", "mfe_ticks", "mae_ticks",
        ])
        for c in cycles:
            w.writerow([
                c.cycle_id,
                c.watch_start_dt,
                f"{c.watch_price:.2f}",
                f"{c.watch_high:.2f}",
                f"{c.watch_low:.2f}",
                c.watch_bars,
                c.seed_dt,
                c.exit_dt,
                c.direction,
                f"{c.seed_price:.2f}",
                f"{c.avg_entry_price:.2f}",
                f"{c.exit_price:.2f}",
                c.exit_type,
                c.depth,
                c.max_position,
                f"{c.pnl_ticks:.2f}",
                f"{c.pnl_dollars:.2f}",
                c.bars_held,
                f"{c.mfe_ticks:.2f}",
                f"{c.mae_ticks:.2f}",
            ])


def write_events_csv(events: list[EventRecord], filepath: str | Path) -> None:
    with open(filepath, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "cycle_id", "datetime", "event", "side", "price",
            "avg_entry_price", "pos_qty", "add_qty", "level", "pnl_ticks",
        ])
        for e in events:
            w.writerow([
                e.cycle_id,
                e.datetime_str,
                e.event,
                e.side,
                f"{e.price:.2f}",
                f"{e.avg_entry_price:.2f}",
                e.pos_qty,
                e.add_qty,
                e.level,
                f"{e.pnl_ticks:.2f}",
            ])


# ---------------------------------------------------------------------------
#  Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="LP Simulator — Python replication of C++ test mode")
    parser.add_argument("--bar-file", type=str,
                        default=r"C:\Projects\pipeline\stages\01-data\data\bar_data\tick\NQ_calibration_1day.csv")
    parser.add_argument("--step-dist", type=float, default=10.0)
    parser.add_argument("--initial-qty", type=int, default=1)
    parser.add_argument("--max-levels", type=int, default=2)
    parser.add_argument("--max-contract-size", type=int, default=4)
    parser.add_argument("--hard-stop", type=float, default=60.0)
    parser.add_argument("--max-fades", type=int, default=3)
    parser.add_argument("--tick-size", type=float, default=0.25)
    parser.add_argument("--output-dir", type=str,
                        default=r"C:\Projects\pipeline\stages\01-data\data\bar_data\tick")
    args = parser.parse_args()

    print(f"Loading bars from {args.bar_file}...")
    bars = load_bars(args.bar_file)
    print(f"Loaded {len(bars)} bars")

    sim = LPSimulator(
        step_dist=args.step_dist,
        initial_qty=args.initial_qty,
        max_levels=args.max_levels,
        max_contract_size=args.max_contract_size,
        hard_stop=args.hard_stop,
        max_fades=args.max_fades,
        tick_size=args.tick_size,
    )

    print("Running simulation...")
    sim.run(bars)

    # Summary
    wins = sum(1 for c in sim.cycles if c.pnl_ticks >= 0)
    losses = sum(1 for c in sim.cycles if c.pnl_ticks < 0)
    total_pnl = sum(c.pnl_ticks for c in sim.cycles)
    print(f"Complete. {len(sim.cycles)} cycles ({wins} W / {losses} L), "
          f"net PnL={total_pnl:.1f} ticks, {len(sim.events)} events")

    # Write output
    out_dir = Path(args.output_dir)
    cycles_path = out_dir / "ATEAM_LP_PY_cycles.csv"
    events_path = out_dir / "ATEAM_LP_PY_events.csv"

    write_cycles_csv(sim.cycles, cycles_path)
    write_events_csv(sim.events, events_path)
    print(f"Written: {cycles_path}")
    print(f"Written: {events_path}")


if __name__ == "__main__":
    main()
