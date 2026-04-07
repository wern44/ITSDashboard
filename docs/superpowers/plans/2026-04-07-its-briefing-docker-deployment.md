# ITS-Briefing Docker Deployment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Package the existing ITS-Briefing app as a Docker image, provide a `docker-compose.yml` for running it on a Debian server, supply an Nginx reverse-proxy vhost template, and document the full deployment workflow + conventions for future apps.

**Architecture:** Multi-stage Docker build (Python 3.13-slim, non-root user). Container binds to `127.0.0.1:8089` on the Debian host; existing Nginx reverse-proxies LAN traffic via plain HTTP. Persistent state (`config/`, `cache/`, `.env`) lives outside the cloned repo under `/srv/apps/its-briefing/`. Ollama runs on a separate LAN server, reached via `OLLAMA_BASE_URL`.

**Tech Stack:** Docker (multi-stage Dockerfile), Docker Compose v2, Nginx (existing on host).

**Spec:** `docs/superpowers/specs/2026-04-07-its-briefing-docker-deployment-design.md`

**Note for the engineer:** No application code is changed by this plan. Only deployment artifacts are added. The existing test suite (22 tests) is not affected and does not need to be re-run between tasks unless explicitly requested.

---

## Task 1: Dockerfile and `.dockerignore`

**Files:**
- Create: `Dockerfile`
- Create: `.dockerignore`

The build is multi-stage: a `builder` stage installs only the runtime dependencies into a venv (no `pip install .` — we keep the source out of the venv to avoid two copies of the package), and a slim `runtime` stage copies the venv plus the source into `/app` and runs as a non-root user.

- [ ] **Step 1: Create `Dockerfile` at the repo root**

```dockerfile
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
```

- [ ] **Step 2: Create `.dockerignore` at the repo root**

```
.git
.gitignore
.gitattributes
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

This keeps the build context small and prevents the local `.env` and `cache/` from leaking into the image.

- [ ] **Step 3: Verify the Dockerfile syntax with `docker build --dry-run` if Docker is available**

Run from `D:/Docs/Coding/ITS_Briefing`:

```bash
docker version 2>/dev/null && docker build --check . || echo "Docker not available — skip"
```

Expected: either `Build check passed` or the `Docker not available — skip` message. Do NOT actually build the image yet (Task 5 does that).

If Docker is not available locally, mark this step complete and continue. The full build will be verified on the deployment server.

- [ ] **Step 4: Commit**

```bash
git add Dockerfile .dockerignore
git commit -m "feat: add Dockerfile and .dockerignore"
```

---

## Task 2: `docker-compose.yml` and `.env.example` update

**Files:**
- Create: `docker-compose.yml`
- Create or restore: `.env.example` (the file is currently missing from disk but is committed in git; this task ensures both the file on disk and a clarifying comment for `OLLAMA_BASE_URL` are in place)

- [ ] **Step 1: Create `docker-compose.yml` at the repo root**

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

    # Bind-mounts: Config (read-only) und Cache (read-write) leben im Host-Dateisystem
    volumes:
      - /srv/apps/its-briefing/config:/app/config:ro
      - /srv/apps/its-briefing/cache:/app/cache

    # .env wird vom Host gelesen, NICHT ins Image gebacken
    env_file:
      - /srv/apps/its-briefing/.env

    # Override FLASK_HOST so the app binds to all interfaces inside the
    # container; the host-side `127.0.0.1:8089:8089` mapping still keeps
    # it accessible only from the host's localhost.
    environment:
      FLASK_HOST: "0.0.0.0"

    # Logs gehen via Docker an json-file mit Rotation
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"
```

- [ ] **Step 2: Restore/update `.env.example` at the repo root**

The file is committed in git but missing from the working tree. Re-create it with the additional clarifying comment on `OLLAMA_BASE_URL`:

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

`FLASK_HOST` stays at `127.0.0.1` for local development. The Compose file overrides it to `0.0.0.0` for the container.

- [ ] **Step 3: Validate the compose file syntax**

If Docker is available locally:

```bash
cd "D:/Docs/Coding/ITS_Briefing"
docker compose config 2>&1 | head -30
```

Expected: prints the resolved compose file with no errors. If you get a warning about the missing `/srv/apps/its-briefing/...` paths or `.env` file, that is expected on the dev machine (those paths only exist on the deployment server) — note it but do not treat it as a failure.

If Docker is not available, run a YAML sanity check via Python:

```bash
source .venv/Scripts/activate
python -c "import yaml; yaml.safe_load(open('docker-compose.yml')); print('ok')"
```

