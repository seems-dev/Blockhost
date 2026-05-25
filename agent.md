# BlockHost Backend — Agent Documentation

## Project Overview

**BlockHost** is a mobile app (Flutter/Android) that lets users spin up Minecraft Bedrock servers in under 90 seconds with no PC required. The backend orchestrates server lifecycle, manages player sessions, handles backups, and monetizes through referrals, ads, BlockCoin rewards, and creator revenue splits.

### Core Value Proposition
- **Player**: Tap a button → get a join code → share on WhatsApp → play with friends in seconds
- **Creator**: Monetize servers through a 30% revenue cut (ads, premium features)
- **Backend**: Zero infrastructure cost (Oracle free tier) + minimal operational overhead

---

## Architecture Overview

### Three-Tier Stack

```
┌─────────────────────────────────────┐
│  Flutter Mobile App (Player Device) │  ← Controls servers, shows join codes
└────────────┬────────────────────────┘
             │ REST API calls
┌────────────▼────────────────────────────────────────┐
│  FastAPI Control Plane (Hetzner CX22 / Oracle Free) │  ← Orchestration logic
│  ├─ Server scheduler (spinup/shutdown)             │
│  ├─ Session manager (join codes, player listing)   │
│  ├─ World backup coordinator (B2 + local sync)     │
│  ├─ Auth & user profiles                           │
│  ├─ Monetization (BlockCoin ledger, payouts)       │
│  └─ Monitoring & health checks                     │
└────────────┬────────────────────────────────────────┘
             │
      ┌──────┴─────────┬──────────────┬─────────────┐
      │                │              │             │
   ┌──▼─┐      ┌──────▼────┐  ┌──────▼────┐  ┌────▼────┐
   │PG  │      │ Redis     │  │ Backblaze │  │ Oracle  │
   │SQL │      │ (session  │  │ B2 (world │  │ Free VM │
   │    │      │  cache)   │  │ backups)  │  │(hosting)│
   └────┘      └───────────┘  └───────────┘  └─────────┘
```

### Infrastructure Breakdown

| Component | Service | Cost | Purpose |
|-----------|---------|------|---------|
| **API Server** | Hetzner CX22 or Oracle Free | €3/mo or $0 | Control plane, orchestration |
| **Database** | Supabase PostgreSQL | ~$20/mo | Users, servers, ledgers |
| **Session Cache** | Redis (on API VM) | $0 | Fast lookups, rate limiting |
| **World Storage** | Backblaze B2 | ~$5-15/mo | Tiered backups (latest 5 + monthly) |
| **Player VMs** | Oracle Free Tier (ARM) | $0 | Minecraft Bedrock server hosting |
| **Premium VMs** | Hetzner CX22 | €3/mo each | High-performance optional tier |

**Total Fixed Cost: ~$30/mo** (scales with user backups)

---

## Core Concepts

### 1. Server Lifecycle

Every BlockHost server goes through these states:

```
CREATED → PROVISIONING → RUNNING → SYNCING_WORLD → SUSPENDED
            ↑                          ↓
            └──────────────────────────┘
                   (periodic checks)
```

**State Transitions:**
- **CREATED**: User taps "New Server" → record in DB, allocate VM slot
- **PROVISIONING**: Terraform provisions ARM VM on Oracle free tier (90s), installs Bedrock server binary
- **RUNNING**: Server is live, players connected, join code valid
- **SYNCING_WORLD**: On player disconnect or timeout, world data synced to B2 + local device
- **SUSPENDED**: Idle for 2+ hours → VM powered down, world state persisted, ready to wake on player reconnect

**Ownership Model**: Each server is owned by one user. If the owner's device is offline, the server shuts down (freeing the Oracle VM for another user).

### 2. Join Code Generation & Validation

**Join Code Format**: `BLOCKHOST-{USER_ID}-{SERVER_ID}-{CHECK_DIGIT}`

Example: `BLOCKHOST-42-1849-7X`

**Flow:**
1. User creates/resumes server → API generates code
2. Code stored in Redis with TTL (valid for 24h by default, extendable)
3. Player on another device scans QR or pastes code
4. API validates: check digit, ownership, server state, player whitelist
5. API returns server IP + port
6. Player's Minecraft client connects directly to that IP:port

**Security Notes:**
- Check digit prevents typos (Luhn-style algorithm)
- Code is not a password—it's a public share mechanism (like a Discord invite)
- Rate-limit code generation (max 10 per server per hour) to prevent spam
- Invalidate old codes on server deletion

### 3. World Backup & Sync Strategy

**Triple-Redundancy Approach:**

