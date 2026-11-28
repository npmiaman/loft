# Pigeon Reddit Agent — Chrome Extension Build Plan

Goal: a Chrome extension that runs Reddit outreach for a user. A visible cursor browses Reddit in the user's own logged-in session, finds people who fit the product's ICP, and DMs them. It also finds threads worth commenting on or posting in.

## 0. What DmDad actually is (from its public pages, read 2026-11-26)

Not verified: the dashboard UI, the walkthrough video, onboarding fields, any Chrome Web Store listing (no store link found on any page).

| Capability | DmDad (per site) | What you want |
|---|---|---|
| Cursor that clicks like a human | Yes ("Magic Mustache Cursor") | Yes |
| DM from a page / search result / list / commenters / whole subreddit | Yes (Page DMer, List DMer, DM Commenters, DM Entire Subreddits) | Yes |
| Templates with `{name}` + spintax | Yes | Yes |
| AI-personalized DMs / spintax | Yes (Pro, credit-based) | Yes |
| Follow-ups, no-duplicate DMs (cloud DB), reply-rate tracking | Yes | Yes |
| Auto rate-limit detection, daily caps | Yes (25 free / 500 Pro / 5,000 Scale per day) | Yes, with lower defaults |
| **"Brain" that works out which subreddits hold the ICP** | **Not found** | **Yes, this is the product** |
| **Browses subreddits and scrolls, scoring people as it reads** | **Not found** (it DMs people already on a page) | **Yes** |
| **Reddit upvoting** | **Not found** (auto-like is X-only) | Yes |
| **Commenting / creating posts** | **Not found** | Yes |

A "1:1 replica" would be a DM sender. Your spec is a superset, and the brain is what DmDad doesn't have. Build DmDad parity first (Phase 1), then the brain on top. Copy the feature set. Don't copy their branding or mustache. Give the cursor its own identity.

## 1. Architecture

```
┌──────────────── Chrome (user's own logged-in Reddit session) ───────────────┐
│ Side panel UI (React)  ◄──►  Service worker (MV3)  ◄──►  Content script    │
│ onboarding, queue,           scheduler (chrome.alarms),   on reddit.com:     │
│ inbox, settings, logs        state machine, GOVERNOR,     cursor overlay,    │
│                              local DB (IndexedDB)         DOM driver, reader │
└───────────────────────────────────┬─────────────────────────────────────────┘
                                    │ HTTPS (auth token, no LLM keys in extension)
                          ┌─────────▼──────────┐
                          │ Pigeon API         │  auth + billing, LLM calls, dedup DB,
                          │ Postgres           │  selector registry, analytics
                          └────────────────────┘
```

**Stack:** WXT (or Plasmo) + TypeScript + React + Tailwind. Dexie for local queue and logs. Backend: Hono or Next route handlers on Postgres (Supabase). Stripe for billing. LLM via the Anthropic API from the backend only. Use a Haiku-class model for bulk lead classification and a Sonnet-class model for writing DMs and comments.

**Why the user's own browser:** the session is theirs, no credentials leave the machine, and it matches how DmDad says it runs. Trade-off: the computer must stay open with the tab active. Say so in onboarding.

### Key technical decisions (resolve in the Phase 0 spike)

1. **Input events.** `dispatchEvent` clicks and typing are `isTrusted: false`. Most Reddit handlers accept them, but chat inputs built on React/Lexical need the native-value-setter + `input` event trick.
   - Fallback: `chrome.debugger` (CDP `Input.dispatchMouseEvent` / `dispatchKeyEvent`) gives trusted events but shows a "debugging this browser" banner.
   - Start with synthetic events and switch only if sends fail.
2. **Reddit DM surface.** I have not inspected the current chat DOM. The spike must confirm: how a chat is opened for a username, the compose box, the send button, how replies and "message request" states appear, and how rate limits surface.
3. **DOM drift.** Reddit's new UI uses web components and shadow DOM, and it changes often. Build a selector registry with several strategies per element (aria-label, role+text, structure). Serve it from the backend so it can be patched without a Web Store review. Add canary checks that report breakage.
4. **Fake cursor.** An absolutely positioned overlay element that follows eased Bézier paths with variable speed and overshoot. It hovers before clicking and dwells while "reading" (dwell time proportional to post length). It's a product feature (users watch it work) and it paces the actions.

## 2. The brain (the differentiator)

**Onboarding → Product Profile.** The user pastes their URL. The backend scrapes it and the LLM produces: what it does, ICP personas, pain-point phrases in the words real people use, competitor names, and "buying signal" patterns ("anyone recommend…", "alternative to X", "frustrated with…"). The user edits and confirms.

**Subreddit discovery.**
- Generate candidate subs from the profile, then verify each through Reddit search/about pages: subscribers, posts per day, and whether the posts match the ICP (sample ~25 titles and score them).
- Parse each sub's rules for self-promo and DM restrictions. Rank by ICP density × activity × safety. Auto-exclude subs that ban solicitation.
- Output a ranked sub list the user can edit. Re-run weekly.

**Lead qualification.** Two stages, to keep cost down:
1. Cheap heuristic filter on post/comment text (keyword and intent patterns, account age, karma floor).
2. LLM classification of survivors. Input: the post or comment and optionally a sample of the author's recent public activity. Output: `fit_score 0–100`, `intent` (asking / complaining / researching / unrelated), a one-line `reason`, and `dm_ok` (false for minors, distress posts, or people who said "no DMs").
Store the reason with the lead. It feeds the DM and gives the user an audit trail.

