"""Lightweight asyncio TCP+UDP game proxy with database-driven routing.

Binds a listener on each server's proxy_port and forwards traffic to the
actual node_ip:vm_port. Re-reads the routing table from the database every
N seconds to pick up migrations with zero restarts.
"""

from __future__ import annotations

import asyncio
import logging
import signal
from dataclasses import dataclass

from sqlalchemy import select

from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.database.db import SessionLocal
from blockhost_backend.database.schema import Node, Server

logger = logging.getLogger(__name__)

BUFFER_SIZE = 4096  # bytes per read for TCP; max datagram for UDP


@dataclass(frozen=True)
class RouteTarget:
    """Where traffic for a proxy_port should be forwarded."""
    backend_ip: str
    backend_port: int


def _load_routing_table() -> dict[int, RouteTarget]:
    """Query the database for all servers with a proxy_port and build a
    proxy_port → (backend_ip, backend_port) mapping."""
    routes: dict[int, RouteTarget] = {}
    with SessionLocal() as db:
        rows = db.execute(
            select(
                Server.proxy_port,
                Server.vm_ipv4,
                Server.vm_port,
                Node.ip_address.label("node_ip"),
            )
            .outerjoin(Node, Server.node_id == Node.id)
            .where(Server.proxy_port.is_not(None))
        ).all()

        for row in rows:
            proxy_port = row.proxy_port
            # Always prefer the live Node IP if the server is assigned to a node
            backend_ip = row.node_ip or row.vm_ipv4
            backend_port = row.vm_port

            if backend_ip and backend_port and proxy_port:
                routes[proxy_port] = RouteTarget(
                    backend_ip=backend_ip,
                    backend_port=backend_port,
                )
    return routes


# ── TCP Proxy ─────────────────────────────────────────────────────────

