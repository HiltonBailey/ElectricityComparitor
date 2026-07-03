// Serve editable config page
var NL = String.fromCharCode(10);
var retailers = flow.get('retailers');
var csvContent = (typeof msg.payload === 'string' && msg.payload.length > 10) ? msg.payload : '';
if (!csvContent && retailers && retailers.length > 0) {
    var csvHeaders = Object.keys(retailers[0]).filter(function(k){ return k !== 'sensor_id'; });
    var csvRows = [];
    for (var ri = 0; ri < retailers.length; ri++) {
        var rowVals = [];
        for (var hi = 0; hi < csvHeaders.length; hi++) {
            var cell = retailers[ri][csvHeaders[hi]];
            rowVals.push(cell !== undefined ? cell : '');
        }
        csvRows.push(rowVals.join(','));
    }
    csvContent = csvHeaders.join(',') + NL + csvRows.join(NL);
}
if (!csvContent || csvContent.length < 10) {
    csvContent = [
        'name,model,dsc,sub,off_pk,sh_pk,pk_pk,off_fit,sh_fit,pk_fit,sp_fit,sp_limit,off_s,off_e,pk_s,pk_e,sp_s,sp_e,off_fit_s,off_fit_e,sh_fit_s,sh_fit_e,pk_fit_s,pk_fit_e,sp_fit_s,sp_fit_e,fixed_export,ev_s,ev_e,ev_pk,off_limit,billing_day',
        'FlowPower,hybrid,1.3419,0,0.33998,0.33998,0.33998,0,0,0,0.45,0,0,0,0,0,17.5,19.5,17.5,19.5,17.5,19.5,17.5,19.5,17.5,19.5,18,0,0,0,0,4',
        'Origin Loop Max,fixed_tou,1.2567,0,0.187,0.187,0.539,0.05,0.05,0.22,0,0,0,0,17,21,0,0,17,21,17,21,17,21,0,0,0,0,0,0,0,4',
        'Globird VPP,fixed_tou,1.32,0,0,0.363,0.495,0,0,0.05,0.15,15,11,14,16,23,18,21,16,23,16,23,16,23,18,21,0,0,0,0,0,4',
        'CovaU SolarMax,fixed_tou,1.1818,0,0,0.2547,0.5581,0.05,0.05,0.05,0.18,0,11,14,17,21,17,21,17,21,17,21,17,21,17,21,0,0,6,0.15,24,4',
        'Amber,variable,1.76,25,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0.05,0,0,0,0,4'
    ].join(NL);
}
var lines = csvContent.split(NL);
var headers = lines[0].split(',');
var hiddenFields = ['ev_s', 'ev_e', 'ev_pk'];
var displayNames = {};
displayNames['off_pk'] = 'Off TOU<br><span style="font-size:9px;font-weight:normal">Rate</span>';
displayNames['sh_pk'] = 'Shld TOU<br><span style="font-size:9px;font-weight:normal">Rate</span>';
displayNames['pk_pk'] = 'Peak TOU<br><span style="font-size:9px;font-weight:normal">Rate</span>';
displayNames['off_fit'] = 'Off FIT<br><span style="font-size:9px;font-weight:normal">Rate</span>';
displayNames['sh_fit'] = 'Shld FIT<br><span style="font-size:9px;font-weight:normal">Rate</span>';
displayNames['pk_fit'] = 'Peak FIT<br><span style="font-size:9px;font-weight:normal">Rate</span>';
displayNames['sp_fit'] = 'S/Peak FIT<br><span style="font-size:9px;font-weight:normal">Rate</span>';
displayNames['sp_limit'] = 'S/Peak Limit<br><span style="font-size:9px;font-weight:normal">kWh</span>';
displayNames['off_s'] = 'Off Start';
displayNames['off_e'] = 'Off End';
displayNames['pk_s'] = 'Peak Start';
displayNames['pk_e'] = 'Peak End';
displayNames['sp_s'] = 'S/Peak Start';
displayNames['sp_e'] = 'S/Peak End';
displayNames['off_fit_s'] = 'Off FIT<br><span style="font-size:9px;font-weight:normal">Start</span>';
displayNames['off_fit_e'] = 'Off FIT<br><span style="font-size:9px;font-weight:normal">End</span>';
displayNames['sh_fit_s'] = 'Shld FIT<br><span style="font-size:9px;font-weight:normal">Start</span>';
displayNames['sh_fit_e'] = 'Shld FIT<br><span style="font-size:9px;font-weight:normal">End</span>';
displayNames['pk_fit_s'] = 'Peak FIT<br><span style="font-size:9px;font-weight:normal">Start</span>';
displayNames['pk_fit_e'] = 'Peak FIT<br><span style="font-size:9px;font-weight:normal">End</span>';
displayNames['sp_fit_s'] = 'S/Peak FIT<br><span style="font-size:9px;font-weight:normal">Start</span>';
displayNames['sp_fit_e'] = 'S/Peak FIT<br><span style="font-size:9px;font-weight:normal">End</span>';
displayNames['fixed_export'] = 'Fixed Export<br><span style="font-size:9px;font-weight:normal">kWh</span>';
displayNames['ev_s'] = 'EV Start';
displayNames['ev_e'] = 'EV End';
displayNames['ev_pk'] = 'EV Rate';
displayNames['off_limit'] = 'Free Limit<br><span style="font-size:9px;font-weight:normal">kWh</span>';
displayNames['billing_day'] = 'Billing Start<br><span style="font-size:9px;font-weight:normal">Day of Month</span>';

