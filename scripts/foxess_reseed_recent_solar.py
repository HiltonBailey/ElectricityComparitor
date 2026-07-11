import pandas as pd
import sys
import shutil
from datetime import datetime

SEED = '/Volumes/share/file_notifications/newseed.csv'
HIST = 'foxess_solar_history.csv'
TARGET = '/Volumes/share/file_notifications/5minelecNEW.csv'
BACKUP = '/Volumes/share/file_notifications/backup/5minelecNEW.csv.bak_' + datetime.now().strftime('%Y%m%d_%H%M%S')
DRY = '--write' not in sys.argv

# Rows already corrected by the newseed overlay
seed = pd.read_csv(SEED, dtype=str, keep_default_na=False)
seed_set = set(seed['pe_datetime'])

# FoxESS-cloud-derived cumulative TOTAL solar, keyed by 5-min bucket start
hist = pd.read_csv(HIST)
hist['total_cum'] = hist['cum_pvPower_kwh'] + hist['meterPower2_kwh'].abs()
hist_map = dict(zip(hist['datetime'], hist['total_cum'].round(4)))

tgt = pd.read_csv(TARGET, dtype=str, keep_default_na=False)
print(f"5minelecNEW rows: {len(tgt)}")

corrected = 0
kept = 0
for i, row in tgt.iterrows():
    pe = row['pe_datetime']
    if pe in seed_set:
        continue  # already corrected from newseed
    pk = pd.to_datetime(pe).floor('5min').strftime('%Y-%m-%d %H:%M:%S')
    if pk in hist_map:
        tgt.iat[i, 13] = str(hist_map[pk])   # solar_gen column
        corrected += 1
    else:
        kept += 1

print(f"recent rows corrected from FoxESS-cloud history: {corrected}")
print(f"recent rows kept as-is (outside history range): {kept}")

sample = tgt[(tgt['pe_datetime'] == '2026-07-10 08:29:59')]
if len(sample):
    print("\nSample 2026-07-10 08:29:59:", list(sample.iloc[0].values))

if DRY:
    print("\n[DRY-RUN] no changes written.")
    sys.exit(0)

shutil.copy2(TARGET, BACKUP)
print(f"\nBackup: {BACKUP}")
tgt.to_csv(TARGET, index=False)
print(f"Wrote {TARGET} ({len(tgt)} rows)")
