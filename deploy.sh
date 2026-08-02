#!/bin/bash
# Deploy Energy Retailer Comparison system.
# 1. Deploys Node-RED gap-filling flow tab via PUT /flow/:id
# 2. Seeds Python server retailer config via POST /api/retailer-config/save
# Bump version by editing VERSION file and committing.
# Usage: ./deploy.sh [node_red_flow.json]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FLOW_FILE="${1:-$SCRIPT_DIR/node_red_flow.json}"
NR_URL="${NR_URL:-http://192.168.50.100:1880}"
NR_USER="${NR_USER:-stilgar}"
NR_PASS="${NR_PASS:-Ha0118021669}"
TAB_ID="tab_energy_retailer_comparison"
SERVER_URL="${SERVER_URL:-http://192.168.50.161:8080}"

DEPLOY_VERSION="v$(cat "$SCRIPT_DIR/VERSION" 2>/dev/null || echo "0.0")"
echo "Deploying $DEPLOY_VERSION ..."

# ─── 1. Extract and deploy Node-RED flow tab ───────────────────────────────────
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
        if n.get('type') == 'group' and 'Energy Retailer Comparison' in n.get('name', ''):
            n['name'] = 'Energy Retailer Comparison $DEPLOY_VERSION'
        if 'func' in n:
            n['func'] = n['func'].replace('##GIT_VERSION##', '$DEPLOY_VERSION')
        if 'template' in n:
            n['template'] = n['template'].replace('##GIT_VERSION##', '$DEPLOY_VERSION')
        nodes.append(n)
if not tab:
    print('Error: tab $TAB_ID not found', file=sys.stderr)
    sys.exit(1)
json.dump({'id': tab['id'], 'label': tab.get('label', ''), 'disabled': tab.get('disabled', False), 'info': tab.get('info', ''), 'nodes': nodes}, sys.stdout)
" > /tmp/flow_tab_payload.json

HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    -X PUT "$NR_URL/flow/$TAB_ID" \
    -u "$NR_USER:$NR_PASS" \
    -H "Content-Type: application/json" \
    -d @/tmp/flow_tab_payload.json)

if [ "$HTTP_CODE" != "204" ] && [ "$HTTP_CODE" != "200" ]; then
    echo "Node-RED deploy failed — HTTP $HTTP_CODE"
    curl -s -X PUT "$NR_URL/flow/$TAB_ID" -u "$NR_USER:$NR_PASS" -H "Content-Type: application/json" -d @/tmp/flow_tab_payload.json
    exit 1
fi
echo "Node-RED flow tab updated (HTTP $HTTP_CODE)."

