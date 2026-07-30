# Project: ElectricityRetailerComparison

## Goal
Multi-retailer electricity cost comparison system comparing FlowPower against Origin/Globird/ZEROHERO/Flow/AGL. Cost calculation and reporting handled by a Python server (`energy_server.py`) running on the n8n LXC (192.168.50.161:8080). HA Dashboard fetches data from the Python server via HTTP. Node‑RED handles only CSV gap-filling.

## Architecture
```
HA SMB share (192.168.50.100:/share)
  └─ file_notifications/5minelecNEW.csv  ← HA data logger writes here
       │
       ├── n8n LXC (192.168.50.161:8080)
       │    └─ Python server reads via SMB mount at /mnt/ha_share/file_notifications/
       │         ├── GET /              → Dashboard (Reports/Charts/Config tabs)
       │         ├── GET /daily-report  → Daily cost table
       │         ├── GET /monthly-report→ Monthly rollup with TOTAL row
       │         ├── GET /seasonal-report→ Seasonal rollup with TOTAL row
       │         ├── GET /5min-detail   → 5-min interval detail
       │         ├── GET /api/status    → CSV rows/retailers/dates/PEA
       │         ├── GET /api/daily-data → JSON daily summaries
       │         ├── GET /api/retailers  → JSON retailer configs
       │         ├── GET /api/chart-data → Chart.js line chart data
       │         └── GET/POST /api/retailer-config → Config editor
       │
       └── Node-RED (192.168.50.100:1880)
            └── CSV gap-filling only (detect_gaps → prepare_ha_queries → process_ha_and_fill)
```

## Pricing Models
1. **hybrid** (FlowPower): All rates same = flat rate. Export within sp_fit window tiers sp_fit/sp_fit2. PEA applied monthly.
2. **fixed_tou** (Origin, ZEROHERO, Globird, Flow Four4Free, AGL): TOU periods with off_limit free cap. Default rate `sh_pk` (shoulder).

## Deployment
### Python Server (192.168.50.161:8080)
- Runs as `energy-server.service` on container 104 (n8n LXC)
- ExecStart: `/opt/energy_server.sh` → `python3 /opt/energy_server.py --csv /mnt/ha_share/... --config /opt/energy_data/retailer_config.csv --port 8080`
- Deploy: `bash deploy.sh` — seeds config via `POST /api/retailer-config/save`

### SMB Mount (Proxmox host → container 104)
- Host mounts `//192.168.50.100/share` at `/mnt/ha_share` (CIFS, user Stilgar)
- Container bind-mounts host's `/mnt/ha_share` at `/mnt/ha_share` via `mp0` in `/etc/pve/lxc/104.conf`

### HA Dashboard
- Separate dashboard `energy-retailer-dashboard` with views `testing` and `energy-retailer-charts`
- Deploy via WebSocket API using `deploy_config_view.py` (see DEPLOY.md)

## Key Files
| File | Purpose |
|---|---|
| `energy_server.py` | Python web server — all cost calc + reporting |
| `node_red_flow.json` | Node-RED flow — CSV gap-filling only |
| `deploy.sh` | Seeds Python server config |
| `dashboard.yaml` | HA view — "Energy Retailer Costs" |
| `dashboard-charts.yaml` | HA view — "Energy Retailer Charts" |
| `dashboard-config.yaml` | HA view — Config editor iframe |
| `newseed.csv` | **Critical backup** — never delete |
| `retailer_config.csv` | Local reference copy of retailer config |

## Critical Context
- **CSV source**: `5minelecNEW.csv` lives on HA SMB share only. No local copy in repo.
- **Node-RED** only handles CSV gap-filling. No cost calculation or HTTP reporting endpoints.
- **Config editor**: `http://192.168.50.161:8080/api/retailer-config`
- **SMB Credentials**: user `Stilgar` / `Ha0118021669`
- **Proxmox host**: 192.168.50.49 (root / `Ha0118021669`), n8n container 104
- **HA**: 192.168.50.100:8123, Node-RED admin on port 1880 (basic auth `stilgar`/`Ha0118021669`)
- **PEA**: Peak Export Adjustment = LWAP − TWAP − pea_base. Applied to FlowPower monthly.
- **CSV columns (14)**: datetime,offpeak,shoulder,peak,export,bat_charge,bat_charge2,bat_discharge,house_load,gen_price,fit_price,aemo_price,pe_datetime,solar_gen
- **Config columns (37)**: name,model,dsc,sub,off_pk,sh_pk,pk_pk,off_fit,sh_fit,pk_fit,sp_fit,sp_fit2,sp_limit,off_s,off_e,pk_s,pk_e,sp_s,sp_e,off_fit_s,off_fit_e,sh_fit_s,sh_fit_e,pk_fit_s,pk_fit_e,sp_fit_s,sp_fit_e,fixed_export,ev_s,ev_e,ev_pk,off_limit,billing_day,pea_base,pea_override,glo_rebate,energymadeeasy_planid

## Retailers
| # | Name | Model | DSC | Notes |
|---|---|---|---|---|
| 1 | FlowPower | hybrid | $2.4047 | sp_fit=35c, sp_fit2=10c, sp_limit=15, PEA applied |
| 2 | Origin Battery Starter | fixed_tou | $1.2567 | pk=57.31c 5-9pm, off=33c, pk_fit=18c |
| 3 | Origin Battery Maximiser | fixed_tou | $1.2567 | pk=53.9c 5-9pm, off=18.7c, pk_fit=22c |
| 4 | ZEROHERO - VPP | fixed_tou | $1.584 | sh=40.7c, pk=52.8c, sp_fit=10c, glo_rebate=1 |
| 5 | Globird Four4Free | fixed_tou | $1.70 | pk=46c 4-11pm, sh=23c, off_limit=50, pk_fit=8c |
| 6 | Flow Four4Free | fixed_tou | $2.4047 | sp_fit=20c, sp_fit2=5c, off_limit=32 |
| 7 | AGL Battery Rewards | fixed_tou | $1.58631 | pk=54.175c 3-9pm, off=21.626c 9pm-3pm, pk_fit=28c 5-9pm, sp_fit=3c 7-8am |

## Version
Current: v3.00
