FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && useradd --create-home cinema
COPY --chown=cinema:cinema . .
RUN mkdir -p /app/static/uploads && chown cinema:cinema /app/static/uploads
USER cinema
EXPOSE 5000

CMD ["sh", "-c", "python migrate.py && exec gunicorn --bind 0.0.0.0:5000 --workers 2 --threads 2 --timeout 30 app:app"]
