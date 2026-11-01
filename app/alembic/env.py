"""Configure Alembic migrations and offline SQL generation for the application database."""

from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context
# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
  fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
from sqlmodel import SQLModel
from core.config import settings

target_metadata = SQLModel.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.

def get_db_url() -> str:
  """Return an explicitly supplied database URL or the configured application URL."""
  return config.attributes.get("database_url", str(settings.DATABASE_URL))

def run_migrations_offline() -> None:
  """Emit migration SQL for the configured URL without opening a database connection."""

  url = get_db_url()
  context.configure(url=url, target_metadata=target_metadata, literal_binds=True, dialect_opts={"paramstyle": "named"},)

  with context.begin_transaction():
    context.run_migrations()


def run_migrations_online() -> None:
  """Apply migrations using a supplied connection or a temporary database engine."""
  connection = config.attributes.get("connection")
  if connection is not None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
      context.run_migrations()
    return
  configuration = config.get_section(config.config_ini_section)
  assert configuration is not None
  configuration["sqlalchemy.url"] = get_db_url()
  connectable = engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool,)

  with connectable.connect() as connection:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
      context.run_migrations()


if context.is_offline_mode():
  run_migrations_offline()
else:
  run_migrations_online()
