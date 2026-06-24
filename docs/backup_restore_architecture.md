# Backup and Restore Architecture for Erex

This document is the implementation-level design for Erex backup and restore.
It assumes:

- Backend: FastAPI.
- Database: PostgreSQL in production.
- Runtime: one Minecraft Bedrock process per `server_id`.
- Storage: local disk now, S3-compatible object storage later.
- Server lifecycle operations already exist: start, stop, restart, status.

## Recommended Architecture

Use a dedicated backup worker architecture, not FastAPI `BackgroundTasks`.

For a small startup:

- Use FastAPI for API validation, authorization, and job creation.
- Use PostgreSQL for metadata, schedules, job state, idempotency, and locks.
- Use Redis + Celery for backup and restore execution.
- Use APScheduler only as a schedule producer, or use Celery Beat if Celery is already deployed.
- Use local filesystem storage through a storage abstraction so S3 can be added later.

FastAPI background tasks are fine for short fire-and-forget work, but backups are long-running, I/O-heavy, failure-sensitive jobs. They need retries, concurrency limits, observability, and durable state. A dedicated worker is the right boundary.

## Architecture Diagram

```text
Flutter Client
    |
    | HTTPS / WebSocket or polling
    v
FastAPI Backend
    |
    | auth, quota checks, job creation
    v
PostgreSQL
    |
    | backup_jobs / restore_jobs / schedules / metadata
    |
    +---------------------------+
                                |
                                v
                         Redis Queue
                                |
                                v
                  Celery Backup/Restore Workers
                                |
       +------------------------+------------------------+
       |                        |                        |
       v                        v                        v
Lifecycle Manager        Storage Backend          Progress Publisher
stop/start/status        local now, S3 later      DB + optional WS
       |
       v
Bedrock Server Process
       |
       v
Server Files on Disk
```

## Folder Structure

Recommended local storage layout:

```text
data/
├── servers/
│   ├── {server_id}/
│   │   ├── bedrock/
│   │   │   ├── worlds/
│   │   │   ├── server.properties
│   │   │   └── allowlist.json
│   │   ├── logs/
│   │   └── runtime/
│   │       ├── locks/
│   │       └── tmp/
│   │
│   └── ...
│
├── backups/
│   ├── {server_id}/
│   │   ├── objects/
│   │   │   ├── {backup_id}.tar.zst
│   │   │   └── {backup_id}.tar.zst.sha256
│   │   ├── staging/
│   │   │   └── {backup_job_id}/
│   │   └── restore/
│   │       └── {restore_job_id}/
│   │
│   └── ...
│
└── tmp/
```

Rules:

- Never expose raw filesystem paths to the client.
- Every path is derived from a trusted `server_id` UUID and configured root.
- Staging directories are removed after success or failure recovery.
- Final backup object names use immutable `backup_id`, not user-provided names.
- Archive format should be `tar.zst` for fast compression and good restore speed.

## Backup Strategy

### Options

`stop server completely`

- Safest for consistency.
- Causes downtime for the whole backup duration.
- Simple operational model.
- Recommended fallback for servers where `save hold` is unavailable or fails.

`Bedrock save hold / save resume`

- Best practical option for minimizing downtime on normal local filesystems.
- The server pauses world writes, flushes data, and allows a consistent copy.
- Players can remain connected, but world save operations are held briefly.
- Must always call `save resume` in `finally`.

`filesystem snapshots`

- Best at larger scale if the server data lives on ZFS, Btrfs, or LVM.
- Snapshot can be created quickly after a save hold, then copied asynchronously.
- Requires storage/platform support and operational maturity.
- Good future upgrade path.

`copy world files directly while running`

- Not safe.
- Bedrock may be writing LevelDB/world files while copy is in progress.
- Can produce subtle corruption even if archive creation succeeds.
- Do not use for production backups.

### Recommended Safe Approach

For Erex now:

1. Try coordinated online backup:
   - Acquire per-server backup lock.
   - Send `save hold`.
   - Poll `save query` until Bedrock reports files are ready.
   - Copy world and required config into a staging directory.
   - Send `save resume`.
   - Compress and checksum from staging while server continues running.

2. If `save hold` or `save query` fails:
   - Stop the server.
   - Copy to staging.
   - Start the server again if it was running before.
   - Compress from staging.

This minimizes downtime while preserving correctness.

Future optimized path:

1. `save hold`.
2. Create filesystem snapshot.
3. `save resume`.
4. Archive from snapshot.
5. Delete snapshot.

