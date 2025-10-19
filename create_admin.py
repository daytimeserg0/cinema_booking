import psycopg2
from psycopg2 import sql
from werkzeug.security import generate_password_hash
from db import get_db_connection


# Admin
admin_username = "admin"
admin_password = "1234"
admin_role = "admin"

try:
    conn = get_db_connection()
    cur = conn.cursor()

    # Check admin name
    cur.execute("SELECT * FROM users WHERE username = %s;", (admin_username,))
    existing_user = cur.fetchone()

    if existing_user:
        print(f"User '{admin_username}' already exists.")
    else:
        hashed_password = generate_password_hash(admin_password)

        # Insert the new admin into the table
        cur.execute("""
                INSERT INTO users (username, password, role)
                VALUES (%s, %s, %s);
            """, (admin_username, hashed_password, admin_role))

        conn.commit()
        print(f"Admin '{admin_username}' created successfully!")

except Exception as e:
    print("Database error:", e)

finally:
    if conn:
        cur.close()
        conn.close()