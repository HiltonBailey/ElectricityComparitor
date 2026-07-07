# Project: ElectricityRetailerComparison

## Goal
Multi-retailer electricity cost comparison system comparing FlowPower against Origin/Globird/CovaU/Amber, integrated as a dynamic Node-RED flow with HA dashboard, deployed selectively via `PUT /flow/:id`.

## Constraints & Preferences
- Three pricing models: FlowPower (hybrid), Amber (variable), Fixed TOU (Origin/Globird/CovaU)
- Script tags stripped by `custom:html-card` — `<iframe>` used instead for the 5-min interactive report
- Node-RED `httpNodeRoot` = `/endpoint`; HTTP nodes served under `/endpoint/`
- HA at http://192.168.50.100:8123, Node-RED admin API on port 1880 (behind nginx with basic auth `stilgar` / `Ha0118021669`)
- **No references to `5minelec.csv`**: HA data logger's `5minelec.csv` is NOT used by the flow. The working file is `5minelecNEW.csv` only. Copy mechanism (previously `inject_copy_csv` / HTTP GET `/endpoint/api/copy-csv`) was removed. Historical data can be imported from `newseed.csv` via `GET /endpoint/api/import-seed`. **`newseed.csv` is a critical backup — never delete or modify it.** If `5minelecNEW.csv` is empty or missing on startup, `detect_gaps_for_ha` generates 12 midnight seed rows (cum=0 for 11 days + today) and gap-filling fills them from HA history. Older seed-csv endpoint (`/endpoint/api/seed-csv`) was removed in v2.33 — it was hazardous as it overwrote the file with 12 bootstrap rows, destroying historical data.
- Billing cycle: configurable via `billing_day` column in retailer_config.csv (default 4, 4th→3rd)
- Deploy via `bash deploy.sh` — uses `PUT /flow/tab_energy_retailer_comparison` (does not touch other tabs)
- Node-RED v5.0.0, HA v2026.6.1, apexcharts-card v1.4.0 (no `entity: url` support — uses `data_generator`)
- Dashboard in YAML mode (file-based)
- Retailer config editor at `http://192.168.50.100:1880/endpoint/api/retailer-config` — stores to `/share/retailer_config.csv`
- **JSON escaping rule**: Never use `'\n'` inside function node code for split/join/replace — JSON interprets `\n` as a literal newline, breaking JS string literals across lines. Always use `String.fromCharCode(10)` instead (e.g. `split(String.fromCharCode(10))`). This applies to ALL new or amended function node code. Existing nodes using `\\n` (double-escaped) in the JSON file are fine, but `String.fromCharCode(10)` is the preferred pattern as it is immune to JSON re-encoding corruption.

## Progress

