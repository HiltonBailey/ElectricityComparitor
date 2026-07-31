#!/usr/bin/env python3
"""Fill AEMO price gaps in 5minelecNEW.csv from HA sensor history export.

The aemo-history.csv records sensor.aemo_nemweb_nsw1_realtime_price with
last_changed in UTC. The AEMO settlement interval is +10:00 local time;
the HA write-time drifts within each 5-min window, so the settlement is the
next 5-min boundary (ceil) of (last_changed + 10h). CSV pe_datetime is the
period-END (:59), i.e. settlement - 1s.

Usage: python3 fill_aemo_ha.py [--csv <path>] [--hist <path>] [--days YYYY-MM-DD,...]
"""
import csv
import sys
from datetime import datetime, timedelta

TARGET_CSV = "/Users/hiltondbailey/repos/ElectricityComparitor/5minelecNEW.csv"
HIST_CSV = "/Users/hiltondbailey/repos/ElectricityComparitor/aemo-history.csv"


def load_ha_history(path):
    hist = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            lc = r.get("last_changed", "").strip()
            st = r.get("state", "").strip()
            if not lc or not st:
                continue
            try:
                ts = datetime.strptime(lc, "%Y-%m-%dT%H:%M:%S.%fZ")
            except ValueError:
                try:
                    ts = datetime.strptime(lc, "%Y-%m-%dT%H:%M:%SZ")
                except ValueError:
                    continue
            try:
                val = float(st)
            except ValueError:
                continue
            hist.append((ts, val))
    hist.sort()
    return hist


def build_price_map(hist, days=None):
    def ceil_5min(dt):
        m = dt.minute
        nxt = ((m // 5) + 1) * 5
        if nxt >= 60:
            return dt.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        return dt.replace(minute=nxt, second=0, microsecond=0)

    price_map = {}
    for ts_utc, val in hist:
        local = ts_utc + timedelta(hours=10)
        if days is not None and local.strftime("%Y-%m-%d") not in days:
            continue
        sett = ceil_5min(local)
        pe = (sett - timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S")
        price_map[pe] = val
    return price_map


def fill(target_csv, hist_csv, days):
    hist = load_ha_history(hist_csv)
    price_map = build_price_map(hist, days)
    print(f"Price map: {len(price_map)} entries for days {sorted(days) if days else 'all'}")

    rows_updated = 0
    rows_skipped = 0
    rows_no_match = 0
    rows = []
    with open(target_csv, newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows.append(header)
        pe_col = header.index("pe_datetime")
        aemo_col = header.index("aemo_price")
        for row in reader:
            pe = row[pe_col].strip()
            if pe in price_map:
                new_val = round(price_map[pe], 4)
                old_val = row[aemo_col].strip()
                try:
                    old_f = float(old_val)
                except ValueError:
                    old_f = 0.0
                if abs(old_f - new_val) > 0.00005:
                    row[aemo_col] = str(new_val)
                    rows_updated += 1
                else:
                    rows_skipped += 1
            else:
                if days is None or any(pe.startswith(d) for d in days):
                    rows_no_match += 1
            rows.append(row)

    with open(target_csv, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(rows)

    print(f"Rows updated: {rows_updated}")
    print(f"Rows skipped (already correct): {rows_skipped}")
    print(f"Rows in target days with no HA match: {rows_no_match}")
    return rows_updated


def main():
    target = TARGET_CSV
    hist_path = HIST_CSV
    days = None
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--csv" and i + 1 < len(args):
            target = args[i + 1]; i += 2
        elif args[i] == "--hist" and i + 1 < len(args):
            hist_path = args[i + 1]; i += 2
        elif args[i] == "--days" and i + 1 < len(args):
            days = set(args[i + 1].split(",")); i += 2
        else:
            i += 1
    fill(target, hist_path, days)


if __name__ == "__main__":
    main()
