import pandas as pd
import numpy as np
from datetime import datetime, timedelta

FOXESS_FILE = 'foxess_full_history.csv'
OUTPUT_FILE = 'foxess_as_5minelec.csv'
INTERVAL_MIN = 5  # target interval in minutes

print("Reading FoxESS long-format data...")
df = pd.read_csv(FOXESS_FILE)

print(f"  Total rows: {len(df):,}")
df['timestamp'] = df['timestamp'].str.replace(r' [AESCTDM]+.*$', '', regex=True)
df['timestamp'] = pd.to_datetime(df['timestamp'])

# Pivot long to wide — use max() agg to handle generationPower duplicates (+X/-X)
print("Pivoting to wide format...")
df = df.pivot_table(index='timestamp', columns='variable', values='value', aggfunc='max').reset_index()
df.columns.name = None
print(f"  Wide rows: {len(df):,}")

# Set timestamp index and sort
df = df.set_index('timestamp').sort_index()

# Compute raw 3-min energy first, then resample by summing energy
# This avoids the bug where .mean() * interval overcounts sparse windows
RAW_INTERVAL = 3  # FoxESS reports at ~3-min intervals
for col in ['generationPower', 'gridConsumptionPower', 'feedinPower',
            'batChargePower', 'batDischargePower', 'loadsPower']:
    df[f'{col}_kwh'] = df[col] * (RAW_INTERVAL / 60)

df['loadsPower'] = df['loadsPower'].abs()

# Resample to 5-min by summing energy, then convert back to mean power
print(f"Resampling {INTERVAL_MIN}-min intervals (sum energy)...")
energy_cols_map = {
    'generationPower': 'generationPower_kwh',
    'gridConsumptionPower': 'gridConsumptionPower_kwh',
    'feedinPower': 'feedinPower_kwh',
    'batChargePower': 'batChargePower_kwh',
    'batDischargePower': 'batDischargePower_kwh',
    'loadsPower': 'loadsPower_kwh',
}
resampled = df[list(energy_cols_map.values())].resample(f'{INTERVAL_MIN}min').sum()
resampled.index = resampled.index.floor(f'{INTERVAL_MIN}min')
resampled = resampled[~resampled.index.duplicated(keep='first')]

# Convert summed energy back to mean power for the 5-min window
for orig_col, energy_col in energy_cols_map.items():
    resampled[orig_col] = resampled[energy_col] / (INTERVAL_MIN / 60)

# Fill NaN with 0
resampled = resampled.fillna(0)
df = resampled.copy()

print(f"  Resampled rows: {len(df):,}")

# FoxESS generationPower = total PV generation (MPPT / panel power).
df['solar_gen_kw'] = df['generationPower'].clip(lower=0)

# Energy per 5-min interval (from the summed 3-min energy, already correct)
energy_cols = {
    'gridConsumptionPower_kwh': 'import_kwh',
    'feedinPower_kwh': 'export_kwh',
    'batChargePower_kwh': 'bat_charge_kwh',
    'batDischargePower_kwh': 'bat_discharge_kwh',
    'loadsPower_kwh': 'house_load_kwh',
}
for src, dst in energy_cols.items():
    df[dst] = df[src]

# solar_gen from generationPower energy
df['solar_gen_kwh'] = df['generationPower_kwh']

# Compute cumulative daily values for ALL energy columns
df['date'] = df.index.date
df['cum_import'] = df.groupby('date')['import_kwh'].cumsum()
df['cum_export'] = df.groupby('date')['export_kwh'].cumsum()
df['cum_house_load'] = df.groupby('date')['house_load_kwh'].cumsum()
df['cum_solar_gen'] = df.groupby('date')['solar_gen_kwh'].cumsum()
df['cum_bat_charge'] = df.groupby('date')['bat_charge_kwh'].cumsum()
df['cum_bat_discharge'] = df.groupby('date')['bat_discharge_kwh'].cumsum()



# Build output rows
print("Building 5minelecNEW-format rows...")
rows = []
prev_date = None
for ts, row in df.iterrows():
    date_str = ts.strftime('%Y-%m-%d')
    time_str = ts.strftime('%H:%M:%S')

    # Period-ending timestamp: end of 5-min slot (add INTERVAL_MIN)
    pe = ts + timedelta(minutes=INTERVAL_MIN) - timedelta(seconds=1)
    pe_str = pe.strftime('%Y-%m-%d %H:%M:%S')

    import_kwh = round(row['import_kwh'], 3)
    export_kwh = round(row['export_kwh'], 3)
    cum_import = round(row['cum_import'], 3)
    cum_export = round(row['cum_export'], 3)

    # Use absolute value for house load (FoxESS can report negative when exporting)
    cum_house_load = round(abs(row['cum_house_load']), 3)
    cum_solar_gen = round(row['cum_solar_gen'], 3)
    cum_bat_charge = round(row['cum_bat_charge'], 3)
    cum_bat_discharge = round(row['cum_bat_discharge'], 3)
    out_row = [
        pe_str,                     # datetime
        '0',                        # offpeak (not used)
        '0',                        # shoulder (not used)
        '0',                        # peak (not used)
        cum_export,                 # export (cumulative)
        cum_bat_charge,             # bat_charge (cumulative)
        cum_bat_charge,             # Bat_Charge_Energy (same)
        cum_bat_discharge,          # Bat_Discharge_Energy (cumulative)
        cum_house_load,             # house_load (cumulative)
        '0',                        # gen_price
        '0',                        # fit_price
        '0',                        # aemo_price
        pe_str,                     # pe_datetime
        cum_solar_gen,              # solar_gen (cumulative)
        cum_import,                 # Import_kWh (cumulative)
    ]
    rows.append(out_row)

# Write CSV
header = [
    'datetime', 'offpeak', 'shoulder', 'peak', 'export',
    'bat_charge', 'Bat_Charge_Energy', 'Bat_Discharge_Energy',
    'house_load', 'gen_price', 'fit_price', 'aemo_price',
    'pe_datetime', 'solar_gen', 'Import_kWh'
]

print(f"Writing {len(rows):,} rows to {OUTPUT_FILE}...")
with open(OUTPUT_FILE, 'w') as f:
    f.write(','.join(header) + '\n')
    for r in rows:
        f.write(','.join(str(v) for v in r) + '\n')

print("Done!")
print(f"Date range: {rows[0][0][:10]} to {rows[-1][0][:10]}")
