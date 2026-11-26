# Deploying the Loft server

The server stores accounts, teams and **encrypted** profile data. It never
launches a browser, so its image has no camoufox/playwright. Clients and cloud
runners install `loft[client]` separately.

What a production deployment needs:
- **Postgres** for metadata (`LOFT_DB`)
- **S3-compatible object storage** for blobs — AWS S3, Cloudflare R2, Backblaze B2 or MinIO (`LOFT_BLOBS` + `AWS_*`)
- **TLS** in front (Caddy here, with automatic Let's Encrypt)

Profile data and fingerprints are end-to-end encrypted with each team's key
before upload, so Postgres and S3 only ever hold ciphertext. Keep them private
anyway — the audit log, names and tags are not encrypted.

## Docker Compose (quickest)

```bash
cp deploy/.env.example deploy/.env
# edit deploy/.env: LOFT_DOMAIN, POSTGRES_PASSWORD, LOFT_BLOBS, AWS_* (or MinIO)
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
```

- Point `LOFT_DOMAIN`'s DNS at the host and open ports 80 and 443. Caddy fetches and renews the cert automatically.
- The first account to register needs no invite and becomes the first owner. Signup is invite-only after that (`LOFT_OPEN_SIGNUP=0`, the default).
- Clients then run `loft login you@team.com --server https://loft.example.com`.

To keep storage on the same box instead of AWS, uncomment the `minio` service in `docker-compose.yml` and set `LOFT_BLOBS=s3://loft`, `LOFT_S3_ENDPOINT=http://minio:9000` in `.env`.

## Without Docker (systemd)

```bash
python3 -m venv /opt/loft && /opt/loft/bin/pip install 'loft[server]'
```

`/etc/loft.env`:

```
LOFT_DB=postgresql://loft:PASSWORD@localhost/loft
LOFT_BLOBS=s3://my-bucket/loft
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=us-east-1
LOFT_OPEN_SIGNUP=0
```

`/etc/systemd/system/loft.service`:

```ini
[Unit]
Description=Loft server
After=network.target postgresql.service

[Service]
EnvironmentFile=/etc/loft.env
# Bind loopback and terminate TLS with Caddy/nginx in front (deploy/Caddyfile),
# or pass --self-signed <host> / --tls-cert/--tls-key to serve TLS directly.
ExecStart=/opt/loft/bin/loft server --host 127.0.0.1 --port 4400
DynamicUser=yes
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
systemctl enable --now loft
```

Put `deploy/Caddyfile` in front for a public TLS endpoint.

## TLS directly (no proxy)

```bash
loft server --host 0.0.0.0 --self-signed loft.example.com   # self-signed, prints the cert path
loft server --host 0.0.0.0 --tls-cert fullchain.pem --tls-key privkey.pem
```

Plain HTTP is refused on any non-loopback bind unless you pass `--insecure`
(only safe behind a TLS proxy on the same host).

## Backups

Everything is in two places:
- **Postgres** — `pg_dump` on a schedule.
- **S3 bucket** — versioning on the bucket, or `aws s3 sync` to a second bucket.

A restore is a Postgres restore plus the bucket; the server is stateless. Keep
both from the same moment. The team keys live only on members' devices, so a
server backup alone can't decrypt anything — that's the point, but it means a
team that loses every device's key loses its profile data.

## Verified

`deploy/` was checked locally (2026-11-27): the real `loft server` process on
**Postgres 17 + S3 (moto) + TLS** served register, team create, profile create,
run-lock, encrypted sync, download and key rotation; blobs landed in S3 as
ciphertext and old versions were deleted on rotation. The slim `loft[server]`
install was confirmed to carry no camoufox. Not yet done on a real cloud host.
