import hmac
import os
import secrets
import threading
import time
from collections import OrderedDict, deque
from datetime import timedelta
from decimal import Decimal
from functools import wraps
from pathlib import Path

import psycopg2
from flask import Flask, abort, flash, g, jsonify, redirect, render_template, request, session, url_for
from werkzeug.exceptions import HTTPException
from werkzeug.security import check_password_hash, generate_password_hash

from db import transaction
from services import (
    ValidationError, cinema_now, create_session_quote, csrf_token, date_label, field, identifier, integer,
    money, parse_date, parse_datetime, parse_price, parse_seats, poster_url,
    remove_upload, safe_next, save_poster, valid_username, validate_session_quote,
)


SESSION_SELECT = """
    SELECT s.*, m.title, m.genre, m.duration, m.poster, m.age_rating, m.is_active,
           h.name AS hall_name, h.rows, h.seats_per_row
    FROM sessions s
    JOIN movies m ON m.id = s.movie_id
    JOIN halls h ON h.id = s.hall_id
"""

TICKET_SELECT = """
    SELECT b.*, s.datetime, s.is_cancelled, m.title, m.poster, m.duration,
           h.name AS hall_name
    FROM bookings b
    JOIN sessions s ON s.id = b.session_id
    JOIN movies m ON m.id = s.movie_id
    JOIN halls h ON h.id = s.hall_id
"""


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if g.user is None:
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if g.user["role"] != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def ticket_groups(rows):
    grouped = OrderedDict()
    now = cinema_now()
    for row in rows:
        key = row["reference"]
        if key not in grouped:
            ticket = dict(row)
            ticket.update(seats=[], total=Decimal("0.00"))
            ticket["session_cancelled"] = row["is_cancelled"]
            ticket["is_cancelled"] = row["is_cancelled"] or row["status"] == "cancelled"
            ticket["can_cancel"] = row["status"] == "active" and not row["is_cancelled"] and row["datetime"] > now
            grouped[key] = ticket
        ticket = grouped[key]
        ticket["seats"].append({"row": row["seat_row"], "number": row["seat_number"]})
        ticket["total"] += row["price"]
        if row["status"] == "cancelled":
            ticket["status"] = "cancelled"
            ticket["is_cancelled"] = True
            ticket["can_cancel"] = False
    return list(grouped.values())


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_mapping(
        SECRET_KEY=os.environ.get("FLASK_SECRET_KEY"),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("SESSION_COOKIE_SECURE", "false").lower() in {"1", "true", "yes"},
        MAX_CONTENT_LENGTH=5 * 1024 * 1024,
        LOGIN_RATE_LIMIT=10,
        CINEMA_TIMEZONE=os.environ.get("CINEMA_TIMEZONE", "Europe/Moscow"),
        UPLOAD_FOLDER=str(Path(app.root_path) / "static" / "uploads"),
    )
    if test_config:
        app.config.update(test_config)
    if not app.secret_key:
        raise RuntimeError("FLASK_SECRET_KEY is required. Set it in .env before starting the application.")
    attempts = {}
    attempts_lock = threading.Lock()
    dummy_password = generate_password_hash(secrets.token_urlsafe(32))

    app.jinja_env.globals.update(csrf_token=csrf_token, poster_url=poster_url, cinema_now=cinema_now)
    date_filter = lambda value: date_label(value) if value else ""
    time_filter = lambda value: value.strftime("%H:%M") if value else ""
    app.jinja_env.filters.update(
        money=money, datefmt=date_filter, timefmt=time_filter,
        datetimefmt=lambda value: f"{date_label(value)}, {value:%H:%M}" if value else "",
        date=date_filter, time=time_filter, cinema_date=date_filter, cinema_time=time_filter,
    )

    @app.context_processor
    def user_context():
        return {"current_user": g.get("user")}

    @app.before_request
    def prepare_request():
        g.user = None
        if request.endpoint in {"static", "health"}:
            return
        if request.method == "POST":
            expected = session.get("csrf_token", "")
            supplied = request.form.get("csrf_token", "")
            if not expected or not supplied or not hmac.compare_digest(expected.encode(), supplied.encode()):
                abort(400, description="Срок действия формы истёк. Обновите страницу и повторите действие.")
        user_id = session.get("user_id")
        if isinstance(user_id, int) and not isinstance(user_id, bool):
            with transaction() as cursor:
                cursor.execute("SELECT id, username, role FROM users WHERE id = %s", (user_id,))
                g.user = cursor.fetchone()
            if g.user:
                g.user["is_admin"] = g.user["role"] == "admin"
            else:
                session.clear()

    @app.after_request
    def response_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; object-src 'none'; base-uri 'self'; "
            "form-action 'self'; frame-ancestors 'none'"
        )
        if request.endpoint != "static":
            response.headers["Cache-Control"] = "no-store"
        return response

    def error_response(status, message=None):
        errors = {
            400: ("Проверьте запрос", "Не удалось обработать форму. Проверьте данные и попробуйте снова."),
            403: ("Доступ закрыт", "У вас нет доступа к этой странице."),
            404: ("Страница не найдена", "Возможно, ссылка устарела. Вернитесь к афише и выберите фильм."),
            405: ("Действие недоступно", "Для этого действия используйте форму на странице."),
            409: ("Данные изменились", "Обновите страницу и попробуйте снова."),
            413: ("Слишком большой файл", "Общий размер формы с постером не должен превышать 5 МБ."),
            429: ("Слишком много попыток", "Подождите минуту, прежде чем пробовать снова."),
            500: ("Не удалось открыть страницу", "Произошла ошибка. Попробуйте ещё раз немного позже."),
            503: ("Сервис временно недоступен", "Не удалось подключиться к базе данных. Попробуйте ещё раз немного позже."),
        }
        title, default = errors.get(status, errors[500])
        return render_template("error.html", status=status, title=title, message=message or default), status

    @app.errorhandler(HTTPException)
    def http_error(error):
        message = error.description if error.code in {400, 409} else None
        return error_response(error.code, message)

    @app.errorhandler(psycopg2.Error)
    def database_error(error):
        app.logger.exception("Database request failed")
        if isinstance(error, (psycopg2.errors.LockNotAvailable, psycopg2.errors.DeadlockDetected)):
            return error_response(409, "Данные сейчас изменяются. Повторите действие через несколько секунд.")
        return error_response(503)

    @app.errorhandler(Exception)
    def unexpected_error(error):
        app.logger.exception("Unhandled request failure")
        return error_response(500)

    @app.get("/")
    def home():
        query = request.args.get("q", "").strip()[:100]
        genre = request.args.get("genre", "").strip()[:80]
        try:
            selected = parse_date(request.args.get("date", ""))
        except ValidationError as error:
            abort(400, description=str(error))
        now = cinema_now()
        conditions = ["m.is_active", "NOT s.is_cancelled", "s.datetime > %s"]
        params = [now]
        if query:
            conditions.append("(m.title ILIKE %s OR m.description ILIKE %s)")
            params.extend([f"%{query}%", f"%{query}%"])
        if genre:
            conditions.append("m.genre = %s")
            params.append(genre)
        if selected:
            conditions.append("s.datetime::date = %s")
            params.append(selected)
        with transaction() as cursor:
            cursor.execute("""
                SELECT m.*, MIN(s.datetime) AS next_session, MIN(s.price) AS min_price,
                       COUNT(s.id) AS session_count
                FROM movies m JOIN sessions s ON s.movie_id = m.id
                WHERE """ + " AND ".join(conditions) + " GROUP BY m.id ORDER BY next_session, m.title", params)
            movies = cursor.fetchall()
            cursor.execute("""
                SELECT DISTINCT m.genre FROM movies m JOIN sessions s ON s.movie_id = m.id
                WHERE m.is_active AND NOT s.is_cancelled AND s.datetime > %s AND m.genre <> '' ORDER BY m.genre
            """, (now,))
            genres = [row["genre"] for row in cursor.fetchall()]
        weekdays = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")
        dates = []
        for offset in range(7):
            day = now.date() + timedelta(days=offset)
            label = "Сегодня" if offset == 0 else "Завтра" if offset == 1 else weekdays[day.weekday()]
            dates.append({"value": day.isoformat(), "label": label, "day": date_label(day)})
        return render_template("index.html", movies=movies, featured=movies[0] if movies else None,
                               genres=genres, selected_genre=genre, search_query=query,
                               selected_date=selected.isoformat() if selected else "", dates=dates)

    @app.get("/movie/<int:movie_id>")
    def movie_sessions(movie_id):
        try:
            selected = parse_date(request.args.get("date", ""))
        except ValidationError as error:
            abort(400, description=str(error))
        with transaction() as cursor:
            cursor.execute("SELECT * FROM movies WHERE id = %s AND is_active", (movie_id,))
            movie = cursor.fetchone()
            if not movie:
                abort(404)
            cursor.execute("""
                SELECT s.*, h.name AS hall_name, h.rows * h.seats_per_row AS total_seats,
                       h.rows * h.seats_per_row - COUNT(b.id) AS available_seats
                FROM sessions s JOIN halls h ON h.id = s.hall_id
                LEFT JOIN bookings b ON b.session_id = s.id AND b.status = 'active'
                WHERE s.movie_id = %s AND NOT s.is_cancelled AND s.datetime > %s
                GROUP BY s.id, h.id ORDER BY s.datetime
            """, (movie_id, cinema_now()))
            rows = cursor.fetchall()
        grouped = OrderedDict()
        for row in rows:
            day = row["datetime"].date()
            if selected and day != selected:
                continue
            key = day.isoformat()
            if key not in grouped:
                grouped[key] = {"date": key, "label": date_label(day), "sessions": []}
            grouped[key]["sessions"].append(row)
        return render_template("movie_sessions.html", movie=movie, sessions_by_date=list(grouped.values()),
                               selected_date=selected.isoformat() if selected else "")

    @app.route("/register", methods=["GET", "POST"])
    def register():
        if g.user:
            return redirect(url_for("home"))
        message = None
        form = {"username": request.form.get("username", "").strip()[:32]}
        if request.method == "POST":
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            if not valid_username(username):
                message = "Логин: от 3 до 32 букв, цифр, символов подчёркивания или дефисов."
            elif not 8 <= len(password) <= 128:
                message = "Пароль должен содержать от 8 до 128 символов."
            elif password != request.form.get("confirm_password", ""):
                message = "Пароли не совпадают."
            else:
                try:
                    with transaction() as cursor:
                        cursor.execute("""
                            INSERT INTO users (username, password, role) VALUES (%s, %s, 'user') RETURNING id
                        """, (username, generate_password_hash(password)))
                        user_id = cursor.fetchone()["id"]
                    session.clear()
                    session["user_id"] = user_id
                    flash("Аккаунт создан. Можно выбирать места!", "success")
                    return redirect(safe_next(request.form.get("next") or request.args.get("next")))
                except psycopg2.errors.UniqueViolation:
                    message = "Этот логин уже занят. Выберите другой."
        return render_template("register.html", message=message, form=form,
                               next=safe_next(request.form.get("next") or request.args.get("next")))

    @app.route("/login", methods=["GET", "POST"])
    def login():
        if g.user:
            return redirect(url_for("home"))
        message = None
        form = {"username": request.form.get("username", "").strip()[:32]}
        if request.method == "POST":
            now = time.monotonic()
            address = request.remote_addr or "unknown"
            with attempts_lock:
                for key in list(attempts):
                    if not attempts[key] or attempts[key][-1] <= now - 60:
                        del attempts[key]
                recent = attempts.setdefault(address, deque())
                while recent and recent[0] <= now - 60:
                    recent.popleft()
                if len(recent) >= app.config["LOGIN_RATE_LIMIT"]:
                    return error_response(429)
                recent.append(now)
            username = request.form.get("username", "").strip()
            password = request.form.get("password", "")
            with transaction() as cursor:
                cursor.execute("SELECT id, password FROM users WHERE lower(username) = lower(%s)", (username[:128],))
                user = cursor.fetchone()
            valid = check_password_hash(user["password"] if user else dummy_password, password[:129])
            if user and valid and 1 <= len(password) <= 128 and 1 <= len(username) <= 128:
                with attempts_lock:
                    attempts.pop(address, None)
                session.clear()
                session["user_id"] = user["id"]
                return redirect(safe_next(request.form.get("next") or request.args.get("next")))
            message = "Неверный логин или пароль."
        return render_template("login.html", message=message, form=form,
                               next=safe_next(request.form.get("next") or request.args.get("next")))

    @app.post("/logout")
    def logout():
        session.clear()
        return redirect(url_for("home"))

    @app.route("/buy_ticket/<int:session_id>", methods=["GET", "POST"])
    @login_required
    def buy_ticket(session_id):
        try:
            with transaction() as cursor:
                suffix = " FOR UPDATE OF s" if request.method == "POST" else ""
                cursor.execute(SESSION_SELECT + " WHERE s.id = %s" + suffix, (session_id,))
                selected_session = cursor.fetchone()
                if not selected_session:
                    abort(404)
                if not selected_session["is_active"] or selected_session["is_cancelled"] or selected_session["datetime"] <= cinema_now():
                    abort(409, description="Бронирование этого сеанса закрыто. Выберите другой сеанс в афише.")
                cursor.execute("SELECT seat_row, seat_number FROM bookings WHERE session_id = %s AND status = 'active'", (session_id,))
                taken = [[row["seat_row"], row["seat_number"]] for row in cursor.fetchall()]
                if request.method == "POST":
                    quote = request.form.get("session_quote", "")
                    if not quote:
                        abort(400, description="Откройте страницу выбора мест и подтвердите бронирование ещё раз.")
                    try:
                        validate_session_quote(quote, selected_session, g.user["id"])
                    except ValidationError as error:
                        abort(409, description=str(error))
                    try:
                        seats = parse_seats(request.form.get("selected_seats", ""), selected_session["rows"], selected_session["seats_per_row"])
                    except ValidationError as error:
                        abort(400, description=str(error))
                    if any(list(seat) in taken for seat in seats):
                        abort(409, description="Одно из выбранных мест уже занято. Вернитесь к схеме зала и выберите другие места.")
                    reference = secrets.token_hex(6).upper()
                    cursor.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (reference,))
                    cursor.execute("SELECT 1 FROM bookings WHERE reference = %s", (reference,))
                    if cursor.fetchone():
                        abort(409, description="Не удалось создать номер брони. Повторите попытку.")
                    booked_at = cinema_now()
                    cursor.executemany("""
                        INSERT INTO bookings (user_id, session_id, seat_row, seat_number, reference, status, price, booked_at)
                        VALUES (%s, %s, %s, %s, %s, 'active', %s, %s)
                    """, [(g.user["id"], session_id, row, number, reference, selected_session["price"], booked_at) for row, number in seats])
            if request.method == "POST":
                flash("Места забронированы. Сохраните номер брони для посещения кинотеатра.", "success")
                return redirect(url_for("booking_detail", reference=reference))
        except psycopg2.errors.UniqueViolation:
            abort(409, description="Выбранное место уже забронировали. Обновите схему и выберите другое.")
        return render_template("buy_ticket.html", selected_session=selected_session, taken=taken, max_seats=8,
                               session_quote=create_session_quote(selected_session, g.user["id"]))

    @app.get("/booking/<reference>")
    @login_required
    def booking_detail(reference):
        with transaction() as cursor:
            cursor.execute(TICKET_SELECT + " WHERE b.reference = %s ORDER BY b.seat_row, b.seat_number", (reference,))
            rows = cursor.fetchall()
        if not rows:
            abort(404)
        if rows[0]["user_id"] != g.user["id"] and g.user["role"] != "admin":
            abort(403)
        ticket = ticket_groups(rows)[0]
        ticket["can_cancel"] = ticket["can_cancel"] and ticket["user_id"] == g.user["id"]
        return render_template("booking.html", ticket=ticket)

    @app.get("/account")
    @login_required
    def account():
        with transaction() as cursor:
            cursor.execute(TICKET_SELECT + " WHERE b.user_id = %s ORDER BY s.datetime, b.reference, b.seat_row, b.seat_number", (g.user["id"],))
            tickets = ticket_groups(cursor.fetchall())
        upcoming = [ticket for ticket in tickets if ticket["can_cancel"]]
        history = sorted((ticket for ticket in tickets if not ticket["can_cancel"]), key=lambda ticket: ticket["datetime"], reverse=True)
        return render_template("account.html", upcoming=upcoming, history=history)

    @app.post("/booking/<reference>/cancel")
    @login_required
    def cancel_booking(reference):
        with transaction() as cursor:
            cursor.execute("SELECT user_id, session_id FROM bookings WHERE reference = %s LIMIT 1", (reference,))
            booking = cursor.fetchone()
            if not booking:
                abort(404)
            if booking["user_id"] != g.user["id"]:
                abort(403)
            cursor.execute("SELECT datetime FROM sessions WHERE id = %s FOR UPDATE", (booking["session_id"],))
            showing = cursor.fetchone()
            if showing["datetime"] <= cinema_now():
                abort(409, description="Отменить бронь можно только до начала сеанса.")
            cursor.execute("UPDATE bookings SET status = 'cancelled' WHERE reference = %s AND user_id = %s AND status = 'active'", (reference, g.user["id"]))
        flash("Бронь отменена. Места снова доступны для выбора.", "success")
        return redirect(url_for("booking_detail", reference=reference))

    @app.route("/admin", methods=["GET", "POST"])
    @admin_required
    def admin_panel():
        if request.method == "POST":
            new_poster = None
            old_poster = None
            try:
                action = request.form.get("action", "")
                if action not in {"add_movie", "edit_movie", "delete_movie", "add_hall", "edit_hall", "delete_hall", "add_session", "edit_session", "delete_session"}:
                    raise ValidationError("Неизвестное действие.")
                with transaction() as cursor:
                    if action.endswith("movie"):
                        new_poster, old_poster = manage_movie(cursor, action)
                    elif action.endswith("hall"):
                        manage_hall(cursor, action)
                    else:
                        manage_session(cursor, action)
                if old_poster and old_poster != new_poster:
                    remove_upload(old_poster)
                flash("Изменения сохранены." if action != "delete_session" else "Сеанс отменён. Все его брони перенесены в историю.", "success")
            except (ValidationError, psycopg2.errors.UniqueViolation, psycopg2.errors.ForeignKeyViolation) as error:
                if new_poster:
                    remove_upload(new_poster)
                message = str(error) if isinstance(error, ValidationError) else "Такая запись уже существует или используется в расписании. Обновите страницу и проверьте данные."
                flash(message, "error")
                if request.form.get("action", "").startswith("edit_"):
                    kind = request.form["action"].removeprefix("edit_")
                    value = request.form.get(f"{kind}_id", "")
                    if value.isdigit():
                        return redirect(url_for("admin_panel", **{f"edit_{kind}": value}))
            return redirect(url_for("admin_panel"))
        with transaction() as cursor:
            cursor.execute("SELECT * FROM movies ORDER BY id DESC")
            movies = cursor.fetchall()
            cursor.execute("SELECT * FROM halls ORDER BY id")
            halls = cursor.fetchall()
            cursor.execute("""
                SELECT s.*, m.title AS movie_title, h.name AS hall_name,
                       COUNT(b.id) FILTER (WHERE b.status = 'active') AS booked_seats,
                       COUNT(b.id) AS total_bookings
                FROM sessions s JOIN movies m ON m.id = s.movie_id JOIN halls h ON h.id = s.hall_id
                LEFT JOIN bookings b ON b.session_id = s.id
                GROUP BY s.id, m.title, h.name ORDER BY s.datetime DESC
            """)
            sessions = cursor.fetchall()
            cursor.execute("SELECT COUNT(DISTINCT reference) AS count FROM bookings WHERE status = 'active'")
            booking_count = cursor.fetchone()["count"]
        edits = {}
        for kind, records in (("movie", movies), ("hall", halls), ("session", sessions)):
            value = request.args.get(f"edit_{kind}", type=int)
            edits[f"edit_{kind}"] = next((record for record in records if record["id"] == value), None)
            if value is not None and not edits[f"edit_{kind}"]:
                abort(404)
        stats = {"movies": len(movies), "halls": len(halls),
                 "sessions": sum(not item["is_cancelled"] and item["datetime"] > cinema_now() for item in sessions),
                 "bookings": booking_count}
        return render_template("admin.html", movies=movies, halls=halls, sessions=sessions, stats=stats, **edits)

    @app.get("/health")
    def health():
        try:
            with transaction() as cursor:
                cursor.execute("SELECT 1 AS ok")
                cursor.fetchone()
            return jsonify(status="ok", database="ok")
        except psycopg2.Error:
            return jsonify(status="error", database="unavailable"), 503

    return app


