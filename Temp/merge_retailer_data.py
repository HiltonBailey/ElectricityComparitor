#!/usr/bin/env python3
"""Merge retailer-provided meter data (B1 export, E1 consumption) into 5minelecNEW.csv,
then optionally fetch AEMO NSW1 5-min RRP prices and update solar_gen from a CSV.

Maps:
  B1 (Solar) 5-min interval kWh -> export (col 4), cumulative within-day
  E1 (Consumption) 5-min interval kWh -> Import_kWh (col 14), cumulative within-day

Options:
  --solar-gen <path>   update solar_gen (col 13) from a CSV with columns datetime,solar_gen
  --skip-aemo          skip the AEMO price fetch step
  --dry-run            validate only, don't write
"""

import csv
import io
import os
import subprocess
import sys
import tempfile
import zipfile
from collections import defaultdict
from datetime import datetime, timedelta

RETAILER_CSV = ""
TARGET_CSV = "/Users/hiltondbailey/repos/ElectricityComparitor/5minelecNEW.csv"
BACKUP_SUFFIX = ".bak"
AEMO_BASE = "https://nemweb.com.au/Reports/ARCHIVE/DispatchIS_Reports/PUBLIC_DISPATCHIS_{}.zip"
CACHE_DIR = "/tmp/aemo_cache"
os.makedirs(CACHE_DIR, exist_ok=True)


def time_header_to_period_end(th):
    parts = th.strip().split(':')
    h = int(parts[0])
    m = int(parts[1])
    end_m = m + 4
    end_h = h
    if end_m >= 60:
        end_m -= 60
        end_h += 1
    return f"{end_h:02d}:{end_m:02d}:59"


def parse_retailer_csv(filepath):
    import csv as csv_mod
    with open(filepath, 'r') as f:
        lines = f.readlines()
    streams = {}
    current_stream = None
    data_start_line = None
    for i, line in enumerate(lines):
        line_stripped = line.strip()
        if not line_stripped:
            continue
        if line_stripped.startswith('Stream ID'):
            reader = csv_mod.reader([line_stripped])
            row = next(reader)
            stream_id = row[2]
            current_stream = stream_id
            streams[current_stream] = {'headers': None, 'data': {}}
            data_start_line = None
            continue
        if current_stream is None:
            continue
        if line_stripped.startswith('LOCAL TIME') or line_stripped.startswith('Total for Period'):
            continue
        if line_stripped.startswith('Date/Time'):
            cols = line_stripped.split(',')
            time_headers = cols[1:289]
            streams[current_stream]['headers'] = time_headers
            data_start_line = i
            continue
        if data_start_line is not None and i > data_start_line:
            cols = line_stripped.split(',')
            if len(cols) < 290:
                continue
            date_str = cols[0].strip()
            if not date_str.isdigit() or len(date_str) != 8:
                continue
            intervals = []
            for j in range(288):
                try:
                    val = float(cols[1 + j].strip())
                except (ValueError, IndexError):
                    val = 0.0
                intervals.append(val)
            streams[current_stream]['data'][date_str] = intervals
    return streams


def build_cumulative_map(streams):
    result = {}
    time_headers = streams.get('B1', {}).get('headers', [])
    if not time_headers:
        time_headers = streams.get('E1', {}).get('headers', [])
    b1_data = streams.get('B1', {}).get('data', {})
    e1_data = streams.get('E1', {}).get('data', {})
    all_dates = set(b1_data.keys()) | set(e1_data.keys())
    for date_str in sorted(all_dates):
        date_formatted = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
        result[date_str] = {}
        cum_export = 0.0
        cum_import = 0.0
        for j in range(288):
            pe_time = time_header_to_period_end(time_headers[j])
            pe_datetime = f"{date_formatted} {pe_time}"
            b1_val = b1_data.get(date_str, [0.0]*288)[j]
            e1_val = e1_data.get(date_str, [0.0]*288)[j]
            cum_export += b1_val
            cum_import += e1_val
            result[date_str][pe_datetime] = {
                'export': round(cum_export, 6),
                'Import_kWh': round(cum_import, 6),
            }
    return result


