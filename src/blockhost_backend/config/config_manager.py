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
    # Optional explicit executable name inside a version/server folder.
    # If empty, BlockHost will try common Bedrock server binary names.
    bedrock_executable_name: str = ""
    bedrock_runtime_driver: str = "local_process"

    worker_agent_url: str = "http://localhost:9000"
    worker_agent_token: str = "change-me-in-dev"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
