from json import load
import os

json_dir = 'json'

from tabulate import tabulate

for file in os.listdir(json_dir):
    with open(os.path.join(json_dir, file), 'r') as f:
        data = load(f)
        if isinstance(data, list) and len(data) > 0 and isinstance(data[0], dict):
            print(f"\nTable view for {file}:")
            print(tabulate(data, headers="keys", tablefmt="grid"))
        else:
            print(f"\n{file}:")
            print(data)