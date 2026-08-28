FROM python:3.11-slim

WORKDIR /app

# Deterministic writable Drain3 state location for the non-root runtime user.
# Operators may override this with a mounted persistent path.
ENV DRAIN3_STATE_PATH=/app/app/state/drain3_state.bin

# Install system dependencies if any are needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Create non-root group and user appuser (UID 10001)
RUN groupadd -g 10001 appuser && \
    useradd -u 10001 -g appuser -s /bin/sh -m appuser

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Set appropriate ownership on /app
RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
