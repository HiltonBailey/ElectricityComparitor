import os
import csv
import sys
from datetime import datetime, timedelta
from foxesscloud.foxesscloud import FoxESS

# --- CONFIGURATION ---
# Assumes credentials are set in the environment:
# export FOXESS_USER='your_username'
# export FOXESS_PASS='your_password'
# export FOXESS_DEVICE_SN='your_device_sn'

USERNAME = os.environ.get("FOXESS_USER")
PASSWORD = os.environ.get("FOXESS_PASS")
DEVICE_SN = os.environ.get("FOXESS_DEVICE_SN")
OUTPUT_FILE = "foxess_historical_export.csv"

def get_all_data():
    if not all([USERNAME, PASSWORD, DEVICE_SN]):
        print("Error: FOXESS_USER, FOXESS_PASS, and FOXESS_DEVICE_SN environment variables must be set.")
        sys.exit(1)

    # 1. Authenticate
    client = FoxESS(USERNAME, PASSWORD)
    # Depending on the library version, authentication might be implicit or explicit
    # client.login() 

    # 2. Define range (e.g., from 2023-01-01 to today)
    # Adjust the start_date to your actual inception date
    start_date = datetime(2023, 1, 1) 
    end_date = datetime.now()
    
    all_data = []
    
    # 3. Loop in manageable chunks (e.g., 30 days at a time)
    current_start = start_date
    while current_start < end_date:
        current_end = min(current_start + timedelta(days=30), end_date)
        print(f"Fetching data from {current_start.date()} to {current_end.date()}...")
        
        # --- TODO: Replace with the actual API call for date range ---
        # Example structure:
        # chunk_data = client.get_report(
        #     device_sn=DEVICE_SN, 
        #     start=current_start, 
        #     end=current_end
        # )
        chunk_data = [] 
        
        all_data.extend(chunk_data)
        current_start = current_end + timedelta(days=1)

    # 4. Write to CSV
    fields = [
        'period_ending', 'offpeak', 'shoulder', 'peak', 'export', 
        'Bat_Charge_Energy', 'Bat_Discharge_Energy', 'house_load', 'solar_gen'
    ]
    
    try:
        with open(OUTPUT_FILE, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            writer.writeheader()
            
            for row in all_data:
                # Ensure mapping matches library output structure
                writer.writerow({
                    'period_ending': row.get('time'),
                    'offpeak': row.get('offpeak'),
                    'shoulder': row.get('shoulder'),
                    'peak': row.get('peak'),
                    'export': row.get('export'),
                    'Bat_Charge_Energy': row.get('bat_charge'),
                    'Bat_Discharge_Energy': row.get('bat_discharge'),
                    'house_load': row.get('load'),
                    'solar_gen': row.get('solar')
                })
        print(f"Successfully wrote {len(all_data)} rows to {OUTPUT_FILE}")
        
    except Exception as e:
        print(f"Error writing CSV: {e}")

if __name__ == "__main__":
    get_all_data()
