"""SQLAlchemy engine and sessions. Each app instance builds its own, so tests get an isolated database."""

from collections.abc import Iterator
from datetime import datetime, timezone

from fastapi import Request
from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    """Naive UTC timestamp (SQLite has no time zones); the API adds the UTC offset when serializing."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def create_db_engine(url: str) -> Engine:
    if not url.startswith("sqlite"):
        return create_engine(url, pool_pre_ping=True)

    in_memory = url in ("sqlite://", "sqlite:///:memory:")
    engine = create_engine(
        url,
        # FastAPI runs sync endpoints in a thread pool, so connections cross threads.
        connect_args={"check_same_thread": False},
        poolclass=StaticPool if in_memory else None,
    )

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db(request: Request) -> Iterator[Session]:
    session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()
