from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest

from conftest import post_form, quote_for, sign_in, token_for


def purchase(client, catalog, seats="1-1,1-2"):
    return post_form(client, f"/buy_ticket/{catalog['session']}", {"selected_seats": seats})


def test_booking_requires_authentication(client, catalog, query):
    response = purchase(client, catalog)
    assert response.status_code == 302 and "/login" in response.location
    assert query("SELECT count(*) FROM bookings")[0][0] == 0


def test_booking_confirmation_and_cancellation(client, catalog, query):
    sign_in(client)
    response = purchase(client, catalog)
    assert response.status_code == 302 and "/booking/" in response.location
    reference = response.location.rsplit("/", 1)[-1]
    rows = query("SELECT reference,status,price FROM bookings ORDER BY seat_number")
    assert len(rows) == 2 and all(row[0] == reference and row[1] == "active" for row in rows)
    assert str(rows[0][2]) == "650.50"
    assert client.get(response.location).status_code == 200
    assert client.get("/account").status_code == 200
    result = post_form(client, f"/booking/{reference}/cancel")
    assert result.status_code == 302
    assert query("SELECT DISTINCT status FROM bookings") == [("cancelled",)]
    assert purchase(client, catalog).status_code == 302
    assert query("SELECT count(*) FROM bookings WHERE status='active'")[0][0] == 2


@pytest.mark.parametrize("seats", ["", "0-1", "1-0", "9-1", "1-13", "-1-1", "abc", "1-1,1-1", "1-1,", ",".join(f"1-{n}" for n in range(1, 10))])
def test_invalid_seats_never_create_bookings(client, catalog, query, seats):
    sign_in(client)
    purchase(client, catalog, seats)
    assert query("SELECT count(*) FROM bookings")[0][0] == 0


def test_conflict_rolls_back_entire_selection(app, client, catalog, query):
    sign_in(client)
    purchase(client, catalog, "1-1")
    other = app.test_client()
    sign_in(other, "another")
    purchase(other, catalog, "1-2,1-1")
    assert query("SELECT seat_row,seat_number FROM bookings WHERE status='active'") == [(1, 1)]


def test_concurrent_buyers_cannot_claim_same_seat(app, catalog, query):
    clients = [app.test_client(), app.test_client()]
    for client, username in zip(clients, ("viewer", "another")):
        sign_in(client, username)
    tokens = [token_for(client) for client in clients]
    quotes = [quote_for(client, catalog["session"]) for client in clients]
    ready = Barrier(2)

    def attempt(index):
        ready.wait(timeout=10)
        return clients[index].post(f"/buy_ticket/{catalog['session']}", data={
            "csrf_token": tokens[index], "selected_seats": "3-5", "session_quote": quotes[index],
        }).status_code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert results.count(302) == 1
    assert query("SELECT count(*) FROM bookings WHERE status='active'")[0][0] == 1


def test_ticket_owner_is_enforced(app, client, catalog, query):
    sign_in(client)
    location = purchase(client, catalog).location
    other = app.test_client()
    sign_in(other, "another")
    assert other.get(location).status_code in (403, 404)
    assert post_form(other, location + "/cancel").status_code in (403, 404)
    assert query("SELECT count(*) FROM bookings WHERE status='active'")[0][0] == 2


def test_started_session_rejects_purchase_and_cancellation(client, catalog, query):
    from app import cinema_now

    sign_in(client)
    location = purchase(client, catalog, "1-1").location
    query("UPDATE sessions SET datetime=%s WHERE id=%s", (cinema_now() - timedelta(minutes=1), catalog["session"]))
    purchase(client, catalog, "1-2")
    post_form(client, location + "/cancel")
    assert query("SELECT count(*) FROM bookings WHERE status='active'")[0][0] == 1


def test_cancelled_session_is_unavailable(client, catalog, query):
    sign_in(client)
    query("UPDATE sessions SET is_cancelled=true WHERE id=%s", (catalog["session"],))
    purchase(client, catalog)
    assert query("SELECT count(*) FROM bookings")[0][0] == 0
