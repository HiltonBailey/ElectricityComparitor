# Project: ElectricityRetailerComparison

## Goal
Multi-retailer electricity cost comparison system comparing FlowPower against Origin/Globird/CovaU/Amber, integrated as a dynamic Node-RED flow with HA dashboard, deployed selectively via `PUT /flow/:id`.

## Constraints & Preferences
- Three pricing models: FlowPower (hybrid), Amber (variable), Fixed TOU (Origin/Globird/CovaU)
- Script tags stripped by `custom:html-card` — `<iframe>` used instead for the 5-min interactive report
- Node-RED `httpNodeRoot` = `/endpoint`; HTTP nodes served under `/endpoint/`
- HA at http://192.168.50.100:8123, Node-RED admin API on port 1880 (behind nginx with basic auth `stilgar` / `Ha0118021669`)
- HA CSV source: `/share/file_notifications/5minelec.csv` (14 cols, cumulative energy columns)
- Billing cycle: 4th of month to 3rd of next month
- Deploy via `bash deploy.sh` — uses `PUT /flow/tab_energy_retailer_comparison` (does not touch other tabs)
- Node-RED v5.0.0, HA v2026.6.1, apexcharts-card v1.4.0 (no `entity: url` support — uses `data_generator`)
- Dashboard in YAML mode (file-based)

## Progress

### Done
- Full Node-RED flow generating 7+ HA sensors via REST API, daily summary/detail HTML reports, and 5-min detail for all fixed_tou + hybrid retailers
- Export rate logic updated for all fixed_tou retailers: priority = super peak > peak > off-peak > shoulder (default)
- Super peak daily export limit via `sp_limit` config column; Globird capped at 15 kWh/day at $0.15, excess falls back to peak $0.05
- FlowPower (hybrid) added to 5-min detail with adjImport/adjExport logic matching daily summary
- 5-min detail TOTAL row shows Imp kWh, Exp kWh, Imp $, Exp $, Net $ values
- Sticky headers fixed: removed `overflow:hidden` from day-sections and nested overflow wrappers, single report div scroll context
- Debug nodes removed, `node.warn()` calls commented out in all function nodes
- Reports sorted by date descending (latest first): Daily Summary, Daily Detail, both 5-min detail endpoints
- Daily Detail date format changed from MM-DD to DD-MM
- All nodes grouped under "Energy Retailer Comparison" group node
- Stale sensor auto-cleanup: tracks previous retailer sensor IDs in flow, sends DELETE to HA when retailers removed
- GitHub repo created and pushed: https://github.com/HiltonBailey/ElectricityComparitor
- Data stored in `flow.set('fiveMinDetail', ...)` instead of HA sensor attribute (avoids ~5-6MB sensor burden)
- Dashboard YAML simplified: 5-min card via `<iframe>` using `custom:html-card` at 400px height
- CORS handling for HTTP endpoints
- Fixed duplicate kWh accumulation — now 1x per interval; fixes TOTAL Imp/Exp kWh values
- Monthly Cost Summary report — new HA sensor `sensor.retailer_monthly_summary`, groups dailySummary by month, shows season column
- Daily Detail larger font (13px) and sticky first column — Date column freezes when scrolling horizontally
- DSC/Net column font fixed — all sub-header and body cells changed from 9px to 11px
- Billing filter removed from dailyData loop — monthly summary uses ALL CSV data from start of file
- BP (billing month) column added to both Daily Summary and Daily Detail reports
- Dashboard saved to HA via WebSocket API (`lovelace/config/save` with monthly card)
- **AEMO price and network cost columns** added to 5-min detail — `csvByDate` stores `aemoPrice` (from `row[11]`) and `nwCost` (from `getNetworkCost`); both `http_handler` and `http_page_handler` render the new columns after Exp $/kWh, before Imp $
- **Deploy via `PUT /flow/:id`** — new `deploy.sh` script updates only `tab_energy_retailer_comparison` tab; no longer touches other flows
- **Versioning via `VERSION` file** (currently v1.1) — injected at deploy time into group label and sensor attributes; `##GIT_VERSION##` placeholders replaced dynamically
- **5 apexcharts-card visualizations** added to `dashboard-charts.yaml` (separate view `energy-retailer-charts`):
  - Daily Cost Comparison — column chart, 5 retailers, 14d span
  - Billing Month Cumulative — line chart, 5 retailers, 31d span
  - Daily Import / Export kWh — stacked column, 14d span
  - 5-Min Import Profile — area chart, FlowPower, today
  - AEMO Wholesale Price — area chart, today