```
Active Server (Oracle VM)
        ↓
    Snapshot 1 (B2 backup) ← Latest version, every 5 min or on save
        ↓
    Local Device (player's phone)  ← Encrypted copy for offline play
        ↓
    Monthly Archive (B2) ← Keep 12 months of monthly snapshots
```

**Sync Workflow:**

*On Server Spinup:*
1. API checks local device backup timestamp
2. If local backup exists and is < 24h old → pull from device (fast path, ~5s)
3. Else → fetch latest from B2 (~20s), restore to VM
4. Server starts, loads world, waits for players

*On Server Shutdown (idle 2h):*
1. Server performs final save (Bedrock command)
2. API pulls world files from VM → uploads to B2 + pushes to player device
3. VM powers down, state archived in PG

*Device-to-Device Transfer:*
- Player A creates a server, plays
- Player A shares the join code with Player B
- When Player B joins, they see Player A's world state (synced via the running VM)
- On shutdown, Player A's device gets the backup of the final world state

**Cost Optimization:**
- B2: Use "warm" tier for latest 5 backups (~$0.01/GB/month per backup)
- Archive monthly snapshots separately (~$0.004/GB/month)
- Prune backups older than 12 months (configurable per user)

### 4. Monetization Model

#### BlockCoin Economy

A virtual currency earned through:
- **Referrals**: +100 BlockCoin per successful sign-up (tracked via `referrer_code`)
- **Rewarded Ads**: +10 BlockCoin per ad view (capped 5 per day to prevent spam)
- **Daily Tasks**: +50 BlockCoin for logging in 7 days straight
- **Server Hosting**: +5 BlockCoin per hour a server runs (incentivizes premium tier)

Redeemable for:
- Premium server tier (unlock 5+ concurrent servers, better performance)
- World backup retention (extend beyond 12 months)
- Custom world names, skins, cosmetics (future)

#### Creator Revenue (30% Cut)

If a server is publicly discoverable:
- Other players can find and join servers via a server browser
- Server owner earns 30% of in-app purchases made while in their server
- Payout via Stripe Connect (weekly or monthly threshold: min $5)

**Ledger Schema:**
```
creator_revenue
  ├─ server_id: UUID
  ├─ amount: Decimal (USD)
  ├─ source: Enum [ad_impression, purchase, subscription]
  ├─ date: DateTime
  ├─ payout_status: Enum [pending, scheduled, paid, failed]
  └─ stripe_transfer_id: Optional[str]
```

#### Stripe Integration

- **Webhook listeners**:
  - `customer.subscription.created` → unlock premium
  - `charge.succeeded` → add BlockCoin to ledger
  - `transfer.reversed` → handle payout failure
- **Idempotency**: All webhook handlers are idempotent (check existence before insert)

---

## Data Models (SQLAlchemy ORM)

### Core Tables

#### `users`
```python
class User:
    id: UUID (PK)
    email: str (unique)
    phone: Optional[str]
    auth_hash: str (bcrypt)
    nickname: str
    referrer_code: str (unique, 8-char alphanumeric)
    referred_by_id: Optional[UUID] (FK → users)
    blockcoin_balance: int (default 0)
    stripe_customer_id: Optional[str]
    subscription_tier: Enum [free, premium] (default free)
    created_at: DateTime
    updated_at: DateTime
    deleted_at: Optional[DateTime] (soft delete)
```

#### `servers`
```python
class Server:
    id: UUID (PK)
    owner_id: UUID (FK → users)
    world_name: str
    join_code: str (unique, indexed)
    state: Enum [created, provisioning, running, syncing, suspended]
    
    # Infrastructure
    vm_provider: Enum [oracle_free, hetzner]
    vm_id: str (external VM ID from Terraform)
    vm_ipv4: Optional[str]
    vm_port: int (default 19132 for Bedrock)
    
    # Monetization
    is_public: bool (default false)
    creator_earnings_total: Decimal (USD)
    
    # Metadata
    created_at: DateTime
    last_activity: DateTime
    last_backup_at: Optional[DateTime]
    world_size_bytes: int (estimated)
    
    # Backups
    backup_count: int (how many versions stored in B2)
    backup_retention_days: int (default 365)
```

#### `sessions` (active player sessions)
```python
class Session:
    id: UUID (PK)
    server_id: UUID (FK → servers)
    player_id: UUID (FK → users, nullable for guests)
    player_username: str (for non-auth players)
    player_ip: str
    
    joined_at: DateTime
    left_at: Optional[DateTime]
    duration_seconds: int (computed on leave)
    is_owner: bool (is this the server owner?)
```

