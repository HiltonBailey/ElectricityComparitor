import pandas as pd
import sys

def append_import_kwh(filepath):
    try:
        df = pd.read_csv(filepath)
        
        # Calculate sum
        # Assuming these columns are numeric.
        df['import_kWh'] = df['offpeak'] + df['shoulder'] + df['peak']
        
        # Save back to same file
        df.to_csv(filepath, index=False)
        print(f"Successfully appended import_kWh to {filepath}")
    except Exception as e:
        print(f"Error processing {filepath}: {e}")

if __name__ == "__main__":
    files = ['5minelecNEW.csv', 'newseed.csv']
    for f in files:
        append_import_kwh(f)
