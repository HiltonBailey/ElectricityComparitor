#!/usr/bin/env python3
"""
Regression test for energy_server.py.

Why this exists
---------------
The `UnboundLocalError: 'r'` and `UnboundLocalError: 'bat'` bugs only
manifested at RUNTIME, when calculate_costs actually executed with a realistic
dataset AND (for `bat`) with OPTIMISE_ALL=False. A plain syntax/lint check
never caught them. This script reproduces those exact conditions so any future
change that re-introduces a scoping/execution bug is caught immediately.

What it does
------------
1. Builds a SYNTHETIC but representative CSV:
     - 3 full days + 1 PARTIAL ("current") day  -> exercises the forecast path
     - per-interval solar/load/import/export + cumulative battery charge/
       discharge counters  -> exercises SOC reconstruction + dispatch
2. LEVEL 1 (fast, in-process): imports energy_server, runs calculate_costs()
   with BOTH OPTIMISE_ALL=True and False across ALL retailers from the real
   retailer_config.csv, then renders EVERY report (daily / monthly / seasonal /
   5-min / half-hour) for representative retailers/dates. Any exception here is
   a regression.
3. LEVEL 2 (thorough, real server): launches the ACTUAL server (subprocess)
   against the synthetic CSV + repo config on a test port, then hits EVERY
   report/API endpoint for BOTH optimise states, asserting HTTP 200 + non-empty
   body. Catches handler/render regressions across all reports.

Usage
-----
    python3 regression_test.py                 # synthetic CSV + repo config
    python3 regression_test.py --csv /path/5min.csv   # use a real CSV (full scale)

Exit code is non-zero if ANY check fails, so it can be wired into CI / a
post-edit hook.
"""

import os
import sys
import csv
import json
import time
import signal
import urllib.request
import urllib.error
import subprocess
import tempfile
import shutil
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import energy_server as es

# ----------------------------------------------------------------------------
# Synthetic dataset
# ----------------------------------------------------------------------------

