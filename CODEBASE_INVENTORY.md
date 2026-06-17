# BlockHost Backend Codebase Inventory

**Last Updated:** 2026-06-17  
**Total Python Files:** 41 in blockhost_backend + 1 in blockhost_worker  
**Framework:** FastAPI (REST API) + WebSocket console streaming  
**Database:** SQLAlchemy ORM (PostgreSQL/SQLite support)  
**Runtime:** systemd-based process management for Minecraft Bedrock servers  

---

## 📊 High-Level Architecture

```
FastAPI (main.py)
├── REST API Routes (api/)
│   ├── Authentication & Authorization
│   ├── Server Lifecycle Management
│   ├── Backup/Restore Operations
│   ├── File Management
│   ├── Version Management
│   └── WebSocket Console Streaming
├── Database Layer (database/)
│   └── SQLAlchemy ORM Models
├── Orchestration (orchestrator/)
│   ├── Server Lifecycle Orchestration
│   ├── Backup Job Management
│   └── Resource Management
├── Runtime Drivers (runtime/)
│   └── systemd-based Bedrock Process Control
├── Minecraft Integration (minecraft/)
│   ├── Server Property Management
│   ├── RakNet Protocol Pinging
│   ├── Version Management & Downloads
│   └── Port Allocation
├── Security (core/security.py)
│   ├── JWT Token Management
│   ├── Password Hashing (pbkdf2_sha256)
│   └── OAuth (Google)
└── Configuration (config/config_manager.py)
    └── Environment-based Settings
```

---

## 📁 Complete File Structure & Descriptions

### **Main Entry Point**

#### [main.py](src/blockhost_backend/main.py)
- **Purpose:** FastAPI application factory and startup hooks
- **Key Responsibilities:**
  - Creates FastAPI app with CORS middleware (allows all origins for Flutter Web)
  - Registers all API route routers (auth, servers, backups, files, versions)
  - Startup hook: Auto-creates database tables and applies migrations
  - Starts backup scheduler on app initialization
  - Health check endpoint at `/health`
- **Dependencies:** All route modules, database schema, backup scheduler
- **Configuration:** CORS max_age = 600 seconds

---

## 🗄️ Database Layer

### [database/db.py](src/blockhost_backend/database/db.py)
- **Purpose:** Database connection and session management
- **Key Features:**
  - SQLAlchemy engine creation (supports SQLite and PostgreSQL)
  - Connection pooling with `pool_pre_ping` for stale connection detection
  - SessionLocal factory for dependency injection
  - Automatic parent directory creation for SQLite files
- **Database URL:** Configurable via `DATABASE_URL` env var (defaults to SQLite)

### [database/schema.py](src/blockhost_backend/database/schema.py)
- **Purpose:** SQLAlchemy ORM model definitions
- **Enums Defined:**
  - `SubscriptionTier`: free, premium (controls server limits and resource allocation)
  - `ServerState`: created, provisioning, running, syncing, suspended
  - `VMProvider`: oracle_free, hetzner, local_bedrock
  - `BackupStatus`: pending, running, held_saves, copying, compressing, verifying, completed, failed, cancelled, deleting, deleted, expired
  - `RestoreStatus`: pending, running, validating_backup, stopping_server, extracting, validating_restore, swapping, starting_server, completed, failed, rolled_back, cancelled
  - `BackupKind`: manual, scheduled, system
  - `BackupStorageBackend`: local, s3
  - `BackupConsistencyMethod`: save_hold, server_stop, filesystem_snapshot

**Core Models:**

| Model | Purpose | Key Fields |
|-------|---------|-----------|
| **User** | User accounts with local/Google OAuth | id, email, auth_hash, nickname, blockcoin_balance, subscription_tier, google_sub, auth_provider |
| **Server** | Minecraft Bedrock server instances | id, owner_id, world_name, join_code, state, vm_ipv4, vm_port, mc_config (JSON), backup_count, world_size_bytes |
| **Ban** | Player ban management | id, server_id, xuid, player_name, reason, active, banned_at, expires_at, unbanned_at |
| **Backup** | Backup metadata and status | id, server_id, owner_id, kind, status, archive_format, size_bytes, checksum_sha256, consistency_method |
| **BackupJob** | Backup execution tracking | id, backup_id, server_id, status, phase, progress_percent, attempts, worker_id |
| **BackupSchedule** | Scheduled backup configuration | id, server_id, enabled, interval_minutes, retention_count, skip_if_server_offline |
| **RestoreJob** | Restore execution tracking | id, backup_id, server_id, status, phase, rollback_enabled, rollback_path |
| **BlockcoinTransaction** | In-game currency tracking | user_id, amount, reason (referral/purchase) |

### [database/types.py](src/blockhost_backend/database/types.py)
- **Purpose:** Custom SQLAlchemy type definitions
- **GUID Type:** Platform-independent UUID storage (native UUID on PostgreSQL, CHAR(36) on SQLite)
- **JSONType:** Alias for SQLAlchemy JSON type (JSONB on PostgreSQL)

