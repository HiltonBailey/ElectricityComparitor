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
    /api/settings          JSON optimise-all flag state
    POST /api/optimise     Toggle optimised battery dispatch for all retailers
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
SETTINGS_PATH = 'settings.json'
OPTIMISE_ALL = False
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

def load_settings():
    global OPTIMISE_ALL
    try:
        with open(SETTINGS_PATH) as f:
            OPTIMISE_ALL = bool(json.load(f).get('optimise_all', False))
    except Exception:
        OPTIMISE_ALL = False

def save_settings(patch):
    s = {}
    try:
        with open(SETTINGS_PATH) as f:
            s = json.load(f)
    except Exception:
        pass
    s.update(patch)
    with open(SETTINGS_PATH, 'w') as f:
        json.dump(s, f)

def ensure_computed():
    """Recompute if CSV/config changed, or the optimise-all flag changed."""
    load_settings()
    csv_hash = _file_hash(CSV_PATH)
    cfg_hash = _file_hash(CONFIG_PATH)
    combined = csv_hash + '|' + cfg_hash + '|' + ('1' if OPTIMISE_ALL else '0')
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

def _imp_rate_at(r, iv, pea):
    """Retailer's marginal import $/kWh at an interval (for optimised dispatch ranking)."""
    h = iv['h']
    mdl = r.get('model')
    if mdl == 'hybrid':
        return float(r.get('sh_pk', 0.2)) + pea
    if mdl == 'variable' or mdl == 'variable_optimised':
        return iv['aemo'] + float(r.get('sh_pk', 0))
    if in_window(h, r.get('off_s', 0), r.get('off_e', 0)):
        return r.get('off_pk', 0)
    if in_window(h, r.get('ev_s', 0), r.get('ev_e', 0)) and r.get('ev_pk', 0) > 0:
        return r.get('ev_pk', 0)
    if in_window(h, r.get('pk_s', 0), r.get('pk_e', 0)):
        return r.get('pk_pk', 0)
    return r.get('sh_pk', 0)

def _exp_rate_at(r, iv):
    """Retailer's marginal feed-in $/kWh at an interval (for optimised dispatch ranking)."""
    h = iv['h']
    mdl = r.get('model')
    if mdl == 'variable' or mdl == 'variable_optimised':
        return iv['aemo'] * float(r.get('off_fit', 1.0))
    if mdl == 'hybrid':
        if in_window(h, r.get('sp_fit_s', 0), r.get('sp_fit_e', 0)) and r.get('sp_limit', 0) > 0:
            return r.get('sp_fit', 0)
        return r.get('off_fit', 0)
    er = r.get('sh_fit', 0)
    if in_window(h, r.get('sp_fit_s', 0), r.get('sp_fit_e', 0)) and r.get('sp_limit', 0) > 0:
        er = r.get('sp_fit', 0)
    elif in_window(h, r.get('pk_fit_s', 0), r.get('pk_fit_e', 0)):
        er = r.get('pk_fit', 0)
    elif in_window(h, r.get('off_fit_s', 0), r.get('off_fit_e', 0)):
        er = r.get('off_fit', 0)
    return er

def _battery_spec(retailers):
    """Shared battery spec: first retailer configured with a battery, else defaults."""
    for r in retailers:
        if float(r.get('bat_cap', 0)) > 0:
            return r
    return {'bat_cap': 41.92, 'bat_chg': 11.0, 'bat_dis': 10.0, 'bat_eff': 0.9,
            'soc_min': 0.02, 'soc_max': 1.0, 'init_soc': 0.5, 'inv_cap': 10.0,
            'ac_cap': 14.5}

def load_retailer_config(path):
    retailers = []
    numeric_fields = [
        'dsc', 'sub', 'off_pk', 'sh_pk', 'pk_pk',
        'off_fit', 'sh_fit', 'pk_fit', 'sp_fit', 'sp_fit2', 'sp_limit',
        'off_s', 'off_e', 'pk_s', 'pk_e', 'sp_s', 'sp_e',
        'off_fit_s', 'off_fit_e', 'sh_fit_s', 'sh_fit_e',
        'pk_fit_s', 'pk_fit_e', 'sp_fit_s', 'sp_fit_e',
        'fixed_export', 'ev_s', 'ev_e', 'ev_pk', 'off_limit', 'billing_day',
        'pea_base', 'pea_override', 'bat_cap', 'bat_chg', 'bat_dis', 'bat_eff',
        'soc_min', 'soc_max', 'init_soc', 'chg_pct', 'dis_pct', 'inv_cap',
        'ac_cap'
    ]
    with open(path, newline='') as f:
        reader = csv.DictReader(f)
        for row in reader:
            r = {k.strip(): (v.strip() if v is not None else '') for k, v in row.items()}
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
                'load_reg': _f(line[8]),
                'solar_reg': _f(line[13]),
            })
    return rows

def extract_intervals(rows):
    intervals = []
    prev_imp = prev_exp = 0.0
    prev_load = prev_solar = 0.0
    prev_date = None
    for row in rows:
        pe = row['pe']
        ci = row['Import_kWh']; ce = row['export']
        i_kwh = max(0.0, ci - prev_imp)
        e_kwh = max(0.0, ce - prev_exp)
        prev_imp = ci; prev_exp = ce
        try:
            dt = datetime.strptime(pe, '%Y-%m-%d %H:%M:%S')
        except: continue
        date = dt.strftime('%Y-%m-%d')
        if date != prev_date:
            prev_load = prev_solar = 0.0
            prev_date = date
        cl = row['load_reg']; cs = row['solar_reg']
        l_kwh = max(0.0, cl - prev_load) if cl >= prev_load else 0.0
        s_kwh = max(0.0, cs - prev_solar) if cs >= prev_solar else 0.0
        prev_load = cl; prev_solar = cs
        intervals.append({
            'pe': pe, 'date': date, 'time': dt.strftime('%H:%M:%S'),
            'h': dt.hour + dt.minute / 60.0,
            'i_kwh': i_kwh, 'e_kwh': e_kwh, 'cum_imp': ci, 'cum_exp': ce,
            'aemo': row['aemo_price'],
            'load_kwh': l_kwh, 'solar_kwh': s_kwh,
        })
    return intervals

# ─── Cost Calculator ─────────────────────────────────────────────────────────

