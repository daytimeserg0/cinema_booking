import os
from flask import Flask, render_template, request, redirect, session, url_for, flash
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from db import get_db_connection

app = Flask(__name__)
app.secret_key = "your_secret_key"


# Home page
@app.route("/")
def home():
    conn = get_db_connection()
    cur = conn.cursor()

    # Select movies that have sessions
    cur.execute("""
            SELECT DISTINCT m.id, m.title, m.genre, m.description, m.poster, m.duration
            FROM movies m
            JOIN sessions s ON s.movie_id = m.id
            ORDER BY m.id;
        """)
    movies = cur.fetchall()

    cur.close()
    conn.close()

    return render_template("index.html", movies=movies)


@app.route("/movie/<int:movie_id>")
def movie_sessions(movie_id):
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute(
        "SELECT id, title, genre, description, duration, poster FROM movies WHERE id=%s;",
        (movie_id,)
    )
    movie = cur.fetchone()

    # Get all sessions for movie
    cur.execute("""
        SELECT s.id, s.datetime, s.price, h.name
        FROM sessions s
        JOIN halls h ON s.hall_id = h.id
        WHERE s.movie_id=%s
        ORDER BY s.datetime;
    """, (movie_id,))
    sessions = cur.fetchall()

    cur.close()
    conn.close()

    return render_template("movie_sessions.html", movie=movie, sessions=sessions)


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
            return redirect("/")

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


UPLOAD_FOLDER = os.path.join('static', 'posters')
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif"}

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS

