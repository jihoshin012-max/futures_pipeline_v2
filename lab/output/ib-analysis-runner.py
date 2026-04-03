"""
IB Analysis Runner — Processes all 18 NQ IB 5-min contracts.
Outputs: ib-analysis-findings.md
"""
import csv
import os
import glob
from collections import defaultdict, Counter
from datetime import datetime

DATA_DIR = "c:/Projects/futures_pipeline/data"
FILES = sorted(glob.glob(os.path.join(DATA_DIR, "NQ-ib-5min-*.csv")))

# Period config
CAL_START = datetime(2021, 10, 1)
CAL_END = datetime(2025, 12, 14)
HO_START = datetime(2025, 12, 17)
HO_END = datetime(2026, 3, 13)

SLOPE_THRESHOLD = 0.25  # pts/bar

def parse_rows(filepath):
    """Parse CSV, return list of dicts with cleaned keys."""
    rows = []
    with open(filepath, 'r') as f:
        reader = csv.reader(f)
        header = next(reader)
        header = [h.strip() for h in header]
        for line in reader:
            if len(line) < len(header):
                continue
            row = {}
            for i, h in enumerate(header):
                row[h] = line[i].strip()
            rows.append(row)
    return rows

def time_to_minutes(t_str):
    """Convert HH:MM:SS.ffffff to minutes since midnight."""
    parts = t_str.split(":")
    return int(parts[0]) * 60 + int(parts[1])

def is_rth(t_str):
    """RTH = 09:30-16:00"""
    m = time_to_minutes(t_str)
    return 570 <= m < 960  # 9:30=570, 16:00=960

def get_contract_from_path(fp):
    base = os.path.basename(fp)
    # NQ-ib-5min-Z1.csv -> Z1
    return base.replace("NQ-ib-5min-", "").replace(".csv", "")

# ---- Collect daily summaries across all contracts ----
class DaySummary:
    def __init__(self):
        self.date = None
        self.contract = None
        self.ib1_high = 0.0
        self.ib1_low = 0.0
        self.ib1_high_broken = 0
        self.ib1_low_broken = 0
        self.ib2_high = 0.0
        self.ib2_low = 0.0
        self.ib2_high_broken = 0
        self.ib2_low_broken = 0
        self.midline_at_1000 = 0.0  # 10:00 bar
        self.midline_at_1030 = 0.0  # 10:30 bar (last bar of IB2)
        self.last_at_1030 = 0.0  # price at IB2 end
        # For timing analysis
        self.ib1_both_broken_time = None  # time when second break happens
        self.ib2_both_broken_time = None
        # For IB2 first break
        self.ib2_first_break = None  # 'HIGH' or 'LOW' or None
        # RTH bars for break timing
        self.ib2_high_break_time = None
        self.ib2_low_break_time = None

all_days = []

