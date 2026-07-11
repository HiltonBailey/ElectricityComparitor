import pandas as pd

HISTORY = 'foxess_solar_history.csv'
OUTPUT = 'foxess_solar_daily.csv'
VARS = ['generationPower', 'pvPower', 'meterPower2']

print(f"Reading {HISTORY}...")
df = pd.read_csv(HISTORY)
df['date'] = df['datetime'].str[:10]

rows = []
for d, g in df.groupby('date'):
    row = {'date': d}
    for v in VARS:
        row[f'{v}_kwh'] = round(g[f'{v}_kwh'].sum(), 3)
        row[f'{v}_peak_kw'] = round(g[f'{v}_kw'].abs().max(), 3)
    # AC solar (Enphase / Grid2) = magnitude of meterPower2. The sensor's sign
    # convention flips (POS early Dec 2025 and sporadically, NEG otherwise), so use abs().
    row['fox_string_pv_kwh'] = round(row['pvPower_kwh'], 3)
    row['ac_solar_kwh'] = round(abs(row['meterPower2_kwh']), 3)
    row['total_pv_kwh'] = round(row['fox_string_pv_kwh'] + row['ac_solar_kwh'], 3)
    rows.append(row)

out = pd.DataFrame(rows)
out.to_csv(OUTPUT, index=False)
print(f"Wrote {len(out)} daily rows -> {OUTPUT}")
print(f"Range: {out['date'].iloc[0]} to {out['date'].iloc[-1]}")
print("\nLast 5 days:")
print(out.tail(5).to_string(index=False))