def merge_into_csv(target_path, cum_map, backup=True):
    if not os.path.exists(target_path):
        print(f"ERROR: Target CSV not found: {target_path}")
        return False
    with open(target_path, 'r') as f:
        lines = f.readlines()
    if not lines:
        print(f"ERROR: Target CSV is empty: {target_path}")
        return False
    header = lines[0].strip()
    header_cols = header.split(',')
    print(f"Header cols ({len(header_cols)}): {header_cols}")
    export_idx = 4
    import_kwh_idx = 14
    pe_datetime_col = 12
    lookup = {}
    for date_str, intervals in cum_map.items():
        for pe_dt, vals in intervals.items():
            lookup[pe_dt] = vals
    print(f"Built lookup with {len(lookup)} period-ending timestamps")
    updated_count = 0
    inserted_count = 0
    new_lines = [header + '\n']
    for line in lines[1:]:
        if not line.strip():
            new_lines.append(line)
            continue
        cols = line.strip().split(',')
        if len(cols) < 15:
            new_lines.append(line)
            continue
        pe_dt = cols[pe_datetime_col].strip()
        if pe_dt in lookup:
            vals = lookup[pe_dt]
            cols[export_idx] = str(vals['export'])
            cols[import_kwh_idx] = str(vals['Import_kWh'])
            updated_count += 1
            del lookup[pe_dt]
        new_lines.append(','.join(cols) + '\n')

    # Insert new rows for unmatched lookup entries
    if lookup:
        print(f"  {len(lookup)} new timestamps to insert")
        for pe_dt in sorted(lookup.keys()):
            vals = lookup[pe_dt]
            # datetime,offpeak,shoulder,peak,export,bat_charge,Bat_Charge_Energy,
            # Bat_Discharge_Energy,house_load,gen_price,fit_price,aemo_price,
            # pe_datetime,solar_gen,Import_kWh
            row = [
                pe_dt,          # datetime
                '0',            # offpeak
                '0',            # shoulder
                '0',            # peak
                str(vals['export']),     # export
                '0',            # bat_charge
                '0',            # Bat_Charge_Energy
                '0',            # Bat_Discharge_Energy
                '0',            # house_load
                '0',            # gen_price
                '0',            # fit_price
                '0',            # aemo_price
                pe_dt,          # pe_datetime
                '0',            # solar_gen
                str(vals['Import_kWh']), # Import_kWh
            ]
            new_lines.append(','.join(row) + '\n')
            inserted_count += 1

    # Sort all data rows by pe_datetime (col 12) to maintain chronological order
    if inserted_count > 0:
        data_lines = new_lines[1:]
        data_lines.sort(key=lambda l: l.split(',')[12].strip() if l.strip() else '')
        new_lines = [new_lines[0]] + data_lines

    print(f"Updated {updated_count} rows, inserted {inserted_count} new rows")
    if inserted_count > 0:
        print(f"  Inserted date range: {list(lookup.keys())[0][:10]} to {list(lookup.keys())[-1][:10]}")
    if backup:
        backup_path = target_path + BACKUP_SUFFIX
        with open(backup_path, 'w') as f:
            f.writelines(lines)
        print(f"Backup written to {backup_path}")
    with open(target_path, 'w') as f:
        f.writelines(new_lines)
    print(f"Updated CSV written to {target_path}")
    return True


def get_dates_from_csv(path):
    dates = set()
    with open(path, newline="") as f:
        reader = csv.reader(f)
        next(reader)
        for row in reader:
            dt_str = row[0].strip()
            if dt_str:
                dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
                dates.add(dt.date())
    return sorted(dates)