#### `blockcoin_ledger`
```python
class BlockcoinTransaction:
    id: UUID (PK)
    user_id: UUID (FK → users)
    amount: int (signed: positive = earn, negative = spend)
    reason: Enum [referral, ad_view, daily_task, server_hosting, purchase, admin_adjustment]
    server_id: Optional[UUID] (for server hosting reason)
    metadata: JSON (flexible data per reason type)
    created_at: DateTime
    
    # Aggregate: sum all amounts per user to get current balance
```

#### `creator_revenue_ledger`
```python
class CreatorRevenueEntry:
    id: UUID (PK)
    server_id: UUID (FK → servers)
    creator_user_id: UUID (FK → users)
    amount_usd: Decimal
    gross_amount_usd: Decimal (before 30% cut)
    source: Enum [ad_impression, purchase, subscription]
    
    payout_status: Enum [pending, scheduled, paid, failed]
    stripe_transfer_id: Optional[str]
    
    event_date: DateTime (when the revenue was earned)
    payout_date: Optional[DateTime]
    created_at: DateTime
```

#### `backup_snapshots`
```python
class BackupSnapshot:
    id: UUID (PK)
    server_id: UUID (FK → servers)
    b2_file_id: str
    backup_size_bytes: int
    
    # Snapshot type
    snapshot_type: Enum [live, hourly, daily, monthly_archive]
    
    # Sync tracking
    synced_to_device: bool
    device_sync_at: Optional[DateTime]
    
    created_at: DateTime
    expires_at: DateTime (auto-calculate based on retention policy)
```

---

## API Routes (Detailed)

### Auth Routes (`/api/auth`)

#### `POST /api/auth/signup`
**Request:**
```json
{
  "email": "user@example.com",
  "phone": "+919876543210",
  "password": "secure_password",
  "nickname": "SurvivalKing",
  "referrer_code": "abc12345"  // optional
}
```

**Response (201):**
```json
{
  "user_id": "550e8400-e29b-41d4-a716-446655440000",
  "referrer_code": "xyz98765",
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "blockcoin_balance": 100  // signup bonus
}
```

**Logic:**
- Hash password with bcrypt (cost 12)
- Generate unique 8-char referrer code
- Award 100 BlockCoin if referred (tracked in ledger)
- Create JWT tokens (access: 24h, refresh: 30d)

---

#### `POST /api/auth/login`
**Request:**
```json
{
  "email": "user@example.com",
  "password": "secure_password"
}
```

**Response (200):**
```json
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "user": {
    "id": "550e8400-...",
    "email": "user@example.com",
    "nickname": "SurvivalKing",
    "blockcoin_balance": 100,
    "subscription_tier": "free"
  }
}
```

---

#### `POST /api/auth/refresh`
**Request:**
```json
{
  "refresh_token": "eyJ..."
}
```

**Response (200):**
```json
{
  "access_token": "eyJ..."
}
```

---

### Server Routes (`/api/servers`)

#### `POST /api/servers` — Create new server
**Request:**
```json
{
  "world_name": "My Survival World",
  "tier": "free"  // or "premium"
}
```

**Response (201):**
```json
{
  "server_id": "660e8400-e29b-41d4-a716-446655440001",
  "state": "provisioning",
  "join_code": "BLOCKHOST-42-660e8-7K",
  "estimated_ready_at": "2024-05-23T10:45:30Z",
  "cost_per_hour_blockcoin": 5
}
```

**Logic:**
- Validate user tier (free users: max 1 server, premium: max 5)
- Create Server record with state=CREATED
- Trigger async task: Terraform provision VM + install Bedrock
- Generate join code, store in Redis with TTL
- Return immediately (client polls `/servers/{id}` for status)

---

#### `GET /api/servers` — List user's servers
**Query Params:** `?include_deleted=false&limit=20&offset=0`

**Response (200):**
```json
{
  "servers": [
    {
      "id": "660e8400-...",
      "world_name": "My Survival World",
      "state": "running",
      "join_code": "BLOCKHOST-42-660e8-7K",
      "vm_ipv4": "192.0.2.100",
      "vm_port": 19132,
      "is_public": false,
      "created_at": "2024-05-20T...",
      "last_activity": "2024-05-23T09:30:00Z",
      "active_players": 2,
      "world_size_mb": 145,
      "creator_earnings_usd": 12.50
    }
  ],
  "total": 3,
  "limit": 20,
  "offset": 0
}
```

---

