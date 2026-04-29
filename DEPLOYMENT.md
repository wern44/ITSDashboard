# Deployment Guide — ITS-Briefing on Debian + Docker

This guide describes how to deploy ITS-Briefing on a Debian Linux server using Docker. By default the app is published directly on the LAN at port 8089; an optional Nginx reverse proxy is documented at the end. It also documents the conventions used for adding additional apps to the same server.

## Architecture

```
Browser im LAN
    │  http://<server-ip>:8089
    ▼
its-briefing-Container (Docker, Port 8089 auf allen Interfaces)
    │
    │  HTTP-Calls
    ▼
Ollama auf separatem Server
    http://<gpu-server>:11434
```

- The container publishes port 8089 on **all host interfaces** (`"8089:8089"` in `docker-compose.yml`) — directly reachable from the LAN.
- There is no auth in front of the app. On untrusted networks, gate access with a host firewall (`ufw`/`nftables`) or put Nginx in front (see "Optional: Nginx reverse proxy" below).
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
- A reachable Ollama or LM Studio instance on the LAN (separate server, with `llama3.1:8b` or another model pulled)
- LAN access to the server's IP from the clients that should reach the app
- (Optional) Nginx, only if you want a reverse proxy in front — see the optional section at the end

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
curl http://127.0.0.1:8089/health                    # JSON-Response vom Host selbst
docker compose logs -f --tail=50                     # Live-Logs
# (Strg-C zum Beenden des Log-Tails)

# 6. Im Browser aufrufen — direkt über die LAN-IP
# http://<server-ip>:8089
```

The published mapping is `"8089:8089"`, so the app is reachable from any LAN client at `http://<server-ip>:8089/` once step 4 succeeds. No DNS, no Nginx, no extra setup.

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
- Health endpoint (from the host): `curl http://127.0.0.1:8089/health`
- Health endpoint (from a LAN client): `curl http://<server-ip>:8089/health`
- Listening sockets: `ss -ltn | grep 8089` — should show `0.0.0.0:8089` (or `*:8089`). If it shows `127.0.0.1:8089`, the port mapping in `docker-compose.yml` is still loopback-only.
- Nginx logs (only if the optional reverse proxy is enabled): `tail -f /var/log/nginx/its-briefing.access.log` / `.error.log`

If the container is `unhealthy`:
1. Check `docker compose logs` for Python tracebacks
2. Verify `LLM_BASE_URL` in `.env` points at a reachable LLM server (e.g. `curl $LLM_BASE_URL/api/tags` for Ollama)
3. Verify the `cache/` directory exists and is writable by UID 1000 (see below)

If the page does not load from a LAN client:
1. Confirm the container is up and healthy from the host (`curl http://127.0.0.1:8089/health`).
2. Confirm port 8089 is published on all interfaces (`ss -ltn | grep 8089` shows `0.0.0.0:8089`).
3. Check the host firewall — `ufw status` / `nft list ruleset` — and allow inbound 8089 if needed (e.g. `sudo ufw allow from 192.168.0.0/16 to any port 8089`).

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

## Optional: Nginx reverse proxy

If you want logging, request-size limits, longer proxy timeouts, or a clean hostname instead of `:8089` in URLs, put Nginx in front:

```bash
# 1. Container nur noch an Loopback exposen — sonst würden zwei Wege parallel offenstehen
sed -i 's/^      - "8089:8089"/      - "127.0.0.1:8089:8089"/' docker-compose.yml
docker compose up -d                                       # container neu binden

# 2. vhost installieren
sudo cp deploy/nginx-its-briefing.conf /etc/nginx/sites-available/its-briefing
sudo ln -s /etc/nginx/sites-available/its-briefing /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx

# 3. Im Browser aufrufen
# http://its-briefing.intern.local
```

The vhost listens on port 80 with `server_name its-briefing.intern.local` and `proxy_pass http://127.0.0.1:8089`. It also raises `proxy_read_timeout` to 300s so the synchronous `POST /generate` doesn't get cut off. Edit `server_name` in `deploy/nginx-its-briefing.conf` if you want a different hostname or `192.168.x.y` instead of an internal name.

### Resolving the internal hostname (only if using Nginx with a hostname)

If you use Pi-hole, Unbound, or any other internal DNS server, add an A record for `its-briefing.intern.local` pointing to the Debian server's LAN IP.

If you don't run internal DNS, add a line to `/etc/hosts` on each client machine that should reach the app:

```
192.168.1.10  its-briefing.intern.local
```

## Conventions for Additional Apps

When adding a new app to this server, pick **one** access pattern explicitly and stick to it:

- **Direct LAN exposure** (the current ITS-Briefing pattern): `ports: "<port>:<port>"`, no Nginx vhost. Fine on a trusted internal LAN; gate with a host firewall otherwise.
- **Nginx-fronted** (the original convention): `ports: "127.0.0.1:<port>:<port>"`, Nginx vhost on port 80, hostname `<app-name>.intern.local`. Use this when you want logging/timeouts/auth headers in front.

Don't mix the two for the same app — having both bindings live at once doubles the surface area.

| Element | Convention |
|---|---|
| App directory | `/srv/apps/<app-name>/` (= `git clone` target) |
| Sub-layout | Repo at the root; `config/`, `cache/` (or `data/`) and `.env` live alongside `docker-compose.yml` and use **relative** bind-mount paths |
| Port | Allocated from pool 8080–8099 (see table below) |
| Compose | `restart: unless-stopped`; `ports: "<port>:<port>"` for direct LAN exposure, or `"127.0.0.1:<port>:<port>"` if Nginx-fronted |
| Nginx vhost | `/etc/nginx/sites-available/<app-name>` (only if Nginx-fronted) |
| Hostname | `<app-name>.intern.local` (only if Nginx-fronted) |
| Log rotation | `max-size: 10m, max-file: 3` |
| Container user | Non-root, UID 1000 unless overridden |
| Healthcheck | Required if the app exposes one |

### Port Allocation

| Port | App | Access pattern |
|---|---|---|
| 8089 | its-briefing | Direct LAN exposure |
| 8088 | (free) | — |
| 8090 | (free) | — |
| 8091 | (free) | — |
| 8092 | (free) | — |

Update this table when adding a new app.