---

## 🔐 Security & Authentication

### [core/security.py](src/blockhost_backend/core/security.py)
- **Purpose:** Cryptographic operations and token management
- **Key Functions:**
  - `hash_password()`: Uses pbkdf2_sha256 with bcrypt (64-byte SHA256 normalization for long passwords)
  - `verify_password()`: Constant-time password comparison
  - `create_access_token()`: JWT token (default 24-hour expiry)
  - `create_refresh_token()`: JWT token (default 30-day expiry)
  - `decode_token()`: Validates token type (access/refresh), signature, and subject
- **Token Structure:** JWT with `sub` (user_id), `type` (access/refresh), `iat`, `exp`, `jti` (unique ID)
- **Algorithm:** HS256 (HMAC SHA-256)
- **Secret Key:** Configurable via `JWT_SECRET_KEY` env var

### [api/deps.py](src/blockhost_backend/api/deps.py)
- **Purpose:** Dependency injection for authentication
- **Functions:**
  - `get_current_user()`: Extracts and validates Bearer token, returns authenticated User
  - `get_ws_user()`: WebSocket authentication variant (token from query param)
  - `bearer_scheme`: FastAPI HTTPBearer scheme
- **Authorization:** All routes require valid JWT access token

### [api/auth.py](src/blockhost_backend/api/auth.py)
- **Routes:**
  - `POST /api/auth/signup` → Creates new user, generates access/refresh tokens
  - `POST /api/auth/login` → Authenticates email/password, returns tokens
  - `POST /api/auth/refresh` → Issues new access token from refresh token
- **Features:**
  - Email uniqueness validation
  - Referral code system (8-char alphanumeric)
  - Referral bonus (100 blockcoin to new user)
  - Automatic referrer lookup and linking
- **Response:** `AuthResponse` with tokens + user info

### [api/google_auth.py](src/blockhost_backend/api/google_auth.py)
- **Route:** `POST /api/auth/google`
- **Purpose:** OAuth2 login with Google ID tokens
- **Flow:**
  1. Client sends Google ID token
  2. Backend verifies with Google's public key
  3. Looks up user by `google_sub` (stable identifier)
  4. Falls back to email for linking existing accounts
  5. Creates new user if neither found
  6. Issues access/refresh tokens
- **Security:** Validates token audience against configured Google Client ID

---

## 🖥️ API Routes

### [api/servers.py](src/blockhost_backend/api/servers.py) — Server Lifecycle Management
**Most complex module (1000+ lines)**

**REST Routes:**
- `POST /api/servers` → Create new server
  - Validates subscription tier limits (1 for free, 3 for premium)
  - Allocates UDP port from configured range (19132-19232)
  - Materializes server directory from version template
  - Stores mc_config (Bedrock server.properties-like JSON)
  - Returns `ServerOut` with join code

- `GET /api/servers` → List user's servers
- `GET /api/servers/{server_id}` → Get server details with stats snapshot
- `POST /api/servers/{server_id}/start` → Start server process
  - Resolves version template from configured directory
  - Writes server.properties file
  - Delegates to ServerLifecycleOrchestrator.start_server()
  - Attaches ban listener for automatic enforcement

- `POST /api/servers/{server_id}/stop` → Stop server process
- `POST /api/servers/{server_id}/restart` → Stop then start
- `DELETE /api/servers/{server_id}` → Delete server (soft delete with retention)

**Statistics & Monitoring:**
- `GET /api/servers/{server_id}/stats` → Server stats (cached 2 seconds)
  - CPU usage (%), RAM usage (MB), uptime
  - RakNet ping latency
  - Online player count and XUID list
  - Comparison against resource limits

**Console & Logging:**
- `GET /api/servers/{server_id}/logs?tail=200` → Last N log lines (capped at 500)
- `WebSocket /api/servers/{server_id}/console/ws?token=JWT` → Real-time console streaming
  - Sends last 500 lines as history on connect
  - Bridges systemd runtime logs to async WebSocket queue
  - Queue size: configurable (default 1000 lines per connection)
  - Receives JSON commands: `{"type": "command", "command": "..."}`
  - Implements flow control (drops oldest entry when queue full)

**Server Configuration:**
- `GET /api/servers/{server_id}/config` → Current Bedrock config
- `PATCH /api/servers/{server_id}/config` → Update world name, gamemode, difficulty, max_players, etc.
- Config persists to `mc_config` JSON field in database

**Player Management & Bans:**
- `POST /api/servers/{server_id}/bans` → Create ban
- `GET /api/servers/{server_id}/bans` → List active/inactive bans
- `DELETE /api/servers/{server_id}/bans/{ban_id}` → Unban player
- Ban enforcement: Automatic kick on join if banned (monitored via log listener)

**Commands (teleport, clearinventory, etc.):**
- `POST /api/servers/{server_id}/teleport` → `tp player x y z`
- `POST /api/servers/{server_id}/clear-inventory` → Clear player inventory
- Implemented via `_ORCHESTRATOR.send_command()`