Expected: `ok`.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml .env.example
git commit -m "feat: add docker-compose.yml and update .env.example for Docker deployment"
```

---

## Task 3: Nginx vhost template

**Files:**
- Create: `deploy/nginx-its-briefing.conf`

This file is a template that the user copies to `/etc/nginx/sites-available/its-briefing` on the Debian server.

- [ ] **Step 1: Create the `deploy/` directory and the vhost file**

`deploy/nginx-its-briefing.conf`:

```nginx
# /etc/nginx/sites-available/its-briefing
#
# Reverse-proxy vhost for ITS-Briefing.
# Replace `its-briefing.intern.local` with your actual internal hostname.

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

- [ ] **Step 2: Spot-check the file**

```bash
test -f "D:/Docs/Coding/ITS_Briefing/deploy/nginx-its-briefing.conf" && echo "exists"
grep -c "proxy_pass" "D:/Docs/Coding/ITS_Briefing/deploy/nginx-its-briefing.conf"
```

Expected: `exists` and `1`.

(Full nginx syntax validation requires `nginx -t` running with the file in place on the Debian server — that happens in Task 6.)

- [ ] **Step 3: Commit**

```bash
git add deploy/nginx-its-briefing.conf
git commit -m "feat: add nginx reverse-proxy vhost template"
```

---

## Task 4: `DEPLOYMENT.md`

**Files:**
- Create: `DEPLOYMENT.md`

A self-contained deployment guide a user can follow without needing the spec or plan documents. Documents the directory layout convention, first-time setup, updates, config changes, and how to add a future app under the same `/srv/apps/<app-name>/` pattern.

- [ ] **Step 1: Create `DEPLOYMENT.md` at the repo root**

````markdown
# Deployment Guide — ITS-Briefing on Debian + Docker + Nginx

This guide describes how to deploy ITS-Briefing on a Debian Linux server that already runs Nginx, using Docker for the application and Nginx as a reverse proxy. It also documents the conventions used for adding additional apps to the same server.

## Architecture

```
Browser im LAN
    │  http://its-briefing.intern.local
    ▼
Nginx (Host, schon da)
    │  proxy_pass http://127.0.0.1:8089
    ▼
its-briefing-Container (Docker)
    │
    │  HTTP-Calls
    ▼
Ollama auf separatem Server
    http://<gpu-server>:11434
```

- The container is bound to `127.0.0.1:8089` only — not reachable from the LAN directly.
- Nginx (already running on the host) routes LAN traffic to it via plain HTTP.
- Ollama runs on a separate LAN machine. The container reaches it over the network.
- Persistent state (config, cache, secrets) lives **outside** the cloned repo so updates don't touch user data.

## Server Directory Layout

```
/srv/apps/
└── its-briefing/
    ├── repo/                # git clone of the project (immutable at runtime)
    ├── config/              # bind-mounted into the container, RW from the host
    │   ├── sources.yaml
    │   └── categories.yaml
    ├── cache/               # bind-mounted into the container, persistent briefings
    └── .env                 # runtime settings (Ollama URL etc.) — NOT in git
```

## Prerequisites

On the Debian server:

- Docker Engine + the Compose plugin installed (`apt-get install docker.io docker-compose-plugin`)
- Nginx installed and running (already the case on this server)
- A reachable Ollama instance on the LAN (separate server, with `llama3.1:8b` or another model pulled)
- An internal hostname or IP that resolves to the Debian server

## First-Time Deployment

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

# 4. .env editieren — vor allem OLLAMA_BASE_URL auf den GPU-Server setzen
vim ../.env

# 5. Container bauen + starten
docker compose up -d --build

# 6. Health-Check
docker compose ps                                    # sollte "healthy" zeigen
curl http://127.0.0.1:8089/health                    # JSON-Response
docker compose logs -f --tail=50                     # Live-Logs
# (Strg-C zum Beenden des Log-Tails)

# 7. Nginx-vhost installieren
sudo cp deploy/nginx-its-briefing.conf /etc/nginx/sites-available/its-briefing
sudo ln -s /etc/nginx/sites-available/its-briefing /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# 8. Im Browser aufrufen
# http://its-briefing.intern.local
```

### Resolving the internal hostname

If you use Pi-hole, Unbound, or any other internal DNS server, add an A record for `its-briefing.intern.local` pointing to the Debian server's LAN IP.

If you don't run internal DNS, add a line to `/etc/hosts` on each client machine that should reach the app:

```
192.168.1.10  its-briefing.intern.local
```

## Update Workflow

After pulling new code from upstream:

```bash
cd /srv/apps/its-briefing/repo
git pull
docker compose up -d --build
```

`config/`, `cache/` and `.env` remain untouched because they live outside `repo/`.

## Editing Config (Sources or Categories)

```bash
vim /srv/apps/its-briefing/config/sources.yaml
docker compose -f /srv/apps/its-briefing/repo/docker-compose.yml restart
```

## Manual Briefing Rebuild

Force a fresh briefing right now (without waiting for the 06:00 schedule):

```bash
docker compose -f /srv/apps/its-briefing/repo/docker-compose.yml \
    exec its-briefing python -m its_briefing.generate
