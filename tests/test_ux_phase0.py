"""Phase 0 (safety) end to end: a real `pigeon server` and a real `pigeon app --no-window` in a temp dir (ports 4600-4619), a real
account browser launched headless by the app, a small stand-in cloud runner, and the dashboard driven in Chromium.

Run:  cd web && npm run build && cd .. && uv run pytest tests/test_ux_phase0.py -s
Needs the built UI, a Playwright Chromium, and the Camoufox browser (`pigeon fetch-browser`), like tests/test_web_e2e.py."""
import json
import socket
import threading
import time

import httpx
import pytest
import uvicorn
from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from tests.test_pigeon import HAVE
from tests.ux_harness import Sandbox, _port_free, browser, needs_ui  # noqa: F401

pytestmark = [needs_ui, pytest.mark.skipif(not HAVE, reason="needs the Camoufox browser (the app launches accounts with it)")]


@pytest.fixture(scope="module")
def sb():
    s = Sandbox()
    yield s
    s.close()


@pytest.fixture(scope="module")
def fake_runner(sb):
    """Answers like a cloud runner: POST /sessions -> {id, view}, and serves the view page."""
    port = next(p for p in range(4600, 4619) if p not in (sb.srv_port, sb.app_port) and _port_free(p))
    app, seen = FastAPI(), []

    @app.post("/sessions")
    def sessions(body: dict):
        seen.append(body["profile_id"])
        return {"id": "s1", "view": "/view/s1"}

    @app.get("/view/s1", response_class=HTMLResponse)
    def view():
        return "<h1>Cloud viewer</h1>"
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=srv.run, daemon=True).start()
    while not srv.started:
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", seen
    srv.should_exit = True


def api(sb, method, path, body=None):
    r = httpx.request(method, sb.url + "/api" + path, headers={"X-Pigeon": "1"}, json=body, timeout=60)
    assert r.status_code < 400, r.text
    return r.json()


