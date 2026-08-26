import asyncio
from blockhost_backend.orchestrator.server_lifecycle import ServerLifecycleOrchestrator
from blockhost_backend.runtime.systemd_runtime import SystemdRuntime

async def main():
    try:
        runtime = SystemdRuntime()
        orch = ServerLifecycleOrchestrator(runtime)
        
        props = orch.get_properties("0233ad30-868f-4e46-a180-8eb002bd6e2c")
        print("GET PROPS:", props)
        
        orch.update_properties("0233ad30-868f-4e46-a180-8eb002bd6e2c", {"gamemode": "creative"})
        print("UPDATE SUCCESS")
    except Exception as e:
        import traceback
        traceback.print_exc()

asyncio.run(main())
