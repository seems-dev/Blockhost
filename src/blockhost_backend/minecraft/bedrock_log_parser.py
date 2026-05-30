from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone


# Common Bedrock dedicated server stdout patterns (multiple versions).
_PLAYER_CONNECTED = re.compile(
    r"Player connected:\s*(?P<name>[^,\n]+?)(?:,\s*xuid:\s*(?P<xuid>\d+))?",
    re.IGNORECASE,
)
_PLAYER_DISCONNECTED = re.compile(
    r"Player disconnected:\s*(?P<name>[^,\n]+?)(?:,\s*xuid:\s*(?P<xuid>\d+))?",
    re.IGNORECASE,
)
_PLAYER_SPAWNED = re.compile(
    r"Player Spawned:\s*(?P<name>[^\s,]+)(?:\s+xuid:\s*(?P<xuid>\d+))?",
    re.IGNORECASE,
)
_SERVER_STARTED = re.compile(r"Server started\.?", re.IGNORECASE)
_SERVER_STOPPING = re.compile(r"(?:Stopping server|Server stop)", re.IGNORECASE)
_TICK_TIME = re.compile(
    r"(?:Average tick time|Tick time|mspt)[:\s]+(?P<ms>[0-9.]+)\s*ms?",
    re.IGNORECASE,
)
_TPS = re.compile(r"(?:TPS|tps)[:\s]+(?P<tps>[0-9.]+)", re.IGNORECASE)
_LEVEL_LOADED = re.compile(r"Level (?:name|loaded)[:\s]+(?P<level>[^\n]+)", re.IGNORECASE)
_LOG_LEVEL = re.compile(r"\[(?:\d{4}-\d{2}-\d{2}[^\]]*)?\s*(?P<level>INFO|WARN|ERROR|DEBUG)\]", re.IGNORECASE)


@dataclass
class BedrockLogEvent:
    raw: str
    level: str | None = None
    event_type: str | None = None
    player_name: str | None = None
    player_xuid: str | None = None
    tps: float | None = None
    tick_ms: float | None = None


@dataclass
class BedrockLiveStats:
    """Aggregated runtime stats derived from stdout parsing + process sampling."""

    started_at: datetime | None = None
    players_online: int = 0
    online_players: list[str] = field(default_factory=list)
    player_xuids: dict[str, str] = field(default_factory=dict)
    tps: float | None = None
    tick_ms: float | None = None
    log_lines_total: int = 0
    events_total: int = 0
    last_log_line: str | None = None
    last_event: str | None = None
    server_started: bool = False
    cpu_usage_percent: float | None = None
    ram_usage_mb: float | None = None
    recent_events: list[BedrockLogEvent] = field(default_factory=list)

    def uptime_seconds(self) -> float | None:
        if self.started_at is None:
            return None
        return max(0.0, (datetime.now(timezone.utc) - self.started_at).total_seconds())


class BedrockLogParser:
    """Stateful parser that tracks player sessions from Bedrock stdout lines."""

    def __init__(self, *, max_recent_events: int = 50) -> None:
        self._max_recent = max_recent_events
        self._connected: dict[str, str] = {}  # xuid or name -> display name
        self.stats = BedrockLiveStats()

    def reset(self) -> None:
        self._connected.clear()
        self.stats = BedrockLiveStats()

    def parse_line(self, line: str) -> BedrockLogEvent | None:
        stripped = line.strip()
        if not stripped:
            return None

        self.stats.log_lines_total += 1
        self.stats.last_log_line = stripped

        level_match = _LOG_LEVEL.search(stripped)
        level = level_match.group("level").upper() if level_match else None

        event = BedrockLogEvent(raw=stripped, level=level)

        if _SERVER_STARTED.search(stripped):
            event.event_type = "server_started"
            self.stats.server_started = True
            if self.stats.started_at is None:
                self.stats.started_at = datetime.now(timezone.utc)
            self._record_event(event, "Server started")
            return event

        if _SERVER_STOPPING.search(stripped):
            event.event_type = "server_stopping"
            self._record_event(event, "Server stopping")
            return event

        for pattern, etype in (
            (_PLAYER_CONNECTED, "player_connected"),
            (_PLAYER_DISCONNECTED, "player_disconnected"),
            (_PLAYER_SPAWNED, "player_spawned"),
        ):
            m = pattern.search(stripped)
            if m:
                name = (m.group("name") or "").strip()
                xuid = (m.group("xuid") or "").strip() or None
                event.event_type = etype
                event.player_name = name
                event.player_xuid = xuid
                self._apply_player_event(etype, name=name, xuid=xuid)
                label = f"{etype}: {name}" if name else etype
                self._record_event(event, label)
                return event

        tps_match = _TPS.search(stripped)
        if tps_match:
            try:
                self.stats.tps = float(tps_match.group("tps"))
                event.tps = self.stats.tps
                event.event_type = "tps"
                self._record_event(event, f"TPS {self.stats.tps:.1f}")
            except ValueError:
                pass
            return event

        tick_match = _TICK_TIME.search(stripped)
        if tick_match:
            try:
                ms = float(tick_match.group("ms"))
                self.stats.tick_ms = ms
                event.tick_ms = ms
                if ms > 0:
                    self.stats.tps = min(20.0, 1000.0 / ms)
                    event.tps = self.stats.tps
                event.event_type = "tick"
                self._record_event(event, f"Tick {ms:.1f}ms")
            except ValueError:
                pass
            return event

        if _LEVEL_LOADED.search(stripped):
            event.event_type = "level_loaded"
            self._record_event(event, "Level loaded")
            return event

        if level == "ERROR":
            event.event_type = "error"
            self._record_event(event, stripped[:120])
            return event

        return None

    def _apply_player_event(self, event_type: str, *, name: str, xuid: str | None) -> None:
        key = xuid or name.lower()
        if not key:
            return

        if event_type in {"player_connected", "player_spawned"}:
            display = name or key
            self._connected[key] = display
            if xuid:
                self.stats.player_xuids[display] = xuid
        elif event_type == "player_disconnected":
            self._connected.pop(key, None)
            # Also remove by display name if xuid key was used differently
            if name:
                for k, v in list(self._connected.items()):
                    if v.lower() == name.lower():
                        self._connected.pop(k, None)

        self.stats.players_online = len(self._connected)
        self.stats.online_players = sorted(set(self._connected.values()))

    def _record_event(self, event: BedrockLogEvent, label: str) -> None:
        self.stats.events_total += 1
        self.stats.last_event = label
        self.stats.recent_events.append(event)
        if len(self.stats.recent_events) > self._max_recent:
            self.stats.recent_events = self.stats.recent_events[-self._max_recent :]
