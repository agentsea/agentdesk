import os

from sqlalchemy import create_engine, Engine
from sqlalchemy.orm import sessionmaker

from .models import Base


DB_TYPE = os.environ.get("DB_TYPE", "sqlite")


def get_pg_conn() -> Engine:
    # Env Vars
    db_user = os.environ.get("DB_USER")
    if not db_user:
        raise ValueError("$DB_USER must be set to a valid postgres db user")

    db_password = os.environ.get("DB_PASS")
    if not db_password:
        raise ValueError("$DB_PASS must be set to a valid postgres db user password")

    db_host = os.environ.get("DB_HOST")
    if not db_user:
        raise ValueError("$DB_HOST must be set to a running postgres server")

    db_name = os.environ.get("DB_NAME")
    if not db_name:
        raise ValueError("$DB_NAME must be set to a postgres db name")

    engine = create_engine(
        f"postgresql+psycopg2://{db_user}:{db_password}@{db_host}/{db_name}",
        client_encoding="utf8",
    )

    return engine


def get_sqlite_conn() -> Engine:
    os.makedirs(os.path.dirname("./data/agentdesk.db"), exist_ok=True)
    engine = create_engine("sqlite:///./data/agentdesk.db")
    return engine


if DB_TYPE == "postgres":
    engine = get_pg_conn()
else:
    engine = get_sqlite_conn()
SessionLocal = sessionmaker(bind=engine)

Base.metadata.create_all(bind=engine)


class WithDB:
    @staticmethod
    def get_db():
        """Get a database connection

        Example:
            ```
            for session in self.get_db():
                session.add(foo)
            ```
        """
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()


def get_db():
    """Get a database connection

    Example:
        ```
        for session in get_db():
            session.add(foo)
        ```
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
