from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

from conftest import post_form, quote_for, sign_in, token_for


def test_concurrent_films_cannot_use_the_same_hall(app, catalog, query):
    second_movie = query(
        "INSERT INTO movies(title,genre,description,duration,poster,release_year,age_rating) "
        "VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
        ("Другой фильм", "Драма", "Описание другого фильма.", 90, "posters/default.svg", 2026, 12),
    )[0][0]
    movie_ids = (catalog["movie"], second_movie)
    clients = [app.test_client(), app.test_client()]
    for client in clients:
        assert sign_in(client, "manager").status_code == 302
    tokens = [token_for(client) for client in clients]
    start = catalog["start"] + timedelta(hours=3)
    ready = Barrier(2)

    def schedule(index):
        ready.wait(timeout=10)
        return clients[index].post("/admin", data={
            "csrf_token": tokens[index],
            "action": "add_session",
            "movie_id": movie_ids[index],
            "hall_id": catalog["hall"],
            "datetime": start.isoformat(timespec="minutes"),
            "price": "450.50",
        }).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(schedule, range(2)))

    assert responses == [302, 302]
    scheduled = query(
        "SELECT movie_id FROM sessions WHERE hall_id=%s AND datetime=%s AND NOT is_cancelled",
        (catalog["hall"], start),
    )
    assert len(scheduled) == 1 and scheduled[0][0] in movie_ids
    assert query("SELECT count(*) FROM sessions")[0][0] == 2
    categories = []
    for client in clients:
        with client.session_transaction() as saved:
            categories.extend(category for category, _ in saved.get("_flashes", []))
    assert sorted(categories) == ["error", "success"]


def test_session_cancellation_racing_purchase_leaves_no_active_ticket(app, catalog, query):
    admin = app.test_client()
    viewer = app.test_client()
    assert sign_in(admin, "manager").status_code == 302
    assert sign_in(viewer).status_code == 302
    admin_token = token_for(admin)
    viewer_token = token_for(viewer)
    viewer_quote = quote_for(viewer, catalog["session"])
    ready = Barrier(2)

    def cancel_session():
        ready.wait(timeout=10)
        return admin.post("/admin", data={
            "csrf_token": admin_token,
            "action": "delete_session",
            "session_id": catalog["session"],
        })

    def purchase():
        ready.wait(timeout=10)
        return viewer.post(f"/buy_ticket/{catalog['session']}", data={
            "csrf_token": viewer_token,
            "session_quote": viewer_quote,
            "selected_seats": "4-6",
        })

    with ThreadPoolExecutor(max_workers=2) as pool:
        cancellation = pool.submit(cancel_session)
        booking = pool.submit(purchase)
        cancel_response = cancellation.result(timeout=20)
        booking_response = booking.result(timeout=20)

    assert cancel_response.status_code == 302
    assert booking_response.status_code in (302, 409)
    assert query("SELECT is_cancelled FROM sessions WHERE id=%s", (catalog["session"],)) == [(True,)]
    assert query("SELECT count(*) FROM bookings WHERE status='active'")[0][0] == 0
    tickets = query("SELECT status FROM bookings")
    if booking_response.status_code == 302:
        assert tickets == [("cancelled",)]
        assert viewer.get(booking_response.location).status_code == 200
    else:
        assert tickets == []


def test_inactive_movie_cannot_be_booked_by_direct_url(client, catalog, query):
    assert sign_in(client).status_code == 302
    quote = quote_for(client, catalog["session"])
    query("UPDATE movies SET is_active=false WHERE id=%s", (catalog["movie"],))
    path = f"/buy_ticket/{catalog['session']}"
    assert client.get(path).status_code == 409
    assert post_form(client, path, {"selected_seats": "1-1", "session_quote": quote}).status_code == 409
    assert query("SELECT count(*) FROM bookings")[0][0] == 0


def test_changed_price_requires_a_fresh_booking_confirmation(client, catalog, query):
    assert sign_in(client).status_code == 302
    quote = quote_for(client, catalog["session"])
    query("UPDATE sessions SET price=900.75 WHERE id=%s", (catalog["session"],))
    path = f"/buy_ticket/{catalog['session']}"
    response = post_form(client, path, {"selected_seats": "1-1", "session_quote": quote})
    assert response.status_code == 409
    assert query("SELECT count(*) FROM bookings")[0][0] == 0

    assert post_form(client, path, {"selected_seats": "1-1"}).status_code == 302
    assert str(query("SELECT price FROM bookings")[0][0]) == "900.75"


def test_ongoing_movie_duration_cannot_change(client, catalog, query):
    from app import cinema_now

    assert sign_in(client, "manager").status_code == 302
    query("UPDATE sessions SET datetime=%s WHERE id=%s", (cinema_now() - timedelta(minutes=30), catalog["session"]))
    post_form(client, "/admin", {
        "action": "edit_movie", "movie_id": catalog["movie"],
        "title": "Тестовый фильм", "genre": "Фантастика",
        "description": "Описание тестового фильма.", "duration": "10",
        "release_year": "2026", "age_rating": "12",
    })
    assert query("SELECT duration FROM movies WHERE id=%s", (catalog["movie"],)) == [(120,)]