## Backup Workflow

```text
Client
  |
  v
POST /api/servers/{server_id}/backups
  |
  v
Authorize + quota + duplicate-job check
  |
  v
Create backup + backup_job rows
  |
  v
Enqueue worker job
  |
  v
Worker acquires server backup lock
  |
  v
Create staging directory
  |
  v
Try save hold + save query
  |
  +--> success: copy consistent files to staging, save resume
  |
  +--> failure: stop server, copy files, optionally restart
  |
  v
Validate staging contents
  |
  v
Compress to temporary archive path
  |
  v
Compute SHA-256
  |
  v
Atomic rename into backups/{server_id}/objects/{backup_id}.tar.zst
  |
  v
Mark backup completed, update size/checksum
  |
  v
Apply retention policy
```

### Files to Include

At minimum:

- `bedrock/worlds/{world_name}/`
- `bedrock/server.properties`
- `bedrock/allowlist.json`, if present
- `bedrock/permissions.json`, if present
- `bedrock/valid_known_packs.json`, if present
- `bedrock/world_behavior_packs.json`, if present
- `bedrock/world_resource_packs.json`, if present

Do not include:

- logs
- runtime process files
- lock files
- temporary files
- downloaded Bedrock binaries

### Concurrency and Locking

Use three layers:

1. PostgreSQL uniqueness/idempotency:
   - Prevent duplicate queued/running jobs for the same server.
   - Use partial unique indexes where possible.

2. PostgreSQL advisory lock:
   - `pg_advisory_lock(hash(server_id))` inside worker.
   - Ensures only one backup/restore mutates a server at a time across workers.

3. Local filesystem lock:
   - Optional defense-in-depth lock file under `data/servers/{server_id}/runtime/locks/backup_restore.lock`.
   - Useful if future scripts/tools operate outside the app.

Rules:

- Multiple servers can back up simultaneously.
- A single server can have only one active backup or restore job.
- A restore blocks backups for that server.
- A backup blocks restore for that server.
- Duplicate manual backup requests should return the existing active job with `202 Accepted`, not create a second job.

### Backup Requests During Active Backup

If a backup is already `pending` or `running`:

- Return `202 Accepted`.
- Response includes the existing `backup_id` and `job_id`.
- Include a message like `backup_already_running`.

If a restore is active:

- Return `409 Conflict`.
- Do not enqueue a backup.

## Metadata Schema

Example completed backup metadata:

```json
{
  "backup_id": "7b4f7a9b-5c91-4c38-a573-8a72ccf99610",
  "server_id": "2cbb3b1a-7f1e-4399-b88d-97bf963b6e4a",
  "owner_id": "7b621428-cf42-4584-b295-615d03e2f93e",
  "kind": "manual",
  "status": "completed",
  "storage_backend": "local",
  "storage_key": "2cbb3b1a-7f1e-4399-b88d-97bf963b6e4a/objects/7b4f7a9b-5c91-4c38-a573-8a72ccf99610.tar.zst",
  "archive_format": "tar.zst",
  "checksum_sha256": "hex encoded sha256",
  "size_bytes": 412345678,
  "uncompressed_size_bytes": 1345678901,
  "world_name": "Bedrock level",
  "included_paths": [
    "bedrock/worlds/Bedrock level",
    "bedrock/server.properties"
  ],
  "bedrock_version": "1.21.80.03",
  "server_was_running": true,
  "consistency_method": "save_hold",
  "created_by_user_id": "7b621428-cf42-4584-b295-615d03e2f93e",
  "created_at": "2026-06-05T12:00:00Z",
  "completed_at": "2026-06-05T12:02:15Z",
  "expires_at": "2026-07-05T12:00:00Z",
  "failure_code": null,
  "failure_message": null
}
```

## Status Tracking

Backup statuses:

```text
pending
running
held_saves
copying
compressing
verifying
completed
failed
cancelled
deleting
deleted
expired
```

Restore statuses:

```text
pending
running
validating_backup
stopping_server
extracting
validating_restore
swapping
starting_server
completed
failed
rolled_back
cancelled
```

Keep both high-level `status` and detailed `phase`. The frontend can display friendly labels from `phase`, while backend logic keys off `status`.

## Progress Reporting

Progress should be stored durably in `backup_jobs` and `restore_jobs`.

Fields:

- `progress_percent`
- `progress_bytes`
- `total_bytes`
- `phase`
- `message`
- `updated_at`

Frontend options:

