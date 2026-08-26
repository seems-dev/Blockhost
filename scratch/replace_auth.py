import re
from pathlib import Path

content = Path("src/blockhost_backend/api/servers.py").read_text()

# We want to replace this exact block, ignoring exact indentation:
pattern = re.compile(
r'    try:\n\s+server_uuid = uuid\.UUID\(server_id\)\n\s+except ValueError:\n\s+raise HTTPException\(status_code=404, detail="Server not found"\)\n\s+server = db\.get\(Server, server_uuid\)\n\s+if not server or server\.owner_id != user\.id:\n\s+raise HTTPException\(status_code=404, detail="Server not found"\)',
re.MULTILINE
)

def replace_with_perm(match):
    # Depending on what the endpoint is doing, we could pass a permission.
    # For now, let's just do a generic replacement and we can manually fix permissions later.
    return '    server = _get_server_for_user(server_id, user, db)'

new_content, count = pattern.subn(replace_with_perm, content)
print(f"Replaced {count} occurrences in servers.py")
Path("src/blockhost_backend/api/servers.py").write_text(new_content)
