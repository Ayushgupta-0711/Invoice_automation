"""SQLAlchemy connection management for the Invoice Automation database."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import URL, create_engine, text
from sqlalchemy.engine import Engine

ROOT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(ROOT_DIR / ".env")


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Create and cache a MySQL engine using environment configuration."""
    import os

    required = ("DB_USER", "DB_PASSWORD", "DB_NAME")
    missing = [key for key in required if not os.getenv(key)]
    if missing:
        raise RuntimeError(
            "Missing database settings in .env: " + ", ".join(missing)
            + ". Copy .env.example to .env and enter your MySQL credentials."
        )

    url = URL.create(
        drivername="mysql+pymysql",
        username=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "3306")),
        database=os.environ["DB_NAME"],
        query={"charset": "utf8mb4"},
    )
    return create_engine(
        url,
        pool_pre_ping=True,
        pool_recycle=1800,
        pool_size=5,
        max_overflow=10,
        future=True,
    )


def test_connection() -> dict[str, str]:
    """Check the database connection and return server/database details."""
    with get_engine().connect() as connection:
        version = str(connection.execute(text("SELECT VERSION()")).scalar_one())
        database = str(connection.execute(text("SELECT DATABASE()")).scalar_one())
    return {"status": "connected", "mysql_version": version, "database": database}