**Internal Functions:**
- `_allocate_port()`: Finds free UDP port in configured range
- `_get_server_stats_snapshot()`: Caches stats for 2 seconds, refreshes in background
- `_compute_server_stats()`: Gathers CPU/RAM from runtime, queries bedrock_ping, gets online players
- `_ensure_version_on_disk()`: Pre-downloads Bedrock version if needed
- `_start_bedrock_process()`: Materializes server dir, writes properties, delegates to runtime
- `_sanitize_version()`: Prevents shell injection in version strings

### [api/backups.py](src/blockhost_backend/api/backups.py) — Backup & Restore Operations

**REST Routes:**
- `POST /api/servers/{server_id}/backups` → Create manual backup
  - Submits `BackupJob` to thread pool
  - Returns job details with `already_running` flag
  
- `GET /api/servers/{server_id}/backups` → List backups (paginated)
  - Returns backup metadata (size, checksum, created_at, status)
  
- `GET /api/servers/{server_id}/backups/{backup_id}/download` → Download backup archive
  
- `DELETE /api/servers/{server_id}/backups/{backup_id}` → Delete backup (state transitions)

- `GET /api/servers/{server_id}/backups/{backup_id}/status` → Poll backup job progress

**Scheduled Backups:**
- `GET /api/servers/{server_id}/backups/schedules` → List schedules
- `POST /api/servers/{server_id}/backups/schedules` → Create schedule
  - `interval_minutes`: How often to run
  - `retention_count`: Keep last N backups
  - `skip_if_server_offline`: Don't backup stopped servers
  - `defer_if_job_active`: Don't overlap jobs
  
- `PATCH /api/servers/{server_id}/backups/schedules/{schedule_id}` → Modify schedule
- `DELETE /api/servers/{server_id}/backups/schedules/{schedule_id}` → Delete schedule

**Restore Operations:**
- `POST /api/servers/{server_id}/backups/{backup_id}/restore` → Create restore job
  - Submits `RestoreJob` to thread pool
  - Optional rollback if restore fails
  - Optional restart after restore
  
- `GET /api/servers/{server_id}/restores/{restore_id}/status` → Poll restore progress

### [api/files.py](src/blockhost_backend/api/files.py) — File Management

**REST Routes:**
- `GET /api/servers/{server_id}/files/list?path=...` → List directory contents
  - Restricted to safe roots: `worlds`, `development_behavior_packs`, `development_resource_packs`, `development_skin_packs`
  - Prevents path traversal
  
- `GET /api/servers/{server_id}/files/read?path=...` → Read text file
  - Max size: 5 MB
  - Text extensions: `.json`, `.txt`, `.mcfunction`, `.properties`, `.yml`, `.yaml`, `.ini`, `.cfg`, `.lang`, `.mcmeta`
  
- `POST /api/servers/{server_id}/files/write` → Write/create text file
  - Max size: 5 MB
  - Same extension whitelist
  
- `POST /api/servers/{server_id}/files/upload` → Upload binary file
  - Max size: 100 MB
  - Restricted to allowed roots

**Security Model:**
- Path resolution with symlink prevention
- Whitelist of allowed root directories
- Owner-only access (via server ownership check)

### [api/versions.py](src/blockhost_backend/api/versions.py) — Bedrock Version Management

**REST Routes:**
- `GET /api/versions` → List installed versions + recommended version
- `GET /api/versions/recommended` → Get recommended version + installation status
- `GET /api/versions/catalog` → Full version catalog (available, recommended, installed)
- `POST /api/versions/{version}/download` → Start background download
  - Downloads in background thread
  - Returns immediately with status
  - Emits progress events
  
- `GET /api/versions/{version}/download/status` → Poll download progress
  - Tracks: `status`, `stage` (fetching/downloading/installing), `bytes`, `error`

**Implementation Details:**
- Reads manifest from `versions/manifest.json`
- Platform detection (Linux x86_64 vs Windows x86_64)
- ZIP extraction with Zip-slip vulnerability prevention
- Progress callbacks for UI feedback

---

## 🎯 Orchestration Layer

### [orchestrator/orchestrator.py](src/blockhost_backend/orchestrator/orchestrator.py)
- **Purpose:** Setup-stage placeholder for VM orchestration
- **Current Implementation:** Returns a hardcoded `MinecraftEndpoint` (minecraft_host, minecraft_port)
- **Future:** Will integrate with Oracle Free Tier or Hetzner Cloud APIs

### [orchestrator/lifecycle_manager.py](src/blockhost_backend/orchestrator/lifecycle_manager.py)
- **Purpose:** Factory for getting configured server lifecycle orchestrator (singleton)
- **Runtime Driver:** Currently hardcoded to `systemd`
- **Returns:** `ServerLifecycleOrchestrator` wrapping `SystemdRuntime`