def download_daily_zip(date):
    date_str = date.strftime("%Y%m%d")
    cache_path = os.path.join(CACHE_DIR, f"PUBLIC_DISPATCHIS_{date_str}.zip")
    if os.path.exists(cache_path):
        print(f"  Using cached {cache_path}")
        return cache_path
    url = AEMO_BASE.format(date_str)
    print(f"  Downloading {url}...")
    try:
        result = subprocess.run(
            ["curl", "-sL", "--max-time", "60", url, "-o", cache_path],
            capture_output=True, text=True, timeout=120
        )
        if result.returncode != 0 or not os.path.exists(cache_path) or os.path.getsize(cache_path) == 0:
            print(f"  ERROR downloading: {result.stderr[:200]}")
            return None
        size = os.path.getsize(cache_path)
        print(f"  Saved {size} bytes")
        return cache_path
    except Exception as e:
        print(f"  ERROR: {e}")
        return None


def extract_nsw1_prices(daily_zip_path, target_date):
    prices = {}
    target_ymd = target_date.strftime("%Y%m%d")
    try:
        with zipfile.ZipFile(daily_zip_path) as outer:
            names = [n for n in outer.namelist() if target_ymd in n and n.endswith(".zip")]
            for name in names:
                inner_data = outer.read(name)
                try:
                    with zipfile.ZipFile(io.BytesIO(inner_data)) as inner:
                        csv_names = [n for n in inner.namelist() if n.endswith(".CSV")]
                        if not csv_names:
                            continue
                        csv_content = inner.read(csv_names[0]).decode("utf-8", errors="replace")
                except zipfile.BadZipFile:
                    continue
                for line in csv_content.splitlines():
                    if line.startswith("D,DISPATCH,PRICE"):
                        parts = line.split(",")
                        if len(parts) > 9:
                            region = parts[6].strip()
                            if region == "NSW1":
                                settlement_date = parts[4].strip().strip('"')
                                rrp = parts[9].strip()
                                try:
                                    rrp_val = float(rrp)
                                except ValueError:
                                    continue
                                try:
                                    dt = datetime.strptime(settlement_date, "%Y/%m/%d %H:%M:%S")
                                except ValueError:
                                    continue
                                prices[dt] = rrp_val
    except Exception as e:
        print(f"  ERROR processing {daily_zip_path}: {e}")
    return prices