async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Copy data from reader to writer until EOF."""
    try:
        while True:
            data = await reader.read(BUFFER_SIZE)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionResetError, BrokenPipeError, OSError):
        pass
    finally:
        writer.close()


async def _handle_tcp_client(
    proxy_port: int,
    routing_table: dict[int, RouteTarget],
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
) -> None:
    """Handle one incoming TCP connection by piping to the backend."""
    route = routing_table.get(proxy_port)
    if not route:
        logger.warning("No route for TCP proxy_port %d", proxy_port)
        client_writer.close()
        return

    try:
        backend_reader, backend_writer = await asyncio.open_connection(
            route.backend_ip, route.backend_port,
        )
    except OSError as e:
        logger.warning(
            "TCP connect failed for proxy_port %d → %s:%d: %s",
            proxy_port, route.backend_ip, route.backend_port, e,
        )
        client_writer.close()
        return

    await asyncio.gather(
        _pipe(client_reader, backend_writer),
        _pipe(backend_reader, client_writer),
    )


# ── UDP Proxy ─────────────────────────────────────────────────────────

class UdpProxyProtocol(asyncio.DatagramProtocol):
    """Bidirectional UDP forwarder.

    Incoming datagrams on the proxy_port are forwarded to the backend.
    Replies from the backend are forwarded back to the original client.
    """

    def __init__(self, proxy_port: int, routing_table: dict[int, RouteTarget], loop: asyncio.AbstractEventLoop):
        self.proxy_port = proxy_port
        self.routing_table = routing_table
        self.loop = loop
        self.transport: asyncio.DatagramTransport | None = None
        # Map client_addr → backend transport for return traffic
        self._client_sessions: dict[tuple, asyncio.DatagramTransport] = {}

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        route = self.routing_table.get(self.proxy_port)
        if not route:
            return

        # Create or reuse a backend socket for this specific client
        if addr not in self._client_sessions:
            # Create a dedicated socket for backend communication
            coro = self._create_backend_session(addr, route)
            asyncio.ensure_future(coro, loop=self.loop)
            # Queue the packet to send once the session is ready
            asyncio.ensure_future(
                self._send_after_session(addr, data, route), loop=self.loop
            )
        else:
            backend_transport = self._client_sessions[addr]
            backend_transport.sendto(data, (route.backend_ip, route.backend_port))

    async def _create_backend_session(self, client_addr: tuple, route: RouteTarget) -> None:
        try:
            transport, _ = await self.loop.create_datagram_endpoint(
                lambda: _UdpBackendProtocol(self, client_addr),
                remote_addr=(route.backend_ip, route.backend_port),
            )
            self._client_sessions[client_addr] = transport
        except OSError as e:
            logger.warning("UDP backend session failed for %s: %s", client_addr, e)

    async def _send_after_session(self, client_addr: tuple, data: bytes, route: RouteTarget) -> None:
        """Wait for the backend session to be created, then forward."""
        for _ in range(50):  # wait up to 500ms
            if client_addr in self._client_sessions:
                self._client_sessions[client_addr].sendto(
                    data, (route.backend_ip, route.backend_port)
                )
                return
            await asyncio.sleep(0.01)

    def error_received(self, exc: Exception) -> None:
        logger.debug("UDP proxy error on port %d: %s", self.proxy_port, exc)

    def connection_lost(self, exc: Exception | None) -> None:
        for transport in self._client_sessions.values():
            transport.close()
        self._client_sessions.clear()


class _UdpBackendProtocol(asyncio.DatagramProtocol):
    """Receives replies from the backend and forwards them to the original client."""

    def __init__(self, proxy: UdpProxyProtocol, client_addr: tuple):
        self.proxy = proxy
        self.client_addr = client_addr

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        if self.proxy.transport:
            self.proxy.transport.sendto(data, self.client_addr)


# ── Main Proxy Engine ────────────────────────────────────────────────

class GameProxy:
    """Manages all TCP and UDP listeners with periodic routing refresh."""

    def __init__(self):
        self.routing_table: dict[int, RouteTarget] = {}
        self._tcp_servers: dict[int, asyncio.Server] = {}
        self._udp_transports: dict[int, asyncio.DatagramTransport] = {}
        self._running = False

    async def start(self) -> None:
        """Load routes and start all listeners."""
        self._running = True
        self.routing_table = _load_routing_table()
        logger.info("Loaded %d routes from database", len(self.routing_table))

        loop = asyncio.get_running_loop()

        for proxy_port in self.routing_table:
            await self._start_listeners(proxy_port, loop)

        # Start background refresh
        asyncio.create_task(self._refresh_loop())
        logger.info("Game proxy started — listening on %d ports", len(self.routing_table))

    async def _start_listeners(self, proxy_port: int, loop: asyncio.AbstractEventLoop) -> None:
        """Start TCP and UDP listeners for a single proxy port."""
        # TCP
        if proxy_port not in self._tcp_servers:
            try:
                tcp_server = await asyncio.start_server(
                    lambda r, w, pp=proxy_port: _handle_tcp_client(pp, self.routing_table, r, w),
                    host="0.0.0.0",
                    port=proxy_port,
                )
                self._tcp_servers[proxy_port] = tcp_server
            except OSError as e:
                logger.error("Failed to bind TCP on port %d: %s", proxy_port, e)

        # UDP
        if proxy_port not in self._udp_transports:
            try:
                transport, _ = await loop.create_datagram_endpoint(
                    lambda pp=proxy_port: UdpProxyProtocol(pp, self.routing_table, loop),
                    local_addr=("0.0.0.0", proxy_port),
                )
                self._udp_transports[proxy_port] = transport
            except OSError as e:
                logger.error("Failed to bind UDP on port %d: %s", proxy_port, e)

    async def _stop_listeners(self, proxy_port: int) -> None:
        """Stop TCP and UDP listeners for a proxy port."""
        if proxy_port in self._tcp_servers:
            self._tcp_servers[proxy_port].close()
            await self._tcp_servers[proxy_port].wait_closed()
            del self._tcp_servers[proxy_port]

        if proxy_port in self._udp_transports:
            self._udp_transports[proxy_port].close()
            del self._udp_transports[proxy_port]

    async def _refresh_loop(self) -> None:
        """Periodically re-read the routing table from the database."""
        settings = get_settings()
        interval = settings.proxy_db_refresh_seconds

        while self._running:
            await asyncio.sleep(interval)
            try:
                new_routes = _load_routing_table()
                loop = asyncio.get_running_loop()

                # Start listeners for new ports
                new_ports = set(new_routes) - set(self.routing_table)
                for pp in new_ports:
                    logger.info("Adding new proxy listener on port %d", pp)
                    await self._start_listeners(pp, loop)

                # Remove listeners for deleted ports
                removed_ports = set(self.routing_table) - set(new_routes)
                for pp in removed_ports:
                    logger.info("Removing proxy listener on port %d", pp)
                    await self._stop_listeners(pp)

                # Update routing table in-place (existing connections get new routes)
                self.routing_table.clear()
                self.routing_table.update(new_routes)

                if new_ports or removed_ports:
                    logger.info(
                        "Routing refresh: +%d -%d ports (total: %d)",
                        len(new_ports), len(removed_ports), len(self.routing_table),
                    )
            except Exception:
                logger.exception("Routing table refresh failed")

    async def shutdown(self) -> None:
        """Clean shutdown of all listeners."""
        self._running = False
        for pp in list(self._tcp_servers):
            await self._stop_listeners(pp)
        logger.info("Game proxy shut down")
