from __future__ import annotations

from dataclasses import dataclass

import httpx

from blockhost_backend.config.config_manager import Settings


@dataclass(frozen=True)
class WorkerStartResult:
    container_id: str
    name: str
    host_port: int


class WorkerClient:
    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.worker_agent_url.rstrip("/")
        self._token = settings.worker_agent_token

    def _headers(self) -> dict[str, str]:
        return {"X-Worker-Token": self._token}

    def start_bedrock(self, *, server_id: str, image: str, env: dict[str, str]) -> WorkerStartResult:
        with httpx.Client(timeout=30.0) as client:
            res = client.post(
                f"{self._base_url}/containers/start",
                headers=self._headers(),
                json={"server_id": server_id, "image": image, "env": env},
            )
        if res.status_code != 200:
            raise RuntimeError(res.text)
        data = res.json()
        return WorkerStartResult(container_id=data["container_id"], name=data["name"], host_port=int(data["host_port"]))

    def stop_bedrock(self, *, server_id: str) -> None:
        with httpx.Client(timeout=30.0) as client:
            res = client.post(f"{self._base_url}/containers/stop", headers=self._headers(), json={"server_id": server_id})
        if res.status_code != 200:
            raise RuntimeError(res.text)

    def remove_bedrock(self, *, server_id: str) -> None:
        with httpx.Client(timeout=30.0) as client:
            res = client.post(
                f"{self._base_url}/containers/remove", headers=self._headers(), json={"server_id": server_id}
            )
        if res.status_code != 200:
            raise RuntimeError(res.text)

