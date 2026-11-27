import itertools
import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

import pytest
import uvicorn

from pigeon.client import Client
from pigeon.server import create_app

PG_BIN = next((Path(p) for p in ("/opt/homebrew/opt/postgresql@17/bin", "/opt/homebrew/opt/postgresql@16/bin",
                                 "/usr/lib/postgresql/16/bin", "/usr/lib/postgresql/17/bin")
               if (Path(p) / "initdb").exists()), None)
_n = itertools.count()


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture(scope="session")
def pg_cluster(tmp_path_factory):
    """A throwaway Postgres cluster on a random port (never touches a running system Postgres)."""
    if PG_BIN is None:
        pytest.skip("no Postgres binaries")
    d = tmp_path_factory.mktemp("pg")
    port = _free_port()
    env = dict(os.environ, LC_ALL="en_US.UTF-8", LANG="en_US.UTF-8")  # postmaster refuses to start without it
    subprocess.run([PG_BIN / "initdb", "-D", d / "data", "-U", "pigeon", "--auth=trust", "-E", "UTF8"],
                   check=True, capture_output=True, env=env)
    import tempfile
    sock = tempfile.mkdtemp(prefix="lpg", dir="/tmp")  # Unix socket paths must stay under ~103 bytes
    subprocess.run([PG_BIN / "pg_ctl", "-D", d / "data", "-l", d / "log", "-w", "-o",
                    f"-p {port} -k {sock} -c listen_addresses=127.0.0.1", "start"], check=True, capture_output=True, env=env)
    yield f"postgresql://pigeon@127.0.0.1:{port}"
    subprocess.run([PG_BIN / "pg_ctl", "-D", d / "data", "-m", "immediate", "stop"], capture_output=True)
    shutil.rmtree(sock, ignore_errors=True)


@pytest.fixture(scope="session")
def s3_endpoint():
    from moto.server import ThreadedMotoServer
    port = _free_port()
    srv = ThreadedMotoServer(ip_address="127.0.0.1", port=port)
    srv.start()
    yield f"http://127.0.0.1:{port}"
    srv.stop()


def pytest_generate_tests(metafunc):
    if "backend" in metafunc.fixturenames:
        metafunc.parametrize("backend", ["sqlite+files", "postgres+s3"])


@pytest.fixture
def backend(request):
    return request.param


@pytest.fixture
def server(tmp_path, request, monkeypatch):
    kind = request.node.callspec.params.get("backend", "sqlite+files") if hasattr(request.node, "callspec") else "sqlite+files"
    kw = {}
    if kind == "postgres+s3":
        import boto3
        import psycopg
        base = request.getfixturevalue("pg_cluster")
        s3 = request.getfixturevalue("s3_endpoint")
        name = f"pigeon_{os.getpid()}_{next(_n)}"
        with psycopg.connect(base + "/postgres", autocommit=True) as c:
            c.execute(f"CREATE DATABASE {name}")
        for k, v in {"AWS_ACCESS_KEY_ID": "test", "AWS_SECRET_ACCESS_KEY": "test", "AWS_DEFAULT_REGION": "us-east-1",
                     "PIGEON_S3_ENDPOINT": s3}.items():
            monkeypatch.setenv(k, v)
        boto3.client("s3", endpoint_url=s3).create_bucket(Bucket=name.replace("_", "-"))
        kw = {"db_url": f"{base}/{name}", "blobs_url": f"s3://{name.replace('_', '-')}/pigeon"}
    port = _free_port()
    app = create_app(tmp_path / "srv", open_signup=False, **kw)
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    srv.should_exit = True
    t.join(5)


@pytest.fixture
def mk(server):
    def make(token=None):
        return Client(server=server, token=token or "")
    return make


FP = {"navigator": {"userAgent": "test"}, "os": "windows"}
