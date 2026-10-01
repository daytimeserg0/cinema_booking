from datetime import timedelta
from io import BytesIO

import pytest

from conftest import PASSWORD, post_form, sign_in


def test_register_login_and_post_only_logout(client, query):
    response = post_form(client, "/register", {"username": "new_viewer", "password": PASSWORD, "confirm_password": PASSWORD})
    assert response.status_code == 302
    stored = query("SELECT password,role FROM users WHERE username='new_viewer'")[0]
    assert stored[0] != PASSWORD and stored[1] == "user"
    assert client.get("/logout").status_code == 405
    assert post_form(client, "/logout").status_code == 302
    assert post_form(client, "/login", {"username": "new_viewer", "password": PASSWORD}).status_code == 302


@pytest.mark.parametrize("username,password,confirmation", [("a", PASSWORD, PASSWORD), ("bad name", PASSWORD, PASSWORD), ("viewer", "123", "123"), ("viewer", PASSWORD, "different")])
def test_registration_validation(client, query, username, password, confirmation):
    post_form(client, "/register", {"username": username, "password": password, "confirm_password": confirmation})
    assert query("SELECT count(*) FROM users")[0][0] == 0


def test_username_is_unique_ignoring_case(client, catalog, query):
    post_form(client, "/register", {"username": "VIEWER", "password": PASSWORD, "confirm_password": PASSWORD})
    assert query("SELECT count(*) FROM users WHERE lower(username)='viewer'")[0][0] == 1


def test_existing_credentials_still_work_after_upgrade(client, query):
    from werkzeug.security import generate_password_hash

    query("INSERT INTO users(username,password,role) VALUES (%s,%s,'user')", ("old.viewer", generate_password_hash("1234")))
    response = post_form(client, "/login", {"username": "old.viewer", "password": "1234"})
    assert response.status_code == 302
    assert client.get("/account").status_code == 200


def test_csrf_and_admin_authorization(client, catalog, query):
    assert client.post("/login", data={"username": "viewer", "password": PASSWORD}).status_code == 400
    sign_in(client)
    assert client.get("/admin").status_code == 403
    assert post_form(client, "/admin", {"action": "add_hall", "name": "Forbidden", "rows": 8, "seats_per_row": 10}).status_code == 403
    assert query("SELECT count(*) FROM halls")[0][0] == 1


def test_non_ascii_csrf_is_rejected_without_server_error(client):
    from conftest import token_for

    token_for(client)
    response = client.post("/login", data={"csrf_token": "неверный-токен", "username": "viewer", "password": PASSWORD})
    assert response.status_code == 400


def test_login_cannot_redirect_to_external_site(client, catalog):
    response = post_form(client, "/login", {"username": "viewer", "password": PASSWORD, "next": "https://example.com"})
    assert response.status_code == 302 and response.location.startswith("/") and not response.location.startswith("//")


def test_role_is_checked_from_database(client, catalog, query):
    sign_in(client, "manager")
    assert client.get("/admin").status_code == 200
    query("UPDATE users SET role='user' WHERE username='manager'")
    assert client.get("/admin").status_code == 403


def test_schedule_conflict_and_decimal_price(client, catalog, query):
    sign_in(client, "manager")
    fields = {"action": "add_session", "movie_id": catalog["movie"], "hall_id": catalog["hall"], "datetime": (catalog["start"] + timedelta(minutes=30)).isoformat(timespec="minutes"), "price": "500.50"}
    post_form(client, "/admin", fields)
    assert query("SELECT count(*) FROM sessions")[0][0] == 1
    fields["datetime"] = (catalog["start"] + timedelta(hours=3)).isoformat(timespec="minutes")
    post_form(client, "/admin", fields)
    assert query("SELECT count(*) FROM sessions")[0][0] == 2
    assert str(query("SELECT price FROM sessions ORDER BY id DESC LIMIT 1")[0][0]) == "500.50"


