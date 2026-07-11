import os
import csv
import sys
import time
import foxesscloud.foxesscloud as fox
from datetime import datetime, timedelta

fox.username = os.environ.get("FOXESS_USER")
fox.password = os.environ.get("FOXESS_PASS")

OUTPUT = 'foxess_solar_raw.csv'
VARS = ['generationPower', 'pvPower', 'meterPower2']

START = datetime(2025, 12, 1)
END = datetime.now()

if not fox.username or not fox.password:
    print("Error: FOXESS_USER and FOXESS_PASS environment variables must be set.")
    sys.exit(1)

if not fox.get_token():
    print("Login failed")
    sys.exit(1)

rows_before = 0
try:
    with open(OUTPUT) as f:
        rows_before = sum(1 for _ in f) - 1
except FileNotFoundError:
    pass

with open(OUTPUT, 'a', newline='') as f:
    writer = csv.writer(f)
    if rows_before == 0:
        writer.writerow(['timestamp', 'variable', 'value'])

    current = START
    fetched = 0
    failed = 0
    while current <= END:
        d_str = current.strftime("%Y-%m-%d")
        try:
            raw = fox.get_raw(time_span='day', d=d_str, v=VARS)
            if raw:
                for var_obj in raw:
                    vname = var_obj['variable']
                    for entry in var_obj['data']:
                        writer.writerow([entry['time'], vname, entry['value']])
                fetched += 1
                print(f"OK   {d_str}")
            else:
                failed += 1
                print(f"EMPTY {d_str}")
        except Exception as e:
            failed += 1
            print(f"ERR  {d_str}: {e}")
        current += timedelta(days=1)
        time.sleep(0.2)

with open(OUTPUT) as f:
    total = sum(1 for _ in f) - 1

print(f"\nDone. Fetched {fetched} days, failed/empty {failed}.")
print(f"Total rows in {OUTPUT}: {total:,}")
