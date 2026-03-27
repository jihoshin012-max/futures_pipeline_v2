# Baseline Files

Each baseline JSON captures the six structural facts at a point in time.

## File Naming

`baseline_YYYYQN_YYYYQN.json` — covers the date range from first quarter to second quarter.

## Baseline Fields

### metadata
- `date_range` — Data period covered
- `data_source` — Source CSV pattern
- `total_rows` — Total tick count
- `created` — When this baseline was generated

### fact1_self_similarity
Distribution shape metrics at 7 thresholds (3, 5, 7, 10, 15, 25, 50 points).
Per session (RTH/ETH/Combined): mean/threshold, median/threshold, P90/threshold, skewness, median/P90 ratio.
Stable ratios across thresholds = self-similar fractal structure.

### fact2_completion_degradation
Completion rates by retracement count for all 6 parent-child pairs (50->25, 25->15, 25->10, 15->7, 10->5, 7->3).
Per session. This is the MOST IMPORTANT fact — directly determines martingale structural backing.
A >10pp drop at 1 retracement triggers STRUCTURE_BREAK.

### fact3_parent_child_ratio
Which parent-child pair has the best completion rate at 1 retracement.
Currently 25->10 for RTH. Tracks whether the optimal scale changes.

### fact4_waste
Retracement waste percentage per parent-child pair.
Measures how much child movement is "wasted" on retracements within each parent swing.

### fact5_time_stability
Spread (max - min) of completion rate at 1 retracement across RTH 30-min time blocks.
Low spread = fractal structure is time-independent. High spread = time-dependent.

### fact6_halfblock_curve
Progress-to-completion curve for the 25->10 pair (RTH).
Shows P(completion | reached X% of parent threshold).
The acceleration past 50% is the "safe zone" signal.

## Founding Baseline

`baseline_2025Q4_2026Q1.json` — Generated from the founding fractal discovery analysis
using 60.9M rows of NQ 1-tick data (Sept 21, 2025 to Mar 13, 2026).
All future comparisons reference this as the long-term drift anchor.
