from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any

import httpx
import websockets

from blockhost_backend.runtime.interface import (
    LogEntry,
    LogListener,
    Runtime,
    RuntimeResourceStats,
    RuntimeStartRequest,
    RuntimeStartResult,
    RuntimeStatus,
)

logger = logging.getLogger(__name__)

class AgentRuntime:
    """Runtime implementation that proxies calls to a remote Node Agent over HTTP."""
    def __init__(self, agent_base_url: str, agent_token: str):
        self.agent_base_url = agent_base_url.rstrip("/")
        self.agent_token = agent_token
        self._headers = {"Authorization": f"Bearer {self.agent_token}"}
        
        self._log_listeners: dict[str, list[LogListener]] = {}
        self._log_threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    def _post(self, path: str, json_data: dict[str, Any] | None = None) -> httpx.Response:
        resp = httpx.post(f"{self.agent_base_url}{path}", headers=self._headers, json=json_data, timeout=300.0)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error from Agent: {resp.text}")
            raise
        return resp

    def _get(self, path: str, params: dict[str, Any] | None = None) -> httpx.Response:
        resp = httpx.get(f"{self.agent_base_url}{path}", headers=self._headers, params=params, timeout=30.0)
        resp.raise_for_status()
        return resp

    def _put(self, path: str, json_data: dict[str, Any] | None = None) -> httpx.Response:
        resp = httpx.put(f"{self.agent_base_url}{path}", headers=self._headers, json=json_data, timeout=300.0)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error from Agent: {resp.text}")
            raise
        return resp

    def _delete(self, path: str) -> httpx.Response:
        resp = httpx.delete(f"{self.agent_base_url}{path}", headers=self._headers, timeout=120.0)
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error from Agent: {resp.text}")
            raise
        return resp

    def start_server(self, request: RuntimeStartRequest) -> RuntimeStartResult:
        # Filter out None values so Pydantic on the Agent doesn't reject the payload
        clean_props = {}
        if request.server_properties_dict:
            clean_props = {k: v for k, v in request.server_properties_dict.items() if v is not None}

        payload = {
            "server_dir_rel": request.server_id,
            "port": request.port,
            "requested_version": request.requested_version,
            "executable_path": str(request.executable_path) if request.executable_path else None,
            "ram_mb": request.ram_mb,
            "cpu_quota_pct": request.cpu_quota_pct,
            "jdk_path": str(request.jdk_path) if request.jdk_path else None,
            "server_properties_dict": clean_props,
            "jar_download_url": request.jar_download_url,
        }
        resp = self._post(f"/agent/servers/{request.server_id}/start", json_data=payload)
        data = resp.json()
        return RuntimeStartResult(
            runtime_id=data["runtime_id"],
            port=data["port"],
            actual_version=data["actual_version"]
        )

    def stop_server(self, server_id: str) -> None:
        try:
            self._post(f"/agent/servers/{server_id}/stop")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return
            raise

    def restart_server(self, request: RuntimeStartRequest) -> RuntimeStartResult:
        self.stop_server(request.server_id)
        return self.start_server(request)

    def get_status(self, server_id: str) -> RuntimeStatus:
        try:
            resp = self._get(f"/agent/servers/{server_id}/status")
            data = resp.json()
            return RuntimeStatus(
                running=data["running"],
                runtime_id=data.get("runtime_id"),
                active_state=data.get("active_state"),
                uptime_seconds=data.get("uptime_seconds"),
                online_players=data.get("online_players"),
            )
        except Exception as e:
            logger.warning("Failed to get status for %s: %s", server_id, e)
            return RuntimeStatus(running=False)

    def get_stats(self, server_id: str) -> RuntimeResourceStats:
        try:
            resp = self._get(f"/agent/servers/{server_id}/stats")
            data = resp.json()
            return RuntimeResourceStats(
                cpu_usage=data.get("cpu_usage"),
                ram_usage_mb=data.get("ram_usage_mb"),
            )
        except Exception as e:
            logger.warning("Failed to get stats for %s: %s", server_id, e)
            return RuntimeResourceStats(cpu_usage=None, ram_usage_mb=None)

    def get_online_players_with_xuid(self, server_id: str) -> dict[str, str | None]:
        try:
            resp = self._get(f"/agent/servers/{server_id}/online_players")
            return resp.json()
        except Exception as e:
            logger.warning("Failed to get online players for %s: %s", server_id, e)
            return {}

    def ping_server(self, server_id: str) -> dict[str, object] | None:
        """Ask the agent to perform a local Bedrock UDP ping and return parsed results."""
        try:
            resp = self._get(f"/agent/servers/{server_id}/ping")
            data = resp.json()
            if data.get("reachable"):
                return data
            return None
        except Exception:
            return None

    def read_logs(self, server_id: str, *, tail: int = 200) -> list[LogEntry]:
        try:
            resp = self._get(f"/agent/servers/{server_id}/logs", params={"tail": tail})
            return [LogEntry(ts=item.get("ts"), line=item["line"]) for item in resp.json()]
        except Exception:
            return []

    def get_properties(self, server_id: str) -> dict[str, str | bool | int]:
        try:
            resp = self._get(f"/agent/servers/{server_id}/properties")
            return resp.json()
        except Exception:
            return {}

    def update_properties(self, server_id: str, props: dict[str, str | bool | int]) -> None:
        self._put(f"/agent/servers/{server_id}/properties", json_data=props)

    def send_command(self, server_id: str, command: str) -> None:
        self._post(f"/agent/servers/{server_id}/command", json_data={"command": command})

    def backup_to_s3(self, server_id: str) -> dict[str, Any]:
        """Ask the agent to zip the server world and upload to S3."""
        resp = self._post(f"/agent/servers/{server_id}/backup")
        return resp.json()

    def restore_from_s3(self, server_id: str) -> dict[str, Any]:
        """Ask the agent to download the world zip from S3 and extract it."""
        resp = self._post(f"/agent/servers/{server_id}/restore")
        return resp.json()

    def purge_server_data(self, server_id: str) -> dict[str, Any]:
        """Ask the agent to stop and delete local world files for this server."""
        resp = self._delete(f"/agent/servers/{server_id}/data")
        return resp.json()

    def add_log_listener(self, server_id: str, listener: LogListener) -> None:
        with self._lock:
            if server_id not in self._log_listeners:
                self._log_listeners[server_id] = []
            if not self._log_listeners[server_id]:
                self._start_ws_thread(server_id)
            self._log_listeners[server_id].append(listener)

    def remove_log_listener(self, server_id: str, listener: LogListener) -> None:
        with self._lock:
            if server_id in self._log_listeners:
                try:
                    self._log_listeners[server_id].remove(listener)
                except ValueError:
                    pass

    def _start_ws_thread(self, server_id: str):
        ws_url = self.agent_base_url.replace("http://", "ws://").replace("https://", "wss://")
        ws_url += f"/agent/servers/{server_id}/logs/stream?token={self.agent_token}"

        def ws_run():
            async def run_loop():
                try:
                    async with websockets.connect(ws_url) as ws:
                        while True:
                            with self._lock:
                                if not self._log_listeners.get(server_id):
                                    break
                            try:
                                msg = await asyncio.wait_for(ws.recv(), timeout=1.0)
                                data = json.loads(msg)
                                entry = LogEntry(ts=data.get("ts"), line=data["line"])
                                with self._lock:
                                    listeners = list(self._log_listeners.get(server_id, []))
                                for cb in listeners:
                                    try:
                                        cb(entry)
                                    except Exception:
                                        pass
                            except asyncio.TimeoutError:
                                continue
                except Exception as e:
                    logger.warning(f"Log stream for {server_id} disconnected: {e}")
                finally:
                    with self._lock:
                        if server_id in self._log_threads:
                            del self._log_threads[server_id]

            asyncio.run(run_loop())

        t = threading.Thread(target=ws_run, daemon=True)
        self._log_threads[server_id] = t
        t.start()
