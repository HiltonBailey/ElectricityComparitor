import csv
import sys
import os
import shutil

def transform_csv(input_path, output_path):
    # Backup
    backup_path = input_path + ".bak"
    shutil.copy2(input_path, backup_path)
    print(f"Backup created at {backup_path}")

    with open(input_path, 'r', newline='') as infile, \
         open(output_path, 'w', newline='') as outfile:
        
        reader = csv.reader(infile)
        writer = csv.writer(outfile)
        
        # Process header
        header = next(reader)
        # Verify indices (indices 1, 2, 3 should be offpeak, shoulder, peak)
        print(f"Original header: {header}")
        
        new_header = [header[0], 'import_kwh'] + header[4:]
        writer.writerow(new_header)
        print(f"New header: {new_header}")
        
        for row in reader:
            if not row: continue
            # Sum columns 1, 2, 3
            try:
                import_kwh = float(row[1]) + float(row[2]) + float(row[3])
                new_row = [row[0], f"{import_kwh:.4f}"] + row[4:]
                writer.writerow(new_row)
            except ValueError as e:
                print(f"Error processing row {row}: {e}")
                continue
    
    # Replace input with output
    shutil.move(output_path, input_path)
    print(f"Transformation complete for {input_path}")

# Run for both files
transform_csv("/Users/hiltondbailey/repos/ElectricityComparitor/newseed.csv", "/Users/hiltondbailey/repos/ElectricityComparitor/newseed.csv.new")
transform_csv("/Volumes/share/file_notifications/5minelecNEW.csv", "/Volumes/share/file_notifications/5minelecNEW.csv.new")
