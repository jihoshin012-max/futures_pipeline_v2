# archetype: rotational
"""
compare_baseline.py — Drift detection against stored baseline.

Compares new quarterly results against the stored baseline JSON.
Each structural fact gets a STABLE/DRIFT/BREAK verdict.

Fact 2 (completion degradation) is the highest-priority indicator.
"""
import json
from pathlib import Path


STABLE = 'STABLE'
DRIFT = 'DRIFT'
BREAK = 'BREAK'


def load_baseline(path):
    """Load baseline JSON file."""
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def _verdict(label, msg):
    return {'verdict': label, 'detail': msg}


def check_fact1(current, baseline):
    """Fact 1: Self-Similarity.
    STABLE: median/threshold ratio within +/-0.10 of baseline at each scale
    DRIFT: ratio shifts by 0.10-0.20 at 3+ thresholds
    BREAK: ratio shifts by >0.20 at any threshold, OR skewness changes by >0.5
    """
    b = baseline.get('rth', {})
    c = current.get('rth', {})
    if not b or not c:
        return _verdict(STABLE, 'No RTH data to compare')

    b_ratios = b.get('median_p90_ratio', [])
    c_ratios = c.get('median_p90_ratio', [])
    b_skew = b.get('skewness', [])
    c_skew = c.get('skewness', [])

    n = min(len(b_ratios), len(c_ratios))
    if n == 0:
        return _verdict(STABLE, 'No ratio data')

    drifts = []
    for i in range(n):
        diff = abs(c_ratios[i] - b_ratios[i])
        if diff > 0.20:
            return _verdict(BREAK,
                f'Median/P90 ratio shifted by {diff:.3f} at threshold index {i} (>{0.20})')
        if diff > 0.10:
            drifts.append(i)

    ns = min(len(b_skew), len(c_skew))
    for i in range(ns):
        diff = abs(c_skew[i] - b_skew[i])
        if diff > 0.5:
            return _verdict(BREAK, f'Skewness shifted by {diff:.2f} at threshold index {i} (>0.5)')

    if len(drifts) >= 3:
        return _verdict(DRIFT,
            f'Median/P90 ratio shifted by >0.10 at {len(drifts)} thresholds: indices {drifts}')

    return _verdict(STABLE, f'All ratios within tolerance (max drift at {len(drifts)} thresholds)')


def check_fact2(current, baseline):
    """Fact 2: Completion Degradation (HIGHEST PRIORITY).
    STABLE: completion rate at 1 retracement within +/-5pp of baseline for all pairs
    DRIFT: 5-10pp shift for any pair
    BREAK: >10pp shift for any pair, OR 50% crossover moves by >=2 levels
    """
    results = []
    for session in ['rth', 'eth', 'combined']:
        b_sess = baseline.get(session, {})
        c_sess = current.get(session, {})
        for pair in b_sess:
            if pair not in c_sess:
                continue
            b_r1 = b_sess[pair].get('retracement_1', 0)
            c_r1 = c_sess[pair].get('retracement_1', 0)
            diff = c_r1 - b_r1
            abs_diff = abs(diff)

            if abs_diff > 10:
                return _verdict(BREAK,
                    f'{session.upper()} {pair}: completion@1ret shifted {diff:+.1f}pp '
                    f'({b_r1:.1f}% -> {c_r1:.1f}%), exceeds 10pp threshold')
            if abs_diff > 5:
                results.append(f'{session.upper()} {pair}: {diff:+.1f}pp '
                              f'({b_r1:.1f}% -> {c_r1:.1f}%)')

            # Check 50% crossover shift
            b_vals = [b_sess[pair].get(f'retracement_{i}', 100) for i in range(6)]
            c_vals = [c_sess[pair].get(f'retracement_{i}', 100) for i in range(6)]
            b_cross = next((i for i, v in enumerate(b_vals) if v < 50), 5)
            c_cross = next((i for i, v in enumerate(c_vals) if v < 50), 5)
            if abs(c_cross - b_cross) >= 2:
                return _verdict(BREAK,
                    f'{session.upper()} {pair}: 50% crossover moved from '
                    f'retracement {b_cross} to {c_cross} (>=2 levels)')

    if results:
        return _verdict(DRIFT, '; '.join(results))
    return _verdict(STABLE, 'All completion rates within 5pp of baseline')


