---
name: resolve-pr-feedback
description: Resolve GitHub PR review threads or acknowledge non-thread PR feedback in one shot using gh CLI. Use when the user asks to mark PR review comments handled, resolve a review thread, react to a PR conversation comment, or mark a comment as not relevant after triage. Prefer this after fixes are complete. For review threads, resolve the thread with an optional reply summary; for non-thread comments, use +1 when addressed and -1 when not relevant.
license: Apache-2.0
compatibility: Requires Python 3.8+ and gh CLI authenticated with repo scope
allowed-tools: Bash(python3:*) Bash(gh:*) Read Write
metadata:
  author: luandro
  version: "1.0.0"
---

# Resolve PR Feedback

Use **after the work is done** to mark PR feedback handled without improvising GitHub API calls.

Pair with `fetch-pr-unresolved-feedback` so you get exact `thread_id` / `comment_id` values and can pipe through `build_actions.py`.

## TL;DR — pick your path

```
How many items to act on?
├── 1  (single)       → scripts/resolve_pr_feedback.py       [preview: --dry-run]
└── 2+ (batch)        → scripts/apply_pr_feedback_actions.py [preview: --plan (human) or --dry-run (JSON)]

Always preview before mutating. The scripts run `gh auth status` internally — do not wrap them in another auth check.
```

## Decision model

For each item, pick a **kind** and a **decision**:

| Kind \ Decision | `addressed` | `not_relevant` |
|---|---|---|
| `review_thread` | Optional reply summary, then resolve thread | `-1` on root review comment, leave unresolved |
| `review_comment` | `+1` reaction | `-1` reaction |
| `issue_comment` | `+1` reaction | `-1` reaction |
| `review` (top-level review body) | **skipped** — not directly resolvable | **skipped** |

Rules of thumb:

- Only mark `addressed` when the code or explanation is actually complete.
- Reply summaries: **one sentence, always reference the commit SHA**. Preferred formats: `"Fixed in abc1234"`, `"Handled in abc1234: <what changed>"`, `"Clarified in abc1234"`. Avoid `"Done"` / `"Fixed"` with no SHA.
- Prefer `-1` for `not_relevant` — don't write argumentative replies unless the user asks for text.
- Never resolve a thread without a SHA reference in chat or reply — reviewers need to know which commit addressed them.

## Preconditions

- `gh` is installed and authenticated.
- The scripts call networked `gh api` endpoints; run with your agent environment's permissions.

## Scripts

| Script | Use when |
|---|---|
| `scripts/resolve_pr_feedback.py` | Exactly one action |
| `scripts/apply_pr_feedback_actions.py` | Multiple actions from a JSON list (runs in-process, no subprocess per action) |

## Single action

```bash
# Resolve a review thread with a reply summary
python3 scripts/resolve_pr_feedback.py --repo owner/repo --pr 179 \
  --kind review_thread --thread-id PRRT_xxx \
  --decision addressed --summary "Fixed in abc1234"

# +1 a conversation (issue) comment
python3 scripts/resolve_pr_feedback.py --repo owner/repo \
  --kind issue_comment --comment-id 123456789 --decision addressed

# -1 a review comment
python3 scripts/resolve_pr_feedback.py --repo owner/repo \
  --kind review_comment --comment-id 987654321 --decision not_relevant
```

You can replace `--repo` + `--pr` with `--url <PR or review URL>`.

**Validate first**: always run once with `--dry-run`, then re-run without it:

```bash
python3 scripts/resolve_pr_feedback.py --repo owner/repo --pr 179 \
  --kind review_thread --thread-id PRRT_xxx --decision addressed --dry-run
```

Add `-v` / `--verbose` for diagnostics on stderr.

## Batch mode

Preferred when resolving 2+ items — in-process, with per-action success/failure reporting.

### Fast path: use `build_actions.py` from `fetch-pr-unresolved-feedback`

```bash
# Paths below assume both skills are siblings; if installed separately,
# resolve each path via the agent's skill registry.
python3 <fetch path>/scripts/fetch_unresolved_pr_feedback.py \
  --repo owner/repo --pr 179 --minimal --output feedback.json

python3 <fetch path>/scripts/build_actions.py \
  --file feedback.json --summary "Fixed in abc1234" --validate --output actions.json

# Review/edit actions.json, then:
python3 scripts/apply_pr_feedback_actions.py --file actions.json --plan
python3 scripts/apply_pr_feedback_actions.py --file actions.json
```

### Manual `actions.json`

JSON array (or `{"actions": [...]}`):

```json
[
  {
    "repo": "owner/repo",
    "pr": 179,
    "kind": "review_thread",
    "thread_id": "PRRT_xxx",
    "comment_id": 123456789,
    "decision": "addressed",
    "summary": "Fixed in abc1234"
  },
  {
    "repo": "owner/repo",
    "kind": "issue_comment",
    "comment_id": 222333444,
    "decision": "not_relevant"
  }
]
```