def manage_movie(cursor, action):
    movie = None
    if action != "add_movie":
        movie_id = identifier(request.form, "movie_id")
        cursor.execute("SELECT * FROM movies WHERE id = %s FOR UPDATE", (movie_id,))
        movie = cursor.fetchone()
        if not movie:
            raise ValidationError("Фильм не найден.")
        if action == "delete_movie":
            cursor.execute("SELECT 1 FROM sessions WHERE movie_id = %s LIMIT 1", (movie_id,))
            if cursor.fetchone():
                raise ValidationError("У фильма есть сеансы. Удаление недоступно, чтобы сохранить расписание и историю бронирований.")
            cursor.execute("DELETE FROM movies WHERE id = %s", (movie_id,))
            return None, movie["poster"]
    title = field(request.form, "title", "Название фильма", 160)
    genre = field(request.form, "genre", "Жанр", 80)
    description = field(request.form, "description", "Описание", 5000, minimum=10)
    duration = integer(request.form, "duration", "Длительность", 1, 400)
    year = integer(request.form, "release_year", "Год выпуска", 1895, cinema_now().year + 5)
    age_rating = integer(request.form, "age_rating", "Возрастной рейтинг", 0, 18)
    if age_rating not in {0, 6, 12, 16, 18}:
        raise ValidationError("Возрастной рейтинг: 0, 6, 12, 16 или 18 лет.")
    if movie and movie["duration"] != duration:
        cursor.execute("""
            SELECT 1 FROM sessions WHERE movie_id = %s AND NOT is_cancelled
                AND datetime + (%s + 15) * INTERVAL '1 minute' > %s LIMIT 1
        """, (movie["id"], movie["duration"], cinema_now()))
        if cursor.fetchone():
            raise ValidationError("Нельзя менять длительность фильма с предстоящими или идущими сеансами. Отмените предстоящие сеансы и дождитесь завершения текущего.")
    new_poster = save_poster(request.files.get("poster"))
    poster = new_poster or (movie["poster"] if movie else "posters/default.svg")
    values = (title, genre, description, duration, year, age_rating, poster)
    try:
        if movie:
            cursor.execute("""
                UPDATE movies SET title = %s, genre = %s, description = %s, duration = %s,
                    release_year = %s, age_rating = %s, poster = %s WHERE id = %s
            """, (*values, movie["id"]))
        else:
            cursor.execute("""
                INSERT INTO movies (title, genre, description, duration, release_year, age_rating, poster, is_active)
                VALUES (%s, %s, %s, %s, %s, %s, %s, true)
            """, values)
    except Exception:
        if new_poster:
            remove_upload(new_poster)
        raise
    return new_poster, movie["poster"] if movie and new_poster else None


