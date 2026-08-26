from fastapi import FastAPI
from fastapi.testclient import TestClient

app = FastAPI()

@app.put("/agent/servers/{server_id}/properties")
def update_server_properties(
    server_id: str,
    props: dict[str, str | bool | int],
):
    return props

client = TestClient(app)
response = client.put("/agent/servers/123/properties", json={"gamemode": "survival", "online-mode": False})
print(response.status_code, response.json())
