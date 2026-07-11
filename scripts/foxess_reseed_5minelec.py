import pandas as pd
import sys
import shutil
from datetime import datetime

SEED = '/Volumes/share/file_notifications/newseed.csv'
TARGET = '/Volumes/share/file_notifications/5minelecNEW.csv'
BACKUP = '/Volumes/share/file_notifications/backup/5minelecNEW.csv.bak_' + datetime.now().strftime('%Y%m%d_%H%M%S')
DRY = '--write' not in sys.argv

# 5minelecNEW (15 col) positions we overlay from newseed:
#  newseed col -> 5mNE col
MAP = {0: 0, 1: 4, 2: 5, 3: 6, 4: 7, 5: 8, 6: 9, 7: 10, 8: 11, 9: 12, 10: 13, 11: 14}
# positions 1,2,3 (offpeak,shoulder,peak) are left untouched (flow zeroes them).

seed = pd.read_csv(SEED, dtype=str, keep_default_na=False)
seed_map = {r['pe_datetime']: r for _, r in seed.iterrows()}

tgt = pd.read_csv(TARGET, dtype=str, keep_default_na=False)
tgt_cols = list(tgt.columns)
print(f"5minelecNEW rows: {len(tgt)}  cols: {len(tgt_cols)}")
print(f"newseed rows: {len(seed)}")

matched = 0
updated_solar = 0
for i, row in tgt.iterrows():
    pe = row['pe_datetime']
    if pe in seed_map:
        sr = seed_map[pe]
        for sc, tc in MAP.items():
            tgt.iat[i, tc] = sr.iloc[sc]
        matched += 1
        # track if solar_gen actually changed
        if str(row['solar_gen']) != str(sr.iloc[10]):
            updated_solar += 1

print(f"rows matched to newseed: {matched}  (solar_gen changed on {updated_solar})")
print(f"unmatched (kept as-is): {len(tgt)-matched}")

# sanity: show a daytime row after overlay
sample = tgt[(tgt['pe_datetime'] == '2025-12-19 09:03:59')]
if len(sample):
    print("\nOverlay sample 2025-12-19 09:03:59:", list(sample.iloc[0].values))

if DRY:
    print("\n[DRY-RUN] no changes written.")
    sys.exit(0)

shutil.copy2(TARGET, BACKUP)
print(f"\nBackup: {BACKUP}")
tgt.to_csv(TARGET, index=False)
print(f"Wrote {TARGET} ({len(tgt)} rows, {len(tgt_cols)} cols)")
