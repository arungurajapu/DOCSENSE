"""SQLAlchemy engine creation, connection pragma listeners, and database initialization."""

from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base


def _is_sqlite_url(database_url: str) -> bool:
    return database_url.strip().lower().startswith("sqlite")


def _is_file_backed_sqlite(engine: Engine) -> bool:
    if engine.dialect.name != "sqlite":
        return False
    db_name = engine.url.database
    if not db_name or db_name == ":memory:":
        return False
    if db_name.startswith("file::memory:"):
        return False
    query = {k.lower(): str(v).lower() for k, v in engine.url.query.items()}
    if query.get("mode") == "memory":
        return False
    return True


def create_db_engine(database_url: str, *, echo: bool = False) -> Engine:
    """Create a SQLAlchemy 2.x engine with SQLite connection pragma discipline.

    The SQLite ``connect`` listener configures ONLY per-connection ``foreign_keys=ON``
    and ``busy_timeout=5000``. Persistent ``journal_mode=WAL`` is applied once in
    :func:`init_db` for file-backed SQLite databases.
    """
    connect_args: dict[str, Any] = {}
    if _is_sqlite_url(database_url):
        connect_args["check_same_thread"] = False

    engine = create_engine(
        database_url,
        echo=echo,
        future=True,
        connect_args=connect_args,
    )

    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _set_sqlite_connection_pragmas(
            dbapi_connection: Any,
            _connection_record: Any,
        ) -> None:
            cursor = dbapi_connection.cursor()
            try:
                cursor.execute("PRAGMA foreign_keys=ON;")
                cursor.execute("PRAGMA busy_timeout=5000;")
            finally:
                cursor.close()

    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a configured SQLAlchemy 2.x sessionmaker bound to ``engine``."""
    return sessionmaker(
        bind=engine,
        autocommit=False,
        autoflush=False,
        expire_on_commit=False,
        class_=Session,
    )


def init_db(engine: Engine) -> str | None:
    """Initialize schema tables and enable WAL mode once on file-backed SQLite."""
    # Ensure ORM models are imported so Base.metadata contains all tables.
    import app.models  # noqa: F401

    is_file_sqlite = _is_file_backed_sqlite(engine)
    if is_file_sqlite and engine.url.database:
        db_path = Path(engine.url.database)
        if not str(db_path).startswith("file:"):
            db_path.parent.mkdir(parents=True, exist_ok=True)

    Base.metadata.create_all(bind=engine)

    if is_file_sqlite:
        with engine.connect() as conn:
            autocommit_conn = conn.execution_options(isolation_level="AUTOCOMMIT")
            mode = autocommit_conn.execute(text("PRAGMA journal_mode=WAL;")).scalar()
            return str(mode).lower() if mode is not None else None

    return None