def manage_hall(cursor, action):
    hall = None
    if action != "add_hall":
        hall_id = identifier(request.form, "hall_id")
        cursor.execute("SELECT * FROM halls WHERE id = %s FOR UPDATE", (hall_id,))
        hall = cursor.fetchone()
        if not hall:
            raise ValidationError("Зал не найден.")
        cursor.execute("SELECT 1 FROM sessions WHERE hall_id = %s LIMIT 1", (hall_id,))
        has_sessions = bool(cursor.fetchone())
        if action == "delete_hall":
            if has_sessions:
                raise ValidationError("У зала есть сеансы. Удаление недоступно, чтобы сохранить историю бронирований.")
            cursor.execute("DELETE FROM halls WHERE id = %s", (hall_id,))
            return
    name = field(request.form, "name", "Название зала", 80)
    rows = integer(request.form, "rows", "Количество рядов", 1, 20)
    seats = integer(request.form, "seats_per_row", "Мест в ряду", 1, 30)
    cursor.execute("SELECT id FROM halls WHERE lower(name) = lower(%s) AND id <> %s", (name, hall["id"] if hall else 0))
    if cursor.fetchone():
        raise ValidationError("Зал с таким названием уже существует.")
    if hall:
        if has_sessions and (rows != hall["rows"] or seats != hall["seats_per_row"]):
            raise ValidationError("Нельзя менять схему зала, у которого есть сеансы. Создайте новый зал с нужной схемой.")
        cursor.execute("UPDATE halls SET name = %s, rows = %s, seats_per_row = %s WHERE id = %s", (name, rows, seats, hall["id"]))
    else:
        cursor.execute("INSERT INTO halls (name, rows, seats_per_row) VALUES (%s, %s, %s)", (name, rows, seats))


