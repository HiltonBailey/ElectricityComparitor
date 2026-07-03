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
- **JSON escaping rule**: Never use `'\n'` inside function node code for split/join/replace — JSON interprets `\n` as a literal newline, breaking JS string literals across lines. Always use `String.fromCharCode(10)` instead (e.g. `split(String.fromCharCode(10))`). This applies to ALL new or amended function node code. Existing nodes using `\\n` (double-escaped) in the JSON file are fine, but `String.fromCharCode(10)` is the preferred pattern as it is immune to JSON re-encoding corruption.

## Progress

### Done

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
- **HA history gap filling**: queries `sensor.energy_import_meter_offpeak/shoulder/peak` and `sensor.energy_export_meter_offpeak` at hour boundaries within gaps; uses real cumulative values as interpolation anchors; falls back to linear interpolation when HA data unavailable; process_ha_and_fill collects 4 HTTP responses via flow variables before processing

## Next Steps
- (none — all items verified and complete)

### Fixed

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
- `node_red_flow.json`: Complete flow — 55 nodes + group + config editor endpoints
- `dashboard.yaml`: HA dashboard YAML — "Energy Retailer Costs" view (path: `testing`)
- `dashboard-charts.yaml`: HA dashboard YAML — "Energy Retailer Charts" view (path: `energy-retailer-charts`)
- `deploy.sh`: Deploy script — `PUT /flow/tab_energy_retailer_comparison` with basic auth, version injection, config seed
- `VERSION`: Current version (v2.11)
- `AGENTS.md`: This file — session continuity for opencode agents
- `DEPLOY.md`: Full instructions for updating HA Dashboards and Node-RED without affecting other tabs

## Updating Without Breaking Other Tabs
See `DEPLOY.md` for the complete guide. In summary:
- **HA Dashboard**: Fetch full config via WebSocket `lovelace/config` (with `url_path: 'power-dashboard'`), replace only views matching paths `testing` and `energy-retailer-charts`, save via WebSocket `lovelace/config/save`. Never use REST endpoints — they replace the entire config.
- **Node-RED**: Run `bash deploy.sh` — it extracts only `tab_energy_retailer_comparison` from `node_red_flow.json` and sends `PUT /flow/:tab_id` to the Node-RED admin API, leaving all other tabs untouched.

## Retailer Config Editor
A web-based editor for retailer rates and TOU periods is available at `http://192.168.50.100:1880/endpoint/api/retailer-editor`. Changes are saved to `/share/retailer_config.csv` on HA and take effect on the next 5-min cycle. The editor is preferred over editing the template node directly — `deploy.sh` auto-seeds the file on first deploy.
