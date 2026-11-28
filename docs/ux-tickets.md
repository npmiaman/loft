# UX tickets: dashboard, classic page, cloud viewer

From the Nov 28 2026 UX + UI audit. Rule for every ticket: write the failing test first, then the fix, then rerun. Each phase ends with its own end-to-end test. Tick a box only when its test passes.

## Phase 0: safety (E2E: `tests/test_ux_phase0.py`)
- [x] P0-1 Approve / Send now use the on-screen edit, not the saved text (`web/components/pigeon/tabs.tsx`)
- [x] P0-2 Edit dialog never wipes an account's proxy (`web/components/accounts/dialogs.tsx`)
- [x] P0-3 "Run on a cloud runner" works: `{}` body, opens `view`, popup opened before the await (`account-row.tsx`, `pigeon/desktop.py`)
- [x] P0-4 Every outgoing DM needs approval, whoever wrote it; Start says what it will send (`pigeon/agent/ops.py` + drawer copy)
- [x] P0-5 Styled confirm for send / post / approve-all / sending-mode Start / delete account / owner change
- [x] P0-6 Never re-DM someone: contacted history survives "Clear"; offer "Clear unsent leads"
- [x] P0-7 AI (DeepSeek) key field in agent Settings; no CLI instructions in the UI
- [x] P0-8 No-encryption-key screen: invite-code input + "Sign in again"
- [x] P0-9 Viewer: stop reconnecting after the session ended / 404, show a final state, warn on tab close, idle timeout (`pigeon/viewer.html`, `pigeon/runner.py`)

## Phase 1: dashboard design system (E2E: screenshot + interaction run over every screen, light/dark, 1280/390)
- [x] P1-1 Tokens: type scale, radii, overlay/on-ok colours, one focus mechanism
- [x] P1-2 Components: ConfirmDialog, EmptyState, Skeleton, Checkbox/Switch, IconButton, anchor buttonStyles, DataTable, formatWhen
- [x] P1-3 Copy: glossary + backend-string map in `web/lib/api.ts`, sentence case, OS names, no emoji
- [x] P1-4 Apply across all screens

## Phase 2: dashboard flows (E2E: sandbox walk-through)
- [x] P2-1 Team page: invites + roles in the new UI; then remove member, key rotation, backups, templates, runners
- [x] P2-2 Typed, grouped agent Settings with validation and unsaved-changes guard
- [x] P2-3 Optimistic row actions + connection-lost banner + Retry
- [x] P2-4 Accounts: status/tag filters, count, bulk actions
- [x] P2-5 People: search, sort, "showing N of M"; drawer feedback via toasts, not a raw text box
- [x] P2-6 Activity in sentences; Overview leads with outcomes
- [x] P2-7 Mobile header + drawer layout
- [x] P4-4 Retire "classic view" links once the above reaches parity

## Phase 4: classic page + viewer (E2E: viewer at a 390px phone viewport; classic page typing survives refresh)
- [x] P4-1 Viewer phone usability: zoom, tap-to-type, paste, 44px targets, layout
- [x] P4-2 Viewer status card, plain errors, action feedback
- [x] P4-3 Classic page: refresh-safe typing, confirms, readable errors, back link, dashboard tokens
