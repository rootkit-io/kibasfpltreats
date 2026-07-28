# Production Deployment — DigitalOcean VPS (Ubuntu 24.04 / 22.04)

24/7 deployment of the full stack on a single Droplet using Docker Compose,
with **Caddy** as the only public entrypoint (automatic HTTPS via Let's
Encrypt).

## Topology

```
                    Internet
                       │
              ┌────────▼────────┐
              │  caddy :80/:443 │   ← only service with host ports
              └────────┬────────┘
      ┌─────────┬──────┴────┬──────────────┐
      ▼         ▼           ▼              ▼
kibasfpltreats app.…      admin.…        api.…
      │         │           │              │
 landing:80  dashboard:3000  admin:3000  backend:8000
                                           │
                              ┌────────────┼──────────┐
                              ▼            ▼          │
                          db:5432     redis:6379      │
                          (internal only, no host ports)
```

| Public host | Compose service | Internal port |
|---|---|---|
| `kibasfpltreats.com` | `landing` | 80 |
| `app.kibasfpltreats.com` | `dashboard` | 3000 |
| `admin.kibasfpltreats.com` | `admin` | 3000 |
| `api.kibasfpltreats.com` | `backend` | 8000 |
| `www.kibasfpltreats.com` | redirect → apex | — |

## Prerequisites

- A DigitalOcean Droplet: Ubuntu 24.04 LTS (or 22.04), **2 GB+ RAM**
  (the backend image bakes in ML models; 1 GB Droplets OOM during builds —
  if you must use one, add swap first, see Troubleshooting).
