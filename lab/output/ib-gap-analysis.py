"""
IB Gap Analysis --5 research gaps across 18 NQ contract files.
Reads NQ-ib-5min-*.csv, deduplicates overlapping dates (keep later expiry),
and analyzes extension, timing, IB1-BOTH filtering, failures, and unbroken level duration.

IB1 = 08:30-09:30 ET (columns populate at 09:30)
IB2 = 09:30-10:30 ET (columns populate at 10:30)
RTH = 09:30-16:00 ET
"""
import glob
import os
import csv
import re
from datetime import datetime, time
from collections import defaultdict
import statistics

DATA_DIR = r"c:\Projects\futures_pipeline\data"
OUT_DIR = r"c:\Projects\futures_pipeline\lab\output"

# ─── Contract ordering for dedup (later expiry wins) ─────────────────────────
# NQ quarterly cycle: H=Mar, M=Jun, U=Sep, Z=Dec
# Year digit: 1=2021, 2=2022, ..., 6=2026
# For dedup: assign a numeric order so later expiry > earlier expiry

def contract_order(filename):
    """Return numeric order for contract. Higher = later expiry = preferred."""
    m = re.search(r'NQ-ib-5min-([HMUZ])(\d)\.csv', filename)
    if not m:
        return 0
    month_code, year_digit = m.group(1), int(m.group(2))
    month_rank = {'H': 1, 'M': 2, 'U': 3, 'Z': 4}[month_code]
    return year_digit * 10 + month_rank


def parse_date(s):
    """Parse date string like '2021-10-1' or '2025-12-17'."""
    s = s.strip()
    parts = s.split('-')
    return datetime(int(parts[0]), int(parts[1]), int(parts[2])).date()


def parse_time(s):
    """Parse time string like ' 09:30:00.000000'."""
    s = s.strip()
    parts = s.split(':')
    return time(int(parts[0]), int(parts[1]), int(parts[2].split('.')[0]))


def load_all_data():
    """Load all 18 files, return dict: date -> list of (contract_order, bars)."""
    files = glob.glob(os.path.join(DATA_DIR, "NQ-ib-5min-*.csv"))
    print(f"Found {len(files)} contract files")

    # date -> (contract_order, contract_name, bars_dict)
    date_data = {}  # date -> {contract_order, contract_name, bars: list of dicts}

    for fpath in files:
        fname = os.path.basename(fpath)
        c_order = contract_order(fname)
        contract_name = fname.replace('NQ-ib-5min-', '').replace('.csv', '')

        with open(fpath, 'r') as f:
            reader = csv.reader(f)
            header = [h.strip() for h in next(reader)]

            for row in reader:
                if len(row) < 22:
                    continue
                d = parse_date(row[0])
                t = parse_time(row[1])
                bar = {
                    'date': d,
                    'time': t,
                    'open': float(row[2].strip()),
                    'high': float(row[3].strip()),
                    'low': float(row[4].strip()),
                    'last': float(row[5].strip()),
                    'ib1_high': float(row[14].strip()),
                    'ib1_low': float(row[15].strip()),
                    'ib1_high_broken': float(row[16].strip()),
                    'ib1_low_broken': float(row[17].strip()),
                    'ib2_high': float(row[18].strip()),
                    'ib2_low': float(row[19].strip()),
                    'ib2_high_broken': float(row[20].strip()),
                    'ib2_low_broken': float(row[21].strip()),
                    'contract': contract_name,
                }

                if d not in date_data or c_order > date_data[d]['order']:
                    if d not in date_data or c_order > date_data[d]['order']:
                        date_data[d] = {'order': c_order, 'contract': contract_name, 'bars': []}
                    date_data[d]['bars'].append(bar)
                elif c_order == date_data[d]['order']:
                    date_data[d]['bars'].append(bar)

    # Sort bars within each day by time
    for d in date_data:
        date_data[d]['bars'].sort(key=lambda b: b['time'])

    print(f"Total unique dates after dedup: {len(date_data)}")
    return date_data


