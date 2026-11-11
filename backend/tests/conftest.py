"""Shared pytest fixtures for DocSense foundation tests."""

from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.session import create_db_engine, create_session_factory, init_db


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    """Create isolated Settings pointing all persistence directories into tmp_path."""
    db_file = tmp_path / "db" / "docsense_test.db"
    return Settings(
        _env_file=None,
        DATABASE_URL=f"sqlite:///{db_file.as_posix()}",
        CHROMA_PERSIST_DIR=str(tmp_path / "chroma"),
        UPLOAD_DIR=str(tmp_path / "uploads"),
    )


@pytest.fixture
def sqlite_file_engine(tmp_path: Path) -> Generator[Engine, None, None]:
    """Create and initialize a file-backed SQLite engine, disposing cleanly on teardown."""
    db_path = tmp_path / "foundation_test.db"
    engine = create_db_engine(f"sqlite:///{db_path.as_posix()}")
    init_db(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def db_session(sqlite_file_engine: Engine) -> Generator[Session, None, None]:
    """Yield a SQLAlchemy Session bound to an initialized file-backed SQLite engine."""
    session_factory = create_session_factory(sqlite_file_engine)
    with session_factory() as session:
        yield session