var html = '<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">';
html += '<style>body{margin:12px;background:#111;color:#eee;font-family:monospace;font-size:12px}';
html += 'h2{color:#4CAF50;margin:16px 0 8px}';
html += 'table{width:100%;border-collapse:collapse;margin-bottom:20px}';
html += 'th,td{padding:6px 8px;border:1px solid #333;text-align:left;min-width:60px}';
html += 'th{background:#222;color:#aaa;font-size:10px;white-space:nowrap;cursor:help;border-bottom:1px dotted #555}';
html += 'td{background:#1a1a1a}';
html += 'input[type=text],select{width:100%;box-sizing:border-box;padding:4px 6px;background:#000;color:#eee;border:1px solid #444;border-radius:3px;font-family:monospace;font-size:11px}';
html += 'input[type=text]:focus,select:focus{outline:none;border-color:#4CAF50}';
html += 'select option{background:#222;color:#eee}';
html += 'input[type=checkbox]{width:14px;height:14px;cursor:pointer}';
html += 'button{padding:10px 24px;color:white;border:none;border-radius:4px;cursor:pointer;font-size:14px;font-family:monospace}';
html += 'button:hover{opacity:0.85}';
html += '#saveBtn{background:#4CAF50}';
html += '#revertBtn:disabled{background:#555;cursor:not-allowed}';
html += '.header{color:#888;font-size:10px;margin:4px 0 12px}';
html += '.success{color:#4CAF50;padding:10px;background:#0a2a0a;border:1px solid #4CAF50;border-radius:4px;margin-bottom:12px;display:none}';
html += '.del-row{background:#1a0d0d !important}';
html += 'th.del{width:40px;text-align:center;cursor:default}';
html += 'td.del{text-align:center}';
html += '</style></head><body>';
html += '<h2>Retailer Configuration</h2>';
html += '<div class="header">Edit retailer rates and TOU periods. Changes take effect on the next 5-min cycle.</div>';
html += '<div id="success" class="success">Saved successfully</div>';
html += '<form id="configForm">';
html += '<div style="overflow-x:auto"><table><thead><tr>';
for (var h = 0; h < headers.length; h++) {
    if (hiddenFields.indexOf(headers[h].trim()) >= 0) continue;
    var hdrName = headers[h].trim();
    var helpText = {
        'name': 'Retailer name (read-only identifier)',
        'model': 'Pricing model: fixed_tou = TOU rates, hybrid = FlowPower blended, variable = wholesale-linked',
        'dsc': 'Daily supply charge ($/day)',
        'sub': 'Monthly subscription fee ($/month)',
        'off_pk': 'Off-peak import rate ($/kWh)',
        'sh_pk': 'Shoulder import rate ($/kWh)',
        'pk_pk': 'Peak import rate ($/kWh)',
        'off_fit': 'Off-peak feed-in tariff ($/kWh)',
        'sh_fit': 'Shoulder feed-in tariff ($/kWh)',
        'pk_fit': 'Peak feed-in tariff ($/kWh)',
        'sp_fit': 'Super peak feed-in tariff ($/kWh)',
        'sp_limit': 'Super peak daily export limit before fallback to peak FIT (kWh)',
        'off_s': 'Off-peak window start hour (0-24, e.g. 11 = 11:00)',
        'off_e': 'Off-peak window end hour (0-24)',
        'pk_s': 'Peak window start hour',
        'pk_e': 'Peak window end hour',
        'sp_s': 'Super peak window start hour',
        'sp_e': 'Super peak window end hour',
        'fixed_export': 'Hybrid only: fixed export during FIT window (kWh). 0 = use actual export',
        'off_fit_s': 'Off-peak FIT window start hour',
        'off_fit_e': 'Off-peak FIT window end hour',
        'sh_fit_s': 'Shoulder FIT window start hour',
        'sh_fit_e': 'Shoulder FIT window end hour',
        'pk_fit_s': 'Peak FIT window start hour',
        'pk_fit_e': 'Peak FIT window end hour',
        'sp_fit_s': 'Super peak FIT window start hour',
        'sp_fit_e': 'Super peak FIT window end hour',
        'off_limit': 'Free import daily cap (kWh) excess charged at shoulder rate',
        'billing_day': 'Billing cycle start day of month (1-28). Default 4.'
    }[hdrName] || '';
    html += '<th title="' + helpText + '">' + (displayNames[hdrName] || hdrName) + '</th>';
}
html += '<th class="del" title="Check to delete this retailer">Del</th>';
html += '</tr></thead><tbody>';