#### `GET /api/servers/{server_id}` — Get server details
**Response (200):**
```json
{
  "id": "660e8400-...",
  "world_name": "My Survival World",
  "state": "running",
  "join_code": "BLOCKHOST-42-660e8-7K",
  "vm_ipv4": "192.0.2.100",
  "vm_port": 19132,
  "is_public": false,
  
  "sessions": [
    {
      "player_username": "PlayerA",
      "joined_at": "2024-05-23T09:15:00Z",
      "duration_minutes": 30
    },
    {
      "player_username": "PlayerB",
      "joined_at": "2024-05-23T09:20:00Z",
      "duration_minutes": 25
    }
  ],
  
  "backups": {
    "latest_at": "2024-05-23T10:15:00Z",
    "total_count": 145,
    "total_size_mb": 890,
    "retention_days": 365
  },
  
  "stats": {
    "playtime_hours": 45,
    "max_concurrent_players": 4,
    "uptime_percent": 98.5
  },
  
  "creator_earnings": {
    "total_usd": 12.50,
    "pending_payout_usd": 5.00,
    "payout_threshold_usd": 5.00
  }
}
```

---

#### `DELETE /api/servers/{server_id}` — Delete server
**Query Params:** `?purge_backups=false`

**Response (204):**
```
(no content)
```

**Logic:**
- Set server.state = SUSPENDED (soft delete)
- Sync final world state to B2
- Power down VM (Terraform destroy)
- If `purge_backups=true`, delete all B2 snapshots (irreversible)
- Default: keep backups for 7 days, then auto-prune

---

#### `POST /api/servers/{server_id}/resume` — Wake suspended server
**Response (200):**
```json
{
  "server_id": "660e8400-...",
  "state": "provisioning",
  "estimated_ready_at": "2024-05-23T11:00:00Z"
}
```

**Logic:**
- Check user has available VM quota (tier-dependent)
- Fetch latest backup from B2
- Provision new VM, restore world files, start server
- Update join code (new IP:port)

---

### Session Routes (`/api/sessions`)

#### `POST /api/sessions/validate-join-code`
**Request:**
```json
{
  "join_code": "BLOCKHOST-42-660e8-7K",
  "player_username": "SurvivalPlayer"
}
```

**Response (200):**
```json
{
  "valid": true,
  "server_id": "660e8400-...",
  "world_name": "My Survival World",
  "owner_nickname": "SurvivalKing",
  
  "connection_details": {
    "server_ip": "192.0.2.100",
    "server_port": 19132
  },
  
  "player": {
    "is_owner": false,
    "can_edit": false,
    "join_permissions": "guest"
  }
}
```

**Validation Logic:**
- Verify check digit (Luhn algorithm)
- Check Redis for code existence and TTL
- Verify server state = RUNNING
- Verify player is not banned
- Record session start in DB
- Return connection details

**Response (400) on invalid code:**
```json
{
  "valid": false,
  "error": "code_expired",
  "error_details": "This join code expired 2 hours ago"
}
```

---

#### `POST /api/sessions/{session_id}/leave` — Player leaves server
**Request:**
```json
{
  "playtime_seconds": 1845,
  "blocks_placed": 234,
  "blocks_destroyed": 567
}
```

**Response (200):**
```json
{
  "session_id": "770e8400-...",
  "duration_seconds": 1845,
  "xp_earned": 50,
  "achievements_unlocked": ["first_block", "master_builder"]
}
```

**Logic:**
- Update session with left_at timestamp
- Award BlockCoin based on playtime (e.g., +1 per 10 min, max 50 per session)
- Trigger world autosave on server
- If no active players → mark server for sync & eventual suspension

---

### Backup Routes (`/api/backups`)

#### `GET /api/servers/{server_id}/backups` — List backups
**Query Params:** `?snapshot_type=all&limit=20`

**Response (200):**
```json
{
  "backups": [
    {
      "id": "880e8400-...",
      "snapshot_type": "live",
      "size_mb": 234,
      "created_at": "2024-05-23T10:15:00Z",
      "synced_to_device": true,
      "device_sync_at": "2024-05-23T10:20:00Z",
      "expires_at": "2024-05-24T10:15:00Z"
    },
    {
      "id": "881e8400-...",
      "snapshot_type": "monthly_archive",
      "size_mb": 234,
      "created_at": "2024-05-01T00:00:00Z",
      "synced_to_device": false,
      "expires_at": "2025-05-01T00:00:00Z"
    }
  ],
  "total": 147
}
```

---

#### `POST /api/servers/{server_id}/backups/force-sync` — Manual backup
**Response (202):**
```json
{
  "backup_id": "882e8400-...",
  "status": "in_progress",
  "estimated_completion_seconds": 45
}
```

---

#### `POST /api/servers/{server_id}/backups/{backup_id}/restore` — Restore old backup
**Request:**
```json
{
  "confirm": true
}
```

