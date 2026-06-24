from __future__ import annotations

import logging
import re
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from blockhost_backend.database.db import SessionLocal
from blockhost_backend.database.schema import Ban
from blockhost_backend.runtime.interface import LogEntry

logger = logging.getLogger(__name__)

# Matches: "Player connected: PlayerName, xuid: 1234567890123456"
_PLAYER_CONNECTED_RE = re.compile(
    r"Player (?:connected|Spawned):\s*([^,]+)(?:,\s*xuid:\s*(\d+))?",
    re.IGNORECASE
)

class BanService:
    def __init__(self):
        # Cache for recently processed XUIDs to prevent duplicate kicks
        # Dict[str, float] -> xuid: timestamp
        self._recently_processed_xuids: dict[str, float] = {}
        self._ttl_seconds = 10.0

    def _clean_cache(self):
        now = time.monotonic()
        to_remove = [k for k, v in self._recently_processed_xuids.items() if now - v > self._ttl_seconds]
        for k in to_remove:
            self._recently_processed_xuids.pop(k, None)

    def attach_listener(self, server_id: str, orchestrator):
        def listener(entry: LogEntry):
            m = _PLAYER_CONNECTED_RE.search(entry.line)
            if not m:
                return
            
            player_name = m.group(1).strip()
            xuid = m.group(2) if m.lastindex >= 2 else None
            
            if not xuid:
                return

            # Prevent duplicate processing within TTL
            self._clean_cache()
            now = time.monotonic()
            if xuid in self._recently_processed_xuids:
                return
            self._recently_processed_xuids[xuid] = now

            self._check_and_enforce_ban(server_id, xuid, player_name, orchestrator)

        orchestrator.add_log_listener(server_id, listener)

    def _check_and_enforce_ban(self, server_id: str, xuid: str, player_name: str, orchestrator):
        try:
            server_uuid = uuid.UUID(server_id)
        except ValueError:
            return

        db = SessionLocal()
        try:
            now_dt = datetime.now(timezone.utc)
            ban = db.scalars(
                select(Ban)
                .where(Ban.server_id == server_uuid, Ban.xuid == xuid, Ban.active == True)
                .order_by(Ban.banned_at.desc())
            ).one_or_none()

            if ban:
                # Add expired ban check
                if ban.expires_at and ban.expires_at <= now_dt:
                    ban.active = False
                    ban.unbanned_at = ban.expires_at or now_dt
                    db.add(ban)
                    db.commit()
                else:
                    self._kick_player(server_id, player_name, ban.reason, orchestrator)
        except Exception as e:
            logger.error("Failed to enforce ban for player %s on server %s: %s", player_name, server_id, e)
        finally:
            db.close()

    def kick_player_if_online(self, server_id: str, xuid: str, player_name: str, reason: str | None, orchestrator):
        """Immediately kick a player if they are currently online on the given server."""
        if not orchestrator.is_running(server_id):
            return

        online_players = orchestrator.get_online_players_with_xuid(server_id)
        for name, p_xuid in online_players.items():
            if p_xuid == xuid or name.lower() == (player_name or "").lower():
                self._kick_player(server_id, name, reason, orchestrator)
                break

    def _kick_player(self, server_id: str, player_name: str, reason: str | None, orchestrator):
        reason_msg = reason or "You are banned."
        cmd = f'kick "{player_name}" §cBanned: {reason_msg}'
        try:
            orchestrator.send_command(server_id, cmd)
            logger.info("Enforced ban: kicked %s from server %s", player_name, server_id)
        except Exception as e:
            logger.error("Failed to send kick command to server %s: %s", server_id, e)

# Singleton instance
ban_service = BanService()
