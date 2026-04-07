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
mkdir -p /srv/apps/its-briefing/config /srv/apps/its-briefing/cache
cp config/sources.yaml     /srv/apps/its-briefing/config/
cp config/categories.yaml  /srv/apps/its-briefing/config/
cp .env.example            /srv/apps/its-briefing/.env

# 4. .env editieren — vor allem OLLAMA_BASE_URL auf den GPU-Server setzen
vim /srv/apps/its-briefing/.env

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

### Host UID assumption

The container runs as user `app` (UID 1000). On a default single-user Debian install this matches the operator's host user, so bind-mounted files in `cache/` are readable from the host without permission gymnastics. **Before deploying, verify your host UID:**

```bash
id $USER
# Expected: uid=1000(...) gid=1000(...) ...
```

If your UID is not 1000, files written by the container will appear "owned by nobody" from the host. Two options:

1. **Quickest:** add yourself to a group with GID 1000 and adjust the host directory permissions:
   ```bash
   sudo chown -R 1000:1000 /srv/apps/its-briefing/cache
   sudo chown -R 1000:1000 /srv/apps/its-briefing/config
   ```
2. **Cleaner (future):** rebuild the image with a build arg overriding the UID. Not yet implemented in this Dockerfile.

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