**Response (200):**
```json
{
  "backup_id": "882e8400-...",
  "restore_status": "in_progress",
  "estimated_completion_seconds": 60,
  "note": "Server will be suspended during restore. You'll be notified when ready."
}
```

**Logic:**
- Require owner confirmation (irreversible)
- Suspend server (kick players)
- Delete current world files
- Download backup from B2
- Restore to VM
- Resume server
- Notify owner via push notification

---

### Monetization Routes (`/api/monetization`)

#### `GET /api/monetization/blockcoin-balance`
**Response (200):**
```json
{
  "balance": 2450,
  "earned_today": 65,
  "pending_daily_tasks": [
    {
      "task_id": "login_streak_7",
      "description": "Login 7 days in a row",
      "days_completed": 4,
      "days_required": 7,
      "reward_blockcoin": 50
    }
  ]
}
```

---

#### `POST /api/monetization/daily-checkin` — Claim daily login reward
**Response (200):**
```json
{
  "blockcoin_earned": 50,
  "new_balance": 2500,
  "streak_days": 5,
  "next_checkin_available_at": "2024-05-24T10:45:30Z"
}
```

**Logic:**
- Check user's last checkin date
- If < 24h ago → return 400 (already claimed)
- If ≥ 24h and < 48h → increment streak
- If ≥ 48h → reset streak to 1
- Award 50 BlockCoin + bonus at day 7 (+50 more)
- Record in blockcoin_ledger

---

#### `GET /api/monetization/creator-earnings`
**Response (200):**
```json
{
  "total_earned_usd": 45.80,
  "pending_payout_usd": 12.30,
  "lifetime_servers": 3,
  "servers_public": 2,
  
  "recent_transactions": [
    {
      "id": "tx_abc123",
      "server_id": "660e8400-...",
      "source": "ad_impression",
      "amount_usd": 0.50,
      "created_at": "2024-05-23T09:15:00Z"
    }
  ],
  
  "payout_history": [
    {
      "id": "payout_001",
      "amount_usd": 25.00,
      "status": "paid",
      "stripe_transfer_id": "tr_abc123",
      "payout_date": "2024-05-15T00:00:00Z"
    }
  ]
}
```

---

#### `POST /api/monetization/request-payout` — Initiate creator payout
**Request:**
```json
{
  "amount_usd": 12.30
}
```

**Response (200):**
```json
{
  "payout_id": "payout_002",
  "amount_usd": 12.30,
  "status": "scheduled",
  "estimated_arrival": "2024-05-30T00:00:00Z",
  "note": "Payout will be processed within 3-5 business days"
}
```

**Logic:**
- Validate user has Stripe Connect account set up
- Verify requested amount ≤ pending balance
- Create Stripe Transfer
- Mark ledger entries as "scheduled"
- Webhook listener (see Stripe section) updates status to "paid"

---

#### `POST /api/monetization/server/{server_id}/make-public` — Enable server monetization
**Response (200):**
```json
{
  "server_id": "660e8400-...",
  "is_public": true,
  "server_browser_url": "https://blockhost.app/servers/660e8400-..."
}
```

---

## Orchestrator Module (`orchestrator.py`)

### Responsibilities
1. **Server Provisioning**: Call Terraform to spin up Oracle VMs
2. **Health Monitoring**: Poll servers for status, detect dead servers
3. **Autosuspend**: Idle servers → suspend after 2h inactivity
4. **Resource Cleanup**: Garbage collect orphaned VMs, old backups
5. **Error Handling & Retry**: Idempotent operations, exponential backoff

### Key Functions

#### `async def provision_server(server_id: UUID, tier: str) -> ServerVM`
```python
"""
Provision a new Minecraft Bedrock server VM.

Steps:
1. Allocate VM slot from pool (tier-dependent)
2. Render Terraform tfvars (server_id, user_id, world_name)
3. Run: terraform plan + apply
4. Wait for VM to boot (up to 90s)
5. Install Bedrock server binary
6. Download world backup from B2 (if resuming)
7. Start Bedrock server process
8. Poll health check endpoint until responsive
9. Update DB with VM IP, port, state=RUNNING
10. Return connection details
"""
```

#### `async def monitor_servers() -> None`
```python
"""
Background task: every 30s, check all RUNNING servers.

Steps:
1. Query servers with state=RUNNING
2. For each, make HTTP request to healthcheck endpoint
3. If healthy, update last_activity timestamp
4. If unhealthy (3 consecutive failures), escalate:
   - Log error to Sentry
   - Send alert to owner
   - Mark state=FAILED, schedule recovery
5. Check for idle servers (no activity > 2h):
   - Trigger sync_and_suspend_server
"""
```

