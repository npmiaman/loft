# Loft server image. The server never launches a browser, so it installs only
# loft[server] (FastAPI, uvicorn, cryptography, psycopg, boto3) -- no camoufox
# or playwright. Clients and cloud runners install the full package separately.
FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml README.md ./
COPY loft ./loft
RUN pip install --no-cache-dir --upgrade pip build && python -m build --wheel

FROM python:3.12-slim
# curl is only for the container HEALTHCHECK below.
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
RUN useradd --system --create-home --uid 10001 loft
COPY --from=build /src/dist/*.whl /tmp/
RUN pip install --no-cache-dir "/tmp/$(ls /tmp | grep '\.whl$')[server]" && rm /tmp/*.whl
USER loft
WORKDIR /home/loft

# Configure with env vars (see deploy/.env.example):
#   LOFT_DB      postgresql://user:pass@host/db
#   LOFT_BLOBS   s3://bucket/prefix   (LOFT_S3_ENDPOINT + AWS_* for non-AWS)
#   LOFT_OPEN_SIGNUP=0  (invite-only; the first account still needs no invite)
ENV LOFT_DATA=/home/loft/data LOFT_OPEN_SIGNUP=0
EXPOSE 4400
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD curl -fsS http://127.0.0.1:4400/health || exit 1

# TLS is terminated by the Caddy reverse proxy (see docker-compose.yml), so the
# app binds plain HTTP on loopback inside the container network.
CMD ["loft", "server", "--host", "0.0.0.0", "--port", "4400", "--insecure"]
