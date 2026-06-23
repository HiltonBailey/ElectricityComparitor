# Deploy Guide — Updating HA Dashboards & Node-RED Without Affecting Other Tabs

## HA Dashboard: Updating Only Energy Retailer Views

The Power Dashboard (`url_path: power-dashboard`) contains 10 tabs. Only
the two Energy Retailer views should ever be modified:

1. **Energy Retailer Costs** (path: `testing`)
2. **Energy Retailer Charts** (path: `energy-retailer-charts`)

**Never update or overwrite the full dashboard config.** Instead, fetch the
current config, replace only those two views by matching their `path` field,
and save the result.

### Required Tooling

- Node.js (v22+ built-in `WebSocket` and `fetch` are used)
- HA long-lived access token (see `AGENTS.md`)

### Procedure

Fetch → replace → save using the HA WebSocket API:

```js
import { WebSocket } from 'ws'; // or use Node.js built-in global WebSocket

const TOKEN = '<long-lived-access-token>';
const WS_URL = 'ws://192.168.50.100:8123/api/websocket';

async function wsCmd(type, payload = {}) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(WS_URL);
    const id = Date.now();
    let sent = false;
    ws.addEventListener('message', (msg) => {
      const d = JSON.parse(msg.data);
      if (d.type === 'auth_ok' && !sent) { sent = true; ws.send(JSON.stringify({ id, type, ...payload })); }
      else if (d.type === 'auth_invalid') { ws.close(); reject(new Error('Auth failed')); }
      else if (d.id === id) { ws.close(); resolve(d); }
    });
    ws.addEventListener('open', () => ws.send(JSON.stringify({ type: 'auth', access_token: TOKEN })));
    ws.addEventListener('error', reject);
    setTimeout(() => { ws.close(); reject(new Error('timeout')); }, 20000);
  });
}

// 1. Fetch current config
const getR = await wsCmd('lovelace/config', { url_path: 'power-dashboard' });
const cfg = getR.result;

// 2. Replace views by path (never by index)
for (let i = 0; i < cfg.views.length; i++) {
  if (cfg.views[i].path === 'testing')
    cfg.views[i] = { /* your new Energy Retailer Costs view */ };
  else if (cfg.views[i].path === 'energy-retailer-charts')
    cfg.views[i] = { /* your new Energy Retailer Charts view */ };
}

// 3. Save
await wsCmd('lovelace/config/save', { url_path: 'power-dashboard', config: cfg });
```

Key rules:
- **Match views by `path`, never by array index** — indices may shift.
- **Vertify after save** by re-fetching and checking all views are present.
- **Never use `PUT /api/lovelace/config/...` REST endpoints** — they replace
  the entire dashboard config. Always use WebSocket `lovelace/config/save`
  with the full existing config plus your changes.

### Two-View Config Sources

- `dashboard.yaml` — Energy Retailer Costs view (path: `testing`)
- `dashboard-charts.yaml` — Energy Retailer Charts view (path: `energy-retailer-charts`)

When generating the view objects, convert YAML → JSON, ensuring:
- `heading` cards use `{ type: 'heading', heading: '...' }`
- `custom:html-card` content preserves newlines (`\n` at end)
- `data_generator` strings use `\n` for line breaks within the JS source
- `apex_config` is a flat object (no nested YAML indentation issues)

---

## Node-RED: Updating Only the Energy Retailer Comparison Tab

The flow file `node_red_flow.json` contains the full Node-RED export (all tabs).
Only the tab with ID `tab_energy_retailer_comparison` should ever be deployed.

### Procedure

Use `bash deploy.sh` — it:

1. Reads `node_red_flow.json`
2. **Extracts only the tab node + its children** (nodes where `z === TAB_ID`)
3. Injects version from `VERSION` file into group label and `##GIT_VERSION##` placeholders
4. Sends `PUT /flow/:tab_id` to the Node-RED admin API

`deploy.sh` does all the work — just run:

```bash
bash deploy.sh
```

### How It Stays Safe

- `deploy.sh` only sends `PUT /flow/tab_energy_retailer_comparison` — this
  is a Node-RED Admin API endpoint that **replaces a single tab by ID**
  without touching any other tabs.
- The Python extraction script inside `deploy.sh` filters to exactly one tab:
  - Finds the tab node with `id === 'tab_energy_retailer_comparison'`
  - Collects all nodes whose `z === 'tab_energy_retailer_comparison'`
  - Builds a minimal payload with only those nodes

### If You Need to Update the Flow

1. Edit `node_red_flow.json` directly (or re-export from Node-RED editor)
2. Ensure the tab ID remains `tab_energy_retailer_comparison`
3. Run `bash deploy.sh`

### Version Bumping

When bumping `VERSION`, always **re-run `deploy.sh`** after editing
the version file. The version is read at deploy time and injected into the
group label and sensor attributes. Editing `VERSION` alone does not update
Node-RED — the deploy script must be run to push the new version.

Do **not** use `POST /flow` (import) or `PUT /flow` (replace all flows) —
those would overwrite other tabs.

---

## Common Mistakes

| Mistake | Consequence | Correct Approach |
|---------|-------------|------------------|
| Saving dashboard via HA REST API (`/api/lovelace/config/...`) | Replaces entire dashboard, deleting other tabs | Use WebSocket `lovelace/config/save` with full config |
| Updating dashboard by view index instead of path | Wrong view gets replaced if order changes | Match views by `path` field |
| Running `curl -X POST http://node-red:1880/flow` | Imports entire flow, duplicating or replacing other tabs | Use `PUT /flow/:tab_id` via `deploy.sh` |
| Editing `deploy.sh` to target a different tab ID | Wrong tab gets updated | Keep `TAB_ID=tab_energy_retailer_comparison` |
| Bumping `VERSION` without re-running `deploy.sh` | Node-RED still shows old version | Always run `bash deploy.sh` after changing `VERSION` |

---

## Verification Checklist

After updating:

- [ ] HA: All power dashboard tabs still present (check via HA or re-fetch config)
- [ ] HA: Energy Retailer Costs view renders HTML reports correctly
- [ ] HA: Energy Retailer Charts view shows apexcharts cards
- [ ] NR: Only `tab_energy_retailer_comparison` tab updated (check Node-RED editor)
- [ ] NR: Sensors updating on next 5-min cycle

---

## Retailer Config Editor

A web-based editor for retailer rates and TOU periods is available at:

**http://192.168.50.100:1880/endpoint/api/retailer-config**

(or view it in an iframe on the HA dashboard)

The editor presents a table with one row per retailer and editable fields
for every configurable parameter (DSC, import/export rates, TOU windows,
EV off-peak, free import cap, etc.). Changes are saved to
`/share/retailer_config.csv` on the HA filesystem and take effect on the
next 5-min cycle.

The config file is loaded at startup and on every 5-min inject. If the file
does not exist, the hardcoded defaults from the template node are used as
fallback.

### Updating the Config File Path in Deploy

`deploy.sh` now POSTs the initial/default config to the editor endpoint
after deploying the flow, ensuring the config file exists on first run.
If you add or remove retailers, update both the template node's CSV and the
`deploy.sh` seed section.
