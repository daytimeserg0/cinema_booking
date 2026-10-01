import io
import os
import re
import secrets
import warnings
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from zoneinfo import ZoneInfo

from flask import current_app, has_app_context, session, url_for
from itsdangerous import BadSignature, URLSafeTimedSerializer
from PIL import Image, ImageOps, UnidentifiedImageError


class ValidationError(ValueError):
    pass


def cinema_now():
    zone = current_app.config["CINEMA_TIMEZONE"] if has_app_context() else os.environ.get("CINEMA_TIMEZONE", "Europe/Moscow")
    return datetime.now(ZoneInfo(zone)).replace(tzinfo=None)


def csrf_token():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_urlsafe(32)
    return session["csrf_token"]


def session_quote_payload(showing, user_id):
    return {
        "user_id": user_id,
        "session_id": showing["id"],
        "movie_id": showing["movie_id"],
        "hall_id": showing["hall_id"],
        "datetime": showing["datetime"].isoformat(),
        "price": str(showing["price"]),
        "rows": showing["rows"],
        "seats_per_row": showing["seats_per_row"],
    }


def create_session_quote(showing, user_id):
    signer = URLSafeTimedSerializer(current_app.secret_key, salt="booking-quote")
    return signer.dumps(session_quote_payload(showing, user_id))


def validate_session_quote(token, showing, user_id):
    signer = URLSafeTimedSerializer(current_app.secret_key, salt="booking-quote")
    try:
        payload = signer.loads(token, max_age=900)
    except BadSignature:
        raise ValidationError("Страница выбора мест устарела. Обновите её, проверьте сеанс и выберите места заново.") from None
    if payload != session_quote_payload(showing, user_id):
        raise ValidationError("Условия сеанса изменились. Обновите страницу и проверьте время, зал и цену перед бронированием.")


def safe_next(value):
    if value and value.startswith("/") and not value.startswith("//") and "\\" not in value:
        if not any(ord(char) < 32 for char in value):
            return value
    return url_for("home")


def valid_username(value):
    return bool(re.fullmatch(r"[\w-]{3,32}", value, re.UNICODE))


def field(form, name, label, maximum, minimum=1):
    value = form.get(name, "").strip()
    if not minimum <= len(value) <= maximum:
        raise ValidationError(f"{label}: от {minimum} до {maximum} символов.")
    return value


def integer(form, name, label, minimum, maximum):
    raw = form.get(name, "")
    if not re.fullmatch(r"\d{1,10}", str(raw)):
        raise ValidationError(f"{label}: укажите целое число от {minimum} до {maximum}.")
    value = int(raw)
    if not minimum <= value <= maximum:
        raise ValidationError(f"{label}: укажите целое число от {minimum} до {maximum}.")
    return value


def identifier(form, name):
    return integer(form, name, "Идентификатор", 1, 2147483647)


def parse_price(value):
    try:
        price = Decimal(value.replace(",", "."))
        if not price.is_finite() or not Decimal("0.01") <= price <= Decimal("9999.99"):
            raise InvalidOperation
        if price != price.quantize(Decimal("0.01")):
            raise InvalidOperation
    except (InvalidOperation, ValueError, AttributeError):
        raise ValidationError("Цена должна быть от 0,01 до 9 999,99 ₽, не больше двух знаков после запятой.") from None
    return price


def parse_datetime(value):
    try:
        result = datetime.fromisoformat(value)
        if result.tzinfo is not None or result.year > cinema_now().year + 5:
            raise ValueError
    except (ValueError, TypeError):
        raise ValidationError("Укажите корректные дату и время сеанса.") from None
    if result <= cinema_now():
        raise ValidationError("Сеанс должен начинаться в будущем.")
    return result


def parse_date(value):
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except (ValueError, TypeError):
        raise ValidationError("Укажите дату в формате ГГГГ-ММ-ДД.") from None


def parse_seats(raw, rows, seats_per_row, maximum=8):
    if not raw or len(raw) > 120:
        raise ValidationError("Выберите от 1 до 8 мест.")
    items = raw.split(",")
    if not 1 <= len(items) <= maximum:
        raise ValidationError("За одно бронирование можно выбрать от 1 до 8 мест.")
    seats = []
    for item in items:
        if not re.fullmatch(r"\d{1,2}-\d{1,2}", item):
            raise ValidationError("Некорректный номер места. Выберите места на схеме зала.")
        row, number = map(int, item.split("-"))
        if not 1 <= row <= rows or not 1 <= number <= seats_per_row:
            raise ValidationError("Выбранного места нет в этом зале.")
        seats.append((row, number))
    if len(set(seats)) != len(seats):
        raise ValidationError("Одно место нельзя выбрать дважды.")
    return sorted(seats)


def money(value):
    price = Decimal(value or 0)
    formatted = f"{price:,.2f}".replace(",", " ").replace(".", ",")
    return formatted.removesuffix(",00") + " ₽"


def date_label(value):
    months = ("января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря")
    return f"{value.day} {months[value.month - 1]}"


def poster_url(filename):
    path = PurePosixPath(str(filename or "posters/default.svg").replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts:
        path = PurePosixPath("posters/default.svg")
    elif len(path.parts) == 1:
        path = PurePosixPath("posters") / path
    elif path.parts[0] not in {"posters", "uploads"}:
        path = PurePosixPath("posters/default.svg")
    return url_for("static", filename=str(path))


def save_poster(upload):
    if not upload or not upload.filename:
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            data = upload.read(5 * 1024 * 1024 + 1)
            if len(data) > 5 * 1024 * 1024:
                raise ValidationError("Размер постера не должен превышать 5 МБ.")
            with Image.open(io.BytesIO(data)) as original:
                if original.format not in {"JPEG", "PNG", "WEBP"}:
                    raise ValidationError("Загрузите постер в формате JPG, PNG или WebP.")
                if original.width * original.height > 20_000_000:
                    raise ValidationError("Размер изображения не должен превышать 20 мегапикселей.")
                original.load()
                image = ImageOps.exif_transpose(original).convert("RGB")
                image.thumbnail((1600, 2400))
                folder = Path(current_app.config["UPLOAD_FOLDER"])
                folder.mkdir(parents=True, exist_ok=True)
                filename = f"{secrets.token_hex(16)}.jpg"
                image.save(folder / filename, "JPEG", quality=88, optimize=True)
                return f"uploads/{filename}"
    except ValidationError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValidationError("Не удалось прочитать постер. Загрузите корректное изображение JPG, PNG или WebP.") from None


def remove_upload(filename):
    if filename and re.fullmatch(r"uploads/[a-f0-9]{32}\.jpg", filename):
        path = Path(current_app.config["UPLOAD_FOLDER"]) / filename.split("/")[1]
        path.unlink(missing_ok=True)