- Poll `GET /api/servers/{server_id}/backup-jobs/{job_id}` every 1-2 seconds.
- Later add WebSocket or Server-Sent Events:
  - `/api/servers/{server_id}/jobs/stream`

Progress calculation:

- Copy phase: bytes copied / estimated source size.
- Compress phase: bytes read from staging / staging size.
- Upload phase later: bytes uploaded / archive size.
- Restore extract phase: bytes extracted / archive uncompressed size if known.

Do not fake exact progress when not available. Return phase-level progress ranges:

- `pending`: 0%
- `held_saves`: 5-10%
- `copying`: 10-45%
- `compressing`: 45-80%
- `verifying`: 80-95%
- `completed`: 100%

## Retention Policy

Support per-server policy:

- keep last `N` completed backups
- keep backups for `D` days
- optional minimum retention for paid tiers
- never delete backups with `pinned = true`

Retention should run:

- after a successful backup
- periodically once per day

Deletion rules:

- Only completed backups are eligible.
- Never delete active/incomplete backups through retention.
- Mark `deleting`, remove object and checksum, then mark `deleted`.
- If delete fails, mark `delete_failed` or keep `completed` with `last_delete_error`.

## Scheduled Backups

Recommended small-startup setup:

- Celery worker for execution.
- Celery Beat or APScheduler for schedule ticks.
- PostgreSQL stores schedules.
- Scheduler only enqueues jobs; worker performs all heavy work.

Avoid running backup logic inside APScheduler directly. If the API process restarts or scales horizontally, scheduler duplication gets messy. If APScheduler is used, run it in one dedicated scheduler process with leader election or a deployment singleton.

Schedule behavior:

- Each schedule has `server_id`, cron/interval, timezone, enabled flag.
- Scheduler finds due schedules and tries to create a backup job.
- If server already has an active backup/restore, skip or defer based on policy.
- Store `last_run_at`, `next_run_at`, `last_success_at`, `consecutive_failures`.

## Restore Workflow

```text
Client
  |
  v
POST /api/servers/{server_id}/restores
  |
  v
Authorize + validate backup ownership + active-job check
  |
  v
Create restore_job row
  |
  v
Enqueue worker job
  |
  v
Worker acquires server backup/restore lock
  |
  v
Validate backup metadata and archive checksum
  |
  v
Stop server if running
  |
  v
Extract archive into restore staging directory
  |
  v
Validate extracted world structure
  |
  v
Move current world to rollback directory
  |
  v
Atomic rename extracted world into active world path
  |
  v
Validate final path
  |
  +--> restart requested: start server
  |
  v
Mark restore completed
```

Restore must never run while the server is running. Even if files can be swapped quickly, the Bedrock process may hold open file handles or rewrite LevelDB files.

## Restore Safety Checks

Before restore:

- Authenticated user owns the server or has admin permission.
- Backup exists.
- Backup belongs to the same `server_id`.
- Backup status is `completed`.
- Backup object exists in storage.
- Backup checksum matches metadata.
- No active backup or restore job for the server.
- Server is not in provisioning/syncing/suspended state unless explicitly supported.
- Enough free disk space exists:
  - at least `archive_size + uncompressed_size + current_world_size + safety_margin`.
- Restore target path is inside the configured server root.
- Archive entries do not contain absolute paths or `..`.
- Backup archive contains exactly one valid world root or expected included paths.

## Atomic Restore Process

Recommended layout during restore:

```text
data/
├── servers/{server_id}/bedrock/worlds/{world_name}
├── servers/{server_id}/runtime/tmp/restore-{restore_job_id}/
│   └── extracted/
└── servers/{server_id}/runtime/tmp/rollback-{restore_job_id}/
    └── {world_name}
```

Atomic local-filesystem restore:

1. Stop server.
2. Extract archive into `runtime/tmp/restore-{restore_job_id}/extracted`.
3. Validate extracted world.
4. Rename current active world:
   - from `worlds/{world_name}`
   - to `runtime/tmp/rollback-{restore_job_id}/{world_name}`
5. Rename restored world:
   - from staging extracted path
   - to `worlds/{world_name}`
6. Validate active world exists.
7. Start server if requested.
8. If startup health check fails and rollback is enabled:
   - stop server
   - move restored world to failed restore directory
   - move rollback world back to active path
   - start original server if it was running before
   - mark job `rolled_back`

POSIX `rename` is atomic only on the same filesystem. Keep staging, rollback, and active worlds under the same mounted filesystem.

## Restore Failure Handling

`corrupted backup archive`