for fp in FILES:
    contract = get_contract_from_path(fp)
    rows = parse_rows(fp)

    # Group by date
    days = defaultdict(list)
    for r in rows:
        days[r['Date']].append(r)

    for date_str, bars in days.items():
        # Parse date
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
        except:
            try:
                dt = datetime.strptime(date_str, "%Y-%m-%d")
            except:
                continue

        ds = DaySummary()
        ds.date = dt
        ds.contract = contract

        # Get IB values from the last RTH bar that has them set
        # IB1/IB2 values are set once and remain constant after their period
        # We need the values after each IB period is complete

        # Sort bars by time
        rth_bars = [b for b in bars if is_rth(b['Time'])]
        if not rth_bars:
            continue

        # Find IB1 values (stable after 09:30 period = first bar at 09:30)
        # IB1 covers 08:30-09:30, values appear on bars after 09:30
        # Find first RTH bar with IB1 High > 0
        for b in rth_bars:
            ib1h = float(b.get('IB1 High', '0'))
            ib1l = float(b.get('IB1 Low', '0'))
            if ib1h > 0 and ib1l > 0:
                ds.ib1_high = ib1h
                ds.ib1_low = ib1l
                ds.ib1_high_broken = int(float(b.get('IB1 High Ray Broken', '0')))
                ds.ib1_low_broken = int(float(b.get('IB1 Low Ray Broken', '0')))
                break

        # For IB1 break status, we need to check the LAST value in the day
        # because breaks accumulate over time
        # Actually, IB1 Ray Broken columns track whether the ray was broken during the day
        # Let's take the max across all RTH bars
        ib1_hb_max = 0
        ib1_lb_max = 0
        ib2_hb_max = 0
        ib2_lb_max = 0

        for b in rth_bars:
            v = int(float(b.get('IB1 High Ray Broken', '0')))
            if v > ib1_hb_max:
                ib1_hb_max = v
            v = int(float(b.get('IB1 Low Ray Broken', '0')))
            if v > ib1_lb_max:
                ib1_lb_max = v
            v = int(float(b.get('IB2 High Ray Broken', '0')))
            if v > ib2_hb_max:
                ib2_hb_max = v
            v = int(float(b.get('IB2 Low Ray Broken', '0')))
            if v > ib2_lb_max:
                ib2_lb_max = v

        ds.ib1_high_broken = ib1_hb_max
        ds.ib1_low_broken = ib1_lb_max
        ds.ib2_high_broken = ib2_hb_max
        ds.ib2_low_broken = ib2_lb_max

        # Get IB2 values
        for b in rth_bars:
            ib2h = float(b.get('IB2 High', '0'))
            ib2l = float(b.get('IB2 Low', '0'))
            if ib2h > 0 and ib2l > 0:
                ds.ib2_high = ib2h
                ds.ib2_low = ib2l
                break

        # Midline at 10:00 and 10:30
        for b in rth_bars:
            t = time_to_minutes(b['Time'])
            mid = float(b.get('Midline', '0'))
            last = float(b.get('Last', '0'))
            if t == 600:  # 10:00
                ds.midline_at_1000 = mid
            if t == 630:  # 10:30
                ds.midline_at_1030 = mid
                ds.last_at_1030 = last

        # IB2 first break direction and timing
        # Look at RTH bars after 10:30 for when IB2 rays break
        ib2_high_first_break = None
        ib2_low_first_break = None

        prev_hb = 0
        prev_lb = 0
        for b in rth_bars:
            t_min = time_to_minutes(b['Time'])
            hb = int(float(b.get('IB2 High Ray Broken', '0')))
            lb = int(float(b.get('IB2 Low Ray Broken', '0')))

            if hb > prev_hb and ib2_high_first_break is None:
                ib2_high_first_break = t_min
                ds.ib2_high_break_time = b['Time']
            if lb > prev_lb and ib2_low_first_break is None:
                ib2_low_first_break = t_min
                ds.ib2_low_break_time = b['Time']

            prev_hb = hb
            prev_lb = lb

        if ib2_high_first_break is not None and ib2_low_first_break is not None:
            if ib2_high_first_break < ib2_low_first_break:
                ds.ib2_first_break = 'HIGH'
            elif ib2_low_first_break < ib2_high_first_break:
                ds.ib2_first_break = 'LOW'
            else:
                ds.ib2_first_break = 'HIGH'  # tie goes to high
            ds.ib2_both_broken_time = max(ib2_high_first_break, ib2_low_first_break)
        elif ib2_high_first_break is not None:
            ds.ib2_first_break = 'HIGH'
        elif ib2_low_first_break is not None:
            ds.ib2_first_break = 'LOW'

        # IB1 both broken timing
        prev_hb = 0
        prev_lb = 0
        ib1_high_first_break = None
        ib1_low_first_break = None
        for b in rth_bars:
            t_min = time_to_minutes(b['Time'])
            hb = int(float(b.get('IB1 High Ray Broken', '0')))
            lb = int(float(b.get('IB1 Low Ray Broken', '0')))
            if hb > prev_hb and ib1_high_first_break is None:
                ib1_high_first_break = t_min
            if lb > prev_lb and ib1_low_first_break is None:
                ib1_low_first_break = t_min
            prev_hb = hb
            prev_lb = lb

        if ib1_high_first_break is not None and ib1_low_first_break is not None:
            ds.ib1_both_broken_time = max(ib1_high_first_break, ib1_low_first_break)

        # Skip invalid days
        if ds.ib1_high <= 0 or ds.ib2_high <= 0:
            continue

        all_days.append(ds)