# Admin page
@app.route("/admin", methods=["GET", "POST"])
def admin_panel():
    if "role" not in session or session["role"] != "admin":
        return redirect("/")

    try:
        conn = get_db_connection()
        cur = conn.cursor()

        # ---------------- ADD HALL ----------------
        if request.method == "POST" and "add_hall" in request.form:
            hall_name = request.form["name"]
            rows = request.form["rows"]
            seats_per_row = request.form["seats_per_row"]

            cur.execute("SELECT * FROM halls WHERE name = %s;", (hall_name,))
            hall = cur.fetchone()

            if hall:
                flash("Hall already exists!", "add_hall_error")
            else:
                cur.execute(
                    "INSERT INTO halls (name, rows, seats_per_row) VALUES (%s, %s, %s);",
                    (hall_name, rows, seats_per_row)
                )
                conn.commit()
                flash(f"Hall '{hall_name}' added successfully!", "add_hall_success")

            cur.close()
            conn.close()
            return redirect(url_for("admin_panel"))

        # ---------------- DELETE HALL ----------------
        if request.method == "POST" and "delete_hall" in request.form:
            hall_id = request.form["delete_hall"]

            # Check for linked sessions
            cur.execute("SELECT id FROM sessions WHERE hall_id = %s;", (hall_id,))
            linked_sessions = cur.fetchall()

            # Delete linked sessions
            if linked_sessions:
                cur.execute("DELETE FROM sessions WHERE hall_id = %s;", (hall_id,))
                conn.commit()

            cur.execute("DELETE FROM halls WHERE id = %s;", (hall_id,))
            conn.commit()

            if linked_sessions:
                flash("Hall and all linked sessions deleted successfully", 'delete_hall_success')
            else:
                flash("Hall deleted successfully!", "delete_hall_success")
            return redirect(url_for("admin_panel"))

        # ---------------- ADD MOVIE ----------------
        if request.method == "POST" and "add_movie" in request.form:
            title = request.form["title"]
            genre = request.form["genre"]
            description = request.form["description"]
            duration = request.form["duration"]
            poster_file = request.files.get("poster")

            if poster_file and allowed_file(poster_file.filename):
                filename = secure_filename(poster_file.filename)
                poster_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
                poster_file.save(poster_path)

            cur.execute("SELECT * FROM movies WHERE title = %s;", (title,))
            movie = cur.fetchone()

            if movie:
                flash("Movie already exists!", "add_movie_error")
            else:
                cur.execute(
                    "INSERT INTO movies (title, genre, description, duration, poster) VALUES (%s, %s, %s, %s, %s);",
                    (title, genre, description, duration, poster_file.filename)
                )
                conn.commit()
                flash(f"Movie '{title}' added successfully!", "add_movie_success")
            return redirect(url_for("admin_panel"))

        # ---------------- DELETE MOVIE ----------------
        if request.method == "POST" and "delete_movie" in request.form:
            movie_id = request.form["delete_movie"]

            # Check for linked sessions
            cur.execute("SELECT id FROM sessions WHERE movie_id = %s;", (movie_id,))
            linked_sessions = cur.fetchall()

            # Delete linked sessions
            if linked_sessions:
                cur.execute("DELETE FROM sessions WHERE movie_id = %s;", (movie_id,))
                conn.commit()

            cur.execute("SELECT poster FROM movies WHERE id = %s;", (movie_id,))
            poster = cur.fetchone()

            cur.execute("DELETE FROM movies WHERE id = %s;", (movie_id,))
            conn.commit()

            if poster and poster[0]:
                poster_path = os.path.join(UPLOAD_FOLDER, poster[0])
                if os.path.exists(poster_path):
                    os.remove(poster_path)
            if linked_sessions:
                flash("Movie and all linked sessions deleted successfully", 'delete_movie_success')
            else:
                flash("Movie deleted successfully!", "delete_movie_success")
            return redirect(url_for("admin_panel"))

        # ---------------- ADD SESSION ----------------
        if request.method == "POST" and "add_session" in request.form:
            movie_id = request.form["movie_id"]
            hall_id = request.form["hall_id"]
            datetime_val = request.form["datetime"]
            price = request.form["price"]

            print(price)
            print(type(price))
            if int(price) > 9999:
                flash("Price cannot exceed 9999!", "add_session_error")
                return redirect(url_for("admin_panel"))

            # Check if a session already exists
            cur.execute("""
                SELECT * FROM sessions 
                WHERE movie_id = %s AND hall_id = %s AND datetime = %s;
            """, (movie_id, hall_id, datetime_val))
            existing_session = cur.fetchone()

            if existing_session:
                flash("A session for this movie, hall, and time already exists!", "add_session_error")
            else:
                cur.execute(
                    "INSERT INTO sessions (movie_id, hall_id, datetime, price) VALUES (%s, %s, %s, %s);",
                    (movie_id, hall_id, datetime_val, price)
                )
                conn.commit()
                flash("Session added successfully!", "add_session_success")

            return redirect(url_for("admin_panel"))

        # ---------------- DELETE SESSION ----------------
        if request.method == "POST" and "delete_session" in request.form:
            session_id = request.form["delete_session"]
            cur.execute("DELETE FROM sessions WHERE id = %s;", (session_id,))
            conn.commit()
            flash("Session deleted successfully!", "delete_session_success")
            return redirect(url_for("admin_panel"))

        # ---------------- FETCH DATA ----------------
        cur.execute("SELECT id, name, rows, seats_per_row FROM halls ORDER BY id;")
        halls = cur.fetchall()

        cur.execute("SELECT id, title, genre, description, duration, poster FROM movies ORDER BY id;")
        movies = cur.fetchall()

        cur.execute("""
        SELECT s.id, m.title, h.name, s.datetime, s.price
        FROM sessions s
        JOIN movies m ON s.movie_id = m.id
        JOIN halls h ON s.hall_id = h.id
        ORDER BY s.id;
        """)
        sessions = cur.fetchall()

        cur.close()
        conn.close()
    except Exception as e:
        flash(f"Error: {e}", "error")
        halls, movies, sessions = [], [], []

    return render_template("admin.html", halls=halls, movies=movies, sessions=sessions)

if __name__ == "__main__":
    app.run(debug=True)