- Detect during checksum verification or extraction.
- Mark restore failed.
- Do not stop server until checksum passes.
- Mark backup as `corrupt` only after a second verification failure or admin audit.

`missing files`

- If archive object is missing, fail before stopping server.
- If required world files are missing after extraction, fail before swapping.

`interrupted restore`

- On worker startup, scan `restore_jobs` stuck in running phases.
- If before swap: clean staging, mark failed.
- If during/after swap: inspect active and rollback paths.
- If active path missing and rollback exists: restore rollback.
- If both exist: keep active path, mark needs_manual_review.

`disk full`

- Check free space before extraction.
- Still handle `ENOSPC` during copy/extract.
- If disk fills before swap, clean staging and fail.
- If disk fills after moving current world but before restored world is placed, move rollback back immediately.

## Rollback Strategy

Do not delete the previous world during restore.

Keep it in a rollback directory until:

- restore is completed
- optional server health check passes
- cleanup succeeds

After success:

- Delete rollback directory asynchronously, or
- Keep it for a short emergency window, for example 24 hours, tagged as internal rollback data.

If restore fails halfway, rollback path gives you the previous world. This prevents total world loss.

## Database Schema

Enums:

```sql
CREATE TYPE backup_status AS ENUM (
  'pending', 'running', 'held_saves', 'copying', 'compressing',
  'verifying', 'completed', 'failed', 'cancelled',
  'deleting', 'deleted', 'expired'
);

CREATE TYPE restore_status AS ENUM (
  'pending', 'running', 'validating_backup', 'stopping_server',
  'extracting', 'validating_restore', 'swapping', 'starting_server',
  'completed', 'failed', 'rolled_back', 'cancelled'
);

CREATE TYPE backup_kind AS ENUM ('manual', 'scheduled', 'system');
CREATE TYPE storage_backend AS ENUM ('local', 's3');
CREATE TYPE consistency_method AS ENUM ('save_hold', 'server_stop', 'filesystem_snapshot');
```

Backups:

```sql
CREATE TABLE backups (
  id UUID PRIMARY KEY,
  server_id UUID NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
  owner_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_by_user_id UUID REFERENCES users(id),
  schedule_id UUID,

  kind backup_kind NOT NULL,
  status backup_status NOT NULL,
  name VARCHAR(128),
  description TEXT,
  pinned BOOLEAN NOT NULL DEFAULT FALSE,

  world_name VARCHAR(128) NOT NULL,
  bedrock_version VARCHAR(64),
  consistency_method consistency_method,
  server_was_running BOOLEAN NOT NULL DEFAULT FALSE,

  storage_backend storage_backend NOT NULL,
  storage_key TEXT NOT NULL,
  archive_format VARCHAR(32) NOT NULL DEFAULT 'tar.zst',
  checksum_sha256 CHAR(64),
  size_bytes BIGINT NOT NULL DEFAULT 0,
  uncompressed_size_bytes BIGINT NOT NULL DEFAULT 0,
  included_paths JSONB NOT NULL DEFAULT '[]',

  failure_code VARCHAR(64),
  failure_message TEXT,

  created_at TIMESTAMPTZ NOT NULL,
  completed_at TIMESTAMPTZ,
  expires_at TIMESTAMPTZ,
  deleted_at TIMESTAMPTZ
);

CREATE INDEX idx_backups_server_created ON backups(server_id, created_at DESC);
CREATE INDEX idx_backups_owner ON backups(owner_id);
CREATE INDEX idx_backups_status ON backups(status);
CREATE INDEX idx_backups_retention ON backups(server_id, pinned, created_at DESC)
  WHERE status = 'completed' AND deleted_at IS NULL;
```

Backup jobs:

```sql
CREATE TABLE backup_jobs (
  id UUID PRIMARY KEY,
  backup_id UUID NOT NULL REFERENCES backups(id) ON DELETE CASCADE,
  server_id UUID NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
  requested_by_user_id UUID REFERENCES users(id),

  status backup_status NOT NULL,
  phase VARCHAR(64) NOT NULL DEFAULT 'pending',
  progress_percent INTEGER NOT NULL DEFAULT 0 CHECK (progress_percent BETWEEN 0 AND 100),
  progress_bytes BIGINT NOT NULL DEFAULT 0,
  total_bytes BIGINT NOT NULL DEFAULT 0,

  idempotency_key VARCHAR(128),
  worker_id VARCHAR(128),
  attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 1,
  locked_at TIMESTAMPTZ,
  heartbeat_at TIMESTAMPTZ,

  error_code VARCHAR(64),
  error_message TEXT,

  created_at TIMESTAMPTZ NOT NULL,
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_backup_jobs_server_status ON backup_jobs(server_id, status);
CREATE INDEX idx_backup_jobs_backup ON backup_jobs(backup_id);
CREATE UNIQUE INDEX uq_active_backup_job_per_server
  ON backup_jobs(server_id)
  WHERE status IN ('pending', 'running', 'held_saves', 'copying', 'compressing', 'verifying');
CREATE UNIQUE INDEX uq_backup_job_idempotency
  ON backup_jobs(server_id, idempotency_key)
  WHERE idempotency_key IS NOT NULL;
```

