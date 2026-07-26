import asyncio
import httpx

async def test():
    url = "https://cdn.modrinth.com/data/wKkoqHrH/versions/qoZXtR9c/Geyser-Spigot.jar"
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(connect=10.0, read=300.0, write=60.0, pool=5.0),
        follow_redirects=True,
    ) as client:
        try:
            async with client.stream("GET", url) as resp:
                print(f"Status: {resp.status_code}")
                resp.raise_for_status()
                total = 0
                async for chunk in resp.aiter_bytes(1024 * 1024):
                    total += len(chunk)
                print(f"Downloaded {total} bytes")
        except Exception as e:
            print(f"Error: {repr(e)}")

asyncio.run(test())
