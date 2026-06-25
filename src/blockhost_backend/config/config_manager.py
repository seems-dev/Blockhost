from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file="src/.env", env_file_encoding="utf-8", extra="ignore")

    # Default to SQLite for local dev (kids-friendly). Set DATABASE_URL to use Postgres.
    database_url: str = "sqlite+pysqlite:///./database/blockhost.db"
    redis_url: str = "redis://localhost:6379/0"

    jwt_secret_key: str = "change-me"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_seconds: int = 60 * 60 * 24
    jwt_refresh_token_expire_seconds: int = 60 * 60 * 24 * 30

    minecraft_public_host: str = "192.168.29.102"
    minecraft_port: int = 19132

    # Local Bedrock runtime (direct process spawning).
    # These paths are resolved relative to the backend working directory.
    bedrock_versions_dir: str = "versions"
    bedrock_servers_dir: str = "servers"
    bedrock_logs_dir: str = "logs"
    bedrock_versions_manifest: str = "versions/manifest.json"
    bedrock_port_range_start: int = 19132
    bedrock_port_range_end: int = 19232
    backup_storage_dir: str = "backups"
    backup_temp_dir: str = "backups/tmp"
    backup_retention_count: int = 7
    backup_save_hold_seconds: float = 2.0
    backup_worker_threads: int = 2
    backup_scheduler_enabled: bool = False
    backup_scheduler_poll_seconds: int = 60
    subscription_expiration_enabled: bool = True
    subscription_expiration_poll_seconds: int = 60
    subscription_grace_period_days: int = 3
    billing_provider: str = "test"
    billing_signature_secret: str = "change-me-billing-secret"
    disk_warning_threshold_percent: float = 80.0
    disk_critical_threshold_percent: float = 90.0
    disk_health_monitor_enabled: bool = True
    disk_health_check_interval_seconds: int = 10 * 60
    free_world_limit_gb: float = 2.0
    premium_world_limit_gb: float = 10.0
    # Optional explicit executable name inside a version/server folder.
    # If empty, BlockHost will try common Bedrock server binary names.
    bedrock_executable_name: str = ""
    bedrock_runtime_driver: str = "systemd"

    worker_agent_url: str = "http://localhost:9000"
    worker_agent_token: str = "change-me-in-dev"

    # Console streaming configuration
    console_queue_max_size: int = 1000  # Max buffered log lines per websocket connection
    console_stream_cleanup_enabled: bool = True  # Stop log stream when no listeners remain (but keep running if player tracking active)

    google_client_id: str = "264249625264-0it828liska1emqu72ebb26s6u6krnmu.apps.googleusercontent.com"  # Add this line before the last two settings

@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
