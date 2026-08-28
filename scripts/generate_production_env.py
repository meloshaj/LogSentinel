#!/usr/bin/env python3
"""
Generate a production-ready .env file for LogSentinel.
Generates cryptographically secure secrets for JWT, encryption, database, and ingestion API keys.
"""

import argparse
import base64
import os
import secrets
import textwrap

def generate_env(out_path: str, domain: str, bucket: str, endpoint: str, region: str) -> None:
    # Generate secure secrets
    jwt_secret = secrets.token_urlsafe(48)
    encryption_key = base64.urlsafe_b64encode(os.urandom(32)).decode('utf-8')
    postgres_password = secrets.token_urlsafe(24)
    ingest_key = f"default:{secrets.token_hex(24)}"

    # Provide placeholders if optional args weren't passed
    domain_val = domain if domain else "logsentinel.example.com"
    frontend_val = f"https://{domain_val}"
    
    bucket_val = bucket if bucket else "logsentinel-archive"
    endpoint_val = endpoint if endpoint else "https://s3.us-east-005.backblazeb2.com"
    region_val = region if region else "us-east-005"

    env_content = textwrap.dedent(f"""\
        # ==========================================
        # Core Deployment Configuration
        # ==========================================
        ENVIRONMENT=production
        DOMAIN_NAME={domain_val}
        FRONTEND_URL={frontend_val}

        # ==========================================
        # Security & Auth
        # ==========================================
        JWT_SECRET_KEY={jwt_secret}
        ENCRYPTION_KEY={encryption_key}
        INGEST_API_KEYS={ingest_key}

        # ==========================================
        # Backend Configuration
        # ==========================================
        # --- Database Credentials ---
        POSTGRES_USER=logsentinel
        POSTGRES_PASSWORD={postgres_password}
        POSTGRES_DB=logsentinel_db
        POSTGRES_HOST=timescaledb
        POSTGRES_PORT=5432
        POSTGRES_SSL_MODE=disable

        # --- Valkey / Redis Configuration ---
        REDIS_URL=redis://valkey:6379/0

        # --- Drain3 state ---
        DRAIN3_STATE_PATH=/app/app/state/drain3_state.bin
        DRAIN3_STATE_BACKEND=file

        # --- Valkey Stream & Consumer Group ---
        LOG_STREAM_NAME=logs:stream
        LOG_WORKERS_GROUP=log_workers

        # --- Hot/Cold Archive Architecture (S3 / Backblaze B2) ---
        S3_ENDPOINT_URL={endpoint_val}
        S3_BUCKET_NAME={bucket_val}
        S3_BACKUP_BUCKET=
        S3_ACCESS_KEY_ID=your_b2_key_id
        S3_SECRET_ACCESS_KEY=your_b2_application_key
        S3_REGION={region_val}

        # --- Retention Policies ---
        ARCHIVE_HOT_RETENTION_DAYS=30
        ARCHIVE_LATENESS_GRACE_HOURS=2

        # --- Pipeline & Graph Scoring Configuration ---
        DRAIN3_BATCH_SIZE=500
        DRAIN3_FLUSH_INTERVAL_SECONDS=5.0
        DRAIN3_QUEUE_DRAIN_TIMEOUT_SECONDS=30.0

        GRAPH_SCORING_ENABLED=True
        GRAPH_SCORING_LOOKBACK_SECONDS=180
        GRAPH_SCORING_TIMEOUT_SECONDS=2.0
        GRAPH_SCORING_MAX_ANOMALY_EVENTS=500
        GRAPH_SCORING_MAX_LOG_RECORDS=5000
    """)

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(env_content)
    
    print(f"Successfully generated production environment configuration at: {out_path}")
    print("IMPORTANT: Please open the file and fill in your B2 Access Key and Secret Key.")

def main():
    parser = argparse.ArgumentParser(description="Generate a production .env file with secure secrets.")
    parser.add_argument("--out", type=str, default=".env.prod", help="Path to output the .env file")
    parser.add_argument("--domain", type=str, default="", help="Primary domain name (e.g. logsentinel.com)")
    parser.add_argument("--bucket", type=str, default="", help="S3 / B2 Bucket Name")
    parser.add_argument("--endpoint", type=str, default="", help="S3 / B2 Endpoint URL")
    parser.add_argument("--region", type=str, default="", help="S3 / B2 Region")
    
    args = parser.parse_args()
    
    generate_env(
        out_path=args.out,
        domain=args.domain,
        bucket=args.bucket,
        endpoint=args.endpoint,
        region=args.region
    )

if __name__ == "__main__":
    main()