### [orchestrator/server_lifecycle.py](src/blockhost_backend/orchestrator/server_lifecycle.py)
- **Purpose:** Server lifecycle state machine and operations
- **Key Methods:**
  - `start_server()`: Provisions server directory, writes properties, starts via runtime
  - `stop_server()`: Stops process via runtime, updates state to suspended
  - `restart_server()`: Stop + start
  - `get_status()`: Process running state, uptime, online players
  - `get_stats()`: CPU%, RAM usage
  - `read_logs()`: Last N log lines from runtime
  - `send_command()`: Send Minecraft command to running server
  - `add_log_listener()` / `remove_log_listener()`: Subscribe to real-time logs

**Resource Allocation:**
- Resolves user subscription tier from database
- Looks up resource limits: Free=512MB RAM/50% CPU, Premium=1024MB RAM/100% CPU
- Passes to runtime via `RuntimeStartRequest`

**Ban Listener Integration:**
- Attaches `ban_service` listener on server start
- Automatically kicks players matching active bans

### [orchestrator/resources.py](src/blockhost_backend/orchestrator/resources.py)
- **Purpose:** Resource limits by subscription tier
- **Data Structure:** `TIER_RESOURCE_LIMITS` dict
  ```python
  {
    SubscriptionTier.free: {"ram_mb": 512, "cpu_quota_pct": 50},
    SubscriptionTier.premium: {"ram_mb": 1024, "cpu_quota_pct": 100},
  }
  ```

### [orchestrator/backup.py](src/blockhost_backend/orchestrator/backup.py)
- **Purpose:** Backup/restore job execution and scheduling
- **Size:** 400+ lines, highly complex

**Key Components:**

1. **Thread Pool Execution:**
   - `_EXECUTOR`: ThreadPoolExecutor with configurable workers (default 2)
   - `submit_backup_job()` / `submit_restore_job()`: Queue jobs for async processing

2. **Server-Level Locks:**
   - `_SERVER_LOCKS`: Per-server mutex to prevent concurrent backup/restore
   - `server_backup_restore_lock()`: Context manager for acquiring lock

3. **Backup Job Execution (`run_backup_job`):**
   - States: pending → running → held_saves → copying → compressing → verifying → completed
   - Workflow:
     1. Validate server directory exists
     2. Query database for backup metadata
     3. Hold saves on running server (pause game for consistent snapshot)
     4. Copy world files + server config + packs to temp directory
     5. Create tar.gz archive with progress tracking
     6. Compute SHA256 checksum
     7. Move to storage backend (local or S3)
     8. Update backup record with size/checksum/status
     9. Apply retention policy (delete old backups)

4. **Restore Job Execution (`run_restore_job`):**
   - States: pending → running → validating_backup → stopping_server → extracting → validating_restore → swapping → starting_server → completed
   - Workflow:
     1. Validate backup archive integrity
     2. Stop server if running
     3. Extract to temp directory
     4. Validate restored world
     5. Swap directories (atomic move with rollback support)
     6. Restart server if desired
   - Rollback: Keeps backup of pre-restore world for recovery

5. **Scheduling:**
   - `start_backup_scheduler_once()`: Starts background polling thread
   - `_poll_schedules()`: Runs every 60 seconds (configurable)
   - Evaluates enabled schedules, triggers jobs respecting deferral rules
   - Tracks `last_run_at`, `next_run_at`, `consecutive_failures`

**Storage Paths:**
- Local: `{backup_storage_dir}/{server_id}/objects/{backup_id}.tar.gz`
- S3: Key format `{server_id}/objects/{backup_id}.tar.gz`

**Included Paths in Backup:**
- World data: `bedrock/worlds/{world_name}/`
- Server config files: `server.properties`, `allowlist.json`, `permissions.json`, etc.
- Behavior/resource/skin pack references

### [orchestrator/manager.py](src/blockhost_backend/orchestrator/manager.py)
- **Purpose:** Simple factory to get orchestrator instance (setup stage only)
- **Returns:** `Orchestrator` with hardcoded minecraft_host/port from config

### [orchestrator/worker_client.py](src/blockhost_backend/orchestrator/worker_client.py)
- **Purpose:** HTTP client for remote worker service (Docker container orchestration)
- **Methods:**
  - `start_bedrock()`: HTTP POST to `/containers/start`
  - `stop_bedrock()`: HTTP POST to `/containers/stop`
  - `remove_bedrock()`: HTTP POST to `/containers/remove`
- **Authentication:** Bearer token via `X-Worker-Token` header
- **Currently Unused:** Worker service not integrated in current systemd-based setup

---

## 🚀 Runtime Layer

### [runtime/interface.py](src/blockhost_backend/runtime/interface.py)
- **Purpose:** Protocol (interface) definitions for runtime drivers
- **Dataclasses:**
  - `LogEntry`: `ts` (optional), `line` (log text)
  - `RuntimeStartRequest`: server_id, server_dir, port, version, executable_name, ram_mb, cpu_quota_pct
  - `RuntimeStartResult`: runtime_id, port, actual_version
  - `RuntimeStatus`: running (bool), runtime_id, active_state, uptime_seconds, online_players
  - `RuntimeResourceStats`: cpu_usage (%), ram_usage_mb