def classify_day(bars):
    """Classify a day's IB1 and IB2 outcomes from bar data.

    Returns dict with:
        ib1_high, ib1_low, ib1_range,
        ib2_high, ib2_low, ib2_range,
        ib1_outcome: H_ONLY, L_ONLY, BOTH, NEITHER
        ib2_outcome: H_ONLY, L_ONLY, BOTH, NEITHER
        ib1_high_broken, ib1_low_broken, ib2_high_broken, ib2_low_broken (bool)
        ib1_first_break_side, ib1_first_break_time
        ib2_first_break_side, ib2_first_break_time
        rth_high, rth_low (max high / min low during 09:30-16:00)
        post_ib2_high, post_ib2_low (max high / min low during 10:30-16:00)
        bars (for per-bar analysis)
    """
    result = {'bars': bars, 'valid': False}

    # Get IB1 levels (from first bar at 09:30 or after where ib1_high > 0)
    ib1_high = ib1_low = 0
    for b in bars:
        if b['ib1_high'] > 0:
            ib1_high = b['ib1_high']
            ib1_low = b['ib1_low']
            break

    # Get IB2 levels (from first bar at 10:30 or after where ib2_high > 0)
    ib2_high = ib2_low = 0
    for b in bars:
        if b['ib2_high'] > 0:
            ib2_high = b['ib2_high']
            ib2_low = b['ib2_low']
            break

    if ib1_high == 0 or ib2_high == 0:
        return result  # incomplete day

    result['ib1_high'] = ib1_high
    result['ib1_low'] = ib1_low
    result['ib1_range'] = ib1_high - ib1_low
    result['ib2_high'] = ib2_high
    result['ib2_low'] = ib2_low
    result['ib2_range'] = ib2_high - ib2_low

    # IB1 ray broken status (check last bar of day)
    last_ib1_bar = None
    for b in bars:
        if b['ib1_high'] > 0:
            last_ib1_bar = b
    ib1_h_broken = last_ib1_bar and last_ib1_bar['ib1_high_broken'] >= 1.0
    ib1_l_broken = last_ib1_bar and last_ib1_bar['ib1_low_broken'] >= 1.0

    # IB2 ray broken status
    last_ib2_bar = None
    for b in bars:
        if b['ib2_high'] > 0:
            last_ib2_bar = b
    ib2_h_broken = last_ib2_bar and last_ib2_bar['ib2_high_broken'] >= 1.0
    ib2_l_broken = last_ib2_bar and last_ib2_bar['ib2_low_broken'] >= 1.0

    result['ib1_high_broken'] = ib1_h_broken
    result['ib1_low_broken'] = ib1_l_broken
    result['ib2_high_broken'] = ib2_h_broken
    result['ib2_low_broken'] = ib2_l_broken

    # IB1 outcome
    if ib1_h_broken and ib1_l_broken:
        result['ib1_outcome'] = 'BOTH'
    elif ib1_h_broken:
        result['ib1_outcome'] = 'H_ONLY'
    elif ib1_l_broken:
        result['ib1_outcome'] = 'L_ONLY'
    else:
        result['ib1_outcome'] = 'NEITHER'

    # IB2 outcome
    if ib2_h_broken and ib2_l_broken:
        result['ib2_outcome'] = 'BOTH'
    elif ib2_h_broken:
        result['ib2_outcome'] = 'H_ONLY'
    elif ib2_l_broken:
        result['ib2_outcome'] = 'L_ONLY'
    else:
        result['ib2_outcome'] = 'NEITHER'

    # Find first break times for IB1 (transition from 0 to 1)
    ib1_first_h_time = None
    ib1_first_l_time = None
    prev_ib1_h = 0
    prev_ib1_l = 0
    for b in bars:
        if b['ib1_high'] == 0:
            continue
        if prev_ib1_h == 0 and b['ib1_high_broken'] >= 1.0:
            ib1_first_h_time = b['time']
        if prev_ib1_l == 0 and b['ib1_low_broken'] >= 1.0:
            ib1_first_l_time = b['time']
        prev_ib1_h = b['ib1_high_broken']
        prev_ib1_l = b['ib1_low_broken']

    # IB1 first break side and time
    if ib1_first_h_time and ib1_first_l_time:
        if ib1_first_h_time < ib1_first_l_time:
            result['ib1_first_break_side'] = 'HIGH'
            result['ib1_first_break_time'] = ib1_first_h_time
        elif ib1_first_l_time < ib1_first_h_time:
            result['ib1_first_break_side'] = 'LOW'
            result['ib1_first_break_time'] = ib1_first_l_time
        else:
            result['ib1_first_break_side'] = 'SAME_BAR'
            result['ib1_first_break_time'] = ib1_first_h_time
    elif ib1_first_h_time:
        result['ib1_first_break_side'] = 'HIGH'
        result['ib1_first_break_time'] = ib1_first_h_time
    elif ib1_first_l_time:
        result['ib1_first_break_side'] = 'LOW'
        result['ib1_first_break_time'] = ib1_first_l_time
    else:
        result['ib1_first_break_side'] = None
        result['ib1_first_break_time'] = None

    # Find first break times for IB2
    ib2_first_h_time = None
    ib2_first_l_time = None
    prev_ib2_h = 0
    prev_ib2_l = 0
    for b in bars:
        if b['ib2_high'] == 0:
            continue
        if prev_ib2_h == 0 and b['ib2_high_broken'] >= 1.0:
            ib2_first_h_time = b['time']
        if prev_ib2_l == 0 and b['ib2_low_broken'] >= 1.0:
            ib2_first_l_time = b['time']
        prev_ib2_h = b['ib2_high_broken']
        prev_ib2_l = b['ib2_low_broken']

    if ib2_first_h_time and ib2_first_l_time:
        if ib2_first_h_time < ib2_first_l_time:
            result['ib2_first_break_side'] = 'HIGH'
            result['ib2_first_break_time'] = ib2_first_h_time
        elif ib2_first_l_time < ib2_first_h_time:
            result['ib2_first_break_side'] = 'LOW'
            result['ib2_first_break_time'] = ib2_first_l_time
        else:
            result['ib2_first_break_side'] = 'SAME_BAR'
            result['ib2_first_break_time'] = ib2_first_h_time
    elif ib2_first_h_time:
        result['ib2_first_break_side'] = 'HIGH'
        result['ib2_first_break_time'] = ib2_first_h_time
    elif ib2_first_l_time:
        result['ib2_first_break_side'] = 'LOW'
        result['ib2_first_break_time'] = ib2_first_l_time
    else:
        result['ib2_first_break_side'] = None
        result['ib2_first_break_time'] = None

    # RTH high/low (09:30-16:00)
    rth_bars = [b for b in bars if time(9, 30) <= b['time'] < time(16, 0)]
    if rth_bars:
        result['rth_high'] = max(b['high'] for b in rth_bars)
        result['rth_low'] = min(b['low'] for b in rth_bars)
    else:
        return result

    # Post-IB2 high/low (10:30-16:00)
    post_ib2_bars = [b for b in bars if time(10, 30) <= b['time'] < time(16, 0)]
    if post_ib2_bars:
        result['post_ib2_high'] = max(b['high'] for b in post_ib2_bars)
        result['post_ib2_low'] = min(b['low'] for b in post_ib2_bars)
    else:
        result['post_ib2_high'] = result['rth_high']
        result['post_ib2_low'] = result['rth_low']

    # Per-bar highs/lows for time-based analysis (all RTH bars after IB2)
    result['rth_bars'] = rth_bars
    result['post_ib2_bars'] = post_ib2_bars
    result['valid'] = True
    return result


def percentile(data, p):
    """Calculate percentile."""
    if not data:
        return 0
    data_sorted = sorted(data)
    k = (len(data_sorted) - 1) * p / 100
    f = int(k)
    c = f + 1
    if c >= len(data_sorted):
        return data_sorted[f]
    return data_sorted[f] + (k - f) * (data_sorted[c] - data_sorted[f])


def fmt_time(t):
    """Format time object as HH:MM."""
    if t is None:
        return "N/A"
    return f"{t.hour:02d}:{t.minute:02d}"


