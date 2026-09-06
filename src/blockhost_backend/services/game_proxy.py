"""Lightweight asyncio TCP+UDP game proxy with database-driven routing.

Binds a listener on each server's proxy_port and forwards traffic to the
actual node_ip:vm_port. Re-reads the routing table from the database every
N seconds to pick up migrations with zero restarts.

How mapping works
-----------------
Each world gets a stable ``proxy_port`` (e.g. 30001) at create time. The UI
shows ``PROXY_HOST:proxy_port``. This process listens on that port and
forwards bytes to ``Node.ip_address`` (fallback ``Server.vm_ipv4``) on
``Server.vm_port`` — the same IP:port the agent logs when the world starts.

Bedrock clients ping first. The dedicated server advertises *its* bind port
in the RakNet pong, so we rewrite those fields to ``proxy_port`` before the
pong reaches the player. Otherwise the client reconnects to
``proxy_host:vm_port`` (wrong machine/process) and shows "version not supported".
"""

from __future__ import annotations

import asyncio
import logging
import socket
from dataclasses import dataclass

import uuid
from sqlalchemy import select

from blockhost_backend.database.db import SessionLocal
from blockhost_backend.database.schema import Node, Server, ServerFlavor, ServerState
from blockhost_backend.minecraft.bedrock_ping import rewrite_bedrock_pong_ports

logger = logging.getLogger(__name__)

BUFFER_SIZE = 65536


@dataclass(frozen=True)
class RouteTarget:
    """Where traffic for a proxy_port should be forwarded."""
    backend_ip: str
    backend_port: int
    flavor: str | None = None
    server_id: str | None = None
    is_suspended: bool = False

_waking_servers: set[str] = set()

def _sync_trigger_wakeup(server_id: str) -> None:
    """Synchronous function to wake up a server."""
    if server_id in _waking_servers:
        return
    _waking_servers.add(server_id)
    try:
        from blockhost_backend.api.servers import _do_start_server
        with SessionLocal() as db:
            server = db.get(Server, uuid.UUID(server_id))
            if server and server.state == ServerState.suspended:
                logger.info("Wake-on-Connect triggered for %s", server_id)
                _do_start_server(server, db)
                db.commit()
    except Exception as e:
        logger.error("Wake-on-Connect failed for %s: %s", server_id, e)
    finally:
        _waking_servers.discard(server_id)

async def _trigger_wakeup(server_id: str) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, _sync_trigger_wakeup, server_id)


def _load_routing_table(*, log_routes: bool = False) -> dict[int, RouteTarget]:
    """Query the database for running servers with a proxy_port and build a
    proxy_port → (backend_ip, backend_port) mapping."""
    routes: dict[int, RouteTarget] = {}
    with SessionLocal() as db:
        rows = db.execute(
            select(
                Server.id,
                Server.proxy_port,
                Server.vm_ipv4,
                Server.vm_port,
                Server.flavor,
                Server.state,
                Node.ip_address.label("node_ip"),
            )
            .outerjoin(Node, Server.node_id == Node.id)
            .where(
                Server.proxy_port.is_not(None),
                Server.state.in_([ServerState.running, ServerState.suspended]),
            )
        ).all()

        for row in rows:
            proxy_port = row.proxy_port
            # Prefer the live node IP (what the agent binds on). vm_ipv4 is the
            # same value for remote nodes; used when node row is missing.
            backend_ip = row.node_ip or row.vm_ipv4
            backend_port = row.vm_port
            flavor = row.flavor.value if isinstance(row.flavor, ServerFlavor) else (
                str(row.flavor) if row.flavor else None
            )

            if backend_ip and backend_port and proxy_port:
                routes[proxy_port] = RouteTarget(
                    backend_ip=backend_ip,
                    backend_port=backend_port,
                    flavor=flavor,
                    server_id=str(row.id),
                    is_suspended=(row.state == ServerState.suspended),
                )
                if log_routes:
                    logger.info(
                        "Route proxy:%d → %s:%d (%s %s) suspended=%s",
                        proxy_port,
                        backend_ip,
                        backend_port,
                        flavor or "unknown",
                        str(row.id)[:8],
                        row.state == ServerState.suspended,
                    )
    return routes


def _set_tcp_nodelay(writer: asyncio.StreamWriter) -> None:
    sock = writer.get_extra_info("socket")
    if sock is not None:
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError:
            pass


# ── TCP Proxy (Java Edition) ──────────────────────────────────────────

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
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def _handle_tcp_client(
    proxy_port: int,
    routing_table: dict[int, RouteTarget],
    client_reader: asyncio.StreamReader,
    client_writer: asyncio.StreamWriter,
) -> None:
    """Handle one incoming TCP connection by piping to the backend."""
    peer = client_writer.get_extra_info("peername")
    route = routing_table.get(proxy_port)
    if not route:
        logger.warning("No route for TCP proxy_port %d (peer=%s)", proxy_port, peer)
        client_writer.close()
        return

    logger.info(
        "TCP %s → proxy:%d → %s:%d",
        peer, proxy_port, route.backend_ip, route.backend_port,
    )
    
    if route.is_suspended:
        logger.info("TCP proxy %d: Server suspended, triggering Wake-on-Connect", proxy_port)
        if route.server_id:
            asyncio.create_task(_trigger_wakeup(route.server_id))
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

    _set_tcp_nodelay(client_writer)
    _set_tcp_nodelay(backend_writer)

    await asyncio.gather(
        _pipe(client_reader, backend_writer),
        _pipe(backend_reader, client_writer),
    )


# ── UDP Proxy (Bedrock Edition) ───────────────────────────────────────

