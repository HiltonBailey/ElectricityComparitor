import os
import foxesscloud.foxesscloud as fox
import json
from datetime import datetime

fox.username = os.environ.get("FOXESS_USER")
fox.password = os.environ.get("FOXESS_PASS")
fox.get_token()

VARS = ['generationPower']
data = fox.get_raw(time_span='day', d=datetime.now().strftime("%Y-%m-%d"), v=VARS)
print(json.dumps(data, indent=2))
