import os
import sys
import csv
import time
from datetime import datetime, timedelta

import foxesscloud.foxesscloud as fox

fox.username = os.environ.get("FOXESS_USER")
fox.password = os.environ.get("FOXESS_PASS")

DAYS = [
    '2026-07-10', '2026-07-11', '2026-07-12', '2026-07-13',
    '2026-07-14', '2026-07-15',
    '2026-07-27', '2026-07-28', '2026-07-29',
]
OUTPUT = os.path.join(os.path.dirname(__file__), 'foxess_solar_gap_raw.csv')
VAR = 'generationPower'

if not fox.username or not fox.password:
    print("Error: set FOXESS_USER and FOXESS_PASS environment variables.")
    sys.exit(1)

if not fox.get_token():
    print("Login failed")
    sys.exit(1)

with open(OUTPUT, 'w', newline='') as f:
    writer = csv.writer(f)
    writer.writerow(['timestamp', 'variable', 'value'])
    for d_str in DAYS:
        try:
            raw = fox.get_raw(time_span='day', d=d_str, v=[VAR], summary=0)
            if raw:
                for var_obj in raw:
                    for entry in var_obj['data']:
                        writer.writerow([entry['time'], var_obj['variable'], entry['value']])
                print(f"OK   {d_str} ({len(raw[0]['data'])} pts)")
            else:
                print(f"EMPTY {d_str}")
        except Exception as e:
            print(f"ERR  {d_str}: {e}")
        time.sleep(0.3)

print(f"\nWrote {OUTPUT}")
