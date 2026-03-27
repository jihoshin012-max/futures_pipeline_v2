# archetype: rotational
"""Part 4: Time-of-day structure analysis (RTH only)."""
import numpy as np
import pandas as pd
try:
    from .zigzag import child_walk_completion, RTH_START, BLOCK_LABELS_30, BLOCK_LABELS_60
except ImportError:
    from zigzag import child_walk_completion, RTH_START, BLOCK_LABELS_30, BLOCK_LABELS_60


PART4_PAIRS = [(15, 7), (25, 10), (50, 25)]


def analyze(results):
    """Time-of-day structure using child-walk, RTH only.

    Returns:
        df30: DataFrame with 30-min block metrics
        df60: DataFrame with 60-min block metrics
    """
    rows_30 = []
    rows_60 = []

    for pt, ct in PART4_PAIRS:
        key = ('RTH', ct)
        if key not in results:
            continue
        cd_ = results[key]
        succ, retc, fav, anch, gross, ts = child_walk_completion(
            cd_['price'], cd_['dir'], cd_['sid'],
            cd_['time_secs'], float(pt),
        )
        blocks_30 = np.minimum(((ts - RTH_START) / 1800).astype(np.int32), 12)

        # Median child swing size per block
        child_sizes = np.abs(np.diff(cd_['price']))
        child_same_sess = np.diff(cd_['sid']) == 0
        child_sizes = child_sizes[child_same_sess]
        child_ts_mid = cd_['time_secs'][:-1][child_same_sess]
        child_blocks = np.minimum(((child_ts_mid - RTH_START) / 1800).astype(np.int32), 12)

        for b30 in range(13):
            bmask = blocks_30 == b30
            n_samp = int(bmask.sum())
            cb_mask = child_blocks == b30
            med_child = float(np.median(child_sizes[cb_mask])) if cb_mask.any() else 0.0

            if n_samp < 30:
                rows_30.append({
                    'block_start': BLOCK_LABELS_30[b30], 'parent_threshold': pt,
                    'sample_count': n_samp,
                    'completion_0_retrace': 'INSUFFICIENT_SAMPLE',
                    'completion_1_retrace': 'INSUFFICIENT_SAMPLE',
                    'waste_pct': 'INSUFFICIENT_SAMPLE',
                    'median_child_swing': med_child,
                })
                continue

            m0 = bmask & (retc == 0)
            c0 = float(succ[m0].sum() / m0.sum()) if m0.any() else 0.0
            m1 = bmask & (retc == 1)
            c1 = float(succ[m1].sum() / m1.sum()) if m1.any() else 0.0
            ms = bmask & succ
            if ms.any():
                g = gross[ms]
                wpct = float(np.median((g - pt) / g * 100))
            else:
                wpct = 0.0

            rows_30.append({
                'block_start': BLOCK_LABELS_30[b30], 'parent_threshold': pt,
                'sample_count': n_samp,
                'completion_0_retrace': round(c0 * 100, 1),
                'completion_1_retrace': round(c1 * 100, 1),
                'waste_pct': round(wpct, 1),
                'median_child_swing': round(med_child, 2),
            })

        # 60-min aggregation
        blocks_60 = blocks_30 // 2
        child_blocks_60 = child_blocks // 2
        for b60 in range(7):
            bmask = blocks_60 == b60
            n_samp = int(bmask.sum())
            cb_mask = child_blocks_60 == b60
            med_child = float(np.median(child_sizes[cb_mask])) if cb_mask.any() else 0.0

            if n_samp < 30:
                rows_60.append({
                    'block_start': BLOCK_LABELS_60[b60], 'parent_threshold': pt,
                    'sample_count': n_samp,
                    'completion_0_retrace': 'INSUFFICIENT_SAMPLE',
                    'completion_1_retrace': 'INSUFFICIENT_SAMPLE',
                    'waste_pct': 'INSUFFICIENT_SAMPLE',
                    'median_child_swing': med_child,
                })
                continue

            m0 = bmask & (retc == 0)
            c0 = float(succ[m0].sum() / m0.sum()) if m0.any() else 0.0
            m1 = bmask & (retc == 1)
            c1 = float(succ[m1].sum() / m1.sum()) if m1.any() else 0.0
            ms = bmask & succ
            wpct = float(np.median((gross[ms] - pt) / gross[ms] * 100)) if ms.any() else 0.0

            rows_60.append({
                'block_start': BLOCK_LABELS_60[b60], 'parent_threshold': pt,
                'sample_count': n_samp,
                'completion_0_retrace': round(c0 * 100, 1),
                'completion_1_retrace': round(c1 * 100, 1),
                'waste_pct': round(wpct, 1),
                'median_child_swing': round(med_child, 2),
            })

    return pd.DataFrame(rows_30), pd.DataFrame(rows_60)


def to_baseline_dict(df30):
    """Extract fact5: time stability spread per parent scale."""
    fact5 = {'rth_completion_1retrace_spread_pp': {}}
    for pt in [15, 25, 50]:
        sub = df30[(df30['parent_threshold'] == pt)]
        vals = []
        for _, row in sub.iterrows():
            v = row['completion_1_retrace']
            if isinstance(v, (int, float)):
                vals.append(v)
        if vals:
            spread = max(vals) - min(vals)
            fact5['rth_completion_1retrace_spread_pp'][f'parent_{pt}'] = round(spread, 1)
    return fact5
