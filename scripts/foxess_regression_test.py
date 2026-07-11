import pandas as pd
import numpy as np
from datetime import datetime

print("=== Loading sources ===")
daily = pd.read_csv('foxess_solar_daily.csv')
daily['date'] = daily['date'].astype(str)

# Enphase authoritative AC (file is MM/DD/YYYY)
en = pd.read_csv('646306_daily_production_report.csv')
en_map = {}
for _, r in en.iterrows():
    try:
        dt = datetime.strptime(str(r['Date/Time']).strip(), '%m/%d/%Y')
    except Exception:
        continue
    en_map[dt.strftime('%Y-%m-%d')] = float(r['Energy Delivered (kWh)'])

# 5minelecNEW (system) solar_gen -> daily total = max within day (daily-resetting cumulative)
sys_df = pd.read_csv('/Volumes/share/file_notifications/5minelecNEW.csv', dtype=str, keep_default_na=False)
sys_df['pe'] = pd.to_datetime(sys_df['pe_datetime'])
sys_df['date'] = sys_df['pe'].dt.strftime('%Y-%m-%d')
sys_df['sg'] = pd.to_numeric(sys_df['solar_gen'], errors='coerce').fillna(0.0)
sys_daily = sys_df.groupby('date')['sg'].max().round(4).to_dict()

print(f"Our daily dates: {len(daily)} | Enphase dates: {len(en_map)}")

print("\n=== REGRESSION: per-date solar totals ===")
print(f"{'date':12} {'foxPV':>7} {'ourAC':>7} {'enAC':>7} {'ourTot':>7} {'expTot':>7} {'sysSol':>7} flag")
flags = []
for _, r in daily.iterrows():
    d = r['date']
    fox = float(r['fox_string_pv_kwh'])
    ac = float(r['ac_solar_kwh'])
    tot = float(r['total_pv_kwh'])
    en_ac = en_map.get(d)
    exp_ac = en_ac if en_ac is not None else ac
    exp_tot = fox + exp_ac
    sys_sol = sys_daily.get(d)
    flag = ''
    if en_ac is not None:
        if abs(ac - en_ac) > 1.0:
            flag += ' ACvsEN'
        if en_ac > 0.5 and ac < 0.1:
            flag += ' AC_MISSING'
    if abs(tot - exp_tot) > 0.5:
        flag += ' TOT'
    if sys_sol is not None and abs(sys_sol - tot) > 0.5:
        flag += ' SYS'
    if flag:
        flags.append(d)
    if flag or d in ('2026-06-02', '2026-05-28', '2025-12-11', '2026-03-05'):
        en_s = f"{en_ac:.3f}" if en_ac is not None else 'NA'
        sy_s = f"{sys_sol:.3f}" if sys_sol is not None else 'NA'
        print(f"{d:12} {fox:7.3f} {ac:7.3f} {en_s:>7} {tot:7.3f} {exp_tot:7.3f} {sy_s:>7} {flag}")

print(f"\nTotal dates flagged: {len(flags)}")
if flags:
    print("Flagged dates:", flags)

# AC vs Enphase overlap stats
ov = daily[daily['date'].isin(en_map.keys())].copy()
ov['en'] = ov['date'].map(en_map)
ov['ac_err'] = (ov['ac_solar_kwh'] - ov['en']).abs()
print(f"\n=== AC vs Enphase (overlap {len(ov)} dates) ===")
print(f"  median |AC-Enphase| = {ov['ac_err'].median():.4f} kWh")
print(f"  max    |AC-Enphase| = {ov['ac_err'].max():.4f} kWh")
print(f"  dates with |AC-Enphase| > 1.0 kWh: {(ov['ac_err'] > 1.0).sum()}")
print(f"  dates with AC=0 but Enphase>0.5: {((ov['ac_solar_kwh'] < 0.1) & (ov['en'] > 0.5)).sum()}")

big = ov[ov['ac_err'] > 1.0][['date', 'ac_solar_kwh', 'en']]
if len(big):
    print("\n  Large AC errors (>1 kWh vs Enphase):")
    for _, b in big.iterrows():
        print(f"    {b['date']}: ourAC={b['ac_solar_kwh']:.3f}  enphase={b['en']:.3f}  diff={b['ac_solar_kwh']-b['en']:+.3f}")

# SYS mismatch detail
sys_bad = [(d, sys_daily.get(d), t) for d, t in zip(daily['date'], daily['total_pv_kwh']) if d in sys_daily and abs(sys_daily[d] - float(t)) > 0.5]
if sys_bad:
    print(f"\n  SYS mismatches (system solar_gen vs derived total): {len(sys_bad)}")
    for d, s, t in sys_bad[:20]:
        print(f"    {d}: sysSol={s}  derivedTot={t}")
