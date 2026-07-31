import json
import csv
from collections import defaultdict
from datetime import datetime, timedelta, timezone, date

AEST = timezone(timedelta(hours=10))
FILL_DAYS = ['2026-07-10','2026-07-11','2026-07-12','2026-07-13','2026-07-14','2026-07-15',
             '2026-07-26','2026-07-27','2026-07-28','2026-07-29']
FOX_HA_OVERRIDE = {'2026-07-26': 14.0}
SRC_CSV = '/Volumes/share/file_notifications/5minelecNEW.csv'
OUT_CSV = 'Temp/5minelecNEW.solar-filled2.csv'


def key_period_end(hh, mm):
    hh, mm = divmod(hh * 60 + mm + 4, 60)
    return f'{hh:02d}:{mm:02d}:59'


def bucket_from_epoch(epoch):
    dt = datetime.fromtimestamp(epoch - 1, tz=AEST)
    return f"{dt.strftime('%Y-%m-%d')} {dt.strftime('%H:%M:59')}"


def enphase_lifetime_totals():
    d = json.load(open('/tmp/enphase_lifetime.json'))
    prod = d['production']
    d0 = date.fromisoformat(d['start_date'])
    out = {}
    for day in FILL_DAYS:
        i = (date.fromisoformat(day) - d0).days
        if 0 <= i < len(prod):
            out[day] = prod[i] / 1000.0
    return out


def build_day(day, fox_hourly, fox_total, fox_raw, micro, enphase_total):
    fox_kwh = defaultdict(float)
    raw_buckets = defaultdict(float)
    for p in fox_raw:
        t = p['time']
        hh, mm = int(t[11:13]), int(t[14:16])
        gs = mm // 5 * 5
        raw_buckets[f'{day} {key_period_end(hh, gs)}'] += p['value'] or 0
    enph_raw = defaultdict(float)
    for iv in micro:
        enph_raw[bucket_from_epoch(iv['end_at'])] += (iv.get('enwh') or 0) / 1000.0
    fox_hour_vals = [float(fox_hourly.get(h + 1) or fox_hourly.get(str(h + 1)) or 0.0) for h in range(24)]
    data_sum = sum(fox_hour_vals)
    scale = fox_total / data_sum if data_sum > 0 else 1.0
    for h in range(0, 24):
        energy = fox_hour_vals[h] * scale
        if energy <= 0:
            continue
        hour_buckets = [f'{day} {key_period_end(h, m)}' for m in range(0, 60, 5)]
        raw_sum = sum(raw_buckets.get(b, 0.0) for b in hour_buckets)
        enph_sum = sum(enph_raw.get(b, 0.0) for b in hour_buckets)
        if raw_sum > 0:
            for b in hour_buckets:
                fox_kwh[b] += energy * (raw_buckets.get(b, 0.0) / raw_sum)
        elif enph_sum > 0:
            for b in hour_buckets:
                fox_kwh[b] += energy * (enph_raw.get(b, 0.0) / enph_sum)
        else:
            for b in hour_buckets:
                fox_kwh[b] += energy / 12.0
    enph_sum = sum(enph_raw.values())
    enph_scale = enphase_total / enph_sum if enph_sum > 0 else 0.0
    enph_kwh = {b: v * enph_scale for b, v in enph_raw.items()}
    buckets = set(fox_kwh) | set(enph_kwh)
    if not buckets:
        buckets = {f'{day} {key_period_end(h, m)}' for h in range(24) for m in range(0, 60, 5)}
    out = {}
    cum = 0.0
    for b in sorted(buckets):
        cum += fox_kwh.get(b, 0.0) + enph_kwh.get(b, 0.0)
        out[b] = round(cum, 2)
    return out, sum(fox_kwh.values()), sum(enph_kwh.values())


def build_jul26(day, micro, combined_total):
    enph_raw = defaultdict(float)
    for iv in micro:
        enph_raw[bucket_from_epoch(iv['end_at'])] += (iv.get('enwh') or 0) / 1000.0
    s = sum(enph_raw.values())
    scale = combined_total / s if s > 0 else 0.0
    out = {}
    cum = 0.0
    for b in sorted(enph_raw):
        cum += enph_raw[b] * scale
        out[b] = round(cum, 2)
    return out


def main():
    fox_hourly_all = json.load(open('Temp/fox_pvyield_hourly.json'))
    fox_raw_all = json.load(open('Temp/fox_pvpower_raw.json'))
    eph_totals = enphase_lifetime_totals()

    fills = {}
    for day in FILL_DAYS:
        micro = json.load(open(f'/tmp/enphase_micro_{day}.json'))['intervals']
        if day == '2026-07-26':
            t_fox = FOX_HA_OVERRIDE[day]
            t_eph = eph_totals[day]
            filled = build_jul26(day, micro, t_fox + t_eph)
            fills[day] = filled
            print(f'{day}: fox={t_fox:.2f} enphase={t_eph:.2f} total={t_fox+t_eph:.2f} final={sorted(filled.values())[-1]:.2f} keys={len(filled)}')
            continue
        t_fox = float(fox_hourly_all[day]['total'])
        t_eph = eph_totals[day]
        filled, fox_alloc, enph_alloc = build_day(day, fox_hourly_all[day]['hourly'], t_fox, fox_raw_all[day], micro, t_eph)
        fills[day] = filled
        print(f'{day}: fox_report={t_fox:.2f} fox_alloc={fox_alloc:.2f} enphase={t_eph:.2f} (micro={enph_alloc:.2f}) total={fox_alloc+enph_alloc:.2f} final={sorted(filled.values())[-1]:.2f} keys={len(filled)}')

    with open(SRC_CSV) as f:
        rows = list(csv.reader(f))
    hdr = rows[0]
    solar_idx = hdr.index('solar_gen')
    updated = 0
    changed = defaultdict(set)
    for row in rows[1:]:
        dt = row[0]
        day = dt[:10]
        if day in fills:
            b = dt
            if b in fills[day]:
                row[solar_idx] = f"{fills[day][b]:.2f}"
                changed[day].add(b)
                updated += 1
    with open(OUT_CSV, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerows(rows)
    print(f'\nupdated rows: {updated}')
    for day in FILL_DAYS:
        print(f'{day}: {len(changed.get(day, set()))} rows filled')
    json.dump(fills, open('Temp/solar_gen_fill2.json', 'w'), indent=1)


if __name__ == '__main__':
    main()
