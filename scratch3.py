import asyncio
import httpx
from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.api.auth import create_access_token
from datetime import timedelta

async def test():
    token = create_access_token({"sub": "a1aeeb8f78db4ddeacd17fdf4f5b354e"}, get_settings(), timedelta(minutes=15))
    url = "http://192.168.29.102:8000/api/servers/3f8363fb-02ab-4038-ae59-1793bb45bb2b/mods/install"
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "modrinth_project_id": "wKkoqHrH",
        "version_id": None
    }
    
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, headers=headers, json=payload, timeout=60.0)
        print(f"Status: {resp.status_code}")
        print(resp.text)

asyncio.run(test())
