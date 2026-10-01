import argparse
import os
from pathlib import Path
import socket

from dotenv import load_dotenv

from local_database import ensure_for_app


if __name__ == "__main__":
    load_dotenv(Path(__file__).resolve().parent / ".env")
    parser = argparse.ArgumentParser(description="Run Cinema Booking locally.")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "5000")))
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Port must be between 1 and 65535.")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if os.name == "nt":
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind(("127.0.0.1", args.port))
    except OSError:
        parser.error(f"Port {args.port} is already in use. Stop the running app or choose --port with a different number.")
    ensure_for_app()
    from app import app
    from migrate import run_migrations

    with app.app_context():
        run_migrations()
    local_dir = Path(__file__).resolve().parent / ".local"
    local_dir.mkdir(exist_ok=True)
    pid_file = local_dir / ("app.pid" if args.port == 5000 else f"app-{args.port}.pid")
    pid_file.write_text(str(os.getpid()), encoding="ascii")
    try:
        app.run(
            host="127.0.0.1",
            port=args.port,
            debug=os.environ.get("FLASK_DEBUG", "false").lower() in {"1", "true", "yes"},
        )
    finally:
        if pid_file.exists() and pid_file.read_text(encoding="ascii") == str(os.getpid()):
            pid_file.unlink()
