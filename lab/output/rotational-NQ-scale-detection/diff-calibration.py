"""Diff C++ test-mode output against Python calibration reference.

Usage: python diff-calibration.py <path-to-ATEAM_LP_TEST_cycles.csv>

Compares cycle count, timestamps, directions, PnL, and depths.
"""
import csv
import sys

def load(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(f))

ref_path = r"c:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection\calibration-chop-filtered-python.csv"
cpp_path = sys.argv[1] if len(sys.argv) > 1 else r"c:\Projects\futures_pipeline\lab\output\rotational-NQ-scale-detection\ATEAM_LP_TEST_cycles.csv"

ref = load(ref_path)
cpp = load(cpp_path)

print(f"Python cycles: {len(ref)}")
print(f"C++    cycles: {len(cpp)}")

if len(ref) != len(cpp):
    print("MISMATCH: cycle count differs")

# Key fields to compare
fields = ["seed_dt", "direction", "seed_price", "exit_dt", "exit_type", "depth", "pnl_ticks"]
n = min(len(ref), len(cpp))
mismatches = 0

for i in range(n):
    diffs = []
    for f in fields:
        rv = ref[i].get(f, "").strip()
        cv = cpp[i].get(f, "").strip()
        if rv != cv:
            diffs.append(f"  {f}: py={rv}  cpp={cv}")
    if diffs:
        mismatches += 1
        print(f"\nCycle {i}:")
        for d in diffs:
            print(d)

if mismatches == 0 and len(ref) == len(cpp):
    # Verify total PnL ticks
    py_total = sum(float(r["pnl_ticks"]) for r in ref)
    cpp_total = sum(float(r["pnl_ticks"]) for r in cpp)
    print(f"\nTotal PnL ticks: py={py_total:.0f}  cpp={cpp_total:.0f}")
    print("PASS: all cycles match")
else:
    print(f"\n{mismatches} cycle(s) differ out of {n} compared")

# Extra cycles in longer list
if len(cpp) > len(ref):
    print(f"\nExtra C++ cycles ({len(cpp) - len(ref)}):")
    for i in range(len(ref), len(cpp)):
        print(f"  {i}: seed_dt={cpp[i].get('seed_dt','')} dir={cpp[i].get('direction','')} pnl={cpp[i].get('pnl_ticks','')}")
elif len(ref) > len(cpp):
    print(f"\nMissing from C++ ({len(ref) - len(cpp)}):")
    for i in range(len(cpp), len(ref)):
        print(f"  {i}: seed_dt={ref[i].get('seed_dt','')} dir={ref[i].get('direction','')} pnl={ref[i].get('pnl_ticks','')}")
