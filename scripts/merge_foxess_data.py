import pandas as pd
import os

# --- PATHS ---
FOX_LONG = 'foxess_full_history.csv'
NEWSEED = 'newseed.csv'
OUTPUT = 'merged_newseed.csv'

def merge_data():
    print("Reading files...")
    # 1. Read and Pivot FoxESS Data
    df_long = pd.read_csv(FOX_LONG)
    # Convert timestamp to datetime objects, handling the timezone abbreviation
    df_long['timestamp'] = df_long['timestamp'].str.replace(r' [A-Z]+', '', regex=True)
    df_long['timestamp'] = pd.to_datetime(df_long['timestamp'], utc=True)
    
    # Pivot: Index by timestamp, columns by variable, values by value
    df_wide = df_long.pivot_table(index='timestamp', columns='variable', values='value').reset_index()
    
    # Rename columns to match newseed structure where possible
    # FoxESS vars: gridConsumptionPower, feedinPower, batChargePower, batDischargePower, loadsPower, generationPower
    # Newseed cols: datetime, offpeak, shoulder, peak, export, bat_charge, bat_charge2, bat_discharge, house_load, gen_price, fit_price, aemo_price, pe_datetime, solar_gen
    
    mapping = {
        'gridConsumptionPower': 'offpeak', # Needs refined mapping to TOU periods
        'feedinPower': 'export',
        'batChargePower': 'bat_charge',
        'batDischargePower': 'bat_discharge',
        'loadsPower': 'house_load',
        'generationPower': 'solar_gen'
    }
    df_wide = df_wide.rename(columns=mapping)
    
    # 2. Read newseed.csv
    df_seed = pd.read_csv(NEWSEED)
    df_seed['datetime'] = pd.to_datetime(df_seed['datetime'], utc=True)
    
    # 3. Merge Strategy:
    # Concatenate new wide data and seed data
    # Filter FoxESS data to only include dates before 2025-12-19
    cutoff = pd.Timestamp('2025-12-19', tz='UTC')
    df_new_part = df_wide[df_wide['timestamp'] < cutoff].copy()
    df_new_part['datetime'] = df_new_part['timestamp']
    
    # Combine
    combined = pd.concat([df_new_part, df_seed], ignore_index=True)
    
    # 4. Save
    combined.to_csv(OUTPUT, index=False)
    print(f"Successfully created {OUTPUT}")

if __name__ == "__main__":
    merge_data()
