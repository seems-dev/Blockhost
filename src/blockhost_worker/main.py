from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from shutil import which
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class WorkerSettings(BaseSettings):
    worker_agent_token: str = "change-me-in-dev"
    minecraft_dynamic_port_start: int = 20000
    minecraft_dynamic_port_end: int = 20100
    docker_network: str = "blockhost_default"


def get_settings() -> WorkerSettings:
    return WorkerSettings()


def require_token(
    x_worker_token: str | None = Header(default=None), settings: WorkerSettings = Depends(get_settings)
) -> None:
    if not x_worker_token or x_worker_token != settings.worker_agent_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unauthorized")


class StartRequest(BaseModel):
    server_id: str
    image: str = Field(min_length=1)
    env: dict[str, str] = Field(default_factory=dict)


class StopRequest(BaseModel):
    server_id: str


class StartResponse(BaseModel):
    container_id: str
    name: str
    host_port: int


_RAKNET_CONTAINER_PORT = 19132


@dataclass(frozen=True)
class ContainerInfo:
    container_id: str
    name: str
    host_port: int


def _run(args: list[str]) -> str:
    if args and args[0] == "docker":
        docker_bin = which("docker") or "/usr/bin/docker"
        args = [docker_bin, *args[1:]]
    try:
        proc = subprocess.run(args, check=True, capture_output=True, text=True)
    except FileNotFoundError as e:
        raise RuntimeError(
            "docker CLI not available in worker container. Rebuild worker image (`docker compose up --build`) "
            "or verify Docker is installed in the worker runtime."
        ) from e
    except subprocess.CalledProcessError as e:
        msg = ((e.stderr or "") + "\n" + (e.stdout or "")).strip() or "docker command failed"
        raise RuntimeError(msg) from e
    return proc.stdout.strip()


def _container_name(server_id: str) -> str:
    return f"blockhost-bedrock-{server_id}"


def _docker_network(settings: WorkerSettings) -> str:
    # Use explicitly configured network (set via docker-compose.yml or environment).
    # This ensures bedrock containers join the correct compose network for service communication.
    return settings.docker_network


def _list_used_ports() -> set[int]:
    used: set[int] = set()
    out = _run(["docker", "ps", "--filter", "label=blockhost.kind=bedrock", "--format", "{{.Ports}}"])
    for token in out.split():
        if f"->{_RAKNET_CONTAINER_PORT}/udp" in token and ":" in token:
            left = token.split("->", 1)[0].rstrip(",")
            host_port = left.split(":")[-1]
            try:
                used.add(int(host_port))
            except Exception:
                continue
    return used


def _pick_port(settings: WorkerSettings) -> int:
    start = settings.minecraft_dynamic_port_start
    end = settings.minecraft_dynamic_port_end
    if end < start:
        raise RuntimeError("Invalid MINECRAFT_DYNAMIC_PORT_* range")
    used = _list_used_ports()
    for port in range(start, end + 1):
        if port not in used:
            return port
    raise RuntimeError("No free Bedrock ports available in configured range")


def _get_host_port(name: str) -> int:
    out = _run(["docker", "port", name, f"{_RAKNET_CONTAINER_PORT}/udp"])
    first = out.splitlines()[0].strip()
    port_str = first.split(":")[-1]
    return int(port_str)


def ensure_running(*, server_id: str, image: str, env: dict[str, str], settings: WorkerSettings) -> ContainerInfo:
    name = _container_name(server_id)
    existing = _run(["docker", "ps", "-a", "--filter", f"name=^{name}$", "--format", "{{.ID}}"])
    if existing:
        _run(["docker", "start", name])
        return ContainerInfo(container_id=existing.splitlines()[0], name=name, host_port=_get_host_port(name))

    host_port = _pick_port(settings)
    network = _docker_network(settings)
    args = [
        "docker",
        "run",
        "-d",
        "--name",
        name,
        "--network",
        network,
        "-p",
        f"{host_port}:{_RAKNET_CONTAINER_PORT}/udp",
        "--label",
        "blockhost.kind=bedrock",
        "--label",
        f"blockhost.server_id={server_id}",
        "-e",
        "EULA=TRUE",
    ]
    for k, v in env.items():
        args += ["-e", f"{k}={v}"]
    args.append(image)
    container_id = _run(args)
    return ContainerInfo(container_id=container_id, name=name, host_port=host_port)


def stop(*, server_id: str) -> None:
    name = _container_name(server_id)
    try:
        _run(["docker", "stop", name])
    except Exception:
        return


def remove(*, server_id: str) -> None:
    name = _container_name(server_id)
    try:
        _run(["docker", "rm", "-f", name])
    except Exception:
        return


app = FastAPI(title="BlockHost Worker", version="0.1.0")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/containers/start", response_model=StartResponse, dependencies=[Depends(require_token)])
def start_container(payload: StartRequest, settings: WorkerSettings = Depends(get_settings)) -> StartResponse:
    try:
        info = ensure_running(server_id=payload.server_id, image=payload.image, env=payload.env, settings=settings)
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))
    return StartResponse(container_id=info.container_id, name=info.name, host_port=info.host_port)


@app.post("/containers/stop", dependencies=[Depends(require_token)])
def stop_container(payload: StopRequest) -> dict[str, str]:
    stop(server_id=payload.server_id)
    return {"status": "ok"}


@app.post("/containers/remove", dependencies=[Depends(require_token)])
def remove_container(payload: StopRequest) -> dict[str, str]:
    remove(server_id=payload.server_id)
    return {"status": "ok"}
