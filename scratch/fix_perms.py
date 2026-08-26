import re
from pathlib import Path

content = Path("src/blockhost_backend/api/servers.py").read_text()

# We will just write a small parser that finds 'def ' above each _get_server_for_user call
lines = content.split('\n')
for i, line in enumerate(lines):
    if "_get_server_for_user(server_id, user, db)" in line:
        # search upwards for 'def '
        for j in range(i, -1, -1):
            if lines[j].startswith("def "):
                print(f"Line {i+1}: {lines[j]}")
                break
