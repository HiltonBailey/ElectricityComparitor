import csv
import sys
from datetime import datetime, timedelta
from collections import defaultdict

RAW = sys.argv[1] if len(sys.argv) > 1 else 'Temp/foxess_solar_gap_raw.csv'
OUT = 'Temp/foxess_solar_5min.csv'

def parse_ts(s):
    s = s.strip()
    return datetime.strptime(s[:19], '%Y-%m-%d %H:%M:%S')

pts = []  # (ts, kw)
with open(RAW) as f:
    r = csv.DictReader(f)
    for row in r:
        if row['variable'] != 'generationPower':
            continue
        try:
            ts = parse_ts(row['timestamp'])
            kw = float(row['value'])
        except Exception:
            continue
        pts.append((ts, kw))
pts.sort()

if not pts:
    print("No points found")
    sys.exit(1)

print(f"raw points: {len(pts)}  span {pts[0][0]} -> {pts[-1][0]}")

buckets = defaultdict(list)  # period_end -> list of kw
for ts, kw in pts:
    pe = ts.replace(second=59, microsecond=0)
    buckets[pe].append(kw)

with open(OUT, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['pe_datetime', 'solar_gen'])
    for pe in sorted(buckets):
        vals = buckets[pe]
        kw_avg = sum(vals) / len(vals)
        kwh = kw_avg * (5.0 / 60.0)
        w.writerow([pe.strftime('%Y-%m-%d %H:%M:%S'), round(kwh, 4)])

print(f"Wrote {OUT}: {len(buckets)} period-end buckets")
