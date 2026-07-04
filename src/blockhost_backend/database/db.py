from __future__ import annotations

from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.engine.url import make_url

from blockhost_backend.config.config_manager import get_settings
#file_name = db.py

settings = get_settings()

connect_args = {}
if settings.database_url.startswith("sqlite"):
    try:
        url = make_url(settings.database_url)
        if url.database:
            db_path = Path(url.database)
            # For relative paths like ./database/blockhost.db ensure parent exists.
            if not db_path.is_absolute():
                db_path.parent.mkdir(parents=True, exist_ok=True)
            else:
                db_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        # Best-effort; create_engine will raise a clear error if still invalid.
        pass
    connect_args = {"check_same_thread": False}

engine = create_engine(settings.database_url, pool_pre_ping=True, connect_args=connect_args)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
