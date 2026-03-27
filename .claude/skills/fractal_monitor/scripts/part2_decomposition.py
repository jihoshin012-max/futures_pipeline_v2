# archetype: rotational
"""Part 2: Hierarchical decomposition — parent-child overlay + child-walk completion."""
import numpy as np
try:
    from .zigzag import child_walk_completion, parent_child_overlay, PARENT_CHILD
except ImportError:
    from zigzag import child_walk_completion, parent_child_overlay, PARENT_CHILD


def analyze(results, splits, parent_child_pairs=None):
    """Compute all Part 2 metrics.

    Returns:
        overlay_rows: list of dicts (items a-c)
        completion_data: {(split, pt, ct): {rc_label: {total, success, rate}}}
        halfblock_data: {(split, pt, ct): [bucket_dicts]}
    """
    if parent_child_pairs is None:
        parent_child_pairs = PARENT_CHILD

    overlay_rows = []
    completion_data = {}
    halfblock_data = {}

    for split in splits:
        for pt, ct in parent_child_pairs:
            pk = (split, pt)
            ck = (split, ct)
            if pk not in results or ck not in results:
                continue
            pd_ = results[pk]
            cd_ = results[ck]

            # Items a-c: parent overlay
            nc, nw, nr, waste, valid = parent_child_overlay(
                pd_['price'], pd_['dir'], pd_['sid'], pd_['orig_idx'],
                cd_['price'], cd_['dir'], cd_['sid'], cd_['orig_idx'],
            )
            v = valid
            row = {
                'split': split, 'parent': pt, 'child': ct,
                'n_parent_swings': int(v.sum()),
                'avg_children': float(np.mean(nc[v])) if v.any() else 0,
                'med_children': float(np.median(nc[v])) if v.any() else 0,
                'avg_with': float(np.mean(nw[v])) if v.any() else 0,
                'avg_retrace': float(np.mean(nr[v])) if v.any() else 0,
                'with_retrace_ratio': (float(np.sum(nw[v])) / max(float(np.sum(nr[v])), 1)),
                'waste_pct': float(np.median(waste[v])) if v.any() else 0,
            }
            overlay_rows.append(row)

            # Items d-e: child-walk completion
            succ, retc, fav, anch, gross, ts = child_walk_completion(
                cd_['price'], cd_['dir'], cd_['sid'],
                cd_['time_secs'], float(pt),
            )
            comp = {}
            for rc in range(6):
                mask = (retc == rc) if rc < 5 else (retc >= 5)
                label = str(rc) if rc < 5 else '5+'
                n_tot = int(mask.sum())
                n_suc = int(succ[mask].sum()) if n_tot > 0 else 0
                comp[label] = {
                    'total': n_tot,
                    'success': n_suc,
                    'rate': float(n_suc / n_tot) if n_tot > 0 else 0.0,
                }
            completion_data[(split, pt, ct)] = comp

            # Half-block curve
            buckets = np.arange(0.1, 1.01, 0.1)
            hb = []
            for b in buckets:
                reached = fav >= b
                n_reached = int(reached.sum())
                n_comp = int(succ[reached].sum()) if n_reached > 0 else 0
                hb.append({
                    'progress_pct': round(float(b * 100), 0),
                    'n_reached': n_reached,
                    'n_completed': n_comp,
                    'completion_rate': float(n_comp / n_reached) if n_reached > 0 else 0,
                })
            halfblock_data[(split, pt, ct)] = hb

    return overlay_rows, completion_data, halfblock_data


def to_baseline_dict(completion_data, halfblock_data, overlay_rows):
    """Extract baseline-format data."""
    # fact2: completion degradation
    fact2 = {}
    for (split, pt, ct), comp in completion_data.items():
        key = split.lower()
        if key not in fact2:
            fact2[key] = {}
        pair_key = f"{pt}_{ct}"
        fact2[key][pair_key] = {
            'retracement_0': round(comp['0']['rate'] * 100, 1),
            'retracement_1': round(comp['1']['rate'] * 100, 1),
            'retracement_2': round(comp['2']['rate'] * 100, 1),
            'retracement_3': round(comp['3']['rate'] * 100, 1),
            'retracement_4': round(comp['4']['rate'] * 100, 1),
            'retracement_5plus': round(comp['5+']['rate'] * 100, 1),
            'sample_counts': [
                comp['0']['total'], comp['1']['total'], comp['2']['total'],
                comp['3']['total'], comp['4']['total'], comp['5+']['total'],
            ],
        }

    # fact3: parent-child ratio + best completion at 1 retrace
    fact3 = {}
    for split in set(s for s, _, _ in completion_data.keys()):
        key = split.lower()
        pairs = []
        ratios = []
        comp_1 = []
        for pt, ct in PARENT_CHILD:
            k = (split, pt, ct)
            if k in completion_data:
                pairs.append(f"{pt}_{ct}")
                ratios.append(round(pt / ct, 2))
                comp_1.append(round(completion_data[k]['1']['rate'] * 100, 1))
        fact3[key] = {'pairs': pairs, 'ratios': ratios, 'completion_at_1_retrace': comp_1}

    # fact4: waste
    fact4 = {}
    for r in overlay_rows:
        key = r['split'].lower()
        if key not in fact4:
            fact4[key] = {'pairs': [], 'waste_pct': []}
        pair_key = f"{r['parent']}_{r['child']}"
        # Skip 25_15 which has 0% waste (median=1 child)
        fact4[key]['pairs'].append(pair_key)
        fact4[key]['waste_pct'].append(round(r['waste_pct'], 1))

    # fact6: halfblock curve for RTH 25->10
    fact6 = {}
    hb_key = ('RTH', 25, 10)
    if hb_key in halfblock_data:
        hb = halfblock_data[hb_key]
        fact6['rth_25_10'] = {
            'progress_pct': [int(h['progress_pct']) for h in hb],
            'completion_pct': [round(h['completion_rate'] * 100, 1) for h in hb],
        }

    return fact2, fact3, fact4, fact6
