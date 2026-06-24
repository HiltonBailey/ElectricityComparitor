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
NR_URL="${NR_URL:-http://192.168.50.100:1880}"
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

    # Seed retailer config file (idempotent — overwrites with same defaults)
    echo "Seeding retailer config file..."
    SEED_CODE=$(curl -s -m 10 -o /dev/null -w "%{http_code}" \
        -X POST "$NR_URL/endpoint/api/retailer-config" \
        -u "$NR_USER:$NR_PASS" \
        -H "Content-Type: application/x-www-form-urlencoded" \
        --data-urlencode "rowCount=5" \
        --data-urlencode "headers=name,model,dsc,sub,off_pk,sh_pk,pk_pk,off_fit,sh_fit,pk_fit,sp_fit,sp_limit,off_s,off_e,pk_s,pk_e,sp_s,sp_e,exp_s,exp_e,exp_rate,fixed_export,ev_s,ev_e,ev_pk,off_limit" \
        --data-urlencode "name_1=FlowPower" \
        --data-urlencode "model_1=hybrid" \
        --data-urlencode "dsc_1=1.3419" \
        --data-urlencode "off_pk_1=0.33998" \
        --data-urlencode "sh_pk_1=0.33998" \
        --data-urlencode "pk_pk_1=0.33998" \
        --data-urlencode "sp_fit_1=0.45" \
        --data-urlencode "exp_s_1=17.5" \
        --data-urlencode "exp_e_1=19.5" \
        --data-urlencode "exp_rate_1=0.45" \
        --data-urlencode "fixed_export_1=18" \
        --data-urlencode "name_2=Origin Loop Max" \
        --data-urlencode "model_2=fixed_tou" \
        --data-urlencode "dsc_2=1.2567" \
        --data-urlencode "off_pk_2=0.187" \
        --data-urlencode "sh_pk_2=0.187" \
        --data-urlencode "pk_pk_2=0.539" \
        --data-urlencode "sh_fit_2=0.05" \
        --data-urlencode "pk_fit_2=0.22" \
        --data-urlencode "pk_s_2=17" \
        --data-urlencode "pk_e_2=21" \
        --data-urlencode "name_3=Globird VPP" \
        --data-urlencode "model_3=fixed_tou" \
        --data-urlencode "dsc_3=1.32" \
        --data-urlencode "sh_pk_3=0.363" \
        --data-urlencode "pk_pk_3=0.495" \
        --data-urlencode "pk_fit_3=0.05" \
        --data-urlencode "sp_fit_3=0.15" \
        --data-urlencode "sp_limit_3=15" \
        --data-urlencode "off_s_3=11" \
        --data-urlencode "off_e_3=14" \
        --data-urlencode "pk_s_3=16" \
        --data-urlencode "pk_e_3=23" \
        --data-urlencode "sp_s_3=18" \
        --data-urlencode "sp_e_3=21" \
        --data-urlencode "name_4=CovaU SolarMax" \
        --data-urlencode "model_4=fixed_tou" \
        --data-urlencode "dsc_4=1.1818" \
        --data-urlencode "sh_pk_4=0.2547" \
        --data-urlencode "pk_pk_4=0.5581" \
        --data-urlencode "sp_fit_4=0.18" \
        --data-urlencode "off_s_4=11" \
        --data-urlencode "off_e_4=14" \
        --data-urlencode "pk_s_4=17" \
        --data-urlencode "pk_e_4=21" \
        --data-urlencode "sp_s_4=17" \
        --data-urlencode "sp_e_4=21" \
        --data-urlencode "ev_s_4=0" \
        --data-urlencode "ev_e_4=6" \
        --data-urlencode "ev_pk_4=0.15" \
        --data-urlencode "off_limit_4=24" \
        --data-urlencode "name_5=Amber" \
        --data-urlencode "model_5=variable" \
        --data-urlencode "dsc_5=1.76" \
        --data-urlencode "sub_5=25" \
        --data-urlencode "exp_rate_5=1" \
        -o /dev/null -w "%{http_code}")
    echo "Config seed: HTTP $SEED_CODE"
else
    echo "Failed — HTTP $HTTP_CODE"
    curl -s -X PUT "$NR_URL/flow/$TAB_ID" \
        -u "$NR_USER:$NR_PASS" \
        -H "Content-Type: application/json" \
        -d @/tmp/flow_tab_payload.json
    echo
    exit 1
fi
