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
- **Everything lives inside the cloned repo directory.** Code, config, cache and `.env` are colocated. Bind mounts in `docker-compose.yml` are relative paths (`./config`, `./cache`, `./.env`), so the same compose file works on a developer laptop and on the production server. `git pull` updates the code; `config/`, `cache/` and `.env` are either tracked or ignored, never overwritten.

## Server Directory Layout

The repo *is* the deployment directory. Pick any path you like — the example below uses `/srv/apps/its-briefing`:

```
/srv/apps/its-briefing/         # = git clone of the repo
├── its_briefing/               # app source (tracked)
├── config/
│   ├── sources.yaml            # tracked, edit in place
│   └── categories.yaml         # tracked, edit in place
├── cache/                      # gitignored, created at first start, persists briefings + SQLite DB
├── .env                        # gitignored, copied from .env.example on first deploy
├── .env.example                # tracked
├── docker-compose.yml          # tracked, uses relative bind-mount paths
└── deploy/
    └── nginx-its-briefing.conf # tracked, copied to /etc/nginx/sites-available/ during setup
```

## Prerequisites

On the Debian server:

- Docker Engine + the Compose plugin installed (`apt-get install docker.io docker-compose-plugin`)
- Nginx installed and running (already the case on this server)
- A reachable Ollama or LM Studio instance on the LAN (separate server, with `llama3.1:8b` or another model pulled)
- An internal hostname or IP that resolves to the Debian server

## First-Time Deployment

```bash
# 1. Verzeichnis vorbereiten und Repo direkt dort hin klonen
sudo mkdir -p /srv/apps
sudo chown $USER:$USER /srv/apps
cd /srv/apps
git clone https://github.com/wern44/ITSDashboard.git its-briefing
cd its-briefing

# 2. .env anlegen — vor allem LLM_BASE_URL auf den GPU-Server zeigen lassen
cp .env.example .env
vim .env

# 3. cache-Verzeichnis vorab anlegen, damit Docker es nicht als root-owned mountet
mkdir -p cache

# 4. Container bauen + starten
docker compose up -d --build

# 5. Health-Check
docker compose ps                                    # sollte "healthy" zeigen
curl http://127.0.0.1:8089/health                    # JSON-Response
docker compose logs -f --tail=50                     # Live-Logs
# (Strg-C zum Beenden des Log-Tails)

# 6. Nginx-vhost installieren
sudo cp deploy/nginx-its-briefing.conf /etc/nginx/sites-available/its-briefing
sudo ln -s /etc/nginx/sites-available/its-briefing /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# 7. Im Browser aufrufen
# http://its-briefing.intern.local
```

### Resolving the internal hostname

If you use Pi-hole, Unbound, or any other internal DNS server, add an A record for `its-briefing.intern.local` pointing to the Debian server's LAN IP.

If you don't run internal DNS, add a line to `/etc/hosts` on each client machine that should reach the app:

```
192.168.1.10  its-briefing.intern.local
```

## Local Development on Windows / macOS

The same `docker-compose.yml` works for local testing because all bind-mount paths are relative to the compose file:

```bash
git clone https://github.com/wern44/ITSDashboard.git its-briefing
cd its-briefing
cp .env.example .env             # then edit LLM_BASE_URL etc.
mkdir -p cache
docker compose up -d --build
# Open http://127.0.0.1:8089
```

No `/srv/apps/...` paths required.

## Update Workflow

```bash
cd /srv/apps/its-briefing
git pull
docker compose up -d --build
```

`config/` is tracked, so `git pull` will update `sources.yaml` / `categories.yaml` if upstream changes them — review the diff before pulling if you've made local edits. `cache/` and `.env` are gitignored and are never touched by `git pull`.

## Editing Config (Sources or Categories)

```bash
vim /srv/apps/its-briefing/config/sources.yaml
docker compose -f /srv/apps/its-briefing/docker-compose.yml restart
```

## Manual Briefing Rebuild

Force a fresh briefing right now (without waiting for the 06:00 schedule):

```bash
docker compose -f /srv/apps/its-briefing/docker-compose.yml \
    exec its-briefing python -m its_briefing.generate
```

Or click the **"Rebuild now"** button in the footer of the web UI.

## Logs and Troubleshooting

- Container logs: `docker compose -f /srv/apps/its-briefing/docker-compose.yml logs --tail=200`
- Nginx access log: `tail -f /var/log/nginx/its-briefing.access.log`
- Nginx error log: `tail -f /var/log/nginx/its-briefing.error.log`
- Health endpoint: `curl http://127.0.0.1:8089/health`

If the container is `unhealthy`:
1. Check `docker compose logs` for Python tracebacks
2. Verify `LLM_BASE_URL` in `.env` points at a reachable LLM server (e.g. `curl $LLM_BASE_URL/api/tags` for Ollama)
3. Verify the `cache/` directory exists and is writable by UID 1000 (see below)

### Host UID assumption

The container runs as user `app` (UID 1000). On a default single-user Debian install this matches the operator's host user, so bind-mounted files in `cache/` are readable from the host without permission gymnastics. **Before deploying, verify your host UID:**

```bash
id $USER
# Expected: uid=1000(...) gid=1000(...) ...
```

If your UID is not 1000, files written by the container will appear "owned by nobody" from the host. Two options:

1. **Quickest:** chown the bind-mounted directories to UID 1000:
   ```bash
   sudo chown -R 1000:1000 cache
   sudo chown -R 1000:1000 config
   ```
2. **Cleaner (future):** rebuild the image with a build arg overriding the UID. Not yet implemented in this Dockerfile.

## Conventions for Additional Apps

When adding a new app to this server, follow the same pattern:

| Element | Convention |
|---|---|
| App directory | `/srv/apps/<app-name>/` (= `git clone` target) |
| Sub-layout | Repo at the root; `config/`, `cache/` (or `data/`) and `.env` live alongside `docker-compose.yml` and use **relative** bind-mount paths |
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
