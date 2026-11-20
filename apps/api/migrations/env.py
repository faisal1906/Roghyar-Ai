from alembic import context
from sqlalchemy import create_engine, pool

from roghyar.database import database_url
from roghyar.models import metadata
from roghyar.settings import Settings


def include_name(name, type_, parent_names):
    # Alembic must never autogenerate changes for PostGIS or other schemas.
    return type_ != "schema" or name == metadata.schema


def run_migrations() -> None:
    url = database_url(Settings())
    if context.is_offline_mode():
        context.configure(
            url=url, literal_binds=True, target_metadata=metadata, include_schemas=True,
            version_table_schema="public",
        )
        with context.begin_transaction():
            context.run_migrations()
        return

    engine = create_engine(url, poolclass=pool.NullPool)
    try:
        with engine.connect() as connection:
            context.configure(
                connection=connection, target_metadata=metadata, include_schemas=True,
                version_table_schema="public", include_name=include_name,
            )
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


run_migrations()