**Opportunities (comment / post).** The same classifier flags threads where a helpful reply fits, and recurring questions worth a post. The LLM drafts, and the **user approves in v1** (autopilot is a later toggle). Comments are the fastest route to a sub ban, so approval stays on by default.

## 3. Agent loop

A **session** is 20–45 minutes, scheduled inside the user's active hours:

1. Pick the next subreddit by score and recency.
2. Navigate like a user: use search or the sub's page, choose a sort, scroll with variable speed and pauses, and open some posts.
3. Read: extract the post, top comments, and authors. Send batches to the backend for scoring. Add qualifying leads to the queue with source URL and reason.
4. Engage lightly: upvote only posts and comments it judged relevant, within a small per-session cap. No bulk or rapid voting.
5. DM phase: pull top-scored leads from the queue, subject to the governor's caps. Open the chat, send, log.
6. Between sessions: poll the chat inbox for replies, mark conversations, and schedule follow-ups.

## 4. DM engine

- **Modes:** (a) template with `{name}` and spintax, (b) AI-personalized per lead from the stored `reason` and the source post. The user can approve or edit the first N, then flip to auto.
- **Rules baked in:** no links in the first message, no duplicate sends (local + cloud check), one follow-up max by default with a delay, and a hard stop on any reply.
- **Stop-words:** "stop", "not interested", "spam", or "report" in a reply → permanent suppress, pause the account for the day, and surface it.
- **Reply inbox:** conversations land in the side panel with AI-suggested replies. A human takes over. This is where deals close, so it's worth getting right.
- **Cross-customer dedup:** the backend dedups across all Pigeon workspaces so one person isn't pitched by five different customers. It cuts report rates and protects accounts.

## 5. Governor (safety layer, non-negotiable)

Every action goes through one gate:
- Per-account daily DM cap with a warm-up ramp (e.g. 5 → 10 → 15 → 25 → 40 by day), configurable with a low hard ceiling. DmDad's FAQ claims Reddit allows ~100/day. That's unsourced, so don't trust it. Default conservative and raise from real data.
- Jittered delays, active-hours window, and per-session action budgets.
- Immediate pause on: rate-limit toast, CAPTCHA, "can't send message", account restriction signals, or an unusual spike in negative replies.
- Global kill switch in the side panel and a full action log.
- **Single account per user.** No account rotation, proxy cycling, fingerprint spoofing, or account marketplace. DmDad's lifetime deal bundles a seller list. We shouldn't. It's also what turns a ToS gray area into outright ban evasion.

## 6. Data model (Postgres, mirrored in IndexedDB where needed)

`workspace`, `reddit_account` (username, warmup_day, health), `product_profile`, `icp`, `subreddit` (score, rules_flags), `lead` (username, source_url, fit_score, intent, reason, status), `template`, `campaign`, `message` (variant, sent_at, status), `conversation` (state, last_reply_at), `event_log`, `selector_registry` (versioned).

## 7. Phased build

| Phase | Time | Ships | Exit criteria |
|---|---|---|---|
| **0. Spike** | Days 1–3 | Content script that drives a test account through open chat → type → send → read reply, plus the cursor overlay | 20 DMs between two test accounts with zero manual touch; synthetic-vs-debugger decision made; chat DOM documented |
| **1. DmDad parity** | Wk 1–2 | Side panel, page DMer (search/members/commenters), CSV list DMer, templates + spintax, local queue, governor v1, kill switch | Install on a fresh Chrome profile, run a 25-DM campaign end to end |
| **2. Brain** | Wk 2–4 | Backend, auth, onboarding → profile → subreddit discovery → browse loop → lead scoring → lead queue | Given a product URL, it produces a sensible sub list and 50 qualified leads with reasons, which you judge by hand |
| **3. Conversations** | Wk 4–5 | AI-personalized DMs, follow-ups, reply detection, inbox with suggested replies, dedup, funnel analytics | Reply rate tracked per template; no duplicate sends in testing |
| **4. Engagement + launch** | Wk 5–6 | Comment/post drafting with approval, light upvoting, Stripe, Chrome Web Store submission or fallback distribution, closed beta of ~10 users | 10 beta users, 1 week of runs, zero unreviewed comments posted |
| **5. Hardening** | Ongoing | Remote selector registry, canary monitoring, E2E tests against real Reddit, account-health dashboard | Selector breakage patched in under a day without a store release |

Your existing `silo` test suite and `reddit-leads` skill cover parts of the research and lead-finding pipeline. Check whether that logic can be reused for Phase 2 before writing it from scratch. I haven't looked at that code.

## 8. Risks

1. **Reddit ToS.** Automated DMs likely violate it, and DmDad's own page says so and disclaims account restrictions. The cost lands on your users' accounts and on you. Mitigations are the governor, approval gates, and honest onboarding copy. They reduce the risk, they don't remove it.
2. **Chrome Web Store.** Spam and automation policies may reject this. I found no store link for DmDad. Plan for rejection: ship as a zip loaded unpacked in developer mode for the beta, and keep the extension Chromium-compatible (Brave, Edge). Decide before Phase 4.
3. **DOM/chat changes.** Mitigated by the remote selector registry and canaries.
4. **Reply quality.** Generic AI DMs get reported. The ICP score threshold and the `reason` line are the quality lever. Set the threshold high.
5. **Active-tab dependence.** Throttled background tabs break timing. Run in a dedicated pinned window and tell users to use a spare machine or VM.
