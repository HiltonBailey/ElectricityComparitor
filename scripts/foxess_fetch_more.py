import os
import csv
import sys
import foxesscloud.foxesscloud as fox
from datetime import datetime, timedelta

fox.username = os.environ.get("FOXESS_USER")
fox.password = os.environ.get("FOXESS_PASS")

VARS = [
    'gridConsumptionPower', 'feedinPower', 'batChargePower',
    'batDischargePower', 'loadsPower', 'generationPower'
]

OUTPUT = 'foxess_full_history.csv'

START = datetime(2025, 6, 1)
END = datetime(2025, 11, 30)

if not fox.username or not fox.password:
    print("Error: FOXESS_USER and FOXESS_PASS environment variables not set.")
    sys.exit(1)

if not fox.get_token():
    print("Login failed")
    sys.exit(1)

rows_before = 0
try:
    with open(OUTPUT) as f:
        rows_before = sum(1 for _ in f) - 1  # minus header
except FileNotFoundError:
    pass

with open(OUTPUT, 'a', newline='') as f:
    writer = csv.writer(f)
    if rows_before == 0:
        writer.writerow(['timestamp', 'variable', 'value'])

    current = START
    fetched = 0
    while current <= END:
        d_str = current.strftime("%Y-%m-%d")
        print(f"Fetching {d_str}...")
        try:
            raw_data = fox.get_raw(time_span='day', d=d_str, v=VARS)
            if raw_data:
                for var_obj in raw_data:
                    var_name = var_obj['variable']
                    for entry in var_obj['data']:
                        writer.writerow([entry['time'], var_name, entry['value']])
                fetched += 1
        except Exception as e:
            print(f"Error {d_str}: {e}")
        current += timedelta(days=1)

with open(OUTPUT) as f:
    total_rows = sum(1 for _ in f) - 1

print(f"\nDone! Fetched {fetched} days.")
print(f"Total rows in {OUTPUT}: {total_rows:,}")
