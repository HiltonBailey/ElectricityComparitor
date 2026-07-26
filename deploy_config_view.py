#!/usr/bin/env python3
"""Deploy energy-retailer-config view to HA dashboard via WebSocket API using dashboard-config.yaml."""
import asyncio, json, sys, os
import websockets
import yaml

HA_URL = "ws://192.168.50.100:8123/api/websocket"
TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiIyZGVkZTMwMWI1Mzc0NmJhOTNhOTM2YzM4N2FmOGU0ZSIsImlhdCI6MTc4MjA4NTA5OSwiZXhwIjoyMDk3NDQ1MDk5fQ.ovX2gmYaIlLbxTcw54DngXne9K8HbDFgl_Sb3afjIcU"
DASHBOARD_PATH = "energy-retailer-dashboard"
VIEW_PATH = "energy-retailer-config"
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_YAML = os.path.join(SCRIPT_DIR, "dashboard-config.yaml")

async def main():
    with open(CONFIG_YAML, "r") as f:
        config_view = yaml.safe_load(f)

    async with websockets.connect(HA_URL) as ws:
        # Receive auth_required
        msg = json.loads(await ws.recv())
        print(f"Server: {msg['type']}")

        # Authenticate
        await ws.send(json.dumps({"type": "auth", "access_token": TOKEN}))
        msg = json.loads(await ws.recv())
        print(f"Auth: {msg['type']}")
        if msg['type'] != 'auth_ok':
            print(f"Auth failed: {msg}")
            return

        # Fetch current dashboard config
        req_id = 1
        await ws.send(json.dumps({
            "id": req_id,
            "type": "lovelace/config",
            "url_path": DASHBOARD_PATH
        }))
        msg = json.loads(await ws.recv())
        print(f"Config fetch: {msg['type']}")
        if msg['type'] != 'result' or not msg.get('success'):
            print(f"Failed to fetch config: {msg}")
            return

        config = msg['result']
        views = config.get('views', [])
        print(f"Current views: {[v.get('path') for v in views]}")

        # Check if config view already exists
        existing_idx = None
        for i, v in enumerate(views):
            if v.get('path') == VIEW_PATH:
                existing_idx = i
                break

        if existing_idx is not None:
            views[existing_idx] = config_view
            print(f"Replaced existing view '{VIEW_PATH}'")
        else:
            views.append(config_view)
            print(f"Added new view '{VIEW_PATH}'")

        config['views'] = views

        # Save config
        req_id = 2
        await ws.send(json.dumps({
            "id": req_id,
            "type": "lovelace/config/save",
            "url_path": DASHBOARD_PATH,
            "config": config
        }))
        msg = json.loads(await ws.recv())
        print(f"Config save: {msg['type']}")
        if msg['type'] == 'result' and msg.get('success'):
            print("Success! Config view added to dashboard.")
        else:
            print(f"Save failed: {msg}")

asyncio.run(main())
