"""Process-level configuration, read once from the environment.

Not a domain module (see MODULES.md) — this is the one place infra values
(DB connection, the per-launch bearer token) are read from the environment,
so no other module hardcodes a port, URL or credential (Rules.md).
"""

from functools import lru_cache

from pydantic_settings import BaseSettings


class Config(BaseSettings):
    bearer_token: str
    postgres_user: str = "researchos"
    postgres_password: str = "researchos"
    postgres_db: str = "researchos"
    postgres_port: int = 5433

    @property
    def database_url(self) -> str:
        """The app's runtime connection string — asyncpg, per TRD §1.4."""
        return self._url("asyncpg")

    @property
    def sync_database_url(self) -> str:
        """Alembic's own migration runner needs a sync driver; psycopg (v3)
        is already a transitive dependency via saq[postgres], so it costs no
        extra dependency to reuse it here instead of adding psycopg2."""
        return self._url("psycopg")

    @property
    def libpq_dsn(self) -> str:
        """Plain libpq-style DSN (no SQLAlchemy driver suffix) for SAQ's
        PostgresQueue, which opens its own psycopg pool directly."""
        return (
            f"postgresql://{self.postgres_user}:{self.postgres_password}"
            f"@127.0.0.1:{self.postgres_port}/{self.postgres_db}"
        )

    def _url(self, driver: str) -> str:
        return (
            f"postgresql+{driver}://{self.postgres_user}:{self.postgres_password}"
            f"@127.0.0.1:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_config() -> Config:
    return Config()