```

Or click the **"Rebuild now"** button in the footer of the web UI.

## Logs and Troubleshooting

- Container logs: `docker compose -f /srv/apps/its-briefing/repo/docker-compose.yml logs --tail=200`
- Nginx access log: `tail -f /var/log/nginx/its-briefing.access.log`
- Nginx error log: `tail -f /var/log/nginx/its-briefing.error.log`
- Health endpoint: `curl http://127.0.0.1:8089/health`

If the container is `unhealthy`:
1. Check `docker compose logs` for Python tracebacks
2. Verify `OLLAMA_BASE_URL` in `.env` points at a reachable Ollama server (`curl $OLLAMA_BASE_URL/api/tags` from the host)
3. Verify the `config/` and `cache/` host directories exist and are owned by UID 1000

## Conventions for Additional Apps

When adding a new app to this server, follow the same pattern:

| Element | Convention |
|---|---|
| App directory | `/srv/apps/<app-name>/` |
| Sub-layout | `repo/`, `config/`, `cache/` (or `data/`), `.env` |
| Localhost port | Allocated from pool 8080–8099 (see table below) |
| Compose | `restart: unless-stopped`, `ports: 127.0.0.1:<port>:<port>` |
| Nginx vhost | `/etc/nginx/sites-available/<app-name>` |
| Hostname | `<app-name>.intern.local` |
| Log rotation | `max-size: 10m, max-file: 3` |
| Container user | Non-root, UID 1000 unless overridden |
| Healthcheck | Required if the app exposes one |

### Port Allocation

| Port | App |
|---|---|
| 8089 | its-briefing |
| 8088 | (free) |
| 8090 | (free) |
| 8091 | (free) |
| 8092 | (free) |

Update this table when adding a new app.
````

- [ ] **Step 2: Spot-check the file**

```bash
test -f "D:/Docs/Coding/ITS_Briefing/DEPLOYMENT.md" && wc -l "D:/Docs/Coding/ITS_Briefing/DEPLOYMENT.md"
```

Expected: file exists, around 150–200 lines.

- [ ] **Step 3: Commit**

```bash
git add DEPLOYMENT.md
git commit -m "docs: add deployment guide and conventions for future apps"
```

---

## Task 5: Local Docker build smoke test (optional)

This task verifies the Dockerfile + docker-compose.yml actually build cleanly on the local machine. Skip if Docker is not installed locally — the Debian server is where it ultimately matters (Task 6).

**Prereqs:** Docker Desktop (Windows) or Docker Engine (Linux) running on the dev machine.

- [ ] **Step 1: Check whether Docker is available**

```bash
docker version
```

If this fails (`docker: command not found` or daemon not running), mark this entire task as **DONE_WITH_CONCERNS** with the note "Docker not available locally — verification deferred to server (Task 6)" and move on.

- [ ] **Step 2: Build the image**

```bash
cd "D:/Docs/Coding/ITS_Briefing"
docker build -t its-briefing:latest .
```

Expected: build completes without errors. Final line shows the image SHA. Image size:

```bash
docker images its-briefing:latest
```

Expected: SIZE column shows < 250 MB (per acceptance criterion 12 in the spec).

- [ ] **Step 3: Run the container ad-hoc (without compose) and hit /health**

The compose file references host paths (`/srv/apps/its-briefing/...`) that don't exist on Windows. For local testing, run the image directly without bind mounts:

```bash
docker run --rm -d \
    --name its-briefing-test \
    -p 127.0.0.1:18089:8089 \
    -e FLASK_HOST=0.0.0.0 \
    -e OLLAMA_BASE_URL=http://invalid.local:11434 \
    its-briefing:latest
sleep 5
curl -s http://127.0.0.1:18089/health
docker stop its-briefing-test
```