### Done
- **`5minelec.csv` eliminated**: Removed `read_original_for_copy` and `compare_csv_read_orig` nodes — flow never reads/writes `5minelec.csv`. Copy mechanism replaced with `build_csv_from_ha` → `http_get_ha_states` → `parse_csv_from_ha` chain that queries HA `/api/states` and writes directly to `5minelecNEW.csv`.
- **Midnight seed row**: Copy now includes a midnight row (cum=0) so gap-filling detects a gap from 00:00 to current time, covering the full day.
- **HA history `Z` suffix fix**: HA's `/api/history/period/` requires `Z` suffix (not `+00:00`). The flow's `.toISOString()` already produces `Z` but Python testing revealed the root cause of empty history results was the `+00:00` format.
- **HA history gap-filling now functional**: CSV seeded with midnight+current row → gap-filling fills 159 rows from HA history with correct cumulative values from all 7 period sensors.
- **Export fix (v2.29)**: Added 3 new HTTP nodes for export_shoulder/peak/superpeak sensors. `process_ha_and_fill` now sums all 4 export period sensors for total export.
- **10-day query clip**: HA history queries limited to last 10 days where short-term recorder has reliable 5-min data.
- **NR v5 auth fix**: All builder functions set `msg.headers: { 'Authorization': authHeader }` instead of relying on node-set headers.
- **5-min detail sensor 404 fixed**: Uncommented `flow.set('fiveMinDetail', ...)` in `calculate_costs` and added `msg.headers` to `build_five_min_detail`.
- **Copy chain removed**: `build_csv_from_ha`, `http_get_ha_states`, `parse_csv_from_ha`, `write_new_csv`, `copy_done_resp`, `inject_copy_csv`, `http_copy_csv_endpoint` nodes removed. Bootstrap moved into `detect_gaps_for_ha` — generates midnight seed rows if CSV is empty.
- **11-day query clip**: Increased from 10 to 11 days to match full HA short-term recorder window.
- **`msg.payload` bootstrap fix**: `detect_gaps_for_ha` now sets `msg.payload = csvData` after generating midnight seeds, so `calculate_costs` receives the seeded CSV data instead of the original empty payload.
- **Seed CSV HTTP endpoint**: Added `/endpoint/api/seed-csv` (HTTP GET) for emergency CSV rebuild — generates 12 bootstrap midnight rows and writes directly to `5minelecNEW.csv` via dedicated `write_seed_csv` file-out node.
- **Broker chain fixed (post-gap-fill)**: Removed erroneous `msg.payload = csvContent` in `process_ha_and_fill` that caused `ReferenceError: csvContent is not defined`, halting the gap-fill `node.send()` and preventing `calculate_costs` from receiving filled data.
- **Full gap-fill recovery demonstrated**: 0→3068 rows (11 days of 5-min data) from all 7 HA history sensors, costs calculated for all 4 retailers, 6 HA sensor entities populated.
- **Seed CSV endpoint removed (v2.33)**: `/endpoint/api/seed-csv` deleted — it overwrote the CSV with 12 bootstrap rows, destroying historical data. Replaced by `/endpoint/api/import-seed` which reads `newseed.csv` and merges with current data.
- **Import seed endpoint added (v2.33)**: `GET /endpoint/api/import-seed` — reads `/share/file_notifications/newseed.csv`, merges rows with current `5minelecNEW.csv` (keeping current rows for overlapping timestamps), writes back. Successfully recovered 198 days (Dec 19 → Jul 4) with 57,578 gap-filled rows from 54,445 seed + 3,133 current rows.
- **6 new nodes**: `import_seed_http_in`, `import_read_seed_csv`, `import_seed_store`, `import_read_current_csv`, `import_seed_merge` (chains: HTTP→file-in→store→file-in→merge→write+HTPP-resp), `import_seed_http_resp`.
- **4 nodes removed (seed-csv)**: `http_seed_csv_endpoint`, `seed_csv_func`, `write_seed_csv`, `http_seed_csv_resp`.
- **FlowPower PEA fix (v2.34)**: `flowRate` was a global const using `billingPea[getBillingKey(today)]` only — all historical dates used today's PEA. Changed to a `getFlowRate(dateStr)` function that computes the rate per-day: `flowPowerRate + (billingPea[getBillingKey(dateStr)] || pea || 0)`. Applied to all 6 hybrid usages: per-interval import, daily summary import/net, 5-min detail impRate and TOTAL impCost. June Import $ dropped from $9.89→$8.14 (PEA −0.074) while July correctly stayed at 0.3230.
- **Timestamp convention (period-ending)**: All CSV timestamps use `HH:MM:59` format representing the end of the 5-min billing period. Code that reads timestamps should not assume `:00` second format — treat the timestamp as period-ending, not period-starting.
- **Bootstrap safety fix**: `detect_gaps_for_ha` bootstrap (which regenerates CSV with 12 midnight seed rows) now checks if the file actually exists via `fs.existsSync()` before running. If the file exists but read returned empty/error, the function returns `null` (skips the cycle) instead of overwriting historical data. This prevents catastrophic data loss when the CSV read fails transiently (e.g., race condition with concurrent write).
- **Gap-fill timestamp fix (v2.35)**: `parseLocal` in both `detect_gaps_for_ha` and `process_ha_and_fill` previously used `timePart.substring(0,5) + ':00'`, truncating seconds. This caused `fromTs` to be 59 seconds early, making generated timestamps 1 min off (e.g., `23:58:59` instead of `23:59:59`). Fixed by using the full `HH:MM:SS` string. Also fixed `timeStr` to output `HH:MM:SS` and removed the hacky `-1000ms` offset — gap-fill now generates correct `:59` timestamps directly from `rowTs`.
- **Trailing gap `toNow` fix (v2.35)**: `toNow` was rounded to the START of the current 5-min slot (`:00`), missing 4:59 of fillable data per cycle. Changed to `floor(...) * 5min + 5min - 1s` so `toNow` is the end of the last complete slot (`:59`), matching the CSV timestamp convention.
- **14-day gap age filter (v2.36)**: `detect_gaps_for_ha` now filters out gaps older than 14 days before triggering HA queries — prevents processing massive un-fillable gaps (e.g., the 186-day seed data gap). Filter applied BEFORE `flow.set('haGapInfo', ...)` so debug endpoint shows only filtered gaps.
- **Full-range HA queries (v2.36)**: `prepare_ha_queries` no longer clips to 11 days — queries the full gap range. HA returns whatever data is available (short-term or long-term). Tracks earliest gap timestamp via `haLongTermStart` flow variable.
- **Gap-fill logging (v2.36)**: `process_ha_and_fill` now appends gap-fill activity to `/share/file_notifications/elecdatalog.txt` via `fs.appendFileSync` after each cycle. Logs: timestamp, data source, gap timestamps (from/to), gap_min, trailing/cross_day flags, row counts. Readable via `GET /endpoint/api/log`.
- **Log HTTP endpoint (v2.36)**: Added `GET /endpoint/api/log` — reads `/share/file_notifications/elecdatalog.txt` and returns JSON with `content` and `lines` fields. 4 new nodes: `logger_http_in`, `logger_read_file`, `logger_func`, `logger_http_resp`.
- **Calculate costs race fix (v2.37)**: `detect_gaps_for_ha` previously sent original csvData to `calculate_costs` (wire[0]) while also triggering the gap-fill chain (wire[1]). `calculate_costs` would write the ORIGINAL (pre-fill) data to the file via output 3 → `write_fixed_csv` BEFORE `process_ha_and_fill` could merge the HA-filled rows. If the gap-fill chained failed or took too long, the file was overwritten with un-filled data, causing rows to be lost. Fixed by changing `return [msg, msg]` to `return [null, msg, msg]` — when gaps exist, wire[0] is `null` so `calculate_costs` is not triggered until `process_ha_and_fill` completes and sends the merged data. When no gaps exist, `return [msg, null]` continues to work as before.
- **NR v5 msg property override warnings fixed (v2.37)**: 9 HTTP request nodes had hardcoded `Authorization: Bearer <token>` in their node `headers` config, but all upstream builder functions also set `msg.headers` dynamically via `global.get('ha_token')`. In NR v5, configured properties win — `msg.headers` was silently discarded and a warning fired each cycle. Fixed by clearing `headers: []` on all 9 nodes (the builders already provide the correct headers via msg).
- **Initial clean reseed (v2.38)**: Flow reverted to commit 22a60c1. CSV cleared and reseeded from newseed.csv (56,834 rows, 19 Dec 2025 onward, HA-sourced cumulative data). Temp files cleaned. Blank line filter added to both `process_ha_and_fill` and `calculate_costs` csvLines splits.
- **Retailer editor enhanced (v2.38)**: Added blank editable row at bottom for adding new retailers. Globird Four4Free added as 7th retailer (fixed_tou, $1.30174 DSC, $0.58152 pk 16-23, $0.36385 sh, first 50kWh off-peak free, $0.08 FIT 16-23).
- **`prevOff` day-boundary reset fixed (v2.38)**: `csvByDate` building loop in `calculate_costs` incorrectly reset `prevExport/prevOff/prevSh/prevPk` to 0 at each day change, causing the first 5-min interval of each day to report the entire cumulative value as a single interval's import. Removed the reset — prev values now carry across day boundaries (matching the dailySummary loop which correctly kept prev values). Also removed a dead duplicate `if (rowDateStr !== prevDay)` block.
- **`deploy.sh` updated (v2.38)**: Changed `rowCount=6` to `rowCount=7`. Added Globird Four4Free seed data at row 7. Redeploys now preserve all 7 retailers.
- **`glo_rebate` config column added (v2.39)**: New column `glo_rebate` in retailer_config.csv controls the $1 evening rebate (applied if evening import < 0.09 kWh). Globird VPP gets `glo_rebate=1`; all other retailers (including Globird Four4Free) get `0`. The code now checks `r.glo_rebate > 0` instead of `r.name.indexOf('Globird')`. Updated all config headers: `retailer_config_template`, `save_config_handler_1`, `serve_config_page_74a84a79`, and `deploy.sh`.

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
- **HA history gap filling**: queries `sensor.energy_import_meter_offpeak/shoulder/peak` and `sensor.energy_export_meter_offpeak/shoulder/peak/superpeak` across gap boundaries; uses real cumulative values as interpolation anchors; falls back to linear interpolation when HA data unavailable; `process_ha_and_fill` collects 7 HTTP responses via flow variables before processing
- **Export sums all 4 period sensors**: `totalExp = vExpOff + vExpSh + vExpPk + vExpSp` in `makeHaRow` — single export cumulative sensor doesn't exist in HA; must reconstruct from 4 period sensors