def time_to_minutes(t):
    """Convert time to minutes from midnight."""
    return t.hour * 60 + t.minute


def minutes_to_time(m):
    """Convert minutes from midnight to time string."""
    return f"{int(m)//60:02d}:{int(m)%60:02d}"


# ─── Gap Analyses ────────────────────────────────────────────────────────────

def gap1_extension_analysis(days):
    """Gap 1: Extension beyond broken IB2 level on one-side days."""
    lines = []
    lines.append("=" * 72)
    lines.append("GAP 1: EXTENSION ANALYSIS")
    lines.append("After IB2 breaks one side, how far does price travel beyond?")
    lines.append("=" * 72)

    # Filter: IB2 one-side days (exactly one of IB2 high/low broken)
    one_side_days = []
    for d, info in days.items():
        if info['ib2_outcome'] in ('H_ONLY', 'L_ONLY'):
            one_side_days.append((d, info))

    lines.append(f"\nTotal one-side IB2 days: {len(one_side_days)}")

    extensions_pts = []
    extensions_pct = []
    records = []  # for CSV

    by_ib1 = defaultdict(list)  # ib1_outcome -> list of extension_pct

    for d, info in one_side_days:
        ib2_range = info['ib2_range']
        if ib2_range <= 0:
            continue

        if info['ib2_outcome'] == 'H_ONLY':
            broken_level = info['ib2_high']
            ext_pts = info['rth_high'] - broken_level
            side = 'HIGH'
        else:
            broken_level = info['ib2_low']
            ext_pts = broken_level - info['rth_low']
            side = 'LOW'

        ext_pct = (ext_pts / ib2_range) * 100
        extensions_pts.append(ext_pts)
        extensions_pct.append(ext_pct)
        by_ib1[info['ib1_outcome']].append(ext_pct)

        records.append({
            'gap': 1,
            'date': str(d),
            'side': side,
            'broken_level': broken_level,
            'extension_pts': round(ext_pts, 2),
            'extension_pct': round(ext_pct, 1),
            'ib1_outcome': info['ib1_outcome'],
            'ib2_range': round(ib2_range, 2),
        })

    if extensions_pts:
        lines.append(f"\n--- Overall Extension Statistics (n={len(extensions_pts)}) ---")
        lines.append(f"  Average extension:   {statistics.mean(extensions_pts):.2f} pts ({statistics.mean(extensions_pct):.1f}% of IB2 range)")
        lines.append(f"  Median extension:    {statistics.median(extensions_pts):.2f} pts ({statistics.median(extensions_pct):.1f}%)")
        lines.append(f"  P25:                 {percentile(extensions_pts, 25):.2f} pts ({percentile(extensions_pct, 25):.1f}%)")
        lines.append(f"  P75:                 {percentile(extensions_pts, 75):.2f} pts ({percentile(extensions_pct, 75):.1f}%)")
        lines.append(f"  P90:                 {percentile(extensions_pts, 90):.2f} pts ({percentile(extensions_pct, 90):.1f}%)")

        lines.append(f"\n--- Extension Thresholds ---")
        gt100 = sum(1 for e in extensions_pct if e > 100)
        gt200 = sum(1 for e in extensions_pct if e > 200)
        gt300 = sum(1 for e in extensions_pct if e > 300)
        n = len(extensions_pct)
        lines.append(f"  > 100% of IB2 range: {gt100}/{n} ({gt100/n*100:.1f}%)")
        lines.append(f"  > 200% of IB2 range: {gt200}/{n} ({gt200/n*100:.1f}%)")
        lines.append(f"  > 300% of IB2 range: {gt300}/{n} ({gt300/n*100:.1f}%)")

        lines.append(f"\n--- Breakdown by IB1 Outcome ---")
        for ib1_out in ['H_ONLY', 'L_ONLY', 'BOTH', 'NEITHER']:
            vals = by_ib1.get(ib1_out, [])
            if vals:
                lines.append(f"  IB1 {ib1_out:8s} (n={len(vals):3d}): "
                             f"avg={statistics.mean(vals):.1f}%  "
                             f"med={statistics.median(vals):.1f}%  "
                             f"P75={percentile(vals, 75):.1f}%  "
                             f"P90={percentile(vals, 90):.1f}%")
            else:
                lines.append(f"  IB1 {ib1_out:8s}: no days")

        # Side breakdown
        high_ext = [r['extension_pct'] for r in records if r['side'] == 'HIGH']
        low_ext = [r['extension_pct'] for r in records if r['side'] == 'LOW']
        lines.append(f"\n--- Breakdown by Break Side ---")
        if high_ext:
            lines.append(f"  HIGH breaks (n={len(high_ext)}): avg={statistics.mean(high_ext):.1f}%  med={statistics.median(high_ext):.1f}%")
        if low_ext:
            lines.append(f"  LOW breaks  (n={len(low_ext)}): avg={statistics.mean(low_ext):.1f}%  med={statistics.median(low_ext):.1f}%")

    return '\n'.join(lines), records