def test_phase0_safety_end_to_end(sb, fake_runner, browser):
    runner_url, runner_seen = fake_runner
    ctx = browser.new_context(viewport={"width": 1280, "height": 900})
    page = ctx.new_page()
    page.set_default_timeout(20000)
    errors, native = [], []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("dialog", lambda d: (native.append(d.message), d.dismiss()))  # every confirm must be the styled one
    page.goto(sb.url)

    # sign up, create a team, add two accounts (one with a proxy)
    page.fill("#em", "owner@x.io")
    page.fill("#pw", "password1")
    page.click("button:text-is('Create account')")
    page.fill("#tn", "Agency")
    page.click("button:text-is('Create')")
    page.click("main button:has-text('Add account') >> nth=0")  # the team already has its starter account
    page.fill("#n_name", "Proxied")
    page.fill("#px_host", "http://u:secret@10.9.8.7:3128")  # a pasted URL fills type/host/port/user/password
    page.click("#n_go")
    page.wait_for_selector("text=Proxied", timeout=60000)
    page.click("button:has-text('Add account')")
    page.fill("#n_name", "Client B")
    page.click("#n_go")
    page.wait_for_selector("text=Client B", timeout=60000)
    ids = {p["name"]: p["id"] for p in api(sb, "GET", "/state")["profiles"]}

    # P0-2: renaming only the proxy keeps its address
    page.click("button[aria-label='More for Proxied']")
    page.click("[role=menuitem]:text-is('Edit')")
    page.wait_for_function("document.querySelector('#px_host')?.value === '10.9.8.7'")
    page.fill("#px_name", "Office")
    page.click("[role=dialog] button[type=submit]")
    page.wait_for_selector("[data-account='Proxied'] button[data-proxy]:has-text('Office')")
    assert api(sb, "GET", f"/profiles/{ids['Proxied']}/proxy")["proxy"] == "http://u:secret@10.9.8.7:3128"

    # P0-3: run on a cloud runner opens the runner's viewer (the popup opens inside the click, then is pointed at the view)
    api(sb, "POST", "/runners", {"name": "Stand-in", "url": runner_url, "key": "k"})
    page.reload()
    page.click("button[aria-label='More for Proxied']")
    with ctx.expect_page() as pop:
        page.click("[role=menuitem]:text-is('Run on a cloud runner')")
    popup = pop.value
    popup.wait_for_url(runner_url + "/view/s1")
    assert popup.inner_text("h1") == "Cloud viewer" and runner_seen == [ids["Proxied"]]
    popup.close()

    # P0-5: deleting names the account and uses the styled confirm
    page.click("button[aria-label='More for Proxied']")
    page.click("[role=menuitem]:text-is('Edit')")
    page.click("[role=dialog] button:text-is('Delete')")
    assert "Delete Proxied?" in page.inner_text("[role=alertdialog]")
    page.click("[role=alertdialog] button:text-is('Delete account')")
    page.wait_for_function("!document.body.innerText.includes('Proxied')")

    # open Client B; its agent controls appear once its session is up
    page.click("[data-account='Client B'] button:text-is('Open browser')")
    page.wait_for_selector("[data-status]:has-text('Open')", timeout=90000)
    page.wait_for_selector("button:text-is('Agent controls')", timeout=120000)

    # P0-4: the row says what a sending mode will do, and starting one asks first
    page.select_option("select[aria-label='What should the agent do?']", "dm")
    page.wait_for_selector("text=Nothing is approved yet, so nothing will be sent.")
    page.click("button:text-is('Start agent')")
    assert "Start sending from Client B?" in page.inner_text("[role=alertdialog]")
    page.click("[role=alertdialog] button:text-is('Cancel')")
    page.select_option("select[aria-label='What should the agent do?']", "find")

    page.click("button:text-is('Agent controls')")
    page.wait_for_selector("#pg h3")

    # P0-7: with no AI key, the refusal offers a button to the key field (no command line); the key saves and is never shown back
    page.click("#pg [role=tab]:text-is('Run')")
    page.click("#pg button:text-is('Start')")
    toast = page.wait_for_selector("[data-sonner-toast]:has-text('AI key')")
    assert "pigeon agent" not in toast.inner_text()
    toast.query_selector("button:has-text('Add AI key')").click()
    page.wait_for_selector("#ai_key")
    page.wait_for_function("document.activeElement && document.activeElement.id === 'ai_key'")
    page.fill("#ai_key", "sk-e2e-0123456789abcdef")
    page.click("#pg button:text-is('Save key')")
    page.wait_for_selector("text=A key is saved (…cdef).")
    k = api(sb, "GET", "/pigeon/key")
    assert k["set"] and "0123456789ab" not in json.dumps(k)
    assert json.loads((sb.home / "pigeon.json").read_text())["deepseek_key"] == "sk-e2e-0123456789abcdef"

    # P0-4 + P0-1: imported people get drafts that wait for approval; Approve sends the text on screen
    page.click("#pg [role=tab]:text-is('People')")
    page.fill("#pgimp", "spez\nexample_user")
    page.click("button:text-is('Import to queue')")
    page.wait_for_selector("#pg >> text=u/example_user", timeout=15000)
    page.click("#pg button:text-is('Write all queued')")
    page.wait_for_selector("#pg textarea[aria-label='Message to u/spez']", timeout=15000)
    leads = {l["username"]: l for l in api(sb, "POST", f"/profiles/{ids['Client B']}/pigeon/op", {"op": "leads_list"})["leads"]}
    assert {l["status"] for l in leads.values()} == {"draft"}  # template drafts wait too
    page.fill("#pg textarea[aria-label='Message to u/spez']", "Edited on screen, not saved")
    page.click("#pg [data-lead=spez] button:text-is('Approve')")
    page.wait_for_timeout(1500)
    spez = next(l for l in api(sb, "POST", f"/profiles/{ids['Client B']}/pigeon/op", {"op": "leads_list"})["leads"] if l["username"] == "spez")
    assert spez["status"] == "ready" and spez["message"] == "Edited on screen, not saved"

    # P0-5: Approve all asks first
    page.click("#pg button:has-text('Approve all')")
    assert "Approve 1 message?" in page.inner_text("[role=alertdialog]")
    page.click("[role=alertdialog] button:text-is('Cancel')")

    # P0-6: "Clear unsent leads" (with a confirm) replaces "Clear all"
    assert "Clear all" not in page.inner_text("#pg")
    page.click("#pg button:text-is('Clear unsent leads')")
    page.click("[role=alertdialog] button:text-is('Clear unsent leads')")
    page.wait_for_function("!document.querySelector('#pg').innerText.includes('u/example_user')", timeout=15000)

    # P0-8: someone in the team without the key gets an input for the full code, and it unlocks the accounts
    full = api(sb, "POST", "/invite", {"role": "admin"})["code"]
    page.keyboard.press("Escape")
    page.click("button:text-is('Close & save')")
    page.wait_for_selector("button:text-is('Close & save')", state="detached", timeout=60000)  # closed accounts carry no badge
    page.click("button[aria-label='Account menu'] >> visible=true")  # Sign out lives in the account menu
    page.click("[role=menuitem]:has-text('Sign out')")
    page.fill("#em", "admin@x.io")
    page.fill("#pw", "password2")
    page.click("button:text-is('Create an account')")  # this server has accounts, so sign-up is its own mode with the invite field
    page.fill("#inv", full.split(".", 1)[0])
    page.click("button:text-is('Create account')")
    page.wait_for_selector("text=This device doesn't have the team's encryption key")
    page.fill("#keycode", full)
    page.click("button:text-is('Join')")
    page.wait_for_selector("text=Client B", timeout=30000)

    assert native == [], native
    assert errors == [], errors
    ctx.close()