# Deduplicate: when multiple contracts cover the same date, keep only one.
# Contract ordering by expiry: Z < H+1 < M+1 < U+1.
# We pick the contract with more RTH bars on that date (front month has more liquidity).
# Ties: pick the later expiry (it's the front month during rollover).
EXPIRY_ORDER = {
    'Z1': 1, 'H2': 2, 'M2': 3, 'U2': 4,
    'Z2': 5, 'H3': 6, 'M3': 7, 'U3': 8,
    'Z3': 9, 'H4': 10, 'M4': 11, 'U4': 12,
    'Z4': 13, 'H5': 14, 'M5': 15, 'U5': 16,
    'Z5': 17, 'H6': 18,
}

date_to_days = defaultdict(list)
for d in all_days:
    date_to_days[d.date].append(d)

deduped = []
for dt, day_list in date_to_days.items():
    if len(day_list) == 1:
        deduped.append(day_list[0])
    else:
        # Pick later expiry (higher order = front month during rollover)
        day_list.sort(key=lambda x: EXPIRY_ORDER.get(x.contract, 99), reverse=True)
        deduped.append(day_list[0])

all_days = sorted(deduped, key=lambda x: x.date)

print(f"Total valid days (after dedup): {len(all_days)}")
print(f"Removed {len(deduped)} - {len(all_days)} = 0 dupes? No, removed {len(date_to_days)} dates from {sum(len(v) for v in date_to_days.values())} entries")
print(f"Contracts: {len(FILES)}")

# ---- Analysis ----

def pct(n, d):
    if d == 0:
        return "0.0%"
    return f"{100*n/d:.1f}%"

def pct_val(n, d):
    if d == 0:
        return 0.0
    return 100*n/d

def slope_category(ds):
    """Midline slope = (midline at 10:30 - midline at 10:00) / 6 bars"""
    if ds.midline_at_1000 <= 0 or ds.midline_at_1030 <= 0:
        return None
    slope = (ds.midline_at_1030 - ds.midline_at_1000) / 6.0
    if slope > SLOPE_THRESHOLD:
        return 'UP'
    elif slope < -SLOPE_THRESHOLD:
        return 'DOWN'
    else:
        return 'FLAT'

def ib1_outcome(ds):
    both = ds.ib1_high_broken and ds.ib1_low_broken
    high_only = ds.ib1_high_broken and not ds.ib1_low_broken
    low_only = not ds.ib1_high_broken and ds.ib1_low_broken
    neither = not ds.ib1_high_broken and not ds.ib1_low_broken
    if both:
        return 'BOTH'
    elif high_only:
        return 'HIGH_ONLY'
    elif low_only:
        return 'LOW_ONLY'
    else:
        return 'NEITHER'

def ib2_outcome(ds):
    both = ds.ib2_high_broken and ds.ib2_low_broken
    high_only = ds.ib2_high_broken and not ds.ib2_low_broken
    low_only = not ds.ib2_high_broken and ds.ib2_low_broken
    neither = not ds.ib2_high_broken and not ds.ib2_low_broken
    if both:
        return 'BOTH'
    elif high_only:
        return 'HIGH_ONLY'
    elif low_only:
        return 'LOW_ONLY'
    else:
        return 'NEITHER'

def price_vs_midline(ds):
    if ds.midline_at_1030 <= 0 or ds.last_at_1030 <= 0:
        return None
    if ds.last_at_1030 > ds.midline_at_1030:
        return 'ABOVE'
    else:
        return 'BELOW'

def is_cal(ds):
    return CAL_START <= ds.date <= CAL_END

def is_ho(ds):
    return HO_START <= ds.date <= HO_END

