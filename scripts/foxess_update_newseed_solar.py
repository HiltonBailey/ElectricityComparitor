import pandas as pd
import sys
import shutil
from datetime import datetime

SEED = '/Volumes/share/file_notifications/newseed.csv'
HIST = 'foxess_solar_history.csv'
BACKUP = '/Volumes/share/file_notifications/backup/newseed.csv.bak_' + datetime.now().strftime('%Y%m%d_%H%M%S')

DRY = '--write' not in sys.argv

# --- Build corrected cumulative TOTAL solar from FoxESS cloud ---
# total solar per 5-min = pvPower (Fox string PV) + abs(meterPower2) (AC/Enphase solar)
hist = pd.read_csv(HIST)
hist['ts'] = pd.to_datetime(hist['datetime'])
hist['date'] = hist['ts'].dt.date
hist['total_5min'] = hist['pvPower_kwh'] + hist['meterPower2_kwh'].abs()
hist = hist.sort_values('ts')
hist['cum'] = hist.groupby('date')['total_5min'].cumsum()
solar_map = dict(zip(hist['ts'].dt.strftime('%Y-%m-%d %H:%M:%S'), hist['cum'].round(4)))

# --- Load amended newseed.csv ---
seed = pd.read_csv(SEED)
seed['pe'] = pd.to_datetime(seed['pe_datetime'])
seed['pk'] = seed['pe'].dt.floor('5min').dt.strftime('%Y-%m-%d %H:%M:%S')
matched = seed['pk'].isin(solar_map)
seed['solar_gen'] = seed['pk'].map(solar_map)

print(f"newseed rows: {len(seed)}")
print(f"rows matched to FoxESS solar: {matched.sum()}  ({100*matched.mean():.1f}%)")
unmatched = seed[~matched]
print(f"unmatched: {len(unmatched)}")
print("unmatched by date (top 10):")
print(unmatched['pe'].dt.date.value_counts().head(10).to_string())

# Fill unmatched cumulative within the same day (forward, then backward) so the
# running total doesn't dip to 0 and recover.
seed['d'] = seed['pe'].dt.date
seed['solar_gen'] = seed.groupby('d')['solar_gen'].ffill()
seed['solar_gen'] = seed.groupby('d')['solar_gen'].bfill()
print("\nSample filled values (first 6 non-zero):")
sample = seed[seed['solar_gen'].notna() & (seed['solar_gen'] > 0)].head(6)
for _, r in sample.iterrows():
    print(f"  {r['pe_datetime']}  solar_gen={r['solar_gen']:.3f}")
print("\nDaily solar totals (kWh) from newseed after fill:")
seed['d'] = seed['pe'].dt.date
daily = seed.groupby('d')['solar_gen'].max()
print(daily.tail(5).to_string())

if DRY:
    print("\n[DRY-RUN] no changes written.")
    sys.exit(0)

# --- Backup then write ---
shutil.copy2(SEED, BACKUP)
print(f"\nBackup written: {BACKUP}")
seed_out = seed.drop(columns=['pe', 'pk', 'd'])
seed_out.to_csv(SEED, index=False)
print(f"Wrote updated {SEED} ({len(seed_out)} rows)")
