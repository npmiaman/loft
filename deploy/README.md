# Deploying the Pigeon server

The server stores accounts, teams and **encrypted** profile data. It never
launches a browser, so its image has no camoufox/playwright. Clients and cloud
runners install `pigeon[client]` separately.

What a production deployment needs:
- **Postgres** for metadata (`PIGEON_DB`)
- **S3-compatible object storage** for blobs — AWS S3, Cloudflare R2, Backblaze B2 or MinIO (`PIGEON_BLOBS` + `AWS_*`)
- **TLS** in front (Caddy here, with automatic Let's Encrypt)

Profile data and fingerprints are end-to-end encrypted with each team's key
before upload, so Postgres and S3 only ever hold ciphertext. Keep them private
anyway — the audit log, names and tags are not encrypted.

## Docker Compose (quickest)

```bash
cp deploy/.env.example deploy/.env
# edit deploy/.env: PIGEON_DOMAIN, POSTGRES_PASSWORD, PIGEON_BLOBS, AWS_* (or MinIO)
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build
```

- Point `PIGEON_DOMAIN`'s DNS at the host and open ports 80 and 443. Caddy fetches and renews the cert automatically.
- The first account to register needs no invite and becomes the first owner. Signup is invite-only after that (`PIGEON_OPEN_SIGNUP=0`, the default).
- Clients then run `pigeon login you@team.com --server https://pigeon.example.com`.

To keep storage on the same box instead of AWS, uncomment the `minio` service in `docker-compose.yml` and set `PIGEON_BLOBS=s3://pigeon`, `PIGEON_S3_ENDPOINT=http://minio:9000` in `.env`.

## Without Docker (systemd)

```bash
python3 -m venv /opt/pigeon && /opt/pigeon/bin/pip install 'pigeon[server]'
```

`/etc/pigeon.env`:

```
PIGEON_DB=postgresql://pigeon:PASSWORD@localhost/pigeon
PIGEON_BLOBS=s3://my-bucket/pigeon
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_DEFAULT_REGION=us-east-1
PIGEON_OPEN_SIGNUP=0
```

`/etc/systemd/system/pigeon.service`:

```ini
[Unit]
Description=Pigeon server
After=network.target postgresql.service

[Service]
EnvironmentFile=/etc/pigeon.env
# Bind loopback and terminate TLS with Caddy/nginx in front (deploy/Caddyfile),
# or pass --self-signed <host> / --tls-cert/--tls-key to serve TLS directly.
ExecStart=/opt/pigeon/bin/pigeon server --host 127.0.0.1 --port 4400
DynamicUser=yes
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
systemctl enable --now pigeon
```

Put `deploy/Caddyfile` in front for a public TLS endpoint.

## TLS directly (no proxy)

```bash
pigeon server --host 0.0.0.0 --self-signed pigeon.example.com   # self-signed, prints the cert path
pigeon server --host 0.0.0.0 --tls-cert fullchain.pem --tls-key privkey.pem
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

`deploy/` was checked locally (2026-11-27): the real `pigeon server` process on
**Postgres 17 + S3 (moto) + TLS** served register, team create, profile create,
run-lock, encrypted sync, download and key rotation; blobs landed in S3 as
ciphertext and old versions were deleted on rotation. The slim `pigeon[server]`
install was confirmed to carry no camoufox. Not yet done on a real cloud host.