def gap2_time_to_break(days):
    """Gap 2: Time to first IB2 break on concordant one-side days."""
    lines = []
    lines.append("\n" + "=" * 72)
    lines.append("GAP 2: TIME TO FIRST IB2 BREAK ON ONE-SIDE DAYS")
    lines.append("IB1 breaks one side, IB2 breaks same direction")
    lines.append("=" * 72)

    # Filter: IB1 one-side AND IB2 breaks same direction
    concordant = []
    for d, info in days.items():
        if info['ib1_outcome'] == 'H_ONLY' and info['ib2_outcome'] == 'H_ONLY':
            concordant.append((d, info, 'HIGH'))
        elif info['ib1_outcome'] == 'L_ONLY' and info['ib2_outcome'] == 'L_ONLY':
            concordant.append((d, info, 'LOW'))

    lines.append(f"\nTotal concordant days (IB1 one-side, IB2 same direction): {len(concordant)}")

    break_times_min = []
    high_times = []
    low_times = []
    hour_dist = defaultdict(int)
    records = []

    for d, info, direction in concordant:
        bt = info.get('ib2_first_break_time')
        if bt is None:
            continue
        mins = time_to_minutes(bt)
        break_times_min.append(mins)
        hour_dist[bt.hour] += 1

        if direction == 'HIGH':
            high_times.append(mins)
        else:
            low_times.append(mins)

        records.append({
            'gap': 2,
            'date': str(d),
            'direction': direction,
            'break_time': fmt_time(bt),
            'break_hour': bt.hour,
            'ib1_outcome': info['ib1_outcome'],
        })

    if break_times_min:
        lines.append(f"\n--- Break Time Distribution (n={len(break_times_min)}) ---")
        lines.append(f"  Median break time: {minutes_to_time(statistics.median(break_times_min))}")
        lines.append(f"  P25 break time:    {minutes_to_time(percentile(break_times_min, 25))}")
        lines.append(f"  P75 break time:    {minutes_to_time(percentile(break_times_min, 75))}")

        lines.append(f"\n--- Distribution by Hour ---")
        for h in range(10, 17):
            count = hour_dist.get(h, 0)
            pct = count / len(break_times_min) * 100
            lines.append(f"  {h:02d}:00-{h:02d}:59: {count:3d} ({pct:.1f}%)")

        lines.append(f"\n--- HIGH vs LOW Timing ---")
        if high_times:
            lines.append(f"  IB1 HIGH only (n={len(high_times)}): "
                         f"median={minutes_to_time(statistics.median(high_times))}  "
                         f"P25={minutes_to_time(percentile(high_times, 25))}  "
                         f"P75={minutes_to_time(percentile(high_times, 75))}")
        if low_times:
            lines.append(f"  IB1 LOW only  (n={len(low_times)}): "
                         f"median={minutes_to_time(statistics.median(low_times))}  "
                         f"P25={minutes_to_time(percentile(low_times, 25))}  "
                         f"P75={minutes_to_time(percentile(low_times, 75))}")

    return '\n'.join(lines), records