def build_synthetic_csv(path):
    """Write a synthetic CSV that exercises every code path.

    Columns (0-indexed) must match load_csv_rows():
      0 datetime, 1 offpeak, 2 shoulder, 3 peak, 4 export(cum),
      5 bat_charge, 6 Bat_Charge_Energy(cum), 7 Bat_Discharge_Energy(cum),
      8 house_load(cum), 9 gen_price, 10 fit_price, 11 aemo_price,
      12 pe_datetime, 13 solar_gen(cum), 14 Import_kWh(cum)
    """
    header = ['datetime', 'offpeak', 'shoulder', 'peak', 'export', 'bat_charge',
              'Bat_Charge_Energy', 'Bat_Discharge_Energy', 'house_load',
              'gen_price', 'fit_price', 'aemo_price', 'pe_datetime',
              'solar_gen', 'Import_kWh']
    days = ['2026-08-22', '2026-08-23', '2026-08-24']          # full days
    partial_day = '2026-08-25'                                # current day (partial)
    counts = {d: 288 for d in days}
    counts[partial_day] = 150                                 # < 288 -> forecast path

    cum_imp = cum_exp = cum_load = cum_solar = 0.0
    cum_bchg = cum_bdis = 0.0

    with open(path, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(header)
        for d, n in counts.items():
            for j in range(n):
                h = j / 12.0
                dt = datetime.strptime(d, '%Y-%m-%d') + timedelta(minutes=5 * j)
                pe = dt.strftime('%Y-%m-%d %H:%M:%S')
                # deterministic, REALISTIC per-interval shapes (kWh / 5-min):
                #   load ~0.08 (≈23 kWh/day), solar peak ~0.18 (≈25 kWh/day),
                #   import ~0.06, export ~0.06.  Battery charges midday,
                #   discharges evening.  Keeps sanity bounds meaningful.
                solar = 0.18 * max(0.0, 1 - abs(h - 12) / 7)          # bell at noon
                load = 0.06 + 0.04 * max(0.0, 1 - abs(h - 19) / 6)   # evening peak
                imp = 0.05 + 0.05 * max(0.0, 1 - abs(h - 3) / 4)     # import overnight
                exp = 0.04 + 0.08 * max(0.0, 1 - abs(h - 13) / 4)    # export midday
                bchg = 0.30 if 10 <= h < 15 else 0.0                 # charge midday
                bdis = 0.35 if 17 <= h < 21 else 0.0                 # discharge evening
                aemo = round(0.05 + 0.02 * (1 + 0.5 * (h % 5)), 4)

                # accumulate into cumulative counters
                cum_imp += imp; cum_exp += exp
                cum_load += load; cum_solar += solar
                cum_bchg += bchg; cum_bdis += bdis

                w.writerow([
                    pe, '0', '0', '0',
                    f'{cum_exp:.6f}', '0',
                    f'{cum_bchg:.6f}', f'{cum_bdis:.6f}',
                    f'{cum_load:.6f}',
                    '0.20', '0.10', f'{aemo:.4f}',
                    pe,
                    f'{cum_solar:.6f}',
                    f'{cum_imp:.6f}',
                ])


# ----------------------------------------------------------------------------
# Measured-floor invariant helper
# ----------------------------------------------------------------------------

def measured_net(es, day_ivs, r):
    """Net cost ($-style) of a day's MEASURED i_kwh/e_kwh under retailer r.

    Mirrors energy_server's _fixed_tou_interval / _finalize so it matches the
    server's cost basis. Used to assert the optimised total is never worse
    than the measured total (the 'measured floor')."""
    model = r.get('model')
    d = {'offKwh': 0.0, 'shKwh': 0.0, 'pkKwh': 0.0, 'evKwh': 0.0, 'spExportKwh': 0.0,
         'pkExportKwh': 0.0, 'shExportKwh': 0.0, 'offExportKwh': 0.0, 'spExportUsed': 0.0,
         'freeUsage': 0.0, 'hr18': 0.0, 'hr19': 0.0, 'hr20': 0.0, 'import': 0.0, 'export': 0.0}
    for iv in day_ivs:
        h = iv['h']; ti = iv['i_kwh']; ek = iv['e_kwh']
        # mirror _accumulate's hr18/19/20 tracking (used by the glo-rebate)
        if 18 <= h < 19:
            d['hr18'] += ti
        elif 19 <= h < 20:
            d['hr19'] += ti
        elif 20 <= h < 21:
            d['hr20'] += ti
        if model in ('fixed_tou', 'fixed_tou_real'):
            es._fixed_tou_interval(d, r, h, ti, ek)
        elif model == 'hybrid':
            rate = float(r.get('sh_pk', 0.2))
            d['import'] += ti * rate
            if es.in_window(h, float(r.get('sp_fit_s', 17.5)), float(r.get('sp_fit_e', 21.5))) and float(r.get('sp_limit', 0)) > 0:
                rem = float(r.get('sp_limit', 0)) - d['spExportUsed']
                if rem > 0:
                    sp = min(ek, rem); fb = ek - sp; d['spExportUsed'] += sp
                    d['export'] += sp * float(r.get('sp_fit', 0)); d['spExportKwh'] += sp
                    if fb > 0:
                        d['export'] += fb * float(r.get('sp_fit2', 0)); d['shExportKwh'] += fb
                else:
                    d['export'] += ek * float(r.get('sp_fit2', 0)); d['shExportKwh'] += ek
            else:
                d['export'] += ek * float(r.get('off_fit', 0))
        else:  # variable / variable_optimised
            d['import'] += ti * (iv['aemo'] + float(r.get('sh_pk', 0)))
            d['export'] += ek * iv['aemo'] * float(r.get('off_fit', 0))
    if model in ('fixed_tou', 'fixed_tou_real'):
        fs = float(r.get('free_s', 0)); fe = float(r.get('free_e', 0))
        if not (fs or fe):
            off_bal = max(0.0, d['offKwh'] - d.get('freeUsage', 0))
            off_rate = float(r.get('off_pk', 0))
            if off_rate == 0 and float(r.get('off_limit', 0)) > 0:
                off_rate = float(r.get('sh_pk', 0))
            imp = (off_bal * off_rate + d['shKwh'] * float(r.get('sh_pk', 0))
                   + d['pkKwh'] * float(r.get('pk_pk', 0)) + d['evKwh'] * float(r.get('ev_pk', 0)))
        else:
            imp = d['import']
        exp = (d['spExportKwh'] * float(r.get('sp_fit', 0)) + d['pkExportKwh'] * float(r.get('pk_fit', 0))
               + d['shExportKwh'] * float(r.get('sh_fit', 0)) + d['offExportKwh'] * float(r.get('off_fit', 0)))
    else:
        imp = d['import']; exp = d['export']
    reb = 0.0
    if float(r.get('glo_rebate', '0')) > 0 and d['hr18'] < 0.1 and d['hr19'] < 0.1 and d['hr20'] < 0.1:
        reb = 1.0
    return imp - exp + float(r.get('dsc', 0)) + float(r.get('sub', 0)) - reb


# ----------------------------------------------------------------------------
# Check harness
# ----------------------------------------------------------------------------

results = []

def check(name, fn):
    try:
        fn()
        results.append((name, True, ''))
        print(f'  PASS  {name}')
    except Exception as e:
        results.append((name, False, repr(e)))
        print(f'  FAIL  {name}\n        {type(e).__name__}: {e}')


# ----------------------------------------------------------------------------
# Level 1: in-process calculate_costs + report rendering (both OPTIMISE_ALL)
# ----------------------------------------------------------------------------

def level1(csv_path, config_path):
    print('\n[Level 1] In-process calculate_costs + report rendering')
    rows = es.load_csv_rows(csv_path)
    assert rows, 'load_csv_rows returned no rows'
    intervals = es.extract_intervals(rows)
    assert intervals, 'extract_intervals returned no intervals'
    retailers = es.load_retailer_config(config_path)
    assert retailers, 'no retailers loaded'

    for opt in (True, False):
        def run():
            es.OPTIMISE_ALL = opt
            daily_data, daily_summary, chart_data, five_min_detail = \
                es.calculate_costs(intervals, retailers)
            assert daily_summary, 'empty daily_summary'
            # at least one day must be the partial/projected day
            proj = [d for d, v in daily_summary.items() if not v.get('complete')]
            assert proj, 'no incomplete (projected) day produced'

            # render every report
            daily_html = es.daily_report_html(daily_summary, retailers)
            assert 'Retailer' in daily_html or len(daily_html) > 200, 'daily report empty'
            es.monthly_report_html(daily_summary, retailers)
            es.seasonal_report_html(daily_summary, retailers)

            # 5-min + half-hour detail for a couple of retailers incl. an
            # optimised/realistic-dispatch one (AGL) so those paths run
            sample = [retailers[0]['name'], 'AGL Battery Rewards']
            for rn in sample:
                if rn not in five_min_detail:
                    continue
                d = proj[0]
                es.fivemin_html(five_min_detail, daily_summary, rn, d)
                es.hourly_html(five_min_detail, daily_summary, rn, d)

            # Sanity: projected day's physical energy totals must be plausible.
            # This catches the "summed across all retailers" bug that produced
            # an 8x-inflated import/export (e.g. 218.8 / 115.6 kWh in a day).
            for d in proj:
                v = daily_summary[d]
                ti = v['totalImport']; te = v['totalExport']
                ts = v.get('totalSolar', 0); tl = v.get('totalLoad', 0)
                assert 0 < ti <= 80, f"{d}: implausible import {ti:.1f} kWh"
                assert 0 <= te <= 80, f"{d}: implausible export {te:.1f} kWh"
                assert 0 < ts <= 60, f"{d}: implausible solar {ts:.1f} kWh"
                assert 0 < tl <= 60, f"{d}: implausible load {tl:.1f} kWh"

            # Measured-floor invariant: for every COMPLETE day, the optimised
            # (reported) net must be <= the measured (actual) net. This is the
            # exact guarantee the production fix enforces — if it ever fails,
            # the optimiser produced a worse-than-measured result.
            _by_date = {}
            for iv in intervals:
                _by_date.setdefault(iv['date'], []).append(iv)
            for date, ds in daily_summary.items():
                if not ds.get('complete'):
                    continue
                day = _by_date.get(date, [])
                if len(day) < 288:
                    continue
                for r in retailers:
                    mn = measured_net(es, day, r)
                    rep = ds['retailers'].get(r['name'], {}).get('net')
                    assert rep is not None, f"{date} {r['name']}: no net"
                    assert rep <= mn + 0.02, \
                        f"{date} {r['name']}: optimised {rep:.2f} > measured {mn:.2f}"
        check(f'calculate_costs + reports (OPTIMISE_ALL={opt})', run)

    # exercise HA SOC fetch guard (must not raise even with no network)
    def soc():
        es._fetch_battery_soc()
    check('fetch_battery_soc does not raise', soc)


# ----------------------------------------------------------------------------
# Level 2: real server, every endpoint, both optimise states
# ----------------------------------------------------------------------------

def _http_get(port, path):
    url = f'http://127.0.0.1:{port}{path}'
    with urllib.request.urlopen(url, timeout=10) as r:
        return r.status, r.read().decode('utf-8', 'replace')

def _http_post_json(port, path, payload):
    url = f'http://127.0.0.1:{port}{path}'
    data = json.dumps(payload).encode()
    req = urllib.request.Request(url, data=data, headers={'Content-Type': 'application/json'}, method='POST')
    with urllib.request.urlopen(req, timeout=10) as r:
        return r.status, r.read().decode('utf-8', 'replace')


def level2(csv_path, config_path):
    print('\n[Level 2] Real server — all report/API endpoints')
    port = 8099
    proc = None
    tmp = None
    try:
        # copy config into a temp dir so settings.json is isolated
        tmp = tempfile.mkdtemp(prefix='regtest_')
        cfg = os.path.join(tmp, 'retailer_config.csv')
        shutil.copy(config_path, cfg)

        proc = subprocess.Popen(
            [sys.executable, os.path.join(HERE, 'energy_server.py'),
             '--csv', csv_path, '--config', cfg, '--port', str(port)],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

        # wait for server to come up
        up = False
        for _ in range(60):
            try:
                st, _ = _http_get(port, '/api/status')
                if st == 200:
                    up = True
                    break
            except Exception:
                time.sleep(0.5)
        if not up:
            out = proc.stdout.read().decode('utf-8', 'replace')[-2000:] if proc.stdout else ''
            raise RuntimeError('server did not start. output:\n' + out)

        def endpoints_for_state(opt_on):
            def run():
                # toggle optimise state
                _http_post_json(port, '/api/optimise', {'on': opt_on})
                # discover a retailer + projected date
                st, body = _http_get(port, '/api/daily-data')
                ds = json.loads(body)
                proj = [d for d, v in ds.items() if not v.get('complete')]
                assert proj, 'no projected day in /api/daily-data'
                d = proj[0]
                st, body = _http_get(port, '/api/retailers')
                rts = json.loads(body)
                assert rts, 'no retailers from /api/retailers'
                rn = rts[0]['name']
                # pick a retailer that exists in 5-min detail
                rn5 = None
                for r in rts:
                    if r['name'] in ds.get(d, {}).get('retailers', {}):
                        rn5 = r['name']; break
                rn5 = rn5 or rn

                # every report + API endpoint
                checks = [
                    ('/', ''),
                    ('/api/status', ''),
                    ('/daily-report', '?days=90'),
                    ('/monthly-report', ''),
                    ('/seasonal-report', ''),
                    ('/5min-detail', f'?retailer={urllib.parse.quote(rn5)}&date={d}'),
                    ('/hourly-detail', f'?retailer={urllib.parse.quote(rn5)}&date={d}'),
                    ('/api/daily-data', ''),
                    ('/api/retailers', ''),
                    ('/api/chart-data', ''),
                    ('/api/retailer-config', ''),
                ]
                for ep, q in checks:
                    status, b = _http_get(port, ep + q)
                    assert status == 200, f'{ep} -> HTTP {status}'
                    if ep.startswith('/api/') and ep != '/api/retailer-config':
                        # API endpoints must return parseable, non-empty JSON
                        parsed = json.loads(b)
                        assert parsed or parsed == 0 or parsed == [], \
                            f'{ep} -> empty/invalid JSON'
                    else:
                        # HTML reports must be substantial
                        assert len(b) > 200, f'{ep} -> body too short ({len(b)})'
            return run

        check('all endpoints (OPTIMISE_ALL=False)', endpoints_for_state(False))
        check('all endpoints (OPTIMISE_ALL=True)', endpoints_for_state(True))
    finally:
        if proc:
            try:
                proc.send_signal(signal.SIGTERM)
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        if tmp and os.path.isdir(tmp):
            shutil.rmtree(tmp, ignore_errors=True)


# ----------------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------------

def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument('--csv', help='Use a real CSV instead of synthetic')
    args = ap.parse_args()

    config_path = os.path.join(HERE, 'retailer_config.csv')
    if not os.path.exists(config_path):
        print('ERROR: retailer_config.csv not found next to this script.', file=sys.stderr)
        sys.exit(2)

    if args.csv:
        csv_path = args.csv
        print(f'Using real CSV: {csv_path}')
    else:
        tmp = tempfile.mkdtemp(prefix='regtest_csv_')
        csv_path = os.path.join(tmp, 'synthetic.csv')
        build_synthetic_csv(csv_path)
        print(f'Built synthetic CSV: {csv_path}')

    try:
        level1(csv_path, config_path)
        level2(csv_path, config_path)
    finally:
        if not args.csv and 'tmp' in dir():
            shutil.rmtree(tmp, ignore_errors=True)

    failed = [r for r in results if not r[1]]
    print('\n' + '=' * 60)
    print(f'Regression summary: {len(results) - len(failed)}/{len(results)} passed')
    if failed:
        print('FAILURES:')
        for name, ok, err in failed:
            print(f'  - {name}: {err}')
        sys.exit(1)
    print('All regression checks passed.')
    sys.exit(0)


if __name__ == '__main__':
    main()
