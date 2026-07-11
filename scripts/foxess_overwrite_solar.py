import pandas as pd
import sys
import shutil
from datetime import datetime

TARGET = '/Volumes/share/file_notifications/5minelecNEW.csv'
HIST = 'foxess_solar_history.csv'
BACKUP = '/Volumes/share/file_notifications/backup/5minelecNEW.csv.solar_corrected_' + datetime.now().strftime('%Y%m%d_%H%M%S')

# 1. Build corrected cumulative TOTAL solar map from history
hist = pd.read_csv(HIST)
hist['ts'] = pd.to_datetime(hist['datetime'])
hist['total_cum'] = hist['cum_pvPower_kwh'] + hist['cum_meterPower2_kwh'].abs()
# The history file datetime is bucket start. Map key = bucket start string.
hist_map = dict(zip(hist['ts'].dt.strftime('%Y-%m-%d %H:%M:%S'), hist['total_cum'].round(4)))

# 2. Load 5minelecNEW (live file)
tgt = pd.read_csv(TARGET, dtype=str, keep_default_na=False)
tgt['pe'] = pd.to_datetime(tgt['pe_datetime'])
tgt['pk'] = tgt['pe'].dt.floor('5min').dt.strftime('%Y-%m-%d %H:%M:%S')

# 3. Overwrite solar_gen for all rows <= 2026-07-09 23:59:59
cutoff = pd.Timestamp('2026-07-09 23:59:59')
mask = (tgt['pe'] <= cutoff)

print(f"5minelecNEW rows: {len(tgt)}")
print(f"Rows to correct (<= 2026-07-09): {mask.sum()}")

corrected_count = 0
for i, row in tgt[mask].iterrows():
    pk = row['pk']
    if pk in hist_map:
        tgt.iat[i, 13] = str(hist_map[pk]) # solar_gen column index 13
        corrected_count += 1

print(f"Rows with corrected solar_gen: {corrected_count}")

# 4. Backup & Write
shutil.copy2(TARGET, BACKUP)
print(f"\nBackup: {BACKUP}")
tgt.drop(columns=['pe', 'pk']).to_csv(TARGET, index=False)
print(f"Wrote updated {TARGET}")
