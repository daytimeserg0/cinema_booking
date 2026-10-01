from contextlib import closing

import psycopg2
import pytest

from db import get_db_connection
from migrate import run_migrations


def test_migrations_are_repeatable_and_preserve_data(app, catalog, query):
    before = query("SELECT id,title FROM movies")
    with app.app_context():
        assert run_migrations() == []
        assert run_migrations() == []
    assert query("SELECT id,title FROM movies") == before


def test_database_prevents_duplicate_active_seat(app, catalog, query):
    fields = (catalog["users"]["viewer"], catalog["session"])
    query("INSERT INTO bookings(user_id,session_id,seat_row,seat_number,reference,price) VALUES (%s,%s,1,1,'FIRST001',650.50)", fields)
    with app.app_context(), closing(get_db_connection()) as conn:
        with pytest.raises(psycopg2.IntegrityError):
            with conn, conn.cursor() as cur:
                cur.execute("INSERT INTO bookings(user_id,session_id,seat_row,seat_number,reference,price) VALUES (%s,%s,1,1,'SECOND01',650.50)", fields)
    assert query("SELECT count(*) FROM bookings")[0][0] == 1


def test_cancelled_seat_can_be_booked_again(query, catalog):
    fields = (catalog["users"]["viewer"], catalog["session"])
    query("INSERT INTO bookings(user_id,session_id,seat_row,seat_number,reference,price,status) VALUES (%s,%s,1,1,'FIRST001',650.50,'cancelled')", fields)
    query("INSERT INTO bookings(user_id,session_id,seat_row,seat_number,reference,price) VALUES (%s,%s,1,1,'SECOND01',650.50)", fields)
    assert query("SELECT count(*) FROM bookings")[0][0] == 2
