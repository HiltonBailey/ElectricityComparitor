import pandas as pd
import numpy as np

RAW = 'foxess_solar_raw.csv'
OUTPUT = 'foxess_solar_history.csv'
VARS = ['generationPower', 'pvPower', 'meterPower2']  # kW; meterPower2 = Grid2/AC solar (negative when generating)

EXPECT = {'generationPower': 'pos', 'pvPower': 'pos', 'meterPower2': 'neg'}

print("Reading raw FoxESS solar data...")
df = pd.read_csv(RAW)
print(f"  {len(df):,} raw samples")

df['timestamp'] = df['timestamp'].str.replace(r' [A-Z]+[+-]\d+$', '', regex=True)
df['timestamp'] = pd.to_datetime(df['timestamp'])
df['value'] = pd.to_numeric(df['value'], errors='coerce')
df = df.dropna(subset=['value'])

# Collapse the (+x / -x) and (+x / 0) duplicate pairs to the single true signed value
# per timestamp (take the value matching the variable's expected sign). Then compute
# energy as value * interval-to-next-sample.
print("Collapsing duplicates and computing per-5-min energy...")
frames = []
for v in VARS:
    sub = df[df['variable'] == v].sort_values('timestamp').copy()
    kept = []
    for ts, grp in sub.groupby('timestamp')['value']:
        vals = list(grp)
        if EXPECT[v] == 'pos':
            cand = [x for x in vals if x >= 0]
            pick = cand[0] if cand else min(vals)
        else:
            cand = [x for x in vals if x <= 0]
            pick = cand[0] if cand else max(vals)
        kept.append((ts, pick))
    s = pd.DataFrame(kept, columns=['timestamp', 'value']).sort_values('timestamp')
    dt = s['timestamp'].diff().dt.total_seconds() / 3600.0
    dt = dt.bfill().ffill().fillna(5.0 / 60.0)
    s['kwh'] = s['value'] * dt
    s['bucket'] = s['timestamp'].dt.floor('5min')
    s = s.set_index('bucket')[['kwh']]
    s.columns = [v]
    frames.append(s)

wide = pd.concat(frames, axis=1).sort_index().fillna(0.0)

# Cumulative energy, resetting each day
wide['_date'] = wide.index.date
cum = wide[[v for v in VARS]].groupby(wide['_date']).cumsum()
cum.columns = [f'cum_{v}' for v in VARS]
wide = pd.concat([wide[[v for v in VARS]], cum], axis=1)

# Mean power (kW) over each 5-min window
power = wide[[v for v in VARS]] / (5.0 / 60.0)

out = pd.DataFrame(index=wide.index)
out['datetime'] = wide.index.strftime('%Y-%m-%d %H:%M:%S')
for v in VARS:
    out[f'{v}_kw'] = power[v].round(4)
    out[f'{v}_kwh'] = wide[v].round(5)
    out[f'cum_{v}_kwh'] = wide[f'cum_{v}'].round(5)

cols = ['datetime']
for v in VARS:
    cols += [f'{v}_kw', f'{v}_kwh', f'cum_{v}_kwh']
out = out[cols]
out.to_csv(OUTPUT, index=False)

print(f"Wrote {len(out):,} rows -> {OUTPUT}")
print(f"Date range: {out['datetime'].iloc[0]}  to  {out['datetime'].iloc[-1]}")

print("\nDaily solar totals (kWh):")
daily = wide[[v for v in VARS]].copy()
daily['date'] = daily.index.date
daily = daily.groupby('date').sum()
for d in daily.index[-4:]:
    r = daily.loc[d]
    print(f"  {d}: FoxPV(pvPower)={r['pvPower']:.2f}  generationPower(suspect)={r['generationPower']:.2f}  ACsolar(-meterPower2)={-r['meterPower2']:.2f}")
