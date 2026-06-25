# Project: ElectricityRetailerComparison

## Goal
Multi-retailer electricity cost comparison system comparing FlowPower against Origin/Globird/CovaU/Amber, integrated as a dynamic Node-RED flow with HA dashboard, deployed selectively via `PUT /flow/:id`.

## Constraints & Preferences
- Three pricing models: FlowPower (hybrid), Amber (variable), Fixed TOU (Origin/Globird/CovaU)
- Script tags stripped by `custom:html-card` — `<iframe>` used instead for the 5-min interactive report
- Node-RED `httpNodeRoot` = `/endpoint`; HTTP nodes served under `/endpoint/`
- HA at http://192.168.50.100:8123, Node-RED admin API on port 1880 (behind nginx with basic auth `stilgar` / `Ha0118021669`)
- HA CSV source: `/share/file_notifications/5minelec.csv` (14 cols, cumulative energy columns)
- Billing cycle: configurable via `billing_day` column in retailer_config.csv (default 4, 4th→3rd)
- Deploy via `bash deploy.sh` — uses `PUT /flow/tab_energy_retailer_comparison` (does not touch other tabs)
- Node-RED v5.0.0, HA v2026.6.1, apexcharts-card v1.4.0 (no `entity: url` support — uses `data_generator`)
- Dashboard in YAML mode (file-based)
- Retailer config editor at `http://192.168.50.100:1880/endpoint/api/retailer-config` — stores to `/share/retailer_config.csv`

## Progress

### Done
- **PEA in Monthly Cost Summary** — per-day PEA stored in dailySummary; monthly report shows `[PEA X.XXXX]` next to FlowPower cost, using the PEA from that billing period (not a single global value)
- **PEA per billing period** — calculate_costs now computes PEA for each billing period (4th to 3rd), stores in `billingPea` object; dailySummary uses `billingPea[day.substring(0,7)]` to get correct PEA per month; Total row shows average PEA across all months
- Full Node-RED flow generating 7+ HA sensors via REST API, daily summary/detail HTML reports, and 5-min detail for all fixed_tou + hybrid retailers
- Export rate logic updated for all fixed_tou retailers: priority = super peak > peak > off-peak > shoulder (default)
- Super peak daily export limit via `sp_limit` config column; Globird capped at 15 kWh/day at $0.15, excess falls back to peak $0.05
- FlowPower (hybrid) added to 5-min detail with adjImport/adjExport logic matching daily summary
- 5-min detail TOTAL row shows Import $, Export $, DSC, Rebate, Net $ values
- Sticky headers fixed: removed `overflow:hidden` from day-sections and nested overflow wrappers, single report div scroll context
- Debug nodes removed, `node.warn()` calls commented out in all function nodes
- Reports sorted by date descending (latest first): Daily Summary, Daily Detail, both 5-min detail endpoints
- Daily Detail date format changed from MM-DD to DD-MM
- All nodes grouped under "Energy Retailer Comparison" group node
- Stale sensor auto-cleanup: tracks previous retailer sensor IDs in flow, sends DELETE to HA when retailers removed
- GitHub repo: https://github.com/HiltonBailey/ElectricityComparitor
- Data stored in `flow.set('fiveMinDetail', ...)` instead of HA sensor attribute (avoids ~5-6MB sensor burden)
- Dashboard YAML simplified: 5-min card via `<iframe>` using `custom:html-card` at 400px height
- CORS handling for HTTP endpoints
- Fixed duplicate kWh accumulation — now 1x per interval; fixes TOTAL Imp/Exp kWh values
- Monthly Cost Summary report — HA sensor `sensor.retailer_monthly_summary`, groups dailySummary by month, shows season column
- Daily Detail larger font (13px) and sticky first column — Date column freezes when scrolling horizontally
- DSC/Net column font fixed — all sub-header and body cells changed from 9px to 11px
- Billing filter removed from dailyData loop — monthly summary uses ALL CSV data from start of file
- BP (billing month) column added to both Daily Summary and Daily Detail reports
- Dashboard saved to HA via WebSocket API (`lovelace/config/save` with monthly card)
- **AEMO price and network cost columns** added to 5-min detail — `csvByDate` stores `aemoPrice` (from `row[11]`) and `nwCost` (from `getNetworkCost`)
- **Deploy via `PUT /flow/:id`** — `deploy.sh` updates only `tab_energy_retailer_comparison` tab; no longer touches other flows
- **Versioning via `VERSION` file** — injected at deploy time into group label and sensor attributes; `##GIT_VERSION##` placeholders replaced dynamically
- **5 apexcharts-card visualizations** in `dashboard-charts.yaml` (view `energy-retailer-charts`)
- **`daily_data` JSON attribute** on `sensor.retailer_daily_summary` — per-day net cost per retailer, cumulative cost, import/export kWh
- **`chart_data` JSON attribute** on `sensor.retailer_five_min_detail` — last 2 days of 5-min intervals per retailer
- **Charts fixed for v1.4.0 compat**: `title` moved to `header.title`, `stack` replaced with `stacked: true`
- **Fixed `billingMonth` hoisting bug** — moved to top-level scope
- **Fixed `build_five_min_detail`** — replaced IIFE with direct variable build, same hoisting issue
- **CovaU SolarMax aligned to EME plan COV1053199MRE1**:
  - DSC: $1.1818, Peak: $0.5581, Shoulder/Off-peak: $0.2547, Peak window: 17-21 MON-SUN
  - Free import 11-14: $0/kWh with 24kWh/day cap; excess at shoulder rate
  - EV Off-Peak: 00:00-05:59 at $0.15/kWh (config columns `ev_s`, `ev_e`, `ev_pk`)
  - FIT: 18c/kWh super peak 17-21, 5c/kWh all other times
