import psycopg2

# PostgreSQL settings
DB_NAME = "cinema_db"
DB_USER = "your_username_here"
DB_PASSWORD = "your_password_here"
DB_HOST = "localhost"
DB_PORT = "5432"

def get_db_connection():
    conn = psycopg2.connect(
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        port=DB_PORT
    )
    return conn