# ======== Section 1: IB2 Overall Stats ========
total = len(all_days)
ib2_one_side = sum(1 for d in all_days if (d.ib2_high_broken != d.ib2_low_broken))
ib2_both = sum(1 for d in all_days if (d.ib2_high_broken and d.ib2_low_broken))
ib2_neither = sum(1 for d in all_days if (not d.ib2_high_broken and not d.ib2_low_broken))

sec1 = f"""## 1. IB2 Overall Stats ({total} valid days)

- IB2 one-side holds: {ib2_one_side}/{total} ({pct(ib2_one_side, total)})
- IB2 both broken: {ib2_both}/{total} ({pct(ib2_both, total)})
- IB2 neither broken: {ib2_neither}/{total} ({pct(ib2_neither, total)})
"""

# ======== Section 2: IB1 Break Stats ========
ib1_counts = Counter(ib1_outcome(d) for d in all_days)
sec2 = f"""## 2. IB1 Break Stats ({total} days)

| IB1 Outcome | Days | % |
|-------------|------|---|
| Both broken | {ib1_counts['BOTH']} | {pct(ib1_counts['BOTH'], total)} |
| High only | {ib1_counts['HIGH_ONLY']} | {pct(ib1_counts['HIGH_ONLY'], total)} |
| Low only | {ib1_counts['LOW_ONLY']} | {pct(ib1_counts['LOW_ONLY'], total)} |
| Neither | {ib1_counts['NEITHER']} | {pct(ib1_counts['NEITHER'], total)} |
"""

# ======== Section 3: IB1 -> IB2 Cross Analysis ========
# When IB1 both break: what happens to IB2?
ib1_both_days = [d for d in all_days if ib1_outcome(d) == 'BOTH']
ib1_one_side_days = [d for d in all_days if ib1_outcome(d) in ('HIGH_ONLY', 'LOW_ONLY')]

ib2_both_when_ib1_both = sum(1 for d in ib1_both_days if ib2_outcome(d) == 'BOTH')
ib2_one_when_ib1_both = sum(1 for d in ib1_both_days if ib2_outcome(d) in ('HIGH_ONLY', 'LOW_ONLY'))
ib2_neither_when_ib1_both = sum(1 for d in ib1_both_days if ib2_outcome(d) == 'NEITHER')

ib2_both_when_ib1_one = sum(1 for d in ib1_one_side_days if ib2_outcome(d) == 'BOTH')
ib2_one_when_ib1_one = sum(1 for d in ib1_one_side_days if ib2_outcome(d) in ('HIGH_ONLY', 'LOW_ONLY'))
ib2_neither_when_ib1_one = sum(1 for d in ib1_one_side_days if ib2_outcome(d) == 'NEITHER')

sec3 = f"""## 3. IB1 -> IB2 Cross Analysis

### When IB1 breaks BOTH sides ({len(ib1_both_days)} days)

| IB2 Outcome | Days | % |
|-------------|------|---|
| Both broken | {ib2_both_when_ib1_both} | {pct(ib2_both_when_ib1_both, len(ib1_both_days))} |
| One-side holds | {ib2_one_when_ib1_both} | {pct(ib2_one_when_ib1_both, len(ib1_both_days))} |
| Neither broken | {ib2_neither_when_ib1_both} | {pct(ib2_neither_when_ib1_both, len(ib1_both_days))} |

### When IB1 breaks ONE side only ({len(ib1_one_side_days)} days)

| IB2 Outcome | Days | % |
|-------------|------|---|
| Both broken | {ib2_both_when_ib1_one} | {pct(ib2_both_when_ib1_one, len(ib1_one_side_days))} |
| One-side holds | {ib2_one_when_ib1_one} | {pct(ib2_one_when_ib1_one, len(ib1_one_side_days))} |
| Neither broken | {ib2_neither_when_ib1_one} | {pct(ib2_neither_when_ib1_one, len(ib1_one_side_days))} |
"""

# ======== Section 4: Combined Signal ========
# IB1 outcome x slope -> IB2 first break
signal_buckets = defaultdict(lambda: {'days': 0, 'high_first': 0, 'low_first': 0, 'one_side': 0})