- A registered domain with DNS managed somewhere you can add A records.
- A Clerk production instance (https://dashboard.clerk.com).

---

## 1. DNS A records

At your DNS provider, point the apex and every subdomain at the Droplet's
public IPv4 (get it from the DigitalOcean control panel):

| Type | Hostname | Value | TTL |
|---|---|---|---|
| A | `@` | `<droplet-ip>` | 3600 |
| A | `www` | `<droplet-ip>` | 3600 |
| A | `app` | `<droplet-ip>` | 3600 |
| A | `admin` | `<droplet-ip>` | 3600 |
| A | `api` | `<droplet-ip>` | 3600 |

Verify propagation **before** starting the stack (Caddy requests certificates
on boot; if DNS isn't live yet, issuance fails and retries burn Let's Encrypt
rate limits):

```bash
dig +short kibasfpltreats.com api.kibasfpltreats.com app.kibasfpltreats.com admin.kibasfpltreats.com
# every line should print the Droplet IP
```

## 2. SSH into the Droplet

```bash
ssh root@<droplet-ip>
```

Recommended first steps (non-root user + firewall):

```bash
adduser deploy && usermod -aG sudo deploy
rsync -a ~/.ssh /home/deploy/ && chown -R deploy:deploy /home/deploy/.ssh
ufw allow OpenSSH && ufw allow 80/tcp && ufw allow 443/tcp && ufw allow 443/udp
ufw enable
su - deploy
```

(443/udp is for HTTP/3; optional but Caddy serves it.)

## 3. Install Docker + Compose plugin

Official Docker apt repository (Ubuntu's default packages are stale):

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

# run docker without sudo (re-login afterwards)
sudo usermod -aG docker $USER && newgrp docker

docker --version && docker compose version   # sanity check
```

## 4. Clone the repository

```bash
cd /opt
sudo mkdir -p kibasfpltreats && sudo chown $USER: kibasfpltreats
git clone https://github.com/<your-org>/kibasfpltreats.git kibasfpltreats
cd kibasfpltreats
```

(Private repo: use a deploy key or fine-grained PAT.)

## 5. Create the production `.env`

```bash
cp .env.example .env
nano .env
```

Every variable, what it does, and where it comes from:

| Variable | Required | Purpose / source |
|---|---|---|
| `DOMAIN` | yes | Root domain Caddy serves (`kibasfpltreats.com`) |
| `ACME_EMAIL` | yes | Let's Encrypt registration email |
| `ADMIN_API_TOKEN` | **yes** | Shared secret gating the Admin API; the admin panel BFF sends it upstream. Generate: `openssl rand -hex 32` |
| `POSTGRES_DB` / `POSTGRES_USER` | yes | Database name/user (defaults `fpl`) |
| `POSTGRES_PASSWORD` | **yes** | Strong password: `openssl rand -hex 24`. Postgres is internal-only, but never keep the default |
| `NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY` | **yes** | Clerk → API Keys → Publishable key (`pk_live_…`) |
| `CLERK_SECRET_KEY` | **yes** | Clerk → API Keys → Secret key (`sk_live_…`). Server-side only |
| `CLERK_JWKS_URL` | **yes** | Clerk → API Keys → Advanced → JWKS URL. The backend verifies dashboard JWTs against it; unset ⇒ public API answers 503 |
| `CLERK_AUDIENCE` | recommended | JWT `aud` claim to enforce (your Clerk Frontend API URL). Unset ⇒ audience check skipped |

`DATABASE_URL` and `REDIS_URL` are **composed inside docker-compose.yml**
from the values above (`…@db:5432/…`, `redis://redis:6379/0`) — do not set
them manually.

Also in the Clerk production instance: add `https://app.kibasfpltreats.com`
to the allowed origins/domains, and set your sign-in/out redirect URLs.

Lock the file down:

```bash
chmod 600 .env
```

## 6. Launch

```bash
docker compose -f docker-compose.yml up -d --build
```

> **Why `-f docker-compose.yml` explicitly?** The repo ships a
> `docker-compose.override.yml` that re-exposes dev ports (8000/3000/3001/8080)
> for local work. Plain `docker compose up` auto-merges it; pinning `-f`
> skips the override so **Caddy is the only public listener** in production.

First build takes several minutes (backend image bakes models + data).
Watch it come up:

```bash
docker compose -f docker-compose.yml ps          # all services Up / healthy
docker compose -f docker-compose.yml logs -f caddy   # watch cert issuance
```

Caddy logs `certificate obtained successfully` per domain on first boot.

## 7. Verify

```bash
curl -I  https://kibasfpltreats.com                     # 200, landing
curl -I  https://app.kibasfpltreats.com                 # 200/307 (Clerk sign-in)
curl -I  https://admin.kibasfpltreats.com               # 200, admin panel
curl -s  https://api.kibasfpltreats.com/api/v1/health   # {"status":"ok",...}
curl -s  https://api.kibasfpltreats.com/api/v1/public/projections/latest
# -> 401 {"detail":"authentication required"}  ← correct: Clerk JWT required
curl -sI http://kibasfpltreats.com | head -1            # 308 redirect to https
```

Then in a browser: sign in at `app.<domain>` (Clerk), confirm the grid loads;
publish a run from `admin.<domain>` and confirm it appears.

## 8. Operations

```bash
# deploy an update
cd /opt/kibasfpltreats && git pull
docker compose -f docker-compose.yml up -d --build

# logs / status
docker compose -f docker-compose.yml logs -f backend
docker compose -f docker-compose.yml ps

# stop (volumes preserved: pgdata, redisdata, caddy_data)
docker compose -f docker-compose.yml down

# database backup (cron this nightly)
docker compose -f docker-compose.yml exec db \
  pg_dump -U fpl -d fpl | gzip > /opt/backups/fpl-$(date +%F).sql.gz

# restore
gunzip -c /opt/backups/fpl-YYYY-MM-DD.sql.gz | \
  docker compose -f docker-compose.yml exec -T db psql -U fpl -d fpl
```

**Volumes that must survive:** `pgdata` (all projection runs), `caddy_data`
(TLS certificates + ACME account — deleting it forces re-issuance and Let's
Encrypt limits duplicate certs to 5/week). `redisdata` is disposable cache.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Caddy loops `obtaining certificate… error` | DNS not propagated, or ports 80/443 blocked. Check `dig`, `ufw status`, and DigitalOcean Cloud Firewalls |
| `429 urn:ietf:params:acme:error:rateLimited` | Cert re-issuance limit hit (usually after deleting `caddy_data`). Wait for the window to pass; never delete `caddy_data` casually |
| Backend build OOM-killed (`exit 137`) | 1 GB Droplet. Add swap: `fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile` |
| Public API always 503 | `CLERK_JWKS_URL` unset in `.env` |
| Public API always 401 in browser | Clerk keys are `pk_test_`/`sk_test_` but you're on the production domain — create a production Clerk instance |
| Dashboard 429 banner | Working as designed: 30 req/min/user rate limit (Phase 14). Retry after 60s |
| `db` unhealthy on first boot | Migrations only run on an **empty** `pgdata` volume. `docker compose down -v` destroys data but re-runs them |
