-- Migration to change logs.id from BIGINT to VARCHAR(26) (ULID)
-- IMPORTANT: Run backfill_ulids.py first before applying this migration!

BEGIN;

-- We assume `id_ulid` column was created and populated by the python backfill script.
-- If not, this migration will fail or leave id as null.

-- Drop default nextval for integer id
ALTER TABLE logs ALTER COLUMN id DROP DEFAULT;
DROP SEQUENCE IF EXISTS logs_id_seq CASCADE;

-- If for some reason the python script was not run, and id_ulid does not exist,
-- we'll safely add it (though it will be null for existing rows).
ALTER TABLE logs ADD COLUMN IF NOT EXISTS id_ulid VARCHAR(26);

-- Now we change the primary key
ALTER TABLE logs DROP CONSTRAINT logs_pkey CASCADE;
ALTER TABLE logs DROP COLUMN id;

-- Rename id_ulid to id
ALTER TABLE logs RENAME COLUMN id_ulid TO id;

-- Set primary key
ALTER TABLE logs ADD PRIMARY KEY (id);

COMMIT;