def _dispatch_battery(r, intervals, pea_by_period, bat=None):
    """Tariff-aware, day-level battery dispatch for ANY retailer model.

    Simulates how the battery would be run to get the most out of that retailer.
    Battery energy is drawn from three sources (charged chronologically, rate and
    SOC limited): initial SOC, stored solar surplus (free), and grid charging at
    the retailer's cheap/free import windows. It is discharged to the highest-value
    uses first — avoid the retailer's most expensive imports, then earn its best
    feed-in credits — respecting per-interval discharge rates and chronological
    availability. Grid-bought energy is only used when the round trip is profitable
    (sell value > buy rate / efficiency), so an optimised dispatch can never be
    economically worse than simply leaving the battery idle.
    `bat` overrides the physical battery spec so all retailers share one battery.
    Returns dict: date -> per-interval {'sim_i','sim_e','curt','soc','chg','dis'}.
    """
    b = bat if bat is not None else r
    cap = float(b.get('bat_cap', 41.92))
    chg_rate = float(b.get('bat_chg', 11)) / 12.0
    dis_rate = float(b.get('bat_dis', 10)) / 12.0
    eff = float(b.get('bat_eff', 0.9))
    inv_kw = float(b.get('inv_cap', 10))
    inv_wh = inv_kw / 12.0
    ac_kw = float(b.get('ac_cap', 14.5))
    ac_wh = ac_kw / 12.0
    soc_min = float(b.get('soc_min', 0.02)) * cap
    soc_max = float(b.get('soc_max', 1.0)) * cap
    init_soc = float(b.get('init_soc', 0.5)) * cap
    off_limit = float(r.get('off_limit', 0))

    by_date = {}
    for iv in intervals:
        by_date.setdefault(iv['date'], []).append(iv)

    carry = bool(b.get('carry_soc', True))
    start_soc = init_soc
    results = {}
    for date in sorted(by_date):
        day = by_date[date]
        n = len(day)
        bp_key = _billing_period_key(date, int(r.get('billing_day', 4)))
        pea = pea_by_period.get(bp_key, 0.0)
        if r.get('model') == 'hybrid' and OPTIMISE_ALL:
            pea = -0.05
        imp = [_imp_rate_at(r, iv, pea) for iv in day]
        exp = [_exp_rate_at(r, iv) for iv in day]
        load = [iv['load_kwh'] for iv in day]
        solar = [iv['solar_kwh'] for iv in day]
        day_energy = sum(load) + sum(solar) + sum(iv['i_kwh'] + iv['e_kwh'] for iv in day)
        if day_energy < 0.01:
            results[date] = [{'sim_i': iv['i_kwh'], 'sim_e': iv['e_kwh'], 'curt': 0.0,
                              'soc': start_soc, 'chg': 0.0, 'dis': 0.0} for iv in day]
            continue

        deficit = [max(0.0, load[i] - solar[i]) for i in range(n)]
        surplus = [max(0.0, solar[i] - load[i]) for i in range(n)]

        # Feasibility guard: if the measured profile itself demands a battery
        # burst beyond the physical battery's capability (e.g. a load spike the
        # meter rounds into a single interval), it is not a reproducible baseline
        # to beat, so keep the measured profile for that day. This keeps the
        # optimised result never-worse-than-measured on every feasible day.
        burst = max(abs((load[i] - solar[i]) - (day[i]['i_kwh'] - day[i]['e_kwh'])) for i in range(n))
        exp_ok = all(day[i]['e_kwh'] <= 1e-9 or day[i]['e_kwh'] + day[i]['load_kwh'] <= inv_wh * 1.25 for i in range(n))
        if burst > max(dis_rate, chg_rate) * 1.2 or not exp_ok:
            results[date] = [{'sim_i': day[i]['i_kwh'], 'sim_e': day[i]['e_kwh'], 'curt': 0.0,
                              'soc': start_soc, 'chg': 0.0, 'dis': 0.0} for i in range(n)]
            continue

        # effective grid-buy rate for battery charging; an off-peak free allowance
        # (off_limit) makes the off window cost zero up to the daily pool
        buy = list(imp)
        if off_limit > 0:
            for i in range(n):
                if in_window(day[i]['h'], r.get('off_s', 0), r.get('off_e', 0)):
                    buy[i] = 0.0
        cheap_rate = min(buy) if buy else 0.0

        # ---- charge phase: solar surplus first (free, chronological) ----
        # Charge rate tapers from full (11 kW) down to 0 as SOC rises from 90% to
        # 100%, matching the inverter's behaviour near full charge.
        def taper_rate(s):
            if s <= 0.9 * soc_max:
                return 1.0
            return max(0.0, min(1.0, (soc_max - s) / max(1e-9, 0.1 * soc_max)))

        soc = start_soc
        solar_chg = [0.0] * n
        cum_free = [0.0] * n
        acc = start_soc - soc_min
        for i in range(n):
            if surplus[i] > 0:
                c = min(surplus[i], chg_rate * taper_rate(soc), (soc_max - soc) / eff)
                soc += c * eff
                solar_chg[i] = c
            acc += solar_chg[i] * eff
            cum_free[i] = acc
        free_E = max(0.0, soc - soc_min)

        # ---- discharge uses (value-ordered): avoid imports, then earn feed-in ----
        # Export uses respect the retailer's per-day super-peak export cap
        # (sp_limit): the first sp_limit kWh in the sp window earn sp_fit, any
        # remainder earns sp_fit2 (or pk_fit if the interval also falls in a pk
        # feed-in window), matching _fixed_tou_interval exactly. sp_fit uses carry
        # the remaining cap; once exhausted their allocation is capped at 0.
        sp_cap = float(r.get('sp_limit', 0))

        def _sp_overflow_rate(i):
            er2 = float(r.get('sp_fit2', 0))
            if in_window(day[i]['h'], r.get('pk_fit_s', 0), r.get('pk_fit_e', 0)):
                er2 = float(r.get('pk_fit', 0))
            return er2

        def _build_uses():
            uses = []
            for i in range(n):
                if deficit[i] > 0:
                    uses.append((imp[i], i, 'def', False))
                if exp[i] > 0:
                    if exp[i] == float(r.get('sp_fit', 0)) and sp_cap > 0:
                        uses.append((exp[i], i, 'exp', True))
                        er2 = _sp_overflow_rate(i)
                        if er2 > 0:
                            uses.append((er2, i, 'exp', False))
                    else:
                        uses.append((exp[i], i, 'exp', False))
            uses.sort(key=lambda u: -u[0])
            return uses
        uses = _build_uses()

        dis_def = [0.0] * n; dis_exp = [0.0] * n
        used_by = [0.0] * n

        def alloc_free(i, typ, amt):
            if typ == 'def':
                dis_def[i] += amt
            else:
                dis_exp[i] += amt
            for j in range(i, n):
                used_by[j] += amt

        remaining = free_E
        sp_used_free = 0.0
        # Export floor: carried-over battery energy (SOC held since yesterday)
        # may only be exported when the feed-in rate clears the round-trip
        # refill cost (shoulder rate / eff). Today's solar surplus is free, so it
        # may be exported at any positive FIT. This stops the sim from draining
        # carried energy at 2c/10c when its replacement cost is 40c+; export-
        # limited retailers carry their SOC forward instead (the overnight rule).
        export_floor = float(r.get('sh_pk', 0)) / eff
        carried_E = max(0.0, start_soc - soc_min)
        solar_E = max(0.0, free_E - carried_E)
        solar_rem = solar_E
        for val, i, typ, is_sp in uses:
            if remaining <= 0:
                break
            room = dis_rate - dis_def[i] - dis_exp[i]
            if room <= 0:
                continue
            # Uses are processed in value order, not time order, so a low-value
            # use at hour 16 must not consume battery energy that a high-value
            # use at hour 18 already reserved. Availability is the minimum slack
            # over all times from i onward: you can never discharge at t more
            # than the free energy accumulated by t.
            ar = max(0.0, min(cum_free[t] - used_by[t] for t in range(i, n)))
            sp_room = max(0.0, sp_cap - sp_used_free) if is_sp else 1e18
            tk = min(room, remaining, ar, sp_room)
            if typ == 'def':
                tk = min(tk, max(0.0, deficit[i] - dis_def[i]))
            if typ == 'exp' and val <= export_floor and not (is_sp and r.get('model') == 'hybrid'):
                tk = min(tk, max(0.0, solar_rem))
            tk = max(0.0, tk)
            if tk <= 0:
                continue
            alloc_free(i, typ, tk)
            remaining -= tk
            if typ == 'exp' and val <= export_floor:
                solar_rem -= tk
            if is_sp:
                sp_used_free += tk

        # ---- grid arbitrage: cheap/free window re-charges the battery and
        #      discharges it only into uses that clear the round-trip cost ----
        unmet = [max(0.0, deficit[i] - dis_def[i]) for i in range(n)]
        # Solar-first: solar fills the battery first (free), so grid arbitrage
        # may only buy the room solar alone cannot fill. This stops overnight
        # grid charging from displacing the next day's solar into a 3c export.
        solar_fill = max(0.0, soc - start_soc)
        room_start = max(0.0, soc_max - start_soc - solar_fill)
        arb_charge_cap = min(chg_rate * sum(1 for i in range(n) if buy[i] <= cheap_rate + 1e-12),
                             room_start)
        grid_def = [0.0] * n; grid_exp = [0.0] * n
        # Hybrid (FlowPower): solar-only charging. Grid arbitrage never clears the
        # round-trip cost (flat 41.36c import vs 35c sp export), so the battery is
        # charged solely from solar surplus and discharged into deficit coverage
        # and sp-window exports. Winter has no solar surplus, so the battery cannot
        # export (no bank) — FlowPower is not a usable retailer in winter.
        if r.get('model') == 'hybrid':
            grid_chg = [0.0] * n
            out = []
            soc2 = start_soc
            for i, iv in enumerate(day):
                chg_tot = solar_chg[i]
                dis_tot = dis_def[i] + dis_exp[i]
                net = load[i] - solar[i] + chg_tot - dis_tot
                sim_i = max(0.0, net)
                sim_e = max(0.0, -net)
                exp_room = max(0.0, inv_wh - load[i])
                curt = max(0.0, sim_e - exp_room)
                sim_e = min(sim_e, exp_room)
                soc2 = min(soc_max, max(soc_min, soc2 + chg_tot * eff - dis_tot))
                out.append({'sim_i': sim_i, 'sim_e': sim_e, 'curt': curt, 'soc': soc2,
                            'chg': chg_tot, 'dis': dis_tot})
            results[date] = out
            if carry:
                start_soc = soc2
            continue
        budget = arb_charge_cap * eff
        cum_grid = [0.0] * n
        gacc = 0.0
        for i in range(n):
            if buy[i] <= cheap_rate + 1e-12:
                gacc += chg_rate
            cum_grid[i] = gacc
        used_grid = [0.0] * n
        sp_used_grid = sp_used_free
        for val, i, typ, is_sp in uses:
            if budget <= 0:
                break
            if val <= cheap_rate / eff:
                break
            room = dis_rate - dis_def[i] - dis_exp[i] - grid_def[i] - grid_exp[i]
            if room <= 0:
                continue
            ar = max(0.0, min(cum_grid[t] - used_grid[t] for t in range(i, n)))
            sp_room = max(0.0, sp_cap - sp_used_grid) if is_sp else 1e18
            if typ == 'def':
                tk = min(unmet[i] - grid_def[i], room, budget, ar, sp_room)
            else:
                tk = min(room, budget, ar, sp_room)
            tk = max(0.0, tk)
            if tk <= 0:
                continue
            if typ == 'def':
                grid_def[i] += tk
            else:
                grid_exp[i] += tk
            budget -= tk
            for j in range(i, n):
                used_grid[j] += tk
            if is_sp:
                sp_used_grid += tk
        grid_chg = [0.0] * n
        fund = (sum(grid_def) + sum(grid_exp)) / eff
        fund_rem = fund
        soc_g = start_soc
        for i in range(n):
            soc_g = min(soc_max, soc_g + solar_chg[i] * eff)
            if buy[i] > cheap_rate + 1e-12:
                continue
            if fund_rem <= 0:
                break
            # AC-side import limit: battery charge + net load <= ac_cap.
            chg_room = max(0.0, ac_wh - max(0.0, load[i] - solar[i]))
            tk = min(chg_rate * taper_rate(soc_g), fund_rem, chg_room,
                     max(0.0, (soc_max - soc_g) / eff))
            tk = max(0.0, tk)
            grid_chg[i] += tk
            soc_g += tk * eff
            fund_rem -= tk
        # Export-limited retailers (sp_limit>0 or off_limit>0) carry their bank
        # forward instead of exporting carried energy at a loss: hold back enough
        # SOC to cover tomorrow morning's deficit before the cheap/off window,
        # scaling down grid-funded exports if they would break that reserve. This
        # keeps the sim's winter bank from draining to 2% and exporting at 10c.
        export_limited = float(r.get('sp_limit', 0)) > 0 or float(r.get('off_limit', 0)) > 0
        dates_all = sorted(by_date)
        nidx = dates_all.index(date) + 1
        nxt = by_date.get(dates_all[nidx]) if nidx < len(dates_all) else None
        reserve = 0.0
        if export_limited and nxt is not None:
            off_s_h = float(r.get('off_s', 0) or 12)
            if r.get('model') == 'hybrid':
                off_s_h = 12.0
            for iv in nxt:
                if iv['h'] < min(off_s_h, 12):
                    reserve += max(0.0, iv['load_kwh'] - iv['solar_kwh'])
            reserve = min(reserve, soc_max - soc_min)
        tot_chg = (sum(solar_chg) + sum(grid_chg)) * eff
        tot_dis = sum(dis_def) + sum(dis_exp) + sum(grid_def) + sum(grid_exp)
        end_soc = start_soc + tot_chg - tot_dis
        shortfall = max(0.0, reserve - end_soc)
        if shortfall > 0 and sum(grid_exp) > 1e-9:
            scale = max(0.0, 1.0 - shortfall / sum(grid_exp))
            grid_exp = [v * scale for v in grid_exp]
        g_total = sum(grid_chg)
        if fund > 0 and g_total < fund - 1e-9:
            fscale = g_total / fund
            grid_def = [v * fscale for v in grid_def]
            grid_exp = [v * fscale for v in grid_exp]

        out = []
        soc2 = start_soc
        for i, iv in enumerate(day):
            chg_tot = solar_chg[i] + grid_chg[i]
            dis_tot = dis_def[i] + dis_exp[i] + grid_def[i] + grid_exp[i]
            net = load[i] - solar[i] + grid_chg[i] - dis_tot
            sim_i = max(0.0, net)
            sim_e = max(0.0, -net)
            exp_room = max(0.0, inv_wh - load[i])
            curt = max(0.0, sim_e - exp_room)
            sim_e = min(sim_e, exp_room)
            soc2 = min(soc_max, max(soc_min, soc2 + chg_tot * eff - dis_tot))
            out.append({'sim_i': sim_i, 'sim_e': sim_e, 'curt': curt, 'soc': soc2,
                        'chg': chg_tot, 'dis': dis_tot})
        results[date] = out
        if carry:
            start_soc = soc2
    return results

