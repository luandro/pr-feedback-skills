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
| Nothing (current branch) | `python3 scripts/fetch_unresolved_pr_feedback.py --format text` |
| `owner/repo` + PR number | `python3 scripts/fetch_unresolved_pr_feedback.py --repo owner/repo --pr 179 --format text` |
| PR URL or review URL | `python3 scripts/fetch_unresolved_pr_feedback.py --url <url> --format text` |

Optional flags (compose with any form above):

| Flag | Effect | When to use |
|---|---|---|
| `--filter-path <path>` | Only threads touching that file | Large PR, agent is focused on one file |
| `--threads-only` | Return only unresolved review threads; skip outstanding reviews and conversation comments (also skips the REST + reviews API calls) | Fastest path when you only care about inline thread feedback |
| `--exclude-bots` | Filter out bot + PR-author comments | Human-only signal; bot threads show `excluded_from_attention: true` by default but are still returned |
| `--format text` (or `-f text`) | Render as plain text with full bodies, numbered, ids inline — no jq needed | Default for agent consumption; use `--format json` when piping into `build_actions.py`. Note: `-f` in `build_actions.py` means `--file`, not `--format` — use the long form when composing pipelines |
| `--output feedback.json` | Write output to file instead of stdout | Piping into `build_actions.py`, or saving tokens |
| `--minimal` | Drop comment bodies and truncate to 200 chars (JSON only) | Big PRs, token-constrained context windows |

**Bot comment behaviour**: bot-authored review threads (capy-ai, greptile-apps, chatgpt-codex-connector, etc.) are included by default because they are common and actionable. Their per-comment `excluded_from_attention` field is `true`, so the resolver pipeline and human agents can distinguish them from human reviewers. Pass `--exclude-bots` only when you want to suppress them entirely.

**Token-size tip**: on PRs with many comments, combine `--output feedback.json` + `--minimal`. A 50-thread PR typically shrinks ~40–70% with `--minimal` while keeping everything `build_actions.py` and the resolver need.

### Step 2 — Report to the user

With `--format text` the output is already agent-readable — full bodies, numbered, `thread_id` and `comment_id` inline. Read it directly and relay the summary. No JSON parsing or jq needed.

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

Rules: default includes all comments including bots (pass `--exclude-bots` to suppress them); stop after reporting — never mutate from this skill.

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
| All counts zero in `summary` | Report "no unresolved feedback" and stop. |
| `--filter-path` returns no threads | Report the path is clean; confirm spelling. |
| GraphQL / REST errors bubble as `RuntimeError` | Surface verbatim; never silently retry. |
| `build_actions.py` emits `"repo": null` | Fetcher input malformed / truncated. Re-fetch. |
| `build_actions.py --validate` → `addressed without summary` | Add a `summary` (with commit SHA) or flip to `not_relevant`. |
| `--minimal` missing a field downstream needs | Re-fetch without `--minimal`; it's a subset optimized for the resolver pipeline. |

