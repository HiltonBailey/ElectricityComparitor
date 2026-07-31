#!/usr/bin/env python3
"""
Electricity cost calculator & web server.
Node-RED handles only CSV gap-filling; this server does all cost calculation and reporting.

Usage:
    python3 energy_server.py                    # Serve HTTP on :8080
    python3 energy_server.py --port 9090        # Custom port
    python3 energy_server.py --csv /path/to/5minelecNEW.csv
    python3 energy_server.py --daemon           # Daemon mode (background)

Endpoints:
    /                      HTML tabbed dashboard (reports + charts + config)
    /api/status            JSON server status
    /daily-report          HTML daily comparison table
    /5min-detail           HTML per-retailer 5-min breakdown
    /hourly-detail         HTML per-retailer hourly breakdown
    /api/daily-data        JSON daily summary
    /api/retailers         JSON retailer configs
    /api/chart-data        JSON chart series
    /api/retailer-config   HTML config editor (GET) / save (POST)
"""

import csv, json, math, os, sys, time, logging, hashlib
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
from pathlib import Path

# ─── Config ──────────────────────────────────────────────────────────────────

CSV_PATH = '5minelecNEW.csv'
CONFIG_PATH = 'retailer_config.csv'
PORT = 8080
DAEMON = False

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(message)s', datefmt='%H:%M:%S')
log = logging.getLogger('energy')

# ─── Cached Computation ─────────────────────────────────────────────────────

_cache = {'rows': [], 'retailers': [], 'data_hash': '', 'daily_summary': {}, 'chart_data': [],
          'five_min_detail': {}, 'last_mtime': 0}

def _file_hash(path):
    try:
        st = os.stat(path)
        return f'{st.st_mtime:.0f}_{st.st_size}'
    except OSError:
        return ''

def ensure_computed():
    """Recompute if CSV or config changed since last computation."""
    csv_hash = _file_hash(CSV_PATH)
    cfg_hash = _file_hash(CONFIG_PATH)
    combined = csv_hash + '|' + cfg_hash
    if combined == _cache['data_hash'] and _cache['daily_summary']:
        return
    log.info('Recomputing costs (CSV/config changed)...')
    t0 = time.time()
    try:
        retailers = load_retailer_config(CONFIG_PATH)
        rows = load_csv_rows(CSV_PATH)
        intervals = extract_intervals(rows)
        daily_data, daily_summary, chart_data, five_min_detail = calculate_costs(intervals, retailers)
        _cache.update({
            'rows': rows, 'retailers': retailers,
            'daily_summary': daily_summary, 'chart_data': chart_data,
            'five_min_detail': five_min_detail, 'data_hash': combined,
        })
        log.info(f'Computed {len(daily_summary)} days in {time.time()-t0:.1f}s')
    except Exception as e:
        log.error(f'Computation failed: {e}')

# ─── Retailer Config ─────────────────────────────────────────────────────────

def in_window(h, s, e):
    if s <= e: return s <= h < e
    else: return h >= s or h < e

