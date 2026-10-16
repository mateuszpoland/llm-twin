from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from twin.settings import settings

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Credentials come from the environment, never from alembic.ini.
config.set_main_option("sqlalchemy.url", settings.database_url)

# No declarative models yet - migrations execute reviewed SQL files.
# When ORM models arrive, point this at their metadata to unlock autogenerate.
target_metadata = None


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