def manage_session(cursor, action):
    showing = None
    if action != "add_session":
        session_id = identifier(request.form, "session_id")
        cursor.execute("SELECT * FROM sessions WHERE id = %s FOR UPDATE", (session_id,))
        showing = cursor.fetchone()
        if not showing:
            raise ValidationError("Сеанс не найден.")
        if showing["datetime"] <= cinema_now():
            raise ValidationError("Прошедшие сеансы доступны только для просмотра.")
        if action == "delete_session":
            cursor.execute("UPDATE sessions SET is_cancelled = true WHERE id = %s", (session_id,))
            cursor.execute("UPDATE bookings SET status = 'cancelled' WHERE session_id = %s AND status = 'active'", (session_id,))
            return
        if showing["is_cancelled"]:
            raise ValidationError("Отменённый сеанс нельзя изменить. Создайте новый.")
        cursor.execute("SELECT 1 FROM bookings WHERE session_id = %s LIMIT 1", (session_id,))
        if cursor.fetchone():
            raise ValidationError("У сеанса уже есть бронирования. Изменение недоступно; при необходимости отмените сеанс и создайте новый.")
    movie_id = identifier(request.form, "movie_id")
    hall_id = identifier(request.form, "hall_id")
    starts = parse_datetime(request.form.get("datetime", ""))
    price = parse_price(request.form.get("price", ""))
    cursor.execute("SELECT id, duration FROM movies WHERE id = %s AND is_active FOR UPDATE", (movie_id,))
    movie = cursor.fetchone()
    if not movie:
        raise ValidationError("Выберите существующий фильм.")
    hall_ids = sorted({hall_id, showing["hall_id"] if showing else hall_id})
    cursor.execute("SELECT id FROM halls WHERE id = ANY(%s) ORDER BY id FOR UPDATE", (hall_ids,))
    locked_halls = [row["id"] for row in cursor.fetchall()]
    if hall_id not in locked_halls:
        raise ValidationError("Выберите существующий зал.")
    ends = starts + timedelta(minutes=movie["duration"] + 15)
    cursor.execute("""
        SELECT s.id FROM sessions s JOIN movies m ON m.id = s.movie_id
        WHERE s.hall_id = %s AND NOT s.is_cancelled AND s.id <> %s
          AND s.datetime < %s AND s.datetime + (m.duration + 15) * INTERVAL '1 minute' > %s LIMIT 1
    """, (hall_id, showing["id"] if showing else 0, ends, starts))
    if cursor.fetchone():
        raise ValidationError("В это время зал занят. Между сеансами нужен перерыв не менее 15 минут.")
    if showing:
        cursor.execute("UPDATE sessions SET movie_id = %s, hall_id = %s, datetime = %s, price = %s WHERE id = %s", (movie_id, hall_id, starts, price, showing["id"]))
    else:
        cursor.execute("INSERT INTO sessions (movie_id, hall_id, datetime, price, is_cancelled) VALUES (%s, %s, %s, %s, false)", (movie_id, hall_id, starts, price))


app = create_app()


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.environ.get("PORT", "5000")),
            debug=os.environ.get("FLASK_DEBUG", "false").lower() in {"1", "true", "yes"})
