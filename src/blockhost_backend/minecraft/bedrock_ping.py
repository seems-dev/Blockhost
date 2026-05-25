from __future__ import annotations

import random
import socket
import struct
import time
from dataclasses import dataclass


_RAKNET_MAGIC = bytes.fromhex("00ffff00fefefefefdfdfdfd12345678")


@dataclass(frozen=True)
class BedrockPingResult:
    latency_ms: int
    server_guid: int
    payload: str


def bedrock_unconnected_ping(*, host: str, port: int, timeout_seconds: float = 1.0) -> BedrockPingResult:
    """
    Query a Bedrock server using RakNet unconnected ping/pong.

    This powers the same basic info Minecraft clients show in the server list:
    MOTD, protocol, version, players, etc.
    """
    client_guid = random.getrandbits(64)
    timestamp_ms = int(time.time() * 1000)
    packet = b"".join(
        [
            b"\x01",  # ID_UNCONNECTED_PING
            struct.pack(">Q", timestamp_ms),
            _RAKNET_MAGIC,
            struct.pack(">Q", client_guid),
        ]
    )

    start = time.perf_counter()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout_seconds)
        sock.sendto(packet, (host, port))
        data, _addr = sock.recvfrom(2048)
    latency_ms = max(0, int((time.perf_counter() - start) * 1000))

    if not data or data[0] != 0x1C:  # ID_UNCONNECTED_PONG
        raise ValueError("Unexpected response from Bedrock server")

    # 0x1c + time(8) + server_guid(8) + magic(16) + payload_len(2) + payload(N)
    if len(data) < 1 + 8 + 8 + 16 + 2:
        raise ValueError("Short response from Bedrock server")

    server_guid = struct.unpack(">Q", data[1 + 8 : 1 + 8 + 8])[0]
    magic = data[1 + 8 + 8 : 1 + 8 + 8 + 16]
    if magic != _RAKNET_MAGIC:
        raise ValueError("Invalid RakNet magic")

    payload_len = struct.unpack(">H", data[1 + 8 + 8 + 16 : 1 + 8 + 8 + 16 + 2])[0]
    payload_bytes = data[1 + 8 + 8 + 16 + 2 : 1 + 8 + 8 + 16 + 2 + payload_len]
    payload = payload_bytes.decode("utf-8", errors="replace")

    return BedrockPingResult(latency_ms=latency_ms, server_guid=server_guid, payload=payload)


def parse_bedrock_pong_payload(payload: str) -> dict[str, object]:
    """
    Parse the ';' separated Bedrock pong payload into a dict.

    Common format (may vary by server build):
    MCPE;MOTD;protocol;version;online;max;server_id;MOTD2;gamemode;gamemode_id;port_v4;port_v6
    """
    parts = payload.split(";")
    if len(parts) < 6:
        return {"raw": payload}

    def _to_int(value: str) -> int | None:
        try:
            return int(value)
        except Exception:
            return None

    out: dict[str, object] = {
        "edition": parts[0] or None,
        "motd": parts[1] or None,
        "protocol": _to_int(parts[2]),
        "version": parts[3] or None,
        "players_online": _to_int(parts[4]),
        "players_max": _to_int(parts[5]),
    }

    if len(parts) > 6:
        out["server_id"] = _to_int(parts[6])
    if len(parts) > 7:
        out["motd2"] = parts[7] or None
    if len(parts) > 8:
        out["gamemode"] = parts[8] or None

    return out

