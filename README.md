# Pigeon — Reddit outreach agent (Chrome extension)

A cursor that browses Reddit like a person, finds people who fit your ICP, upvotes what's relevant, drafts comments, and sends DMs. The brain runs on the **DeepSeek API**.

## Install

```bash
npm install
npm run build          # outputs .output/chrome-mv3
```

Chrome → `chrome://extensions` → Developer mode → **Load unpacked** → select `.output/chrome-mv3`.
Log in to Reddit in that browser, click the Pigeon icon to open the side panel.

`npm run dev` gives you a hot-reloading dev build.

## First run (5 minutes)

1. **Settings** → paste your DeepSeek key, set your Reddit username. Models: `deepseek-flash` (default), `deepseek-v4-pro`.
2. **Setup** → enter your website → *Analyze my product* → edit the ICP → *Find subreddits*. Each subreddit is checked live (members, posting rate, ICP density over 25 real titles, rules risk); subs that ban promotion/DMs are disabled by default.
3. **Messages** → pick *AI writes each DM* or *Use my template*. For templates, write `{Hey|Hi|Hello} {name}, …` or hit **✨ Add spintax with AI**.
4. **Run** → *Find leads* first. Review in **Leads**, approve drafts, then switch to *Autopilot*.

Pigeon works in its **own Reddit window** (using your session). Keep it open and visible; don't minimize it.

## What it does

| Feature | Where |
|---|---|
| Visible cursor with curved, eased movement, hover/dwell, ripple on click, on-page Stop button | `lib/page/cursor.ts` |
| Browse a subreddit, read posts, open threads, read comments | `entrypoints/reddit.content.ts` |
| ICP profile from your site, subreddit discovery + rules-risk | `lib/brain.ts` |
| Two-stage lead scoring with a stored reason per lead | `lib/brain.ts` → `scoreItems` |
| Relevant-only upvotes (capped per session, never double-votes) | `lib/agent.ts` |
| Template DMs with `{name} {subreddit} {topic}` + `{a\|b\|c}` spintax | `renderTemplate` |
| **AI spintax writer** (validated: keeps variables, no nesting, no links, ≥8 variants, retries with feedback) | `addSpintax` |
| AI-written DMs per person, optional approval step | `writeDm` |
| Comment drafting, **approve → post**; post ideas (copy + open submit page) | Comments tab |
| Page DMer (right-click or button), List DMer (paste usernames), reply check, CSV export | Run / Leads tabs |
| Governor: daily cap, warm-up ramp, active hours, jittered gaps, auto-pause | `lib/governor.ts` |

**Human-like behavior** (`lib/page/persona.ts`, `cursor.ts`, `dom.ts`, `human.ts`) — nothing runs on a fixed rhythm:
- **Persona per session:** typing speed, typo rate, scroll style (wheel ticks vs trackpad glide), mouse speed, patience. Persisted across page loads, re-rolled after ~45 min, drifts slightly slower over time. *Settings → On-page speed* scales all of it.
- **Cursor:** irregular multi-waypoint routes (different every move), surges/lags and slight creep-back, hand tremor, overshoot-and-correct on far targets, hesitation mid-move, drifting while "reading".
- **Clicks:** never dead centre (wide spread, sometimes anywhere inside the target, left-biased on wide elements), varied settle time and press duration, occasional re-aim, and the odd **stray click on empty background** (only on non-interactive spots; never links, buttons, votes or posts).
- **Typing:** continuously drifting speed plus fast bursts and slow patches, per-word rhythm, fast common letter pairs, pauses after punctuation and before long words, "thinking" stops mid-message. Typos of four kinds (neighbouring key, swapped letters, doubled letter, skipped letter), noticed immediately or a few characters later, then backspaced and retyped. The sent text is always exactly what was intended.
- **Scrolling:** variable flick sizes, easings and gaps, glances/pauses, occasional scroll-back.
- **DMs and comments:** gap before each DM is right-skewed (many shortish, a few long) with a ~7% chance of a multi-minute break; a pause to re-read before sending; comments get longer think-before-typing and proofread pauses.

**Governor behavior:** DMs stop automatically on a rate-limit banner (paused 6h), a captcha (24h), or 3 failed sends in a row (1h).

## Tests

```bash
npm run test:logic     # spintax, warm-up ramp, active hours
npm run test:e2e       # ~4 min: real Chromium + this extension against a MOCK of Reddit's DOM and a mock DeepSeek
```

The e2e suite (`run`, `human`, `ui`, `agent`) covers: browse/scroll, link click + navigation, reading posts/comments, shadow-DOM upvotes, typing + posting a comment, sending a DM, rate-limit detection, abort, spintax generate/validate/retry, subreddit discovery, a full autopilot session, Stop, and a rate-limit pause; plus `human.mjs`, which measures that typing rhythm, typos, click spread, press durations, scroll profiles, cursor paths and stray clicks actually vary.
(First time: `npx playwright install chromium`.)

## What is NOT verified — read this

- **Nothing here has run against real Reddit.** The mock encodes my assumptions about Reddit's markup (`shreddit-post`/`shreddit-comment` attributes, a "Chat" button on profiles, a composer + "Send" button). The tests prove the *mechanics* work; they can't prove Reddit's current DOM matches.
- **The riskiest piece is the DM flow** (`dm()` in `reddit.content.ts`): Reddit's chat UI may open in an overlay, a new tab, or an iframe. Selectors are text/aria based and fall back to the legacy `/message/compose` page, but expect to adjust them on first contact.
- Reply detection (`scanInbox`) is a heuristic; use *Mark replied* if it misses.
- AI prompts (scoring, DMs, comments) have only been tested with mocked responses. Read real output before trusting auto-send; keep *Approve AI drafts* on until you do.
- Synthetic events are `isTrusted: false`. If Reddit ignores them for the chat composer, the fallback is `chrome.debugger` input (not built).

## First real-Reddit smoke test

1. *Run → Find leads* with one subreddit. Expect log lines like `Read N posts in r/x`. If `No posts could be read`, the post selectors need updating.
2. In **Leads**, write a message for one lead and press **Send now** to a second account you own. Watch the Pigeon window. Failures say which step broke: `no-chat-button`, `no-composer`, or `send-failed`.
3. Only then try Autopilot with a low daily cap.

## Layout

```
entrypoints/background.ts      message router, context menu, scheduler alarm
entrypoints/reddit.content.ts  page commands (browse, readPost, clickLink, upvote, dm, postComment, scan)
entrypoints/sidepanel/         React UI (Run, Setup, Leads, Messages, Comments, Settings)
lib/agent.ts                   session loop (service worker)
lib/brain.ts  lib/llm.ts       DeepSeek-powered brain + client
lib/governor.ts                caps, warm-up, pacing
lib/page/                      cursor, human timing, DOM helpers, Reddit reader
```

The DeepSeek key is stored in `chrome.storage.local` in your browser. Moving the LLM calls behind your own backend (so users don't bring keys) is the next step for distribution.
