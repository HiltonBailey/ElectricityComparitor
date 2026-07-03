#!/usr/bin/env python3
import re
from pathlib import Path
p=Path('node_red_flow.json')
if not p.exists():
    print('File not found:',p)
    raise SystemExit(1)
text=p.read_text()
orig=text
# Make a backup
bk=p.with_suffix('.json.bak')
if not bk.exists():
    bk.write_text(orig)

# Patterns to replace: split('\n'), split("\n"), join('\n'), join("\n")
text=re.sub(r"split\(\\'\\n\\'\)", "split(String.fromCharCode(10))", text)
text=re.sub(r'split\(\\"\\n\\"\)', 'split(String.fromCharCode(10))', text)
text=re.sub(r"join\(\\'\\n\\'\)", "join(String.fromCharCode(10))", text)
text=re.sub(r'join\(\\"\\n\\"\)', 'join(String.fromCharCode(10))', text)

# Also handle msg.payload = 'OK\\n' etc - skip replacing those
if text==orig:
    print('No replacements made')
else:
    p.write_text(text)
    print('Replacements applied; backup at',bk)
    # report remaining occurrences
    rem = []
    if "split('\\n')" in text or 'split("\\n")' in text:
        rem.append('split')
    if "join('\\n')" in text or 'join("\\n")' in text:
        rem.append('join')
    print('Remaining patterns:', rem)