- **Runtime Protocol Methods:**
  - `start_server(request)`: Start Bedrock process
  - `stop_server(server_id)`: Stop process
  - `restart_server(request)`: Stop + start
  - `get_status(server_id)`: Current runtime state
  - `get_stats(server_id)`: Resource usage
  - `get_online_players_with_xuid(server_id)`: Dict[player_name, xuid]
  - `read_logs(server_id, tail)`: Last N log lines
  - `send_command(server_id, command)`: Send console command
  - `add_log_listener()` / `remove_log_listener()`: Real-time log subscription

### [runtime/systemd_runtime.py](src/blockhost_backend/runtime/systemd_runtime.py)
- **Purpose:** systemd-based process management for Bedrock servers
- **Implementation:** Production-safe, uses systemd as source of truth

**Key Operations:**

1. **Start Server:**
   - Creates named FIFO (`stdin.fifo`) for process stdin
   - Spawns `systemd-run --user --unit {unit-name}` process
   - Applies memory limit via `MemoryMax` property
   - Applies CPU quota via `CPUQuota` property (percentage)
   - Sets `WorkingDirectory` to server directory
   - Runs Bedrock executable with FIFO stdin
   - Waits for server to be ready (ping until response)
   - Starts background log stream for player tracking
   - Returns `RuntimeStartResult` with unit name, port, detected version

2. **Stop Server:**
   - `systemctl --user stop {unit}`
   - Resets failed state
   - Clears player tracking cache
   - Stops log stream

3. **Status Polling:**
   - `systemctl --user is-active {unit}`
   - Returns `running`, `uptime_seconds`, list of online players

4. **Resource Stats:**
   - Parses systemd properties for CPU/memory usage
   - Returns percentage and absolute values

5. **Player Tracking:**
   - Background log stream parses player events
   - Regex: `Player (connected|Spawned): {name}, xuid: {xuid}`
   - Maintains in-memory dict: `{player_name: xuid}`
   - Updated dynamically as logs arrive

6. **Log Streaming:**
   - `journalctl -u {unit} -f` background process
   - Thread-safe queue for multiple listeners
   - Supports `add_log_listener()` / `remove_log_listener()`
   - Each listener is a callable that receives `LogEntry` objects

7. **Command Sending:**
   - Writes commands to FIFO stdin
   - Used for in-game commands (kick, teleport, etc.)

**Systemd Unit Names:**
- Pattern: `blockhost-bedrock-{server_uuid}`
- Sanitized: Non-alphanumeric characters replaced with `-`

---

## 🎮 Minecraft Integration

### [minecraft/bedrock_ping.py](src/blockhost_backend/minecraft/bedrock_ping.py)
- **Purpose:** RakNet unconnected ping for Bedrock server discovery
- **Protocol:** RakNet magic bytes + client GUID + timestamp
- **Response:** Parses Bedrock pong payload (MOTD, version, player count, gamemode)
- **Timeout:** 1 second default
- **Returns:** `BedrockPingResult` with latency_ms, server_guid, parsed payload dict

**Parsed Pong Fields:**
```
edition, motd, protocol, version, players_online, players_max, 
server_id, motd2, gamemode
```

### [minecraft/bedrock_properties.py](src/blockhost_backend/minecraft/bedrock_properties.py)
- **Purpose:** Generate `server.properties` file for Bedrock
- **Data Structure:** `BedrockServerProperties` dataclass
- **Written Keys:**
  - `server-name`, `gamemode`, `difficulty`, `max-players`
  - `allow-cheats`, `online-mode`, `level-name`, `level-seed`
  - `server-port`, `server-portv6` (UDP ports, sequentially allocated)
- **Format:** Key=value, newline-separated

### [minecraft/port_alloc.py](src/blockhost_backend/minecraft/port_alloc.py)
- **Purpose:** UDP port allocation and availability checking
- **Functions:**
  - `is_udp_port_free()`: Dual-stack IPv4/IPv6 binding test
  - `pick_free_udp_port()`: Find first available port in range
- **Port Range:** Configurable (default 19132-19232, 100 ports)
- **Conflict Detection:** Checks both UDP and TCP to avoid confusion

### [minecraft/bedrock_download.py](src/blockhost_backend/minecraft/bedrock_download.py)
- **Purpose:** Download and extract Bedrock server versions
- **Manifest Format:** JSON with version entries mapping to platform-specific URLs
  ```json
  {
    "recommended_version": "1.21.0",
    "versions": {
      "1.21.0": {
        "linux_x86_64": {"url": "...", "sha256": "..."},
        "windows_x86_64": {"url": "...", "sha256": "..."}
      }
    }
  }
  ```

**Key Functions:**
- `recommended_version()`: Extract from manifest
- `available_versions()`: List all versions in manifest
- `resolve_download_target()`: Get URL for current platform
- `ensure_version_installed()`: Download if needed, cache on disk
- `safe_extract_zip()`: Unzip with Zip-slip prevention