@pytest.mark.parametrize("price", ["-1", "0", "10000", "NaN", "Infinity", "abc"])
def test_invalid_prices_are_rejected(client, catalog, query, price):
    sign_in(client, "manager")
    post_form(client, "/admin", {"action": "add_session", "movie_id": catalog["movie"], "hall_id": catalog["hall"], "datetime": (catalog["start"] + timedelta(hours=3)).isoformat(timespec="minutes"), "price": price})
    assert query("SELECT count(*) FROM sessions")[0][0] == 1


def test_session_cancel_preserves_ticket_history(app, client, catalog, query):
    sign_in(client)
    booking = post_form(client, f"/buy_ticket/{catalog['session']}", {"selected_seats": "1-1"}).location
    admin = app.test_client()
    sign_in(admin, "manager")
    post_form(admin, "/admin", {"action": "delete_session", "session_id": catalog["session"]})
    assert query("SELECT is_cancelled FROM sessions")[0][0] is True
    assert query("SELECT status FROM bookings")[0][0] == "cancelled"
    assert client.get(booking).status_code == 200


def test_deleting_used_movie_or_hall_does_not_remove_schedule(client, catalog, query):
    sign_in(client, "manager")
    post_form(client, "/admin", {"action": "delete_movie", "movie_id": catalog["movie"]})
    post_form(client, "/admin", {"action": "delete_hall", "hall_id": catalog["hall"]})
    assert query("SELECT count(*) FROM movies")[0][0] == 1
    assert query("SELECT count(*) FROM halls")[0][0] == 1
    assert query("SELECT count(*) FROM sessions")[0][0] == 1


def test_invalid_poster_does_not_create_movie(client, catalog, query):
    sign_in(client, "manager")
    post_form(client, "/admin", {"action": "add_movie", "title": "New", "genre": "Драма", "description": "Test description", "duration": "100", "release_year": "2026", "age_rating": "12", "poster": (BytesIO(b"<script>alert(1)</script>"), "poster.jpg")}, content_type="multipart/form-data")
    assert query("SELECT count(*) FROM movies")[0][0] == 1


def test_public_pages_and_not_found(client, catalog):
    for path in ("/", "/?q=Тестовый", "/?genre=Фантастика", f"/movie/{catalog['movie']}", "/login", "/register", "/health"):
        assert client.get(path).status_code == 200, path
    assert client.get("/movie/999999").status_code == 404
    assert client.get("/missing-page").status_code == 404


def test_booked_session_cannot_be_edited(app, client, catalog, query):
    sign_in(client)
    post_form(client, f"/buy_ticket/{catalog['session']}", {"selected_seats": "2-3"})
    admin = app.test_client()
    sign_in(admin, "manager")
    post_form(admin, "/admin", {
        "action": "edit_session", "session_id": catalog["session"],
        "movie_id": catalog["movie"], "hall_id": catalog["hall"],
        "datetime": (catalog["start"] + timedelta(hours=4)).isoformat(timespec="minutes"),
        "price": "900.00",
    })
    saved = query("SELECT datetime,price FROM sessions WHERE id=%s", (catalog["session"],))[0]
    assert saved[0] == catalog["start"] and str(saved[1]) == "650.50"


def test_poster_is_reencoded_with_generated_filename(app, client, catalog, query):
    from pathlib import Path
    from PIL import Image

    sign_in(client, "manager")
    upload = BytesIO()
    Image.new("RGB", (20, 30), (30, 40, 50)).save(upload, format="PNG")
    upload.seek(0)
    post_form(client, "/admin", {
        "action": "add_movie", "title": "Новый фильм", "genre": "Драма", "description": "Новый сюжет",
        "duration": "100", "release_year": "2026", "age_rating": "12",
        "poster": (upload, "../../unsafe.png"),
    }, content_type="multipart/form-data")
    result = query("SELECT poster FROM movies WHERE title='Новый фильм'")
    assert len(result) == 1
    name = result[0][0]
    assert name.startswith("uploads/") and ".." not in name and "unsafe" not in name
    uploaded = Path(app.config["UPLOAD_FOLDER"]) / Path(name).name
    with Image.open(uploaded) as image:
        assert image.format in ("JPEG", "WEBP")
