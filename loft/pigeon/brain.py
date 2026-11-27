"""The brain: product profile -> subreddit discovery -> lead scoring -> message writing. Prompts match the extension's."""
from __future__ import annotations

import math
import random
import re
import time
from html.parser import HTMLParser

import httpx

from .llm import Llm


def clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n] + "…"


# ---------------------------------------------------------------- product profile
class _Text(HTMLParser):
    SKIP = {"script", "style", "noscript", "svg", "nav", "footer"}

    def __init__(self):
        super().__init__()
        self.depth = 0
        self.title = ""
        self.meta: dict[str, str] = {}
        self.heads: list[str] = []
        self.paras: list[str] = []
        self._cur: str | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in self.SKIP:
            self.depth += 1
        elif tag == "meta" and (a.get("name") or a.get("property")):
            self.meta[a.get("name") or a.get("property")] = a.get("content") or ""
        elif not self.depth and tag in ("title", "h1", "h2", "h3", "p", "li"):
            self._cur, self._buf = tag, []

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.depth:
            self.depth -= 1
        elif tag == self._cur:
            text = " ".join("".join(self._buf).split())
            if tag == "title":
                self.title = text
            elif tag in ("h1", "h2", "h3") and text:
                self.heads.append(text)
            elif len(text) > 30:
                self.paras.append(text)
            self._cur = None

    def handle_data(self, data):
        if self._cur and not self.depth:
            self._buf.append(data)


def scrape_site(url: str, http: httpx.Client | None = None) -> str:
    r = (http or httpx).get(url, follow_redirects=True, timeout=30, headers={"User-Agent": "Mozilla/5.0"})
    if not r.is_success:
        raise RuntimeError(f"Could not fetch {url} ({r.status_code})")
    p = _Text()
    p.feed(r.text)
    desc = p.meta.get("description") or p.meta.get("og:description") or ""
    return clip("\n".join([f"TITLE: {p.title}", f"DESCRIPTION: {desc}", f"HEADINGS: {' | '.join(p.heads[:40])}",
                           f"TEXT: {' '.join(p.paras[:80])}"]), 7000)


def build_profile(llm: Llm, url: str, site_text: str, extra: str = "") -> dict:
    r = llm.json(
        "You are a sharp growth strategist who finds customers on Reddit. From a product website, build a profile "
        "that will be used to (1) find the right subreddits, (2) recognise people with a live need, (3) write DMs. "
        'Respond in json only. Schema: {"name":str,"summary":str (1-2 sentences, plain),"offer":str (what we give them, 1 sentence),'
        '"icp":str (who exactly buys this and the situation they are in),"personas":[str] (3-5),'
        '"painPhrases":[str] (8-15 things real people literally write on Reddit when they have this problem),'
        '"buyingSignals":[str] (5-10 post patterns like "looking for alternative to X", "anyone recommend ..."),'
        '"competitors":[str],"subredditIdeas":[str] (20-25 real subreddit names without r/, ranging from niche to broad),'
        '"searchQueries":[str] (6-10 reddit search queries)}. Use only subreddits you are confident exist.',
        f"WEBSITE ({url}):\n{site_text}\n\nEXTRA CONTEXT FROM THE FOUNDER:\n{extra or '(none)'}",
        max_tokens=3500, temperature=0.4,
    )
    g = lambda k: r.get(k) or []
    return dict(url=url, name=r.get("name", ""), summary=r.get("summary", ""), offer=r.get("offer", ""),
                icp=r.get("icp", ""), personas=g("personas"), painPhrases=g("painPhrases"),
                buyingSignals=g("buyingSignals"), competitors=g("competitors"),
                subredditIdeas=[re.sub(r"^/?r/", "", s, flags=re.I).strip() for s in g("subredditIdeas") if s.strip()],
                searchQueries=g("searchQueries"))


