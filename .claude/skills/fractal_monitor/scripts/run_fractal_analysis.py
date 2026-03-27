#!/usr/bin/env python3
# archetype: rotational
"""
run_fractal_analysis.py — Main entry point for NQ fractal structure analysis.

Usage:
    python run_fractal_analysis.py \
        --data-path "stages/01-data/data/bar_data/tick" \
        --date-range "2026-03-14 to 2026-06-13" \
        --session RTH \
        --baseline baseline/baseline_2025Q4_2026Q1.json \
        --output output/fractal_quarterly_2026_Q2/

    python run_fractal_analysis.py \
        --data-path "..." \
        --parts 1,2 \
        --output output/
"""
import argparse
import json
import sys
import time
from pathlib import Path

# Add the scripts directory to path for relative imports
SCRIPT_DIR = Path(__file__).parent
SKILL_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

# Direct imports (not relative — this is a CLI entry point)
from zigzag import (
    load_tick_data, run_all_zigzags, warmup_numba,
    parse_date_range, THRESHOLDS, SPLITS, PARENT_CHILD,
)
import part1_distributions as p1
import part2_decomposition as p2
import part3_powerlaw as p3
import part4_timeofday as p4
import compare_baseline as cb
import generate_report as gr


def parse_args():
    parser = argparse.ArgumentParser(description='NQ Fractal Structure Analysis')
    parser.add_argument('--data-path', required=True,
                       help='Directory containing NQ_BarData_1tick_*.csv files')
    parser.add_argument('--date-range', default=None,
                       help='Date range: "YYYY-MM-DD to YYYY-MM-DD"')
    parser.add_argument('--session', default='all',
                       help='Session split: RTH, ETH, Combined, or all (default: all)')
    parser.add_argument('--baseline', default=None,
                       help='Path to baseline JSON for drift comparison')
    parser.add_argument('--prev-baseline', default=None,
                       help='Path to previous quarter baseline for short-term comparison')
    parser.add_argument('--output', required=True,
                       help='Output directory for results')
    parser.add_argument('--parts', default='1,2,3,4',
                       help='Comma-separated list of parts to run (default: 1,2,3,4)')
    parser.add_argument('--save-baseline', action='store_true',
                       help='Save current results as a new baseline JSON')
    return parser.parse_args()


def main():
    args = parse_args()
    t0 = time.time()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    parts = [int(p.strip()) for p in args.parts.split(',')]
    if args.session == 'all':
        splits = SPLITS
    else:
        splits = [args.session]

    # Parse date range
    date_start, date_end = parse_date_range(args.date_range)
    date_range_str = args.date_range or 'full dataset'

    # Warmup numba
    print("=== Numba warmup ===", flush=True)
    warmup_numba()

    # Load data
    print("\n=== Loading data ===", flush=True)
    prices, time_secs, cal_dates = load_tick_data(
        args.data_path, date_start, date_end)

    # Run zig-zags
    print("\n=== Running zig-zag passes ===", flush=True)
    results = run_all_zigzags(prices, time_secs, cal_dates, splits, THRESHOLDS)
    del prices, time_secs, cal_dates  # free memory

    # Run analysis parts
    analysis_results = {}
    all_sizes = {}

    if 1 in parts:
        print("\n=== Part 1: Distributions ===", flush=True)
        part1_stats, all_sizes = p1.analyze(results, splits, THRESHOLDS)
        analysis_results['fact1_self_similarity'] = p1.to_baseline_dict(part1_stats, THRESHOLDS)

    if 2 in parts:
        print("\n=== Part 2: Decomposition ===", flush=True)
        overlay_rows, completion_data, halfblock_data = p2.analyze(results, splits)
        fact2, fact3, fact4, fact6 = p2.to_baseline_dict(
            completion_data, halfblock_data, overlay_rows)
        analysis_results['fact2_completion_degradation'] = fact2
        analysis_results['fact3_parent_child_ratio'] = fact3
        analysis_results['fact4_waste'] = fact4
        analysis_results['fact6_halfblock_curve'] = fact6

    if 3 in parts and all_sizes:
        print("\n=== Part 3: Power Law ===", flush=True)
        pl_rows = p3.analyze(all_sizes, splits, THRESHOLDS)
        analysis_results['powerlaw'] = p3.to_baseline_dict(pl_rows)

    if 4 in parts and 'RTH' in splits:
        print("\n=== Part 4: Time of Day ===", flush=True)
        df30, df60 = p4.analyze(results)
        analysis_results['fact5_time_stability'] = p4.to_baseline_dict(df30)
        # Save CSVs
        df30.to_csv(output_dir / 'part4_timeofday_30min.csv', index=False)
        df60.to_csv(output_dir / 'part4_timeofday_60min.csv', index=False)

    # Compare against baseline
    verdicts = None
    overall = None
    if args.baseline and Path(args.baseline).exists():
        print("\n=== Drift Detection ===", flush=True)
        verdicts, overall = cb.compare(analysis_results, args.baseline)
        print(f"  Overall verdict: {overall}")
        for k, v in verdicts.items():
            print(f"    {k}: {v['verdict']} — {v['detail']}")

        # Also compare against previous quarter if provided
        if args.prev_baseline and Path(args.prev_baseline).exists():
            print("\n=== Short-term Drift (vs previous quarter) ===", flush=True)
            prev_verdicts, prev_overall = cb.compare(analysis_results, args.prev_baseline)
            print(f"  vs previous quarter: {prev_overall}")

    # Generate report
    print("\n=== Generating Report ===", flush=True)
    gr.generate(
        analysis_results, verdicts, overall, output_dir,
        date_range=date_range_str,
        baseline_path=args.baseline,
    )

    # Optionally save as new baseline
    if args.save_baseline:
        bl_path = output_dir / 'baseline_new.json'
        baseline_out = {
            'metadata': {
                'date_range': date_range_str,
                'created': __import__('datetime').date.today().isoformat(),
                'total_rows': 0,  # Would need to track this
                'data_source': 'NQ_BarData_1tick_rot',
            },
            **analysis_results,
        }
        with open(bl_path, 'w', encoding='utf-8') as f:
            json.dump(baseline_out, f, indent=2, default=str)
        print(f"\n  Baseline saved: {bl_path}")

    print(f"\n=== Done in {time.time()-t0:.1f}s ===", flush=True)


if __name__ == '__main__':
    main()