Backup schedules:

```sql
CREATE TABLE backup_schedules (
  id UUID PRIMARY KEY,
  server_id UUID NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
  owner_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,

  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  cron_expression VARCHAR(128),
  interval_minutes INTEGER,
  timezone VARCHAR(64) NOT NULL DEFAULT 'UTC',

  retention_count INTEGER NOT NULL DEFAULT 7,
  retention_days INTEGER,
  skip_if_server_offline BOOLEAN NOT NULL DEFAULT FALSE,
  defer_if_job_active BOOLEAN NOT NULL DEFAULT TRUE,

  last_run_at TIMESTAMPTZ,
  last_success_at TIMESTAMPTZ,
  next_run_at TIMESTAMPTZ,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,

  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_backup_schedules_due ON backup_schedules(enabled, next_run_at);
CREATE INDEX idx_backup_schedules_server ON backup_schedules(server_id);
```

Restore jobs:

```sql
CREATE TABLE restore_jobs (
  id UUID PRIMARY KEY,
  backup_id UUID NOT NULL REFERENCES backups(id),
  server_id UUID NOT NULL REFERENCES servers(id) ON DELETE CASCADE,
  requested_by_user_id UUID NOT NULL REFERENCES users(id),

  status restore_status NOT NULL,
  phase VARCHAR(64) NOT NULL DEFAULT 'pending',
  progress_percent INTEGER NOT NULL DEFAULT 0 CHECK (progress_percent BETWEEN 0 AND 100),
  progress_bytes BIGINT NOT NULL DEFAULT 0,
  total_bytes BIGINT NOT NULL DEFAULT 0,

  restart_after_restore BOOLEAN NOT NULL DEFAULT TRUE,
  server_was_running BOOLEAN NOT NULL DEFAULT FALSE,
  rollback_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  rollback_path TEXT,

  worker_id VARCHAR(128),
  attempts INTEGER NOT NULL DEFAULT 0,
  locked_at TIMESTAMPTZ,
  heartbeat_at TIMESTAMPTZ,

  error_code VARCHAR(64),
  error_message TEXT,

  created_at TIMESTAMPTZ NOT NULL,
  started_at TIMESTAMPTZ,
  completed_at TIMESTAMPTZ,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX idx_restore_jobs_server_status ON restore_jobs(server_id, status);
CREATE INDEX idx_restore_jobs_backup ON restore_jobs(backup_id);
CREATE UNIQUE INDEX uq_active_restore_job_per_server
  ON restore_jobs(server_id)
  WHERE status IN (
    'pending', 'running', 'validating_backup', 'stopping_server',
    'extracting', 'validating_restore', 'swapping', 'starting_server'
  );
```

## API Design

All endpoints must require authentication. Every `{server_id}` lookup must verify ownership or admin access.

### Create Backup

```text
POST /api/servers/{server_id}/backups
```

Request:

```json
{
  "name": "Before Nether reset",
  "description": "Optional user note",
  "idempotency_key": "client-generated-key",
  "force_offline": false
}
```

Response `202 Accepted`:

```json
{
  "backup_id": "uuid",
  "job_id": "uuid",
  "status": "pending",
  "phase": "pending",
  "progress_percent": 0,
  "already_running": false
}
```

Status codes:

- `202`: job accepted or existing active backup returned
- `401`: unauthenticated
- `403`: no permission
- `404`: server not found
- `409`: restore already active
- `422`: invalid request
- `429`: rate limited
- `507`: quota or disk capacity exceeded

Validation:

- `name` max 128 chars.
- `description` max 2000 chars.
- `idempotency_key` max 128 chars.
- enforce per-user/manual backup rate limit.
- enforce quota before enqueue.

### List Backups

```text
GET /api/servers/{server_id}/backups?status=completed&limit=50&cursor=...
```

