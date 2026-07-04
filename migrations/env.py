"""
VIGIL-AI Cameroun — Alembic Environment
Configures SQLAlchemy migrations against PostgreSQL (SYNC ONLY)
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine, pool

from app.config import settings
from app.database import Base

# Import all models so Alembic can detect them
from app.models import (  # noqa: F401
    Alert,
    AuditLog,
    Analysis,
    Case,
    CaseHistory,
    CaseNote,
    Role,
    Submission,
    User,
)

# Alembic Config object
config = context.config

# Use sync database URL for migrations (IMPORTANT FIX)
config.set_main_option(
    "sqlalchemy.url",
    settings.SYNC_DATABASE_URL
)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations without a DB connection."""
    url = config.get_main_option("sqlalchemy.url")

    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations using a SYNC engine (required for Alembic stability)."""

    connectable = create_engine(
        config.get_main_option("sqlalchemy.url"),
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()