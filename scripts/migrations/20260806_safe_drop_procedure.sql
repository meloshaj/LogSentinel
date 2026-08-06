-- 1. Create the safe drop chunks procedure
CREATE OR REPLACE PROCEDURE safe_drop_chunks(retention_interval INTERVAL, max_retries INT DEFAULT 5)
LANGUAGE plpgsql
AS $$
DECLARE
    retry_count INT := 0;
BEGIN
    WHILE retry_count < max_retries LOOP
        BEGIN
            -- Set strict local lock timeout
            SET LOCAL lock_timeout = '1000ms';
            
            -- Execute drop chunks
            PERFORM drop_chunks('logs', retention_interval);
            
            RAISE NOTICE 'Successfully dropped chunks older than %', retention_interval;
            RETURN;
        EXCEPTION WHEN lock_not_available THEN
            RAISE WARNING 'Could not acquire lock for drop_chunks (Attempt %/%). Retrying in 5 seconds...', retry_count + 1, max_retries;
            retry_count := retry_count + 1;
            PERFORM pg_sleep(5);
        END;
    END LOOP;
    
    RAISE WARNING 'Failed to drop chunks after % attempts due to lock contention. Deferring to next run.', max_retries;
END;
$$;

-- 2. Create the TimescaleDB job wrapper
CREATE OR REPLACE PROCEDURE safe_drop_chunks_job(job_id INT, config JSONB)
LANGUAGE plpgsql
AS $$
DECLARE
    retention_interval INTERVAL;
    max_retries INT;
BEGIN
    retention_interval := COALESCE((config->>'retention_interval')::INTERVAL, INTERVAL '30 days');
    max_retries := COALESCE((config->>'max_retries')::INT, 5);
    
    CALL safe_drop_chunks(retention_interval, max_retries);
END;
$$;

-- 3. Replace the existing retention policy with our custom lock-safe policy
DO $$
BEGIN
    -- Only remove if it exists (catch exceptions if it doesn't)
    PERFORM remove_retention_policy('logs', if_exists => true);
EXCEPTION WHEN OTHERS THEN
    -- Ignore if not present
END $$;

-- 4. Schedule the custom job
SELECT add_job('safe_drop_chunks_job', '1 day', config => '{"retention_interval": "30 days", "max_retries": 5}');
