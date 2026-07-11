import os
import csv
import foxesscloud.foxesscloud as fox
from datetime import datetime, timedelta

# Configuration - uses environment variables set in the shell
fox.username = os.environ.get("FOXESS_USER")
fox.password = os.environ.get("FOXESS_PASS")
OUTPUT_FILE = "foxess_full_history.csv"

# Variables identified from your discover_vars.py run
VARS = [
    'gridConsumptionPower', 'feedinPower', 'batChargePower', 
    'batDischargePower', 'loadsPower', 'generationPower'
]

def fetch_all():
    if not fox.username or not fox.password:
        print("Error: FOXESS_USER and FOXESS_PASS environment variables not set.")
        return

    if not fox.get_token():
        print("Login failed")
        return

    # Adjust start_date to your inception date
    start_date = datetime(2025, 12, 1) 
    end_date = datetime.now()
    
    # Prepare CSV file
    with open(OUTPUT_FILE, 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['timestamp', 'variable', 'value'])
        
        current = start_date
        while current <= end_date:
            d_str = current.strftime("%Y-%m-%d")
            print(f"Fetching {d_str}...")
            try:
                # Returns list of variable objects
                raw_data = fox.get_raw(time_span='day', d=d_str, v=VARS)
                
                if raw_data:
                    for var_obj in raw_data:
                        var_name = var_obj['variable']
                        for entry in var_obj['data']:
                            writer.writerow([entry['time'], var_name, entry['value']])
                            
            except Exception as e:
                print(f"Error {d_str}: {e}")
            current += timedelta(days=1)
    print(f"Done! Data saved to {OUTPUT_FILE}")

if __name__ == "__main__":
    fetch_all()
