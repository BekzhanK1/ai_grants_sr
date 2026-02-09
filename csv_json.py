from csv import DictReader
import json
import os

csv_files = ['admin_group_tab.csv', 'admin_menu_tab.csv', 'admin_grant_tab.csv']
csv_dir = 'csv'
json_dir = 'json'

if not os.path.exists(json_dir):
    os.makedirs(json_dir)

for csv_file in csv_files:
    with open(os.path.join(csv_dir, csv_file), 'r') as f:
        reader = DictReader(f)
        data = []
        for row in reader:    
            data.append(row)

    with open(os.path.join(json_dir, csv_file.replace('.csv', '.json')), 'w') as f:
        json.dump(data, f, indent=4)