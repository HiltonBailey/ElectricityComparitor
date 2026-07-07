import csv
from datetime import datetime
from collections import defaultdict

import sys
# Set LIVE_PATH to either 5minelecNEW.csv (working file) or newseed.csv (seed backup)
LIVE_PATH = sys.argv[1] if len(sys.argv) > 1 else '/Volumes/share/file_notifications/5minelecNEW.csv'
HOURLY_PATH = '/Volumes/share/file_notifications/Hourly_Old_Data.csv'
OUT_PATH = LIVE_PATH + '.patched'

# 1. Parse hourly data: date -> {hour -> cumulative_solar_at_HH:59:59}
hourly_solar = {}
with open(HOURLY_PATH) as f:
    reader = csv.reader(f)
    header = next(reader)
    for row in reader:
        if len(row) < 2:
            continue
        dt_str = row[0].strip()
        if not dt_str:
            continue
        try:
            dt = datetime.strptime(dt_str, '%d/%m/%Y %H:%M:%S')
        except ValueError:
            continue
        date_key = dt.strftime('%Y-%m-%d')
        hour = dt.hour
        try:
            val = float(row[1])
        except (ValueError, IndexError):
            val = 0
        if date_key not in hourly_solar:
            hourly_solar[date_key] = {}
        hourly_solar[date_key][hour] = val

def get_hourly_delta(hourly_vals, hour):
    if hour not in hourly_vals or (hour - 1) not in hourly_vals:
        return 0
    return hourly_vals[hour] - hourly_vals[hour - 1]

# 2. Read live CSV, group by day, check solar
print('Reading live CSV...')
with open(LIVE_PATH) as f:
    reader = csv.reader(f)
    header = next(reader)
    all_rows = list(reader)

print(f'Total rows in live CSV: {len(all_rows)}')

# Group by day and compute total solar
day_groups = defaultdict(list)
for row in all_rows:
    if len(row) < 14:
        continue
    try:
        date_key = row[0][:10]
    except:
        continue
    day_groups[date_key].append(row)

# Find days with zero total solar that exist in hourly data
days_to_fill = []
stats = {'skipped_has_solar': 0, 'no_hourly_data': 0, 'to_fill': 0}

for date_key in sorted(day_groups.keys()):
    rows = day_groups[date_key]
    total_solar = 0.0
    for row in rows:
        try:
            total_solar += float(row[13])
        except ValueError:
            pass
    
    if total_solar > 0.001:  # has real solar data
        stats['skipped_has_solar'] += 1
        continue
    
    if date_key not in hourly_solar:
        stats['no_hourly_data'] += 1
        continue
    
    days_to_fill.append(date_key)
    stats['to_fill'] += 1

print(f'Days with existing solar: {stats["skipped_has_solar"]}')
print(f'Days with no hourly data: {stats["no_hourly_data"]}')
print(f'Days to fill: {len(days_to_fill)}')
if days_to_fill:
    print(f'Range: {days_to_fill[0]} to {days_to_fill[-1]}')

# 3. Fill solar (cumulative values)
total_solar_added = 0.0

for date_key in days_to_fill:
    rows = day_groups[date_key]
    hourly_vals = hourly_solar[date_key]
    
    # Sort rows by timestamp
    rows.sort(key=lambda r: r[0])
    
    # Count rows per hour so we can distribute delta correctly
    hour_row_counts = {}
    for row in rows:
        try:
            dt = datetime.strptime(row[0], '%Y-%m-%d %H:%M:%S')
        except ValueError:
            continue
        h = dt.hour
        if 9 <= h <= 17:
            hour_row_counts[h] = hour_row_counts.get(h, 0) + 1

    cumulative = 0.0
    prev_hour = -1
    day_total = 0.0
    row_idx = -1  # position within current hour (for tracking)
    rows_in_hour = 0
    
    for row in rows:
        try:
            dt = datetime.strptime(row[0], '%Y-%m-%d %H:%M:%S')
        except ValueError:
            continue
        hour = dt.hour
        
        if 9 <= hour <= 17:
            if hour != prev_hour:
                delta = get_hourly_delta(hourly_vals, hour)
                rows_in_hour = hour_row_counts.get(hour, 1)
                per_step = delta / rows_in_hour if rows_in_hour > 0 else 0
                prev_hour = hour
                row_idx = 0
            cumulative += per_step
            row_idx += 1
            row[13] = f'{cumulative:.6f}'
            day_total = cumulative
        elif hour < 9:
            row[13] = '0'
        else:
            row[13] = f'{cumulative:.6f}'
    
    total_solar_added += day_total
    print(f'  {date_key}: {len(rows)} rows, {day_total:.3f} total kWh')

# 4. Write back
print(f'\nWriting {len(all_rows)} rows to {OUT_PATH}...')
with open(OUT_PATH, 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(header)
    writer.writerows(all_rows)

print(f'Done. {len(days_to_fill)} days, {total_solar_added:.3f} kWh total solar added')
