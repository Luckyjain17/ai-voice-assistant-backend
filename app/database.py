import os
from collections.abc import Generator
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.env import load_backend_env

load_backend_env()

DATABASE_URL = os.getenv("DATABASE_URL") or "sqlite:///./ai_call_db.sqlite"

engine_kwargs = {}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

engine = create_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)


def safe_database_url() -> str:
    parsed = urlsplit(DATABASE_URL)
    if not parsed.username and not parsed.password:
        return DATABASE_URL

    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    netloc = f"{parsed.username}:***@{host}" if parsed.username else host
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))
