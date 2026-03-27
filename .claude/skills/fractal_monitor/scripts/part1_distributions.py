# archetype: rotational
"""Part 1: Multi-threshold swing size distributions."""
import numpy as np
from scipy import stats as sp_stats


def compute_swing_sizes(data):
    """Return swing sizes (consecutive same-session swings)."""
    p = data['price']; s = data['sid']
    sizes = np.abs(np.diff(p))
    same = np.diff(s) == 0
    return sizes[same]


def analyze(results, splits, thresholds):
    """Compute distribution stats for all splits x thresholds.

    Returns:
        all_stats: {split: [row_dict, ...]}
        all_sizes: {(split, th): np.array}
    """
    all_stats = {}
    all_sizes = {}
    for split in splits:
        rows = []
        for th in thresholds:
            key = (split, th)
            if key not in results:
                continue
            sz = compute_swing_sizes(results[key])
            s = {
                'split': split, 'threshold': th,
                'count': int(len(sz)),
                'mean': float(np.mean(sz)) if len(sz) > 0 else 0.0,
                'median': float(np.median(sz)) if len(sz) > 0 else 0.0,
                'p75': float(np.percentile(sz, 75)) if len(sz) > 0 else 0.0,
                'p90': float(np.percentile(sz, 90)) if len(sz) > 0 else 0.0,
                'std': float(np.std(sz)) if len(sz) > 0 else 0.0,
                'skewness': float(sp_stats.skew(sz)) if len(sz) > 2 else 0.0,
            }
            s['median_p90_ratio'] = s['median'] / s['p90'] if s['p90'] > 0 else 0.0
            s['mean_over_threshold'] = s['mean'] / th
            s['median_over_threshold'] = s['median'] / th
            s['p90_over_threshold'] = s['p90'] / th
            rows.append(s)
            all_sizes[key] = sz
        all_stats[split] = rows
    return all_stats, all_sizes


def to_baseline_dict(all_stats, thresholds):
    """Extract baseline-format data from stats."""
    out = {'thresholds': thresholds}
    for split, rows in all_stats.items():
        key = split.lower()
        out[key] = {
            'mean_over_threshold': [round(r['mean_over_threshold'], 3) for r in rows],
            'median_over_threshold': [round(r['median_over_threshold'], 3) for r in rows],
            'p90_over_threshold': [round(r['p90_over_threshold'], 3) for r in rows],
            'skewness': [round(r['skewness'], 2) for r in rows],
            'median_p90_ratio': [round(r['median_p90_ratio'], 3) for r in rows],
        }
    return out
