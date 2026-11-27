"""The interactive part of `loft run`: keeps the browser open, serves the control channel, and lets tools
(Pigeon) attach to the live session."""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Callable

from .control import SessionControl
from .pigeon.attach import PigeonHost


def interactive(context, pid: int, info: dict, *, home: Path, log: Callable[[str], None] = print,
                tick: float = 0.4, host_factory=PigeonHost, client=None,
                should_stop: Callable[[], bool] = lambda: False) -> None:
    """Open the profile's start page, then block until the browser closes. While it is open, the control channel
    is up, and an attached Pigeon agent (if asked for) runs here, on the thread that owns the browser."""
    from playwright.sync_api import TimeoutError as PWTimeout

    closed = threading.Event()
    context.on("close", lambda *_: closed.set())
    page = context.pages[0] if context.pages else context.new_page()
    if info.get("start_url"):
        page.goto(info["start_url"])

    central = None
    if client is not None and info.get("team_id"):  # the team's shared memory: who was DMed, where we commented
        from .pigeon.central import CentralBrain
        central = CentralBrain(client, info["team_id"], pid)
    host = host_factory(pid, home, central)
    host.should_stop = should_stop
    ctl = SessionControl(pid, home)
    host.mount(ctl)
    ctl.start()
    log(f"control channel up for profile {pid}: `loft pigeon attach {pid}` connects the Pigeon agent")
    try:
        while not closed.is_set():
            if should_stop():
                log("stop requested: closing and syncing")
                break
            try:
                context.wait_for_event("close", timeout=tick * 1000)  # also pumps Playwright's events
            except PWTimeout:
                pass
            except Exception:
                break  # the browser is gone
            if closed.is_set():
                break
            host.pump(context)
    finally:
        host.shutdown()  # stop any running agent and persist Pigeon state before the profile is packed and uploaded
        ctl.close()