def load_retailer_config(path):
    retailers = []
    numeric_fields = [
        'dsc', 'sub', 'off_pk', 'sh_pk', 'pk_pk',
        'off_fit', 'sh_fit', 'pk_fit', 'sp_fit', 'sp_fit2', 'sp_limit',
        'off_s', 'off_e', 'pk_s', 'pk_e', 'sp_s', 'sp_e',
        'off_fit_s', 'off_fit_e', 'sh_fit_s', 'sh_fit_e',
        'pk_fit_s', 'pk_fit_e', 'sp_fit_s', 'sp_fit_e',
        'fixed_export', 'ev_s', 'ev_e', 'ev_pk', 'off_limit', 'billing_day',
        'pea_base', 'pea_override'
    ]
    with open(path, newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            r = {k.strip(): v.strip() for k, v in row.items()}
            for fn in numeric_fields:
                if fn in r:
                    try: r[fn] = float(r[fn])
                    except ValueError: r[fn] = 0.0
            retailers.append(r)
    return retailers

# ─── CSV Reader ──────────────────────────────────────────────────────────────

def _f(v):
    try: return float(v)
    except: return 0.0

def load_csv_rows(path):
    rows = []
    with open(path, newline='') as f:
        reader = csv.reader(f)
        hdr = next(reader, None)
        if not hdr: return rows
        for line in reader:
            if len(line) < 15: continue
            rows.append({
                'pe': line[12].strip(),
                'Import_kWh': _f(line[14]),
                'export': _f(line[4]),
                'aemo_price': _f(line[11]),
            })
    return rows

def extract_intervals(rows):
    intervals = []
    prev_imp = prev_exp = 0.0
    for row in rows:
        pe = row['pe']
        ci = row['Import_kWh']; ce = row['export']
        i_kwh = max(0.0, ci - prev_imp)
        e_kwh = max(0.0, ce - prev_exp)
        prev_imp = ci; prev_exp = ce
        try:
            dt = datetime.strptime(pe, '%Y-%m-%d %H:%M:%S')
        except: continue
        intervals.append({
            'pe': pe, 'date': dt.strftime('%Y-%m-%d'), 'time': dt.strftime('%H:%M:%S'),
            'h': dt.hour + dt.minute / 60.0,
            'i_kwh': i_kwh, 'e_kwh': e_kwh, 'cum_imp': ci, 'cum_exp': ce,
            'aemo': row['aemo_price'],
        })
    return intervals

# ─── Cost Calculator ─────────────────────────────────────────────────────────

def _fixed_tou_interval(d, r, h, ti, ek):
    imp_rate = r.get('sh_pk', 0)
    if in_window(h, r.get('off_s', 0), r.get('off_e', 0)):
        imp_rate = r.get('off_pk', 0)
        if r.get('off_limit', 0) > 0 and r.get('off_pk', 0) == 0:
            fu = d.get('freeUsage', 0)
            if fu < r['off_limit']:
                fp = min(ti, r['off_limit'] - fu)
                d['freeUsage'] = fu + fp
                d['import'] += fp * imp_rate + (ti - fp) * r.get('sh_pk', 0)
                imp_rate = None
    elif in_window(h, r.get('ev_s', 0), r.get('ev_e', 0)) and r.get('ev_pk', 0) > 0:
        imp_rate = r.get('ev_pk', 0)
    elif in_window(h, r.get('pk_s', 0), r.get('pk_e', 0)):
        imp_rate = r.get('pk_pk', 0)
    if imp_rate is not None: d['import'] += ti * imp_rate
    if in_window(h, r.get('off_s', 0), r.get('off_e', 0)): d['offKwh'] += ti
    elif in_window(h, r.get('ev_s', 0), r.get('ev_e', 0)) and r.get('ev_pk', 0) > 0: d['evKwh'] += ti
    elif in_window(h, r.get('pk_s', 0), r.get('pk_e', 0)): d['pkKwh'] += ti
    else: d['shKwh'] += ti
    if in_window(h, r.get('sp_fit_s', 0), r.get('sp_fit_e', 0)) and r.get('sp_limit', 0) > 0:
        rem = r['sp_limit'] - d['spExportUsed']
        if rem > 0:
            sp = min(ek, rem); fb = ek - sp
            d['spExportUsed'] += sp; d['export'] += sp * r.get('sp_fit', 0)
            d['spExportKwh'] += sp
            if fb > 0:
                er2 = r.get('sp_fit2', 0)
                if in_window(h, r.get('pk_fit_s', 0), r.get('pk_fit_e', 0)): er2 = r.get('pk_fit', 0)
                d['export'] += fb * er2
                if er2 == r.get('pk_fit', 0): d['pkExportKwh'] += fb
                else: d['shExportKwh'] += fb
        else:
            er2 = r.get('sp_fit2', 0)
            if in_window(h, r.get('pk_fit_s', 0), r.get('pk_fit_e', 0)): er2 = r.get('pk_fit', 0)
            d['export'] += ek * er2
            if er2 == r.get('pk_fit', 0): d['pkExportKwh'] += ek
            else: d['shExportKwh'] += ek
    else:
        er = r.get('sh_fit', 0)
        if in_window(h, r.get('sp_fit_s', 0), r.get('sp_fit_e', 0)): er = r.get('sp_fit', 0)
        elif in_window(h, r.get('pk_fit_s', 0), r.get('pk_fit_e', 0)): er = r.get('pk_fit', 0)
        elif in_window(h, r.get('off_fit_s', 0), r.get('off_fit_e', 0)): er = r.get('off_fit', 0)
        d['export'] += ek * er
        if er == r.get('sp_fit', 0): d['spExportKwh'] += ek
        elif er == r.get('pk_fit', 0): d['pkExportKwh'] += ek
        elif er == r.get('off_fit', 0): d['offExportKwh'] += ek
        else: d['shExportKwh'] += ek

def _billing_period_key(date_str, billing_day):
    dt = datetime.strptime(date_str, '%Y-%m-%d')
    if dt.day >= billing_day:
        return dt.strftime('%Y-%m')
    else:
        if dt.month == 1:
            return f'{dt.year-1}-12'
        else:
            return f'{dt.year}-{dt.month-1:02d}'

def calculate_costs(intervals, retailers):
    iv_by_date = {}
    for iv in intervals:
        iv_by_date.setdefault(iv['date'], []).append(iv)

    # First pass: compute PEA per billing period for hybrid retailers
    billing_periods = {}
    for r in retailers:
        if r['model'] != 'hybrid': continue
        bp = r.get('billing_day', 4)
        pb = float(r.get('pea_base', 0.017))
        # Group all intervals by billing period
        for iv in intervals:
            pk = _billing_period_key(iv['date'], int(bp))
            if pk not in billing_periods:
                billing_periods[pk] = {'aemo_sum': 0.0, 'aemo_count': 0,
                                       'import_aemo_sum': 0.0, 'import_sum': 0.0,
                                       'pea_rate': 0.0}
            bp_data = billing_periods[pk]
            bp_data['aemo_sum'] += iv['aemo']
            bp_data['aemo_count'] += 1
            bp_data['import_aemo_sum'] += iv['i_kwh'] * iv['aemo']
            bp_data['import_sum'] += iv['i_kwh']
    pea_by_period = {}
    for r in retailers:
        if r['model'] != 'hybrid': continue
        po = r.get('pea_override', 0)
        if float(po) != 0:
            for pk in set(_billing_period_key(iv['date'], int(r.get('billing_day', 4))) for iv in intervals):
                pea_by_period[pk] = float(po)
        else:
            pb = float(r.get('pea_base', 0.017))
            for pk, bp_data in billing_periods.items():
                if bp_data['aemo_count'] == 0 or bp_data['import_sum'] == 0:
                    pea_by_period[pk] = 0.0
                    continue
                twap = bp_data['aemo_sum'] / bp_data['aemo_count']
                lwap = bp_data['import_aemo_sum'] / bp_data['import_sum']
                cpea = lwap - twap
                pea_by_period[pk] = cpea - pb
    _cache['pea_periods'] = billing_periods

    daily_data = {}
    for date_str, day_ivs in iv_by_date.items():
        dd = {}
        for r in retailers:
            dd[r['name']] = {
                'intervals': 0, 'lastTime': '', 'totalImport': 0.0, 'totalExport': 0.0,
                'import': 0.0, 'export': 0.0, 'spExportUsed': 0.0,
                'hr18': 0.0, 'hr19': 0.0, 'hr20': 0.0,
                'offKwh': 0.0, 'shKwh': 0.0, 'pkKwh': 0.0, 'evKwh': 0.0,
                'spExportKwh': 0.0, 'pkExportKwh': 0.0, 'shExportKwh': 0.0, 'offExportKwh': 0.0,
            }
        daily_data[date_str] = dd
        for iv in day_ivs:
            h = iv['h']; ti = iv['i_kwh']; ek = iv['e_kwh']
            for r in retailers:
                d = dd[r['name']]
                d['intervals'] += 1; d['lastTime'] = iv['time']
                d['totalImport'] += ti; d['totalExport'] += ek
                if 18 <= h < 19: d['hr18'] += ti
                elif 19 <= h < 20: d['hr19'] += ti
                elif 20 <= h < 21: d['hr20'] += ti
                if r['model'] == 'fixed_tou':
                    _fixed_tou_interval(d, r, h, ti, ek)
                elif r['model'] == 'hybrid':
                    bp = int(r.get('billing_day', 4))
                    pk = _billing_period_key(date_str, bp)
                    pea = pea_by_period.get(pk, 0.0)
                    flow_rate = r.get('sh_pk', 0.2) + pea
                    d['import'] += ti * flow_rate
                    parts = iv['time'].split(':'); fh = int(parts[0]) + int(parts[1]) / 60.0
                    sps = float(r.get('sp_fit_s', 17.5)); spe = float(r.get('sp_fit_e', 21.5))
                    if sps <= fh < spe and r.get('sp_limit', 0) > 0:
                        rem = r['sp_limit'] - d['spExportUsed']
                        if rem > 0:
                            sp = min(ek, rem); fb = ek - sp
                            d['spExportUsed'] += sp; d['export'] += sp * r.get('sp_fit', 0)
                            d['spExportKwh'] += sp
                            if fb > 0:
                                er2 = r.get('sp_fit2', 0)
                                d['export'] += fb * er2; d['shExportKwh'] += fb
                        else:
                            er2 = r.get('sp_fit2', 0)
                            d['export'] += ek * er2; d['shExportKwh'] += ek
                    else:
                        d['export'] += ek * r.get('off_fit', 0)
                elif r['model'] == 'variable':
                    d['import'] += ti * (iv['aemo'] + 0.0515)
                    d['export'] += ek * iv['aemo'] * r.get('off_fit', 0)
    
    daily_summary = {}; chart_data = []; five_min_detail = {}
    
    for date_str in sorted(daily_data.keys()):
        d0 = daily_data[date_str][retailers[0]['name']]
        ds = {'totalImport': round(d0['totalImport'], 3), 'totalExport': round(d0['totalExport'], 3), 'retailers': {}}
        cheapest_net = float('inf'); cheapest_name = ''
        for r in retailers:
            d = daily_data[date_str][r['name']]
            if r['model'] == 'fixed_tou':
                da_imp = (d['offKwh'] * r.get('off_pk', 0) + d['shKwh'] * r.get('sh_pk', 0) +
                          d['pkKwh'] * r.get('pk_pk', 0) + d['evKwh'] * r.get('ev_pk', 0))
                da_exp = (d['spExportKwh'] * r.get('sp_fit', 0) + d['pkExportKwh'] * r.get('pk_fit', 0) +
                          d['shExportKwh'] * r.get('sh_fit', 0) + d['offExportKwh'] * r.get('off_fit', 0))
                d['import'] = da_imp; d['export'] = da_exp
            elif r['model'] == 'hybrid':
                bp_key = _billing_period_key(date_str, int(r.get('billing_day', 4)))
                pea = pea_by_period.get(bp_key, 0.0)
                eff_rate = float(r.get('sh_pk', 0.2)) + pea
                d['import'] = d['totalImport'] * eff_rate
            
            ri = round(d['import'], 2); re = round(d['export'], 2); rd = round(r.get('dsc', 0), 2)
            gr = r.get('glo_rebate', '0')
            if float(gr) > 0 and d['hr18'] < 0.1 and d['hr19'] < 0.1 and d['hr20'] < 0.1:
                reb = 1.00
            else: reb = 0.0
            d['gloRebate'] = reb; d['net'] = round(ri - re + rd - reb, 2)
            
            ds['retailers'][r['name']] = {'dsc': rd, 'import': ri, 'export': re, 'net': d['net'], 'gloRebate': reb}
            if d['net'] < cheapest_net: cheapest_net = d['net']; cheapest_name = r['name']
        ds['cheapest'] = cheapest_name; daily_summary[date_str] = ds
        cd = {'date': date_str, 'retailers': {}}
        for r in retailers:
            cd['retailers'][r['name']] = round(daily_data[date_str][r['name']]['net'], 2)
        cd['cheapest'] = cheapest_name; chart_data.append(cd)
    
    # 5-min detail
    for r in [x for x in retailers if x['model'] in ('fixed_tou', 'hybrid')]:
        fm = {}
        for date_str in sorted(iv_by_date.keys()):
            outs = []; spu = 0; hr18 = hr19 = hr20 = 0.0; tik = tek = tic = tec = 0.0
            bp_key = _billing_period_key(date_str, int(r.get('billing_day', 4)))
            pea = pea_by_period.get(bp_key, 0.0)
            for iv in iv_by_date[date_str]:
                h = iv['h']; i_kwh = iv['i_kwh']; e_kwh = iv['e_kwh']
                if 18 <= h < 19: hr18 += i_kwh
                elif 19 <= h < 20: hr19 += i_kwh
                elif 20 <= h < 21: hr20 += i_kwh
                tik += i_kwh; tek += e_kwh
                if r['model'] == 'fixed_tou':
                    ir = r.get('sh_pk', 0); tou = 'Sho'
                    if in_window(h, r.get('off_s', 0), r.get('off_e', 0)):
                        ir = r.get('off_pk', 0); tou = 'Off'
                    elif in_window(h, r.get('ev_s', 0), r.get('ev_e', 0)) and r.get('ev_pk', 0) > 0:
                        ir = r.get('ev_pk', 0); tou = 'EV '
                    elif in_window(h, r.get('pk_s', 0), r.get('pk_e', 0)):
                        ir = r.get('pk_pk', 0); tou = 'Pk '
                    er = r.get('sh_fit', 0); fit = 'Sho'
                    if in_window(h, r.get('sp_s', 0), r.get('sp_e', 0)) and r.get('sp_limit', 0) > 0:
                        rem = r['sp_limit'] - spu
                        if rem > 0:
                            sp = min(e_kwh, rem); fb = e_kwh - sp; spu += sp
                            er = r.get('sp_fit', 0); fit = 'Sp '
                            if fb > 0:
                                fr = r.get('sh_fit', 0)
                                if in_window(h, r.get('pk_s', 0), r.get('pk_e', 0)): fr = r.get('pk_fit', 0)
                                er = (sp * r.get('sp_fit', 0) + fb * fr) / e_kwh if e_kwh > 0 else 0
                                fit = 'Sp+'
                        else:
                            er = r.get('sh_fit', 0); fit = 'Sho'
                            if in_window(h, r.get('pk_s', 0), r.get('pk_e', 0)): er = r.get('pk_fit', 0); fit = 'Pk '
                    else:
                        if in_window(h, r.get('sp_s', 0), r.get('sp_e', 0)): er = r.get('sp_fit', 0); fit = 'Sp '
                        elif in_window(h, r.get('pk_s', 0), r.get('pk_e', 0)): er = r.get('pk_fit', 0); fit = 'Pk '
                        elif in_window(h, r.get('off_s', 0), r.get('off_e', 0)): er = r.get('off_fit', 0); fit = 'Off'
                    ic = i_kwh * ir; ec = e_kwh * er
                else:
                    tou = 'Flat'; ir = r.get('sh_pk', 0.2) + pea
                    er = r.get('off_fit', 0); fit = 'Off'
                    if in_window(h, r.get('sp_fit_s', 0), r.get('sp_fit_e', 0)):
                        rem2 = r.get('sp_limit', 0) - spu
                        if rem2 > 0:
                            sp2 = min(e_kwh, rem2); fb2 = e_kwh - sp2; spu += sp2
                            er = (sp2 * r.get('sp_fit', 0) + fb2 * r.get('sp_fit2', 0)) / e_kwh if e_kwh > 0 else 0
                            fit = 'Sp '
                        else:
                            er = r.get('sp_fit2', 0); fit = 'Sp2'
                    ic = i_kwh * ir; ec = e_kwh * er
                tic += ic; tec += ec
                outs.append({'time': iv['time'][:5], 'tou': tou, 'fit': fit, 'ik': round(i_kwh, 3), 'ek': round(e_kwh, 3),
                             'ir': round(ir, 4), 'er': round(er, 4), 'ic': round(ic, 3), 'ec': round(ec, 3)})
            reb = 1.0 if (float(r.get('glo_rebate','0')) > 0 and hr18 < 0.1 and hr19 < 0.1 and hr20 < 0.1) else 0.0
            nt = round(tic - tec + r.get('dsc', 0) - reb, 2)
            outs.append({'time': 'TOTAL', 'ik': round(tik, 3), 'ek': round(tek, 3),
                         'ic': round(tic, 3), 'ec': round(tec, 3),
                         'dsc': r.get('dsc', 0), 'rebate': reb, 'net': nt,
                         'hr18': round(hr18, 3), 'hr19': round(hr19, 3), 'hr20': round(hr20, 3)})
            fm[date_str] = {'intervals': outs, 'summary': {'hr18': hr18, 'hr19': hr19, 'hr20': hr20, 'net': nt, 'dsc': r.get('dsc', 0), 'rebate': reb}}
        five_min_detail[r['name']] = fm
    
    return daily_data, daily_summary, chart_data, five_min_detail

# ─── HTML Generation ─────────────────────────────────────────────────────────

def daily_report_html(daily_summary, retailers, days=None):
    html = '''<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:13px;white-space:nowrap">
    <thead><tr style="background:#1a1a1a;color:white">
    <th style="padding:4px;text-align:left;position:sticky;left:0;background:#1a1a1a;z-index:2">Date</th>
    <th style="padding:4px;text-align:right">Imp kWh</th>
    <th style="padding:4px;text-align:right">Exp kWh</th>'''
    for r in retailers:
        html += f'<th style="padding:4px;text-align:right">{r["name"]}</th>'
    html += '<th style="padding:4px;text-align:right;color:#4CAF50">Cheapest</th></tr></thead><tbody>'
    dates = sorted(daily_summary.keys(), reverse=True)
    if days is not None: dates = dates[:max(1, days)]
    for ds in dates:
        d = daily_summary[ds]
        html += f'<tr style="background:#111"><td style="padding:4px;text-align:left;color:#aaa;position:sticky;left:0;background:#111;z-index:1">{ds}</td>'
        html += f'<td style="padding:4px;text-align:right;color:#8cf">{d["totalImport"]:.1f}</td>'
        html += f'<td style="padding:4px;text-align:right;color:#fc8">{d["totalExport"]:.1f}</td>'
        for r in retailers:
            rd = d['retailers'].get(r['name'], {})
            v = rd.get('net', 0)
            c = '#4CAF50' if r['name'] == d['cheapest'] else '#ccc'
            html += f'<td style="padding:4px;text-align:right;color:{c}">${v:.2f}</td>'
        html += f'<td style="padding:4px;text-align:right;color:#ccc;font-weight:bold">{d["cheapest"]}</td></tr>'
    ti = sum(daily_summary[ds]['totalImport'] for ds in dates)
    te = sum(daily_summary[ds]['totalExport'] for ds in dates)
    rtot = {r['name']: sum(daily_summary[ds]['retailers'].get(r['name'], {}).get('net', 0) for ds in dates) for r in retailers}
    cheapest = min(rtot, key=lambda rn: rtot[rn])
    html += '<tr style="background:#2a2a2a;font-weight:bold">'
    html += f'<td style="padding:4px;text-align:left;color:white;position:sticky;left:0;background:#2a2a2a;z-index:1">Total</td>'
    html += f'<td style="padding:4px;text-align:right;color:#8cf">{ti:.1f}</td>'
    html += f'<td style="padding:4px;text-align:right;color:#fc8">{te:.1f}</td>'
    for r in retailers:
        v = rtot[r['name']]; c = '#4CAF50' if r['name'] == cheapest else '#ccc'
        html += f'<td style="padding:4px;text-align:right;color:{c}">${v:.2f}</td>'
    html += f'<td style="padding:4px;text-align:right;color:#ccc;font-weight:bold">{cheapest}</td></tr>'
    html += '</tbody></table></div>'
    return html

def monthly_report_html(daily_summary, retailers):
    months = {}
    for ds, d in sorted(daily_summary.items()):
        m = ds[:7]
        if m not in months:
            months[m] = {'imp': 0, 'exp': 0, 'retailers': {}}
        months[m]['imp'] += d['totalImport']
        months[m]['exp'] += d['totalExport']
        for r in retailers:
            rn = r['name']
            v = d['retailers'].get(rn, {}).get('net', 0)
            months[m]['retailers'][rn] = months[m]['retailers'].get(rn, 0) + v
    html = '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:13px;white-space:nowrap">'
    html += '<thead><tr style="background:#1a1a1a;color:white">'
    html += '<th style="padding:4px;text-align:left;position:sticky;left:0;background:#1a1a1a;z-index:2">Month</th>'
    html += '<th style="padding:4px;text-align:right">Imp kWh</th><th style="padding:4px;text-align:right">Exp kWh</th>'
    for r in retailers:
        html += f'<th style="padding:4px;text-align:right">{r["name"]}</th>'
    html += '<th style="padding:4px;text-align:right;color:#4CAF50">Cheapest</th></tr></thead><tbody>'
    for m in sorted(months.keys(), reverse=True):
        mm = months[m]
        cheapest = min(mm['retailers'], key=lambda rn: mm['retailers'][rn])
        html += f'<tr style="background:#111"><td style="padding:4px;text-align:left;color:#aaa;position:sticky;left:0;background:#111;z-index:1">{m}</td>'
        html += f'<td style="padding:4px;text-align:right;color:#8cf">{mm["imp"]:.1f}</td>'
        html += f'<td style="padding:4px;text-align:right;color:#fc8">{mm["exp"]:.1f}</td>'
        for r in retailers:
            v = mm['retailers'].get(r['name'], 0)
            c = '#4CAF50' if r['name'] == cheapest else '#ccc'
            html += f'<td style="padding:4px;text-align:right;color:{c}">${v:.2f}</td>'
        html += f'<td style="padding:4px;text-align:right;color:#ccc;font-weight:bold">{cheapest}</td></tr>'
    t_imp = sum(mm['imp'] for mm in months.values())
    t_exp = sum(mm['exp'] for mm in months.values())
    t_ret = {r['name']: sum(mm['retailers'].get(r['name'], 0) for mm in months.values()) for r in retailers}
    t_cheapest = min(t_ret, key=lambda rn: t_ret[rn])
    html += '<tr style="background:#222;font-weight:bold"><td style="padding:4px;text-align:left;color:#fff;position:sticky;left:0;background:#222;z-index:1">TOTAL</td>'
    html += f'<td style="padding:4px;text-align:right;color:#8cf">{t_imp:.1f}</td><td style="padding:4px;text-align:right;color:#fc8">{t_exp:.1f}</td>'
    for r in retailers:
        v = t_ret.get(r['name'], 0)
        c = '#4CAF50' if r['name'] == t_cheapest else '#fff'
        html += f'<td style="padding:4px;text-align:right;color:{c}">${v:.2f}</td>'
    html += f'<td style="padding:4px;text-align:right;color:#4CAF50">{t_cheapest}</td></tr>'
    html += '</tbody></table></div>'
    return html

_SEASONS = [('Summer', [12, 1, 2]), ('Autumn', [3, 4, 5]), ('Winter', [6, 7, 8]), ('Spring', [9, 10, 11])]

def seasonal_report_html(daily_summary, retailers):
    seasons = {}
    for ds, d in sorted(daily_summary.items()):
        y = int(ds[:4]); mo = int(ds[5:7])
        if mo == 12:
            label = f'Summer {y}/{y+1}'
        elif mo <= 2:
            label = f'Summer {y-1}/{y}'
        elif mo <= 5:
            label = f'Autumn {y}'
        elif mo <= 8:
            label = f'Winter {y}'
        else:
            label = f'Spring {y}'
        if label not in seasons:
            seasons[label] = {'imp': 0, 'exp': 0, 'retailers': {}}
        seasons[label]['imp'] += d['totalImport']
        seasons[label]['exp'] += d['totalExport']
        for r in retailers:
            rn = r['name']
            v = d['retailers'].get(rn, {}).get('net', 0)
            seasons[label]['retailers'][rn] = seasons[label]['retailers'].get(rn, 0) + v
    html = '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:13px;white-space:nowrap">'
    html += '<thead><tr style="background:#1a1a1a;color:white">'
    html += '<th style="padding:4px;text-align:left;position:sticky;left:0;background:#1a1a1a;z-index:2">Season</th>'
    html += '<th style="padding:4px;text-align:right">Imp kWh</th><th style="padding:4px;text-align:right">Exp kWh</th>'
    for r in retailers:
        html += f'<th style="padding:4px;text-align:right">{r["name"]}</th>'
    html += '<th style="padding:4px;text-align:right;color:#4CAF50">Cheapest</th></tr></thead><tbody>'
    for s in sorted(seasons.keys(), reverse=True):
        ss = seasons[s]
        cheapest = min(ss['retailers'], key=lambda rn: ss['retailers'][rn])
        html += f'<tr style="background:#111"><td style="padding:4px;text-align:left;color:#aaa;position:sticky;left:0;background:#111;z-index:1">{s}</td>'
        html += f'<td style="padding:4px;text-align:right;color:#8cf">{ss["imp"]:.1f}</td>'
        html += f'<td style="padding:4px;text-align:right;color:#fc8">{ss["exp"]:.1f}</td>'
        for r in retailers:
            v = ss['retailers'].get(r['name'], 0)
            c = '#4CAF50' if r['name'] == cheapest else '#ccc'
            html += f'<td style="padding:4px;text-align:right;color:{c}">${v:.2f}</td>'
        html += f'<td style="padding:4px;text-align:right;color:#ccc;font-weight:bold">{cheapest}</td></tr>'
    t_imp = sum(ss['imp'] for ss in seasons.values())
    t_exp = sum(ss['exp'] for ss in seasons.values())
    t_ret = {r['name']: sum(ss['retailers'].get(r['name'], 0) for ss in seasons.values()) for r in retailers}
    t_cheapest = min(t_ret, key=lambda rn: t_ret[rn])
    html += '<tr style="background:#222;font-weight:bold"><td style="padding:4px;text-align:left;color:#fff;position:sticky;left:0;background:#222;z-index:1">TOTAL</td>'
    html += f'<td style="padding:4px;text-align:right;color:#8cf">{t_imp:.1f}</td><td style="padding:4px;text-align:right;color:#fc8">{t_exp:.1f}</td>'
    for r in retailers:
        v = t_ret.get(r['name'], 0)
        c = '#4CAF50' if r['name'] == t_cheapest else '#fff'
        html += f'<td style="padding:4px;text-align:right;color:{c}">${v:.2f}</td>'
    html += f'<td style="padding:4px;text-align:right;color:#4CAF50">{t_cheapest}</td></tr>'
    html += '</tbody></table></div>'
    return html

def fivemin_html(fm, ds, rname, date_str):
    d = fm.get(rname, {}).get(date_str)
    if not d: return '<div style="color:#888;padding:20px">No data</div>'
    ivs = d['intervals']; tot = ivs[-1] if ivs else {}
    dr = ds.get(date_str, {}).get('retailers', {}).get(rname, {})
    if dr:
        ik = ds[date_str].get('totalImport', tot.get('ik', 0))
        ek = ds[date_str].get('totalExport', tot.get('ek', 0))
        ic = dr.get('import', tot.get('ic', 0))
        ec = dr.get('export', tot.get('ec', 0))
        dsc = dr.get('dsc', tot.get('dsc', 0))
        reb = dr.get('gloRebate', tot.get('rebate', 0))
        net = dr.get('net', tot.get('net', 0))
    else:
        ik = tot.get('ik', 0); ek = tot.get('ek', 0)
        ic = tot.get('ic', 0); ec = tot.get('ec', 0)
        dsc = tot.get('dsc', 0); reb = tot.get('rebate', 0)
        net = tot.get('net', 0)
    h = (f'<div style="display:flex;justify-content:space-between;padding:6px 10px;background:#151515;color:#aaa;font-size:14px;font-weight:bold;border-bottom:1px solid #222">'
         f'<span>{date_str} — {rname}</span>'
         f'<span style="white-space:nowrap">{ik:.2f} kWh &nbsp;&nbsp;|&nbsp;&nbsp; Exp {ek:.2f} kWh &nbsp;&nbsp;|&nbsp;&nbsp; '
         f'Import ${ic:.2f} &nbsp;&nbsp;|&nbsp;&nbsp; Export ${ec:.2f} &nbsp;&nbsp;|&nbsp;&nbsp; '
         f'DSC ${dsc:.2f} &nbsp;&nbsp;|&nbsp;&nbsp; Rebate ${reb:.2f} &nbsp;&nbsp;|&nbsp;&nbsp; '
         f'Net ${net:.2f}</span></div>')
    t = '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:15px"><thead><tr style="background:#1a1a1a;color:white;position:sticky;top:0;z-index:1">'
    t += '<th style="padding:4px 6px;text-align:left">Time</th><th style="padding:4px 6px;text-align:left">TOU</th>'
    t += '<th style="padding:4px 6px;text-align:left">FIT</th>'
    t += '<th style="padding:4px 6px;text-align:right">Imp kWh</th><th style="padding:4px 6px;text-align:right">Exp kWh</th>'
    t += '<th style="padding:4px 6px;text-align:right">Imp $/kWh</th><th style="padding:4px 6px;text-align:right">Exp $/kWh</th>'
    t += '<th style="padding:4px 6px;text-align:right">Imp $</th><th style="padding:4px 6px;text-align:right">Exp $</th>'
    t += '<th style="padding:4px 6px;text-align:right">Net $</th></tr></thead><tbody>'
    for iv in ivs:
        if iv.get('time') == 'TOTAL': continue
        nt = iv.get('ic', 0) - iv.get('ec', 0)
        t += (f'<tr><td style="padding:2px 6px;color:#aaa">{iv["time"]}</td>'
              f'<td style="padding:2px 6px;color:#ccc">{iv.get("tou","")}</td>'
              f'<td style="padding:2px 6px;color:#fc8">{iv.get("fit","")}</td>'
              f'<td style="padding:2px 6px;text-align:right;color:#8cf">{iv["ik"]:.3f}</td>'
              f'<td style="padding:2px 6px;text-align:right;color:#fc8">{iv["ek"]:.3f}</td>'
              f'<td style="padding:2px 6px;text-align:right;color:#888">{iv["ir"]:.4f}</td>'
              f'<td style="padding:2px 6px;text-align:right;color:#888">{iv["er"]:.4f}</td>'
              f'<td style="padding:2px 6px;text-align:right;color:#ff8a65">{iv["ic"]:.3f}</td>'
              f'<td style="padding:2px 6px;text-align:right;color:#8fbc8f">{iv["ec"]:.3f}</td>'
              f'<td style="padding:2px 6px;text-align:right;color:{"#ff5252" if nt>=0 else "#4CAF50"}">{nt:.3f}</td></tr>')
    t += '</tbody></table></div>'
    return h + t

# ─── Hourly Report ─────────────────────────────────────────────────────────────

def hourly_html(fm, ds, rname, date_str):
    d = fm.get(rname, {}).get(date_str)
    if not d: return '<div style="color:#888;padding:20px">No data</div>'
    ivs = d['intervals']
    dr = ds.get(date_str, {}).get('retailers', {}).get(rname, {})
    tot = ivs[-1] if ivs else {}
    if dr:
        ik = ds[date_str].get('totalImport', tot.get('ik', 0))
        ek = ds[date_str].get('totalExport', tot.get('ek', 0))
        ic = dr.get('import', tot.get('ic', 0))
        ec = dr.get('export', tot.get('ec', 0))
        dsc = dr.get('dsc', tot.get('dsc', 0))
        reb = dr.get('gloRebate', tot.get('rebate', 0))
        net = dr.get('net', tot.get('net', 0))
    else:
        ik = tot.get('ik', 0); ek = tot.get('ek', 0)
        ic = tot.get('ic', 0); ec = tot.get('ec', 0)
        dsc = tot.get('dsc', 0); reb = tot.get('rebate', 0)
        net = tot.get('net', 0)

    hours = {}
    for iv in ivs:
        if iv.get('time') == 'TOTAL': continue
        h = iv['time'][:2]
        hours.setdefault(h, {'ik': 0.0, 'ek': 0.0, 'ic': 0.0, 'ec': 0.0, 'count': 0, 'tou': iv.get('tou', ''), 'fit_counts': {}})
        hours[h]['ik'] += iv['ik']
        hours[h]['ek'] += iv['ek']
        hours[h]['ic'] += iv['ic']
        hours[h]['ec'] += iv['ec']
        hours[h]['count'] += 1
        ft = iv.get('fit', '')
        hours[h]['fit_counts'][ft] = hours[h]['fit_counts'].get(ft, 0) + 1

    hdr = (f'<div style="display:flex;justify-content:space-between;padding:6px 10px;background:#151515;color:#aaa;font-size:14px;font-weight:bold;border-bottom:1px solid #222">'
           f'<span>{date_str} — {rname}</span>'
           f'<span style="white-space:nowrap">{ik:.2f} kWh &nbsp;&nbsp;|&nbsp;&nbsp; Exp {ek:.2f} kWh &nbsp;&nbsp;|&nbsp;&nbsp; '
           f'Import ${ic:.2f} &nbsp;&nbsp;|&nbsp;&nbsp; Export ${ec:.2f} &nbsp;&nbsp;|&nbsp;&nbsp; '
           f'DSC ${dsc:.2f} &nbsp;&nbsp;|&nbsp;&nbsp; Rebate ${reb:.2f} &nbsp;&nbsp;|&nbsp;&nbsp; '
           f'Net ${net:.2f}</span></div>')
    t = '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:15px"><thead><tr style="background:#1a1a1a;color:white;position:sticky;top:0;z-index:1">'
    t += '<th style="padding:4px 6px;text-align:left">Hour</th><th style="padding:4px 6px;text-align:left">TOU</th>'
    t += '<th style="padding:4px 6px;text-align:left">FIT</th>'
    t += '<th style="padding:4px 6px;text-align:right">Imp kWh</th><th style="padding:4px 6px;text-align:right">Exp kWh</th>'
    t += '<th style="padding:4px 6px;text-align:right">Avg Imp $/kWh</th><th style="padding:4px 6px;text-align:right">Avg Exp $/kWh</th>'
    t += '<th style="padding:4px 6px;text-align:right">Imp $</th><th style="padding:4px 6px;text-align:right">Exp $</th>'
    t += '<th style="padding:4px 6px;text-align:right">Net $</th></tr></thead><tbody>'
    for h in sorted(hours.keys(), key=int):
        hv = hours[h]
        inet = hv['ic'] - hv['ec']
        avg_ir = hv['ic'] / hv['ik'] if hv['ik'] > 0 else 0
        avg_er = hv['ec'] / hv['ek'] if hv['ek'] > 0 else 0
        label = f'{h}:00-{int(h)+1}:00'
        dom_fit = max(hv['fit_counts'], key=hv['fit_counts'].get) if hv['fit_counts'] else ''
        t += (f'<tr><td style="padding:2px 6px;color:#aaa">{label}</td>'
              f'<td style="padding:2px 6px;color:#ccc">{hv["tou"]}</td>'
              f'<td style="padding:2px 6px;color:#fc8">{dom_fit}</td>'
              f'<td style="padding:2px 6px;text-align:right;color:#8cf">{hv["ik"]:.3f}</td>'
              f'<td style="padding:2px 6px;text-align:right;color:#fc8">{hv["ek"]:.3f}</td>'
              f'<td style="padding:2px 6px;text-align:right;color:#888">{avg_ir:.4f}</td>'
              f'<td style="padding:2px 6px;text-align:right;color:#888">{avg_er:.4f}</td>'
              f'<td style="padding:2px 6px;text-align:right;color:#ff8a65">{hv["ic"]:.3f}</td>'
              f'<td style="padding:2px 6px;text-align:right;color:#8fbc8f">{hv["ec"]:.3f}</td>'
              f'<td style="padding:2px 6px;text-align:right;color:{"#ff5252" if inet>=0 else "#4CAF50"}">{inet:.3f}</td></tr>')
    t += '</tbody></table></div>'
    return hdr + t


# ─── Dashboard (Tabbed UI) ────────────────────────────────────────────────────

_PAGE_PREFIX = '''<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Energy Dashboard</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d0d0d;color:#ccc;font-family:monospace;font-size:13px;height:100vh;display:flex;flex-direction:column}
.subtabs{display:flex;background:#151515;border-bottom:1px solid #2a2a2a;flex-shrink:0}
.subtabs button{padding:6px 14px;cursor:pointer;color:#666;font-family:monospace;border:none;background:none;border-bottom:2px solid transparent}
.subtabs button:hover{color:#ccc;background:#1f1f1f}
.subtabs button.active{color:#4CAF50;border-bottom-color:#4CAF50}
.subtab-content{flex:1;min-height:0;display:none;overflow:hidden;flex-direction:column}
.subtab-content.active{display:flex}
.subtab-content iframe{width:100%;height:100%;border:none;background:#0d0d0d}
.report-controls{padding:4px 6px;display:flex;gap:4px;flex-wrap:wrap;align-items:center;background:#111;flex-shrink:0}
.report-controls label{color:#888;display:flex;align-items:center;gap:3px}
.report-controls input,.report-controls select{background:#1a1a1a;color:#ccc;border:1px solid #333;padding:2px 4px;font-family:monospace}
.report-controls input[type=date]{width:clamp(90px,20vw,150px)}
.report-controls input[type=number]{width:45px}
.report-controls select{width:clamp(80px,18vw,140px)}
#5minStatus{color:#666;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:200px}
.chart-controls{margin-bottom:6px;display:flex;gap:4px;flex-wrap:wrap;align-items:center}
.chart-controls label{color:#888;display:flex;align-items:center;gap:3px}
.chart-controls select,.chart-controls input{background:#1a1a1a;color:#ccc;border:1px solid #333;padding:2px 4px;font-family:monospace}
.chart-legend{display:flex;gap:8px;flex-wrap:wrap;margin:6px 0}
.chart-legend span{cursor:pointer;padding:2px 6px;border-radius:3px;border:1px solid transparent}
.chart-legend span.active{border-color:#4CAF50}
</style></head><body>
'''

_REPORTS_HTML = '''<div class=subtabs>
<button class=active onclick="switchSub(this,'sub-seasonal')">Seasonal</button>
<button onclick="switchSub(this,'sub-monthly')">Monthly</button>
<button onclick="switchSub(this,'sub-daily')">Daily</button>
<button onclick="switchSub(this,'sub-hourly')">Hourly</button>
<button onclick="switchSub(this,'sub-5min')">5-Min</button>
</div>
<div class=subtab-content id=sub-seasonal style="display:flex">
<div style=flex:1><iframe src=/seasonal-report style="width:100%;height:100%;border:none;background:#0d0d0d"></iframe></div>
</div>
<div class=subtab-content id=sub-monthly>
<div style=flex:1><iframe src=/monthly-report style="width:100%;height:100%;border:none;background:#0d0d0d"></iframe></div>
</div>
<div class=subtab-content id=sub-daily>
<div class=report-controls>
<label>From <input type=date id=fDate onchange=loadDaily()></label>
<label>To <input type=date id=tDate onchange=loadDaily()></label>
<label>Days <input type=number id=daysNum value=90 min=1 max=365 onchange=loadDaily()></label>
<label>Billing Period <select id=bpSel onchange=loadDaily()><option value="">All</option></select></label>
</div>
<div style=flex:1><iframe id=dailyFrame src=/daily-report?days=90 style="width:100%;height:100%;border:none;background:#0d0d0d"></iframe></div>
</div>
<div class=subtab-content id=sub-5min>
<div class=report-controls>
<label>Retailer <select id=retailerSel onchange=load5min()></select></label>
<label>Date <input type=date id=dateSel onchange=load5min()></label>
<label style=color:#888 id=5minStatus></label>
</div>
<div style=flex:1><iframe id=5minFrame style="width:100%;height:100%;border:none;background:#0d0d0d"></iframe></div>
</div>
<div class=subtab-content id=sub-hourly>
<div class=report-controls>
<label>Retailer <select id=retailerSel2 onchange=loadHourly()></select></label>
<label>Date <input type=date id=dateSel2 onchange=loadHourly()></label>
<label style=color:#888 id=hourlyStatus></label>
</div>
<div style=flex:1><iframe id=hourlyFrame style="width:100%;height:100%;border:none;background:#0d0d0d"></iframe></div>
</div>
<script>
function switchSub(btn,id){
document.querySelectorAll('.subtabs button').forEach(function(b){b.classList.remove('active')});
btn.classList.add('active');
document.querySelectorAll('.subtab-content').forEach(function(c){c.classList.remove('active');c.style.display='none'});
document.getElementById(id).style.display='flex';
if(id==='sub-5min')populate5minSelectors();
if(id==='sub-hourly')populateHourlySelectors();
}
var allRetailers=null,allDates=null;
function populate5minSelectors(){
if(allRetailers)return;
var rs=document.getElementById('retailerSel'),ds=document.getElementById('dateSel');
fetch('/api/retailers').then(function(r){return r.json()}).then(function(data){
allRetailers=data;rs.innerHTML='';
allRetailers.forEach(function(r,i){
var o=document.createElement('option');o.value=r.name;o.textContent=r.name;rs.appendChild(o)});
load5min()});
if(!allDates){
fetch('/api/daily-data').then(function(r){return r.json()}).then(function(data){
allDates=Object.keys(data).sort().reverse();
if(allDates.length)ds.value=allDates[0];
load5min()});
}}
function load5min(){
var r=document.getElementById('retailerSel').value,d=document.getElementById('dateSel').value;
if(!r||!d)return;
document.getElementById('5minStatus').textContent=r+' — '+d;
document.getElementById('5minFrame').src='/5min-detail?retailer='+encodeURIComponent(r)+'&date='+encodeURIComponent(d);
}
var hourlyRetailers=null,hourlyDates=null;
function populateHourlySelectors(){
if(hourlyRetailers)return;
var rs=document.getElementById('retailerSel2'),ds=document.getElementById('dateSel2');
fetch('/api/retailers').then(function(r){return r.json()}).then(function(data){
hourlyRetailers=data;rs.innerHTML='';
hourlyRetailers.forEach(function(r,i){
var o=document.createElement('option');o.value=r.name;o.textContent=r.name;rs.appendChild(o)});
loadHourly()});
if(!hourlyDates){
fetch('/api/daily-data').then(function(r){return r.json()}).then(function(data){
hourlyDates=Object.keys(data).sort().reverse();
if(hourlyDates.length)ds.value=hourlyDates[0];
loadHourly()});
}}
function loadHourly(){
var r=document.getElementById('retailerSel2').value,d=document.getElementById('dateSel2').value;
if(!r||!d)return;
document.getElementById('hourlyStatus').textContent=r+' — '+d;
document.getElementById('hourlyFrame').src='/hourly-detail?retailer='+encodeURIComponent(r)+'&date='+encodeURIComponent(d);
}
function loadDaily(){
var f=document.getElementById('fDate').value,t=document.getElementById('tDate').value,d=document.getElementById('daysNum').value,b=document.getElementById('bpSel').value;
var p=[];
if(b){p.push('bp='+b)}
else{if(f)p.push('from='+f);if(t)p.push('to='+t);if(!f&&!t)p.push('days='+d);}
document.getElementById('dailyFrame').src='/daily-report?'+p.join('&');
}
function bpKey(dt,bd){
var mo=parseInt(dt.substring(5,7)),da=parseInt(dt.substring(8,10)),yr=parseInt(dt.substring(0,4));
if(da>=bd)return yr+'-'+(mo<10?'0':'')+mo;
mo--;if(mo==0){mo=12;yr--;}
return yr+'-'+(mo<10?'0':'')+mo;
}
function loadBillingPeriods(){
fetch('/api/daily-data').then(function(r){return r.json()}).then(function(data){
var sel=document.getElementById('bpSel'),found={},bda=4;
Object.keys(data).sort().reverse().forEach(function(d){
var k=bpKey(d,bda);found[k]=true});
Object.keys(found).sort().reverse().forEach(function(k){
var o=document.createElement('option');o.value=k;o.textContent=k;
if(sel.options.length==1||k>sel.options[1].value)sel.insertBefore(o,sel.options[1]);
else sel.appendChild(o)});
})}
loadBillingPeriods();
</script>'''

_CHARTS_HTML = '''<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<div style="padding:10px;overflow-y:auto;height:100vh">
<div class=chart-controls>
<label>Range <select id=rangeSel><option value=7>7d</option><option value=14>14d</option><option value=31 selected>31d</option><option value=90>90d</option><option value=365>All</option></select></label>
<label>Type <select id=typeSel><option value=net selected>Net $</option><option value=import>Import $</option><option value=export>Export $</option></select></label>
</div>
<div class=chart-legend id=chartLegend></div>
<canvas id=mainChart></canvas>
<script>
var chartData=null,chart=null,coloring=['#4CAF50','#2196F3','#FF9800','#E91E63','#9C27B0','#00BCD4','#FF5722','#607D8B'];
fetch('/api/chart-data').then(function(r){return r.json()}).then(function(d){chartData=d;renderChart()});
function renderChart(){
if(!chartData||!chartData.length)return;
var range=parseInt(document.getElementById('rangeSel').value);var type=document.getElementById('typeSel').value;
var data=range>=365?chartData:chartData.slice(-range);
var labels=data.map(function(d){return d.date.slice(5)});
var retailers=Object.keys(data[data.length-1].retailers);
var datasets=[];
retailers.forEach(function(r,i){
var vals=data.map(function(d){var v=d.retailers[r]||0;return type==='import'?Math.abs(v):type==='export'?Math.abs(v):v});
datasets.push({label:r,data:vals,borderColor:coloring[i%coloring.length],backgroundColor:coloring[i%coloring.length]+'33',fill:false,tension:0.2,pointRadius:2});
});
var ctx=document.getElementById('mainChart').getContext('2d');
if(chart)chart.destroy();
chart=new Chart(ctx,{type:'line',data:{labels:labels,datasets:datasets},options:{
responsive:true,maintainAspectRatio:false,
plugins:{legend:{display:false},tooltip:{mode:'index',intersect:false,backgroundColor:'#1a1a1a',titleColor:'#ccc',bodyColor:'#ccc',borderColor:'#333',borderWidth:1}},
scales:{x:{ticks:{color:'#888',maxTicksLimit:15,font:{size:10}},grid:{color:'#222'}},
y:{ticks:{color:'#888',font:{size:10},callback:function(v){return'$'+v.toFixed(2)}},grid:{color:'#222'}}}
}});
var lg=document.getElementById('chartLegend');lg.innerHTML='';
retailers.forEach(function(r,i){
var sp=document.createElement('span');sp.textContent=r;sp.style.color=coloring[i%coloring.length];
sp.onclick=function(){var idx=chart.data.datasets.findIndex(function(ds){return ds.label===r});
var ds=chart.data.datasets[idx];ds.hidden=!ds.hidden;chart.update();sp.classList.toggle('active')};
sp.classList.add('active');lg.appendChild(sp)});
}
document.getElementById('rangeSel').onchange=renderChart;
document.getElementById('typeSel').onchange=renderChart;
</script>
</div>'''

def dashboard_html(tab=None):
    if tab == 'reports':
        return _PAGE_PREFIX + _REPORTS_HTML + '</body></html>'
    elif tab == 'charts':
        return _PAGE_PREFIX + _CHARTS_HTML + '</body></html>'
    elif tab == 'config':
        return '<html><head><meta charset="utf-8"><meta http-equiv="refresh" content="0;url=/api/retailer-config"></head><body></body></html>'
    return _PAGE_PREFIX + _REPORTS_HTML + '</body></html>'

# ─── Config Editor ────────────────────────────────────────────────────────────

def _read_config_raw(path):
    with open(path, newline='') as f:
        return f.read()

def _write_config_raw(path, content):
    with open(path, 'w', newline='') as f:
        f.write(content)

_CFG_FIELDS = [
    ('name', 'Name', False),
    ('model', 'Model', False),
    ('dsc', 'DSC $/day', False),
    ('sub', 'Sub $/day', False),
    ('off_pk', 'OffPk $/kWh', False),
    ('sh_pk', 'Shoulder $/kWh', False),
    ('pk_pk', 'Peak $/kWh', False),
    ('off_fit', 'OffPk FIT $/kWh', False),
    ('sh_fit', 'Sh FIT $/kWh', False),
    ('pk_fit', 'Peak FIT $/kWh', False),
    ('sp_fit', 'SuperPk FIT', False),
    ('sp_fit2', 'SP FIT Excess', False),
    ('sp_limit', 'SP Limit kWh', False),
    ('off_s', 'OffPk Start', False),
    ('off_e', 'OffPk End', False),
    ('pk_s', 'Peak Start', False),
    ('pk_e', 'Peak End', False),
    ('sp_s', 'SP Start', False),
    ('sp_e', 'SP End', False),
    ('off_fit_s', 'OffPk FIT Start', False),
    ('off_fit_e', 'OffPk FIT End', False),
    ('sh_fit_s', 'Sh FIT Start', False),
    ('sh_fit_e', 'Sh FIT End', False),
    ('pk_fit_s', 'Pk FIT Start', False),
    ('pk_fit_e', 'Pk FIT End', False),
    ('sp_fit_s', 'SP FIT Start', False),
    ('sp_fit_e', 'SP FIT End', False),
    ('fixed_export', 'Fixed Exp kWh', False),
    ('ev_s', 'EV Start', False),
    ('ev_e', 'EV End', False),
    ('ev_pk', 'EV $/kWh', False),
    ('off_limit', 'Off Limit kWh', False),
    ('billing_day', 'Billing Day', False),
    ('pea_base', 'PEA Base', False),
    ('pea_override', 'PEA Override', False),
    ('glo_rebate', 'Glo Rebate', True),
    ('energymadeeasy_planid', 'Plan ID', False),
]
_HIDDEN = {'sensor_id'}

def config_editor_html(rows):
    import html as hmod
    headers = list(rows[0].keys()) if rows else []
    visible_cols = [c for c in headers if c not in _HIDDEN] if headers else [f[0] for f in _CFG_FIELDS]
    visible_labels = {f[0]: f[1] for f in _CFG_FIELDS}
    input_type = {f[0]: 'checkbox' if f[2] else 'text' for f in _CFG_FIELDS}

    trs = []
    for ri, row in enumerate(rows):
        cells = []
        for col in visible_cols:
            val = row.get(col, '')
            if col == 'model':
                sel = ('<select name="model_%d">' % (ri+1) +
                    '<option value="fixed_tou"%s>fixed_tou</option>' % (' selected' if val == 'fixed_tou' else '') +
                    '<option value="hybrid"%s>hybrid</option>' % (' selected' if val == 'hybrid' else '') +
                    '<option value="variable"%s>variable</option>' % (' selected' if val == 'variable' else '') +
                    '</select>')
                cells.append('<td style="padding:2px 4px">' + sel + '</td>')
            elif col == 'name':
                cells.append('<td style="padding:2px 4px"><input name="name_%d" value="%s" style="width:140px"></td>' % (ri+1, hmod.escape(val)))
            elif col == 'glo_rebate':
                chk = ' checked' if val == '1' or val == '1.0' else ''
                cells.append('<td style="padding:2px 4px;text-align:center"><input type="checkbox" name="glo_rebate_%d"%s></td>' % (ri+1, chk))
            elif col == 'billing_day':
                cells.append('<td style="padding:2px 4px"><input name="billing_day_%d" value="%s" style="width:40px"></td>' % (ri+1, hmod.escape(str(val))))
            elif col in ('fixed_export', 'off_limit', 'sp_limit'):
                cells.append('<td style="padding:2px 4px"><input name="%s_%d" value="%s" style="width:50px"></td>' % (col, ri+1, hmod.escape(str(val))))
            else:
                cells.append('<td style="padding:2px 4px"><input name="%s_%d" value="%s" style="width:65px"></td>' % (col, ri+1, hmod.escape(str(val))))
        cells.append('<td style="padding:2px 4px;text-align:center"><input type="checkbox" name="del_%d"></td>' % (ri+1))
        trs.append('<tr>' + ''.join(cells) + '</tr>')

    blank_cells = []
    for col in visible_cols:
        if col == 'model':
            blank_cells.append('<td style="padding:2px 4px"><select name="model_' + str(len(rows)+1) + '">'
                '<option value="fixed_tou">fixed_tou</option>'
                '<option value="hybrid">hybrid</option>'
                '<option value="variable">variable</option></select></td>')
        elif col == 'name':
            blank_cells.append('<td style="padding:2px 4px"><input name="name_%d" style="width:140px"></td>' % (len(rows)+1))
        elif col == 'glo_rebate':
            blank_cells.append('<td style="padding:2px 4px;text-align:center"><input type="checkbox" name="glo_rebate_%d"></td>' % (len(rows)+1))
        elif col == 'billing_day':
            blank_cells.append('<td style="padding:2px 4px"><input name="billing_day_%d" value="4" style="width:40px"></td>' % (len(rows)+1))
        else:
            blank_cells.append('<td style="padding:2px 4px"><input name="%s_%d" style="width:65px"></td>' % (col, len(rows)+1))
    blank_cells.append('<td style="padding:2px 4px;text-align:center"><input type="checkbox" name="del_%d"></td>' % (len(rows)+1))
    trs.append('<tr>' + ''.join(blank_cells) + '</tr>')

    ths = ''.join('<th style="padding:4px 6px;text-align:left;font-size:10px;position:sticky;top:0;background:#1a1a1a;z-index:1">' + hmod.escape(visible_labels.get(col, col)) + '</th>' for col in visible_cols)
    ths += '<th style="padding:4px 6px;text-align:center;font-size:10px;position:sticky;top:0;background:#1a1a1a;z-index:1">Del</th>'

    hdr_inp = '<input type="hidden" name="headers" value="' + hmod.escape(','.join(visible_cols)) + '">'
    rc_inp = '<input type="hidden" name="rowCount" value="' + str(len(rows)+1) + '">'

    return '''<!DOCTYPE html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Retailer Config Editor</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#0d0d0d;color:#ccc;font-family:monospace;padding:10px}
h2{color:white;margin-bottom:10px;font-size:14px}
table{border-collapse:collapse;font-size:11px;width:100%}
th{color:#888;font-weight:bold;border-bottom:1px solid #333}
td{border-bottom:1px solid #1a1a1a}
input[type=text],select{background:#1a1a1a;color:#ccc;border:1px solid #333;padding:2px 4px;font-size:11px;font-family:monospace;width:65px}
input[type=text]:focus{outline:1px solid #4CAF50;border-color:#4CAF50}
select{width:85px}
.btn{padding:6px 16px;margin:8px 4px;border:none;cursor:pointer;font-size:12px;font-family:monospace}
.btn-save{background:#4CAF50;color:white}
.btn-reset{background:#555;color:#ccc}
#msg{color:#4CAF50;margin:6px 0;font-size:11px}
input[name^=name_]{width:140px!important}
input[name^=billing_day_]{width:40px!important}
input[name^=fixed_export_],input[name^=off_limit_],input[name^=sp_limit_]{width:50px!important}
</style></head><body>
<h2>Retailer Config Editor</h2>
<div id="msg"></div>
<form method="post" action="/api/retailer-config/save" onsubmit="document.getElementById('msg').textContent='Saving...'">
''' + hdr_inp + rc_inp + '''
<div style="overflow-x:auto;max-height:70vh;overflow-y:auto">
<table><thead><tr>''' + ths + '''</tr></thead>
<tbody>''' + '\n'.join(trs) + '''</tbody></table></div>
<div style="margin-top:8px">
<button class="btn btn-save" type="submit">Save</button>
<button class="btn btn-reset" type="button" onclick="window.location.href='/api/retailer-config?t='+Date.now()">Reset</button>
</div></form></body></html>'''

def _parse_config_form(body, visible_cols):
    row_count = int(body.get('rowCount', [0])[0] if isinstance(body.get('rowCount'), list) else body.get('rowCount', 0))
    delete_rows = set()
    for k, v in body.items():
        if isinstance(v, list): v = v[0]
        if k.startswith('del_') and v == 'on':
            delete_rows.add(k.replace('del_', ''))

    full_headers = [f[0] for f in _CFG_FIELDS]
    csv_header = ','.join(full_headers)
    out_lines = [csv_header]

    for i in range(1, row_count + 1):
        si = str(i)
        if si in delete_rows:
            continue
        row = []
        for col in full_headers:
            key = '%s_%s' % (col, si)
            val = body.get(key, [''])[0] if isinstance(body.get(key), list) else body.get(key, '')
            if isinstance(val, list): val = val[0]
            if val is None: val = ''
            val = val.strip()
            if col == 'glo_rebate':
                val = '1' if val in ('on', '1', 'true') else '0'
            row.append(val)
        if not row[0]:
            continue
        out_lines.append(','.join(row))
    return '\n'.join(out_lines) + '\n'

# ─── HTTP Server ─────────────────────────────────────────────────────────────

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path); path = parsed.path; params = parse_qs(parsed.query)
        try:
            ensure_computed()
            ds = _cache['daily_summary']; rt = _cache['retailers']; fm = _cache['five_min_detail']; cd = _cache['chart_data']
        except Exception as e:
            self._json({'error': str(e)}, 500); return
        
        if path == '/':
            tab = params.get('tab', [None])[0]
            self._html(dashboard_html(tab=tab))
        elif path == '/api/status':
            self._json({'status': 'ok', 'csv_rows': len(_cache.get('rows', [])), 'retailers': len(rt), 'dates': len(ds),
                         'pea_brackets': {k: {'twap': round(v['aemo_sum']/v['aemo_count'],4) if v['aemo_count'] else 0,
                                              'lwap': round(v['import_aemo_sum']/v['import_sum'],4) if v['import_sum'] else 0,
                                              'imports': round(v['import_sum'],1)}
                                           for k,v in _cache.get('pea_periods', {}).items()}})
        elif path == '/daily-report':
            date_str = params.get('date', [None])[0]
            from_str = params.get('from', [None])[0]
            to_str = params.get('to', [None])[0]
            days_str = params.get('days', [None])[0]
            bp_str = params.get('bp', [None])[0]
            if bp_str:
                bd = int(rt[0].get('billing_day', 4)) if rt else 4
                subset = {d: v for d, v in ds.items() if _billing_period_key(d, bd) == bp_str}
                self._html(daily_report_html(subset, rt, days=None))
            elif date_str and date_str in ds:
                subset = {date_str: ds[date_str]}
                self._html(daily_report_html(subset, rt))
            elif from_str or to_str:
                subset = {d: v for d, v in ds.items()
                          if (not from_str or d >= from_str) and (not to_str or d <= to_str)}
                days = int(days_str) if days_str else None
                self._html(daily_report_html(subset, rt, days=days))
            else:
                days = int(days_str) if days_str else 90
                self._html(daily_report_html(ds, rt, days=days))
        elif path == '/monthly-report':
            self._html(monthly_report_html(ds, rt))
        elif path == '/seasonal-report':
            self._html(seasonal_report_html(ds, rt))
        elif path == '/5min-detail':
            rn = params.get('retailer', [None])[0]; dt = params.get('date', [None])[0]
            if not rn or not dt: self._html('<div style="color:#888;padding:20px">?retailer=X&date=YYYY-MM-DD</div>'); return
            self._html(fivemin_html(fm, ds, rn, dt))
        elif path == '/hourly-detail':
            rn = params.get('retailer', [None])[0]; dt = params.get('date', [None])[0]
            if not rn or not dt: self._html('<div style="color:#888;padding:20px">?retailer=X&date=YYYY-MM-DD</div>'); return
            self._html(hourly_html(fm, ds, rn, dt))
        elif path == '/api/daily-data':
            self._json(ds)
        elif path == '/api/retailers':
            self._json(rt)
        elif path == '/api/chart-data':
            self._json(cd)
        elif path == '/api/retailer-config':
            try:
                raw = _read_config_raw(CONFIG_PATH)
                rows = load_retailer_config(CONFIG_PATH)
                self._html(config_editor_html(rows))
            except Exception as e:
                self._html('<div style="color:red;padding:20px">Error reading config: ' + str(e) + '</div>')
        else:
            self._json({'error': 'Not found', 'paths': ['/','/api/status','/daily-report','/monthly-report','/seasonal-report','/5min-detail','/hourly-detail','/api/daily-data','/api/retailers','/api/chart-data','/api/retailer-config','/api/retailer-config/save']}, 404)
    
    def do_POST(self):
        parsed = urlparse(self.path); path = parsed.path
        if path == '/api/retailer-config/save':
            content_len = int(self.headers.get('Content-Length', 0))
            body_raw = self.rfile.read(content_len) if content_len > 0 else b''
            ctype = self.headers.get('Content-Type', '')
            try:
                if 'application/json' in ctype:
                    body = json.loads(body_raw)
                else:
                    from urllib.parse import parse_qs as pqs
                    body = pqs(body_raw.decode('utf-8', errors='replace'))
                visible_cols = body.get('headers', '').split(',') if isinstance(body.get('headers'), str) else []
                csv_out = _parse_config_form(body, visible_cols)
                _write_config_raw(CONFIG_PATH, csv_out)
                _cache['data_hash'] = ''
                log.info('Config saved, cache invalidated')
                self._html('<!DOCTYPE html><html><head><meta http-equiv="refresh" content="0;url=/api/retailer-config?t=%d"></head><body>Saved. Redirecting...</body></html>' % int(time.time()*1000))
            except Exception as e:
                log.error(f'Config save failed: {e}')
                self._json({'error': str(e)}, 500)
        else:
            self._json({'error': 'POST not supported on ' + path}, 404)
    
    def _html(self, html, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'text/html;charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(html.encode())
    
    def _json(self, data, status=200):
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode())
    
    def log_message(self, fmt, *args):
        log.info(f'{args[0]} {args[1]} {args[2]}')

# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    global CSV_PATH, CONFIG_PATH, PORT, DAEMON
    i = 1
    while i < len(sys.argv):
        a = sys.argv[i]
        if a == '--port' and i+1 < len(sys.argv): PORT = int(sys.argv[i+1]); i+=2
        elif a == '--csv' and i+1 < len(sys.argv): CSV_PATH = sys.argv[i+1]; i+=2
        elif a == '--config' and i+1 < len(sys.argv): CONFIG_PATH = sys.argv[i+1]; i+=2
        elif a == '--daemon': DAEMON = True; i+=1
        else: print(f'Unknown: {a}'); return
    
    if DAEMON:
        pid = os.fork()
        if pid > 0:
            with open('/tmp/energy_server.pid', 'w') as pf: pf.write(str(pid))
            print(f'Daemon PID {pid}')
            sys.exit(0)
        sys.stdout = open('/tmp/energy_server.log', 'w')
        sys.stderr = sys.stdout
    
    try:
        ensure_computed()
    except Exception as e:
        log.warning(f'Initial compute failed: {e}')
    
    server = HTTPServer(('', PORT), Handler)
    log.info(f'Serving on http://0.0.0.0:{PORT}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info('Shutting down')
        server.server_close()

if __name__ == '__main__':
    main()
