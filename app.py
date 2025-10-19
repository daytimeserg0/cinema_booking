from flask import Flask, render_template, request, redirect, session, url_for, flash
import psycopg2
from werkzeug.security import generate_password_hash, check_password_hash
from db import get_db_connection

app = Flask(__name__)
app.secret_key = "your_secret_key"


# Home page
@app.route("/")
def home():
    return render_template("index.html")

# Registration page
@app.route("/register", methods=["GET", "POST"])
def register():
    message = None
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]
        hashed_password = generate_password_hash(password)

        conn = get_db_connection()
        cur = conn.cursor()

        # Check if username already exists
        cur.execute("SELECT * FROM users WHERE username = %s;", (username,))
        user = cur.fetchone()

        if user:
            message = "Username already exists!"
        else:
            cur.execute(
                "INSERT INTO users (username, password, role) VALUES (%s, %s, %s) RETURNING id;",
                (username, hashed_password, "user")
            )
            new_user_id = cur.fetchone()[0]
            conn.commit()

            # Set session for the new user
            session["user_id"] = new_user_id
            session["username"] = username
            session["role"] = "user"

            cur.close()
            conn.close()
            return redirect("/")  # redirect to home after successful registration

        cur.close()
        conn.close()

    return render_template("register.html", message=message)

# Login page
@app.route("/login", methods=["GET", "POST"])
def login():
    message = None
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE username = %s;", (username,))
        user = cur.fetchone()
        cur.close()
        conn.close()

        if user and check_password_hash(user[2], password):
            # store user info in session
            session["user_id"] = user[0]
            session["username"] = user[1]
            session["role"] = user[3]
            return redirect("/")
        else:
            message = "Invalid username or password!"

    return render_template("login.html", message=message)

# Logout route
@app.route("/logout")
def logout():
    session.clear()  # clears all session data
    return redirect("/")

# Account page (protected example)
@app.route("/account")
def account():
    if "username" not in session:
        return redirect("/login")
    return f"Hello, {session['username']}! This is your account page."

# Admin page
@app.route("/admin", methods=["GET", "POST"])
def admin_panel():
    if "role" not in session or session["role"] != "admin":
        return redirect("/")

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # Handle adding new hall
        if request.method == "POST" and "add_hall" in request.form:
            hall_name = request.form["name"]
            rows = request.form["rows"]
            seats_per_row = request.form["seats_per_row"]

            cur.execute("SELECT * FROM halls WHERE name = %s;", (hall_name,))
            hall = cur.fetchone()

            if hall:
                flash("Hall already exists!", "error")
            else:
                cur.execute(
                    "INSERT INTO halls (name, rows, seats_per_row) VALUES (%s, %s, %s);",
                    (hall_name, rows, seats_per_row)
                )
                conn.commit()
                flash(f"Hall '{hall_name}' added successfully!", "success")

            cur.close()
            conn.close()
            return redirect(url_for("admin_panel"))

        # Handle deleting hall
        if request.method == "POST" and "delete_hall" in request.form:
            hall_id = request.form["delete_hall"]
            cur.execute("DELETE FROM halls WHERE id = %s;", (hall_id,))
            conn.commit()
            flash("Hall deleted successfully!", "success")
            cur.close()
            conn.close()
            return redirect(url_for("admin_panel"))

        # GET: fetch all halls
        cur.execute("SELECT id, name, rows, seats_per_row FROM halls ORDER BY id;")
        halls = cur.fetchall()
        cur.close()
        conn.close()
    except Exception as e:
        flash(f"Error: {e}", "error")
        halls = []

    return render_template("admin.html", halls=halls)



if __name__ == "__main__":
    app.run(debug=True)
