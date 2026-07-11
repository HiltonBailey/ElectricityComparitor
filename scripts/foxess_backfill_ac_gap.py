import pandas as pd
import numpy as np
import shutil
from datetime import datetime

HISTORY = 'foxess_solar_history.csv'
DAILY = 'foxess_solar_daily.csv'
ENPHASE = '646306_daily_production_report.csv'

# 1. Build Enphase AC map (file is MM/DD/YYYY) for the 21 gap dates
en = pd.read_csv(ENPHASE)
en_map = {}
for _, r in en.iterrows():
    try:
        dt = datetime.strptime(str(r['Date/Time']).strip(), '%m/%d/%Y')
    except Exception:
        continue
    en_map[dt.strftime('%Y-%m-%d')] = float(r['Energy Delivered (kWh)'])

# Gap dates where FoxESS meterPower2 == 0 (AC missing) OR materially under-counts
# vs authoritative Enphase (|diff| > 1.0 kWh) -- from regression test.
GAP_DATES = [
    '2025-12-11',
    '2026-05-27', '2026-05-28', '2026-05-29', '2026-05-30', '2026-05-31',
    '2026-06-01', '2026-06-02', '2026-06-03', '2026-06-04',
    '2026-06-05', '2026-06-06', '2026-06-07', '2026-06-08',
    '2026-06-09', '2026-06-10', '2026-06-11', '2026-06-12',
    '2026-06-13', '2026-06-14', '2026-06-15', '2026-06-16',
    '2026-06-17', '2026-06-18',
]
gap_set = set(GAP_DATES)
missing = [d for d in GAP_DATES if d not in en_map]
assert not missing, f"Enphase missing for: {missing}"

# 2. Load history
hist = pd.read_csv(HISTORY)
hist['date'] = hist['datetime'].str[:10]

# Backup history before modifying
shutil.copy2(HISTORY, HISTORY + '.bak_acgap_' + datetime.now().strftime('%Y%m%d_%H%M%S'))

print(f"Gap dates to backfill AC: {len(GAP_DATES)}")
print(f"{'date':12} {'foxPV':>8} {'enphaseAC':>11} {'oldAC':>8} {'newAC':>8}")
for d in GAP_DATES:
    sub = hist[hist['date'] == d].copy()
    if len(sub) == 0:
        print(f"{d:12} NO HISTORY ROWS - skip")
        continue
    # daily fox PV total from history cumulative
    fox_pv = float(sub['cum_pvPower_kwh'].iloc[-1])
    en_ac = en_map[d]
    # distribute AC across buckets weighted by per-bucket pvPower_kwh (follows the sun)
    pv = sub['pvPower_kwh'].astype(float).values
    pv_total = pv.sum()
    if pv_total > 0:
        weights = pv / pv_total
    else:
        weights = np.ones(len(pv)) / len(pv)
    ac_energy = en_ac * weights  # per-bucket AC energy (kWh)
    # per-bucket meterPower2_kwh: negative when generating
    sub['meterPower2_kwh'] = -ac_energy
    # cumulative, resetting each day
    sub['cum_meterPower2_kwh'] = sub['meterPower2_kwh'].cumsum()
    old_ac = float(sub['cum_meterPower2_kwh'].iloc[0])  # currently ~0
    new_ac = float(sub['cum_meterPower2_kwh'].iloc[-1])
    # write back into hist by index
    hist.loc[sub.index, 'meterPower2_kwh'] = sub['meterPower2_kwh']
    hist.loc[sub.index, 'cum_meterPower2_kwh'] = sub['cum_meterPower2_kwh']
    print(f"{d:12} {fox_pv:8.3f} {en_ac:11.3f} {old_ac:8.3f} {new_ac:8.3f}")

hist.to_csv(HISTORY, index=False)
print(f"\nWrote backfilled {HISTORY}")

# 3. Regenerate daily file
df = pd.read_csv(HISTORY)
df['date'] = df['datetime'].str[:10]
rows = []
VARS = ['generationPower', 'pvPower', 'meterPower2']
for d, g in df.groupby('date'):
    row = {'date': d}
    for v in VARS:
        row[f'{v}_kwh'] = round(g[f'{v}_kwh'].sum(), 3)
        row[f'{v}_peak_kw'] = round(g[f'{v}_kw'].abs().max(), 3)
    row['fox_string_pv_kwh'] = round(row['pvPower_kwh'], 3)
    row['ac_solar_kwh'] = round(abs(row['meterPower2_kwh']), 3)
    row['total_pv_kwh'] = round(row['fox_string_pv_kwh'] + row['ac_solar_kwh'], 3)
    rows.append(row)
out = pd.DataFrame(rows)
out.to_csv(DAILY, index=False)
print(f"Regenerated {DAILY} ({len(out)} rows)")

print("\nBackfilled gap-date daily totals (foxPV + enphaseAC = total):")
for d in GAP_DATES:
    r = out[out['date'] == d].iloc[0]
    print(f"  {d}: foxPV={r['fox_string_pv_kwh']:.3f}  AC={r['ac_solar_kwh']:.3f}  total={r['total_pv_kwh']:.3f}")
