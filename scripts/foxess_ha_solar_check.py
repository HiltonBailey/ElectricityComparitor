import json
import os
import sys
import urllib.request
from datetime import datetime, timedelta, timezone

HA_URL = os.environ.get("HA_URL", "http://192.168.50.100:8123")
TOKEN = os.environ.get("HA_TOKEN")
if not TOKEN:
    print("Error: HA_TOKEN environment variable must be set.")
    sys.exit(1)
ENTITY = "sensor.foxmodbus_solar_energy_today"

START = datetime(2025, 12, 1, tzinfo=timezone.utc)
END = datetime.now(timezone.utc)

def fetch(start, end):
    url = (f"{HA_URL}/api/history/period/{start.isoformat().replace('+00:00','Z')}"
           f"?filter_entity_id={ENTITY}&end_time={end.isoformat().replace('+00:00','Z')}")
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = json.load(r)
    return data

states = []  # (local_datetime, value)
cur = START
while cur < END:
    nxt = min(cur + timedelta(days=30), END)
    print(f"Querying {cur.date()} -> {nxt.date()} ...", flush=True)
    try:
        data = fetch(cur, nxt)
        ent = data[0] if (isinstance(data, list) and data and isinstance(data[0], list)) else None
        if ent:
            for s in ent:
                try:
                    lu = datetime.fromisoformat(s['last_updated'].replace('Z', '+00:00'))
                    val = float(s['state'])
                    states.append((lu, val))
                except (ValueError, KeyError, TypeError):
                    pass
    except Exception as e:
        print(f"  error: {e}")
    cur = nxt

print(f"\nTotal state samples: {len(states)}")

# End-of-day figure per LOCAL date = max value observed that day (just before midnight reset)
from collections import defaultdict
from zoneinfo import ZoneInfo
LOCAL = ZoneInfo("Australia/Sydney")
daily = defaultdict(float)
daily_max_lu = {}
for lu, val in states:
    local = lu.astimezone(LOCAL)
    d = local.date().isoformat()
    if val > daily[d]:
        daily[d] = val
        daily_max_lu[d] = local

rows = []
for d in sorted(daily):
    rows.append({'date': d, 'foxmodbus_solar_energy_today_kwh': round(daily[d], 3),
                 'sample_time': daily_max_lu[d].isoformat()})
print(f"Days with data: {len(rows)}")
print(f"Earliest: {rows[0]['date']}  Latest: {rows[-1]['date']}")

with open('foxess_ha_solar_daily.csv', 'w', newline='') as f:
    import csv
    w = csv.DictWriter(f, fieldnames=['date', 'foxmodbus_solar_energy_today_kwh', 'sample_time'])
    w.writeheader()
    w.writerows(rows)
print("Wrote foxess_ha_solar_daily.csv")

print("\nFirst 3 / last 5:")
for r in rows[:3] + rows[-5:]:
    print(" ", r)
