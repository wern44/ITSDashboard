---
title: ITS-Briefing — Docker Deployment behind existing Nginx on Debian
date: 2026-04-07
status: approved
---

# ITS-Briefing — Docker Deployment Design

## 1. Purpose

Package the ITS-Briefing Python application as a Docker container and document a repeatable deployment workflow for hosting it (and future apps) behind an existing Nginx reverse proxy on a Debian Linux server. The deployment is for an internal LAN — no public exposure, no Let's Encrypt, no auth.

The target server already runs Nginx as a system package, presumably with Certbot for any public-facing services. This design adds Docker as a sibling tool: each app lives in its own container, binds to a localhost port, and Nginx routes internal LAN traffic to it via `proxy_pass`.

The design also establishes conventions for placing additional apps under the same `/srv/apps/<app-name>/` pattern so the server can grow into a small self-hosted "platform" without additional tooling.

## 2. Decisions Reference

| Decision | Choice |
|---|---|
| Multi-app architecture | Docker per app + existing Nginx as reverse proxy |
| Image base | `python:3.13-slim`, multi-stage build |
| Container user | Non-root `app` (UID 1000) |
| Port binding | `127.0.0.1:8089:8089` — not publicly exposed |
| Ollama placement | External (separate server, reachable via LAN) |
| Domain strategy | Internal LAN hostname (e.g. `its-briefing.intern.local`) |
| Transport | Plain HTTP (internal LAN, no auth on the app) |
| Persistent state | Bind-mounted `config/` (read-only) and `cache/` (read-write) under `/srv/apps/its-briefing/` |
| `.env` location | `/srv/apps/its-briefing/.env` — outside the cloned repo |
| Restart policy | `unless-stopped` (auto-restart on crash + reboot) |
| Log management | Docker `json-file` driver, 10 MB × 3 rotation |
| Update workflow | `git pull && docker compose up -d --build` |

## 3. Architecture Overview

ITS-Briefing runs as a single container on the Debian host. Nginx (already running on the host) proxies LAN traffic to the container via a localhost-only port binding. Ollama runs on a separate machine on the LAN and is reached over the network.

```
Browser im LAN
    │  http://its-briefing.intern.local
    ▼
Nginx (Host, schon da)
    │  proxy_pass http://127.0.0.1:8089
    ▼
its-briefing-Container (Docker)
    │  ├─ liest config aus /app/config (bind-mount → /srv/apps/its-briefing/config)
    │  ├─ schreibt cache nach /app/cache (bind-mount → /srv/apps/its-briefing/cache)
    │  ├─ liest .env (vom Compose-Service als env_file gelesen)
    │  └─ APScheduler läuft im Container, generiert täglich um 06:00 Europe/Berlin
    │
    │  HTTP-Calls
    ▼
Ollama auf separatem Server
    http://<gpu-server>:11434
```

The container itself is stateless — every persistent file (config, cache, secrets) lives on the host and is bind-mounted in. This means a `docker compose down && up -d --build` after a code update never touches user data.

## 4. Server Directory Layout

Convention for ITS-Briefing and all future apps on this server:

```
/srv/apps/
├── its-briefing/
│   ├── repo/                        # git clone of the project (immutable at runtime)
│   │   ├── Dockerfile
│   │   ├── docker-compose.yml
│   │   ├── its_briefing/...
│   │   └── ...
│   ├── config/                      # bind-mounted into container (RO), editable without rebuild
│   │   ├── sources.yaml
│   │   └── categories.yaml
│   ├── cache/                       # bind-mounted into container (RW), persistent briefings
│   │   └── briefing-2026-04-07.json
│   └── .env                         # runtime settings (Ollama URL etc.) — NOT in git
└── <future-app>/
    └── ...
```

**Why this layout:**

- `repo/` is immutable at runtime; updates happen via `git pull`. The Dockerfile builds the image from this directory.
- `config/` and `cache/` live outside `repo/` so updates never overwrite user data.
- `.env` is outside the repo so secrets and host-specific values don't end up in git.
- The same pattern applies to every future app under `/srv/apps/<app-name>/`. New apps drop in without additional infrastructure decisions.

## 5. Dockerfile

Multi-stage build, Python 3.13-slim, non-root user, healthcheck.

