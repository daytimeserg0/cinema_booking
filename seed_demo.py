import argparse
from contextlib import closing
from datetime import datetime, time, timedelta
from decimal import Decimal
import os
import sys
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import psycopg2

from db import get_db_connection
from migrate import run_migrations

MOVIES = (
    {
        "title": "За пределами орбиты", "genre": "Фантастика", "duration": 142,
        "age_rating": 12, "poster": "posters/orbit.svg",
        "description": "Последний сигнал с заброшенной станции зовёт команду исследователей туда, где время течёт иначе. На краю изученной вселенной им предстоит найти дорогу домой.",
    },
    {
        "title": "Город после дождя", "genre": "Драма", "duration": 118,
        "age_rating": 16, "poster": "posters/city.svg",
        "description": "В ночном городе случайная встреча фотографа и музыканта превращается в путешествие по местам, которые каждый из них давно хотел забыть.",
    },
    {
        "title": "Тихий север", "genre": "Приключения", "duration": 126,
        "age_rating": 12, "poster": "posters/north.svg",
        "description": "Молодая проводница и её брат отправляются через северные горы к маяку, чтобы исполнить обещание и заново научиться слышать друг друга.",
    },
    {
        "title": "То самое лето", "genre": "Комедия", "duration": 104,
        "age_rating": 12, "poster": "posters/summer.svg",
        "description": "Четверо друзей возвращаются в приморский город детства. Один потерянный чемодан меняет их планы и дарит лето, которого так не хватало.",
    },
    {
        "title": "Нулевой сигнал", "genre": "Триллер", "duration": 112,
        "age_rating": 16, "poster": "posters/signal.svg",
        "description": "Радиоведущая получает звонок из завтрашнего дня. Чтобы предотвратить исчезновение незнакомца, ей нужно распутать историю собственного прошлого.",
    },
    {
        "title": "Лес чудес", "genre": "Анимация", "duration": 92,
        "age_rating": 6, "poster": "posters/forest.svg",
        "description": "Любопытная лисица и маленький хранитель звёзд отправляются искать пропавшую луну, пока волшебный лес не погрузился в вечный сон.",
    },
)
HALLS = (("Атмосфера", 8, 12), ("Горизонт", 6, 10))
SEED_LOCK = 724981532


def seed_movies(cur):
    movie_ids = []
    added = 0
    for movie in MOVIES:
        cur.execute("SELECT id, is_active, duration FROM movies WHERE poster = %s FOR UPDATE", (movie["poster"],))
        existing = cur.fetchall()
        if len(existing) > 1:
            raise RuntimeError("Several movies use a demo poster. Resolve the duplicate before running the demo seed again.")
        values = (
            movie["title"], movie["description"], movie["duration"], movie["poster"],
            movie["genre"], 2026, movie["age_rating"],
        )
        if existing:
            movie_id, active, duration = existing[0]
        else:
            cur.execute(
                """INSERT INTO movies (title, description, duration, poster, genre, release_year, age_rating)
                    VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id""", values,
            )
            movie_id = cur.fetchone()[0]
            active = True
            duration = movie["duration"]
            added += 1
        if active:
            movie_ids.append((movie_id, duration))
    return movie_ids, added


def seed_schedule(cur, movies, now):
    if not movies:
        return 0, 0
    halls_added = 0
    sessions_added = 0
    for hall_index, (name, rows, seats) in enumerate(HALLS):
        cur.execute("SELECT id FROM halls WHERE lower(name) = lower(%s) ORDER BY id FOR UPDATE", (name,))
        matches = cur.fetchall()
        if len(matches) > 1:
            raise RuntimeError("Several halls share a demo hall name. Resolve the duplicate before refreshing the schedule.")
        if matches:
            hall_id = matches[0][0]
        else:
            cur.execute(
                "INSERT INTO halls (name, rows, seats_per_row) VALUES (%s, %s, %s) RETURNING id",
                (name, rows, seats),
            )
            hall_id = cur.fetchone()[0]
            halls_added += 1
        for day_index in range(7):
            day = now.date() + timedelta(days=day_index)
            for slot_index, hour in enumerate((11, 14, 17, 20)):
                starts = datetime.combine(day, time(hour, 30 if hall_index else 0))
                if starts <= now + timedelta(minutes=15):
                    continue
                movie_id, duration = movies[(day.toordinal() + slot_index + hall_index) % len(movies)]
                cur.execute(
                    """SELECT 1 FROM sessions s JOIN movies m ON m.id = s.movie_id
                        WHERE s.hall_id = %s AND (
                            s.datetime = %s OR (
                                NOT s.is_cancelled AND s.datetime < %s
                                AND s.datetime + (m.duration + 15) * interval '1 minute' > %s
                            )
                        ) LIMIT 1""",
                    (hall_id, starts, starts + timedelta(minutes=duration + 15), starts),
                )
                if cur.fetchone():
                    continue
                price = Decimal(390 if hour < 17 else 490) + Decimal(60 * hall_index)
                cur.execute(
                    "INSERT INTO sessions (movie_id, hall_id, datetime, price) VALUES (%s, %s, %s, %s)",
                    (movie_id, hall_id, starts, price),
                )
                sessions_added += 1
    return halls_added, sessions_added


def seed_demo(refresh_schedule=False):
    timezone = ZoneInfo(os.environ.get("CINEMA_TIMEZONE", "Europe/Moscow"))
    now = datetime.now(timezone).replace(tzinfo=None)
    run_migrations()
    with closing(get_db_connection()) as conn:
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(%s)", (SEED_LOCK,))
                movies, movies_added = seed_movies(cur)
                halls_added, sessions_added = seed_schedule(cur, movies, now) if refresh_schedule else (0, 0)
    return {"movies_added": movies_added, "halls_added": halls_added, "sessions_added": sessions_added}


def main():
    parser = argparse.ArgumentParser(description="Add the fictional Cinema Booking demo catalog.")
    parser.add_argument("--refresh-schedule", action="store_true", help="Fill available demo hall slots for the next seven days without replacing existing sessions.")
    args = parser.parse_args()
    try:
        result = seed_demo(refresh_schedule=args.refresh_schedule)
    except (psycopg2.Error, RuntimeError, ZoneInfoNotFoundError) as exc:
        print(f"Demo setup failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"Demo ready. Movies added: {result['movies_added']}; "
        f"halls added: {result['halls_added']}; sessions added: {result['sessions_added']}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
