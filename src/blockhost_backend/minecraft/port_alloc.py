from __future__ import annotations

import socket
from dataclasses import dataclass

#port_alloc.py
@dataclass(frozen=True)
class PortRange:
    start: int
    end: int


def is_udp_port_free(*, port: int) -> bool:
    """
    Best-effort check that a UDP port is free for both IPv4 and IPv6 binds.

    Bedrock uses UDP and can bind on IPv4/IPv6, so only checking IPv4 can miss collisions.
    """
    # Prefer a dual-stack IPv6 bind test: if another process holds IPv4:port, then
    # binding an IPv6 socket with IPV6_V6ONLY=0 typically fails (Bedrock tends to
    # use IPv6 and/or dual-stack depending on platform).
    try:
        with socket.socket(socket.AF_INET6, socket.SOCK_DGRAM) as sock6:
            try:
                sock6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            except OSError:
                # Some platforms disallow toggling; fall back to separate binds.
                pass
            sock6.bind(("::", port))
        # Also ensure TCP isn't already taken (some platforms/services bind both).
        with socket.socket(socket.AF_INET6, socket.SOCK_STREAM) as tcp6:
            try:
                tcp6.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
            except OSError:
                pass
            tcp6.bind(("::", port))
        return True
    except OSError:
        # Fall back to IPv4-only check; this is still better than always claiming "free".
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock4:
                sock4.bind(("0.0.0.0", port))
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp4:
                tcp4.bind(("0.0.0.0", port))
            return True
        except OSError:
            return False


def pick_free_udp_port(*, port_range: PortRange, used_ports: set[int]) -> int:
    if port_range.end < port_range.start:
        raise ValueError("Invalid port range")

    for port in range(port_range.start, port_range.end + 1):
        ipv6_port = port + 1
        if ipv6_port > port_range.end:
            continue

        if port in used_ports or ipv6_port in used_ports:
            continue

        if not is_udp_port_free(port=port):
            continue

        if not is_udp_port_free(port=ipv6_port):
            continue

        return port

    raise RuntimeError("No free UDP ports available in configured range")
