import os
from contextlib import closing
from datetime import timedelta
from html.parser import HTMLParser

import pytest
from werkzeug.security import generate_password_hash

from app import create_app
from db import get_db_connection
from migrate import run_migrations

PASSWORD = "Cinema-test-password-42"
PASSWORD_HASH = generate_password_hash(PASSWORD, method="pbkdf2:sha256:10000")


class TokenParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.fields = {}

    def handle_starttag(self, tag, attrs):
        fields = dict(attrs)
        if tag == "input" and fields.get("name"):
            self.fields[fields["name"]] = fields.get("value")


def token_for(client):
    response = client.get("/login", follow_redirects=True)
    parser = TokenParser()
    parser.feed(response.get_data(as_text=True))
    assert parser.fields.get("csrf_token"), "No CSRF token in rendered page"
    return parser.fields["csrf_token"]


def quote_for(client, session_id):
    response = client.get(f"/buy_ticket/{session_id}")
    parser = TokenParser()
    parser.feed(response.get_data(as_text=True))
    return parser.fields.get("session_quote", "")


def post_form(client, path, data=None, **kwargs):
    fields = dict(data or {})
    fields["csrf_token"] = token_for(client)
    if path.startswith("/buy_ticket/") and "session_quote" not in fields:
        fields["session_quote"] = quote_for(client, path.rsplit("/", 1)[-1])
    return client.post(path, data=fields, **kwargs)


def sign_in(client, username="viewer"):
    return post_form(client, "/login", {"username": username, "password": PASSWORD})


@pytest.fixture(scope="session")
def app(tmp_path_factory):
    database = os.environ.get("TEST_DB_NAME", "cinema_booking_test")
    if not database.endswith("_test"):
        raise RuntimeError("TEST_DB_NAME must end in _test; tests reset their dedicated database.")
    instance = create_app({
        "TESTING": True,
        "SECRET_KEY": "isolated-test-secret",
        "DB_NAME": database,
        "UPLOAD_FOLDER": str(tmp_path_factory.mktemp("posters")),
    })
    with instance.app_context():
        run_migrations()
    yield instance


@pytest.fixture(autouse=True)
def clean_database(app):
    with app.app_context(), closing(get_db_connection()) as conn:
        with conn, conn.cursor() as cur:
            cur.execute("TRUNCATE bookings, sessions, movies, halls, users RESTART IDENTITY CASCADE")
    yield


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def query(app):
    def execute(statement, parameters=()):
        with app.app_context(), closing(get_db_connection()) as conn:
            with conn, conn.cursor() as cur:
                cur.execute(statement, parameters)
                return cur.fetchall() if cur.description else []
    return execute


@pytest.fixture
def catalog(query):
    from app import cinema_now

    start = (cinema_now() + timedelta(days=2)).replace(hour=18, minute=0, second=0, microsecond=0)
    users = {}
    for username, role in (("viewer", "user"), ("another", "user"), ("manager", "admin")):
        users[username] = query(
            "INSERT INTO users(username,password,role) VALUES (%s,%s,%s) RETURNING id",
            (username, PASSWORD_HASH, role),
        )[0][0]
    movie = query(
        "INSERT INTO movies(title,genre,description,duration,poster,release_year,age_rating) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        ("Тестовый фильм", "Фантастика", "Описание тестового фильма.", 120, "posters/default.svg", 2026, 12),
    )[0][0]
    hall = query("INSERT INTO halls(name,rows,seats_per_row) VALUES ('Тестовый зал',8,12) RETURNING id")[0][0]
    session = query(
        "INSERT INTO sessions(movie_id,hall_id,datetime,price) VALUES (%s,%s,%s,650.50) RETURNING id",
        (movie, hall, start),
    )[0][0]
    return {"users": users, "movie": movie, "hall": hall, "session": session, "start": start}
