"""ASGI entry point: ``uvicorn app.asgi:app``.

Tables are created automatically only for SQLite (local development). For
PostgreSQL run ``alembic upgrade head`` before starting."""

from app.config import get_settings
from app.main import create_app

app = create_app(create_tables=get_settings().database_url.startswith("sqlite"))