#### `async def sync_and_suspend_server(server_id: UUID) -> None`
```python
"""
Graceful shutdown: sync world to B2 + device, then power down.

Steps:
1. Send STOP command to Bedrock server (60s grace period)
2. Wait for server to save world files
3. Download world files from VM
4. Upload to B2 with timestamp
5. Push to player's device via push notification (they accept download)
6. Run: terraform destroy (power down VM)
7. Update DB: state=SUSPENDED, last_backup_at=now()
8. Free VM slot for next user
"""
```

#### `async def resume_server(server_id: UUID) -> None`
```python
"""
Resume a suspended server (new VM allocation, restore world).

Steps:
1. Check user tier quota
2. Allocate new VM
3. Fetch latest backup from B2
4. Run terraform apply
5. Restore world files to new VM
6. Start Bedrock server
7. Generate new join code (new IP:port)
8. Update DB, notify owner
"""
```

---

## Backup Module (`backup.py`)

### B2 Organization

```
blockhost-backups/
├── {user_id}/
│   └── {server_id}/
│       ├── live/
│       │   └── world_{timestamp}.tar.gz  (latest 5, each 24h TTL)
│       ├── daily/
│       │   └── world_2024-05-21.tar.gz
│       └── monthly_archive/
│           └── world_2024-04.tar.gz      (12-month retention)
```

### Key Functions

#### `async def backup_world(server_id: UUID, backup_type: str) -> BackupSnapshot`
```python
"""
Create and upload a world backup to B2.

backup_type: 'live' | 'daily' | 'monthly_archive'
"""
```

#### `async def restore_world(backup_id: UUID, vm_ipv4: str) -> None`
```python
"""
Download backup from B2, transfer to VM, extract world files.
"""
```

#### `async def sync_to_device(backup_id: UUID, user_id: UUID) -> None`
```python
"""
Send encrypted backup download link to player device.
Use push notification + signed URL (expires in 1h).
"""
```

#### `async def prune_backups(server_id: UUID) -> None`
```python
"""
Auto-cleanup old backups based on retention policy.
- Keep latest 5 live backups (rotate on new backup)
- Keep 30-day daily snapshots
- Keep 12-month archive
- Delete anything older
"""
```

---

## Configuration Module (`config.py`)

```python
from pydantic_settings import BaseSettings
from enum import Enum

class Environment(str, Enum):
    DEV = "development"
    STAGING = "staging"
    PROD = "production"

class Settings(BaseSettings):
    # Environment
    ENVIRONMENT: Environment = Environment.DEV
    DEBUG: bool = False
    
    # Server
    PORT: int = 8000
    HOST: str = "0.0.0.0"
    
    # Database
    DATABASE_URL: str  # postgresql://user:pass@host/dbname
    DATABASE_POOL_SIZE: int = 20
    DATABASE_MAX_OVERFLOW: int = 10
    
    # Redis
    REDIS_URL: str  # redis://localhost:6379
    REDIS_CACHE_TTL_SECONDS: int = 3600
    
    # JWT
    JWT_SECRET_KEY: str
    JWT_ALGORITHM: str = "HS256"
    JWT_ACCESS_TOKEN_EXPIRE_HOURS: int = 24
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 30
    
    # Terraform
    TERRAFORM_DIR: str = "./terraform"
    TERRAFORM_STATE_BUCKET: str  # Cloud storage path
    ORACLE_COMPARTMENT_ID: str
    ORACLE_SUBNET_ID: str
    HETZNER_API_TOKEN: str
    
    # Backblaze B2
    B2_APP_KEY_ID: str
    B2_APP_KEY: str
    B2_BUCKET_NAME: str = "blockhost-backups"
    B2_BACKUP_RETENTION_DAYS: int = 365
    B2_ARCHIVE_RETENTION_DAYS: int = 1095  # 3 years
    
    # Stripe
    STRIPE_SECRET_KEY: str
    STRIPE_PUBLISHABLE_KEY: str
    STRIPE_WEBHOOK_SECRET: str
    STRIPE_CONNECT_PLATFORM_ACCOUNT_ID: str
    
    # Sentry
    SENTRY_DSN: Optional[str] = None
    
    # Features
    ENABLE_PUBLIC_SERVER_BROWSER: bool = True
    MAX_SERVERS_FREE_TIER: int = 1
    MAX_SERVERS_PREMIUM_TIER: int = 5
    SERVER_IDLE_SUSPEND_HOURS: int = 2
    
    class Config:
        env_file = ".env"
        case_sensitive = True

settings = Settings()
```