def check_fact3(current, baseline):
    """Fact 3: Parent/Child Ratio.
    STABLE: best-performing ratio stays within same pair (currently 25->10)
    DRIFT: a different pair takes the lead by >3pp
    BREAK: no pair shows completion >70% at 1 retracement
    """
    b = baseline.get('rth', {})
    c = current.get('rth', {})
    if not b or not c:
        return _verdict(STABLE, 'No RTH data')

    b_pairs = b.get('pairs', [])
    b_comp = b.get('completion_at_1_retrace', [])
    c_pairs = c.get('pairs', [])
    c_comp = c.get('completion_at_1_retrace', [])

    if not c_comp:
        return _verdict(STABLE, 'No completion data')

    max_c = max(c_comp)
    if max_c < 70:
        return _verdict(BREAK, f'No pair has completion >70% at 1 retracement (best: {max_c:.1f}%)')

    b_best_idx = b_comp.index(max(b_comp)) if b_comp else 0
    c_best_idx = c_comp.index(max_c)
    b_best_pair = b_pairs[b_best_idx] if b_best_idx < len(b_pairs) else ''
    c_best_pair = c_pairs[c_best_idx] if c_best_idx < len(c_pairs) else ''

    if b_best_pair != c_best_pair:
        # Check if lead is >3pp
        if b_best_pair in c_pairs:
            old_best_in_new = c_comp[c_pairs.index(b_best_pair)]
            lead = max_c - old_best_in_new
            if lead > 3:
                return _verdict(DRIFT,
                    f'Best pair shifted from {b_best_pair} to {c_best_pair} '
                    f'(lead: {lead:.1f}pp)')

    return _verdict(STABLE, f'Best pair: {c_best_pair} at {max_c:.1f}%')


def check_fact4(current, baseline):
    """Fact 4: Waste %.
    STABLE: within +/-5pp
    DRIFT: 5-10pp shift
    BREAK: >10pp shift
    """
    results = []
    for session in ['rth', 'eth', 'combined']:
        b = baseline.get(session, {})
        c = current.get(session, {})
        b_pairs = b.get('pairs', [])
        b_waste = b.get('waste_pct', [])
        c_pairs = c.get('pairs', [])
        c_waste = c.get('waste_pct', [])

        for i, bp in enumerate(b_pairs):
            if bp in c_pairs:
                ci = c_pairs.index(bp)
                diff = abs(c_waste[ci] - b_waste[i])
                if diff > 10:
                    return _verdict(BREAK,
                        f'{session.upper()} {bp}: waste shifted {diff:.1f}pp (>10pp)')
                if diff > 5:
                    results.append(f'{session.upper()} {bp}: {diff:.1f}pp')

    if results:
        return _verdict(DRIFT, '; '.join(results))
    return _verdict(STABLE, 'All waste% within 5pp')


def check_fact5(current, baseline):
    """Fact 5: Time Stability.
    STABLE: spread remains within 5pp of baseline
    DRIFT: spread increases by 5-15pp
    BREAK: spread >30pp
    """
    b = baseline.get('rth_completion_1retrace_spread_pp', {})
    c = current.get('rth_completion_1retrace_spread_pp', {})

    results = []
    for k in b:
        if k not in c:
            continue
        c_val = c[k]
        b_val = b[k]
        if c_val > 30:
            return _verdict(BREAK, f'{k}: spread is {c_val:.1f}pp (>30pp, fractal is time-dependent)')
        diff = c_val - b_val
        if diff > 15:
            return _verdict(DRIFT, f'{k}: spread increased by {diff:.1f}pp (>15pp)')
        if diff > 5:
            results.append(f'{k}: +{diff:.1f}pp')

    if results:
        return _verdict(DRIFT, '; '.join(results))
    return _verdict(STABLE, 'Time stability within tolerance')


