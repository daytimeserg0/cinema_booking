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
            cur.execute("DELETE FROM halls WHERE id = %s;", (hall_id,))
            conn.commit()
            flash("Hall deleted successfully!", "delete_hall_success")
            cur.close()
            conn.close()
            return redirect(url_for("admin_panel"))

        # ---------------- ADD MOVIE ----------------
        if request.method == "POST" and "add_movie" in request.form:
            title = request.form["title"]
            description = request.form["description"]
            duration = request.form["duration"]
            poster_file = request.files.get("poster")

            #poster_path = None
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
                    "INSERT INTO movies (title, description, duration, poster) VALUES (%s, %s, %s, %s);",
                    (title, description, duration, poster_file.filename)
                )
                conn.commit()
                flash(f"Movie '{title}' added successfully!", "add_movie_success")
            return redirect(url_for("admin_panel"))

        # ---------------- DELETE MOVIE ----------------
        if request.method == "POST" and "delete_movie" in request.form:
            movie_id = request.form["delete_movie"]

            cur.execute("SELECT poster FROM movies WHERE id = %s;", (movie_id,))
            poster = cur.fetchone()

            cur.execute("DELETE FROM movies WHERE id = %s;", (movie_id,))
            conn.commit()

            if poster and poster[0]:
                poster_path = os.path.join(UPLOAD_FOLDER, poster[0])
                print(poster_path)
                if os.path.exists(poster_path):
                    os.remove(poster_path)

            flash("Movie deleted successfully!", "delete_movie_success")
            return redirect(url_for("admin_panel"))

        # ---------------- FETCH DATA ----------------
        cur.execute("SELECT id, name, rows, seats_per_row FROM halls ORDER BY id;")
        halls = cur.fetchall()

        cur.execute("SELECT id, title, description, duration, poster FROM movies ORDER BY id;")
        movies = cur.fetchall()

        cur.close()
        conn.close()
    except Exception as e:
        flash(f"Error: {e}", "error")
        halls, movies = [], []

    return render_template("admin.html", halls=halls, movies=movies)



if __name__ == "__main__":
    app.run(debug=True)