- **Seasonal Report** — HA sensor `sensor.retailer_seasonal_report` with Yearly/Monthly/Summer/Autumn/Winter/Spring totals per retailer; cheapest → green, Best column
- **Energy Retailer Dashboard** at `energy-retailer-dashboard` with 3 views: Costs (path `testing`), Charts (path `energy-retailer-charts`), Config (path `energy-retailer-config`)
- **Retailer config editor** — web-based editor with editable table, dropdown model select, hover tooltips, delete checkboxes, localStorage revert backup
- **Config editor field widths** — min-width 60px on all cells, horizontal scroll wrapper for 31-column table
- **Per-period FIT windows** — each TOU period (off/sh/pk/sp) has configurable FIT start/end (`off_fit_s`, `off_fit_e`, `sh_fit_s`, `sh_fit_e`, `pk_fit_s`, `pk_fit_e`, `sp_fit_s`, `sp_fit_e`)
- **Fixed config editor crash** — missing comma in helpText object + undefined `off_s`/`off_e`/`off_pk` variables in save handler
- **Import $ and Export $ on 5-min detail TOTAL line** — summary shows `Import $: $X.XX | Export $: $X.XX | DSC: $X.XX | Rebate: $X.XX | Net: $X.XX`
- **TOU period label: EV Offpeak → Off** — all import rates for TOU retailers show Off/Shoulder/Peak only
- **Fixed NaN from division by zero** — CovaU off_limit logic: guarded `totalImport > 0` before dividing
- **5-min detail / daily detail rounding alignment** — TOTAL row accumulates from raw unrounded values instead of summing rounded intervals; totals now match daily detail report
- **Billing day configurable** — `billing_day` CSV column (32nd field, default 4) replaces hardcoded `4`; editable via config editor; propagated via `flow.get('billingDay')` to all billing logic (calculate_costs, daily summary, daily detail)

### In Progress
- (none)

### Blocked
- (none)

## Key Decisions
- Sticky headers: removed nested overflow containers, single scrollable report div with `position:sticky;top:0` on header
- Stale sensor cleanup: cached previous sensor IDs in flow variable, DELETE via second function output + separate HTTP DELETE node
- FlowPower 5-min detail: tracks cumulative import/export kWh and outside-window export per day, applies hybrid adjImport/adjExport at TOTAL row
- TOTAL row uses raw unrounded accumulators (not rounded interval sums) for accurate totals matching daily detail
- Deploy strategy: `PUT /flow/:id` via `deploy.sh` — safer, faster, single-tab only
- Versioning: plain `VERSION` file read by deploy script, injected as `v{version}` into group name and sensor attributes
- Charts use `data_generator` (not `entity: url`) for apexcharts-card v1.4.0 — data sourced from HA sensor JSON attributes
- Dashboard YAML split: `dashboard.yaml` = "Energy Retailer Costs" view; `dashboard-charts.yaml` = "Energy Retailer Charts" view
- Config extended with `ev_s`, `ev_e`, `ev_pk`, `off_limit` columns — 4th TOU period (EV Off-Peak) and free import daily cap, only populated for CovaU
- Free import 24kWh/day cap tracked per day per retailer as `freeUsage`, reset daily; excess charged at shoulder rate
- Per-period FIT windows allow different FIT windows from TOU windows for each period
- Retailer config stored in `/share/retailer_config.csv`; flow reads on each 5-min cycle; template node as fallback