---

## Database Module (`db.py`)

```python
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_size=settings.DATABASE_POOL_SIZE,
    max_overflow=settings.DATABASE_MAX_OVERFLOW,
    pool_pre_ping=True,  # Test connections before reuse
)

async_session_factory = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

async def get_db():
    """Dependency: inject async session into route handlers."""
    async with async_session_factory() as session:
        yield session

async def init_db():
    """Create all tables on startup."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
```

---

## Error Handling & Logging Strategy

### Error Hierarchy
```python
class BlockHostError(Exception):
    """Base exception."""
    pass

class ServerProvisioningError(BlockHostError):
    """Terraform or VM provisioning failed."""
    pass

class BackupSyncError(BlockHostError):
    """B2 upload/download failed."""
    pass

class InvalidJoinCodeError(BlockHostError):
    """Code validation failed."""
    pass

class InsufficientQuotaError(BlockHostError):
    """User hit tier limits."""
    pass
```

### Logging Pattern
```python
import logging
from sentry_sdk import capture_exception

logger = logging.getLogger(__name__)

async def provision_server(...):
    try:
        # Implementation
        pass
    except ServerProvisioningError as e:
        logger.error(f"Provisioning failed for server {server_id}: {e}")
        capture_exception(e)
        # Retry logic here
        raise
```

---

## Testing Strategy

### Test Pyramid

```
           /\
          /  \  E2E tests (3-4 key user flows)
         /────\
        /      \
       /────────\  Integration tests (API + DB + Terraform mocks)
      /          \
     /────────────\  Unit tests (models, utils, validation)
    /              \
   /────────────────\
```

### Test Files Structure
```
tests/
├── unit/
│   ├── test_models.py
│   ├── test_auth.py
│   └── test_backup.py
├── integration/
│   ├── test_server_lifecycle.py
│   ├── test_join_code_validation.py
│   └── test_monetization_ledger.py
├── e2e/
│   ├── test_create_server_to_player_join.py
│   └── test_server_suspension_and_resume.py
└── conftest.py  # Fixtures
```

### Example Test
```python
@pytest.mark.asyncio
async def test_join_code_validation(db_session, redis_client):
    # Arrange
    user = await create_test_user(db_session)
    server = await create_test_server(db_session, owner=user)
    join_code = generate_join_code(user.id, server.id)
    await redis_client.set(f"join_code:{join_code}", server.id)
    
    # Act
    result = await validate_join_code(join_code, db_session)
    
    # Assert
    assert result.server_id == server.id
    assert result.valid is True
```

---

## Deployment & CI/CD Pipeline

### Environments

**Development**
- FastAPI reload=True
- All endpoints logging
- Lightweight Terraform (local state)
- SQLite or local PG

**Staging**
- Full feature parity with prod
- Mock Stripe (test mode)
- Terraform remote state (Terraform Cloud)
- Real DB + Redis

**Production**
- FastAPI reload=False
- Structured logging (JSON to CloudWatch/Datadog)
- Terraform remote state + locking
- RDS + ElastiCache + B2 + Stripe (live)

### CI/CD Steps (GitHub Actions)

```yaml
name: CI/CD Pipeline

on: [push, pull_request]

jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - uses: actions/setup-python@v4
        with:
          python-version: '3.12'
      - run: uv sync
      - run: uv run black --check .
      - run: uv run ruff check .
      - run: uv run pytest tests/ --cov=blockhost_backend
  
  deploy-staging:
    needs: test
    if: github.ref == 'refs/heads/main'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3
      - run: terraform apply -auto-approve -var-file=staging.tfvars
      - run: docker build -t blockhost:staging .
      - run: docker push ${{ secrets.REGISTRY }}/blockhost:staging
      - run: kubectl set image deployment/blockhost blockhost=... -n staging
  
  deploy-prod:
    needs: test
    if: github.ref == 'refs/heads/releases/*'
    runs-on: ubuntu-latest
    steps:
      # Similar to staging, but with production configs
```

---

## Monitoring & Observability

### Key Metrics

**Infrastructure**
- Server uptime % (per VM)
- API response time p50/p95/p99
- Database query latency
- B2 backup sync time
- Terraform plan/apply duration

**Business**
- Active servers (concurrent)
- Daily active players
- Average session duration
- BlockCoin circulation (earned vs spent)
- Creator revenue payouts ($ and count)

### Alerts

```python
# Sentry integration
if tf.plan_duration > 60:
    capture_message("Slow Terraform plan", level="warning")

if db_query_time > 1000:  # ms
    capture_exception(DatabaseSlowError())

if b2_upload_fails >= 3:
    capture_message("B2 backup sync failing", level="error")
    # Alert on-call engineer
```

