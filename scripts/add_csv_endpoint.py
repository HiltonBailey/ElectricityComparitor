import json, sys, uuid

FLOW_FILE = sys.argv[1]
CSV_FILE = sys.argv[2]
NR_URL = sys.argv[3]
NR_USER = sys.argv[4]
NR_PASS = sys.argv[5]
TAB_ID = 'tab_energy_retailer_comparison'

with open(FLOW_FILE) as f:
    flow = json.load(f)

# Find the tab node and existing nodes
tab = None
existing_ids = set()
for n in flow:
    existing_ids.add(n.get('id', ''))
    if n.get('type') == 'tab' and n.get('id') == TAB_ID:
        tab = n

if not tab:
    print('Tab not found', file=sys.stderr)
    sys.exit(1)

# Generate unique IDs for temp nodes
in_id = 'tmp_csv_http_in'
func_id = 'tmp_csv_func'
out_id = 'tmp_csv_out'

# Create temp nodes
temp_nodes = [
    {
        'id': in_id,
        'type': 'http in',
        'z': TAB_ID,
        'name': 'TMP Write CSV',
        'url': '/api/write-csv',
        'method': 'post',
        'upload': False,
        'swaggerDoc': '',
        'x': 200, 'y': 800,
        'wires': [[func_id]]
    },
    {
        'id': func_id,
        'type': 'function',
        'z': TAB_ID,
        'name': 'TMP CSV handler',
        'func': """
var fs = require('fs');
var csv = msg.payload;
fs.writeFileSync('/share/file_notifications/5minelecNEW.csv', csv);
node.warn('CSV written: ' + csv.length + ' bytes');
msg.payload = { status: 'ok', bytes: csv.length };
return msg;
""",
        'outputs': 1,
        'x': 400, 'y': 800,
        'wires': [['tmp_csv_resp']]
    },
    {
        'id': 'tmp_csv_resp',
        'type': 'http response',
        'z': TAB_ID,
        'name': 'TMP CSV response',
        'x': 600, 'y': 800,
        'wires': []
    }
]

# Remove any existing temp nodes
flow = [n for n in flow if n.get('id') not in {in_id, func_id, 'tmp_csv_resp'}]

# Add temp nodes
flow.extend(temp_nodes)

# Write modified flow
with open('/tmp/write_csv_flow.json', 'w') as f:
    json.dump(flow, f, indent=2)

print('Temp flow written to /tmp/write_csv_flow.json')
