"""Identity consistency: everything that depends on the profile's language and its exit IP's country must agree, in the REAL browser.

Each case launches Camoufox with options from Pigeon's own prepare_launch, for an exit IP in a given country (the exit-IP lookup is stubbed,
so no network: the country, time zone and language logic is the real code), then reads the actual values back and audits them.
English is the default language; `language="country"` follows the country's native language."""
import warnings

import pytest

from pigeon import engine
from tests.test_pigeon import HAVE

pytestmark = pytest.mark.skipif(not HAVE, reason="needs the real Camoufox browser")

IPS = {"IT": "79.50.199.86", "US": "8.8.8.8", "GB": "81.2.69.142", "DE": "5.9.0.1", "FR": "51.77.0.1", "JP": "133.242.0.1",
       "BR": "200.147.0.1", "IN": "103.21.58.1", "ES": "88.0.0.1"}
MAC_DEFAULT = {"en-US": "Samantha", "en-GB": "Daniel", "en-IN": "Rishi", "it-IT": "Alice", "ja-JP": "Kyoko", "de-DE": "Anna"}


def launch(cc, tmp_path, language=None, voices=True, headless=True):
    from camoufox.sync_api import Camoufox
    warnings.filterwarnings("ignore")
    engine_check = engine.check_proxy
    engine.check_proxy = lambda proxy, **kw: dict(ok=True, ip=IPS[cc], ipv6=False, ms=1, stable=True)
    try:
        os_name = engine.host_os()
        info = dict(fingerprint=engine.new_fingerprint(os_name), proxy={"server": "http://127.0.0.1:9"}, os=os_name, canvas="real", name="t",
                    **({"language": language} if language else {}))
        expect: dict = {}
        opts = engine.prepare_launch(info, tmp_path / f"{cc}-{language}", headless, seed="42", log=lambda m: None, expect=expect)
    finally:
        engine.check_proxy = engine_check
    if not voices:
        opts.pop("config", None)
    with Camoufox(**opts) as ctx:
        return engine.audit_identity(ctx, expect), expect


def failing(rows):
    return [f"{r['signal']}: {r['actual']} (expected {r['expected']})" for r in rows if not r["ok"]]


@pytest.mark.parametrize("cc", list(IPS))
def test_english_is_the_default_and_everything_agrees_for_every_country(cc, tmp_path):
    rows, expect = launch(cc, tmp_path)
    assert failing(rows) == []
    by = {r["signal"]: r for r in rows}
    assert by["browser language"]["actual"].startswith("en")  # English by default, wherever the IP is
    assert by["time zone (from the exit IP)"]["actual"] == expect["timezone"]  # while the time zone still follows the exit IP
    assert expect["country"] == cc
    tag = by["browser language"]["actual"]
    if engine.host_os() == "macos" and tag in MAC_DEFAULT:
        assert by["default speech voice"]["actual"][0].startswith(MAC_DEFAULT[tag])  # the voice a stock Mac in that language defaults to


@pytest.mark.parametrize("cc", ["IT", "JP", "DE"])
def test_country_language_mode_also_agrees(cc, tmp_path):
    rows, expect = launch(cc, tmp_path, language="country")
    assert failing(rows) == []
    by = {r["signal"]: r for r in rows}
    assert not by["browser language"]["actual"].startswith("en")  # the native language this time
    if engine.host_os() == "macos":
        assert by["default speech voice"]["actual"][0].startswith(MAC_DEFAULT[by["browser language"]["actual"]])  # Alice for it-IT, never the host's Rishi


def test_an_explicit_language_is_honoured(tmp_path):
    rows, _ = launch("FR", tmp_path, language="it-IT")
    assert failing(rows) == [] and {r["signal"]: r for r in rows}["browser language"]["actual"] == "it-IT"


def test_the_audit_would_have_caught_the_original_rishi_bug(tmp_path):
    """Without Pigeon's voice list Camoufox leaves no default voice, and macOS fills in this machine's own: a mismatch the audit must flag."""
    rows, _ = launch("IT", tmp_path, language="country", voices=False, headless=False)
    bad = failing(rows)
    assert any(b.startswith("default speech voice") for b in bad), rows  # headed, as a user runs it: the host's default shows through
    good, _ = launch("IT", tmp_path, language="country", voices=True, headless=False)
    assert failing(good) == []


def test_open_profile_audits_the_real_launch_and_logs_the_result(mk, tmp_path):
    """The audit runs on every real launch, not only in tests, and a profile without a proxy is audited too (against its own language)."""
    from pigeon.engine import open_profile
    from tests.conftest import FP
    owner = mk()
    owner.register("o@x.io", "password1", "Owner")
    tid = owner.create_team("T")["id"]
    os_name = engine.host_os()
    pid = owner.create_profile(tid, name="a", os=os_name, fingerprint=engine.new_fingerprint(os_name), canvas=engine.default_canvas(os_name))["id"]
    logs = []
    with open_profile(owner, pid, headless=True, home=tmp_path, log=logs.append) as ctx:
        assert ctx.pigeon_audit and all(r["ok"] for r in ctx.pigeon_audit), engine.format_audit(ctx.pigeon_audit)
    assert any("identity consistent" in m for m in logs), logs


def test_policy_and_voice_defaults_without_a_browser():
    assert engine.english_locale("IT") == ["en-US", "en"] and engine.english_locale("gb") == ["en-GB", "en"] and engine.english_locale("IN") == ["en-IN", "en"]
    assert engine.browser_locale("IT", "1") == ["en-US", "en"]
    assert engine.browser_locale("IT", "1", "country") == ["it-IT", "it", "en-US", "en"]
    assert engine.browser_locale("FR", "1", "it-IT") == ["it-IT", "it", "en-US", "en"] and engine.browser_locale("FR", "1", "en-GB") == ["en-GB", "en"]
    for loc, name in (("en-US", "Samantha"), ("en-GB", "Daniel"), ("it-IT", "Alice"), ("en-NG", "Samantha"), ("en-KE", "Samantha")):
        v = engine.profile_voices("macos", [loc], "p")
        assert [x["name"] for x in v if x["isDefault"]] == [name], loc  # exactly one default, the right one (regional English falls back to en-US's)
    assert engine.profile_voices("macos", ["it-IT"], "p") == engine.profile_voices("macos", ["it-IT"], "p")  # stable per profile
    w = engine.profile_voices("windows", ["it-IT"], "p")
    assert sum(x["isDefault"] for x in w) == 1 and next(x for x in w if x["isDefault"])["lang"].startswith("it")
