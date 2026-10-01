DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM public.halls
        GROUP BY lower(name) HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'Migration stopped: hall names differ only by case. Resolve duplicate halls before retrying; no data has been removed.';
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS halls_name_lower_key
    ON public.halls (lower(name));
