import socket
import threading
import time

import pytest
import uvicorn

from loft.client import Client
from loft.server import create_app


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def server(tmp_path):
    port = _free_port()
    app = create_app(tmp_path / "srv", open_signup=False)
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
