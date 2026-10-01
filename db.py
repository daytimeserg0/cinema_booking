import os
from contextlib import contextmanager
from pathlib import Path

import psycopg2
from dotenv import load_dotenv
from flask import current_app, has_app_context
from psycopg2.extras import RealDictCursor

load_dotenv(Path(__file__).resolve().parent / ".env")


def get_db_connection():
    config = current_app.config if has_app_context() else {}
    defaults = {
        "DB_NAME": "cinema_db",
        "DB_USER": "cinema_app",
        "DB_PASSWORD": "",
        "DB_HOST": "127.0.0.1",
        "DB_PORT": "5433",
    }
    values = {key: config.get(key, os.environ.get(key, default)) for key, default in defaults.items()}
    return psycopg2.connect(
        dbname=values["DB_NAME"], user=values["DB_USER"], password=values["DB_PASSWORD"],
        host=values["DB_HOST"], port=values["DB_PORT"], connect_timeout=5,
        options="-c statement_timeout=15000 -c lock_timeout=5000",
    )


@contextmanager
def transaction():
    connection = get_db_connection()
    try:
        with connection:
            with connection.cursor(cursor_factory=RealDictCursor) as cursor:
                yield cursor
    finally:
        connection.close()
