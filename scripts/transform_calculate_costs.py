import json
import re

FILE = '/Users/hiltondbailey/repos/ElectricityComparitor/node_red_flow.json'

with open(FILE, 'r') as f:
    data = json.load(f)

for node in data:
    if isinstance(node, dict) and node.get('id') == 'calculate_costs':
        func = node['func']
        lines = func.split('\n')
        
        # Track changes
        changes = []
        
        new_lines = []
        prev_cum_import = 0
        in_raw_rows = False
        
        for i, line in enumerate(lines):
            original = line
            
            # --- 1. Raw row parsing: replace cumOff/cumSh/cumPk with cumKwh ---
            if 'cumOff: parseFloat(row[1]) || 0,' in line and 'cumSh: parseFloat(row[2]) || 0,' in line and 'cumPk: parseFloat(row[3]) || 0,' in line:
                # Already handled, will be replaced below
                pass
            
            if line.strip().startswith('cumOff: parseFloat(row[1]) || 0,'):
                new_lines.append('            cumKwh: (parseFloat(row[14]) || 0),')
                changes.append(f'Line {i}: replaced cumOff with cumKwh')
                continue
            
            if line.strip().startswith('cumSh: parseFloat(row[2]) || 0,'):
                changes.append(f'Line {i}: removed cumSh')
                continue  # skip
            
            if line.strip().startswith('cumPk: parseFloat(row[3]) || 0,'):
                changes.append(f'Line {i}: removed cumPk')
                continue  # skip
            
            # --- 2. Threshold check (sum of cum deltas) ---
            if 'curr.cumOff + curr.cumSh + curr.cumPk - prev.cumOff - prev.cumSh - prev.cumPk < 0.01' in line:
                new_line = line.replace(
                    'curr.cumOff + curr.cumSh + curr.cumPk - prev.cumOff - prev.cumSh - prev.cumPk < 0.01',
                    'curr.cumKwh - prev.cumKwh < 0.001'
                )
                new_lines.append(new_line)
                changes.append(f'Line {i}: simplified delta check')
                continue
            
            # --- 3. deltaTot calculation ---
            if 'var deltaTot = Math.max(0, (curr.cumOff - prev.cumOff) + (curr.cumSh - prev.cumSh) + (curr.cumPk - prev.cumPk));' in line:
                new_line = line.replace(
                    'var deltaTot = Math.max(0, (curr.cumOff - prev.cumOff) + (curr.cumSh - prev.cumSh) + (curr.cumPk - prev.cumPk));',
                    'var deltaTot = Math.max(0, curr.cumKwh - prev.cumKwh);'
                )
                new_lines.append(new_line)
                changes.append(f'Line {i}: simplified deltaTot')
                continue
            
            # --- 4. Gap variable assignments ---
            if 'var gapOff = curr.cumOff - firstRow.cumOff;' in line:
                new_lines.append('var gapKwh = curr.cumKwh - firstRow.cumKwh;')
                changes.append(f'Line {i}: replaced gapOff with gapKwh')
                continue
            
            if 'var gapSh = curr.cumSh - firstRow.cumSh;' in line:
                changes.append(f'Line {i}: removed gapSh')
                continue  # skip
            
            if 'var gapPk = curr.cumPk - firstRow.cumPk;' in line:
                changes.append(f'Line {i}: removed gapPk')
                continue  # skip
            
            # --- 5. sCumOff/Sh/Pk assignments ---
            if 'var sCumOff = firstRow.cumOff + gapOff * sj / numSlots;' in line:
                new_line = line.replace(
                    'var sCumOff = firstRow.cumOff + gapOff * sj / numSlots;',
                    'var sCumKwh = firstRow.cumKwh + gapKwh * sj / numSlots;'
                )
                new_lines.append(new_line)
                changes.append(f'Line {i}: replaced sCumOff with sCumKwh')
                continue
            
            if 'var sCumSh = firstRow.cumSh + gapSh * sj / numSlots;' in line:
                changes.append(f'Line {i}: removed sCumSh')
                continue  # skip
            
            if 'var sCumPk = firstRow.cumPk + gapPk * sj / numSlots;' in line:
                changes.append(f'Line {i}: removed sCumPk')
                continue  # skip
            
            # --- 6. Jump variables ---
            m = re.match(r'^(\s*)var jumpOff = curr\.cumOff - prev\.cumOff;(.*)', line)
            if m:
                new_lines.append(m.group(1) + 'var jumpKwh = curr.cumKwh - prev.cumKwh;' + m.group(2))
                changes.append(f'Line {i}: replaced jumpOff with jumpKwh')
                continue
            
            # Remove jumpSh/jumpPk lines that follow
            if line.strip().startswith('var jumpSh = curr.cumSh - prev.cumSh;') or \
               line.strip().startswith('var jumpPk = curr.cumPk - prev.cumPk;'):
                changes.append(f'Line {i}: removed jumpSh/jumpPk')
                continue  # skip
            
            # --- 7. Synthetic row cumOff/cumSh/cumPk ---
            if 'cumOff: prev.cumOff + (jumpOff * sj / numSlots2),' in line:
                new_line = line.replace(
                    'cumOff: prev.cumOff + (jumpOff * sj / numSlots2),',
                    'cumKwh: prev.cumKwh + (jumpKwh * sj / numSlots2),'
                )
                new_lines.append(new_line)
                changes.append(f'Line {i}: synthetic row cumOff->cumKwh')
                continue
            
            if 'cumSh: prev.cumSh + (jumpSh * sj / numSlots2),' in line or \
               'cumPk: prev.cumPk + (jumpPk * sj / numSlots2),' in line:
                changes.append(f'Line {i}: removed synth cumSh/cumPk')
                continue  # skip
            
            # --- 8. Plateau redistribution ---
            if 'cumOff: prev.cumOff + (jumpOff * frac),' in line:
                new_line = line.replace(
                    'cumOff: prev.cumOff + (jumpOff * frac),',
                    'cumKwh: prev.cumKwh + (jumpKwh * frac),'
                )
                new_lines.append(new_line)
                changes.append(f'Line {i}: plateau cumOff->cumKwh')
                continue
            
            if 'cumSh: prev.cumSh + (jumpSh * frac),' in line or \
               'cumPk: prev.cumPk + (jumpPk * frac),' in line:
                changes.append(f'Line {i}: removed plateau cumSh/cumPk')
                continue  # skip
            
            # --- 9. GapOff/Sh/Pk gap-fill ---
            if 'var gapOff = curr.cumOff - prev.cumOff;' in line and 'jumpOff' not in line:
                new_line = line.replace(
                    'var gapOff = curr.cumOff - prev.cumOff;',
                    'var gapKwh = curr.cumKwh - prev.cumKwh;'
                )
                new_lines.append(new_line)
                changes.append(f'Line {i}: gap-fill gapOff->gapKwh')
                continue
            
            if line.strip().startswith('var gapSh = curr.cumSh - prev.cumSh;') or \
               line.strip().startswith('var gapPk = curr.cumPk - prev.cumPk;'):
                changes.append(f'Line {i}: removed gap-fill gapSh/gapPk')
                continue  # skip
            
            if 'cumOff: prev.cumOff + (gapOff * sj / numSlots),' in line:
                new_line = line.replace(
                    'cumOff: prev.cumOff + (gapOff * sj / numSlots),',
                    'cumKwh: prev.cumKwh + (gapKwh * sj / numSlots),'
                )
                new_lines.append(new_line)
                changes.append(f'Line {i}: gap-fill cumOff->cumKwh')
                continue
            
            if 'cumSh: prev.cumSh + (gapSh * sj / numSlots),' in line or \
               'cumPk: prev.cumPk + (gapPk * sj / numSlots),' in line:
                changes.append(f'Line {i}: removed gap-fill cumSh/cumPk')
                continue  # skip
            
            # --- 10. BP calculation section ---
            if 'const cumOff = e.cumOff;' in line and 'cumSh' not in lines[i+1:i+4]:
                new_line = line.replace(
                    'const cumOff = e.cumOff;',
                    'const cumKwh = e.cumKwh;'
                )
                new_lines.append(new_line)
                changes.append(f'Line {i}: BP cumOff->cumKwh')
                continue
            
            if line.strip() == 'const cumSh = e.cumSh;' or line.strip() == 'const cumPk = e.cumPk;':
                changes.append(f'Line {i}: removed BP cumSh/cumPk')
                continue  # skip
            
            if 'const importKwh = Math.max(0, (cumOff - bpPrevOff) + (cumSh - bpPrevSh) + (cumPk - bpPrevPk));' in line:
                new_line = line.replace(
                    'const importKwh = Math.max(0, (cumOff - bpPrevOff) + (cumSh - bpPrevSh) + (cumPk - bpPrevPk));',
                    'const importKwh = Math.max(0, cumKwh - bpPrevKwh);'
                )
                new_lines.append(new_line)
                changes.append(f'Line {i}: BP importKwh from cumKwh')
                continue
            
            if 'bpPrevOff = cumOff; bpPrevSh = cumSh; bpPrevPk = cumPk;' in line:
                new_line = line.replace(
                    'bpPrevOff = cumOff; bpPrevSh = cumSh; bpPrevPk = cumPk;',
                    'bpPrevKwh = cumKwh;'
                )
                new_lines.append(new_line)
                changes.append(f'Line {i}: BP prev tracking')
                continue
            
            # Variable declarations for prev values
            if 'var bpPrevOff=0,bpPrevSh=0,bpPrevPk=0;' in line:
                new_line = line.replace(
                    'var bpPrevOff=0,bpPrevSh=0,bpPrevPk=0;',
                    'var bpPrevKwh=0;'
                )
                new_lines.append(new_line)
                changes.append(f'Line {i}: BP prev var')
                continue
            
            # --- 11. Daily detail section ---
            if 'const cumOffpeak = e.cumOff;' in line:
                new_line = line.replace(
                    'const cumOffpeak = e.cumOff;',
                    'const cumKwh = e.cumKwh;'
                )
                new_lines.append(new_line)
                changes.append(f'Line {i}: detail cumOffpeak->cumKwh')
                continue
            
            if 'const cumShoulder = e.cumSh;' in line or 'const cumPeak = e.cumPk;' in line:
                changes.append(f'Line {i}: removed detail cumSh/cumPk')
                continue  # skip
            
            if 'const offpeakKwh = Math.max(0, cumOffpeak - prevOffpeak);' in line:
                new_line = line.replace(
                    'const offpeakKwh = Math.max(0, cumOffpeak - prevOffpeak);',
                    'const kwh = Math.max(0, cumKwh - prevKwh);'
                )
                new_lines.append(new_line)
                changes.append(f'Line {i}: detail kwh from cumKwh')
                continue
            
            if 'const shoulderKwh = Math.max(0, cumShoulder - prevShoulder);' in line or \
               'const peakKwh = Math.max(0, cumPeak - prevPeak);' in line:
                changes.append(f'Line {i}: removed detail sh/pk kwh')
                continue  # skip
            
            if 'prevOffpeak = cumOffpeak; prevShoulder = cumShoulder; prevPeak = cumPeak; prevExport = cumExport;' in line:
                new_line = line.replace(
                    'prevOffpeak = cumOffpeak; prevShoulder = cumShoulder; prevPeak = cumPeak; prevExport = cumExport;',
                    'prevKwh = cumKwh; prevExport = cumExport;'
                )
                new_lines.append(new_line)
                changes.append(f'Line {i}: detail prev tracking')
                continue
            
            # --- 12. Globird section ---
            if 'var impOff=0,impSh=0,impPk=0;' in line:
                new_line = 'var impKwh=0,impOff=0,impSh=0,impPk=0;'
                new_lines.append(new_line)
                changes.append(f'Line {i}: globird add impKwh')
                continue
            
            if 'var iKwh=Math.max(0,(e.cumOff-pOff)+(e.cumSh-pSh)+(e.cumPk-pPk));' in line:
                new_line = line.replace(
                    'var iKwh=Math.max(0,(e.cumOff-pOff)+(e.cumSh-pSh)+(e.cumPk-pPk));',
                    'var iKwh=Math.max(0,e.cumKwh-pKwh);'
                )
                new_lines.append(new_line)
                changes.append(f'Line {i}: globird iKwh from cumKwh')
                continue
            
            if 'if (h>=globirdR.off_s && h<globirdR.off_e) impOff+=iKwh;' in line:
                new_line = '            impKwh += iKwh;'
                new_lines.append(new_line)
                changes.append(f'Line {i}: globird use impKwh')
                continue
            
            if 'else if (h>=globirdR.pk_s && h<globirdR.pk_e) impPk+=iKwh;' in line or \
               'else impSh+=iKwh;' in line:
                changes.append(f'Line {i}: removed globird period branches')
                continue  # skip
            
            if 'if (h>=18 && h<21) { evenImp+=Math.max(0,(e.cumOff-prevEvOff)+(e.cumSh-prevEvSh)+(e.cumPk-prevEvPk)); }' in line:
                new_line = line.replace(
                    'if (h>=18 && h<21) { evenImp+=Math.max(0,(e.cumOff-prevEvOff)+(e.cumSh-prevEvSh)+(e.cumPk-prevEvPk)); }',
                    'if (h>=18 && h<21) { evenImp+=Math.max(0,e.cumKwh-prevEvKwh); }'
                )
                new_lines.append(new_line)
                changes.append(f'Line {i}: globird evenImp')
                continue
            
            if 'pOff=e.cumOff;pSh=e.cumSh;pPk=e.cumPk;pExp=e.cumExp;' in line:
                new_line = line.replace(
                    'pOff=e.cumOff;pSh=e.cumSh;pPk=e.cumPk;pExp=e.cumExp;',
                    'pKwh=e.cumKwh;pExp=e.cumExp;'
                )
                new_lines.append(new_line)
                changes.append(f'Line {i}: removed pOff/pSh/pPk')
                continue
            
            if 'prevEvOff=e.cumOff;prevEvSh=e.cumSh;prevEvPk=e.cumPk;' in line:
                new_line = line.replace(
                    'prevEvOff=e.cumOff;prevEvSh=e.cumSh;prevEvPk=e.cumPk;',
                    'prevEvKwh=e.cumKwh;'
                )
                new_lines.append(new_line)
                changes.append(f'Line {i}: globird prevEv')
                continue
            
            if 'var impCost=impOff*globirdR.off_pk+impSh*globirdR.sh_pk+impPk*globirdR.pk_pk;' in line:
                new_line = line.replace(
                    'var impCost=impOff*globirdR.off_pk+impSh*globirdR.sh_pk+impPk*globirdR.pk_pk;',
                    'var impCost=impKwh*globirdR.sh_pk;'
                )
                new_lines.append(new_line)
                changes.append(f'Line {i}: globird impCost')
                continue
            
            # --- 13. Other fixed_tou section ---
            if 'const cumOff = e.cumOff;' in line and 'cumSh' in lines[i+1:i+4]:
                new_line = line.replace(
                    'const cumOff = e.cumOff;',
                    'const cumKwh = e.cumKwh;'
                )
                new_lines.append(new_line)
                changes.append(f'Line {i}: other cumOff->cumKwh')
                continue
            
            if re.match(r'^\s*const cumSh = e\.cumSh;$', line) or \
               re.match(r'^\s*const cumPk = e\.cumPk;$', line):
                changes.append(f'Line {i}: removed other cumSh/cumPk')
                continue  # skip
            
            if 'const oKwh = Math.max(0, cumOff - prevOff);' in line:
                new_line = line.replace(
                    'const oKwh = Math.max(0, cumOff - prevOff);',
                    'const kwh = Math.max(0, cumKwh - prevKwh);'
                )
                new_lines.append(new_line)
                changes.append(f'Line {i}: other oKwh->kwh')
                continue
            
            if 'const sKwh = Math.max(0, cumSh - prevSh);' in line or \
               'const pKwh = Math.max(0, cumPk - prevPk);' in line:
                changes.append(f'Line {i}: removed other sKwh/pKwh')
                continue  # skip
            
            if 'prevOff = cumOff; prevSh = cumSh; prevPk = cumPk; prevExport = cumExp;' in line:
                new_line = line.replace(
                    'prevOff = cumOff; prevSh = cumSh; prevPk = cumPk; prevExport = cumExp;',
                    'prevKwh = cumKwh; prevExport = cumExp;'
                )
                new_lines.append(new_line)
                changes.append(f'Line {i}: other prev tracking')
                continue
            
            # --- 14. CSV write-back ---
            if 'parts[1] = rawRows[rr].cumOff.toFixed(3);' in line:
                new_line = 'parts[14] = rawRows[rr].cumKwh.toFixed(3);'
                new_lines.append(new_line)
                changes.append(f'Line {i}: write-back cumKwh')
                continue
            
            if 'parts[2] = rawRows[rr].cumSh.toFixed(3);' in line or \
               'parts[3] = rawRows[rr].cumPk.toFixed(3);' in line:
                changes.append(f'Line {i}: removed write-back sh/pk')
                continue  # skip
            
            # --- 15. Variable declarations ---
            if 'var prevOff=0,prevSh=0,prevPk=0,prevExp=0;' in line:
                new_line = 'var prevKwh=0,prevExp=0;'
                new_lines.append(new_line)
                changes.append(f'Line {i}: prevOff/Sh/Pk -> prevKwh')
                continue
            
            if 'var prevOffpeak=0,prevShoulder=0,prevPeak=0,prevExport=0;' in line:
                new_line = 'var prevKwh=0,prevExport=0;'
                new_lines.append(new_line)
                changes.append(f'Line {i}: prevOffpeak/etc -> prevKwh')
                continue
            
            # --- 16. rawRows object look-ahead reference ---
            # (rawRows[ri - lookback - 1].cumOff - rawRows[ri - lookback - 2].cumOff) + (rawRows[ri - lookback - 1].cumSh - rawRows[ri - lookback - 2].cumSh) + (rawRows[ri - lookback - 1].cumPk
            if re.search(r'rawRows\[ri - lookback - 1\]\.cumOff - rawRows\[ri - lookback - 2\]\.cumOff\)', line):
                pattern = r'Math\.max\(0, \(rawRows\[ri - lookback - 1\]\.cumOff - rawRows\[ri - lookback - 2\]\.cumOff\) \+ \(rawRows\[ri - lookback - 1\]\.cumSh - rawRows\[ri - lookback - 2\]\.cumSh\) \+ \(rawRows\[ri - lookback - 1\]\.cumPk'
                replacement = 'Math.max(0, rawRows[ri - lookback - 1].cumKwh - rawRows[ri - lookback - 2].cumKwh'
                new_line = re.sub(pattern, replacement, line)
                new_lines.append(new_line)
                changes.append(f'Line {i}: plateau detection using cumKwh')
                continue
            
            # Default: keep the line
            new_lines.append(line)
        
        func = '\n'.join(new_lines)
        node['func'] = func
        print(f"calculate_costs: {len(changes)} changes made")
        for c in changes:
            print(f"  {c}")
        break

with open(FILE, 'w') as f:
    json.dump(data, f, indent=2)
print(f"Written to {FILE}")