for (var i = 1; i < lines.length; i++) {
    var row = lines[i].split(',');
    if (row.length < headers.length) continue;
    html += '<tr>';
    for (var j = 0; j < headers.length; j++) {
        if (hiddenFields.indexOf(headers[j].trim()) >= 0) continue;
        var val = (row[j] || '').trim();
        var hdr = headers[j].trim();
        if (j === 0) {
            html += '<td style="color:#888">' + val + '</td>';
        } else {
            if (hdr === 'model') {
                var opts = ['fixed_tou', 'hybrid', 'variable'];
                html += '<td style="min-width:100px"><select name="' + hdr + '_' + i + '" style="min-width:90px">';
                for (var oi = 0; oi < opts.length; oi++) {
                    var o = opts[oi];
                    html += '<option value="' + o + '"' + (val === o ? ' selected' : '') + '>' + o + '</option>';
                }
                html += '</select></td>';
            } else {
                html += '<td><input type="text" name="' + hdr + '_' + i + '" value="' + val + '"></td>';
            }
        }
    }
    html += '<td class="del"><input type="checkbox" class="del-check" name="del_' + i + '"></td>';
    html += '</tr>';
}
html += '</tbody></table></div>';
html += '<input type="hidden" name="rowCount" value="' + (lines.length - 1) + '">';
html += '<input type="hidden" name="headers" value="' + headers.join(',') + '">';
html += '<input type="hidden" name="previousCsv" id="previousCsv">';
html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px">';
html += '<button id="saveBtn" type="submit">Save Configuration</button>';
html += '<div style="display:flex;align-items:center;gap:8px">';
html += '<label style="display:inline-flex;align-items:center;gap:5px;font-size:11px;color:#aaa;cursor:pointer"><input type="checkbox" id="confirmRevert" style="width:13px;height:13px">Revert</label>';
html += '<button id="revertBtn" type="button" onclick="revertConfig()" disabled style="padding:5px 12px;font-size:10px;background:#f44336;color:white;border:none;border-radius:3px;cursor:not-allowed">Revert</button>';
html += '</div></div>';
html += '</form>';
html += '<script>';
html += '(function(){';
html += '  var form = document.getElementById("configForm");';
html += '  var backup = localStorage.getItem("retailerConfigBackup");';
html += '  if (!backup) {';
html += '    var fd = new FormData(form);';
html += '    var params = new URLSearchParams(fd);';
html += '    for (var k of fd.keys()) { if (k.indexOf("del_") === 0) fd.delete(k); }';
html += '    backup = new URLSearchParams(fd).toString();';
html += '    localStorage.setItem("retailerConfigBackup", backup);';
html += '  }';
html += '  document.getElementById("previousCsv").value = backup;';
html += '  document.getElementById("confirmRevert").addEventListener("change", function() { document.getElementById("revertBtn").disabled = !this.checked; });';
html += '  form.addEventListener("submit", function(e) {';
html += '    e.preventDefault();';
html += '    var fd = new FormData(form);';
html += '    for (var k of fd.keys()) { if (k.indexOf("del_") === 0) fd.delete(k); }';
html += '    var dels = [];';
html += '    document.querySelectorAll(".del-check:checked").forEach(function(cb) { dels.push(cb.name.replace("del_","")); });';
html += '    var fd2 = new FormData(form);';
html += '    for (var k of fd2.keys()) { if (k.indexOf("del_") === 0) fd2.delete(k); }';
html += '    fd2.append("deleteRows", dels.join(","));';
html += '    fetch("/endpoint/api/retailer-config/save", {method:"POST",headers:{"Content-Type":"application/x-www-form-urlencoded"},body:new URLSearchParams(fd2).toString()}).then(function(r) {';
html += '      if (r.ok) { window.location.reload(); }';
html += '      else { r.text().then(function(t) { alert("Save failed: " + t); }); }';
html += '    });';
html += '  });';
html += '})();';
html += 'function revertConfig() { if (!document.getElementById("confirmRevert").checked) { alert("Tick the checkbox first to confirm revert."); return; }';
html += '  var backup = localStorage.getItem("retailerConfigBackup");';
html += '  if (!backup) { alert("No previous config to revert to."); return; }';
html += '  if (!confirm("Revert to previous configuration? Unsaved changes will be lost.")) return;';
html += '  fetch("/endpoint/api/retailer-config/save", {method:"POST",headers:{"Content-Type":"application/x-www-form-urlencoded"},body:backup}).then(function(r) {';
html += '    if (r.ok) { localStorage.removeItem("retailerConfigBackup"); window.location.reload(); }';
html += '    else { r.text().then(function(t) { alert("Revert failed: " + t); }); }';
html += '  });';
html += '}';
html += '</script>';

msg.payload = html;
msg.statusCode = 200;
msg.headers = { 'Content-Type': 'text/html; charset=utf-8', 'Access-Control-Allow-Origin': '*' };
node.send(msg);