def update_aemo_prices(path):
    print("\nFetching AEMO NSW1 5-min RRP prices...")
    dates = get_dates_from_csv(path)
    print(f"Found {len(dates)} unique dates: {dates[0]} to {dates[-1]}")
    all_prices = {}
    for i, date in enumerate(dates):
        print(f"[{i+1}/{len(dates)}] Processing {date}...")
        zip_path = download_daily_zip(date)
        if zip_path:
            day_prices = extract_nsw1_prices(zip_path, date)
            all_prices.update(day_prices)
            print(f"  Got {len(day_prices)} NSW1 prices for {date}")
        else:
            print(f"  SKIPPING {date} - download failed")
    print(f"\nTotal unique AEMO prices fetched: {len(all_prices)}")
    if not all_prices:
        print("ERROR: No prices fetched. Skipping AEMO update.")
        return False

    print("Updating aemo_price column in CSV...")
    rows_updated = 0
    rows_skipped = 0
    rows_not_found = 0
    rows = []
    price_warnings = []

    with open(path, newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows.append(header)
        dt_col = header.index("pe_datetime")
        aemo_col = header.index("aemo_price")
        gen_col = header.index("gen_price")

        for row in reader:
            dt_str = row[dt_col].strip()
            if not dt_str:
                rows.append(row)
                continue
            dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
            aemo_price_new = all_prices.get(dt)
            if aemo_price_new is None:
                ts = dt.hour * 3600 + dt.minute * 60 + dt.second
                next_5min_ts = ((ts // 300) + 1) * 300
                if next_5min_ts >= 86400:
                    next_5min_ts = 0
                    next_dt = dt.replace(hour=0, minute=0, second=0) + timedelta(days=1)
                else:
                    next_dt = dt.replace(hour=0, minute=0, second=0) + timedelta(seconds=next_5min_ts)
                aemo_price_new = all_prices.get(next_dt)
            if aemo_price_new is None:
                ts = dt.hour * 3600 + dt.minute * 60 + dt.second
                prev_5min_ts = (ts // 300) * 300
                prev_dt = dt.replace(hour=0, minute=0, second=0) + timedelta(seconds=prev_5min_ts)
                aemo_price_new = all_prices.get(prev_dt)

            if aemo_price_new is not None:
                aemo_per_kwh = round(aemo_price_new / 1000, 4)
                old_val = row[aemo_col].strip()
                old_aemo = float(old_val) if old_val else 0
                gen_val = float(row[gen_col]) if row[gen_col].strip() else 0
                if abs(old_aemo - aemo_per_kwh) > 0.00005:
                    row[aemo_col] = str(aemo_per_kwh)
                    rows_updated += 1
                    if old_aemo != 0 and gen_val != 0:
                        price_warnings.append(f"  {dt_str}: aemo {old_aemo}->{aemo_per_kwh}, gen was {gen_val}")
                else:
                    rows_skipped += 1
            else:
                rows_not_found += 1
            rows.append(row)

    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(rows)

    print(f"\nAEMO update complete!")
    print(f"  Rows updated: {rows_updated}")
    print(f"  Rows skipped (already correct): {rows_skipped}")
    print(f"  Rows with no AEMO match: {rows_not_found}")
    if price_warnings:
        print(f"Sample updates (showing first 10 where old aemo != 0):")
        for w in price_warnings[:10]:
            print(w)
    return True


def update_solar_gen(target_path, solar_gen_path):
    print(f"\nUpdating solar_gen from {solar_gen_path}...")
    if not os.path.exists(solar_gen_path):
        print(f"ERROR: Solar gen CSV not found: {solar_gen_path}")
        return False

    solar_lookup = {}
    with open(solar_gen_path, newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        dt_col = header.index("datetime")
        sg_col = header.index("solar_gen")
        for row in reader:
            ts = row[dt_col].strip()
            if ts:
                try:
                    solar_lookup[ts] = float(row[sg_col].strip())
                except (ValueError, IndexError):
                    pass
    print(f"Loaded {len(solar_lookup)} solar gen entries")

    rows_updated = 0
    rows_skipped = 0
    rows_not_found = 0
    rows = []

    with open(target_path, newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows.append(header)
        pe_col = header.index("pe_datetime")
        sg_col_target = header.index("solar_gen")

        for row in reader:
            pe_dt = row[pe_col].strip()
            if pe_dt in solar_lookup:
                new_val = solar_lookup[pe_dt]
                old_val = float(row[sg_col_target].strip()) if row[sg_col_target].strip() else 0
                if abs(old_val - new_val) > 0.00005:
                    row[sg_col_target] = str(new_val)
                    rows_updated += 1
                else:
                    rows_skipped += 1
            else:
                rows_not_found += 1
            rows.append(row)

    with open(target_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(rows)

    print(f"Solar gen update complete!")
    print(f"  Rows updated: {rows_updated}")
    print(f"  Rows skipped (already correct): {rows_skipped}")
    print(f"  Rows with no match in solar gen CSV: {rows_not_found}")
    return True


def main():
    global RETAILER_CSV
    dry_run = '--dry-run' in sys.argv
    skip_aemo = '--skip-aemo' in sys.argv
    solar_gen_path = None
    filtered = []
    i = 0
    args_list = sys.argv[1:]
    while i < len(args_list):
        if args_list[i] == '--solar-gen' and i + 1 < len(args_list):
            solar_gen_path = args_list[i + 1]
            i += 2
        elif args_list[i] in ('--dry-run', '--skip-aemo'):
            i += 1
        else:
            filtered.append(args_list[i])
            i += 1
    args = filtered
    if len(args) > 0:
        RETAILER_CSV = args[0]
    print(f"Retailer CSV: {RETAILER_CSV}")
    print(f"Target CSV: {TARGET_CSV}")

    if not RETAILER_CSV:
        print("ERROR: No retailer CSV specified.")
        print("Usage: python3 merge_retailer_data.py [--dry-run] [--skip-aemo] [--solar-gen <path>] <path_to_MeterDataReport.csv>")
        return 1

    print(f"Parsing retailer CSV: {RETAILER_CSV}")
    streams = parse_retailer_csv(RETAILER_CSV)
    print(f"Found streams: {list(streams.keys())}")
    for stream_id, stream_data in streams.items():
        n_dates = len(stream_data['data'])
        n_headers = len(stream_data['headers']) if stream_data['headers'] else 0
        dates_range = ""
        if n_dates > 0:
            dates = sorted(stream_data['data'].keys())
            dates_range = f"{dates[0]} to {dates[-1]}"
        print(f"  {stream_id}: {n_dates} dates, {n_headers} intervals/date ({dates_range})")

    if 'B1' not in streams or 'E1' not in streams:
        print("ERROR: Missing B1 or E1 stream in retailer CSV")
        return 1

    b1_data = streams['B1']['data']
    b1_daily_totals = {}
    for date_str, intervals in b1_data.items():
        b1_daily_totals[date_str] = round(sum(intervals), 3)
    print(f"\nB1 (Solar) daily totals: min={min(b1_daily_totals.values()):.3f}, max={max(b1_daily_totals.values()):.3f}")
    print(f"B1 stream total: {sum(b1_daily_totals.values()):.3f}")

    e1_data = streams['E1']['data']
    e1_daily_totals = {}
    for date_str, intervals in e1_data.items():
        e1_daily_totals[date_str] = round(sum(intervals), 3)
    print(f"E1 (Consumption) daily totals: min={min(e1_daily_totals.values()):.3f}, max={max(e1_daily_totals.values()):.3f}")
    print(f"E1 stream total: {sum(e1_daily_totals.values()):.3f}")

    print("\nBuilding cumulative map...")
    cum_map = build_cumulative_map(streams)
    n_periods = sum(len(v) for v in cum_map.values())
    print(f"Built {n_periods} period-ending entries across {len(cum_map)} dates")

    sample_date = sorted(cum_map.keys())[0]
    sample_entries = list(cum_map[sample_date].items())
    print(f"\nSample date {sample_date}: first 3 and last 3 entries:")
    for pe_dt, vals in sample_entries[:3] + sample_entries[-3:]:
        print(f"  {pe_dt}: export={vals['export']:.6f}, Import_kWh={vals['Import_kWh']:.6f}")

    for date_str in sorted(cum_map.keys()):
        last_entry = list(cum_map[date_str].values())[-1]
        export_diff = abs(last_entry['export'] - b1_daily_totals.get(date_str, 0))
        import_diff = abs(last_entry['Import_kWh'] - e1_daily_totals.get(date_str, 0))
        if export_diff > 0.01:
            print(f"WARNING: {date_str} export cumulative {last_entry['export']:.4f} != B1 total {b1_daily_totals.get(date_str, 0):.4f}")
        if import_diff > 0.01:
            print(f"WARNING: {date_str} Import cumulative {last_entry['Import_kWh']:.4f} != E1 total {e1_daily_totals.get(date_str, 0):.4f}")

    if dry_run:
        print("\n*** DRY RUN -- skipping merge and AEMO update ***")
        print(f"Would update {len(cum_map)} dates into {TARGET_CSV}")
        return 0

    print(f"\nMerging into target CSV: {TARGET_CSV}")
    success = merge_into_csv(TARGET_CSV, cum_map, backup=True)
    if not success:
        return 1

    if skip_aemo:
        print("\nSkipping AEMO price update (--skip-aemo).")
    else:
        update_aemo_prices(TARGET_CSV)

    if solar_gen_path:
        update_solar_gen(TARGET_CSV, solar_gen_path)

    print("\nDone.")
    return 0


if __name__ == '__main__':
    sys.exit(main())
