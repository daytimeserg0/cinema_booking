import argparse
from contextlib import closing
from getpass import getpass
import os
import re
import sys

import psycopg2
from werkzeug.security import generate_password_hash

from db import get_db_connection


def main():
    parser = argparse.ArgumentParser(description="Create a Cinema Booking administrator.")
    parser.add_argument("--username", default="admin")
    parser.add_argument("--password-env", metavar="VARIABLE", help="Read a password from a named environment variable instead of prompting.")
    args = parser.parse_args()
    username = args.username.strip()
    if not re.fullmatch(r"[\w-]{3,32}", username):
        parser.error("Username must contain 3–32 letters, digits, underscores or hyphens.")
    if args.password_env:
        password = os.environ.get(args.password_env, "")
    else:
        password = getpass("Administrator password: ")
        confirmation = getpass("Confirm password: ")
        if password != confirmation:
            parser.error("Passwords do not match.")
    if not 8 <= len(password) <= 128:
        parser.error("Password must contain 8–128 characters.")
    try:
        with closing(get_db_connection()) as conn:
            with conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT id FROM users WHERE lower(username) = lower(%s)", (username,))
                    if cur.fetchone():
                        print("That username already exists; no account or password was changed.", file=sys.stderr)
                        return 1
                    cur.execute(
                        "INSERT INTO users (username, password, role) VALUES (%s, %s, 'admin')",
                        (username, generate_password_hash(password)),
                    )
    except psycopg2.errors.UniqueViolation:
        print("That username already exists; no account or password was changed.", file=sys.stderr)
        return 1
    except psycopg2.Error as exc:
        print(f"Could not create administrator: {exc.diag.message_primary or 'database connection failed'}", file=sys.stderr)
        return 1
    print(f"Administrator '{username}' created.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
