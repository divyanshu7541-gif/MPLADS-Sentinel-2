from collections.abc import Generator

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from ..core.config import get_settings

_engine: Engine | None = None
_SessionLocal: sessionmaker | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        # hide_parameters: bound values (descriptions, description_normalized, names) never appear
        # in SQL logs or in database error messages; results are unaffected.
        # Pool sizing comes from DB_POOL_SIZE / DB_MAX_OVERFLOW / DB_POOL_RECYCLE
        # (app/core/config.py); pre-ping replaces connections the server closed.
        s = get_settings()
        _engine = create_engine(
            s.database_url,
            pool_size=s.db_pool_size,
            max_overflow=s.db_max_overflow,
            pool_recycle=s.db_pool_recycle,
            pool_pre_ping=True,
            hide_parameters=True,
        )
    return _engine


def get_session_factory() -> sessionmaker:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)
    return _SessionLocal


def get_db() -> Generator[Session, None, None]:
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.close()