**Download Flow:**
1. Check if already installed
2. Query manifest for platform-specific URL
3. Stream download with progress callbacks
4. Verify SHA256 if available
5. Extract to version directory
6. Return path

---

## 🔧 Configuration

### [config/config_manager.py](src/blockhost_backend/config/config_manager.py)
- **Purpose:** Centralized configuration via environment variables
- **Framework:** Pydantic BaseSettings

**Key Settings:**
| Setting | Default | Purpose |
|---------|---------|---------|
| `DATABASE_URL` | sqlite:///./database/blockhost.db | Database connection string |
| `REDIS_URL` | redis://localhost:6379/0 | Redis (optional, for future use) |
| `JWT_SECRET_KEY` | change-me | Token signing key |
| `JWT_ACCESS_TOKEN_EXPIRE_SECONDS` | 86400 | Token lifetime (24 hours) |
| `JWT_REFRESH_TOKEN_EXPIRE_SECONDS` | 2592000 | Refresh token lifetime (30 days) |
| `MINECRAFT_PUBLIC_HOST` | 192.168.29.102 | IP address clients connect to |
| `MINECRAFT_PORT` | 19132 | Base Bedrock port |
| `BEDROCK_VERSIONS_DIR` | versions/ | Directory storing Bedrock versions |
| `BEDROCK_SERVERS_DIR` | servers/ | Directory storing server instances |
| `BEDROCK_LOGS_DIR` | logs/ | Directory storing server logs |
| `BEDROCK_PORT_RANGE_START` | 19132 | UDP port range start |
| `BEDROCK_PORT_RANGE_END` | 19232 | UDP port range end (100 servers max) |
| `BACKUP_STORAGE_DIR` | backups/ | Local backup storage |
| `BACKUP_TEMP_DIR` | backups/tmp/ | Temporary backup staging |
| `BACKUP_WORKER_THREADS` | 2 | Thread pool size for backup jobs |
| `BACKUP_SCHEDULER_ENABLED` | true | Enable scheduled backups |
| `BACKUP_SCHEDULER_POLL_SECONDS` | 60 | Schedule polling interval |
| `CONSOLE_QUEUE_MAX_SIZE` | 1000 | Max log lines per WebSocket connection |
| `BEDROCK_RUNTIME_DRIVER` | systemd | Runtime driver (only systemd supported) |
| `WORKER_AGENT_URL` | http://localhost:9000 | Docker worker service URL |
| `WORKER_AGENT_TOKEN` | change-me-in-dev | Worker authentication token |
| `GOOGLE_CLIENT_ID` | 264249... | Google OAuth client ID |

---

## 🔐 Services & Utilities

### [services/ban_service.py](src/blockhost_backend/services/ban_service.py)
- **Purpose:** Automatic ban enforcement and player kick
- **Mechanism:**
  - Attaches log listener to running server
  - Parses player join events
  - Queries database for active bans
  - Immediately kicks banned players
- **Cache:** Recent XUIDs cached for 10 seconds (prevents duplicate kicks)
- **Flow:**
  1. Log listener detects "Player connected: {name}, xuid: {xuid}"
  2. Looks up ban in database (unique on server_id, xuid, active=true)
  3. Checks expiration (auto-deactivate if expired)
  4. Sends kick command if banned: `kick "{name}" §cBanned: {reason}`

### [utils.py](src/blockhost_backend/utils.py)
- **Purpose:** Utility functions
- **Functions:**
  - `generate_referrer_code()`: 8-char alphanumeric code
  - `generate_join_code()`: Unique server invite code
    - Format: `BLOCKHOST-{user_uuid_prefix}-{server_uuid_prefix}-{checksum}`
    - Checksum: Byte sum modulo 36, prevents simple typos

### [models/server_modal.py](src/blockhost_backend/models/server_modal.py)
- **Purpose:** Pydantic model for server data
- **Currently:** Mirrors Server ORM model for serialization

---

## 📊 API Schemas

### [api/schemas.py](src/blockhost_backend/api/schemas.py)
- **Purpose:** Request/response Pydantic models
- **Key Classes:**
  - `SignupRequest`: email, password, nickname, phone, referrer_code
  - `LoginRequest`: email, password
  - `CreateServerRequest`: world_name, tier, config
  - `ServerOut`: Full server info (owner, state, config, IP:port)
  - `ServerDetail`: ServerOut + minecraft_host, minecraft_port
  - `BedrockServerStats`: CPU%, RAM%, players, latency, MOTD
  - `BedrockConfig`: Bedrock properties (gamemode, difficulty, max_players, etc.)
  - `CreateBackupRequest`: force_offline, backup_name, backup_description
  - `CreateRestoreRequest`: rollback_enabled, restart_after_restore
  - `BanCreateRequest`: xuid, player_name, reason, duration, expires_at
  - `FileInfo`: name, path, is_dir, size, last_modified

