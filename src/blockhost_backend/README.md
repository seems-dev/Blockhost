**BlockHost Backend — Architecture Overview**

- **Purpose**: Central HTTP API and orchestration service that manages user accounts, game servers (Minecraft Bedrock), backups, and worker orchestration.

**Components**
- **API server**: FastAPI application entrypoint at [src/blockhost_backend/main.py](src/blockhost_backend/main.py). Registers routers under `/api/*` and starts background services (e.g., backup scheduler).
- **API routers**: Modular endpoints in [src/blockhost_backend/api/](src/blockhost_backend/api/) (examples: `auth`, `versions`, `files`, `servers`, `backups`). Each router uses FastAPI dependency injection for auth and DB access.
- **Database layer**: SQLAlchemy models and schema defined in [src/blockhost_backend/database/schema.py](src/blockhost_backend/database/schema.py). Connection and session lifecycle in [src/blockhost_backend/database/db.py](src/blockhost_backend/database/db.py).
- **Security**: JWT based auth and password hashing implemented in [src/blockhost_backend/core/security.py](src/blockhost_backend/core/security.py). Request auth helpers in [src/blockhost_backend/api/deps.py](src/blockhost_backend/api/deps.py).
- **Orchestration**: Lightweight orchestrator interface in [src/blockhost_backend/orchestrator/](src/blockhost_backend/orchestrator/) that abstracts where and how Bedrock servers run (local/systemd, remote VMs, or worker agents).
- **Worker agent client**: `WorkerClient` (in [src/blockhost_backend/orchestrator/worker_client.py](src/blockhost_backend/orchestrator/worker_client.py)) talks to an external worker agent microservice (configured by `worker_agent_url`) to start/stop containerized Bedrock instances.
- **Config**: Environment-driven settings via [src/blockhost_backend/config/config_manager.py](src/blockhost_backend/config/config_manager.py).

**High-level request flow**
- **Client → API**: Frontend (Flutter app or web) calls the FastAPI endpoints (`/api/*`). These routes are implemented as APIRouter modules and mounted in [main.py](src/blockhost_backend/main.py).
- **Auth → DB**: Protected endpoints use `get_current_user` (see [api/deps.py](src/blockhost_backend/api/deps.py)) which decodes JWTs via `core/security.py` and loads the `User` from the DB session provided by `get_db()` (see [database/db.py](src/blockhost_backend/database/db.py)).
- **API → Orchestrator/Workers**: When a server lifecycle action is requested (create/start/stop/remove), the API uses the orchestrator abstractions. For remote/local worker setups this results in calls to the `WorkerClient` (HTTP) or local `ServerLifecycleOrchestrator` (systemd/local runtime).
- **Backups & background work**: Backup scheduling and job processing run via the backend's scheduler. The scheduler is started during FastAPI startup (`start_backup_scheduler_once()` in [main.py](src/blockhost_backend/main.py)). Backup jobs are represented in the DB (`backup_jobs`, `backups`) and may be executed by worker processes that update job state.

**API design highlights**
- **REST-ish routers**: Routes are grouped by resource (e.g., `/api/auth`, `/api/versions`, `/api/backups`). See [src/blockhost_backend/api/](src/blockhost_backend/api/).
- **Dependency injection**: DB session and auth are provided via FastAPI `Depends()` helpers (`get_db`, `get_current_user`). This keeps handlers simple and testable.
- **Asynchronous-friendly**: Some endpoints (file upload, background install) use background threads or async endpoints and return immediate status for long-running tasks (see `/api/versions/{version}/download`).
- **Idempotency & job models**: Long-running work (backups/restores) is modelled with `BackupJob` / `RestoreJob` rows that carry progress, lock/heartbeat, attempts and worker assignment.

**Data model & persistence**
- **ORM**: SQLAlchemy declarative models live in [database/schema.py](src/blockhost_backend/database/schema.py). Key tables: `users`, `servers`, `backups`, `backup_jobs`, `restore_jobs`, `blockcoin_ledger`.
- **DB URL**: `DATABASE_URL` controls engine. Default is local SQLite (see [config_manager.py](src/blockhost_backend/config/config_manager.py)), but production should use Postgres (application performs light ALTERs on startup when detecting Postgres).