### Dashboards (Grafana/DataDog)

1. **Control Plane Dashboard**: API health, DB connections, error rates
2. **Infrastructure Dashboard**: VM count, uptime, resource utilization
3. **Business Dashboard**: DAU, server count, BlockCoin earned/spent
4. **Creator Revenue Dashboard**: Payouts pending, top servers by earnings

---

## Security Considerations

### Authentication & Authorization

- **JWT tokens** (stateless): access (24h) + refresh (30d)
- **Refresh rotation**: old refresh token invalidated on new issue
- **Rate limiting**: 10 login attempts per IP per hour (Redis)
- **Password policy**: min 8 chars, entropy check (zxcvbn library)

### Data Protection

- **Database**: Encrypted at rest (RDS encryption), TLS in transit
- **Backups**: Encrypted before upload to B2 (AES-256, keys in KMS)
- **API**: HTTPS only, HSTS header, no password in logs
- **Secrets**: All credentials in `.env` (not committed), rotated monthly

### API Security

- **CORS**: Restricted to mobile app domain only (prod)
- **CSRF**: Implicit (stateless JWT, no cookies)
- **Input validation**: Pydantic models enforce schema
- **SQL injection**: SQLAlchemy ORM (parametrized queries)
- **Rate limiting**: 100 req/min per user, 1000 req/min global

### Access Control

- **Server ownership**: Only owner can modify/delete server
- **Join codes**: Public but code-guessing is computationally expensive (check digit)
- **Creator revenue**: Only creator can withdraw payouts
- **Admin operations**: Require super-admin JWT token (separate login)

---

## Roadmap & Future Phases

### Phase 1 (MVP — current)
- ✅ Server create/delete/suspend/resume
- ✅ Player join via code
- ✅ World backup (B2)
- ✅ Basic auth (email + password)
- ✅ Simple BlockCoin (referrals + daily login)

### Phase 2 (3 months)
- Public server browser (discovery)
- Creator monetization (30% cut, payouts)
- Advanced world management (restore old versions)
- Custom skins & cosmetics
- Multiplayer mini-games

### Phase 3 (6 months)
- Realm-style cross-device sync (play offline, sync on reconnect)
- Custom plugins/mods support
- Server templates (survival, PvP, creative, etc.)
- In-game marketplace
- Guilds/clans system

### Phase 4 (12 months+)
- Native Bedrock content marketplace integration
- Cross-platform (Java Edition support)
- Mobile game modes (one-handed UI, touch-optimized)
- Tournaments & leaderboards
- Creator fund (Anthropic sponsors top creators)

---

## Glossary

| Term | Definition |
|------|-----------|
| **VM** | Virtual Machine (Oracle or Hetzner instance) |
| **Oracle Free Tier** | AWS-like free offering from Oracle Cloud (2 ARM vCPU, 12GB RAM, always-free) |
| **Bedrock Server** | Official Minecraft server binary (runs on Linux, supports mobile clients) |
| **Join Code** | Public, short code to invite players (not a password) |
| **BlockCoin** | Virtual currency earned via gameplay, ads, referrals |
| **Creator Revenue** | 30% cut of in-app purchases made in a server |
| **B2** | Backblaze's S3-compatible cloud storage (cheap, ~$0.01/GB/month) |
| **Terraform** | IaC tool to provision VMs idempotently |
| **Sync** | Copy world state from running server → B2 backup + local device |
| **Suspend** | Power down idle server, keep world in B2 |
| **Resume** | Wake suspended server on new VM, restore world |

---

## Contributing Guidelines

### Code Style
- Use Black for formatting (`uv run black .`)
- Use Ruff for linting (`uv run ruff check .`)
- Type hints on all functions
- Docstrings for public APIs (Google style)

### PR Checklist
- [ ] Tests pass locally
- [ ] New tests for new features
- [ ] Database migrations included (if applicable)
- [ ] Security review (if touching auth/payments)
- [ ] Terraform plan validated

### Branch Strategy
- `main`: stable, deployable to staging
- `releases/*`: tagged releases for production
- `feature/*`: feature branches, reviewed before merge

---

## Contact & Support

**Issues & Discussions**: GitHub Discussions or Issues
**Slack**: `#blockhost-backend` on team workspace
**On-call**: Check PagerDuty for current on-call rotation
**Security**: security@blockhost.dev

---

**Document Version**: 1.0  
**Last Updated**: May 2026  
**Owner**: @Seems (BlockHost creator)