---

## 📦 Worker Service

### [blockhost_worker/main.py](src/blockhost_worker/main.py)
- **Purpose:** Standalone FastAPI service for Docker container orchestration
- **Role:** Remote worker for production deployment (not currently used in local setup)
- **Endpoints:**
  - `POST /containers/start` → Spawn Bedrock Docker container
  - `POST /containers/stop` → Stop container
  - `POST /containers/remove` → Remove container
- **Authentication:** Token-based via `X-Worker-Token` header
- **Port Allocation:** Selects available host port for RakNet (19132)
- **Environment Variables:** Passed to container for version, properties, etc.

---

## 🔄 Request/Response Flow Examples

### Creating & Starting a Server

```
1. POST /api/servers (user: free tier)
   ├─ Check server limit (1 for free tier) ✓
   ├─ Allocate UDP port (e.g., 19133) ✓
   ├─ Download Bedrock version if needed ✓
   ├─ Create Server in database (state=created) ✓
   └─ Return ServerOut with join_code

2. POST /api/servers/{id}/start
   ├─ Query Server from database ✓
   ├─ Materialize server directory (copy from version template) ✓
   ├─ Write server.properties file ✓
   ├─ Call SystemdRuntime.start_server()
   │  ├─ Create stdin.fifo for input ✓
   │  ├─ Run systemd-run --unit blockhost-bedrock-{uuid} ✓
   │  ├─ Apply resource limits (512MB RAM, 50% CPU for free) ✓
   │  ├─ Wait for server ready (RakNet ping) ✓
   │  └─ Return RuntimeStartResult
   ├─ Update Server (state=running, vm_id=unit_name, vm_ipv4, vm_port) ✓
   ├─ Attach ban_service log listener ✓
   └─ Return ServerActionResponse
```

### Backup Workflow

```
1. POST /api/servers/{id}/backups (manual backup)
   ├─ Create Backup record (status=pending) ✓
   ├─ Create BackupJob record ✓
   ├─ Submit to thread pool → run_backup_job(job_id) ✓
   └─ Return BackupJobOut with already_running=false

2. Background thread: run_backup_job()
   ├─ Acquire server lock ✓
   ├─ Update job status=running ✓
   ├─ Hold saves on running server (pause game) ✓
   ├─ Copy world files + config to temp directory ✓
   ├─ Create tar.gz archive with progress ✓
   ├─ Compute SHA256 checksum ✓
   ├─ Move to storage (local: backups/{server_id}/objects/{backup_id}.tar.gz) ✓
   ├─ Update Backup record (status=completed, size, checksum) ✓
   ├─ Apply retention policy (delete old backups) ✓
   └─ Release server lock

3. GET /api/servers/{id}/backups/{bid}/status
   └─ Return BackupJobOut with progress_percent, total_bytes
```

### Console Streaming

```
1. WebSocket /api/servers/{id}/console/ws?token=JWT
   ├─ Authenticate user via JWT ✓
   ├─ Verify user owns server ✓
   ├─ Check server is running ✓
   ├─ Send last 500 log lines as history ✓
   └─ Connection accepted

2. Server sends logs
   ├─ SystemdRuntime log listener (journalctl -f thread) captures logs ✓
   ├─ Parses player events for XUID tracking ✓
   ├─ Enqueues to bounded queue (maxsize=1000) ✓
   ├─ WebSocket sends JSON: {"type": "log", "log": {...}} ✓
   └─ Repeat for each new log line

3. Client sends command
   ├─ WebSocket receives JSON: {"type": "command", "command": "..."} ✓
   ├─ Calls SystemdRuntime.send_command() ✓
   ├─ Command written to stdin.fifo ✓
   └─ Bedrock processes command

4. WebSocket disconnect
   ├─ Removes log listener ✓
   ├─ Cleans up queue ✓
   └─ Connection closed
```

---

## 🔍 Key Architectural Decisions

### 1. **Database-Driven State Machine**
- Server states (created, provisioning, running, suspended) stored in database
- Backup/restore states tracked in job records
- Enables resumption after service restart

### 2. **systemd as Source of Truth**
- Actual process state lives in systemd, not database
- Database is secondary (for user queries, UI display)
- Prevents state mismatches between database and actual processes

### 3. **Per-Server Locking for Backup/Restore**
- Threading.Lock per server_id prevents concurrent jobs
- Held during entire backup operation (can take minutes)
- Essential for data consistency

### 4. **WebSocket Log Streaming with Bounded Queue**
- Async WebSocket receives logs from sync runtime thread
- Queue has maximum size (default 1000 lines) to prevent unbounded memory growth
- Drops oldest entries when full (acceptable for log streaming)

### 5. **Three-Tier Resource Management**
- Subscription tier (free/premium) determines limits
- Orchestrator allocates resources (RAM, CPU) to runtime
- systemd enforces limits via MemoryMax and CPUQuota