# ─── 2. Seed Python server retailer config ───────────────────────────────────
echo "Seeding Python server retailer config..."
SEED_CODE=$(curl -s -m 10 -o /dev/null -w "%{http_code}" \
    -X POST "$SERVER_URL/api/retailer-config/save" \
    -H "Content-Type: application/x-www-form-urlencoded" \
    --data-urlencode "rowCount=9" \
    --data-urlencode "headers=name,model,dsc,sub,off_pk,sh_pk,pk_pk,off_fit,sh_fit,pk_fit,sp_fit,sp_fit2,sp_limit,off_s,off_e,pk_s,pk_e,free_s,free_e,sp_s,sp_e,off_fit_s,off_fit_e,sh_fit_s,sh_fit_e,pk_fit_s,pk_fit_e,sp_fit_s,sp_fit_e,fixed_export,ev_s,ev_e,ev_pk,off_limit,billing_day,pea_base,pea_override,glo_rebate,energymadeeasy_planid,bat_cap,bat_chg,bat_dis,bat_eff,soc_min,soc_max,init_soc,chg_pct,dis_pct,inv_cap,ac_cap" \
    --data-urlencode "name_1=FlowPower" \
    --data-urlencode "model_1=hybrid" \
    --data-urlencode "dsc_1=2.4047" \
    --data-urlencode "off_pk_1=0.4136" \
    --data-urlencode "sh_pk_1=0.4136" \
    --data-urlencode "pk_pk_1=0.4136" \
    --data-urlencode "sp_fit_1=0.35" \
    --data-urlencode "sp_fit2_1=0.1" \
    --data-urlencode "sp_limit_1=15.0" \
    --data-urlencode "sp_s_1=17.5" \
    --data-urlencode "sp_e_1=21.5" \
    --data-urlencode "off_fit_s_1=17.5" \
    --data-urlencode "off_fit_e_1=21.5" \
    --data-urlencode "sh_fit_s_1=17.5" \
    --data-urlencode "sh_fit_e_1=21.5" \
    --data-urlencode "pk_fit_s_1=17.5" \
    --data-urlencode "pk_fit_e_1=21.5" \
    --data-urlencode "sp_fit_s_1=17.5" \
    --data-urlencode "sp_fit_e_1=21.5" \
    --data-urlencode "billing_day_1=4.0" \
    --data-urlencode "pea_base_1=0.017" \
    --data-urlencode "energymadeeasy_planid_1=FP11143383MRE1" \
    --data-urlencode "name_2=Origin Battery Starter" \
    --data-urlencode "model_2=fixed_tou" \
    --data-urlencode "dsc_2=1.2567" \
    --data-urlencode "off_pk_2=0.33" \
    --data-urlencode "sh_pk_2=0.33" \
    --data-urlencode "pk_pk_2=0.5731" \
    --data-urlencode "off_fit_2=0.05" \
    --data-urlencode "sh_fit_2=0.05" \
    --data-urlencode "pk_fit_2=0.18" \
    --data-urlencode "pk_s_2=17.0" \
    --data-urlencode "pk_e_2=21.0" \
    --data-urlencode "pk_fit_s_2=17.0" \
    --data-urlencode "pk_fit_e_2=21.0" \
    --data-urlencode "billing_day_2=4.0" \
    --data-urlencode "name_3=Origin Battery Maximiser" \
    --data-urlencode "model_3=fixed_tou" \
    --data-urlencode "dsc_3=1.2567" \
    --data-urlencode "off_pk_3=0.187" \
    --data-urlencode "sh_pk_3=0.187" \
    --data-urlencode "pk_pk_3=0.539" \
    --data-urlencode "off_fit_3=0.05" \
    --data-urlencode "sh_fit_3=0.05" \
    --data-urlencode "pk_fit_3=0.22" \
    --data-urlencode "pk_s_3=17.0" \
    --data-urlencode "pk_e_3=21.0" \
    --data-urlencode "pk_fit_s_3=17.0" \
    --data-urlencode "pk_fit_e_3=21.0" \
    --data-urlencode "billing_day_3=4.0" \
    --data-urlencode "name_4=ZEROHERO - VPP" \
    --data-urlencode "model_4=fixed_tou" \
    --data-urlencode "dsc_4=1.584" \
    --data-urlencode "sh_pk_4=0.407" \
    --data-urlencode "pk_pk_4=0.528" \
    --data-urlencode "pk_fit_4=0.02" \
    --data-urlencode "sp_fit_4=0.1" \
    --data-urlencode "sp_limit_4=15.0" \
    --data-urlencode "off_s_4=11.0" \
    --data-urlencode "off_e_4=14.0" \
    --data-urlencode "pk_s_4=16.0" \
    --data-urlencode "pk_e_4=23.0" \
    --data-urlencode "sp_s_4=18.0" \
    --data-urlencode "sp_e_4=21.0" \
    --data-urlencode "off_fit_s_4=23.0" \
    --data-urlencode "off_fit_e_4=16.0" \
    --data-urlencode "sh_fit_s_4=16.0" \
    --data-urlencode "sh_fit_e_4=23.0" \
    --data-urlencode "pk_fit_s_4=16.0" \
    --data-urlencode "pk_fit_e_4=23.0" \
    --data-urlencode "sp_fit_s_4=18.0" \
    --data-urlencode "sp_fit_e_4=21.0" \
    --data-urlencode "billing_day_4=4.0" \
    --data-urlencode "glo_rebate_4=1" \
    --data-urlencode "name_5=Globird Four4Free" \
    --data-urlencode "model_5=fixed_tou" \
    --data-urlencode "dsc_5=1.30174" \
    --data-urlencode "off_pk_5=0.25608" \
    --data-urlencode "sh_pk_5=0.36385" \
    --data-urlencode "pk_pk_5=0.58152" \
    --data-urlencode "sh_fit_5=0.08" \
    --data-urlencode "pk_fit_5=0.08" \
    --data-urlencode "off_s_5=11.0" \
    --data-urlencode "off_e_5=15.0" \
    --data-urlencode "pk_s_5=16.0" \
    --data-urlencode "pk_e_5=23.0" \
    --data-urlencode "off_fit_s_5=23.0" \
    --data-urlencode "off_fit_e_5=16.0" \
    --data-urlencode "sh_fit_s_5=16.0" \
    --data-urlencode "sh_fit_e_5=23.0" \
    --data-urlencode "pk_fit_s_5=16.0" \
    --data-urlencode "pk_fit_e_5=23.0" \
    --data-urlencode "off_limit_5=50.0" \
    --data-urlencode "billing_day_5=4.0" \
    --data-urlencode "energymadeeasy_planid_5=GLO1149401MRE1" \
    --data-urlencode "name_6=Flow Four4Free" \
    --data-urlencode "model_6=fixed_tou" \
    --data-urlencode "dsc_6=2.4047" \
    --data-urlencode "sh_pk_6=0.4136" \
    --data-urlencode "pk_pk_6=0.4136" \
    --data-urlencode "sp_fit_6=0.2" \
    --data-urlencode "sp_fit2_6=0.05" \
    --data-urlencode "sp_limit_6=15.0" \
    --data-urlencode "off_s_6=11.0" \
    --data-urlencode "off_e_6=15.0" \
    --data-urlencode "off_fit_s_6=17.5" \
    --data-urlencode "off_fit_e_6=21.5" \
    --data-urlencode "sh_fit_s_6=17.5" \
    --data-urlencode "sh_fit_e_6=21.5" \
    --data-urlencode "pk_fit_s_6=17.5" \
    --data-urlencode "pk_fit_e_6=21.5" \
    --data-urlencode "sp_fit_s_6=17.5" \
    --data-urlencode "sp_fit_e_6=21.5" \
    --data-urlencode "off_limit_6=32.0" \
    --data-urlencode "billing_day_6=4.0" \
    --data-urlencode "energymadeeasy_planid_6=FP11147426MRE1" \
    --data-urlencode "name_7=AGL Battery Rewards" \
    --data-urlencode "model_7=fixed_tou" \
    --data-urlencode "dsc_7=1.58631" \
    --data-urlencode "off_pk_7=0.21626" \
    --data-urlencode "sh_pk_7=0.21626" \
    --data-urlencode "pk_pk_7=0.54175" \
    --data-urlencode "off_fit_7=0.03" \
    --data-urlencode "sh_fit_7=0.03" \
    --data-urlencode "pk_fit_7=0.28" \
    --data-urlencode "sp_fit_7=0.03" \
    --data-urlencode "off_s_7=21.0" \
    --data-urlencode "off_e_7=15.0" \
    --data-urlencode "pk_s_7=15.0" \
    --data-urlencode "pk_e_7=21.0" \
    --data-urlencode "pk_fit_s_7=17.0" \
    --data-urlencode "pk_fit_e_7=21.0" \
    --data-urlencode "sp_fit_s_7=7.0" \
    --data-urlencode "sp_fit_e_7=8.0" \
    --data-urlencode "billing_day_7=4.0" \
    --data-urlencode "name_8=Amber" \
    --data-urlencode "model_8=variable" \
    --data-urlencode "dsc_8=1.032" \
    --data-urlencode "sub_8=0.8213" \
    --data-urlencode "sh_pk_8=0.0915" \
    --data-urlencode "off_fit_8=1.0" \
    --data-urlencode "billing_day_8=4" \
    --data-urlencode "bat_cap_8=41.92" \
    --data-urlencode "bat_chg_8=11" \
    --data-urlencode "bat_dis_8=10" \
    --data-urlencode "bat_eff_8=0.9" \
    --data-urlencode "soc_min_8=0.02" \
    --data-urlencode "soc_max_8=1.0" \
    --data-urlencode "init_soc_8=0.5" \
    --data-urlencode "chg_pct_8=0.3" \
    --data-urlencode "dis_pct_8=0.7" \
    --data-urlencode "inv_cap_8=10" \
    --data-urlencode "ac_cap_8=14.5" \
    --data-urlencode "name_9=Flipped Solar Sharer 2.2" \
    --data-urlencode "model_9=fixed_tou" \
    --data-urlencode "dsc_9=1.1883" \
    --data-urlencode "off_pk_9=0.2664" \
    --data-urlencode "sh_pk_9=0.1836" \
    --data-urlencode "pk_pk_9=0.5810" \
    --data-urlencode "off_fit_9=0.02" \
    --data-urlencode "sh_fit_9=0.02" \
    --data-urlencode "pk_fit_9=0.02" \
    --data-urlencode "off_s_9=20.0" \
    --data-urlencode "off_e_9=8.0" \
    --data-urlencode "pk_s_9=16.0" \
    --data-urlencode "pk_e_9=20.0" \
    --data-urlencode "free_s_9=11.0" \
    --data-urlencode "free_e_9=14.0" \
    --data-urlencode "off_limit_9=24.0" \
    --data-urlencode "billing_day_9=4.0" \
    --data-urlencode "dummy_seed=1")
echo "Python server config seed: HTTP $SEED_CODE"
