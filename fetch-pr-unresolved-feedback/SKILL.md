---
name: fetch-pr-unresolved-feedback
description: Fetch unresolved GitHub PR review threads, outstanding review bodies, and conversation comments in one shot using gh CLI and GraphQL. Use when the user asks to fetch, inspect, list, summarize, or triage unresolved PR comments or reviews from a PR URL, review URL, PR number, or the current branch PR. Do not use for applying fixes or replying; this skill is read-only.
license: Apache-2.0
compatibility: Requires Python 3.8+ and gh CLI authenticated with repo scope
allowed-tools: Bash(python3:*) Bash(gh:*) Read Write
metadata:
  author: luandro
  version: "1.0.0"
---

# Fetch PR Unresolved Feedback

Read-only skill that returns the **unresolved feedback surface** of a GitHub PR as structured JSON. Pairs with `resolve-pr-feedback` (this skill → `build_actions.py` → batch resolver).

The fetcher runs `gh auth status` internally — don't wrap it in another auth check. Never hand-roll `gh api` or REST calls.

## Scripts

| Script | Purpose |
|---|---|
| `scripts/fetch_unresolved_pr_feedback.py` | Fetch the unresolved feedback JSON |
| `scripts/build_actions.py` | Convert that JSON into a ready-to-edit `actions.json` for `resolve-pr-feedback` batch mode |

## Preconditions

- `gh` is installed and authenticated.
- The script calls networked `gh api` endpoints; run with your agent environment's permissions.

## Workflow

### Step 1 — Pick the input form

| User gave you | Command |
|---|---|
| Nothing (current branch) | `python3 scripts/fetch_unresolved_pr_feedback.py` |
| `owner/repo` + PR number | `python3 scripts/fetch_unresolved_pr_feedback.py --repo owner/repo --pr 179` |
| PR URL or review URL | `python3 scripts/fetch_unresolved_pr_feedback.py --url <url>` |

Optional flags (compose with any form above):

| Flag | Effect | When to use |
|---|---|---|
| `--filter-path <path>` | Only threads touching that file | Large PR, agent is focused on one file |
| `--include-all` | Include PR author + bot comments | User explicitly asked for everything |
| `--output feedback.json` | Write JSON to file | Piping into `build_actions.py`, or saving tokens |
| `--minimal` | Drop verbose fields (timestamps, URLs, long bodies truncated to 200 chars) | Big PRs, token-constrained context windows |

**Token-size tip**: on PRs with many comments, combine `--output feedback.json` + `--minimal`. A 50-thread PR typically shrinks ~40–70% with `--minimal` while keeping everything `build_actions.py` and the resolver need.

### Step 2 — Report to the user

Read the JSON and summarize. Default summary format:

```
PR #179 (owner/repo) — N unresolved thread(s), M outstanding review(s), K conversation comment(s)
1. [src/main.rs:42] @reviewer: <first line of body>…  (thread_id: PRRT_xxx)
2. [src/lib.rs:10]  @reviewer: <first line of body>…  (thread_id: PRRT_yyy)
...
```

Then stop. This skill is read-only — do **not** apply fixes or replies here.

### Step 3 — Hand off (only if the user asks to act)

If the user wants to resolve or react, either:

- For a single item: call `resolve-pr-feedback` with the `thread_id` / `comment_id` from the report.
- For multiple items: pipe through the bundled builder:

  ```bash
  # Paths below assume both skills are siblings; if installed separately,
  # resolve each path via the agent's skill registry.
  python3 scripts/fetch_unresolved_pr_feedback.py --repo owner/repo --pr 179 --minimal --output feedback.json
  python3 scripts/build_actions.py --file feedback.json --summary "Fixed in <sha>" --validate --output actions.json
  # Let the user review/edit actions.json, then preview before mutating:
  python3 <resolve path>/scripts/apply_pr_feedback_actions.py --file actions.json --plan
  python3 <resolve path>/scripts/apply_pr_feedback_actions.py --file actions.json
  ```

## Output shape

- `pull_request` — `{owner, repo, number, url, title, state, author}`
- `summary` — counts: `unresolved_review_thread_count`, `unresolved_review_comment_count`, `outstanding_review_count`, `conversation_comment_count`, `total_attention_items`
- `unresolved_review_threads[]` — source of truth for inline review state. Each carries `thread_id`, `root_comment_id` (nullable — resolver auto-fetches), `path`, `line`, `is_outdated`, and `comments[]`.
- `outstanding_reviews[]` — top-level review bodies in `CHANGES_REQUESTED` / `COMMENTED` state. **Report-only** — quote the `body` for manual follow-up.
- `conversation_comments[]` — top-level PR issue comments (no native resolved state).

Rules: default excludes bot + PR-author comments (pass `--include-all` only when asked); stop after reporting — never mutate from this skill.

## `build_actions.py`

Turns fetcher output into a `resolve-pr-feedback` action list. Every unresolved thread + conversation comment becomes one action; outstanding reviews are skipped.

```bash
python3 scripts/build_actions.py --file feedback.json --summary "Fixed in abc1234" --validate --output actions.json
cat feedback.json | python3 scripts/build_actions.py --decision not_relevant
```

| Flag | Effect |
|---|---|
| `--file / -f`, `--output / -o` | Fetcher JSON input (stdin if omitted), actions JSON output (stdout if omitted) |
| `--decision` | `addressed` (default) or `not_relevant` — applied to every action |
| `--summary` | Default reply text for `addressed` `review_thread` actions |
| `--drop-context` | Omit the `_context` hint (`path:line` or `@author`) added for human review |
| `--validate` | Warn + exit non-zero on missing `repo`, `addressed review_thread` without `summary`, missing thread/comment ids. |

Field mapping (for hand-built actions): `thread_id` → resolver `thread_id` (kind `review_thread`); `root_comment_id` → `comment_id` (nullable); `conversation_comments[].id` → `comment_id` (kind `issue_comment`). `outstanding_reviews[]` is not resolvable — skipped.

**Review the generated `actions.json` before applying** — it's a scaffold, not a policy.

## Error handling

| Symptom | Fix |
|---|---|
| `gh auth status` failure inside the script | Ask the user to run `gh auth login` and retry. |
| `Empty output from gh …; expected JSON` | A `gh` extension is shadowing / misbehaving. Run `gh extension list`; remove the offender or pass `--repo` + `--pr` explicitly to skip auto-detection. |
| `GraphQL response missing field at path repository.pullRequest…` | PR doesn't exist, was moved, or the token lacks access. |
| No PR for the current branch | Ask for a PR URL or `--repo` + `--pr`. |
| `Could not parse PR URL` | Confirm the URL is `https://github.com/owner/repo/pull/<n>`. |
| All counts zero in `summary` | Report "no unresolved feedback" and stop. Don't silently re-run with `--include-all`. |
| `--filter-path` returns no threads | Report the path is clean; confirm spelling. |
| GraphQL / REST errors bubble as `RuntimeError` | Surface verbatim; never silently retry. |
| `build_actions.py` emits `"repo": null` | Fetcher input malformed / truncated. Re-fetch. |
| `build_actions.py --validate` → `addressed without summary` | Add a `summary` (with commit SHA) or flip to `not_relevant`. |
| `--minimal` missing a field downstream needs | Re-fetch without `--minimal`; it's a subset optimized for the resolver pipeline. |