**Orchestration & microservices**
- **Orchestrator abstraction**: `Orchestrator` and `ServerLifecycleOrchestrator` provide a place to implement provisioning (VM providers), runtime drivers (systemd), and worker coordination.
- **Worker agent**: The worker agent is a separate microservice (HTTP API) that the backend calls via `WorkerClient`. It exposes endpoints such as `/containers/start`, `/containers/stop`, and `/containers/remove` and is authenticated with an `X-Worker-Token` header. Worker URL and token are configured in settings (`worker_agent_url`, `worker_agent_token`).
- **Local runtime**: For development the backend can control local Bedrock instances via a `systemd` runtime adapter (see `runtime/systemd_runtime.py` referenced by the lifecycle orchestrator).

**Dependencies (selected)**
- **FastAPI**: HTTP framework and router system.
- **SQLAlchemy**: ORM and DB schema management.
- **pydantic-settings**: Configuration management.
- **httpx**: Backend HTTP client used by `WorkerClient`.
- **passlib / jose**: Password hashing and JWT handling.
- **Google auth libs**: `google-auth` for Google sign-in flow.

**Environment & configuration**
- **Settings source**: `src/.env` and environment variables; see [config/config_manager.py](src/blockhost_backend/config/config_manager.py).
- **Important vars**: `DATABASE_URL`, `REDIS_URL`, `JWT_SECRET_KEY`, `WORKER_AGENT_URL`, `WORKER_AGENT_TOKEN`, `BEDROCK_*` directories and runtime driver.

**Running locally (developer notes)**
- **Startup**: Run the FastAPI app (e.g., `uvicorn blockhost_backend.main:app --reload`). The app will create DB tables on startup and start the backup scheduler.
- **Worker agent**: For full lifecycle features (container-based servers) run the companion worker agent and set `WORKER_AGENT_URL` / `WORKER_AGENT_TOKEN` accordingly.

**Console streaming implementation**
- **Live console**: WebSocket endpoint at `/{server_id}/console/ws` streams Bedrock server logs in real-time to connected clients.
- **Data flow**: systemd journal → `journalctl -f` process (per server) → background reader thread (parses player join/leave, calls listeners) → per-connection bounded `asyncio.Queue` → WebSocket → frontend.
- **Bounded memory**: Each websocket connection uses a queue with `console_queue_max_size` (default 1000 lines). When full, oldest entries are dropped automatically (no unbounded growth).
- **Listener hardening**: Exceptions in listener callbacks are caught and logged; one failed listener does not crash the log stream or affect other listeners.
- **Resource cleanup**: Log streams can optionally stop when no listeners remain (configurable via `console_stream_cleanup_enabled`). Reconnecting listeners automatically restart the stream. Player tracking is paused during cleanup but resumes on reconnection.
- **Configuration**: Use `CONSOLE_QUEUE_MAX_SIZE` and `CONSOLE_STREAM_CLEANUP_ENABLED` environment variables to tune.

**Where to look next**
- **API handlers**: [src/blockhost_backend/api/](src/blockhost_backend/api/)
- **Orchestration & worker client**: [src/blockhost_backend/orchestrator/](src/blockhost_backend/orchestrator/)
- **Console streaming**: [src/blockhost_backend/runtime/systemd_runtime.py](src/blockhost_backend/runtime/systemd_runtime.py) (log stream) and [src/blockhost_backend/api/servers.py](src/blockhost_backend/api/servers.py) (websocket handler)
- **Data models**: [src/blockhost_backend/database/schema.py](src/blockhost_backend/database/schema.py)

**Hardening notes**
- Console streaming is memory-bounded per connection (prevents DoS via slow clients).
- Listener exceptions are isolated and logged (prevents cascade failures).
- Log streams clean up when unused to free systemd/journalctl resources.
- All three improvements maintain backward-compatible behavior and APIs.