for d in all_days:
    ib1 = ib1_outcome(d)
    slope = slope_category(d)
    if slope is None:
        continue
    fb = d.ib2_first_break
    if fb is None:
        continue

    key = (ib1, slope)
    signal_buckets[key]['days'] += 1
    if fb == 'HIGH':
        signal_buckets[key]['high_first'] += 1
    else:
        signal_buckets[key]['low_first'] += 1

    ib2o = ib2_outcome(d)
    if ib2o in ('HIGH_ONLY', 'LOW_ONLY', 'NEITHER'):
        signal_buckets[key]['one_side'] += 1

# Build table for one-side IB1
one_side_rows = []
both_side_rows = []

for (ib1, slope), stats in sorted(signal_buckets.items()):
    days = stats['days']
    if days < 3:
        continue

    if stats['high_first'] >= stats['low_first']:
        direction = 'HIGH first'
        correct = stats['high_first']
    else:
        direction = 'LOW first'
        correct = stats['low_first']

    # Determine expected direction based on IB1 outcome
    if ib1 == 'HIGH_ONLY':
        direction = 'HIGH first'
        correct = stats['high_first']
    elif ib1 == 'LOW_ONLY':
        direction = 'LOW first'
        correct = stats['low_first']

    acc = pct_val(correct, days)
    one_side_rate = pct_val(stats['one_side'], days)

    ib1_label = {'BOTH': 'IB1 BOTH', 'HIGH_ONLY': 'IB1 HIGH only', 'LOW_ONLY': 'IB1 LOW only', 'NEITHER': 'IB1 NEITHER'}.get(ib1, ib1)

    row = f"| {ib1_label} + {slope} | {days} | {direction} | {acc:.0f}% | {one_side_rate:.0f}% |"

    if ib1 in ('HIGH_ONLY', 'LOW_ONLY'):
        one_side_rows.append((acc, row))
    elif ib1 == 'BOTH':
        both_side_rows.append((acc, row))

one_side_rows.sort(key=lambda x: -x[0])
both_side_rows.sort(key=lambda x: -x[0])

sec4 = """## 4. Combined Signal: IB1 + Midline Slope -> IB2 First Break

Midline slope = (midline at 10:30 - midline at 10:00) / 6 bars.
Slope threshold: +/-0.25 pts/bar (UP/FLAT/DOWN).
All signals known by 10:30 (before IB2 breaks).

### When IB1 breaks ONE side only (highest accuracy)

| Signal | Days | IB2 First Break | Accuracy | One-Side Holds |
|--------|------|----------------|----------|---------------|
"""
for _, row in one_side_rows:
    sec4 += row + "\n"

sec4 += """
### When IB1 breaks BOTH sides

| Signal | Days | IB2 First Break | Accuracy | One-Side Holds |
|--------|------|----------------|----------|---------------|
"""
for _, row in both_side_rows:
    sec4 += row + "\n"

# ======== Section 5: Price vs Midline ========
# Standalone
above_days = [d for d in all_days if price_vs_midline(d) == 'ABOVE' and d.ib2_first_break is not None]
below_days = [d for d in all_days if price_vs_midline(d) == 'BELOW' and d.ib2_first_break is not None]

above_high_first = sum(1 for d in above_days if d.ib2_first_break == 'HIGH')
below_low_first = sum(1 for d in below_days if d.ib2_first_break == 'LOW')

above_one_side = sum(1 for d in above_days if ib2_outcome(d) in ('HIGH_ONLY', 'LOW_ONLY', 'NEITHER'))
below_one_side = sum(1 for d in below_days if ib2_outcome(d) in ('HIGH_ONLY', 'LOW_ONLY', 'NEITHER'))

sec5 = f"""## 5. Price vs Midline Analysis

### Standalone Signal

| Price Position at IB2 End | Days | IB2 Same Direction | One-Side Holds |
|--------------------------|------|-------------------|---------------|
| ABOVE midline | {len(above_days)} | {pct(above_high_first, len(above_days))} HIGH first | {pct(above_one_side, len(above_days))} |
| BELOW midline | {len(below_days)} | {pct(below_low_first, len(below_days))} LOW first | {pct(below_one_side, len(below_days))} |

### Triple Signal: IB1 + Slope + Price vs Midline -> IB2 First Break

"""

