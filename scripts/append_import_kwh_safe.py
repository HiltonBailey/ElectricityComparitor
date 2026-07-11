import csv
import os

def append_import_kwh_safe(filepath):
    temp_filepath = filepath + ".tmp"
    try:
        with open(filepath, 'r', newline='') as f_in, \
             open(temp_filepath, 'w', newline='') as f_out:
            reader = csv.reader(f_in)
            writer = csv.writer(f_out)
            
            header = next(reader)
            if 'Import_kWh' in header:
                print(f"File {filepath} already has Import_kWh column. Skipping.")
                return

            header.append('Import_kWh')
            writer.writerow(header)
            
            for row in reader:
                # Assuming index 1: offpeak, 2: shoulder, 3: peak
                try:
                    # Handle 'unavailable' or other non-numeric values
                    val1 = float(row[1]) if row[1] not in ['unavailable', ''] else 0.0
                    val2 = float(row[2]) if row[2] not in ['unavailable', ''] else 0.0
                    val3 = float(row[3]) if row[3] not in ['unavailable', ''] else 0.0
                    total = val1 + val2 + val3
                    row.append(f"{total:.3f}")
                except (ValueError, IndexError):
                    row.append('0.000') # Default for malformed rows
                
                writer.writerow(row)
        
        # Replace original file with updated one
        os.replace(temp_filepath, filepath)
        print(f"Successfully appended Import_kWh to {filepath}")
        
    except Exception as e:
        print(f"Error processing {filepath}: {e}")
        if os.path.exists(temp_filepath):
            os.remove(temp_filepath)

if __name__ == "__main__":
    # Files to update
    files = ['5minelecNEW.csv', 'newseed.csv', 'merged_newseed.csv']
    for f in files:
        if os.path.exists(f):
            append_import_kwh_safe(f)
        else:
            print(f"File {f} not found.")