def gap3_ib1_both_signal(days):
    """Gap 3: On IB1 BOTH days, any secondary signal for IB2 direction?"""
    lines = []
    lines.append("\n" + "=" * 72)
    lines.append("GAP 3: IB1 BOTH DAYS --SECONDARY SIGNAL SEARCH")
    lines.append("=" * 72)

    both_days = [(d, info) for d, info in days.items() if info['ib1_outcome'] == 'BOTH']
    lines.append(f"\nTotal IB1 BOTH days: {len(both_days)}")

    # IB1 first break side
    first_break_high = 0
    first_break_low = 0
    first_break_same = 0
    first_break_none = 0

    # IB1 first break -> IB2 first break concordance
    concordance = defaultdict(lambda: defaultdict(int))
    # IB2 outcome split
    ib2_outcomes = defaultdict(int)
    # IB1 range by IB2 outcome
    ib1_range_by_ib2 = defaultdict(list)
    ib2_range_by_ib2 = defaultdict(list)
    # First break time by IB2 outcome
    first_break_time_by_ib2 = defaultdict(list)

    records = []

    for d, info in both_days:
        fb_side = info.get('ib1_first_break_side')
        fb_time = info.get('ib1_first_break_time')
        ib2_fb_side = info.get('ib2_first_break_side')
        ib2_out = info['ib2_outcome']

        if fb_side == 'HIGH':
            first_break_high += 1
        elif fb_side == 'LOW':
            first_break_low += 1
        elif fb_side == 'SAME_BAR':
            first_break_same += 1
        else:
            first_break_none += 1

        if fb_side and ib2_fb_side:
            concordance[fb_side][ib2_fb_side] += 1

        ib2_outcomes[ib2_out] += 1
        ib1_range_by_ib2[ib2_out].append(info['ib1_range'])
        ib2_range_by_ib2[ib2_out].append(info['ib2_range'])

        if fb_time:
            first_break_time_by_ib2[ib2_out].append(time_to_minutes(fb_time))

        records.append({
            'gap': 3,
            'date': str(d),
            'ib1_first_break_side': fb_side or 'NONE',
            'ib1_first_break_time': fmt_time(fb_time),
            'ib2_first_break_side': ib2_fb_side or 'NONE',
            'ib2_outcome': ib2_out,
            'ib1_range': round(info['ib1_range'], 2),
            'ib2_range': round(info['ib2_range'], 2),
        })

    n = len(both_days)
    lines.append(f"\n--- IB1 First Break Side ---")
    lines.append(f"  HIGH first:  {first_break_high} ({first_break_high/n*100:.1f}%)")
    lines.append(f"  LOW first:   {first_break_low} ({first_break_low/n*100:.1f}%)")
    lines.append(f"  SAME_BAR:    {first_break_same} ({first_break_same/n*100:.1f}%)")
    lines.append(f"  NONE:        {first_break_none} ({first_break_none/n*100:.1f}%)")

    lines.append(f"\n--- IB1 First Break -> IB2 First Break Direction ---")
    for ib1_side in ['HIGH', 'LOW', 'SAME_BAR']:
        row = concordance.get(ib1_side, {})
        total = sum(row.values())
        if total > 0:
            parts = [f"{k}={v} ({v/total*100:.1f}%)" for k, v in sorted(row.items())]
            lines.append(f"  IB1 first {ib1_side:8s} (n={total}): IB2 first -> {', '.join(parts)}")

    # Concordance rate: IB1 first break side predicts IB2 first break side
    total_testable = 0
    total_concordant = 0
    for ib1_side in ['HIGH', 'LOW']:
        row = concordance.get(ib1_side, {})
        total = sum(row.values())
        same_side = row.get(ib1_side, 0)
        total_testable += total
        total_concordant += same_side

    if total_testable > 0:
        lines.append(f"\n  Overall IB1->IB2 direction concordance: {total_concordant}/{total_testable} "
                     f"({total_concordant/total_testable*100:.1f}%)")

    lines.append(f"\n--- IB2 Outcome on IB1 BOTH Days ---")
    for out in ['BOTH', 'H_ONLY', 'L_ONLY', 'NEITHER']:
        count = ib2_outcomes.get(out, 0)
        lines.append(f"  IB2 {out:8s}: {count} ({count/n*100:.1f}%)")

    lines.append(f"\n--- IB1 Range by IB2 Outcome ---")
    for out in ['BOTH', 'H_ONLY', 'L_ONLY', 'NEITHER']:
        vals = ib1_range_by_ib2.get(out, [])
        if vals:
            lines.append(f"  IB2 {out:8s}: IB1 range avg={statistics.mean(vals):.2f}  med={statistics.median(vals):.2f}")

    lines.append(f"\n--- IB2 Range by IB2 Outcome ---")
    for out in ['BOTH', 'H_ONLY', 'L_ONLY', 'NEITHER']:
        vals = ib2_range_by_ib2.get(out, [])
        if vals:
            lines.append(f"  IB2 {out:8s}: IB2 range avg={statistics.mean(vals):.2f}  med={statistics.median(vals):.2f}")

    # Filter search: can we predict IB2 direction on BOTH days > 55%?
    lines.append(f"\n--- Filter Tests (target: >55% IB2 direction prediction) ---")

    # Test 1: IB1 first break side -> IB2 outcome direction
    for filter_side in ['HIGH', 'LOW']:
        filtered = [(d, info) for d, info in both_days
                    if info.get('ib1_first_break_side') == filter_side]
        if filtered:
            ib2_h = sum(1 for _, info in filtered if info['ib2_outcome'] in ('H_ONLY', 'BOTH') and
                        info.get('ib2_first_break_side') == filter_side)
            lines.append(f"  IB1 first {filter_side} -> IB2 first same: {ib2_h}/{len(filtered)} "
                         f"({ib2_h/len(filtered)*100:.1f}%)")

    # Test 2: IB1 range size (above/below median) vs IB2 one-side rate
    ib1_ranges = [info['ib1_range'] for _, info in both_days]
    median_ib1 = statistics.median(ib1_ranges)
    above_med = [(d, info) for d, info in both_days if info['ib1_range'] > median_ib1]
    below_med = [(d, info) for d, info in both_days if info['ib1_range'] <= median_ib1]

    for label, group in [('IB1 range > median', above_med), ('IB1 range <= median', below_med)]:
        if group:
            one_side = sum(1 for _, info in group if info['ib2_outcome'] in ('H_ONLY', 'L_ONLY'))
            lines.append(f"  {label} (n={len(group)}): IB2 one-side = {one_side}/{len(group)} "
                         f"({one_side/len(group)*100:.1f}%)")

    # Test 3: IB2 range size
    ib2_ranges = [info['ib2_range'] for _, info in both_days]
    median_ib2 = statistics.median(ib2_ranges)
    above_med2 = [(d, info) for d, info in both_days if info['ib2_range'] > median_ib2]
    below_med2 = [(d, info) for d, info in both_days if info['ib2_range'] <= median_ib2]

    for label, group in [('IB2 range > median', above_med2), ('IB2 range <= median', below_med2)]:
        if group:
            one_side = sum(1 for _, info in group if info['ib2_outcome'] in ('H_ONLY', 'L_ONLY'))
            lines.append(f"  {label} (n={len(group)}): IB2 one-side = {one_side}/{len(group)} "
                         f"({one_side/len(group)*100:.1f}%)")

    # Test 4: IB1 first break time (early vs late)
    all_fb_times = [(d, info) for d, info in both_days
                    if info.get('ib1_first_break_time') is not None]
    if all_fb_times:
        fb_min_list = [time_to_minutes(info['ib1_first_break_time']) for _, info in all_fb_times]
        med_fb_time = statistics.median(fb_min_list)
        early = [(d, info) for d, info in all_fb_times
                 if time_to_minutes(info['ib1_first_break_time']) <= med_fb_time]
        late = [(d, info) for d, info in all_fb_times
                if time_to_minutes(info['ib1_first_break_time']) > med_fb_time]
        for label, group in [('IB1 first break early', early), ('IB1 first break late', late)]:
            if group:
                one_side = sum(1 for _, info in group if info['ib2_outcome'] in ('H_ONLY', 'L_ONLY'))
                lines.append(f"  {label} (n={len(group)}): IB2 one-side = {one_side}/{len(group)} "
                             f"({one_side/len(group)*100:.1f}%)")

    # Test 5: IB1/IB2 size ratio
    size_ratios = [(d, info, info['ib2_range'] / info['ib1_range'] if info['ib1_range'] > 0 else 0)
                   for d, info in both_days if info['ib1_range'] > 0]
    if size_ratios:
        ratios = [r for _, _, r in size_ratios]
        med_ratio = statistics.median(ratios)
        high_ratio = [(d, info) for d, info, r in size_ratios if r > med_ratio]
        low_ratio = [(d, info) for d, info, r in size_ratios if r <= med_ratio]
        for label, group in [('IB2/IB1 ratio > median', high_ratio), ('IB2/IB1 ratio <= median', low_ratio)]:
            if group:
                one_side = sum(1 for _, info in group if info['ib2_outcome'] in ('H_ONLY', 'L_ONLY'))
                lines.append(f"  {label} (n={len(group)}): IB2 one-side = {one_side}/{len(group)} "
                             f"({one_side/len(group)*100:.1f}%)")

    return '\n'.join(lines), records


