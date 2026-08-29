import base64
import secrets


def generate_secrets():
    # 32 bytes of randomness for the JWT secret (hex encoded)
    jwt_secret = secrets.token_hex(32)

    # 32 bytes of randomness for Fernet encryption key (url-safe base64 encoded)
    encryption_key = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("utf-8")

    # 24 bytes of randomness for Postgres password (url-safe base64 encoded)
    postgres_password = base64.urlsafe_b64encode(secrets.token_bytes(24)).decode(
        "utf-8"
    )

    # 24 bytes of randomness for API key (hex encoded)
    api_key = secrets.token_hex(24)

    print("=" * 60)
    print("🔒 LogSentinel Secure Credentials Generator 🔒")
    print("=" * 60)
    print("\nCopy and paste these values into your .env or .env.prod file:\n")
    print(f"JWT_SECRET_KEY={jwt_secret}")
    print(f"ENCRYPTION_KEY={encryption_key}")
    print(f"INGEST_API_KEYS=default:{api_key}")
    print(f"POSTGRES_PASSWORD={postgres_password}")
    print("\n" + "=" * 60)


if __name__ == "__main__":
    generate_secrets()
