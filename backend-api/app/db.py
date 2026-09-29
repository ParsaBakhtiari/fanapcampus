from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


_settings = get_settings()
_engine_kwargs: dict = {"pool_pre_ping": True}
if not _settings.database_url.startswith("sqlite"):
    # Recycle before RDS / NAT idle timeouts close connections.
    _engine_kwargs.update(
        pool_recycle=1800,
        pool_size=_settings.db_pool_size,
        max_overflow=_settings.db_max_overflow,
    )

engine = create_engine(_settings.database_url, **_engine_kwargs)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