## Next Steps
- Verify 5-min cycles continue running cleanly with 57k+ row CSV
- Verify `elecdatalog.txt` appears after first gap-fill cycle

## Key Decisions (cont.)
- **Seed CSV endpoint removed**: `/endpoint/api/seed-csv` was dangerous — it overwrote `5minelecNEW.csv` with 12 bootstrap rows, destroying any historical data. Replaced by `/endpoint/api/import-seed` which reads `newseed.csv` (external historical snapshot) and merges it with current data without data loss.
- **Import endpoint chain**: HTTP→file-in (newseed.csv)→store→file-in (5minelecNEW.csv)→merge→write+response. Uses `write_fixed_csv` (existing) for the final write.
- **Merge strategy**: current CSV rows take priority for overlapping timestamps; seed rows fill in missing older dates only.

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
- `node_red_flow.json`: Complete flow — 72 nodes + group + config editor endpoints + import seed endpoint
- `dashboard.yaml`: HA dashboard YAML — "Energy Retailer Costs" view (path: `testing`)
- `dashboard-charts.yaml`: HA dashboard YAML — "Energy Retailer Charts" view (path: `energy-retailer-charts`)
- `deploy.sh`: Deploy script — `PUT /flow/tab_energy_retailer_comparison` with basic auth, version injection, config seed
- `VERSION`: Current version (v2.35)
- `AGENTS.md`: This file — session continuity for opencode agents
- `DEPLOY.md`: Full instructions for updating HA Dashboards and Node-RED without affecting other tabs

## Updating Without Breaking Other Tabs
See `DEPLOY.md` for the complete guide. In summary:
- **HA Dashboard**: Fetch full config via WebSocket `lovelace/config` (with `url_path: 'power-dashboard'`), replace only views matching paths `testing` and `energy-retailer-charts`, save via WebSocket `lovelace/config/save`. Never use REST endpoints — they replace the entire config.
- **Node-RED**: Run `bash deploy.sh` — it extracts only `tab_energy_retailer_comparison` from `node_red_flow.json` and sends `PUT /flow/:tab_id` to the Node-RED admin API, leaving all other tabs untouched.

## Retailer Config Editor
A web-based editor for retailer rates and TOU periods is available at `http://192.168.50.100:1880/endpoint/api/retailer-editor`. Changes are saved to `/share/retailer_config.csv` on HA and take effect on the next 5-min cycle. The editor is preferred over editing the template node directly — `deploy.sh` auto-seeds the file on first deploy.