def product_brief(p: dict) -> str:
    return (f"PRODUCT: {p['name']} — {p['summary']}\nOFFER: {p['offer']}\nICP: {p['icp']}\n"
            f"PERSONAS: {'; '.join(p['personas'])}\nPAIN PHRASES: {' | '.join(p['painPhrases'])}\n"
            f"BUYING SIGNALS: {' | '.join(p['buyingSignals'])}\nCOMPETITORS: {', '.join(p['competitors'])}")


# ---------------------------------------------------------------- subreddit discovery
def _gather_sub(reddit_json, name: str) -> dict | None:
    try:
        about = reddit_json(f"/r/{name}/about.json")["data"]
        if not about or about.get("over18") or about.get("subreddit_type") == "private":
            return None
        newest = [c["data"] for c in reddit_json(f"/r/{name}/new.json?limit=25")["data"]["children"]]
        try:
            rules = [f"{r['short_name']}: {clip(r.get('description') or '', 160)}"
                     for r in reddit_json(f"/r/{name}/about/rules.json")["rules"]]
        except Exception:
            rules = []
        times = [p["created_utc"] for p in newest]
        span = max(1, max(times) - min(times)) if len(times) > 1 else 86400
        return dict(name=about["display_name"], subscribers=about.get("subscribers") or 0,
                    posts_per_day=min(500, len(newest) / span * 86400), description=clip(about.get("public_description") or "", 240),
                    rules=rules, titles=[clip(p["title"], 110) for p in newest[:25]])
    except Exception:
        return None


def discover_subreddits(llm: Llm, product: dict, reddit_json, progress=print) -> list[dict]:
    ideas = list(dict.fromkeys(s.lower() for s in product["subredditIdeas"]))[:25]
    facts = []
    for i, n in enumerate(ideas, 1):
        progress(f"Checking r/{n} ({i}/{len(ideas)})")
        f = _gather_sub(reddit_json, n)
        if f:
            facts.append(f)
        time.sleep(0.7)
    if not facts:
        raise RuntimeError("None of the suggested subreddits could be read. Is this profile logged in to Reddit?")
    progress("Scoring ICP density and rules…")
    out = []
    for i in range(0, len(facts), 6):
        batch = facts[i:i + 6]
        r = llm.json(
            "You judge whether a subreddit is a good place to find a product's customers. Respond in json only: "
            '{"subs":[{"name":str,"icpDensity":0-100,"rulesRisk":"low|medium|high","reason":str (max 20 words)}]}. '
            "icpDensity = share of the sampled recent post titles written by people who match the ICP with a need the product addresses. Be strict. "
            'rulesRisk: "high" if the rules ban self-promotion, advertising, solicitation or unsolicited DMs, or require mod approval; '
            '"medium" if promotion is limited (e.g. 9:1 rules, specific threads); "low" otherwise.',
            product_brief(product) + "\n\nSUBREDDITS:\n" + "\n\n".join(
                f"### r/{f['name']} ({f['subscribers']} members)\n{f['description']}\nRULES: {' || '.join(f['rules']) or '(none listed)'}\n"
                f"RECENT TITLES:\n- " + "\n- ".join(f["titles"]) for f in batch),
            max_tokens=2500)
        for f in batch:
            j = next((s for s in r.get("subs", []) if s["name"].lower() == f["name"].lower()), None)
            if not j:
                continue
            risk = j["rulesRisk"] if j.get("rulesRisk") in ("low", "medium", "high") else "medium"
            size = min(100, math.log10(max(10, f["subscribers"])) / 6 * 100)
            activity = min(100, f["posts_per_day"] * 4)
            score = round(j["icpDensity"] * 0.6 + size * 0.1 + activity * 0.15 + {"low": 15, "medium": 8, "high": 0}[risk])
            out.append(dict(name=f["name"], subscribers=f["subscribers"], posts_per_day=round(f["posts_per_day"], 1),
                            icp_density=j["icpDensity"], rules_risk=risk, score=score, reason=j.get("reason", ""),
                            enabled=risk != "high" and j["icpDensity"] >= 35, last_visited=0))
    return sorted(out, key=lambda s: -s["score"])


