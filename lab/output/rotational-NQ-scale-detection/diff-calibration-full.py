"""Diff C++ ATEAM_ROTATION_V3_FULL test-mode output against Python calibration reference.

Usage: python diff-calibration-full.py [cpp-cycles-path]

Compares cycle count, timestamps, directions, PnL, exit types, and hold counts.
Reports per-cycle deviations and aggregate statistics.

Expected deviations:
  - Entry prices may differ by 1-3 ticks due to partial-bar timing
    (C++ evaluates on potentially incomplete bars, Python on completed bars)
  - Hold mechanic amplifies timing differences: a single different hold decision
    cascades into different trade durations and subsequent entries
  - Cycle counts may differ if timing differences cause gate pass/fail at boundaries
"""
import csv
import sys
from pathlib import Path

REF_DIR = Path(r"C:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection")
CPP_DIR = Path(r"C:\Projects\futures_pipeline\data")

ref_path = REF_DIR / "calibration-full-python.csv"
cpp_path = sys.argv[1] if len(sys.argv) > 1 else str(CPP_DIR / "ATEAM_LP_TEST_cycles.csv")


def load(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


ref = load(ref_path)
cpp = load(cpp_path)

print(f"Python cycles: {len(ref)}")
print(f"C++    cycles: {len(cpp)}")
print()

# --- Aggregate stats ---
def stats(cycles, label):
    if not cycles:
        print(f"  {label}: (empty)")
        return
    total_pnl = sum(float(c.get("pnl_ticks", 0)) for c in cycles)
    wins = sum(1 for c in cycles if float(c.get("pnl_ticks", 0)) >= 0)
    losses = len(cycles) - wins
    holds = sum(int(c.get("hold_count", 0)) for c in cycles)
    d2_exits = sum(1 for c in cycles if c.get("exit_type", "") == "D2_EXIT")
    reversals = sum(1 for c in cycles if c.get("exit_type", "") == "REVERSAL")
    hard_stops = sum(1 for c in cycles if c.get("exit_type", "") == "HARD_STOP")
    eod = sum(1 for c in cycles if c.get("exit_type", "") == "EOD_FLATTEN")
    print(f"  {label}: {len(cycles)} cycles ({wins}W/{losses}L), PnL={total_pnl:.1f}t, "
          f"holds={holds}, D2={d2_exits}, REV={reversals}, STOP={hard_stops}, EOD={eod}")

stats(ref, "Python")
stats(cpp, "C++   ")
print()

# --- Per-cycle comparison ---
key_fields = ["seed_dt", "direction", "exit_type", "depth"]
numeric_fields = ["seed_price", "exit_price", "pnl_ticks", "hold_count"]

n = min(len(ref), len(cpp))
mismatches = 0
pnl_diffs = []

for i in range(n):
    diffs = []
    for f in key_fields:
        rv = ref[i].get(f, "").strip()
        cv = cpp[i].get(f, "").strip()
        if rv != cv:
            diffs.append(f"  {f}: py={rv}  cpp={cv}")

    for f in numeric_fields:
        rv = float(ref[i].get(f, 0))
        cv = float(cpp[i].get(f, 0))
        if abs(rv - cv) > 0.01:
            diffs.append(f"  {f}: py={rv:.2f}  cpp={cv:.2f}  (delta={cv-rv:+.2f})")
            if f == "pnl_ticks":
                pnl_diffs.append(cv - rv)

    if diffs:
        mismatches += 1
        if mismatches <= 20:  # limit output
            print(f"Cycle {i}:")
            for d in diffs:
                print(d)

if mismatches > 20:
    print(f"  ... ({mismatches - 20} more mismatches)")

# --- Summary ---
print()
if mismatches == 0 and len(ref) == len(cpp):
    py_total = sum(float(r["pnl_ticks"]) for r in ref)
    cpp_total = sum(float(r["pnl_ticks"]) for r in cpp)
    print(f"Total PnL: py={py_total:.1f}t  cpp={cpp_total:.1f}t")
    print("PASS: all cycles match")
elif mismatches == 0 and len(ref) != len(cpp):
    print(f"PARTIAL: first {n} cycles match but count differs "
          f"(py={len(ref)}, cpp={len(cpp)})")
else:
    print(f"{mismatches} cycle(s) differ out of {n} compared")
    if pnl_diffs:
        print(f"PnL deviation: mean={sum(pnl_diffs)/len(pnl_diffs):+.2f}t, "
              f"max={max(pnl_diffs, key=abs):+.2f}t, "
              f"sum={sum(pnl_diffs):+.2f}t")

    # Check if deviations are timing-related (same direction/exit_type, small price diff)
    timing_only = 0
    for i in range(n):
        rd = ref[i].get("direction", "").strip()
        cd = cpp[i].get("direction", "").strip()
        re = ref[i].get("exit_type", "").strip()
        ce = cpp[i].get("exit_type", "").strip()
        if rd == cd and re == ce:
            sp_diff = abs(float(ref[i].get("seed_price", 0)) - float(cpp[i].get("seed_price", 0)))
            if sp_diff <= 1.0:  # within 4 ticks
                timing_only += 1

    if timing_only > 0:
        print(f"\nTiming-only deviations (same dir+exit, seed_price within 1pt): "
              f"{timing_only}/{mismatches}")

# Extra/missing cycles
if len(cpp) > len(ref):
    print(f"\nExtra C++ cycles ({len(cpp) - len(ref)}):")
    for i in range(len(ref), min(len(cpp), len(ref) + 5)):
        c = cpp[i]
        print(f"  {i}: seed={c.get('seed_dt','')} dir={c.get('direction','')} "
              f"exit={c.get('exit_type','')} pnl={c.get('pnl_ticks','')}")
elif len(ref) > len(cpp):
    print(f"\nMissing from C++ ({len(ref) - len(cpp)}):")
    for i in range(len(cpp), min(len(ref), len(cpp) + 5)):
        c = ref[i]
        print(f"  {i}: seed={c.get('seed_dt','')} dir={c.get('direction','')} "
              f"exit={c.get('exit_type','')} pnl={c.get('pnl_ticks','')}")