- **`daily_data` JSON attribute** on `sensor.retailer_daily_summary` — per-day net cost per retailer, cumulative cost, import/export kWh
- **`chart_data` JSON attribute** on `sensor.retailer_five_min_detail` — last 2 days of 5-min intervals per retailer (impKwh, aemoPrice, nwCost)
- **`GET /api/5min-detail?format=chart`** — returns structured JSON for 5-min interval data
- **`GET /api/5min-detail?type=daily`** — returns daily chart data array
- Charts fixed for v1.4.0 compat: `title` moved to `header.title`, `stack` replaced with `stacked: true`
- **Fixed `billingMonth` hoisting bug** — `function billingMonth()` defined inside `forEach` arrow function body not hoisted in Node.js → moved to top-level scope
- **Fixed `build_five_min_detail`** — replaced IIFE with direct variable build, same hoisting issue with arrow functions
- **Both `daily_data` (179 days) and `chart_data` (4 retailers × 2 days) now populate** on HA sensors after deploying v1.9 with these fixes
- **Dashboards re-saved** via HA WebSocket `lovelace/config/save` — both `testing` and `energy-retailer-charts` views have all cards
- **CovaU SolarMax aligned to EME plan COV1053199MRE1** — fetched from `api.energymadeeasy.gov.au`:
  - DSC: $1.30 → $1.1818 (118.18c/day)
  - Peak rate: $0.6139 → $0.5581 (55.81c/kWh)
  - Shoulder/off-peak rate: $0.2802 → $0.2547 (25.47c/kWh)
  - Peak window: 17-21 (Weekend exemption removed — peak applies MON-SUN)
  - Free import 11-14: $0/kWh with **24kWh/day cap** (was unlimited); excess charged at shoulder $0.2547
  - Added **EV Off-Peak** TOU period: 00:00-05:59 at $0.15/kWh (new config columns `ev_s`, `ev_e`, `ev_pk`)
  - FIT unchanged: 18c/kWh super peak 17-21, 5c/kWh all other times ✅
- **Seasonal Report** — new HA sensor `sensor.retailer_seasonal_report` with Yearly/Monthly/Summer/Autumn/Winter/Spring totals per retailer; HTML table + JSON `data` attribute

### In Progress
- (none)

### Blocked
- (none)

## Key Decisions
- Sticky headers: removed nested overflow containers, single scrollable report div with `position:sticky;top:0` on header — reliable in iframes
- Stale sensor cleanup: cached previous sensor IDs in flow variable, DELETE via second function output + separate HTTP DELETE node
- FlowPower 5-min detail: tracks cumulative import/export kWh and outside-window export per day, applies hybrid adjImport/adjExport at TOTAL row
- TOTAL row uses stored interval data (not live sum) so adjusted hybrid values display correctly
- Deploy strategy: `PUT /flow/:id` via `deploy.sh` — safer, faster, single-tab only
- Versioning: plain `VERSION` file read by deploy script, injected as `v{version}` into group name and sensor attributes
- Charts use `data_generator` (not `entity: url`) for apexcharts-card v1.4.0 — data sourced from HA sensor JSON attributes (`daily_data`, `chart_data`)
- Dashboard YAML split: `dashboard.yaml` = original "Energy Retailer Costs" view; `dashboard-charts.yaml` = "Energy Retailer Charts" view
- Config extended with `ev_s`, `ev_e`, `ev_pk`, `off_limit` columns — 4th TOU period (EV Off-Peak) and free import daily cap, only populated for CovaU
- Free import 24kWh/day cap tracked per day per retailer as `freeUsage`, reset daily; excess charged at shoulder rate

## Next Steps
- Wait for next 5-min cycle to trigger; verify CovaU costs recalculate with new EME-aligned rates
- Check 5-min detail report text to confirm EV Offpeak period label appears for CovaU 00:00-05:59 intervals
- Verify seasonal report coloring fix (cheapest → green) and Best column shows on reload
- Bump VERSION once all changes verified

## Critical Context
- Node-RED httpNodeRoot = `/endpoint` — all HTTP input nodes accessed via `/endpoint/` prefix
- Flow variable `fiveMinDetail` stored by `calculate_costs`, read by both HTTP handlers
- `expOutside45c` tracked as positive in 5-min detail, then subtracted for adjImport/adjExport (matches daily calc which tracks as negative and adds)
- Node-RED admin API accessible at port 1880 with nginx basic auth (`stilgar` / `Ha0118021669`) — no Bearer token needed for admin API
- Token: `eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiI0NTQ1YjAwYWEwYTQ0YjRiYmZlN2FkMjIwMDlkMTgzZiIsImlhdCI6MTc4MTA3MjcwMCwiZXhwIjoyMDk2NDMyNzAwfQ.majW8v0wjDid-9rKMExUhG18xcYISsaijzxTaaYDq4U` (HA long-lived access token)
- Dashboard path `testing` for Energy Retailer Costs; `energy-retailer-charts` for Energy Retailer Charts
- AEMO price available as `row[11]` in CSV parsing loops; network cost from `getNetworkCost(dt)` function
- `daily_data` format: `[{"date":"2026-06-01","FlowPower":1.23,"FlowPower_cum":1.23,"import_kwh":8.5,"export_kwh":3.2,"cheapest":"FlowPower",...}]`
- `chart_data` format: `{"FlowPower_2026-06-12":[{"t":"00:00","ik":0.123,"ap":45.23,"nw":0.0515},...],...}`
- apexcharts-card v1.4.0 — no `entity: url`; all series use `data_generator` reading from entity attributes

## Relevant Files
- `node_red_flow.json`: Complete upstream flow — 28 nodes + group
- `dashboard.yaml`: HA dashboard YAML — "Energy Retailer Costs" view (path: `testing`)
- `dashboard-charts.yaml`: HA dashboard YAML — "Energy Retailer Charts" view (path: `energy-retailer-charts`)
- `deploy.sh`: Deploy script — `PUT /flow/tab_energy_retailer_comparison` with basic auth, version injection
- `VERSION`: Current version (v1.9 — not bumped until EME changes verified)
- `AGENTS.md`: This file — session continuity for opencode agents
