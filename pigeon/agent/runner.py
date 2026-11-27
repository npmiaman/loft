"""Glue between Pigeon's profile lifecycle (lock -> pull -> run -> push -> unlock) and Pigeon."""
from __future__ import annotations

import time
from typing import Callable

from ..client import Client
from ..engine import PROFILES, LocalProfile, open_data, run_profile
from .agent import Agent
from .bridge import Bridge
from .llm import Llm
from .central import CentralBrain
from .state import STATE_NAME, State, load_key


def state_path(pid: int, home=PROFILES):
    return LocalProfile(pid, home).data / STATE_NAME


def with_state(client: Client, pid: int, fn: Callable[[State], object], home=PROFILES):
    """Read/edit a profile's Pigeon state without opening a browser. Takes the lock and syncs the result."""
    out = []
    with open_data(client, pid, home=home) as data:
        st = State(data / STATE_NAME)
        out.append(fn(st))
        st.save()  # always persist, so a forgotten save() can't silently drop an edit
    return out[0] if out else None


def central_for(client: Client, pid: int) -> CentralBrain:
    """The team's shared memory for this profile's team. If the server can't be asked, nothing should send."""
    try:
        return CentralBrain(client, client.profile(pid)["team_id"], pid)
    except Exception as e:
        raise RuntimeError(f"can't reach the team's central brain ({e}); not starting, so nobody gets messaged twice")


def make_llm(st: State) -> Llm | None:
    key, model = load_key()
    return Llm(key, model or st.settings["model"]) if key else None


def with_agent(client: Client, pid: int, fn: Callable[[Agent], object], *, headless: bool = False,
               pace: float | None = None, home=PROFILES, reraise_interrupt: bool = False, label: str = "run"):
    """Open the profile in Camoufox, hand `fn` a ready Agent, and sync the profile back when it finishes.

    While it runs the profile has the same control channel as an attached session, so `pigeon agent status|detach|leads|approve`
    and the desktop app work on an unattended run too (and `detach` stops it cleanly). Ctrl-C also stops the agent cleanly so
    the profile (cookies, Pigeon state) still uploads; with `reraise_interrupt` the KeyboardInterrupt is raised again once
    that sync is done (so a multi-account rotation can stop)."""
    from ..control import SessionControl
    from .attach import PigeonHost

    central = central_for(client, pid)
    interrupted = []

    def session(context):
        page = context.pages[0] if context.pages else context.new_page()
        host = PigeonHost(pid, home, central)
        st = host.st  # one State, shared by the agent and the control channel
        bridge = Bridge(page, pace if pace is not None else st.settings["pace"])
        host.should_stop = getattr(context, "pigeon_stop", lambda: False)  # Ctrl+C / `pigeon stop` / the desktop's stop button
        bridge.on_tick = host.tick_for(bridge)  # control requests are answered on this thread, between the agent's steps
        agent = Agent(bridge, st, make_llm(st), central, diag_dir=home / str(pid) / "diagnostics")
        host.bridge, host.state, host.job, host.since = bridge, "running", label, time.time()
        ctl = SessionControl(pid, home)
        host.mount(ctl)
        ctl.start()
        try:
            fn(agent)
            if host.should_stop():  # the run was told to stop (the engine turns Ctrl+C into a stop request, not an exception)
                interrupted.append(True)
        except KeyboardInterrupt:
            interrupted.append(True)
            bridge.abort()
            st.log("info", "Interrupted; syncing the profile")
        finally:
            host.bridge, host.state, host.job = None, "idle", None
            host.service()
            st.save()
            ctl.close()

    run_profile(client, pid, headless=headless, session=session, home=home)
    if interrupted and reraise_interrupt:
        raise KeyboardInterrupt()
