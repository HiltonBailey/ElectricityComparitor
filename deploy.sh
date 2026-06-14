#!/bin/bash
# Deploy ONLY the EnergyRetailerComparison flow tab to Node-RED via PUT /flow/:id
# Injects version from VERSION file into:
#   - Group node name (visible in Node-RED editor)
#   - Sensor attributes (visible in HA dashboard)
# Bump the version by editing VERSION and committing.
# Usage: ./deploy.sh [node_red_flow.json]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FLOW_FILE="${1:-$SCRIPT_DIR/node_red_flow.json}"
NR_URL="${NR_URL:-http://192.168.50.9:1880}"
NR_USER="${NR_USER:-stilgar}"
NR_PASS="${NR_PASS:-Ha0118021669}"
TAB_ID="tab_energy_retailer_comparison"

# Read version from VERSION file
DEPLOY_VERSION="v$(cat "$SCRIPT_DIR/VERSION" 2>/dev/null || echo "0.0")"

echo "Deploying $DEPLOY_VERSION ..."

python3 -c "
import json, sys

with open('$FLOW_FILE') as f:
    data = json.load(f)

tab = None
nodes = []

for n in data:
    if n.get('type') == 'tab' and n.get('id') == '$TAB_ID':
        tab = n
    elif n.get('z') == '$TAB_ID':
        # Inject version into group node name
        if n.get('type') == 'group' and 'Energy Retailer Comparison' in n.get('name', ''):
            n['name'] = 'Energy Retailer Comparison $DEPLOY_VERSION'
        # Replace ##GIT_VERSION## placeholders in function/template code
        if 'func' in n:
            n['func'] = n['func'].replace('##GIT_VERSION##', '$DEPLOY_VERSION')
        if 'template' in n:
            n['template'] = n['template'].replace('##GIT_VERSION##', '$DEPLOY_VERSION')
        nodes.append(n)

if not tab:
    print('Error: tab $TAB_ID not found', file=sys.stderr)
    sys.exit(1)

body = {
    'id': tab['id'],
    'label': tab.get('label', ''),
    'disabled': tab.get('disabled', False),
    'info': tab.get('info', ''),
    'nodes': nodes
}
json.dump(body, sys.stdout)
" > /tmp/flow_tab_payload.json

echo "Sending PUT /flow/$TAB_ID to $NR_URL ..."
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    -X PUT "$NR_URL/flow/$TAB_ID" \
    -u "$NR_USER:$NR_PASS" \
    -H "Content-Type: application/json" \
    -d @/tmp/flow_tab_payload.json)

if [ "$HTTP_CODE" = "204" ] || [ "$HTTP_CODE" = "200" ]; then
    echo "Success (HTTP $HTTP_CODE) — $TAB_ID updated to $DEPLOY_VERSION."
else
    echo "Failed — HTTP $HTTP_CODE"
    curl -s -X PUT "$NR_URL/flow/$TAB_ID" \
        -u "$NR_USER:$NR_PASS" \
        -H "Content-Type: application/json" \
        -d @/tmp/flow_tab_payload.json
    echo
    exit 1
fi