# ---------------------------------------------------------------- lead scoring
def score_items(llm: Llm, product: dict, items: list[dict]) -> list[dict]:
    """items: {id, kind, author, subreddit, title?, text}. Returns {id, fit, intent, reason, dm_ok, comment_fit}."""
    results: list[dict] = []
    for i in range(0, len(items), 12):
        batch = items[i:i + 12]
        r = llm.json(
            "You qualify Reddit authors as potential customers for a product. Respond in json only: "
            '{"results":[{"id":str,"fit":0-100,"intent":"asking|complaining|researching|sharing|unrelated","reason":str (max 25 words, cite what they wrote),"dm_ok":bool,"comment_fit":bool}]}. '
            "fit = how likely the AUTHOR matches the ICP AND has a live need visible in their text. Be strict: 70+ only with a clear, current need; "
            "generic chatter, news, memes and venting without a need score under 30. "
            "dm_ok=false for: vendors/self-promoters, bots, minors, people in distress or crisis, anyone who said they do not want DMs, or text with no personal need. "
            "comment_fit=true only for POSTS where a genuinely helpful, non-salesy reply that mentions the product would add value (e.g. they ask a question the product answers).",
            product_brief(product) + "\n\nITEMS:\n" + "\n---\n".join(
                f"[id={it['id']}] {it['kind']} by u/{it['author']} in r/{it['subreddit']}\n"
                f"{'TITLE: ' + it['title'] + chr(10) if it.get('title') else ''}{clip(it['text'], 900)}" for it in batch),
            max_tokens=3000)
        results.extend(r.get("results") or [])
    return results


# ---------------------------------------------------------------- writing
SPIN_RE = re.compile(r"\{([^{}|]+(?:\|[^{}]*)+)\}")


def render_template(body: str, lead: dict) -> str:
    spun = SPIN_RE.sub(lambda m: random.choice([c.strip() for c in m.group(1).split("|")]), body)
    topic = clip((lead.get("post_title") or "your post").rstrip(" \t\n?.!:;,"), 70)
    topic = topic[:1].lower() + topic[1:]
    out = re.sub(r"\{name\}", lambda _: lead["username"], spun, flags=re.I)
    out = re.sub(r"\{subreddit\}", lambda _: lead["subreddit"], out, flags=re.I)
    return re.sub(r"\{topic\}|\{title\}", lambda _: topic, out, flags=re.I)


def write_dm(llm: Llm, product: dict, lead: dict, guidance: str = "", team_context: str = "") -> str:
    r = llm.json(
        "You write the first Reddit DM from a founder to one specific person. Respond in json only: {\"message\":str}. Rules: "
        "max 60 words; plain text; sound like a real person typing, not a marketer; open with something specific they actually wrote; "
        "be upfront that you build the product (never pretend to be a customer or a neutral party); "
        "NO links, NO emojis, NO \"hope this finds you well\", NO flattery, NO hard pitch; "
        "end with one low-pressure question; do not invent facts about them or the product beyond the brief."
        + (f"\nFounder guidance: {guidance}" if guidance else ""),
        product_brief(product) + f"\n\nRECIPIENT: u/{lead['username']} in r/{lead['subreddit']}\n"
        f"THEY WROTE ({lead['post_title']}): {lead['snippet']}\nWHY THEY FIT: {lead['reason']}"
        + (f"\n\n{team_context}" if team_context else ""),
        max_tokens=400, temperature=0.9)
    return (r.get("message") or "").strip()


def draft_comment(llm: Llm, product: dict, post: dict, team_context: str = "") -> dict:
    return llm.json(
        'You write a Reddit comment replying to a post. Respond in json only: {"comment":str,"why":str (one line)}. Rules: '
        "lead with real, specific help that answers their question; 40-110 words; conversational; "
        "mention the product only if it truly fits, in one sentence, and disclose that you build it; no links; no emojis; "
        "follow what the subreddit would tolerate.",
        f"{product_brief(product)}\n\nPOST in r/{post['subreddit']}\nTITLE: {post['title']}\n{clip(post.get('body') or '', 1200)}"
        + (f"\n\n{team_context}" if team_context else ""),
        max_tokens=500, temperature=0.8)