Expected: `curl` returns JSON containing `"status": "ok"` (the `next_scheduled_run` field will be set; `last_briefing_date` will be `null` since there's no cache).

The container will use port 18089 on the host (not 8089) to avoid conflicting with any locally-running dev server.

- [ ] **Step 4: Verify the container ran as the `app` user**

```bash
docker run --rm its-briefing:latest whoami
```

Expected output: `app`

- [ ] **Step 5: Report**

If all the above succeeded: report DONE.

If the build worked but `/health` returned an error or the container crashed: report DONE_WITH_CONCERNS with the logs from `docker logs its-briefing-test` (run before the `docker stop`).

If Docker isn't available: report DONE_WITH_CONCERNS as noted in Step 1.

No commit — this task only runs verification commands, no files change.

---

## Task 6: Server-side smoke test (manual, deferred to user)

This task is **manual verification on the actual Debian server**. The implementer subagent does not run this — it's a checklist for the user once they pull the changes onto the server.

The implementer should output the checklist below as a final report so the user has it handy.

**Checklist (user runs on the Debian server after `git pull` of this branch):**

- [ ] On the Debian server, ensure Docker Engine + the Compose plugin are installed: `docker version && docker compose version`
- [ ] Create the host directory structure:
  ```bash
  sudo mkdir -p /srv/apps/its-briefing
  sudo chown $USER:$USER /srv/apps/its-briefing
  cd /srv/apps/its-briefing
  git clone <git-url> repo
  cd repo
  mkdir -p ../config ../cache
  cp config/sources.yaml config/categories.yaml ../config/
  cp .env.example ../.env
  ```
- [ ] Edit `/srv/apps/its-briefing/.env` and set `OLLAMA_BASE_URL` to the LAN address of your Ollama server
- [ ] Build and start the container:
  ```bash
  docker compose up -d --build
  ```
- [ ] Wait ~15 seconds, then verify health:
  ```bash
  docker compose ps                                     # STATUS should include "healthy"
  curl http://127.0.0.1:8089/health                     # JSON with "status": "ok"
  curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8089/   # 200
  ```
- [ ] Verify the container runs as the `app` user:
  ```bash
  docker compose exec its-briefing whoami    # → app
  ```
- [ ] Trigger a manual briefing build to test the full pipeline against the real Ollama server:
  ```bash
  docker compose exec its-briefing python -m its_briefing.generate
  ls -la /srv/apps/its-briefing/cache/        # → briefing-YYYY-MM-DD.json should exist
  ```
- [ ] Install the Nginx vhost:
  ```bash
  sudo cp deploy/nginx-its-briefing.conf /etc/nginx/sites-available/its-briefing
  sudo ln -s /etc/nginx/sites-available/its-briefing /etc/nginx/sites-enabled/
  sudo nginx -t                               # → "syntax is ok"
  sudo systemctl reload nginx
  ```
- [ ] Make `its-briefing.intern.local` resolve (DNS or /etc/hosts) on a client machine
- [ ] Open `http://its-briefing.intern.local` in a browser — should see the dark-mode briefing page
- [ ] Verify the daily schedule is registered:
  ```bash
  docker compose logs its-briefing | grep -i "next run"
  ```
  Expected: a log line like `Scheduler started; next run at 2026-04-08 06:00:00+02:00`
- [ ] (After 24 h or by manually rolling the system clock) verify the scheduled run actually fires

---

## Self-Review Checklist (for the plan author)

- All 12 spec sections covered:
  - Section 1 (Purpose) — covered in plan goal
  - Section 2 (Decisions) — reflected in Task 1 + Task 2 file content
  - Section 3 (Architecture) — illustrated in Task 4 DEPLOYMENT.md
  - Section 4 (Server Directory Layout) — Task 4 DEPLOYMENT.md, Task 6 manual setup
  - Section 5 (Dockerfile) — Task 1
  - Section 6 (docker-compose.yml) — Task 2
  - Section 7 (Nginx vhost) — Task 3
  - Section 8 (Deployment Workflow) — Task 4 DEPLOYMENT.md, Task 6 manual checklist
  - Section 9 (Conventions for Future Apps) — Task 4 DEPLOYMENT.md
  - Section 10 (New Files in Repo) — Tasks 1–4 cover all 5 new files (Dockerfile, .dockerignore, docker-compose.yml, deploy/nginx-its-briefing.conf, DEPLOYMENT.md) + .env.example update in Task 2
  - Section 11 (Acceptance Criteria) — Task 5 (criteria 1, 11, 12) and Task 6 (criteria 2–10)
  - Section 12 (Out of Scope) — explicitly not addressed; nothing to do
- All file paths are absolute or unambiguous relative paths
- Every code block is complete (no "fill in details" placeholders)
- Function/file names referenced in later tasks match exactly what earlier tasks created (Dockerfile uses `python -m its_briefing` which is the existing entry point; compose references the Dockerfile and the host paths consistently)
- Commit messages are conventional and reflect what each task added