### 6. **RakNet Ping for Server Availability**
- UDP protocol, same as Bedrock server itself
- Provides MOTD, player count, version in single roundtrip
- Used to detect server readiness on start

### 7. **Storage Backend Abstraction**
- Backup can target local filesystem or S3
- Controlled by `BackupStorageBackend` enum + `storage_key`
- S3 not fully integrated in current codebase

### 8. **Automatic Ban Enforcement**
- Attached as log listener on server start
- Parses player join events in real-time
- Immediately kicks banned players (sub-second latency)

---

## ⚠️ Resource Isolation & Security

### How Users Are Isolated

1. **Database-Level Ownership:**
   - Every server, backup, and ban belongs to `owner_id`
   - All queries filtered by `user.id` → no cross-user leakage

2. **Process Isolation:**
   - Each server runs in separate systemd unit
   - Resource limits enforced (RAM, CPU % per tier)
   - Separate working directory (files don't overlap)

3. **Port Isolation:**
   - Each server allocated unique UDP port
   - Bedrock RakNet runs on isolated ports

4. **File System Isolation:**
   - Files API whitelists safe root directories
   - Path traversal checks prevent escape
   - Each server has dedicated directory tree

5. **WebSocket Authentication:**
   - JWT token validated on connect
   - Server ownership verified
   - Only owner can stream console

### Memory & CPU Limits

| Resource | Free Tier | Premium Tier |
|----------|-----------|-------------|
| RAM | 512 MB | 1024 MB |
| CPU | 50% | 100% |
| Servers | 1 | 3 |

- Enforced by systemd MemoryMax/CPUQuota
- Prevents runaway processes affecting other users
- Can be tuned via `TIER_RESOURCE_LIMITS` in code

### Network Isolation

- Worker service runs in Docker with configured network
- Bedrock containers cannot access other containers without explicit routing
- Local mode: All servers share single Bedrock binary (current setup)

---

## 📋 Dependency Overview

**Core Framework:**
- FastAPI 0.115.0+ (REST/WebSocket server)
- Uvicorn 0.30.0+ (ASGI server)
- SQLAlchemy 2.0+ (ORM)
- Pydantic 2.0+ (Validation, settings)

**Database:**
- psycopg[binary] 3.0+ (PostgreSQL driver)
- sqlite3 (built-in, for local development)

**Security:**
- python-jose[cryptography] (JWT tokens)
- passlib[bcrypt] (Password hashing)
- email-validator 2.0+

**External Services:**
- Google Auth: google-auth (OAuth2 verification)
- Stripe 10.0+ (Payments, not integrated yet)
- B2SDK 2.0+ (Backblaze B2, optional storage backend)
- Redis 5.0+ (Optional, for future caching/queuing)

**Utilities:**
- httpx 0.27+ (HTTP client for worker calls, version downloads)
- websockets 12.0+ (WebSocket support)
- python-multipart (Form data parsing)
- python-dotenv (Environment variable loading)

---

## 🚦 Startup Sequence

1. **main.py: create_app()**
   - Creates FastAPI instance
   - Registers CORS middleware
   - Registers all route routers
   - Returns app

2. **main.py: @app.on_event("startup")**
   - `Base.metadata.create_all()` → Creates all tables
   - Schema migrations (ALTER TABLE if needed)
   - `start_backup_scheduler_once()` → Starts polling thread

3. **Uvicorn Server**
   - Binds to configured host:port (default 0.0.0.0:8000)
   - Ready to accept requests

---

## 🎯 Future Extensibility Points

1. **Additional Runtime Drivers**
   - Docker (via existing worker_client)
   - Kubernetes
   - VM orchestration (Oracle, Hetzner)

2. **Storage Backends**
   - S3 (infrastructure exists, not fully integrated)
   - GCS
   - Backblaze B2

3. **Backup Consistency Methods**
   - Currently: save_hold (pause game)
   - Future: filesystem snapshots, server_stop

4. **Scheduling**
   - Currently: interval-based polling
   - Future: Cron expressions, timezone support

5. **Authentication**
   - Currently: Local + Google OAuth
   - Future: GitHub, Apple, Discord

---

## 📝 Summary Statistics

- **Total Lines of Code:** ~4000 (Python)
- **API Endpoints:** 25+ REST routes + 1 WebSocket route
- **Database Models:** 9 core models (User, Server, Ban, Backup, BackupJob, BackupSchedule, RestoreJob, BlockcoinTransaction)
- **Configuration Options:** 20+ environment variables
- **Subscription Tiers:** 2 (free, premium)
- **Server Limits:** 1 (free), 3 (premium)
- **Resource Limits:** Configurable per tier (RAM, CPU)
- **Supported Platforms:** Linux x86_64, Windows x86_64 (for Bedrock downloads)
- **Process Management:** systemd --user mode
- **Backup Methods:** tar.gz archives with SHA256 verification
- **Player Tracking:** Real-time via RakNet ping + log parsing

---

**Document Generated:** 2026-06-17  
**Codebase State:** Setup stage (local development with systemd runtime)