## Next Steps
- (none — all items verified and complete)

## Critical Context
- Node-RED httpNodeRoot = `/endpoint` — all HTTP input nodes accessed via `/endpoint/` prefix
- Flow variable `fiveMinDetail` stored by `calculate_costs`, read by both HTTP handlers
- `expOutside45c` tracked as positive in 5-min detail, then subtracted for adjImport/adjExport
- Node-RED admin API accessible at port 1880 with nginx basic auth (`stilgar` / `Ha0118021669`) — no Bearer token needed for admin API
- Token: `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiIyZGVkZTMwMWI1Mzc0NmJhOTNhOTM2YzM4N2FmOGU0ZSIsImlhdCI6MTc4MjA4NTA5OSwiZXhwIjoyMDk3NDQ1MDk5fQ.ovX2gmYaIlLbxTcw54DngXne9K8HbDFgl_Sb3afjIcU` (HA long-lived access token)
- Dashboard path `testing` for Energy Retailer Costs; `energy-retailer-charts` for Energy Retailer Charts; `energy-retailer-config` for Energy Retailer Configuration
- **Energy Retailer Dashboard**: separate dashboard at `energy-retailer-dashboard` with 3 views
- AEMO price available as `row[11]` in CSV parsing loops; network cost from `getNetworkCost(dt)` function
- `daily_data` format: `[{"date":"2026-06-01","FlowPower":1.23,"FlowPower_cum":1.23,"import_kwh":8.5,"export_kwh":3.2,"cheapest":"FlowPower",...}]`
- `chart_data` format: `{"FlowPower_2026-06-12":[{"t":"00:00","ik":0.123,"ap":45.23,"nw":0.0515},...],...}`
- apexcharts-card v1.4.0 — no `entity: url`; all series use `data_generator` reading from entity attributes
- CSV header: 32 columns: `name,model,dsc,sub,off_pk,sh_pk,pk_pk,off_fit,sh_fit,pk_fit,sp_fit,sp_limit,off_s,off_e,pk_s,pk_e,sp_s,sp_e,off_fit_s,off_fit_e,sh_fit_s,sh_fit_e,pk_fit_s,pk_fit_e,sp_fit_s,sp_fit_e,fixed_export,ev_s,ev_e,ev_pk,off_limit,billing_day`

## Relevant Files
- `node_red_flow.json`: Complete flow — 40+ nodes + group + config editor endpoints
- `dashboard.yaml`: HA dashboard YAML — "Energy Retailer Costs" view (path: `testing`)
- `dashboard-charts.yaml`: HA dashboard YAML — "Energy Retailer Charts" view (path: `energy-retailer-charts`)
- `deploy.sh`: Deploy script — `PUT /flow/tab_energy_retailer_comparison` with basic auth, version injection, config seed
- `VERSION`: Current version (v2.5)
- `AGENTS.md`: This file — session continuity for opencode agents
- `DEPLOY.md`: Full instructions for updating HA Dashboards and Node-RED without affecting other tabs

## Updating Without Breaking Other Tabs
See `DEPLOY.md` for the complete guide. In summary:
- **HA Dashboard**: Fetch full config via WebSocket `lovelace/config` (with `url_path: 'power-dashboard'`), replace only views matching paths `testing` and `energy-retailer-charts`, save via WebSocket `lovelace/config/save`. Never use REST endpoints — they replace the entire config.
- **Node-RED**: Run `bash deploy.sh` — it extracts only `tab_energy_retailer_comparison` from `node_red_flow.json` and sends `PUT /flow/:tab_id` to the Node-RED admin API, leaving all other tabs untouched.

## Retailer Config Editor
A web-based editor for retailer rates and TOU periods is available at `http://192.168.50.100:1880/endpoint/api/retailer-config`. Changes are saved to `/share/retailer_config.csv` on HA and take effect on the next 5-min cycle. The editor is preferred over editing the template node directly — `deploy.sh` auto-seeds the file on first deploy.
