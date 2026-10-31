"""Alembic environment: the URL comes from Settings (or DATABASE_URL); SQLite needs batch mode for ALTERs."""
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import Settings  # noqa: E402
from app.db import Base  # noqa: E402

config = context.config
if config.config_file_name:
    fileConfig(config.config_file_name)
url = config.get_main_option("sqlalchemy.url") or os.environ.get("DATABASE_URL") or Settings().database_url
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(url)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
