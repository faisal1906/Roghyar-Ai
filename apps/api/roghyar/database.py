from sqlalchemy import URL

from .settings import Settings


def database_url(settings: Settings) -> URL:
    return URL.create(
        "postgresql+psycopg",
        username=settings.db_user,
        password=settings.db_password.get_secret_value(),
        host=settings.db_host,
        port=settings.db_port,
        database=settings.db_name,
        query={
            "connect_timeout": "2",
            "options": "-c timezone=UTC -c statement_timeout=2000 -c search_path=pg_catalog,public,extensions",
            "sslmode": "verify-full" if settings.environment == "production" else "prefer",
        },
    )
