import asyncio
import websockets
import json
import sys

async def main():
    try:
        # Just test if the endpoint returns 403 or connects
        # We don't have a real server_id or token, but we should get an error message from the server, not a hard disconnect.
        uri = "ws://localhost:8000/api/servers/00000000-0000-0000-0000-000000000000/console/ws?token=invalid"
        async with websockets.connect(uri) as ws:
            msg = await ws.recv()
            print("Received:", msg)
    except Exception as e:
        print("Exception:", e)

asyncio.run(main())
