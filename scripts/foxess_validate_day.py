import os
import sys
import pandas as pd
import numpy as np
import foxesscloud.foxesscloud as fox
from datetime import datetime

fox.username = os.environ.get("FOXESS_USER")
fox.password = os.environ.get("FOXESS_PASS")

DAY = os.environ.get("FOX_DAY", "2026-07-09")
VARS = ['generationPower', 'pvPower', 'meterPower2']

if not fox.username or not fox.password:
    print("Error: FOXESS_USER and FOXESS_PASS environment variables must be set.")
    sys.exit(1)

if not fox.get_token():
    print("Login failed")
    sys.exit(1)

print(f"Fetching {DAY} for {VARS} ...")
raw = fox.get_raw(time_span='day', d=DAY, v=VARS)
if not raw:
    print("No data returned for that day.")
    sys.exit(1)

rows = []
for var_obj in raw:
    vname = var_obj['variable']
    for entry in var_obj['data']:
        rows.append({'timestamp': entry['time'], 'variable': vname, 'value': entry['value']})

df = pd.DataFrame(rows)
print(f"Raw samples: {len(df)}")

df['timestamp'] = df['timestamp'].str.replace(r' [A-Z]+[+-]\d+$', '', regex=True)
df['timestamp'] = pd.to_datetime(df['timestamp'])

print(f"\n=== DAILY TOTALS for {DAY} (energy via actual sample-time deltas) ===")
for v in VARS:
    sub = df[df['variable'] == v].sort_values('timestamp').copy()
    sub['value'] = pd.to_numeric(sub['value'], errors='coerce')
    sub = sub.dropna(subset=['value'])
    if len(sub) < 2:
        print(f"  {v:20s} insufficient data")
        continue
    # Energy for each sample = power * (time to next sample). Trailing sample uses prior delta.
    dt = sub['timestamp'].diff().dt.total_seconds() / 3600.0  # hours
    dt = dt.bfill()
    energy = (sub['value'] * dt).sum()
    print(f"  {v:20s} total = {energy:8.3f} kWh   (mean {sub['value'].mean():7.3f} kW, max {sub['value'].max():7.3f} kW)")

print("\nNote: meterPower2 is the 2nd CT (Grid2/AC solar). If it reads negative,")
print("its AC-solar generation is the absolute value (-meterPower2).")