Per-action required fields:

| kind | Required | Optional |
|---|---|---|
| `review_thread` | `repo`, `pr`, `thread_id`, `decision` | `comment_id` (auto-fetched if omitted), `summary` |
| `review_comment` | `repo`, `comment_id`, `decision` | — |
| `issue_comment` | `repo`, `comment_id`, `decision` | — |
| `review` | (any) | — (always skipped) |

Notes:

- `comment_id: null` is fine for `review_thread` — the script auto-fetches the root via GraphQL.
- `pr` is required for `review_thread` replies (to hit `/pulls/{pr}/comments/{id}/replies`). Omit it and `addressed` with a summary will fail.
- Unknown keys (e.g. `_context` from `build_actions.py`) are ignored.
- Alternative: use `"url": "https://github.com/.../pull/179"` instead of `repo` + `pr`.

### Run batch

```bash
python3 scripts/apply_pr_feedback_actions.py --file actions.json --plan              # human preview, no API calls
python3 scripts/apply_pr_feedback_actions.py --file actions.json --dry-run           # JSON preview per action
python3 scripts/apply_pr_feedback_actions.py --file actions.json                     # apply (exit 0 even if some fail)
python3 scripts/apply_pr_feedback_actions.py --file actions.json --exit-code         # apply, exit non-zero if any failed (CI / fail-fast)
python3 scripts/apply_pr_feedback_actions.py --file actions.json -v                  # apply + per-action stderr log
```

`--plan` prints one line per action + a kind/decision breakdown — prefer it for user confirmation. `--dry-run` is JSON (machine-parseable). `--exit-code` turns partial failures into a non-zero exit for CI / agent loops that need fail-fast; omit it to keep the default "always exit 0, inspect `results[]`" behavior.

Or via stdin:

```bash
echo '[{"repo":"owner/repo","kind":"issue_comment","comment_id":123,"decision":"addressed"}]' \
  | python3 scripts/apply_pr_feedback_actions.py --dry-run
```

Output shape: `{"count": N, "results": [{"index": i, "ok": bool, "result"|"error": ...}]}`. Failed actions carry `"ok": false` and `"error"` but don't abort the batch — other actions still run. Fix and re-run only the failures (or pass `--exit-code` to gate a CI step).

## Idempotency & re-running

- **Resolving an already-resolved thread** is a GraphQL no-op (safe to retry).
- **Reactions** are deduped per user, per comment, per content — a second `+1` from the same token silently returns 200 (safe to retry, but won't produce a visible change).
- **Reply summaries post a new comment every time**. Re-running an `addressed` `review_thread` action with a `summary` creates a duplicate reply on GitHub. Either re-run only the failed-index entries from `results[]`, or strip `summary` from already-replied actions before retrying.
- After a successful batch, re-run the fetcher to confirm counts dropped. Example: `python3 <fetch skill>/scripts/fetch_unresolved_pr_feedback.py --repo owner/repo --pr 179` and compare `summary` counts.

## Handling outstanding reviews

The fetcher surfaces `outstanding_reviews[]` — top-level review bodies. These are **not resolvable via API**. Handoff pattern: quote each `body` with `author` + review `url` in your report, ask the user to classify each as "addressed", "needs follow-up", or "ignore", and (if addressed) request a re-review via `gh pr comment` or direct them to do so manually.

## Behavioral rules

- Only mark `addressed` when work is complete. Every reply summary must reference a commit SHA.
- For `not_relevant`, prefer `-1` over argumentative replies unless the user asks for text.
- Let the script auto-fetch missing `comment_id` for review threads — don't probe by hand.
- `kind: review` entries are skipped; handle via the "Handling outstanding reviews" pattern.
- Never retry a failed action silently — surface the error, let the user decide.
- On big batches, `--plan` first; apply only after the user confirms.

## Error handling

| Symptom | Fix |
|---|---|
| `gh auth status` failure inside the script | Ask the user to run `gh auth login` and retry. |
| `Could not load review thread 'PRRT_xxx'` | Thread deleted, resolved by someone else, or id wrong. Re-fetch. |
| `PR number is required to reply…` | `review_thread` action missing `pr`. Add it. |
| `not_relevant review_thread requires a numeric root comment id…` | Add `comment_id` (from fetcher `root_comment_id`) or change decision. |
| Batch entry `"ok": false` | Inspect `error`; other actions still applied. Fix and re-submit only failures. |
| `kind: review` passed | Skipped with reason. Handle manually — see "Handling outstanding reviews". |
| `gh api` failure (rate limit / 403 / 404) | Surface stderr verbatim. Typical causes: rate limits, missing `repo` scope, stale ids. |
| Reactions return 200 but invisible | GitHub dedupes identical reactions per user. Check if you already reacted. |
