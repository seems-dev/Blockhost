"""Background worker that sleeps idle servers locally on the node."""

import logging
import threading
import time
from datetime import timedelta

from sqlalchemy import select, update

from blockhost_backend.config.config_manager import get_settings
from blockhost_backend.database.db import SessionLocal
from blockhost_backend.database.schema import Server, ServerState, utcnow
from blockhost_backend.orchestrator.lifecycle_manager import get_server_lifecycle_orchestrator

logger = logging.getLogger(__name__)

SLEEP_INTERVAL_SECONDS = 300  # 5 minutes
IDLE_TIMEOUT_MINUTES = 15

def _auto_sleeper_cycle() -> None:
    """Find idle servers and suspend them locally (no S3 backup)."""
    settings = get_settings()
    cutoff_time = utcnow() - timedelta(minutes=IDLE_TIMEOUT_MINUTES)
    
    with SessionLocal() as db:
        # 1. Find all running servers with 0 players that haven't been active
        idle_servers = db.execute(
            select(Server).where(
                Server.state == ServerState.running,
                Server.players_online == 0,
                Server.last_activity < cutoff_time
            )
        ).scalars().all()
        
        if not idle_servers:
            return
            
        orchestrator = get_server_lifecycle_orchestrator()
        
        for server in idle_servers:
            sid = str(server.id)
            logger.info("[auto-sleeper] Suspending idle server %s (last active: %s)", sid, server.last_activity)
            
            try:
                # 2. Stop the systemd process using the orchestrator
                # stop_server expects a Server object, not a string!
                # It also sets server.state = ServerState.suspended internally.
                orchestrator.stop_server(server)
                
                # 3. Refresh RAM tracking on the node
                if server.node_id:
                    from blockhost_backend.services.node_capacity import refresh_node_allocated_ram
                    refresh_node_allocated_ram(db, server.node_id)
                db.commit()
                
            except Exception as e:
                logger.error("[auto-sleeper] Failed to sleep server %s: %s", sid, e)
                db.rollback()

        # 4. Check for idle AppDeployments
        from blockhost_backend.database.schema import AppDeployment, DeploymentState, CustomDomain, DomainStatus
        import httpx
        import asyncio
        
        idle_deployments = db.execute(
            select(AppDeployment).where(
                AppDeployment.state == DeploymentState.running,
                AppDeployment.node_id != None
            )
        ).scalars().all()
        
        for dep in idle_deployments:
            if not dep.node:
                continue
            did = str(dep.id)
            agent_url = f"http://{dep.node.ip_address}:{dep.node.agent_port}/agent/deployments/{did}/network-rx"
            try:
                with httpx.Client(timeout=5.0) as client:
                    resp = client.get(
                        agent_url,
                        headers={"Authorization": f"Bearer {settings.worker_agent_token}"}
                    )
                    if resp.status_code == 200:
                        rx_bytes = resp.json().get("rx_bytes", 0)
                        if dep.last_network_rx is not None and rx_bytes == dep.last_network_rx:
                            # It's idle!
                            logger.info("[auto-sleeper] Suspending idle AppDeployment %s", did)
                            
                            # 1. Stop it on agent
                            stop_url = f"http://{dep.node.ip_address}:{dep.node.agent_port}/agent/deployments/{did}/stop"
                            client.post(stop_url, headers={"Authorization": f"Bearer {settings.worker_agent_token}"})
                            
                            # 2. Update DB
                            dep.state = DeploymentState.suspended
                            
                            # 3. Register fallback route for custom domains
                            from blockhost_backend.services.ingress import ingress_service
                            active_domains = db.execute(
                                select(CustomDomain).where(
                                    CustomDomain.deployment_id == dep.id,
                                    CustomDomain.status == DomainStatus.active
                                )
                            ).scalars().all()
                            
                            # Since we are in a sync loop, we must run the async registration
                            loop = asyncio.new_event_loop()
                            for cd in active_domains:
                                loop.run_until_complete(ingress_service.register_fallback_route(cd.domain))
                            loop.close()
                                
                            db.commit()
                        else:
                            dep.last_network_rx = rx_bytes
                            db.commit()
            except Exception as e:
                logger.warning("[auto-sleeper] Failed to check/sleep deployment %s: %s", did, e)
                db.rollback()

        # 5. Check if any node became very empty (<10% active RAM) to trigger consolidation
        from blockhost_backend.database.schema import Node, NodeState
        from blockhost_backend.services.node_rebalancer import _node_active_ram_ratio, _rebalance_cycle
        
        online_nodes = db.execute(select(Node).where(Node.status == NodeState.online)).scalars().all()
        needs_rebalance = False
        for node in online_nodes:
            ratio = _node_active_ram_ratio(node, db)
            if 0 < ratio < 0.10:
                logger.info("[auto-sleeper] Node %s active RAM ratio is %.1f%% — triggering rebalancer for consolidation", node.name, ratio * 100)
                needs_rebalance = True
                break
                
        if needs_rebalance:
            try:
                # Trigger it in a new thread so we don't block the sleeper loop
                threading.Thread(target=_rebalance_cycle, name="auto-triggered-rebalance").start()
            except Exception as e:
                logger.error("[auto-sleeper] Failed to trigger rebalance cycle: %s", e)


def _auto_sleeper_loop() -> None:
    """Background daemon loop."""
    logger.info("Auto-Sleeper started (interval=%ds, timeout=%dm)", SLEEP_INTERVAL_SECONDS, IDLE_TIMEOUT_MINUTES)
    while True:
        try:
            time.sleep(SLEEP_INTERVAL_SECONDS)
            _auto_sleeper_cycle()
        except Exception:
            logger.exception("Auto-sleeper cycle failed")


_sleeper_started = False
_start_lock = threading.Lock()


def start_auto_sleeper_once() -> None:
    global _sleeper_started
    with _start_lock:
        if _sleeper_started:
            return
        _sleeper_started = True

    t = threading.Thread(target=_auto_sleeper_loop, daemon=True, name="AutoSleeper")
    t.start()
