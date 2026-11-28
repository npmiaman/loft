# UX tickets: Chrome extension

From the Nov 28 2026 UX + UI audit. Rule for every ticket: write the failing test first, then the fix, then rerun. Each phase ends with its own end-to-end test. Tick a box only when its test passes.

## Phase 0: safety (E2E: `tests/e2e/phase0.mjs`)
- [x] P0-1 Approve / Send now use the on-screen edit, not the saved text (`LeadsTab.tsx`)
- [x] P0-4 Every outgoing DM needs approval, whoever wrote it (template, page scan, import); "Skip AI scoring" defaults off; Start says what it will send (`lib/agent.ts`, `RunTab.tsx`, `MessagesTab.tsx`)
- [x] P0-5 In-app confirm (not `window.confirm`) for send now / approve & post / approve all / clear
- [x] P0-6 Never re-DM someone: a contacted set that "Clear" never deletes; "Clear unsent leads"
- [x] P0-10 One DM-cap function shared by the governor and the UI (SOP plan aware); status banner with Resume after a pause/captcha

## Phase 3: extension UX + UI (E2E: `tests/e2e/phase3.mjs`)
- [x] P3-1 5 tabs: Home, Review (DM drafts + comments + posts), Setup, Warm-up, Settings (gear)
- [x] P3-2 First-run flow (key → product → subreddits → safety) + Reddit login check
- [x] P3-3 Warm-up vocabulary: "DM ramp" vs "Warm-up plan"; the plan visibly controls scheduling
- [x] P3-4 Tokens, spacing utilities, Notice component, :focus-visible, ≥28px targets, dark mode
- [x] P3-5 Settings validation + Test key; Setup inline status; Leads search/sort/undo
- [x] P3-6 On-page HUD off Reddit's chat box; focusable Stop