def gap4_failure_analysis(days):
    """Gap 4: IB1 broke one side but IB2 broke OTHER direction first."""
    lines = []
    lines.append("\n" + "=" * 72)
    lines.append("GAP 4: FAILURE ANALYSIS")
    lines.append("IB1 one-side break, but IB2 first breaks the OTHER direction")
    lines.append("=" * 72)

    # IB1 one-side days
    ib1_one_side = [(d, info) for d, info in days.items()
                    if info['ib1_outcome'] in ('H_ONLY', 'L_ONLY')]

    # Failures: IB2 first break is opposite to IB1 break direction
    failures = []
    successes = []

    for d, info in ib1_one_side:
        ib2_fb = info.get('ib2_first_break_side')
        if ib2_fb is None:
            continue  # IB2 never broke

        if info['ib1_outcome'] == 'H_ONLY':
            expected = 'HIGH'
        else:
            expected = 'LOW'

        if ib2_fb == expected:
            successes.append((d, info))
        elif ib2_fb in ('HIGH', 'LOW') and ib2_fb != expected:
            failures.append((d, info))
        # SAME_BAR doesn't clearly fail or succeed

    total_testable = len(failures) + len(successes)
    lines.append(f"\nIB1 one-side days with IB2 break: {total_testable}")
    lines.append(f"  Successes (IB2 same direction): {len(successes)} ({len(successes)/total_testable*100:.1f}%)")
    lines.append(f"  Failures  (IB2 other direction): {len(failures)} ({len(failures)/total_testable*100:.1f}%)")

    records = []

    if failures:
        fail_ib1_ranges = [info['ib1_range'] for _, info in failures]
        fail_ib2_ranges = [info['ib2_range'] for _, info in failures]
        fail_ratios = [info['ib2_range'] / info['ib1_range'] if info['ib1_range'] > 0 else 0
                       for _, info in failures]
        fail_break_times = [time_to_minutes(info['ib2_first_break_time'])
                           for _, info in failures if info.get('ib2_first_break_time')]

        succ_ib1_ranges = [info['ib1_range'] for _, info in successes]
        succ_ib2_ranges = [info['ib2_range'] for _, info in successes]
        succ_ratios = [info['ib2_range'] / info['ib1_range'] if info['ib1_range'] > 0 else 0
                       for _, info in successes]
        succ_break_times = [time_to_minutes(info['ib2_first_break_time'])
                           for _, info in successes if info.get('ib2_first_break_time')]

        lines.append(f"\n--- Failure Characteristics vs Success ---")
        lines.append(f"  {'Metric':<25s} {'Failures':>15s} {'Successes':>15s}")
        lines.append(f"  {'-'*25} {'-'*15} {'-'*15}")
        lines.append(f"  {'IB1 range (avg)':<25s} {statistics.mean(fail_ib1_ranges):>15.2f} {statistics.mean(succ_ib1_ranges):>15.2f}")
        lines.append(f"  {'IB1 range (med)':<25s} {statistics.median(fail_ib1_ranges):>15.2f} {statistics.median(succ_ib1_ranges):>15.2f}")
        lines.append(f"  {'IB2 range (avg)':<25s} {statistics.mean(fail_ib2_ranges):>15.2f} {statistics.mean(succ_ib2_ranges):>15.2f}")
        lines.append(f"  {'IB2 range (med)':<25s} {statistics.median(fail_ib2_ranges):>15.2f} {statistics.median(succ_ib2_ranges):>15.2f}")
        lines.append(f"  {'IB2/IB1 ratio (avg)':<25s} {statistics.mean(fail_ratios):>15.2f} {statistics.mean(succ_ratios):>15.2f}")
        lines.append(f"  {'IB2/IB1 ratio (med)':<25s} {statistics.median(fail_ratios):>15.2f} {statistics.median(succ_ratios):>15.2f}")
        if fail_break_times and succ_break_times:
            lines.append(f"  {'IB2 break time (med)':<25s} {minutes_to_time(statistics.median(fail_break_times)):>15s} {minutes_to_time(statistics.median(succ_break_times)):>15s}")

        # Extension on failure vs success days
        fail_ext = []
        succ_ext = []
        for d, info in failures:
            ib2_range = info['ib2_range']
            if ib2_range <= 0:
                continue
            # On failure days, the extension is in the WRONG direction
            if info['ib2_outcome'] in ('L_ONLY', 'BOTH'):
                # IB1 was H_ONLY but IB2 broke low
                ext = info['ib2_low'] - info['rth_low']
            elif info['ib2_outcome'] in ('H_ONLY', 'BOTH'):
                ext = info['rth_high'] - info['ib2_high']
            else:
                ext = 0
            fail_ext.append(ext)

        for d, info in successes:
            ib2_range = info['ib2_range']
            if ib2_range <= 0:
                continue
            if info['ib1_outcome'] == 'H_ONLY':
                ext = info['rth_high'] - info['ib2_high']
            else:
                ext = info['ib2_low'] - info['rth_low']
            succ_ext.append(ext)

        if fail_ext and succ_ext:
            lines.append(f"\n--- Extension (pts) ---")
            lines.append(f"  Failure avg extension:  {statistics.mean(fail_ext):.2f} pts")
            lines.append(f"  Success avg extension:  {statistics.mean(succ_ext):.2f} pts")

        # Contract clustering
        lines.append(f"\n--- Contract/Period Clustering ---")
        contract_counts = defaultdict(int)
        year_counts = defaultdict(int)
        for d, info in failures:
            contract_counts[info['bars'][0]['contract']] += 1
            year_counts[d.year] += 1
        for c, count in sorted(contract_counts.items(), key=lambda x: -x[1])[:10]:
            lines.append(f"  {c}: {count} failures")
        lines.append(f"  By year: {dict(sorted(year_counts.items()))}")

        # Pre-trade indicators
        lines.append(f"\n--- Potential Pre-Trade Indicators ---")
        # IB2/IB1 ratio threshold
        for thresh in [0.5, 0.75, 1.0, 1.5, 2.0]:
            fail_above = sum(1 for r in fail_ratios if r > thresh)
            succ_above = sum(1 for r in succ_ratios if r > thresh)
            total_above = fail_above + succ_above
            if total_above > 0:
                fail_rate = fail_above / total_above * 100
                lines.append(f"  IB2/IB1 > {thresh:.1f}: fail rate = {fail_above}/{total_above} ({fail_rate:.1f}%)")

        for d, info in failures:
            records.append({
                'gap': 4,
                'date': str(d),
                'ib1_outcome': info['ib1_outcome'],
                'ib2_outcome': info['ib2_outcome'],
                'ib1_range': round(info['ib1_range'], 2),
                'ib2_range': round(info['ib2_range'], 2),
                'ratio': round(info['ib2_range'] / info['ib1_range'], 3) if info['ib1_range'] > 0 else 0,
                'ib2_break_time': fmt_time(info.get('ib2_first_break_time')),
                'contract': info['bars'][0]['contract'],
            })

    return '\n'.join(lines), records


