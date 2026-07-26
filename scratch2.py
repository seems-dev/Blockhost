import asyncio
import httpx

async def test():
    token = "change-me-in-dev"
    url = "http://192.168.29.102:8001/agent/servers/3f8363fb-02ab-4038-ae59-1793bb45bb2b/mods/download"
    headers = {"Authorization": f"Bearer {token}"}
    payload = {
        "files": [
            {
                "url": "https://cdn.modrinth.com/data/wKkoqHrH/versions/qoZXtR9c/Geyser-Spigot.jar",
                "filename": "Geyser-Spigot.jar",
                "subdir": "plugins"
            }
        ]
    }
    
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, headers=headers, json=payload, timeout=60.0)
        print(resp.status_code)
        print(resp.text)

asyncio.run(test())
