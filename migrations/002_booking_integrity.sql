ALTER TABLE public.movies
    ADD COLUMN IF NOT EXISTS genre text DEFAULT '',
    ADD COLUMN IF NOT EXISTS release_year integer NOT NULL DEFAULT 2026,
    ADD COLUMN IF NOT EXISTS age_rating integer NOT NULL DEFAULT 12,
    ADD COLUMN IF NOT EXISTS is_active boolean NOT NULL DEFAULT true;

ALTER TABLE public.sessions
    ADD COLUMN IF NOT EXISTS is_cancelled boolean NOT NULL DEFAULT false;

ALTER TABLE public.bookings
    ADD COLUMN IF NOT EXISTS reference varchar(12),
    ADD COLUMN IF NOT EXISTS status varchar(16) NOT NULL DEFAULT 'active',
    ADD COLUMN IF NOT EXISTS price numeric(6, 2);

UPDATE public.movies SET genre = '' WHERE genre IS NULL;
UPDATE public.movies SET description = '' WHERE description IS NULL;
UPDATE public.movies SET poster = '' WHERE poster IS NULL;
UPDATE public.bookings
SET reference = 'OLD' || upper(lpad(to_hex(id), 9, '0'))
WHERE reference IS NULL;
UPDATE public.bookings b SET price = s.price
FROM public.sessions s WHERE s.id = b.session_id AND b.price IS NULL;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM public.users
        GROUP BY lower(username) HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'Migration stopped: usernames differ only by case. Resolve duplicate users before retrying; no data has been removed.';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.bookings WHERE status = 'active'
        GROUP BY session_id, seat_row, seat_number HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'Migration stopped: multiple active bookings claim the same seat. Resolve the conflict before retrying; no data has been removed.';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.bookings b
        JOIN public.sessions s ON s.id = b.session_id
        JOIN public.halls h ON h.id = s.hall_id
        WHERE b.seat_row > h.rows OR b.seat_number > h.seats_per_row
    ) THEN
        RAISE EXCEPTION 'Migration stopped: a booking is outside its hall seating plan. Correct the affected data before retrying; no data has been removed.';
    END IF;
END $$;

ALTER TABLE public.users
    ALTER COLUMN role SET NOT NULL,
    ALTER COLUMN role SET DEFAULT 'user';
ALTER TABLE public.movies
    ALTER COLUMN description SET DEFAULT '',
    ALTER COLUMN description SET NOT NULL,
    ALTER COLUMN poster SET DEFAULT '',
    ALTER COLUMN poster SET NOT NULL,
    ALTER COLUMN genre SET DEFAULT '',
    ALTER COLUMN genre SET NOT NULL,
    ALTER COLUMN duration SET NOT NULL,
    ALTER COLUMN release_year SET NOT NULL,
    ALTER COLUMN age_rating SET NOT NULL,
    ALTER COLUMN is_active SET NOT NULL;
ALTER TABLE public.halls
    ALTER COLUMN rows SET NOT NULL,
    ALTER COLUMN seats_per_row SET NOT NULL;
ALTER TABLE public.sessions
    ALTER COLUMN movie_id SET NOT NULL,
    ALTER COLUMN hall_id SET NOT NULL,
    ALTER COLUMN datetime SET NOT NULL,
    ALTER COLUMN price SET NOT NULL,
    ALTER COLUMN is_cancelled SET NOT NULL;
ALTER TABLE public.bookings
    ALTER COLUMN user_id SET NOT NULL,
    ALTER COLUMN session_id SET NOT NULL,
    ALTER COLUMN seat_row SET NOT NULL,
    ALTER COLUMN seat_number SET NOT NULL,
    ALTER COLUMN booked_at SET NOT NULL,
    ALTER COLUMN reference SET NOT NULL,
    ALTER COLUMN status SET NOT NULL,
    ALTER COLUMN price SET NOT NULL;

DO $$
DECLARE
    item record;
BEGIN
    FOR item IN
        SELECT * FROM (VALUES
            ('users', 'users_username_length', 'CHECK (char_length(btrim(username)) BETWEEN 3 AND 32)'),
            ('users', 'users_password_present', 'CHECK (char_length(password) > 0)'),
            ('users', 'users_role_valid', 'CHECK (role IN (''user'', ''admin''))'),
            ('movies', 'movies_title_length', 'CHECK (char_length(btrim(title)) BETWEEN 1 AND 160)'),
            ('movies', 'movies_duration_valid', 'CHECK (duration BETWEEN 1 AND 400)'),
            ('movies', 'movies_release_year_valid', 'CHECK (release_year BETWEEN 1888 AND 2100)'),
            ('movies', 'movies_age_rating_valid', 'CHECK (age_rating IN (0, 6, 12, 16, 18))'),
            ('halls', 'halls_name_length', 'CHECK (char_length(btrim(name)) BETWEEN 1 AND 80)'),
            ('halls', 'halls_rows_valid', 'CHECK (rows BETWEEN 1 AND 20)'),
            ('halls', 'halls_seats_valid', 'CHECK (seats_per_row BETWEEN 1 AND 30)'),
            ('sessions', 'sessions_price_valid', 'CHECK (price BETWEEN 0.01 AND 9999.99)'),
            ('sessions', 'sessions_movie_id_fkey', 'FOREIGN KEY (movie_id) REFERENCES public.movies(id)'),
            ('sessions', 'sessions_hall_id_fkey', 'FOREIGN KEY (hall_id) REFERENCES public.halls(id)'),
            ('bookings', 'bookings_status_valid', 'CHECK (status IN (''active'', ''cancelled''))'),
            ('bookings', 'bookings_reference_valid', 'CHECK (reference ~ ''^[A-Z0-9]{6,12}$'')'),
            ('bookings', 'bookings_seat_row_valid', 'CHECK (seat_row > 0)'),
            ('bookings', 'bookings_seat_number_valid', 'CHECK (seat_number > 0)'),
            ('bookings', 'bookings_price_valid', 'CHECK (price BETWEEN 0.01 AND 9999.99)'),
            ('bookings', 'bookings_user_id_fkey', 'FOREIGN KEY (user_id) REFERENCES public.users(id)'),
            ('bookings', 'bookings_session_id_fkey', 'FOREIGN KEY (session_id) REFERENCES public.sessions(id)')
        ) AS constraints(table_name, constraint_name, definition)
    LOOP
        IF NOT EXISTS (
            SELECT 1 FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE n.nspname = 'public'
              AND t.relname = item.table_name
              AND c.conname = item.constraint_name
        ) THEN
            EXECUTE format('ALTER TABLE public.%I ADD CONSTRAINT %I %s',
                item.table_name, item.constraint_name, item.definition);
        END IF;
    END LOOP;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS users_username_lower_key
    ON public.users (lower(username));
CREATE INDEX IF NOT EXISTS bookings_reference_idx
    ON public.bookings (reference);
CREATE UNIQUE INDEX IF NOT EXISTS bookings_active_seat_key
    ON public.bookings (session_id, seat_row, seat_number)
    WHERE status = 'active';
CREATE INDEX IF NOT EXISTS sessions_movie_datetime_idx
    ON public.sessions (movie_id, datetime);
CREATE INDEX IF NOT EXISTS sessions_hall_datetime_idx
    ON public.sessions (hall_id, datetime);
CREATE INDEX IF NOT EXISTS bookings_user_booked_idx
    ON public.bookings (user_id, booked_at DESC);