def gap5_unbroken_level(days):
    """Gap 5: IB2 unbroken level analysis --proximity, and late breaks on BOTH days."""
    lines = []
    lines.append("\n" + "=" * 72)
    lines.append("GAP 5: IB2 UNBROKEN LEVEL DURATION")
    lines.append("On one-side days, does the unbroken level hold all day?")
    lines.append("=" * 72)

    one_side_days = [(d, info) for d, info in days.items()
                     if info['ib2_outcome'] in ('H_ONLY', 'L_ONLY')]

    lines.append(f"\nTotal one-side IB2 days: {len(one_side_days)}")

    lines.append(f"\nNOTE: By definition, one-side days have the unbroken IB2 level holding")
    lines.append(f"all day --the ray-broken column reflects the full RTH session.")
    lines.append(f"The unbroken level holds 100% of the time on these days.")
    lines.append(f"The useful analysis is: (A) how close price gets to the unbroken level,")
    lines.append(f"and (B) on IB2 BOTH days, when does the second side break?")

    # ── Part A: Proximity analysis on one-side days ──
    min_distances = []  # as % of IB2 range
    min_distances_pts = []
    records = []

    for d, info in one_side_days:
        ib2_range = info['ib2_range']
        if ib2_range <= 0:
            continue

        if info['ib2_outcome'] == 'H_ONLY':
            # IB2 high was broken, low was NOT broken
            unbroken_level = info['ib2_low']
            post_bars = info.get('post_ib2_bars', [])
            if post_bars:
                closest = min(b['low'] for b in post_bars if b['low'] > 0)
            else:
                closest = unbroken_level
            min_dist = closest - unbroken_level
        else:  # L_ONLY
            # IB2 low was broken, high was NOT broken
            unbroken_level = info['ib2_high']
            post_bars = info.get('post_ib2_bars', [])
            if post_bars:
                closest = max(b['high'] for b in post_bars if b['high'] > 0)
            else:
                closest = unbroken_level
            min_dist = unbroken_level - closest

        min_dist_pct = (min_dist / ib2_range) * 100 if ib2_range > 0 else 0
        min_distances_pts.append(min_dist)
        min_distances.append(min_dist_pct)

        records.append({
            'gap': 5,
            'date': str(d),
            'broken_side': info['ib2_outcome'],
            'unbroken_level': round(unbroken_level, 2),
            'held_all_day': 'Y',
            'min_distance_pts': round(min_dist, 2),
            'min_distance_pct': round(min_dist_pct, 1),
        })

    n = len(one_side_days)

    lines.append(f"\n--- Part A: Closest Approach to Unbroken Level (one-side days, n={len(min_distances_pts)}) ---")
    if min_distances_pts:
        lines.append(f"  Avg closest distance:    {statistics.mean(min_distances_pts):.2f} pts ({statistics.mean(min_distances):.1f}% of IB2 range)")
        lines.append(f"  Median closest distance: {statistics.median(min_distances_pts):.2f} pts ({statistics.median(min_distances):.1f}%)")
        lines.append(f"  P25 closest distance:    {percentile(min_distances_pts, 25):.2f} pts ({percentile(min_distances, 25):.1f}%)")
        lines.append(f"  P75 closest distance:    {percentile(min_distances_pts, 75):.2f} pts ({percentile(min_distances, 75):.1f}%)")

    # Proximity thresholds
    lines.append(f"\n--- Proximity to Unbroken Level ---")
    within_25 = sum(1 for p in min_distances if p < 25)
    within_50 = sum(1 for p in min_distances if p < 50)
    within_75 = sum(1 for p in min_distances if p < 75)
    lines.append(f"  Price comes within 25% of IB2 range: {within_25}/{n} ({within_25/n*100:.1f}%)")
    lines.append(f"  Price comes within 50% of IB2 range: {within_50}/{n} ({within_50/n*100:.1f}%)")
    lines.append(f"  Price comes within 75% of IB2 range: {within_75}/{n} ({within_75/n*100:.1f}%)")

    # Distance never gets close (stays > 100% away)
    stays_far = sum(1 for p in min_distances if p >= 100)
    lines.append(f"  Price stays > 100% away from unbroken: {stays_far}/{n} ({stays_far/n*100:.1f}%)")

    # ── Part B: IB2 BOTH days --second side break timing ──
    both_days = [(d, info) for d, info in days.items() if info['ib2_outcome'] == 'BOTH']
    lines.append(f"\n--- Part B: IB2 BOTH Days --Second Side Break Timing (n={len(both_days)}) ---")
    lines.append(f"  These are days that started as one-side but eventually broke the other.")

    second_break_times = []
    first_to_second_gaps = []

    for d, info in both_days:
        fb_time = info.get('ib2_first_break_time')
        ib2_first_h = None
        ib2_first_l = None
        prev_h = 0
        prev_l = 0
        for b in info['bars']:
            if b['ib2_high'] == 0:
                continue
            if prev_h == 0 and b['ib2_high_broken'] >= 1.0:
                ib2_first_h = b['time']
            if prev_l == 0 and b['ib2_low_broken'] >= 1.0:
                ib2_first_l = b['time']
            prev_h = b['ib2_high_broken']
            prev_l = b['ib2_low_broken']

        if ib2_first_h and ib2_first_l:
            if ib2_first_h < ib2_first_l:
                second_time = ib2_first_l
                first_time = ib2_first_h
            elif ib2_first_l < ib2_first_h:
                second_time = ib2_first_h
                first_time = ib2_first_l
            else:
                continue  # same bar, skip

            second_break_times.append(time_to_minutes(second_time))
            gap = time_to_minutes(second_time) - time_to_minutes(first_time)
            first_to_second_gaps.append(gap)

            records.append({
                'gap': 5,
                'date': str(d),
                'broken_side': 'BOTH',
                'unbroken_level': 0,
                'held_all_day': 'N',
                'min_distance_pts': 0,
                'min_distance_pct': 0,
                'first_break_time': fmt_time(first_time),
                'second_break_time': fmt_time(second_time),
                'gap_minutes': gap,
            })

    if second_break_times:
        lines.append(f"\n  Days with distinct first/second break: {len(second_break_times)}")
        lines.append(f"  Second break time --median: {minutes_to_time(statistics.median(second_break_times))}")
        lines.append(f"  Second break time --P25:    {minutes_to_time(percentile(second_break_times, 25))}")
        lines.append(f"  Second break time --P75:    {minutes_to_time(percentile(second_break_times, 75))}")

        # RTH-only gaps (exclude globex second breaks)
        rth_gaps = [g for g, t in zip(first_to_second_gaps, second_break_times) if 570 <= t < 960]
        lines.append(f"\n  Time gap between first and second break (RTH only, n={len(rth_gaps)}):")
        if rth_gaps:
            lines.append(f"    Median gap: {statistics.median(rth_gaps):.0f} minutes")
            lines.append(f"    Average gap: {statistics.mean(rth_gaps):.0f} minutes")
            lines.append(f"    P25 gap: {percentile(rth_gaps, 25):.0f} minutes")
            lines.append(f"    P75 gap: {percentile(rth_gaps, 75):.0f} minutes")

        # Split RTH vs globex second breaks
        rth_second = [t for t in second_break_times if 570 <= t < 960]  # 09:30-16:00
        globex_second = [t for t in second_break_times if t >= 960 or t < 570]
        lines.append(f"\n  Second break during RTH (09:30-16:00): {len(rth_second)} ({len(rth_second)/len(second_break_times)*100:.1f}%)")
        lines.append(f"  Second break during globex (overnight): {len(globex_second)} ({len(globex_second)/len(second_break_times)*100:.1f}%)")

        # Distribution of RTH second breaks by hour
        hour_dist = defaultdict(int)
        for t in rth_second:
            hour_dist[t // 60] += 1
        lines.append(f"\n  Second break by hour (RTH only, n={len(rth_second)}):")
        for h in range(10, 17):
            count = hour_dist.get(h, 0)
            pct = count / len(rth_second) * 100 if rth_second else 0
            lines.append(f"    {h:02d}:00-{h:02d}:59: {count:3d} ({pct:.1f}%)")

    return '\n'.join(lines), records


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print("Loading data...")
    date_data = load_all_data()

    print("Classifying days...")
    days = {}
    skipped = 0
    for d, dd in date_data.items():
        info = classify_day(dd['bars'])
        if info['valid']:
            info['contract'] = dd['contract']
            days[d] = info
        else:
            skipped += 1

    print(f"Valid trading days: {len(days)}, Skipped: {skipped}")

    # Basic stats
    ib1_outcomes = defaultdict(int)
    ib2_outcomes = defaultdict(int)
    for d, info in days.items():
        ib1_outcomes[info['ib1_outcome']] += 1
        ib2_outcomes[info['ib2_outcome']] += 1

    n = len(days)
    header_lines = []
    header_lines.append("IB GAP ANALYSIS -- NQ 5-Min IB Data")
    header_lines.append(f"Date range: {min(days.keys())} to {max(days.keys())}")
    header_lines.append(f"Total valid trading days: {n}")
    header_lines.append(f"\nIB1 Outcomes:")
    for out in ['H_ONLY', 'L_ONLY', 'BOTH', 'NEITHER']:
        header_lines.append(f"  {out:8s}: {ib1_outcomes[out]:4d} ({ib1_outcomes[out]/n*100:.1f}%)")
    header_lines.append(f"\nIB2 Outcomes:")
    for out in ['H_ONLY', 'L_ONLY', 'BOTH', 'NEITHER']:
        header_lines.append(f"  {out:8s}: {ib2_outcomes[out]:4d} ({ib2_outcomes[out]/n*100:.1f}%)")

    # Run all gaps
    all_records = []

    g1_text, g1_records = gap1_extension_analysis(days)
    all_records.extend(g1_records)

    g2_text, g2_records = gap2_time_to_break(days)
    all_records.extend(g2_records)

    g3_text, g3_records = gap3_ib1_both_signal(days)
    all_records.extend(g3_records)

    g4_text, g4_records = gap4_failure_analysis(days)
    all_records.extend(g4_records)

    g5_text, g5_records = gap5_unbroken_level(days)
    all_records.extend(g5_records)

    # Write summary
    disclaimer = ("\n\n" + "=" * 72 +
                  "\nDISCLAIMER: All percentages are observed frequencies from historical data."
                  "\nThey do not predict future outcomes. Sample sizes vary by gap."
                  "\nThis analysis uses 5-minute bar data; intrabar breaks may occur earlier"
                  "\nthan reported bar timestamps. Extension calculations use bar high/low,"
                  "\nnot tick-level data."
                  "\n" + "=" * 72)

    summary = '\n'.join(header_lines) + '\n' + g1_text + g2_text + g3_text + g4_text + g5_text + disclaimer

    summary_path = os.path.join(OUT_DIR, 'ib-gap-analysis-summary.txt')
    with open(summary_path, 'w') as f:
        f.write(summary)
    print(f"\nWrote summary to {summary_path}")

    # Write CSV
    csv_path = os.path.join(OUT_DIR, 'ib-gap-analysis.csv')
    if all_records:
        # Gather all keys
        all_keys = set()
        for r in all_records:
            all_keys.update(r.keys())
        fieldnames = ['gap', 'date'] + sorted(all_keys - {'gap', 'date'})

        with open(csv_path, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
            writer.writeheader()
            for r in sorted(all_records, key=lambda x: (x['gap'], x['date'])):
                writer.writerow(r)
    print(f"Wrote CSV to {csv_path}")

    # Print summary to console
    print("\n" + summary)


if __name__ == '__main__':
    main()
