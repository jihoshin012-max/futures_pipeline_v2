# archetype: rotational
"""
generate_report.py — Produce standardized markdown report with drift verdicts.
"""
import json
from pathlib import Path
from datetime import date


def generate(analysis_results, verdicts, overall, output_dir,
             date_range='', baseline_path=None, prev_baseline_path=None):
    """Generate markdown report and JSON results file.

    Args:
        analysis_results: dict with fact1..fact6 structured data + raw stats
        verdicts: dict from compare_baseline (or None if no baseline)
        overall: 'ALL_STABLE'/'DRIFT_DETECTED'/'STRUCTURE_BREAK' (or None)
        output_dir: Path for output files
        date_range: string describing the data range
        baseline_path: path to the baseline compared against
        prev_baseline_path: path to previous quarter baseline (optional)
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    today = date.today().isoformat()
    quarter = f"{date.today().year}_Q{(date.today().month - 1) // 3 + 1}"

    lines = [f'# NQ Fractal Structure Monitor Report — {quarter}\n']
    lines.append(f'Generated: {today}')
    lines.append(f'Data range: {date_range}')
    if baseline_path:
        lines.append(f'Baseline: {Path(baseline_path).name}')
    lines.append('')

    # Overall verdict
    if overall:
        emoji = {'ALL_STABLE': 'OK', 'DRIFT_DETECTED': 'WARNING', 'STRUCTURE_BREAK': 'CRITICAL'}
        lines.append(f'## Overall Verdict: {overall} [{emoji.get(overall, "")}]\n')
    else:
        lines.append('## Standalone Analysis (No Baseline Comparison)\n')

    # Six-fact comparison table
    if verdicts:
        lines.append('## Drift Detection Summary\n')
        lines.append('| Fact | Verdict | Detail |')
        lines.append('|------|---------|--------|')
        for fact_key in ['fact1_self_similarity', 'fact2_completion_degradation',
                         'fact3_parent_child_ratio', 'fact4_waste',
                         'fact5_time_stability', 'fact6_halfblock_curve']:
            v = verdicts.get(fact_key, {'verdict': '?', 'detail': ''})
            label = fact_key.replace('_', ' ').title()
            icon = {'STABLE': 'STABLE', 'DRIFT': 'DRIFT', 'BREAK': '!! BREAK !!'}
            lines.append(f"| {label} | {icon.get(v['verdict'], v['verdict'])} | {v['detail']} |")
        lines.append('')

        # Detailed sections for DRIFT/BREAK
        for fact_key, v in verdicts.items():
            if v['verdict'] in ('DRIFT', 'BREAK'):
                lines.append(f"### {fact_key.replace('_', ' ').title()}: {v['verdict']}\n")
                lines.append(f"{v['detail']}\n")
                if v['verdict'] == 'BREAK' and 'fact2' in fact_key:
                    lines.append('**Action Required:** Re-evaluate strategy parameters. '
                                'The completion degradation curve has shifted beyond safe bounds.\n')
                lines.append('')

    # Part 1 summary
    fact1 = analysis_results.get('fact1_self_similarity', {})
    rth1 = fact1.get('rth', {})
    if rth1:
        lines.append('## Part 1: Self-Similarity (RTH)\n')
        lines.append('| Threshold | Mean/Th | Median/Th | P90/Th | Skewness | Med/P90 |')
        lines.append('|-----------|---------|-----------|--------|----------|---------|')
        ths = fact1.get('thresholds', [])
        for i, th in enumerate(ths):
            m = rth1.get('mean_over_threshold', [])[i] if i < len(rth1.get('mean_over_threshold', [])) else 0
            md = rth1.get('median_over_threshold', [])[i] if i < len(rth1.get('median_over_threshold', [])) else 0
            p = rth1.get('p90_over_threshold', [])[i] if i < len(rth1.get('p90_over_threshold', [])) else 0
            sk = rth1.get('skewness', [])[i] if i < len(rth1.get('skewness', [])) else 0
            mr = rth1.get('median_p90_ratio', [])[i] if i < len(rth1.get('median_p90_ratio', [])) else 0
            lines.append(f'| {th}pt | {m:.3f} | {md:.3f} | {p:.3f} | {sk:.2f} | {mr:.3f} |')
        lines.append('')

    # Part 2 summary — completion rates
    fact2 = analysis_results.get('fact2_completion_degradation', {})
    rth2 = fact2.get('rth', {})
    if rth2:
        lines.append('## Part 2: Completion Rates (RTH)\n')
        lines.append('| Pair | 0 ret | 1 ret | 2 ret | 3 ret | 4 ret | 5+ ret |')
        lines.append('|------|-------|-------|-------|-------|-------|--------|')
        for pair, data in rth2.items():
            vals = [f"{data.get(f'retracement_{i}', 0):.1f}%" for i in range(5)]
            vals.append(f"{data.get('retracement_5plus', 0):.1f}%")
            lines.append(f"| {pair} | {' | '.join(vals)} |")
        lines.append('')

    # Part 3 summary
    fact3_pl = analysis_results.get('powerlaw', {})
    rth_pl = fact3_pl.get('rth', {})
    if rth_pl:
        lines.append('## Part 3: Power Law Exponents (RTH)\n')
        lines.append('| Threshold | Exponent | R-squared |')
        lines.append('|-----------|----------|-----------|')
        for i, th in enumerate(rth_pl.get('thresholds', [])):
            exp = rth_pl.get('exponents', [])[i]
            r2 = rth_pl.get('r_squared', [])[i]
            lines.append(f'| {th}pt | {exp:.3f} | {r2:.3f} |')
        lines.append('')

    lines.append('---')
    lines.append(f'*Report generated by fractal_monitor skill on {today}*\n')

    # Write markdown
    report_name = f'fractal_quarterly_{quarter}'
    md_path = output_dir / f'{report_name}.md'
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    # Write JSON (current results in baseline schema)
    json_path = output_dir / f'{report_name}.json'
    output_json = {
        'metadata': {
            'date_range': date_range,
            'created': today,
            'baseline_compared': str(baseline_path) if baseline_path else None,
            'overall_verdict': overall,
        },
        **analysis_results,
    }
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(output_json, f, indent=2, default=str)

    print(f"  Report: {md_path}")
    print(f"  JSON:   {json_path}")
    return md_path, json_path