def generate_post_ideas(llm: Llm, product: dict, subs: list[dict], team_context: str = "") -> list[dict]:
    r = llm.json(
        "You propose Reddit posts that earn attention by giving value first, then let the product come up naturally. "
        'Respond in json only: {"ideas":[{"subreddit":str,"title":str,"body":str (80-200 words),"angle":str (one line)}]}. '
        "Exactly 5 ideas across the given subreddits. No ad copy, no links, written like a founder sharing what they learned.",
        f"{product_brief(product)}\n\nSUBREDDITS: {', '.join('r/' + s['name'] for s in subs)}"
        + (f"\n\n{team_context}" if team_context else ""),
        max_tokens=3000, temperature=0.9)
    now = int(time.time() * 1000)
    return [dict(i, id=f"{now}-{n}") for n, i in enumerate(r.get("ideas") or [])]


# ---------------------------------------------------------------- spintax
VARS = ["{name}", "{subreddit}", "{topic}", "{title}"]


def count_variants(body: str) -> int:
    n = 1
    for m in SPIN_RE.finditer(body):
        n *= len(m.group(1).split("|"))
    return n


def preview_variants(body: str, sample: dict, n: int = 4) -> list[str]:
    seen: dict[str, None] = {}
    for _ in range(n * 6):
        if len(seen) >= n:
            break
        seen[render_template(body, sample)] = None
    return list(seen)


def valid_spintax(original: str, candidate: str) -> str | None:
    """None if the candidate is acceptable, else why not."""
    if re.search(r"[{}]", re.sub(r"\{[^{}]*\}", "", candidate)):  # removing every innermost group must leave no braces
        return "unbalanced or nested braces"
    for v in VARS:
        if candidate.count(v) < original.count(v):
            return f"lost variable {v}"
    if re.search(r"https?://|www\.", candidate, re.I):
        return "contains a link"
    if count_variants(candidate) < 8:
        return "too few variations"
    leftovers = re.findall(r"\{[^{}]*\}", SPIN_RE.sub("", candidate))
    if any(l.lower() not in VARS for l in leftovers):
        return f"unknown placeholder {','.join(leftovers)}"
    return None


def add_spintax(llm: Llm, body: str, intensity: str = "medium") -> str:
    """Rewrite a message with {a|b|c} spintax so no two DMs read the same. Validated and retried once."""
    spots = {"light": "4-6", "medium": "7-10", "heavy": "11-15"}[intensity]
    feedback = ""
    for _ in range(2):
        r = llm.json(
            "You are a spintax writer for cold outreach messages. Rewrite the user's message so it contains spintax: "
            "groups written {option one|option two|option three} where the renderer picks one option at random. "
            'Respond in json only: {"template":str}. Rules: '
            f"use {spots} spintax groups, each with 2-4 options; spin greetings, openers, connectors, the ask, and sign-offs, plus a few mid-sentence phrases; "
            "EVERY option in a group must make the sentence grammatical and keep the exact meaning and tone, so any combination reads naturally; "
            "options may differ in length but must not change facts or add claims; "
            "never nest groups (no braces inside a group); never add links or emojis; "
            "keep these placeholders exactly as written and outside of spin groups where possible: {name} {subreddit} {topic} {title}; "
            "keep it sounding like one real person typing, not a template. Preserve line breaks."
            + (f"\nYour previous attempt was rejected: {feedback}. Fix that." if feedback else ""),
            f"MESSAGE:\n{body}", max_tokens=1500, temperature=0.8)
        t = (r.get("template") or "").strip()
        problem = valid_spintax(body, t) if t else "empty result"
        if not problem:
            return t
        feedback = problem
    raise RuntimeError(f"Spintax writer could not produce a valid template ({feedback}). Try again or edit by hand.")