# Triple signal
triple_buckets = defaultdict(lambda: {'days': 0, 'high_first': 0, 'low_first': 0, 'one_side': 0})

for d in all_days:
    ib1 = ib1_outcome(d)
    slope = slope_category(d)
    pvm = price_vs_midline(d)
    fb = d.ib2_first_break
    if slope is None or pvm is None or fb is None:
        continue

    key = (ib1, slope, pvm)
    triple_buckets[key]['days'] += 1
    if fb == 'HIGH':
        triple_buckets[key]['high_first'] += 1
    else:
        triple_buckets[key]['low_first'] += 1
    if ib2_outcome(d) in ('HIGH_ONLY', 'LOW_ONLY', 'NEITHER'):
        triple_buckets[key]['one_side'] += 1

triple_rows = []
for (ib1, slope, pvm), stats in triple_buckets.items():
    days = stats['days']
    if days < 5:
        continue

    ib1_label = {'BOTH': 'IB1 BOTH', 'HIGH_ONLY': 'IB1 HIGH only', 'LOW_ONLY': 'IB1 LOW only', 'NEITHER': 'IB1 NEITHER'}.get(ib1, ib1)

    if ib1 == 'HIGH_ONLY':
        direction = 'HIGH'
        correct = stats['high_first']
    elif ib1 == 'LOW_ONLY':
        direction = 'LOW'
        correct = stats['low_first']
    else:
        # For BOTH, pick majority
        if stats['high_first'] >= stats['low_first']:
            direction = 'HIGH'
            correct = stats['high_first']
        else:
            direction = 'LOW'
            correct = stats['low_first']

    acc = pct_val(correct, days)
    one_side_rate = pct_val(stats['one_side'], days)

    triple_rows.append((acc, days, f"| {ib1_label} + {slope} + {pvm} | {days} | {direction} | {acc:.0f}% | {one_side_rate:.0f}% |"))

triple_rows.sort(key=lambda x: (-x[0], -x[1]))

sec5 += """Top triple signals (min 5 days, sorted by accuracy):

| Signal | Days | Direction | Accuracy | One-Side |
|--------|------|-----------|----------|----------|
"""
for _, _, row in triple_rows:
    sec5 += row + "\n"

# ======== Section 6: Per-Contract Consistency ========
# Top 2 signals from section 4: IB1 LOW only + DOWN, IB1 HIGH only + UP
contract_stats = defaultdict(lambda: {
    'total_days': 0,
    'lo_dn_days': 0, 'lo_dn_correct': 0,
    'ho_up_days': 0, 'ho_up_correct': 0,
})

for d in all_days:
    c = d.contract
    contract_stats[c]['total_days'] += 1

    ib1 = ib1_outcome(d)
    slope = slope_category(d)
    fb = d.ib2_first_break
    if slope is None or fb is None:
        continue

    if ib1 == 'LOW_ONLY' and slope == 'DOWN':
        contract_stats[c]['lo_dn_days'] += 1
        if fb == 'LOW':
            contract_stats[c]['lo_dn_correct'] += 1

    if ib1 == 'HIGH_ONLY' and slope == 'UP':
        contract_stats[c]['ho_up_days'] += 1
        if fb == 'HIGH':
            contract_stats[c]['ho_up_correct'] += 1

sec6 = """## 6. Per-Contract Consistency (top 2 signals)

| Contract | Days | LO+DN Days | LO+DN Acc | HO+UP Days | HO+UP Acc |
|----------|------|-----------|-----------|-----------|-----------|
"""

for c in sorted(contract_stats.keys()):
    s = contract_stats[c]
    lo_acc = pct(s['lo_dn_correct'], s['lo_dn_days']) if s['lo_dn_days'] > 0 else "N/A"
    ho_acc = pct(s['ho_up_correct'], s['ho_up_days']) if s['ho_up_days'] > 0 else "N/A"
    sec6 += f"| {c} | {s['total_days']} | {s['lo_dn_days']} | {lo_acc} | {s['ho_up_days']} | {ho_acc} |\n"

