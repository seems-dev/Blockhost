from __future__ import annotations

import random
import socket
import struct
import time
from dataclasses import dataclass
#bedrock_ping.py

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
    if len(parts) > 10:
        out["port_v4"] = _to_int(parts[10])
    if len(parts) > 11:
        out["port_v6"] = _to_int(parts[11])

    return out


def rewrite_bedrock_pong_ports(data: bytes, advertised_port: int) -> bytes:
    """Rewrite server-port fields in a RakNet unconnected pong.

    Bedrock clients read port_v4/port_v6 from the pong and reconnect to that
    port on the address they pinged. If we leave the node's real vm_port in
    the payload, the client abandons proxy_host:proxy_port and tries
    proxy_host:vm_port — a different process, often a different MC version
    ("version not supported").
    """
    if not data or data[0] != 0x1C:
        return data

    header_len = 1 + 8 + 8 + 16  # id + time + guid + magic
    if len(data) < header_len + 2:
        return data

    payload_len = struct.unpack(">H", data[header_len : header_len + 2])[0]
    payload_start = header_len + 2
    payload_end = payload_start + payload_len
    if payload_end > len(data):
        return data

    try:
        payload = data[payload_start:payload_end].decode("utf-8")
    except UnicodeDecodeError:
        return data

    parts = payload.split(";")
    # MCPE;MOTD;protocol;version;online;max;server_id;MOTD2;gamemode;gamemode_id;port_v4;port_v6
    if len(parts) < 11 or parts[0] not in ("MCPE", "MCEE"):
        return data

    port_str = str(advertised_port)
    parts[10] = port_str
    if len(parts) >= 12:
        parts[11] = port_str

    new_payload = ";".join(parts).encode("utf-8")
    return data[:header_len] + struct.pack(">H", len(new_payload)) + new_payload + data[payload_end:]

