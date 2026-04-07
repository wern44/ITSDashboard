# ─── Build stage: install runtime dependencies into a venv ────────────
FROM python:3.13-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

# build-essential is needed to compile any wheels that ship as sdist
# (e.g. pydantic-core on uncommon arches). Removed in the runtime stage.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install runtime dependencies into a fresh venv. We deliberately do NOT
# `pip install .` here — the source code is copied into /app in the
# runtime stage instead. This avoids having two copies of the package.
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install \
        "flask>=3.0" \
        "feedparser>=6.0" \
        "httpx>=0.27" \
        "apscheduler>=3.10" \
        "pydantic>=2.6" \
        "pyyaml>=6.0" \
        "python-dotenv>=1.0"

# ─── Runtime stage: minimal image, non-root user ──────────────────────
FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Non-root user for the application. UID 1000 is the default first user
# on a Debian host, which keeps bind-mounted file permissions sane.
RUN groupadd --system --gid 1000 app \
    && useradd  --system --uid 1000 --gid app --home /app --shell /usr/sbin/nologin app

WORKDIR /app

# Copy the prebuilt venv from the builder stage
COPY --from=builder /opt/venv /opt/venv

# Copy the application source. Python's `python -m its_briefing` will
# find this via cwd because /app is sys.path[0] under `python -m`.
COPY --chown=app:app its_briefing/ ./its_briefing/

# Pre-create mount points so they exist with the correct ownership
# before Docker bind-mounts the host directories on top of them.
RUN mkdir -p /app/config /app/cache && chown -R app:app /app

USER app

EXPOSE 8089

# Healthcheck via the existing /health endpoint. `docker compose ps`
# uses this to report healthy/unhealthy.
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; \
        sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8089/health',timeout=3).status==200 else 1)"

CMD ["python", "-m", "its_briefing"]
