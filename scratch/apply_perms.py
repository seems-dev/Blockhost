import re
from pathlib import Path

content = Path("src/blockhost_backend/api/servers.py").read_text()
lines = content.split('\n')

mapping = {
    "def reprovision_java_server(": "start_stop",
    "def switch_server_software(": "config",
    "def get_server(": None,
    "def start_server(": "start_stop",
    "def stop_server(": "start_stop",
    "def toggle_server(": "start_stop",
    "def get_server_config(": "config",
    "def update_server_config(": "config",
    "def get_server_stats(": None,
    "def get_server_logs(": "console",
    "def _require_server(": "console",
}

for i in range(len(lines)):
    if "_get_server_for_user(server_id, user, db)" in lines[i]:
        # find the function def above
        perm = None
        for j in range(i, -1, -1):
            if lines[j].startswith("def "):
                func_def = lines[j]
                for k, v in mapping.items():
                    if func_def.startswith(k):
                        perm = v
                        break
                break
        
        if perm:
            lines[i] = lines[i].replace("_get_server_for_user(server_id, user, db)", f'_get_server_for_user(server_id, user, db, required_permission="{perm}")')

Path("src/blockhost_backend/api/servers.py").write_text("\n".join(lines))
print("Applied permissions")
