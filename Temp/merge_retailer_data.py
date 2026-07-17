#!/usr/bin/env python3
"""Merge retailer-provided meter data (B1 export, E1 consumption) into 5minelecNEW.csv.

Maps:
  B1 (Solar) 5-min interval kWh → export (col 4), cumulative within-day
  E1 (Consumption) 5-min interval kWh → Import_kWh (col 14), cumulative within-day
  solar_gen (col 13) is KEPT from original CSV (not overwritten)

Overwrites existing values for matching dates (2026-05-17 to 2026-07-16).
"""

import csv
import sys
import os
from collections import defaultdict

RETAILER_CSV = "temp/30421169_41023660658_20260517_20260717_MeterDataReport.csv"
TARGET_CSV = "/tmp/5minelecNEW_original.csv"
BACKUP_SUFFIX = ".bak"

# Time column headers map to period-ending times (HH:MM:59 format)
# "0:00" -> 00:04:59, "0:05" -> 00:09:59, ..., "23:55" -> 23:59:59
def time_header_to_period_end(th):
    """Convert time header like '0:00' to 'HH:MM:59' period-ending timestamp."""
    parts = th.strip().split(':')
    h = int(parts[0])
    m = int(parts[1])
    # Period end = start + 4min 59sec
    end_m = m + 4
    end_h = h
    if end_m >= 60:
        end_m -= 60
        end_h += 1
    return f"{end_h:02d}:{end_m:02d}:59"

def parse_retailer_csv(filepath):
    """Parse retailer CSV, return dict mapping date -> list of (time_header, period_end, kWh) for each stream."""
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

        # Detect stream header using proper CSV parsing for the quoted field
        if line_stripped.startswith('Stream ID'):
            reader = csv_mod.reader([line_stripped])
            row = next(reader)
            # row = ['Stream ID', 'Meter 213294978,...', 'B1', 'B1', 'KWH', 'Solar']
            stream_id = row[2]  # B1 or E1
            current_stream = stream_id
            streams[current_stream] = {'headers': None, 'data': {}}
            data_start_line = None
            continue

        if current_stream is None:
            continue

        # Skip the LOCAL TIME row and "Total for Period" rows
        if line_stripped.startswith('LOCAL TIME') or line_stripped.startswith('Total for Period'):
            continue

        # Date/Time header row (the 288 column header row)
        if line_stripped.startswith('Date/Time'):
            cols = line_stripped.split(',')
            # cols[0] = "Date/Time", cols[1..288] = time headers, cols[289] = "Quality", cols[290] = "Total"
            time_headers = cols[1:289]
            streams[current_stream]['headers'] = time_headers
            data_start_line = i
            continue

        # Data rows (date, 288 values, quality, total)
        if data_start_line is not None and i > data_start_line:
            cols = line_stripped.split(',')
            if len(cols) < 290:
                continue
            date_str = cols[0].strip()
            if not date_str.isdigit() or len(date_str) != 8:
                continue
            # Parse 288 interval values (cols 1..288)
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
    """Build a dict: date -> period_end -> {col_name: cumulative_kWh}"""
    # B1 = export (grid solar export), E1 = Import_kWh (grid consumption)
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
    """Read target CSV, update solar_gen (col 13) and Import_kWh (col 14) for matching datetimes."""
    if not os.path.exists(target_path):
        print(f"ERROR: Target CSV not found: {target_path}")
        return False

    # Read target CSV
    with open(target_path, 'r') as f:
        lines = f.readlines()

    if not lines:
        print(f"ERROR: Target CSV is empty: {target_path}")
        return False

    # Parse header
    header = lines[0].strip()
    header_cols = header.split(',')
    # Find column indices
    # datetime=0, offpeak=1, shoulder=2, peak=3, export=4, bat_charge=5, Bat_Charge_Energy=6,
    # Bat_Discharge_Energy=7, house_load=8, gen_price=9, fit_price=10, aemo_price=11,
    # pe_datetime=12, solar_gen=13, Import_kWh=14
    print(f"Header cols ({len(header_cols)}): {header_cols}")
    print(f"Expected: datetime,offpeak,shoulder,peak,export,bat_charge,Bat_Charge_Energy,Bat_Discharge_Energy,house_load,gen_price,fit_price,aemo_price,pe_datetime,solar_gen,Import_kWh")

    export_idx = 4
    import_kwh_idx = 14
    pe_datetime_col = 12  # pe_datetime column for matching

    # Build lookup: pe_datetime -> {export, Import_kWh}
    lookup = {}
    for date_str, intervals in cum_map.items():
        for pe_dt, vals in intervals.items():
            lookup[pe_dt] = vals

    print(f"Built lookup with {len(lookup)} period-ending timestamps")

    # Process data rows
    updated_count = 0
    not_found_count = 0
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
            del lookup[pe_dt]  # remove to track unmatched
        # else: keep original values

        new_lines.append(','.join(cols) + '\n')

    print(f"Updated {updated_count} rows")
    if lookup:
        print(f"WARNING: {len(lookup)} lookup entries not matched in target CSV")
        # Show first few unmatched
        for pe_dt in list(lookup.keys())[:5]:
            print(f"  Unmatched: {pe_dt}")
    else:
        print("All lookup entries matched successfully.")

    # Write backup
    if backup:
        backup_path = target_path + BACKUP_SUFFIX
        with open(backup_path, 'w') as f:
            f.writelines(lines)
        print(f"Backup written to {backup_path}")

    # Write updated CSV
    with open(target_path, 'w') as f:
        f.writelines(new_lines)
    print(f"Updated CSV written to {target_path}")

    return True

