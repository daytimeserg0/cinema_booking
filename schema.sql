CREATE TABLE IF NOT EXISTS public.users (
    id serial PRIMARY KEY,
    username text NOT NULL,
    password text NOT NULL,
    role text NOT NULL DEFAULT 'user'
);

CREATE TABLE IF NOT EXISTS public.movies (
    id serial PRIMARY KEY,
    title text NOT NULL,
    description text NOT NULL DEFAULT '',
    duration integer NOT NULL,
    poster text NOT NULL DEFAULT '',
    genre text NOT NULL DEFAULT '',
    release_year integer NOT NULL DEFAULT 2026,
    age_rating integer NOT NULL DEFAULT 12,
    is_active boolean NOT NULL DEFAULT true
);

CREATE TABLE IF NOT EXISTS public.halls (
    id serial PRIMARY KEY,
    name text NOT NULL,
    rows integer NOT NULL,
    seats_per_row integer NOT NULL
);

CREATE TABLE IF NOT EXISTS public.sessions (
    id serial PRIMARY KEY,
    movie_id integer NOT NULL REFERENCES public.movies(id),
    hall_id integer NOT NULL REFERENCES public.halls(id),
    datetime timestamp without time zone NOT NULL,
    price numeric(6, 2) NOT NULL,
    is_cancelled boolean NOT NULL DEFAULT false
);

CREATE TABLE IF NOT EXISTS public.bookings (
    id serial PRIMARY KEY,
    user_id integer NOT NULL REFERENCES public.users(id),
    session_id integer NOT NULL REFERENCES public.sessions(id),
    seat_row integer NOT NULL,
    seat_number integer NOT NULL,
    booked_at timestamp without time zone NOT NULL DEFAULT now(),
    reference varchar(12) NOT NULL,
    status varchar(16) NOT NULL DEFAULT 'active',
    price numeric(6, 2) NOT NULL
);