def check_fact6(current, baseline):
    """Fact 6: Half-Block Curve.
    STABLE: completion at 60% progress within +/-5pp
    DRIFT: 5-10pp shift
    BREAK: >10pp shift, OR curve no longer accelerates past 50%
    """
    b = baseline.get('rth_25_10', {})
    c = current.get('rth_25_10', {})
    if not b or not c:
        return _verdict(STABLE, 'No half-block data')

    b_prog = b.get('progress_pct', [])
    b_comp = b.get('completion_pct', [])
    c_prog = c.get('progress_pct', [])
    c_comp = c.get('completion_pct', [])

    # Check 60% progress point
    b_at_60 = None
    c_at_60 = None
    for i, p in enumerate(b_prog):
        if p == 60:
            b_at_60 = b_comp[i]
    for i, p in enumerate(c_prog):
        if p == 60:
            c_at_60 = c_comp[i]

    if b_at_60 is not None and c_at_60 is not None:
        diff = abs(c_at_60 - b_at_60)
        if diff > 10:
            return _verdict(BREAK,
                f'Completion at 60% progress shifted {diff:.1f}pp '
                f'({b_at_60:.1f}% -> {c_at_60:.1f}%)')
        if diff > 5:
            return _verdict(DRIFT,
                f'Completion at 60% progress shifted {diff:.1f}pp '
                f'({b_at_60:.1f}% -> {c_at_60:.1f}%)')

    # Check curve acceleration past 50%
    if len(c_comp) >= 6:
        pre_50 = c_comp[3] if len(c_comp) > 3 else c_comp[-1]  # 40%
        at_60 = c_comp[5] if len(c_comp) > 5 else c_comp[-1]   # 60%
        at_90 = c_comp[8] if len(c_comp) > 8 else c_comp[-1]   # 90%
        if at_90 - at_60 < at_60 - pre_50:
            return _verdict(DRIFT, 'Curve no longer accelerates past 50% progress')

    return _verdict(STABLE, f'Half-block curve within tolerance')


def compare(current_results, baseline_path):
    """Run all 6 drift checks against baseline.

    Args:
        current_results: dict with keys fact1..fact6
        baseline_path: path to baseline JSON

    Returns:
        verdicts: dict with fact1..fact6 verdicts
        overall: 'ALL_STABLE', 'DRIFT_DETECTED', or 'STRUCTURE_BREAK'
    """
    baseline = load_baseline(baseline_path)

    verdicts = {
        'fact1_self_similarity': check_fact1(
            current_results.get('fact1_self_similarity', {}),
            baseline.get('fact1_self_similarity', {})),
        'fact2_completion_degradation': check_fact2(
            current_results.get('fact2_completion_degradation', {}),
            baseline.get('fact2_completion_degradation', {})),
        'fact3_parent_child_ratio': check_fact3(
            current_results.get('fact3_parent_child_ratio', {}),
            baseline.get('fact3_parent_child_ratio', {})),
        'fact4_waste': check_fact4(
            current_results.get('fact4_waste', {}),
            baseline.get('fact4_waste', {})),
        'fact5_time_stability': check_fact5(
            current_results.get('fact5_time_stability', {}),
            baseline.get('fact5_time_stability', {})),
        'fact6_halfblock_curve': check_fact6(
            current_results.get('fact6_halfblock_curve', {}),
            baseline.get('fact6_halfblock_curve', {})),
    }

    has_break = any(v['verdict'] == BREAK for v in verdicts.values())
    has_drift = any(v['verdict'] == DRIFT for v in verdicts.values())

    if has_break:
        overall = 'STRUCTURE_BREAK'
    elif has_drift:
        overall = 'DRIFT_DETECTED'
    else:
        overall = 'ALL_STABLE'

    return verdicts, overall
