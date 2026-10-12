# python:3.11.16-alpine3.23; immutable multi-arch OCI index.
# Registry manifest digest: sha256:01da514c644bb7635d5a76452190f3181943ede3b2385e71c6f1c10033abb965
# Production-resolved arm64 manifest: sha256:22e3aa8e254b59eb3f6e462ace38c84ca48dc532d66500c78e00d1ddeafb3bf0
FROM python@sha256:01da514c644bb7635d5a76452190f3181943ede3b2385e71c6f1c10033abb965 AS runtime

FROM runtime AS builder

RUN apk add --no-cache \
        build-base \
        cargo \
        freetype-dev \
        gfortran \
        lapack-dev \
        libffi-dev \
        linux-headers \
        openblas-dev \
        openssl-dev \
        postgresql16-dev \
        python3-dev \
        rust \
        zeromq-dev

COPY backend/requirements.txt /tmp/requirements.txt

RUN python -m pip install --no-cache-dir \
        'setuptools>=78.1.1' \
        'wheel>=0.46.2' && \
    python -m pip install --no-cache-dir --prefix=/install -r /tmp/requirements.txt

FROM runtime

WORKDIR /app

# Deterministic writable Drain3 state location for the non-root runtime user.
# Operators may override this with a mounted persistent path.
ENV DRAIN3_STATE_PATH=/app/app/state/drain3_state.bin

RUN apk add --no-cache \
        bash \
        ca-certificates \
        libffi \
        libgcc \
        libgomp \
        libpq \
        libstdc++ \
        openblas \
        openssl \
        postgresql16-client \
        tzdata && \
    addgroup -g 10001 -S appuser && \
    adduser -u 10001 -S -D -G appuser -h /home/appuser appuser && \
    python -m pip uninstall -y setuptools wheel || true && \
    rm -rf \
        /root/.cache \
        /usr/local/bin/pip \
        /usr/local/bin/pip3 \
        /usr/local/bin/pip3.11 \
        /usr/local/lib/python3.11/site-packages/pip \
        /usr/local/lib/python3.11/site-packages/pip-*.dist-info \
        /usr/local/lib/python3.11/site-packages/setuptools \
        /usr/local/lib/python3.11/site-packages/setuptools-*.dist-info \
        /usr/local/lib/python3.11/site-packages/wheel \
        /usr/local/lib/python3.11/site-packages/wheel-*.dist-info

COPY --from=builder /install/ /usr/local/
COPY backend/ /app/
COPY scripts/ /repo/scripts/
COPY config/ /repo/config/

RUN chown -R appuser:appuser /app /home/appuser

USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"

CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