def _fixed_tou_interval(d, r, h, ti, ek):
    imp_rate = r.get('sh_pk', 0)
    if in_window(h, r.get('off_s', 0), r.get('off_e', 0)):
        imp_rate = r.get('off_pk', 0)
        if r.get('off_limit', 0) > 0:
            fu = d.get('freeUsage', 0)
            if fu < r['off_limit']:
                fp = min(ti, r['off_limit'] - fu)
                d['freeUsage'] = fu + fp
                bal = r.get('off_pk', 0)
                if bal == 0:
                    bal = r.get('sh_pk', 0)
                d['import'] += (ti - fp) * bal
                imp_rate = None
            elif r.get('off_pk', 0) == 0:
                imp_rate = r.get('sh_pk', 0)
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

    sims = {}
    if OPTIMISE_ALL:
        bat = _battery_spec(retailers)
        for r in retailers:
            sims[r['name']] = _dispatch_battery(r, intervals, pea_by_period, bat)
    else:
        for r in retailers:
            if r['model'] == 'variable_optimised':
                sims[r['name']] = _dispatch_battery(r, intervals, pea_by_period, _battery_spec(retailers))

    def _accumulate(sims_dict):
        adata = {}
        for date_str, day_ivs in iv_by_date.items():
            add = {}
            for r in retailers:
                add[r['name']] = {
                    'intervals': 0, 'lastTime': '', 'totalImport': 0.0, 'totalExport': 0.0,
                    'import': 0.0, 'export': 0.0, 'spExportUsed': 0.0,
                    'hr18': 0.0, 'hr19': 0.0, 'hr20': 0.0,
                    'offKwh': 0.0, 'shKwh': 0.0, 'pkKwh': 0.0, 'evKwh': 0.0,
                    'spExportKwh': 0.0, 'pkExportKwh': 0.0, 'shExportKwh': 0.0, 'offExportKwh': 0.0,
                    'curtailKwh': 0.0,
                }
            adata[date_str] = add
            sday = {r['name']: sims_dict.get(r['name'], {}).get(date_str) for r in retailers}
            for iv_idx, iv in enumerate(day_ivs):
                h = iv['h']
                for r in retailers:
                    d = add[r['name']]
                    si = sday[r['name']]
                    ti = iv['i_kwh']; ek = iv['e_kwh']
                    if si:
                        ti = si[iv_idx]['sim_i']; ek = si[iv_idx]['sim_e']
                        d['curtailKwh'] += si[iv_idx].get('curt', 0.0)
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
                    elif r['model'] in ('variable', 'variable_optimised'):
                        d['import'] += ti * (iv['aemo'] + r.get('sh_pk', 0))
                        d['export'] += ek * iv['aemo'] * r.get('off_fit', 0)
        return adata

    daily_data = _accumulate(sims)

    def _finalize(d, r, date_str):
        if r['model'] == 'fixed_tou':
            off_bal = max(0.0, d['offKwh'] - d.get('freeUsage', 0))
            off_rate = r.get('off_pk', 0)
            if off_rate == 0 and r.get('off_limit', 0) > 0:
                off_rate = r.get('sh_pk', 0)
            da_imp = (off_bal * off_rate + d['shKwh'] * r.get('sh_pk', 0) +
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
        rs = round(r.get('sub', 0), 2)
        gr = r.get('glo_rebate', '0')
        if float(gr) > 0 and d['hr18'] < 0.1 and d['hr19'] < 0.1 and d['hr20'] < 0.1:
            reb = 1.00
        else: reb = 0.0
        d['gloRebate'] = reb; d['net'] = round(ri - re + rd + rs - reb, 2)

    if OPTIMISE_ALL:
        meas_data = _accumulate({})
        used_meas = set()
        for date_str in daily_data.keys():
            for r in retailers:
                d = daily_data[date_str][r['name']]
                md = meas_data[date_str][r['name']]
                _finalize(d, r, date_str)
                _finalize(md, r, date_str)
                if md['net'] < d['net']:
                    daily_data[date_str][r['name']] = md
                    used_meas.add((date_str, r['name']))
    else:
        used_meas = set()

    daily_summary = {}; chart_data = []; five_min_detail = {}
    
    for date_str in sorted(daily_data.keys()):
        day_ivs = iv_by_date[date_str]
        measured_imp = sum(iv['i_kwh'] for iv in day_ivs)
        measured_exp = sum(iv['e_kwh'] for iv in day_ivs)
        measured_solar = sum(iv.get('solar_kwh', 0.0) for iv in day_ivs)
        measured_load = sum(iv.get('load_kwh', 0.0) for iv in day_ivs)
        complete = len(day_ivs) >= 288
        ds = {'totalImport': round(measured_imp, 3), 'totalExport': round(measured_exp, 3),
              'totalSolar': round(measured_solar, 3), 'totalLoad': round(measured_load, 3), 'complete': complete, 'retailers': {}}
        cheapest_net = float('inf'); cheapest_name = ''
        for r in retailers:
            d = daily_data[date_str][r['name']]
            _finalize(d, r, date_str)

            ri = d['import']; re = d['export']; rd = round(r.get('dsc', 0), 2)
            rs = round(r.get('sub', 0), 2)
            reb = d['gloRebate']
            
            ds['retailers'][r['name']] = {'dsc': rd, 'sub': rs, 'import': ri, 'export': re, 'net': d['net'], 'gloRebate': reb,
                                          'curtail': round(d.get('curtailKwh', 0), 3)}
            if d['net'] < cheapest_net: cheapest_net = d['net']; cheapest_name = r['name']
        ds['cheapest'] = cheapest_name; daily_summary[date_str] = ds
        cd = {'date': date_str, 'retailers': {}}
        for r in retailers:
            cd['retailers'][r['name']] = round(daily_data[date_str][r['name']]['net'], 2)
        cd['cheapest'] = cheapest_name; chart_data.append(cd)
    
    # 5-min detail
    for r in [x for x in retailers if x['model'] in ('fixed_tou', 'hybrid', 'variable', 'variable_optimised')]:
        fm = {}
        for date_str in sorted(iv_by_date.keys()):
            outs = []; spu = 0; hr18 = hr19 = hr20 = 0.0; tik = tek = tic = tec = 0.0
            bp_key = _billing_period_key(date_str, int(r.get('billing_day', 4)))
            pea = pea_by_period.get(bp_key, 0.0)
            sday = sims.get(r['name'], {}).get(date_str)
            if (date_str, r['name']) in used_meas:
                sday = None
            for iv_idx, iv in enumerate(iv_by_date[date_str]):
                h = iv['h']; i_kwh = iv['i_kwh']; e_kwh = iv['e_kwh']
                if sday:
                    i_kwh = sday[iv_idx]['sim_i']; e_kwh = sday[iv_idx]['sim_e']
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
                elif r['model'] in ('variable', 'variable_optimised'):
                    tou = 'Wsh'; ir = iv['aemo'] + r.get('sh_pk', 0)
                    er = iv['aemo'] * r.get('off_fit', 0); fit = 'Wsh'
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
                o = {'time': iv['time'][:5], 'tou': tou, 'fit': fit, 'ik': round(i_kwh, 3), 'ek': round(e_kwh, 3),
                     'ir': round(ir, 4), 'er': round(er, 4), 'ic': round(ic, 3), 'ec': round(ec, 3)}
                if sday:
                    o['soc'] = round(sday[iv_idx]['soc'], 2)
                    o['curt'] = round(sday[iv_idx]['curt'], 3)
                outs.append(o)
            reb = 1.0 if (float(r.get('glo_rebate','0')) > 0 and hr18 < 0.1 and hr19 < 0.1 and hr20 < 0.1) else 0.0
            nt = round(tic - tec + r.get('dsc', 0) + r.get('sub', 0) - reb, 2)
            outs.append({'time': 'TOTAL', 'ik': round(tik, 3), 'ek': round(tek, 3),
                         'ic': round(tic, 3), 'ec': round(tec, 3),
                         'dsc': r.get('dsc', 0), 'sub': r.get('sub', 0), 'rebate': reb, 'net': nt,
                         'hr18': round(hr18, 3), 'hr19': round(hr19, 3), 'hr20': round(hr20, 3)})
            fm[date_str] = {'intervals': outs, 'summary': {'hr18': hr18, 'hr19': hr19, 'hr20': hr20, 'net': nt, 'dsc': r.get('dsc', 0), 'sub': r.get('sub', 0), 'rebate': reb}}
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

_PVGIS_TERRIGAL = {1: 4.71, 2: 4.24, 3: 3.58, 4: 2.91, 5: 2.28, 6: 1.78,
                   7: 2.14, 8: 2.95, 9: 3.84, 10: 4.42, 11: 4.86, 12: 5.04}
_EST_MONTHS_AHEAD = 4

def _estimate_months(daily_summary, retailers):
    """Guestimate values for months after the last observed month.

    Solar: PVGIS expected production for Terrigal, scaled by the factor the
    user's system achieves relative to the area average (per season). Other
    flows (imp/exp/load) and retailer nets are linearly interpolated between
    the last observed month and the next observed month around the annual
    cycle. Returns {month_key: {'imp','exp','solar','days','retailers',}}.
    Returns {} when no estimates are needed.
    """
    if not daily_summary:
        return {}
    rnames = [r['name'] for r in retailers]
    obs = {}
    for ds, d in daily_summary.items():
        if not d.get('complete', True):
            continue
        m = ds[:7]
        if m not in obs:
            obs[m] = {'imp': 0.0, 'exp': 0.0, 'solar': 0.0, 'load': 0.0, 'days': 0,
                      'ret': {rn: 0.0 for rn in rnames}}
        om = obs[m]
        om['imp'] += d['totalImport']; om['exp'] += d['totalExport']
        om['solar'] += d.get('totalSolar', 0); om['load'] += d.get('totalLoad', 0)
        om['days'] += 1
        for rn in rnames:
            om['ret'][rn] += d['retailers'].get(rn, {}).get('net', 0)
    last = sorted(obs.keys())[-1]
    last_y, last_m = int(last[:4]), int(last[5:7])

    def daily(key):
        om = obs.get(key)
        if not om or not om['days']:
            return None
        return {'imp': om['imp'] / om['days'], 'exp': om['exp'] / om['days'],
                'solar': om['solar'] / om['days'], 'load': om['load'] / om['days'],
                'ret': {rn: om['ret'][rn] / om['days'] for rn in rnames}}

    # Seasonal PVGIS factor the system actually achieves (obs solar / PVGIS)
    season_f = {'summer': [], 'autumn': [], 'winter': [], 'spring': []}
    def season_of(mo):
        if mo in (12, 1, 2): return 'summer'
        if mo in (3, 4, 5): return 'autumn'
        if mo in (6, 7, 8): return 'winter'
        return 'spring'
    for m, om in obs.items():
        if not om['days']: continue
        mo = int(m[5:7])
        if mo in _PVGIS_TERRIGAL:
            season_f[season_of(mo)].append((om['solar'] / om['days']) / _PVGIS_TERRIGAL[mo])
    def seavg(season):
        v = season_f[season]
        return sum(v) / len(v) if v else None
    wf = seavg('winter'); sf = seavg('summer')
    def pvgis_factor(mo):
        s = season_of(mo)
        if seavg(s) is not None:
            return seavg(s)
        if s == 'spring' and wf is not None and sf is not None:
            frac = (mo - 9) / 2.0
            return wf + (sf - wf) * frac
        return sf if sf is not None else (wf if wf is not None else 10.0)

    # Interpolation anchors: last observed month and the next observed around cycle
    obs_months = sorted(int(m[5:7]) for m in obs)
    nxt = next((mo for mo in obs_months if mo > last_m), None)
    if nxt is None:
        nxt = obs_months[0]
    a_key = f"{last_y:04d}-{last_m:02d}"
    b_key = f"{last_y:04d}-{nxt:02d}"
    if b_key not in obs:
        b_key = f"{last_y - 1:04d}-{nxt:02d}"
    if b_key not in obs:
        b_key = f"{last_y + 1:04d}-{nxt:02d}"
    a = daily(a_key); b = daily(b_key)
    if a is None or b is None:
        return {}
    span = (nxt - last_m) % 12
    if span == 0: span = 12

    est = {}
    y, mo = last_y, last_m
    import calendar
    for _ in range(_EST_MONTHS_AHEAD):
        mo += 1
        if mo > 12:
            mo = 1; y += 1
        t = (mo - last_m) % 12 / span
        est_solar = _PVGIS_TERRIGAL[mo] * pvgis_factor(mo)
        est_imp = a['imp'] + (b['imp'] - a['imp']) * t
        est_exp = a['exp'] + (b['exp'] - a['exp']) * t
        est_load = a['load'] + (b['load'] - a['load']) * t
        days = calendar.monthrange(y, mo)[1]
        ret = {}
        for rn in rnames:
            ret[rn] = (a['ret'][rn] + (b['ret'][rn] - a['ret'][rn]) * t) * days
        mk = f"{y:04d}-{mo:02d}"
        est[mk] = {'imp': est_imp * days, 'exp': est_exp * days,
                   'solar': est_solar * days, 'load': est_load * days,
                   'days': days, 'retailers': ret}
    return est

def monthly_report_html(daily_summary, retailers):
    months = {}
    for ds, d in sorted(daily_summary.items()):
        m = ds[:7]
        if m not in months:
            months[m] = {'imp': 0, 'exp': 0, 'solar': 0, 'load': 0, 'days': 0, 'retailers': {}, 'est': False}
        if not d.get('complete', True):
            continue
        months[m]['imp'] += d['totalImport']
        months[m]['exp'] += d['totalExport']
        months[m]['solar'] += d.get('totalSolar', 0)
        months[m]['load'] += d.get('totalLoad', 0)
        months[m]['days'] += 1
        for r in retailers:
            rn = r['name']
            v = d['retailers'].get(rn, {}).get('net', 0)
            months[m]['retailers'][rn] = months[m]['retailers'].get(rn, 0) + v
    has_est = False
    for m, mm in _estimate_months(daily_summary, retailers).items():
        mm['est'] = True; months[m] = mm; has_est = True
    html = '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:13px;white-space:nowrap">'
    html += '<thead><tr style="background:#1a1a1a;color:white">'
    html += '<th style="padding:4px;text-align:left;position:sticky;left:0;background:#1a1a1a;z-index:2">Month</th>'
    html += '<th style="padding:4px;text-align:right">Avg Imp kWh</th><th style="padding:4px;text-align:right">Avg Exp kWh</th>'
    html += '<th style="padding:4px;text-align:right">Avg Solar kWh</th><th style="padding:4px;text-align:right">Avg Load kWh</th>'
    for r in retailers:
        html += f'<th style="padding:4px;text-align:right">{r["name"]}</th>'
    html += '<th style="padding:4px;text-align:right;color:#4CAF50">Cheapest</th></tr></thead><tbody>'
    for m in sorted(months.keys(), reverse=True):
        mm = months[m]
        cheapest = min(mm['retailers'], key=lambda rn: mm['retailers'][rn])
        avg_imp = mm['imp'] / mm['days'] if mm['days'] else 0.0
        avg_exp = mm['exp'] / mm['days'] if mm['days'] else 0.0
        avg_sol = mm['solar'] / mm['days'] if mm['days'] else 0.0
        avg_load = mm['load'] / mm['days'] if mm['days'] else 0.0
        if mm.get('est'):
            rowbg = '#0d1420'; mlbl = f'~{m}'; mcol = '#5b7fbf'; estcls = 'font-style:italic'
            labcol = '#7b8ea8'
        else:
            rowbg = '#111'; mlbl = m; mcol = '#aaa'; estcls = ''; labcol = '#aaa'
        html += f'<tr style="background:{rowbg}"><td style="padding:4px;text-align:left;color:{labcol};{estcls};position:sticky;left:0;background:{rowbg};z-index:1">{mlbl}</td>'
        html += f'<td style="padding:4px;text-align:right;color:#8cf">{avg_imp:.1f}</td>'
        html += f'<td style="padding:4px;text-align:right;color:#fc8">{avg_exp:.1f}</td>'
        html += f'<td style="padding:4px;text-align:right;color:#9c9">{avg_sol:.1f}</td>'
        html += f'<td style="padding:4px;text-align:right;color:#caa">{avg_load:.1f}</td>'
        for r in retailers:
            v = mm['retailers'].get(r['name'], 0)
            c = '#4CAF50' if r['name'] == cheapest else '#ccc'
            html += f'<td style="padding:4px;text-align:right;color:{c};{estcls}">${v:.2f}</td>'
        html += f'<td style="padding:4px;text-align:right;color:#ccc;font-weight:bold">{cheapest}</td></tr>'
    t_imp = sum(mm['imp'] for mm in months.values() if not mm.get('est'))
    t_exp = sum(mm['exp'] for mm in months.values() if not mm.get('est'))
    t_days = sum(mm['days'] for mm in months.values() if not mm.get('est'))
    t_solar = sum(mm['solar'] for mm in months.values() if not mm.get('est'))
    t_load = sum(mm['load'] for mm in months.values() if not mm.get('est'))
    t_ret = {r['name']: sum(mm['retailers'].get(r['name'], 0) for mm in months.values() if not mm.get('est')) for r in retailers}
    t_cheapest = min(t_ret, key=lambda rn: t_ret[rn])
    avg_imp_t = t_imp / t_days if t_days else 0.0
    avg_exp_t = t_exp / t_days if t_days else 0.0
    avg_sol_t = t_solar / t_days if t_days else 0.0
    avg_load_t = t_load / t_days if t_days else 0.0
    html += '<tr style="background:#222;font-weight:bold"><td style="padding:4px;text-align:left;color:#fff;position:sticky;left:0;background:#222;z-index:1">TOTAL</td>'
    html += f'<td style="padding:4px;text-align:right;color:#8cf">{avg_imp_t:.1f}</td><td style="padding:4px;text-align:right;color:#fc8">{avg_exp_t:.1f}</td>'
    html += f'<td style="padding:4px;text-align:right;color:#9c9">{avg_sol_t:.1f}</td><td style="padding:4px;text-align:right;color:#caa">{avg_load_t:.1f}</td>'
    for r in retailers:
        v = t_ret.get(r['name'], 0)
        c = '#4CAF50' if r['name'] == t_cheapest else '#fff'
        html += f'<td style="padding:4px;text-align:right;color:{c}">${v:.2f}</td>'
    html += f'<td style="padding:4px;text-align:right;color:#4CAF50">{t_cheapest}</td></tr>'
    html += '</tbody></table></div>'
    if has_est:
        html += ('<div style="color:#7b8ea8;font-size:12px;font-style:italic;padding:6px 4px">'
                 'Rows prefixed with ~ are guestimates (PVGIS solar expectations calibrated to '
                 'your system + seasonal load averages). They drop off automatically once actuals arrive.</div>')
    return html

_SEASONS = [('Summer', [12, 1, 2]), ('Autumn', [3, 4, 5]), ('Winter', [6, 7, 8]), ('Spring', [9, 10, 11])]

def _season_label(y, mo):
    if mo == 12:
        return f'Summer {y}/{y+1}'
    if mo <= 2:
        return f'Summer {y-1}/{y}'
    if mo <= 5:
        return f'Autumn {y}'
    if mo <= 8:
        return f'Winter {y}'
    return f'Spring {y}'

def seasonal_report_html(daily_summary, retailers):
    seasons = {}
    for ds, d in sorted(daily_summary.items()):
        y = int(ds[:4]); mo = int(ds[5:7])
        label = _season_label(y, mo)
        if label not in seasons:
            seasons[label] = {'imp': 0, 'exp': 0, 'retailers': {}, 'est': False}
        if not d.get('complete', True):
            continue
        seasons[label]['imp'] += d['totalImport']
        seasons[label]['exp'] += d['totalExport']
        for r in retailers:
            rn = r['name']
            v = d['retailers'].get(rn, {}).get('net', 0)
            seasons[label]['retailers'][rn] = seasons[label]['retailers'].get(rn, 0) + v
    has_est = False
    for m, mm in _estimate_months(daily_summary, retailers).items():
        y = int(m[:4]); mo = int(m[5:7])
        label = _season_label(y, mo)
        if label in seasons:
            continue
        seasons[label] = {'imp': mm['imp'], 'exp': mm['exp'],
                          'retailers': {rn: mm['retailers'].get(rn, 0) for rn in [r['name'] for r in retailers]},
                          'est': True}
        has_est = True
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
        if ss.get('est'):
            rowbg = '#0d1420'; labcol = '#7b8ea8'; estcls = 'font-style:italic'; slbl = f'~{s}'
        else:
            rowbg = '#111'; labcol = '#aaa'; estcls = ''; slbl = s
        html += f'<tr style="background:{rowbg}"><td style="padding:4px;text-align:left;color:{labcol};{estcls};position:sticky;left:0;background:{rowbg};z-index:1">{slbl}</td>'
        html += f'<td style="padding:4px;text-align:right;color:#8cf">{ss["imp"]:.1f}</td>'
        html += f'<td style="padding:4px;text-align:right;color:#fc8">{ss["exp"]:.1f}</td>'
        for r in retailers:
            v = ss['retailers'].get(r['name'], 0)
            c = '#4CAF50' if r['name'] == cheapest else '#ccc'
            html += f'<td style="padding:4px;text-align:right;color:{c};{estcls}">${v:.2f}</td>'
        html += f'<td style="padding:4px;text-align:right;color:#ccc;font-weight:bold">{cheapest}</td></tr>'
    t_imp = sum(ss['imp'] for ss in seasons.values() if not ss.get('est'))
    t_exp = sum(ss['exp'] for ss in seasons.values() if not ss.get('est'))
    t_ret = {r['name']: sum(ss['retailers'].get(r['name'], 0) for ss in seasons.values() if not ss.get('est')) for r in retailers}
    t_cheapest = min(t_ret, key=lambda rn: t_ret[rn])
    html += '<tr style="background:#222;font-weight:bold"><td style="padding:4px;text-align:left;color:#fff;position:sticky;left:0;background:#222;z-index:1">TOTAL</td>'
    html += f'<td style="padding:4px;text-align:right;color:#8cf">{t_imp:.1f}</td><td style="padding:4px;text-align:right;color:#fc8">{t_exp:.1f}</td>'
    for r in retailers:
        v = t_ret.get(r['name'], 0)
        c = '#4CAF50' if r['name'] == t_cheapest else '#fff'
        html += f'<td style="padding:4px;text-align:right;color:{c}">${v:.2f}</td>'
    html += f'<td style="padding:4px;text-align:right;color:#4CAF50">{t_cheapest}</td></tr>'
    html += '</tbody></table></div>'
    if has_est:
        html += ('<div style="color:#7b8ea8;font-size:12px;font-style:italic;padding:6px 4px">'
                 'Rows prefixed with ~ are guestimates (PVGIS solar expectations calibrated to '
                 'your system + seasonal load averages). They drop off automatically once actuals arrive.</div>')
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
    sub = dr.get('sub', tot.get('sub', 0))
    h = (f'<div style="display:flex;justify-content:space-between;padding:6px 10px;background:#151515;color:#aaa;font-size:14px;font-weight:bold;border-bottom:1px solid #222">'
         f'<span>{date_str} — {rname}</span>'
         f'<span style="white-space:nowrap">{ik:.2f} kWh &nbsp;&nbsp;|&nbsp;&nbsp; Exp {ek:.2f} kWh &nbsp;&nbsp;|&nbsp;&nbsp; '
         f'Import ${ic:.2f} &nbsp;&nbsp;|&nbsp;&nbsp; Export ${ec:.2f} &nbsp;&nbsp;|&nbsp;&nbsp; '
         f'DSC ${dsc:.2f} &nbsp;&nbsp;|&nbsp;&nbsp; Sub ${sub:.2f} &nbsp;&nbsp;|&nbsp;&nbsp; Rebate ${reb:.2f} &nbsp;&nbsp;|&nbsp;&nbsp; '
         f'Net ${net:.2f}</span></div>')
    t = '<div style="overflow-x:auto"><table style="width:100%;border-collapse:collapse;font-size:15px"><thead><tr style="background:#1a1a1a;color:white;position:sticky;top:0;z-index:1">'
    t += '<th style="padding:4px 6px;text-align:left">Time</th><th style="padding:4px 6px;text-align:left">TOU</th>'
    t += '<th style="padding:4px 6px;text-align:left">FIT</th>'
    t += '<th style="padding:4px 6px;text-align:right">Imp kWh</th><th style="padding:4px 6px;text-align:right">Exp kWh</th>'
    t += '<th style="padding:4px 6px;text-align:right">Imp $/kWh</th><th style="padding:4px 6px;text-align:right">Exp $/kWh</th>'
    t += '<th style="padding:4px 6px;text-align:right">Imp $</th><th style="padding:4px 6px;text-align:right">Exp $</th>'
    t += '<th style="padding:4px 6px;text-align:right">SOC %</th><th style="padding:4px 6px;text-align:right">Curt kWh</th>'
    t += '<th style="padding:4px 6px;text-align:right">Net $</th></tr></thead><tbody>'
    for iv in ivs:
        if iv.get('time') == 'TOTAL': continue
        nt = iv.get('ic', 0) - iv.get('ec', 0)
        soc = iv.get('soc')
        curt = iv.get('curt', 0)
        soc_cell = f'<td style="padding:2px 6px;text-align:right;color:#9cf">{soc/41.92*100:.0f}</td>' if soc is not None else '<td style="padding:2px 6px;text-align:right;color:#333">-</td>'
        curt_cell = f'<td style="padding:2px 6px;text-align:right;color:#f99">{curt:.3f}</td>' if curt else '<td style="padding:2px 6px;text-align:right;color:#333">-</td>'
        t += (f'<tr><td style="padding:2px 6px;color:#aaa">{iv["time"]}</td>'
              f'<td style="padding:2px 6px;color:#ccc">{iv.get("tou","")}</td>'
              f'<td style="padding:2px 6px;color:#fc8">{iv.get("fit","")}</td>'
              f'<td style="padding:2px 6px;text-align:right;color:#8cf">{iv["ik"]:.3f}</td>'
              f'<td style="padding:2px 6px;text-align:right;color:#fc8">{iv["ek"]:.3f}</td>'
              f'<td style="padding:2px 6px;text-align:right;color:#888">{iv["ir"]:.4f}</td>'
              f'<td style="padding:2px 6px;text-align:right;color:#888">{iv["er"]:.4f}</td>'
              f'<td style="padding:2px 6px;text-align:right;color:#ff8a65">{iv["ic"]:.3f}</td>'
              f'<td style="padding:2px 6px;text-align:right;color:#8fbc8f">{iv["ec"]:.3f}</td>'
              f'{soc_cell}{curt_cell}'
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
    sub = dr.get('sub', tot.get('sub', 0))

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
           f'DSC ${dsc:.2f} &nbsp;&nbsp;|&nbsp;&nbsp; Sub ${sub:.2f} &nbsp;&nbsp;|&nbsp;&nbsp; Rebate ${reb:.2f} &nbsp;&nbsp;|&nbsp;&nbsp; '
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
.header{display:flex;justify-content:space-between;align-items:center;padding:6px 10px;background:#101010;border-bottom:1px solid #2a2a2a;flex-shrink:0}
.header span{color:#888}
.header label{display:flex;align-items:center;gap:6px;color:#888;cursor:pointer}
#optStatus{color:#4CAF50}
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
<div class=header>
<span>Energy Retailer Comparison <span id=optStatus></span></span>
<label title="Run every retailer with optimal battery dispatch for its own tariff (persistent)">
<input type=checkbox id=optimiseAll onchange=toggleOptimise() style="accent-color:#4CAF50">
Optimised battery for all retailers</label>
</div>
<script>
function loadOptimise(){
fetch('/api/settings').then(function(r){return r.json()}).then(function(s){
var el=document.getElementById('optimiseAll');if(el)el.checked=!!s.optimise_all;
var st=document.getElementById('optStatus');if(st)st.textContent=s.optimise_all?'(optimised)':'';
});
}
function toggleOptimise(){
var el=document.getElementById('optimiseAll');if(!el)return;
el.disabled=true;
fetch('/api/optimise',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({on:el.checked})})
.then(function(){location.reload()})
.catch(function(){el.disabled=false});
}
loadOptimise();
</script>
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
    ('bat_cap', 'Bat Cap kWh', False),
    ('bat_chg', 'Bat Chg kW', False),
    ('bat_dis', 'Bat Dis kW', False),
    ('bat_eff', 'Bat Eff', False),
    ('soc_min', 'SOC Min', False),
    ('soc_max', 'SOC Max', False),
    ('init_soc', 'Init SOC', False),
    ('chg_pct', 'Chg Pct', False),
    ('dis_pct', 'Dis Pct', False),
    ('inv_cap', 'Inv Cap kW', False),
    ('ac_cap', 'AC Cap kW', False),
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
                    '<option value="variable_optimised"%s>var_optim</option>' % (' selected' if val == 'variable_optimised' else '') +
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
                '<option value="variable">variable</option>'
                '<option value="variable_optimised">var_optim</option></select></td>')
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
        elif path == '/api/settings':
            self._json({'optimise_all': OPTIMISE_ALL})
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
        global OPTIMISE_ALL
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
        elif path == '/api/optimise':
            content_len = int(self.headers.get('Content-Length', 0))
            body_raw = self.rfile.read(content_len) if content_len > 0 else b''
            try:
                on = json.loads(body_raw).get('on', not OPTIMISE_ALL)
                save_settings({'optimise_all': bool(on)})
                OPTIMISE_ALL = bool(on)
                _cache['data_hash'] = ''
                log.info(f'Optimise-all set to {bool(on)}, cache invalidated')
                self._json({'optimise_all': bool(on)})
            except Exception as e:
                log.error(f'Optimise toggle failed: {e}')
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
    global CSV_PATH, CONFIG_PATH, SETTINGS_PATH, PORT, DAEMON
    i = 1
    while i < len(sys.argv):
        a = sys.argv[i]
        if a == '--port' and i+1 < len(sys.argv): PORT = int(sys.argv[i+1]); i+=2
        elif a == '--csv' and i+1 < len(sys.argv): CSV_PATH = sys.argv[i+1]; i+=2
        elif a == '--config' and i+1 < len(sys.argv): CONFIG_PATH = sys.argv[i+1]; i+=2
        elif a == '--daemon': DAEMON = True; i+=1
        else: print(f'Unknown: {a}'); return

    SETTINGS_PATH = os.path.join(os.path.dirname(os.path.abspath(CONFIG_PATH)), 'settings.json')
    
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
