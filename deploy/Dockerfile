# Pigeon server image. The server never launches a browser, so it installs only
# pigeon[server] (FastAPI, uvicorn, cryptography, psycopg, boto3) -- no camoufox
# or playwright. Clients and cloud runners install the full package separately.
FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml README.md ./
COPY pigeon ./pigeon
RUN pip install --no-cache-dir --upgrade pip build && python -m build --wheel

FROM python:3.12-slim
# curl is only for the container HEALTHCHECK below.
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --system --create-home --uid 10001 pigeon
COPY --from=build /src/dist/*.whl /tmp/
RUN pip install --no-cache-dir "/tmp/$(ls /tmp | grep '\.whl$')[server]" && rm /tmp/*.whl
USER pigeon
WORKDIR /home/pigeon

# Configure with env vars (see deploy/.env.example):
#   PIGEON_DB      postgresql://user:pass@host/db
#   PIGEON_BLOBS   s3://bucket/prefix   (PIGEON_S3_ENDPOINT + AWS_* for non-AWS)
#   PIGEON_OPEN_SIGNUP=0  (invite-only; the first account still needs no invite)
ENV PIGEON_DATA=/home/pigeon/data PIGEON_OPEN_SIGNUP=0
EXPOSE 4400
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD curl -fsS http://127.0.0.1:4400/health || exit 1

# TLS is terminated by the Caddy reverse proxy (see docker-compose.yml), so the
# app binds plain HTTP on loopback inside the container network.
CMD ["pigeon", "server", "--host", "0.0.0.0", "--port", "4400", "--insecure"]
