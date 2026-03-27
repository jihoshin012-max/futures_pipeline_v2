# archetype: rotational
"""Part 3: Power law tail analysis."""
import numpy as np
from scipy import stats as sp_stats


def analyze(all_sizes, splits, thresholds):
    """Fit power law to right tail of each threshold distribution.

    Returns:
        pl_rows: list of dicts with exponent, r_squared, n_tail
    """
    pl_rows = []
    for split in splits:
        for th in thresholds:
            key = (split, th)
            if key not in all_sizes:
                pl_rows.append({'split': split, 'threshold': th,
                                'exponent': 0, 'r_squared': 0, 'n_tail': 0})
                continue
            sz = all_sizes[key]
            if len(sz) < 20:
                pl_rows.append({'split': split, 'threshold': th,
                                'exponent': 0, 'r_squared': 0, 'n_tail': 0})
                continue
            med = np.median(sz)
            tail = sz[sz > med]
            if len(tail) < 10:
                pl_rows.append({'split': split, 'threshold': th,
                                'exponent': 0, 'r_squared': 0, 'n_tail': len(tail)})
                continue
            bins = np.logspace(np.log10(med), np.log10(tail.max()), 40)
            counts, edges = np.histogram(tail, bins=bins)
            centers = np.sqrt(edges[:-1] * edges[1:])
            mask = counts > 0
            if mask.sum() < 3:
                pl_rows.append({'split': split, 'threshold': th,
                                'exponent': 0, 'r_squared': 0, 'n_tail': len(tail)})
                continue
            log_x = np.log10(centers[mask])
            log_y = np.log10(counts[mask].astype(float))
            slope, intercept, r, p, se = sp_stats.linregress(log_x, log_y)
            pl_rows.append({
                'split': split, 'threshold': th,
                'exponent': round(float(slope), 3),
                'r_squared': round(float(r**2), 3),
                'n_tail': int(len(tail)),
            })
    return pl_rows


def to_baseline_dict(pl_rows):
    """Extract baseline-format power law data."""
    out = {}
    for r in pl_rows:
        key = r['split'].lower()
        if key not in out:
            out[key] = {'thresholds': [], 'exponents': [], 'r_squared': []}
        out[key]['thresholds'].append(r['threshold'])
        out[key]['exponents'].append(r['exponent'])
        out[key]['r_squared'].append(r['r_squared'])
    return out
