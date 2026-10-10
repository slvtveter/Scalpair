# ---------- Stage 1: builder ----------
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install -r requirements.txt

# ---------- Stage 2: runtime ----------
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# curl + gosu for the healthcheck and privilege drop
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl gosu \
    && rm -rf /var/lib/apt/lists/*

# non-root runtime user
RUN groupadd -r app && useradd -r -g app -d /srv app \
    && mkdir -p /srv/data \
    && chown -R app:app /srv

WORKDIR /srv
COPY --from=builder /opt/venv /opt/venv
COPY app ./app
COPY pytest.ini ./
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# entrypoint chowns the data volume (pre-existing volumes may be root-owned)
# and drops privileges to `app`
ENTRYPOINT ["/entrypoint.sh"]

EXPOSE 8000

HEALTHCHECK --interval=20s --timeout=5s --start-period=25s --retries=4 \
    CMD curl -sf http://localhost:8000/api/v1/health || exit 1

# single worker: the in-memory market state is process-local by design
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*", "--log-level", "info"]