class UdpProxyProtocol(asyncio.DatagramProtocol):
    """Bidirectional UDP forwarder.

    Incoming datagrams on the proxy_port are forwarded to the backend.
    Replies from the backend are forwarded back to the original client,
    with Bedrock unconnected-pong ports rewritten to this proxy_port.
    """

    def __init__(self, proxy_port: int, routing_table: dict[int, RouteTarget], loop: asyncio.AbstractEventLoop):
        self.proxy_port = proxy_port
        self.routing_table = routing_table
        self.loop = loop
        self.transport: asyncio.DatagramTransport | None = None
        self._client_sessions: dict[tuple, asyncio.DatagramTransport] = {}
        self._session_tasks: dict[tuple, asyncio.Task] = {}

    def connection_made(self, transport: asyncio.DatagramTransport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        route = self.routing_table.get(self.proxy_port)
        if not route:
            return

        if route.is_suspended:
            if route.server_id:
                self.loop.create_task(_trigger_wakeup(route.server_id))
            
            # Send fake pong if it's an Unconnected Ping (0x01)
            if len(data) >= 33 and data[0] == 0x01 and self.transport:
                timestamp = data[1:9]
                magic = data[9:25]
                server_guid = b"\x00\x00\x00\x00\x00\x00\x00\x01"
                payload = f"MCPE;Server Waking Up...;503;1.20;0;0;1;Wait 15s...;Survival;1;{self.proxy_port};{self.proxy_port}".encode("utf-8")
                import struct
                payload_len = struct.pack(">H", len(payload))
                fake_pong = b"\x1c" + timestamp + server_guid + magic + payload_len + payload
                self.transport.sendto(fake_pong, addr)
            return

        session = self._client_sessions.get(addr)
        if session is not None:
            try:
                session.sendto(data)
            except OSError as e:
                logger.debug("UDP send to backend failed for %s: %s", addr, e)
            return

        self.loop.create_task(self._forward_new_client(addr, data, route))

    async def _forward_new_client(self, client_addr: tuple, data: bytes, route: RouteTarget) -> None:
        existing = self._session_tasks.get(client_addr)
        if existing is None or existing.done():
            self._session_tasks[client_addr] = self.loop.create_task(
                self._create_backend_session(client_addr, route)
            )
        try:
            await self._session_tasks[client_addr]
        except OSError:
            return

        session = self._client_sessions.get(client_addr)
        if session is not None:
            try:
                session.sendto(data)
            except OSError as e:
                logger.debug("UDP send to backend failed for %s: %s", client_addr, e)

    async def _create_backend_session(self, client_addr: tuple, route: RouteTarget) -> None:
        if client_addr in self._client_sessions:
            return
        logger.info(
            "UDP %s → proxy:%d → %s:%d",
            client_addr, self.proxy_port, route.backend_ip, route.backend_port,
        )
        transport, _ = await self.loop.create_datagram_endpoint(
            lambda: _UdpBackendProtocol(self, client_addr),
            remote_addr=(route.backend_ip, route.backend_port),
        )
        self._client_sessions[client_addr] = transport

    def error_received(self, exc: Exception) -> None:
        logger.debug("UDP proxy error on port %d: %s", self.proxy_port, exc)

    def connection_lost(self, exc: Exception | None) -> None:
        for transport in self._client_sessions.values():
            transport.close()
        self._client_sessions.clear()
        self._session_tasks.clear()


class _UdpBackendProtocol(asyncio.DatagramProtocol):
    """Receives replies from the backend and forwards them to the original client."""

    def __init__(self, proxy: UdpProxyProtocol, client_addr: tuple):
        self.proxy = proxy
        self.client_addr = client_addr

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        if not self.proxy.transport:
            return
        rewritten = rewrite_bedrock_pong_ports(data, self.proxy.proxy_port)
        self.proxy.transport.sendto(rewritten, self.client_addr)


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
        self.routing_table = _load_routing_table(log_routes=True)
        logger.info("Loaded %d routes from database", len(self.routing_table))

        loop = asyncio.get_running_loop()

        for proxy_port in self.routing_table:
            await self._start_listeners(proxy_port, loop)

        asyncio.create_task(self._refresh_loop())
        logger.info("Game proxy started — listening on %d ports", len(self.routing_table))

    async def _start_listeners(self, proxy_port: int, loop: asyncio.AbstractEventLoop) -> None:
        """Start TCP and UDP listeners for a single proxy port."""
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
        from blockhost_backend.config.config_manager import get_settings

        settings = get_settings()
        interval = settings.proxy_db_refresh_seconds

        while self._running:
            await asyncio.sleep(interval)
            try:
                new_routes = _load_routing_table(log_routes=False)
                loop = asyncio.get_running_loop()

                changed_backend = [
                    pp for pp in (set(new_routes) & set(self.routing_table))
                    if new_routes[pp] != self.routing_table[pp]
                ]
                for pp in changed_backend:
                    old, new = self.routing_table[pp], new_routes[pp]
                    logger.info(
                        "Route updated proxy:%d  %s:%d → %s:%d",
                        pp, old.backend_ip, old.backend_port, new.backend_ip, new.backend_port,
                    )

                new_ports = set(new_routes) - set(self.routing_table)
                for pp in new_ports:
                    logger.info("Adding new proxy listener on port %d", pp)
                    await self._start_listeners(pp, loop)

                removed_ports = set(self.routing_table) - set(new_routes)
                for pp in removed_ports:
                    logger.info("Removing proxy listener on port %d", pp)
                    await self._stop_listeners(pp)

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
        for pp in list(set(self._tcp_servers) | set(self._udp_transports)):
            await self._stop_listeners(pp)
        logger.info("Game proxy shut down")