Response `200 OK`:

```json
{
  "items": [
    {
      "backup_id": "uuid",
      "server_id": "uuid",
      "name": "Before Nether reset",
      "kind": "manual",
      "status": "completed",
      "size_bytes": 412345678,
      "world_name": "Bedrock level",
      "created_at": "2026-06-05T12:00:00Z",
      "completed_at": "2026-06-05T12:02:15Z",
      "pinned": false
    }
  ],
  "next_cursor": null
}
```

### Get Backup

```text
GET /api/servers/{server_id}/backups/{backup_id}
```

Response `200 OK`:

```json
{
  "backup_id": "uuid",
  "server_id": "uuid",
  "kind": "manual",
  "status": "completed",
  "phase": "completed",
  "progress_percent": 100,
  "size_bytes": 412345678,
  "checksum_sha256": "hex encoded sha256",
  "created_at": "2026-06-05T12:00:00Z",
  "completed_at": "2026-06-05T12:02:15Z",
  "failure_code": null,
  "failure_message": null
}
```

### Download Backup

```text
GET /api/servers/{server_id}/backups/{backup_id}/download
```

Behavior:

- Validate ownership.
- Validate backup belongs to server.
- Validate status is `completed`.
- Stream local file using `FileResponse` or `StreamingResponse`.
- Do not reveal storage path.
- Set `Content-Disposition: attachment`.

Status codes:

- `200`: stream archive
- `403`: no permission
- `404`: backup not found
- `409`: backup not completed
- `410`: object deleted or expired

### Delete Backup

```text
DELETE /api/servers/{server_id}/backups/{backup_id}
```

Request:

```json
{
  "delete_even_if_pinned": false
}
```

Response `202 Accepted` or `204 No Content`.

Prefer `202` if deletion is done by worker, especially for S3 later.

Validation:

- Cannot delete active backup.
- Cannot delete pinned backup unless explicit flag and permission allow it.

### Create Restore

```text
POST /api/servers/{server_id}/restores
```

Request:

```json
{
  "backup_id": "uuid",
  "restart_after_restore": true,
  "rollback_enabled": true,
  "confirm": "RESTORE"
}
```

Response `202 Accepted`:

```json
{
  "restore_job_id": "uuid",
  "backup_id": "uuid",
  "server_id": "uuid",
  "status": "pending",
  "phase": "pending",
  "progress_percent": 0
}
```

Status codes:

- `202`: restore accepted
- `400`: missing confirmation
- `401`: unauthenticated
- `403`: no permission
- `404`: server or backup not found
- `409`: server running cannot be restored synchronously, or active job exists
- `422`: invalid request
- `507`: insufficient disk space

Recommended behavior:

- API may accept restore while server is running because the worker will stop it.
- If product policy requires explicit user stop first, return `409`.
- The safer UX is to accept with `restart_after_restore` and clearly show downtime state.

### Job Status Endpoints

```text
GET /api/servers/{server_id}/backup-jobs/{job_id}
GET /api/servers/{server_id}/restore-jobs/{job_id}
GET /api/servers/{server_id}/jobs
```

Use these for frontend progress polling.

## Security Requirements

Permission checks:

- User must own server or have admin role.
- Backup ownership is validated through both `backup.server_id` and `backup.owner_id`.
- Restore may require a stronger permission than download/delete.

Path traversal protection:

- Never accept archive paths from the client.
- Derive paths only from configured roots and UUIDs.
- Resolve every final path and verify it is under the expected root.
- Reject archive members with:
  - absolute paths
  - `..`
  - symlinks, unless explicitly supported and sanitized
  - hard links

Backup ownership validation:

- `backup.server_id == server.id`
- `backup.owner_id == current_user.id` unless admin.
- Never restore a backup from another server unless a separate import flow exists.

Rate limiting:

- Per-user manual backup creation limit, for example 3/hour on free tier.
- Per-server active job limit: 1.
- Download rate limit and bandwidth throttling for abuse control.

Quota enforcement:

- Track per-user and per-server backup storage usage.
- Estimate backup size before creation using world size.
- Reject new backup if quota would be exceeded.
- Run retention before rejecting if policy allows deleting old unpinned backups.

## Scalability Plan

### 10 Servers

- One FastAPI process.
- One Redis instance.
- One Celery worker with concurrency 1-2 for backup jobs.
- Local disk storage.
- Polling progress is enough.

### 100 Servers

Bottlenecks:

- Disk I/O during simultaneous copy/compression.
- CPU during compression.
- Local disk capacity.
- Long API requests if download streaming is served by API.

Changes:

- Separate backup worker queue.
- Limit backup worker concurrency per host.
- Use low-priority I/O if available.
- Add retention and quota enforcement.
- Add metrics for backup duration, failures, bytes written.

### 1000 Servers

Bottlenecks:

- Single-node local storage no longer scales.
- Download bandwidth competes with game servers.
- Scheduler must avoid thundering herd jobs.
- Restore operations need placement awareness.

Changes:

- Store backups in S3-compatible storage.
- Keep storage abstraction:
  - `put_file(local_path, storage_key)`
  - `get_file(storage_key, local_path)`
  - `delete(storage_key)`
  - `generate_download_url(storage_key)`
- Use presigned URLs for download.
- Shard workers by host/region.
- Add per-node concurrency and disk-pressure controls.
- Add jitter to scheduled backups.
- Move schedule dispatch into a singleton scheduler or distributed lock.

## Backup Pseudocode

```python
def create_backup(server_id, user_id, request):
    server = get_owned_server(server_id, user_id)
    enforce_rate_limit(user_id, "manual_backup")
    enforce_storage_quota(server.owner_id, server_id)

    active_restore = find_active_restore(server_id)
    if active_restore:
        raise Conflict("restore_active")

    existing = find_active_backup(server_id, request.idempotency_key)
    if existing:
        return existing

    backup = insert_backup(
        server_id=server.id,
        owner_id=server.owner_id,
        created_by_user_id=user_id,
        kind="manual",
        status="pending",
        name=request.name,
        storage_backend="local",
    )
    job = insert_backup_job(
        backup_id=backup.id,
        server_id=server.id,
        requested_by_user_id=user_id,
        status="pending",
        idempotency_key=request.idempotency_key,
    )
    enqueue("backup.create", job.id)
    return job
```

```python
def run_backup_job(job_id):
    job = load_job(job_id)

    with postgres_advisory_lock(job.server_id):
        if has_active_restore(job.server_id):
            fail(job, "restore_active")
            return

        mark_running(job, phase="running", progress=1)
        server = load_server(job.server_id)
        backup = load_backup(job.backup_id)

        staging = paths.backup_staging(server.id, job.id)
        temp_archive = paths.temp_archive(server.id, backup.id)
        final_archive = paths.final_archive(server.id, backup.id)

        server_was_running = lifecycle.is_running(server.id)
        consistency_method = None
        save_held = False

        try:
            ensure_empty_dir(staging)
            estimate = estimate_backup_input_size(server)
            update_progress(job, phase="running", total_bytes=estimate)

            if server_was_running and not job.force_offline:
                try:
                    lifecycle.send_command(server.id, "save hold")
                    wait_until_save_query_ready(server.id, timeout=30)
                    save_held = True
                    consistency_method = "save_hold"
                    update_progress(job, phase="held_saves", progress=8)
                    copy_backup_inputs(server, staging, job)
                finally:
                    if save_held:
                        lifecycle.send_command(server.id, "save resume")
            else:
                if server_was_running:
                    lifecycle.stop(server.id, timeout=60)
                consistency_method = "server_stop"
                copy_backup_inputs(server, staging, job)

            if server_was_running and consistency_method == "server_stop":
                lifecycle.start(server.id)

            validate_staging_backup(staging)
            write_manifest(staging, backup, consistency_method, server_was_running)

            compress_tar_zst(staging, temp_archive, job)
            checksum = sha256_file(temp_archive)
            size = file_size(temp_archive)

            atomic_rename(temp_archive, final_archive)
            write_text(final_archive.with_suffix(".tar.zst.sha256"), checksum)

            mark_backup_completed(
                backup,
                storage_key=storage_key_for(final_archive),
                checksum_sha256=checksum,
                size_bytes=size,
                consistency_method=consistency_method,
                server_was_running=server_was_running,
            )
            mark_job_completed(job)
            apply_retention(server.id)

        except Exception as exc:
            cleanup_file(temp_archive)
            mark_backup_failed(backup, exc)
            mark_job_failed(job, exc)
            if server_was_running and not lifecycle.is_running(server.id):
                lifecycle.start(server.id)
            raise
        finally:
            if save_held:
                lifecycle.send_command_best_effort(server.id, "save resume")
            cleanup_dir(staging)
```

## Restore Pseudocode

