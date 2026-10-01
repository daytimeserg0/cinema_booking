import argparse
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile

import psycopg2
from psycopg2 import sql
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent
LOCAL = ROOT / ".local"
DATA = LOCAL / "postgres-data"
LOG = LOCAL / "postgres.log"
ENV_FILE = ROOT / ".env"
ADMIN_FILE = LOCAL / "postgres-admin.env"
LOCAL_PORT = 5433


def postgres_bin():
    if os.name != "nt":
        raise RuntimeError("The private database helper is for Windows. Use Docker Compose or an existing PostgreSQL server on this platform.")
    settings = dotenv_values(ENV_FILE)
    location = os.environ.get("POSTGRES_BIN") or settings.get("POSTGRES_BIN")
    path = Path(location or r"C:\Program Files\PostgreSQL\18\bin")
    if not all((path / name).is_file() for name in ("pg_ctl.exe", "initdb.exe")):
        raise RuntimeError("PostgreSQL tools not found. Set POSTGRES_BIN to the PostgreSQL 18 bin directory.")
    return path


def command(executable, *args, **kwargs):
    return subprocess.run(
        [str(postgres_bin() / executable), *map(str, args)],
        cwd=ROOT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        **kwargs,
    )


def require_free_port():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if os.name == "nt":
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind(("127.0.0.1", LOCAL_PORT))
    except OSError as exc:
        raise RuntimeError(
            f"Port {LOCAL_PORT} is already in use. Stop the conflicting server or configure an existing database in .env."
        ) from exc


def running():
    if not (DATA / "PG_VERSION").is_file():
        return False
    return command("pg_ctl.exe", "-D", DATA, "status", capture_output=True).returncode == 0


def start():
    if not (DATA / "PG_VERSION").is_file():
        raise RuntimeError("Local database is not initialized. Run: python local_database.py init")
    if not running():
        require_free_port()
        result = command(
            "pg_ctl.exe", "-D", DATA, "-l", LOG, "-w", "-t", "30", "start",
            capture_output=True,
        )
        if result.returncode:
            raise RuntimeError(f"PostgreSQL could not start. See {LOG} for details; the existing data was preserved.")


def stop():
    if running():
        command("pg_ctl.exe", "-D", DATA, "-m", "fast", "-w", "-t", "30", "stop", check=True)


def write_private_file(path, content):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)


def create_cluster(admin_password):
    password_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", newline="\n", dir=LOCAL,
            prefix="init-password-", suffix=".tmp", delete=False,
        ) as password_file:
            password_file.write(admin_password + "\n")
            password_path = Path(password_file.name)
        command(
            "initdb.exe", "-D", DATA, "-U", "postgres", "-A", "scram-sha-256",
            "--encoding=UTF8", "--locale=C", f"--pwfile={password_path}", check=True,
        )
    finally:
        if password_path is not None:
            password_path.unlink(missing_ok=True)
    with (DATA / "postgresql.conf").open("a", encoding="utf-8") as config:
        config.write("\nlisten_addresses = '127.0.0.1'\nport = 5433\n")


def initialize(resume=False):
    postgres_bin()
    if not resume:
        if ENV_FILE.exists() or DATA.exists() or ADMIN_FILE.exists():
            raise RuntimeError(
                "Existing .env, credentials or cluster found; nothing was overwritten. "
                "Use start for an existing installation, or init --resume for an incomplete private setup."
            )
        require_free_port()
        LOCAL.mkdir(exist_ok=True)
        admin_password = secrets.token_urlsafe(32)
        app_password = secrets.token_urlsafe(32)
        write_private_file(ADMIN_FILE, f"PGPASSWORD={admin_password}\n")
        write_private_file(
            ENV_FILE,
            "DB_NAME=cinema_db\nDB_USER=cinema_app\n"
            f"DB_PASSWORD={app_password}\nDB_HOST=127.0.0.1\nDB_PORT={LOCAL_PORT}\n"
            f"FLASK_SECRET_KEY={secrets.token_hex(32)}\n"
            "CINEMA_TIMEZONE=Europe/Moscow\nSESSION_COOKIE_SECURE=false\n"
            "FLASK_DEBUG=false\nPORT=5000\n",
        )
    settings = dotenv_values(ENV_FILE)
    admin_password = dotenv_values(ADMIN_FILE).get("PGPASSWORD")
    if (
        settings.get("DB_HOST") not in {"127.0.0.1", "localhost"}
        or settings.get("DB_PORT") != str(LOCAL_PORT)
        or settings.get("DB_USER") != "cinema_app"
        or settings.get("DB_NAME") != "cinema_db"
        or not settings.get("DB_PASSWORD")
        or not admin_password
    ):
        raise RuntimeError("Recovery requires the private setup's original .env and .local/postgres-admin.env credentials.")
    try:
        if not (DATA / "PG_VERSION").is_file():
            if DATA.exists():
                raise RuntimeError(f"An incomplete data directory exists at {DATA}. Inspect it before moving it aside; initialization will not delete it.")
            require_free_port()
            create_cluster(admin_password)
        start()
        conn = psycopg2.connect(
            host="127.0.0.1", port=LOCAL_PORT, user="postgres",
            password=admin_password, dbname="postgres", connect_timeout=5,
        )
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", ("cinema_app",))
                if cur.fetchone() is None:
                    cur.execute(
                        sql.SQL("CREATE ROLE {} LOGIN PASSWORD %s").format(sql.Identifier("cinema_app")),
                        (settings["DB_PASSWORD"],),
                    )
                cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", ("cinema_db",))
                if cur.fetchone() is None:
                    cur.execute("CREATE DATABASE cinema_db OWNER cinema_app")
        finally:
            conn.close()
        from migrate import run_migrations

        conn = psycopg2.connect(
            host="127.0.0.1", port=LOCAL_PORT, user="cinema_app",
            password=settings["DB_PASSWORD"], dbname="cinema_db", connect_timeout=5,
        )
        try:
            run_migrations(conn)
        finally:
            conn.close()
    except (OSError, subprocess.CalledProcessError, psycopg2.Error, RuntimeError) as exc:
        raise RuntimeError(
            "Private setup was not completed. Existing data and credentials were retained. "
            "Resolve the reported problem, then run: python local_database.py init --resume\n"
            f"{exc}"
        ) from exc
    print("Database ready: 127.0.0.1:5433 / cinema_db. Credentials saved in .env.")


def ensure_for_app():
    settings = dotenv_values(ENV_FILE)
    host = os.environ.get("DB_HOST", settings.get("DB_HOST", "127.0.0.1"))
    port = os.environ.get("DB_PORT", settings.get("DB_PORT", str(LOCAL_PORT)))
    if host in {"127.0.0.1", "localhost"} and str(port) == str(LOCAL_PORT) and (DATA / "PG_VERSION").exists():
        start()


def main():
    parser = argparse.ArgumentParser(description="Manage the private Windows PostgreSQL server.")
    parser.add_argument("action", choices=("init", "start", "stop", "status"))
    parser.add_argument("--resume", action="store_true", help="Resume an incomplete initialization without replacing credentials.")
    args = parser.parse_args()
    if args.resume and args.action != "init":
        parser.error("--resume is only valid with init.")
    try:
        if args.action == "init":
            initialize(resume=args.resume)
        elif args.action == "start":
            start()
        elif args.action == "stop":
            stop()
        else:
            print("running" if running() else "stopped")
    except (OSError, subprocess.CalledProcessError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
