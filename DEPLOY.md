# Deploy Guide

## Overview
Two components to deploy:
1. **Node-RED flow tab** — CSV gap-filling only
2. **Python server config** — retailer rates/TOU config

Run once: `bash deploy.sh`

---

## 1. Node-RED: Gap-Filling Flow

The flow file `node_red_flow.json` contains the full export. Only tab `tab_energy_retailer_comparison` is deployed.

`deploy.sh` extracts that single tab, injects the version from `VERSION`, and sends `PUT /flow/:tab_id` to the Node-RED admin API. Other tabs are untouched.

### Updating the Flow
1. Edit `node_red_flow.json` (or re-export from Node-RED editor)
2. Ensure tab ID remains `tab_energy_retailer_comparison`
3. Run `bash deploy.sh`

### Version Bumping
Edit `VERSION`, then re-run `bash deploy.sh`. The version is injected into the group label at deploy time.

---

## 2. Python Server: Retailer Config

The Python server at `http://192.168.50.161:8080` reads retailer config from `/opt/energy_data/retailer_config.csv`.

`deploy.sh` POSTs the full retailer list to `/api/retailer-config/save` to seed/update the config file.

### Config Editor
Web-based editor at `http://192.168.50.161:8080/api/retailer-config`

Edit rates, TOU windows, FIT periods etc. Saves to `/opt/energy_data/retailer_config.csv` on the server.

---

## 3. HA Dashboard: Updating Views Only

The `energy-retailer-dashboard` has 3 views (`testing`, `energy-retailer-charts`, `energy-retailer-config`).

NEVER use REST endpoints — they replace the entire dashboard. Use HA WebSocket API:

```js
const TOKEN = '<ha-token>';
const WS_URL = 'ws://192.168.50.100:8123/api/websocket';

// 1. Fetch current config
const getR = await wsCmd('lovelace/config', { url_path: 'energy-retailer-dashboard' });
const cfg = getR.result;

// 2. Replace views by path
for (let i = 0; i < cfg.views.length; i++) {
  if (cfg.views[i].path === 'testing')
    cfg.views[i] = { /* Energy Retailer Costs */ };
  else if (cfg.views[i].path === 'energy-retailer-charts')
    cfg.views[i] = { /* Energy Retailer Charts */ };
}

// 3. Save
await wsCmd('lovelace/config/save', { url_path: 'energy-retailer-dashboard', config: cfg });
```

View YAML sources:
- `dashboard.yaml` — path: `testing`
- `dashboard-charts.yaml` — path: `energy-retailer-charts`
- `dashboard-config.yaml` — path: `energy-retailer-config` (config editor iframe, typically left untouched)

### Verification
- Dashboard shows all 3 views
- Reports display current data (check date range)
- Charts render apexcharts cards

---

## Server Locations

| Component | Address | Auth |
|---|---|---|
| Python Server | http://192.168.50.161:8080 | None (internal) |
| Node-RED Admin | http://192.168.50.100:1880 | basic auth: `stilgar` / `Ha0118021669` |
| HA | http://192.168.50.100:8123 | Bearer token |
| Proxmox Host | 192.168.50.49 | SSH: `root` / `Ha0118021669` |
| HA SMB Share | `//192.168.50.100/share` | user `Stilgar` / `Ha0118021669` |

## Common Mistakes

| Mistake | Consequence | Fix |
|---|---|---|
| Using HA REST API for dashboard update | Replaces entire dashboard | Use WebSocket `lovelace/config/save` |
| Using `POST /flow` on Node-RED | Imports/duplicates tabs | Use `PUT /flow/:tab_id` via deploy.sh |
| Bumping VERSION without re-deploying | Version label not updated | Run `bash deploy.sh` |
| Editing config on server but not in repo | Lost on next deploy | Update `deploy.sh` seed data + `node_red_flow.json` template |