def main():
    # Parse retailer CSV
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

    # Validate B1 daily totals
    b1_data = streams['B1']['data']
    b1_daily_totals = {}
    for date_str, intervals in b1_data.items():
        b1_daily_totals[date_str] = round(sum(intervals), 3)
    print(f"\nB1 (Solar) daily totals: min={min(b1_daily_totals.values()):.3f}, max={max(b1_daily_totals.values()):.3f}")
    print(f"B1 stream total: {sum(b1_daily_totals.values()):.3f}")

    # Validate E1 daily totals
    e1_data = streams['E1']['data']
    e1_daily_totals = {}
    for date_str, intervals in e1_data.items():
        e1_daily_totals[date_str] = round(sum(intervals), 3)
    print(f"E1 (Consumption) daily totals: min={min(e1_daily_totals.values()):.3f}, max={max(e1_daily_totals.values()):.3f}")
    print(f"E1 stream total: {sum(e1_daily_totals.values()):.3f}")

    # Build cumulative map
    print("\nBuilding cumulative map...")
    cum_map = build_cumulative_map(streams)
    n_periods = sum(len(v) for v in cum_map.values())
    print(f"Built {n_periods} period-ending entries across {len(cum_map)} dates")

    # Test: show first and last few entries for one date
    sample_date = sorted(cum_map.keys())[0]
    sample_entries = list(cum_map[sample_date].items())
    print(f"\nSample date {sample_date}: first 3 and last 3 entries:")
    for pe_dt, vals in sample_entries[:3] + sample_entries[-3:]:
        print(f"  {pe_dt}: export={vals['export']:.6f}, Import_kWh={vals['Import_kWh']:.6f}")

    # Check last cumulative values match daily totals
    for date_str in sorted(cum_map.keys()):
        last_entry = list(cum_map[date_str].values())[-1]
        export_diff = abs(last_entry['export'] - b1_daily_totals.get(date_str, 0))
        import_diff = abs(last_entry['Import_kWh'] - e1_daily_totals.get(date_str, 0))
        if export_diff > 0.01:
            print(f"WARNING: {date_str} export cumulative {last_entry['export']:.4f} != B1 total {b1_daily_totals.get(date_str, 0):.4f}")
        if import_diff > 0.01:
            print(f"WARNING: {date_str} Import cumulative {last_entry['Import_kWh']:.4f} != E1 total {e1_daily_totals.get(date_str, 0):.4f}")

    # Merge into target CSV
    print(f"\nMerging into target CSV: {TARGET_CSV}")
    success = merge_into_csv(TARGET_CSV, cum_map, backup=True)
    if not success:
        return 1

    print("\nDone. Run downstream processing to verify.")
    return 0

if __name__ == '__main__':
    sys.exit(main())