```dockerfile
# ─── Build stage: install dependencies into a venv ────────────────────
FROM python:3.13-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

# System deps for any wheels that need compilation (kept minimal)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY its_briefing/ ./its_briefing/

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && /opt/venv/bin/pip install .

# ─── Runtime stage: minimal image, non-root user ──────────────────────
FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

# Non-root user
RUN groupadd --system --gid 1000 app \
    && useradd  --system --uid 1000 --gid app --home /app --shell /usr/sbin/nologin app

WORKDIR /app

# Copy the prebuilt venv and the source
COPY --from=builder /opt/venv /opt/venv
COPY --chown=app:app its_briefing/ ./its_briefing/

# Mount points (will be bind-mounted at runtime, but must exist + be owned by app)
RUN mkdir -p /app/config /app/cache && chown -R app:app /app

USER app

EXPOSE 8089

# Healthcheck so docker compose / nginx know if the app is alive
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; \
        sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8089/health',timeout=3).status==200 else 1)"

CMD ["python", "-m", "its_briefing"]
```

**Design notes:**

- **Multi-stage build:** the `builder` stage holds `build-essential` for compiling wheels (e.g. `pydantic-core`); the `runtime` stage doesn't inherit it and stays small (~150 MB instead of ~500 MB).
- **`python:3.13-slim`** matches the local development version. The slim variant saves ~700 MB vs the full image.
- **Non-root user (`app`, UID 1000):** standard practice. UID 1000 typically matches the first regular user on a Debian host, which makes bind-mounted `cache/` files readable from the host without permission gymnastics. If the host user has a different UID, the value can be overridden via build arg in a follow-up.
- **`/app/config` and `/app/cache` exist in the image:** otherwise Docker creates them as `root`-owned at bind-mount time. Pre-creating them with `app` ownership avoids permission errors at runtime.
- **Healthcheck via `/health`:** the existing endpoint. `docker compose ps` will show `healthy/unhealthy` status.
- **`CMD ["python", "-m", "its_briefing"]`:** the existing entry point — starts Flask + scheduler. No change to the application code is required.

**Explicitly NOT included:**

- No `gunicorn`/`uvicorn` in front. For a single-user internal-LAN app, the Flask dev server is sufficient.
- No Tini/`init` as PID 1. Python handles SIGTERM correctly via `__main__.py`'s signal handlers.
- No embedded Ollama. It runs on a separate server.

## 6. docker-compose.yml

```yaml
services:
  its-briefing:
    build:
      context: .
      dockerfile: Dockerfile
    image: its-briefing:latest
    container_name: its-briefing
    restart: unless-stopped

    # Port nur an localhost — Nginx auf dem Host macht das Routing
    ports:
      - "127.0.0.1:8089:8089"

    # Bind-mounts: Config und Cache leben im Host-Dateisystem
    volumes:
      - /srv/apps/its-briefing/config:/app/config:ro
      - /srv/apps/its-briefing/cache:/app/cache

    # .env wird vom Host gelesen, NICHT ins Image gebacken
    env_file:
      - /srv/apps/its-briefing/.env

    # Override FLASK_HOST so the app binds to all interfaces inside the
    # container; the host-side `127.0.0.1:8089:8089` mapping still keeps
    # it accessible only from the host.
    environment:
      FLASK_HOST: "0.0.0.0"

    # Logs gehen via Docker an json-file mit Rotation
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
```

**Design notes:**

- **`ports: 127.0.0.1:8089:8089`** — the container is NOT reachable from outside the host. Only the host's Nginx can reach it. This is the central security detail of the reverse-proxy pattern.
- **`config:ro`** — config files are read-only inside the container. Editing happens on the host (`vim /srv/apps/its-briefing/config/sources.yaml`); a `docker compose restart` picks up the change.
- **`cache` (RW)** — the container writes briefings here. Survives `docker compose down`.
- **`env_file: /srv/apps/its-briefing/.env`** — the `.env` lives outside the cloned repo. This means it never conflicts with `git pull` and can be managed independently of the source.
- **`restart: unless-stopped`** — the container comes back up automatically after a server reboot or crash. Exactly what's wanted for a central app host.
- **Log rotation** — `max-size: 10m, max-file: 3` keeps container logs from filling the disk.

### Adjusted `.env.example`

Since Ollama runs on a separate server in the Docker deployment, the in-repo `.env.example` gets a clarifying comment on `OLLAMA_BASE_URL`:

```
# In a Docker deployment, set this to your Ollama server's LAN address
# (e.g. http://192.168.1.50:11434). For local development, the default works.
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=llama3.1:8b
TIMEZONE=Europe/Berlin
SCHEDULE_HOUR=6
SCHEDULE_MINUTE=0
FLASK_HOST=127.0.0.1
FLASK_PORT=8089
LOG_LEVEL=INFO
```

`FLASK_HOST` stays at `127.0.0.1` for local development (more secure default). The Docker Compose file overrides it to `0.0.0.0` for the container, so users don't have to remember this in their production `.env`. The host-side `127.0.0.1:8089:8089` port mapping ensures the container is still only accessible from the host's localhost.

## 7. Nginx Reverse-Proxy Configuration

A vhost file deposited at `/etc/nginx/sites-available/its-briefing` and symlinked to `sites-enabled/`.

```nginx
# /etc/nginx/sites-available/its-briefing
server {
    listen 80;
    listen [::]:80;
    server_name its-briefing.intern.local;

    # Logging — eigene Files pro App, einfach zu greppen
    access_log /var/log/nginx/its-briefing.access.log;
    error_log  /var/log/nginx/its-briefing.error.log;

    # Body-Limit knapp halten — die App nimmt keine großen Uploads an
    client_max_body_size 1m;

    location / {
        proxy_pass         http://127.0.0.1:8089;
        proxy_http_version 1.1;

        # Standard-Header, damit Flask die Original-Request-Infos sieht
        proxy_set_header Host              $host;
        proxy_set_header X-Real-IP         $remote_addr;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # Längeres Timeout für POST /generate (Pipeline läuft ggf. 1-2 Min)
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
}
```

**Design notes:**

- **`server_name its-briefing.intern.local`** — placeholder; replaced by the real internal hostname during deployment. The `DEPLOYMENT.md` documents the two ways to make the name resolve: an internal DNS server (Pi-hole, Unbound, router-level) or a per-client `/etc/hosts` entry.
- **`proxy_read_timeout 300s`** — critical for the `POST /generate` endpoint. The pipeline can take 1–2 minutes (50 articles × Ollama latency). The default 60s would yield a 504 Gateway Timeout while the pipeline is still running. 5 minutes gives generous headroom.
- **`client_max_body_size 1m`** — defensive. The app accepts no uploads.
- **Per-app log files** — a single `/var/log/nginx/access.log` would mix all apps together; per-app files make debugging easier.
- **No HTTPS block** — plain HTTP is the chosen transport. If TLS is wanted later, a second `server` block with `listen 443 ssl` and certificate paths can be added without touching the rest.

**Activation:**

```bash
sudo ln -s /etc/nginx/sites-available/its-briefing /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

## 8. Deployment Workflow

### First-time deployment (one-time)

```bash
# 1. Verzeichnisse anlegen
sudo mkdir -p /srv/apps/its-briefing
sudo chown $USER:$USER /srv/apps/its-briefing
cd /srv/apps/its-briefing

# 2. Repo klonen
git clone <git-url> repo
cd repo

# 3. Persistente Verzeichnisse + Initial-Config
mkdir -p ../config ../cache
cp config/sources.yaml config/categories.yaml ../config/
cp .env.example ../.env
# .env editieren — vor allem OLLAMA_BASE_URL auf den GPU-Server setzen
vim ../.env

# 4. Container bauen + starten
docker compose up -d --build

# 5. Health-Check
docker compose ps                                    # sollte "healthy" zeigen
curl http://127.0.0.1:8089/health                    # JSON-Response
docker compose logs -f --tail=50                     # Live-Logs

# 6. Nginx-vhost installieren
sudo cp deploy/nginx-its-briefing.conf /etc/nginx/sites-available/its-briefing
sudo ln -s /etc/nginx/sites-available/its-briefing /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# 7. Im Browser aufrufen
# http://its-briefing.intern.local
```

### Update workflow (after code changes upstream)

```bash
cd /srv/apps/its-briefing/repo
git pull
docker compose up -d --build
```

`config/`, `cache/` and `.env` remain untouched because they live outside `repo/`.

### Config changes (add a source or category)

```bash
vim /srv/apps/its-briefing/config/sources.yaml
docker compose -f /srv/apps/its-briefing/repo/docker-compose.yml restart
```

### Manual briefing rebuild from inside the container

```bash
docker compose -f /srv/apps/its-briefing/repo/docker-compose.yml \
    exec its-briefing python -m its_briefing.generate
