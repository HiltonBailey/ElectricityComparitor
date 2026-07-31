import json
import csv
from collections import defaultdict
from datetime import datetime, timedelta, timezone

AEST = timezone(timedelta(hours=10))

def key_period_end(hh, mm):
    hh, mm = divmod(hh * 60 + mm + 4, 60)
    return f'{hh:02d}:{mm:02d}:59'

def bucket_from_epoch(epoch):
    dt = datetime.fromtimestamp(epoch - 1, tz=AEST)
    return f"{dt.strftime('%Y-%m-%d')} {dt.strftime('%H:%M:59')}"

def build_day(day, fox_rec, fox_raw, enphase_intervals):
    fox_hourly = fox_rec['hourly']
    total = float(fox_rec.get('total') or 0.0)
    fox_kwh = defaultdict(float)
    raw_buckets = defaultdict(float)
    for p in fox_raw:
        t = p['time']
        hh, mm = int(t[11:13]), int(t[14:16])
        gs = mm // 5 * 5
        b = f'{day} {key_period_end(hh, gs)}'
        raw_buckets[b] += p['value'] or 0
    enph_kwh = defaultdict(float)
    for iv in enphase_intervals:
        b = bucket_from_epoch(iv['end_at'])
        enph_kwh[b] += (iv.get('enwh') or 0) / 1000.0
    hour_vals = [float(fox_hourly.get(h + 1) or fox_hourly.get(str(h + 1)) or 0.0) for h in range(24)]
    data_sum = sum(hour_vals)
    scale = total / data_sum if data_sum > 0 else 1.0
    for h in range(0, 24):
        energy = hour_vals[h] * scale
        if energy <= 0:
            continue
        hour_buckets = [f'{day} {key_period_end(h, m)}' for m in range(0, 60, 5)]
        raw_sum = sum(raw_buckets.get(b, 0.0) for b in hour_buckets)
        enph_sum = sum(enph_kwh.get(b, 0.0) for b in hour_buckets)
        if raw_sum > 0:
            for b in hour_buckets:
                fox_kwh[b] += energy * (raw_buckets.get(b, 0.0) / raw_sum)
        elif enph_sum > 0:
            for b in hour_buckets:
                fox_kwh[b] += energy * (enph_kwh.get(b, 0.0) / enph_sum)
        else:
            for b in hour_buckets:
                fox_kwh[b] += energy / 12.0
    buckets = set(fox_kwh) | set(enph_kwh)
    out = {}
    cum = 0.0
    for b in sorted(buckets):
        cum += fox_kwh.get(b, 0.0) + enph_kwh.get(b, 0.0)
        out[b] = round(cum, 2)
    return out, sum(fox_kwh.values()), sum(enph_kwh.values())

def main():
    fox_hourly_all = json.load(open('Temp/fox_pvyield_hourly.json'))
    fox_raw_all = json.load(open('Temp/fox_pvpower_raw.json'))
    gap_days = ['2026-07-10','2026-07-11','2026-07-12','2026-07-13','2026-07-14','2026-07-15',
                '2026-07-16','2026-07-17','2026-07-18','2026-07-19',
                '2026-07-27','2026-07-28','2026-07-29']
    for day in gap_days:
        try:
            enphase = json.load(open(f'/tmp/enphase_micro_{day}.json'))['intervals']
        except Exception as e:
            print(day, 'ENPHASE ERR', e)
            continue
        filled, fox_tot, enph_tot = build_day(day, fox_hourly_all[day], fox_raw_all[day], enphase)
        report_tot = fox_hourly_all[day]['total']
        print(f'{day}: fox_report={report_tot:.2f} fox_alloc={fox_tot:.2f} enphase={enph_tot:.2f} total={fox_tot+enph_tot:.2f}')

if __name__ == '__main__':
    main()
