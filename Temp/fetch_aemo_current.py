#!/usr/bin/env python3
"""Fetch AEMO NSW1 5-min RRP prices from the CURRENT DispatchIS directory
for recent dates not yet in the ARCHIVE (published with ~2-3 day delay),
and update the aemo_price column of a target CSV.

Usage: python3 fetch_aemo_current.py [--csv <path>] [--dates 20260730,20260731]
"""
import csv
import os
import re
import subprocess
import sys
import zipfile
import io
from collections import defaultdict
from datetime import datetime

CURRENT_BASE = "https://nemweb.com.au/Reports/CURRENT/DispatchIS_Reports/"
TARGET_CSV = "/Users/hiltondbailey/repos/ElectricityComparitor/5minelecNEW.csv"
CACHE_DIR = "/tmp/aemo_cache"


def list_current_files():
    out = subprocess.run(["curl", "-skL", "--max-time", "60", CURRENT_BASE],
                         capture_output=True, text=True)
    files = re.findall(r'PUBLIC_DISPATCHIS_(\d{8})(\d{4})_(\d+)\.zip', out.stdout)
    return {(y, h, s): f"PUBLIC_DISPATCHIS_{y}{h}_{s}.zip" for y, h, s in files}


def download_current(name):
    cache_path = os.path.join(CACHE_DIR, name)
    if os.path.exists(cache_path) and os.path.getsize(cache_path) > 1000:
        return cache_path
    url = CURRENT_BASE + name
    subprocess.run(["curl", "-skL", "--max-time", "30", url, "-o", cache_path],
                   capture_output=True, timeout=60)
    return cache_path if os.path.exists(cache_path) and os.path.getsize(cache_path) > 1000 else None


def extract_nsw1_prices_from_zip(zip_path):
    prices = {}
    try:
        with zipfile.ZipFile(zip_path) as z:
            csv_names = [n for n in z.namelist() if n.endswith(".CSV")]
            if not csv_names:
                return prices
            content = z.read(csv_names[0]).decode("utf-8", errors="replace")
        for line in content.splitlines():
            if line.startswith("D,DISPATCH,PRICE"):
                parts = line.split(",")
                if len(parts) > 9 and parts[6].strip() == "NSW1":
                    try:
                        dt = datetime.strptime(parts[4].strip().strip('"'), "%Y/%m/%d %H:%M:%S")
                        rrp = float(parts[9].strip())
                        prices[dt] = rrp
                    except (ValueError, IndexError):
                        continue
    except Exception:
        pass
    return prices


def update_aemo_current(target_csv, dates):
    files = list_current_files()
    wanted = set(dates)
    all_prices = {}
    for (y, h, s), name in sorted(files.items()):
        if y not in wanted:
            continue
        p = download_current(name)
        if not p:
            continue
        day_prices = extract_nsw1_prices_from_zip(p)
        all_prices.update(day_prices)
    print(f"Total NSW1 prices fetched for {wanted}: {len(all_prices)}")

    rows_updated = 0
    rows_not_found = 0
    rows = []
    with open(target_csv, newline="") as f:
        reader = csv.reader(f)
        header = next(reader)
        rows.append(header)
        dt_col = header.index("pe_datetime")
        aemo_col = header.index("aemo_price")
        for row in reader:
            dt_str = row[dt_col].strip()
            if not dt_str:
                rows.append(row)
                continue
            dt = datetime.strptime(dt_str, "%Y-%m-%d %H:%M:%S")
            if dt.strftime("%Y%m%d") not in wanted:
                rows.append(row)
                continue
            p = all_prices.get(dt)
            if p is not None:
                new_val = round(p / 1000.0, 4)
                old_val = row[aemo_col].strip()
                try:
                    old_f = float(old_val)
                except ValueError:
                    old_f = 0.0
                if abs(old_f - new_val) > 0.00005:
                    row[aemo_col] = str(new_val)
                    rows_updated += 1
            else:
                rows_not_found += 1
            rows.append(row)

    with open(target_csv, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerows(rows)

    print(f"Rows updated: {rows_updated}")
    print(f"Rows with no match: {rows_not_found}")
    return rows_updated


def main():
    target = TARGET_CSV
    dates = ["20260730", "20260731"]
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--csv" and i + 1 < len(args):
            target = args[i + 1]; i += 2
        elif args[i] == "--dates" and i + 1 < len(args):
            dates = args[i + 1].split(","); i += 2
        else:
            i += 1
    update_aemo_current(target, dates)


if __name__ == "__main__":
    main()