```python
def create_restore(server_id, user_id, request):
    if request.confirm != "RESTORE":
        raise BadRequest("confirmation_required")

    server = get_owned_server(server_id, user_id)
    backup = get_backup(request.backup_id)

    if backup.server_id != server.id:
        raise NotFound("backup_not_found")
    if backup.status != "completed":
        raise Conflict("backup_not_completed")
    if find_active_backup_or_restore(server.id):
        raise Conflict("job_active")

    check_disk_space_for_restore(server, backup)

    job = insert_restore_job(
        backup_id=backup.id,
        server_id=server.id,
        requested_by_user_id=user_id,
        status="pending",
        restart_after_restore=request.restart_after_restore,
        rollback_enabled=request.rollback_enabled,
    )
    enqueue("backup.restore", job.id)
    return job
```

```python
def run_restore_job(job_id):
    job = load_restore_job(job_id)

    with postgres_advisory_lock(job.server_id):
        server = load_server(job.server_id)
        backup = load_backup(job.backup_id)

        archive = storage.fetch_to_local_if_needed(backup.storage_key)
        restore_tmp = paths.restore_tmp(server.id, job.id)
        rollback_dir = paths.rollback_tmp(server.id, job.id)
        active_world = paths.active_world(server.id, backup.world_name)

        server_was_running = lifecycle.is_running(server.id)

        try:
            mark_restore(job, "validating_backup", 5)
            verify_checksum(archive, backup.checksum_sha256)
            validate_archive_members(archive)
            check_disk_space_for_restore(server, backup)

            mark_restore(job, "stopping_server", 15)
            if server_was_running:
                lifecycle.stop(server.id, timeout=60)

            mark_restore(job, "extracting", 25)
            ensure_empty_dir(restore_tmp)
            extract_archive_safely(archive, restore_tmp, job)

            mark_restore(job, "validating_restore", 65)
            restored_world = find_and_validate_world(restore_tmp, backup.world_name)

            mark_restore(job, "swapping", 75)
            ensure_same_filesystem(active_world.parent, rollback_dir)
            ensure_empty_dir(rollback_dir.parent)

            atomic_rename(active_world, rollback_dir / backup.world_name)
            try:
                atomic_rename(restored_world, active_world)
            except Exception:
                atomic_rename(rollback_dir / backup.world_name, active_world)
                raise

            validate_active_world(active_world)

            if job.restart_after_restore:
                mark_restore(job, "starting_server", 90)
                lifecycle.start(server.id)
                wait_for_healthy_or_raise(server.id, timeout=60)

            mark_restore_completed(job)
            schedule_rollback_cleanup(rollback_dir)

        except Exception as exc:
            mark_restore_failed(job, exc)
            if job.rollback_enabled:
                rollback_restore_if_needed(active_world, rollback_dir, backup.world_name)
                mark_restore_rolled_back(job)
            if server_was_running and not lifecycle.is_running(server.id):
                lifecycle.start(server.id)
            raise
        finally:
            cleanup_dir(restore_tmp)
```

## Failure Recovery Strategy

Worker startup recovery:

1. Find backup jobs stuck in active statuses with stale `heartbeat_at`.
2. For each stale backup job:
   - If final archive exists and checksum matches, mark completed.
   - If only temp archive/staging exists, delete temp files and mark failed.
   - Ensure `save resume` is sent if server is running.
3. Find restore jobs stuck in active statuses with stale `heartbeat_at`.
4. For each stale restore:
   - If phase is before `swapping`, clean staging and mark failed.
   - If active world missing and rollback exists, move rollback back.
   - If active world exists and rollback exists, mark `needs_manual_review` or `rolled_back` depending on health check.

Operational alerts:

- backup failure rate
- restore failure
- corrupted backup checksum
- disk free space below threshold
- jobs stuck without heartbeat
- retention deletion failures

## Production Recommendations

- Add a `BackupStorage` interface before implementing local storage.
- Use `tarfile` carefully or call system `tar` with explicit sanitized paths; never extract untrusted members without validating names.
- Prefer `zstandard` compression level 3-6 for speed.
- Run backup workers with lower CPU/I/O priority where possible.
- Cap concurrent backups per physical host.
- Use `save hold` with strict timeout and guaranteed `save resume`.
- Keep restore staging on the same filesystem as active worlds for atomic rename.
- Keep rollback data until restore health checks pass.
- Add integration tests using a fake lifecycle manager and fixture world directories.
- Add chaos tests for interruption during copy, compression, extraction, and swap.
- Design download through a storage abstraction now so S3 presigned URLs can replace API streaming later.