# ======== Section 7: IB2 Break Timing ========
# When both break, distribution of second break time
ib2_both_days = [d for d in all_days if d.ib2_high_broken and d.ib2_low_broken and d.ib2_both_broken_time is not None]
ib1_both_timing_days = [d for d in all_days if d.ib1_high_broken and d.ib1_low_broken and d.ib1_both_broken_time is not None]

# Bucket by hour
hour_dist = Counter()
for d in ib2_both_days:
    hour = d.ib2_both_broken_time // 60
    hour_dist[hour] += 1

sec7 = f"""## 7. IB2 Break Timing

### When IB2 both sides break ({len(ib2_both_days)} days), second break time distribution

| Hour | Days | % |
|------|------|---|
"""
for h in sorted(hour_dist.keys()):
    sec7 += f"| {h:02d}:00-{h:02d}:59 | {hour_dist[h]} | {pct(hour_dist[h], len(ib2_both_days))} |\n"

# Median both-broken time
if ib2_both_days:
    times = sorted(d.ib2_both_broken_time for d in ib2_both_days)
    median_ib2 = times[len(times)//2]
    median_ib2_str = f"{median_ib2//60:02d}:{median_ib2%60:02d}"
else:
    median_ib2_str = "N/A"

if ib1_both_timing_days:
    times1 = sorted(d.ib1_both_broken_time for d in ib1_both_timing_days)
    median_ib1 = times1[len(times1)//2]
    median_ib1_str = f"{median_ib1//60:02d}:{median_ib1%60:02d}"
else:
    median_ib1_str = "N/A"

sec7 += f"""
- Median IB2 both-broken time: {median_ib2_str}
- Median IB1 both-broken time: {median_ib1_str}
"""

# ======== Section 8: Holdout Validation ========
cal_days = [d for d in all_days if is_cal(d)]
ho_days = [d for d in all_days if is_ho(d)]

# Top signals to validate
signals_to_test = [
    ('IB1 LOW only + DOWN', lambda d: ib1_outcome(d) == 'LOW_ONLY' and slope_category(d) == 'DOWN', 'LOW'),
    ('IB1 HIGH only + UP', lambda d: ib1_outcome(d) == 'HIGH_ONLY' and slope_category(d) == 'UP', 'HIGH'),
    ('IB1 HIGH only + FLAT', lambda d: ib1_outcome(d) == 'HIGH_ONLY' and slope_category(d) == 'FLAT', 'HIGH'),
    ('IB1 LOW only + FLAT', lambda d: ib1_outcome(d) == 'LOW_ONLY' and slope_category(d) == 'FLAT', 'LOW'),
    ('IB1 BOTH + UP', lambda d: ib1_outcome(d) == 'BOTH' and slope_category(d) == 'UP', 'HIGH'),
    ('IB1 BOTH + DOWN', lambda d: ib1_outcome(d) == 'BOTH' and slope_category(d) == 'DOWN', 'LOW'),
    ('IB1 BOTH + FLAT', lambda d: ib1_outcome(d) == 'BOTH' and slope_category(d) == 'FLAT', 'LOW'),
]

# Also triple signals
triple_signals_to_test = [
    ('IB1 HIGH only + UP + ABOVE', lambda d: ib1_outcome(d) == 'HIGH_ONLY' and slope_category(d) == 'UP' and price_vs_midline(d) == 'ABOVE', 'HIGH'),
    ('IB1 LOW only + DOWN + BELOW', lambda d: ib1_outcome(d) == 'LOW_ONLY' and slope_category(d) == 'DOWN' and price_vs_midline(d) == 'BELOW', 'LOW'),
    ('IB1 BOTH + FLAT + BELOW', lambda d: ib1_outcome(d) == 'BOTH' and slope_category(d) == 'FLAT' and price_vs_midline(d) == 'BELOW', 'LOW'),
    ('IB1 BOTH + FLAT + ABOVE', lambda d: ib1_outcome(d) == 'BOTH' and slope_category(d) == 'FLAT' and price_vs_midline(d) == 'ABOVE', 'HIGH'),
]

def eval_signal(days_list, cond_fn, expected_dir):
    matching = [d for d in days_list if d.ib2_first_break is not None and cond_fn(d)]
    if not matching:
        return 0, 0, 0.0, 0.0
    correct = sum(1 for d in matching if d.ib2_first_break == expected_dir)
    one_side = sum(1 for d in matching if ib2_outcome(d) in ('HIGH_ONLY', 'LOW_ONLY', 'NEITHER'))
    return len(matching), correct, pct_val(correct, len(matching)), pct_val(one_side, len(matching))

sec8 = f"""## 8. Holdout Validation

Calibration: {CAL_START.strftime('%Y-%m-%d')} to {CAL_END.strftime('%Y-%m-%d')} ({len(cal_days)} days)
Holdout: {HO_START.strftime('%Y-%m-%d')} to {HO_END.strftime('%Y-%m-%d')} ({len(ho_days)} days)

### Double Signals (IB1 + Slope)

| Signal | Cal Days | Cal Acc | Cal 1-Side | HO Days | HO Acc | HO 1-Side |
|--------|----------|---------|-----------|---------|--------|-----------|
"""

for name, cond, exp_dir in signals_to_test:
    c_n, c_corr, c_acc, c_os = eval_signal(cal_days, cond, exp_dir)
    h_n, h_corr, h_acc, h_os = eval_signal(ho_days, cond, exp_dir)
    sec8 += f"| {name} | {c_n} | {c_acc:.0f}% | {c_os:.0f}% | {h_n} | {h_acc:.0f}% | {h_os:.0f}% |\n"

sec8 += """
### Triple Signals (IB1 + Slope + Price vs Midline)

| Signal | Cal Days | Cal Acc | Cal 1-Side | HO Days | HO Acc | HO 1-Side |
|--------|----------|---------|-----------|---------|--------|-----------|
"""

for name, cond, exp_dir in triple_signals_to_test:
    c_n, c_corr, c_acc, c_os = eval_signal(cal_days, cond, exp_dir)
    h_n, h_corr, h_acc, h_os = eval_signal(ho_days, cond, exp_dir)
    sec8 += f"| {name} | {c_n} | {c_acc:.0f}% | {c_os:.0f}% | {h_n} | {h_acc:.0f}% | {h_os:.0f}% |\n"

# ======== Assemble Document ========
contracts_list = sorted(set(d.contract for d in all_days))

doc = f"""# Initial Balance Analysis — Research Findings

Date: 2026-04-03
Data: {len(FILES)} NQ contracts ({', '.join(sorted(get_contract_from_path(f) for f in FILES))})
Total: {total} valid trading days (IB1 High > 0 AND IB2 High > 0)
IB1: 08:30-09:30 ET, IB2: 09:30-10:30 ET
Midline: 160-bar rolling mean on 5-min chart
Slope: (midline at 10:30 - midline at 10:00) / 6 bars
Slope threshold: +/-0.25 pts/bar (UP/FLAT/DOWN)
RTH: 09:30-16:00

**Disclaimer:** All percentages reported are observed frequencies from
historical data, not statistical probabilities. Past frequencies do not
guarantee future outcomes.

---

{sec1}
---

{sec2}
---

{sec3}
---

{sec4}
---

{sec5}
---

{sec6}
---

{sec7}
---

{sec8}
---

## Data Files

IB data per contract: data/NQ-ib-5min-[contract].csv
IB study: lab/utility-NQ-study-ib-box.cpp
Period config: _config/period-config.md

---

## Open Questions

- Can these signals be integrated with the rangefade rotation strategy?
- Should IB break direction influence the rangefade's directional bias?
- Is there value in an IB3 period (10:30-11:30)?
- Can the midline slope threshold be optimized further?
"""

# Write findings
output_path = "c:/Projects/futures_pipeline/lab/output/ib-analysis-findings.md"
with open(output_path, 'w') as f:
    f.write(doc)

print(f"\nWrote findings to {output_path}")
print(f"\n--- PREVIEW ---")
print(doc[:3000])