```

## 9. Conventions for Future Apps

These are documented in the `DEPLOYMENT.md` file in the repo so any future app added under `/srv/apps/<app-name>/` follows the same pattern.

| Element | Convention |
|---|---|
| App directory | `/srv/apps/<app-name>/` |
| Sub-layout | `repo/`, `config/`, `cache/` (or `data/`), `.env` |
| Localhost port | Allocated from pool 8080–8099 (tracked in `DEPLOYMENT.md`) |
| Compose | `restart: unless-stopped`, `ports: 127.0.0.1:<port>:<port>` |
| Nginx vhost | `/etc/nginx/sites-available/<app-name>` |
| Hostname | `<app-name>.intern.local` |
| Log rotation | `max-size: 10m, max-file: 3` |
| Container user | Non-root, UID 1000 unless overridden |
| Healthcheck | Required if the app exposes one |

The port allocation table at the top of `DEPLOYMENT.md`:

```
8089 — its-briefing
8088 — (free)
8090 — (free)
...
```

## 10. New Files in the Repository

```
ITS_Briefing/
├── Dockerfile                       # NEW
├── docker-compose.yml               # NEW
├── DEPLOYMENT.md                    # NEW (full deployment guide + conventions)
├── deploy/
│   └── nginx-its-briefing.conf      # NEW (vhost template)
├── .dockerignore                    # NEW (excludes cache/, .venv/, .env, .git, tests)
└── (rest unchanged)
```

### `.dockerignore` content

```
.git
.gitignore
.venv
venv
__pycache__
*.py[cod]
*.egg-info
.pytest_cache
cache/
.env
docs/
tests/
README.md
DEPLOYMENT.md
.dockerignore
Dockerfile
docker-compose.yml
deploy/
```

This keeps the build context small and avoids accidentally leaking the local `.env` or `cache/` into the image.

### `.env.example` adjustment

The existing `.env.example` is updated with a comment indicating that `OLLAMA_BASE_URL` must point to the external Ollama server in a Docker deployment, and `FLASK_HOST` must be `0.0.0.0` inside the container. The local-development defaults are kept as fallbacks.

## 11. Acceptance Criteria

1. `docker compose build` builds the image without errors on Linux/macOS/WSL.
2. `docker compose up -d` starts the container; after 15 s `docker compose ps` reports `healthy`.
3. `curl http://127.0.0.1:8089/health` returns JSON with `"status": "ok"`.
4. `curl http://127.0.0.1:8089/` returns HTTP 200 with the "Briefing not yet generated" page (or the latest briefing if one exists).
5. The container writes to `cache/` on the host: `docker compose exec its-briefing python -m its_briefing.generate` produces a file under `/srv/apps/its-briefing/cache/`.
6. `docker compose down && docker compose up -d` preserves all `cache/` contents.
7. `docker compose down && docker compose up -d --build` after a code change deploys the new version without losing cache.
8. Editing `config/sources.yaml` on the host and running `docker compose restart` makes the new sources active in the next pipeline run.
9. `nginx -t` reports `syntax is ok` for the supplied vhost file.
10. With a working DNS or `/etc/hosts` entry, `http://its-briefing.intern.local` reaches the app from another machine on the LAN.
11. The container runs as the `app` user (UID 1000), not root: `docker compose exec its-briefing whoami` returns `app`.
12. The image is < 250 MB on disk (`docker images its-briefing:latest`).

## 12. Out of Scope

Explicitly NOT addressed by this design:

- Production WSGI server (gunicorn/uvicorn) — Flask dev server is sufficient for single-user internal LAN
- HTTPS / TLS termination — plain HTTP per Section 4 decision
- Authentication / authorization — none on the app, no add-on layer planned
- Container registry — image is built on the deployment server, not pushed
- CI/CD — manual `git pull && docker compose up -d --build` workflow
- Centralized log aggregation — local Docker `json-file` driver is enough
- Backups of `cache/` — JSON files are regenerable from the source feeds; no backup needed
- Multi-server orchestration — one Debian server, no Swarm/Kubernetes
- Resource limits — no `cpus`/`memory` caps in compose; the app's footprint is well under typical container defaults
