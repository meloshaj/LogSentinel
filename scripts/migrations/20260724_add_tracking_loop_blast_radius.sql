-- Add nullable blast-radius payload storage for existing tracking-loop rows.
-- Safe to run repeatedly; does not drop, recreate, or backfill the table.

ALTER TABLE tracking_loops
    ADD COLUMN IF NOT EXISTS blast_radius JSONB NULL;
