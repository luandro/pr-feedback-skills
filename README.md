# PR Feedback Skills

[![Agent Skills](https://img.shields.io/badge/Agent%20Skills-Spec%20Compliant-blue)](https://agentskills.io)
[![License: Apache 2.0](https://img.shields.io/badge/License-Apache%202.0-green.svg)](LICENSE)
[![Python 3.8+](https://img.shields.io/badge/Python-3.8%2B-blue.svg)](https://www.python.org/)

Two complementary [Agent Skills](https://agentskills.io) for managing GitHub PR feedback with any AI coding agent.

**fetch-pr-unresolved-feedback** retrieves all unresolved review threads, outstanding reviews, and conversation comments from a PR as structured JSON. **resolve-pr-feedback** resolves threads, adds reactions, and replies in batch -- all through the `gh` CLI.

Together they form a pipeline: **fetch -> build actions -> resolve**.

## Quick Start

```bash
# Install both skills
npx skills add luandro/pr-feedback-skills

# Fetch unresolved feedback from a PR
python3 scripts/fetch_unresolved_pr_feedback.py --repo owner/repo --pr 42

# Resolve threads after fixing
python3 scripts/resolve_pr_feedback.py --repo owner/repo --pr 42 \
  --kind review_thread --thread-id PRRT_xxx \
  --decision addressed --summary "Fixed in abc1234"
```

## Install

```bash
npx skills add luandro/pr-feedback-skills
```

Preview available skills before installing:

```bash
npx skills add luandro/pr-feedback-skills --list
```

Install non-interactively (CI-friendly):

```bash
npx skills add luandro/pr-feedback-skills -y
```

Works with **Claude Code**, **Cursor**, **Copilot**, **Aider**, **OpenCode**, and [40+ more agents](https://github.com/vercel-labs/skills#available-agents).

<details>
<summary>Alternative: manual install</summary>

```bash
git clone https://github.com/luandro/pr-feedback-skills.git
# Then point your agent to the cloned directory
```

</details>

## Prerequisites

- **GitHub CLI** (`gh`) installed and authenticated with `repo` scope
- **Python 3.8+**

Verify setup:

```bash
gh auth status
python3 --version
```

## Skills

### fetch-pr-unresolved-feedback

Read-only skill that returns the unresolved feedback surface of a GitHub PR as structured JSON.

```bash
# Current branch's PR
python3 scripts/fetch_unresolved_pr_feedback.py

# Specific repo + PR number
python3 scripts/fetch_unresolved_pr_feedback.py --repo owner/repo --pr 179

# PR URL
python3 scripts/fetch_unresolved_pr_feedback.py --url https://github.com/owner/repo/pull/179

# Token-efficient output for large PRs
python3 scripts/fetch_unresolved_pr_feedback.py --repo owner/repo --pr 179 --minimal --output feedback.json
```

Key flags: `--filter-path <path>`, `--include-all`, `--minimal`, `--output <file>`

See [`fetch-pr-unresolved-feedback/SKILL.md`](fetch-pr-unresolved-feedback/SKILL.md) for full documentation.

### resolve-pr-feedback

Resolve threads, react to comments, and reply with summaries -- single or batch mode.

```bash
# Single action (dry-run first)
python3 scripts/resolve_pr_feedback.py --repo owner/repo --pr 179 \
  --kind review_thread --thread-id PRRT_xxx \
  --decision addressed --summary "Fixed in abc1234" --dry-run

# Batch mode via actions file
python3 scripts/apply_pr_feedback_actions.py --file actions.json --plan
python3 scripts/apply_pr_feedback_actions.py --file actions.json
```

See [`resolve-pr-feedback/SKILL.md`](resolve-pr-feedback/SKILL.md) for full documentation.

## Pipeline: Fetch -> Build Actions -> Resolve

The two skills are designed to work together. Use `build_actions.py` (bundled with the fetcher) to convert feedback JSON into a ready-to-edit actions file for the resolver:

```bash
# 1. Fetch unresolved feedback
python3 fetch-pr-unresolved-feedback/scripts/fetch_unresolved_pr_feedback.py \
  --repo owner/repo --pr 179 --minimal --output feedback.json

# 2. Build actions scaffold
python3 fetch-pr-unresolved-feedback/scripts/build_actions.py \
  --file feedback.json --summary "Fixed in abc1234" --validate --output actions.json

# 3. Review & edit actions.json, then preview
python3 resolve-pr-feedback/scripts/apply_pr_feedback_actions.py --file actions.json --plan

# 4. Apply
python3 resolve-pr-feedback/scripts/apply_pr_feedback_actions.py --file actions.json
```

When both skills are installed via `npx skills add`, they become siblings under the agent's skill directory (e.g., `.claude/skills/`). Agents resolve cross-skill paths through their skill registry.

## Cross-Skill Path Resolution

The SKILL.md files use `<fetch path>` and `<resolve path>` placeholders for cross-skill references. When installed via `npx skills add`, both skills land as sibling directories:

```
.claude/skills/
├── fetch-pr-unresolved-feedback/
└── resolve-pr-feedback/
```

Agents that support the Agent Skills spec resolve these paths via their skill registry. For manual usage, replace the placeholders with the actual paths to each skill's `scripts/` directory.

## Decision Model

| Kind | `addressed` | `not_relevant` |
|---|---|---|
| `review_thread` | Reply summary + resolve thread | `-1` reaction, leave unresolved |
| `review_comment` | `+1` reaction | `-1` reaction |
| `issue_comment` | `+1` reaction | `-1` reaction |
| `review` (top-level) | Skipped (not API-resolvable) | Skipped |

Rules:
- Only mark `addressed` when work is complete
- Reply summaries must reference a commit SHA
- Prefer `-1` for `not_relevant` over argumentative replies
- Always preview with `--plan` or `--dry-run` before mutating

## Validation

Validate skills against the [Agent Skills specification](https://agentskills.io/specification):

```bash
# Using skills-ref (from agentskills/agentskills repo)
skills-ref validate ./fetch-pr-unresolved-feedback
skills-ref validate ./resolve-pr-feedback

# Generate prompt XML for custom harnesses
skills-ref to-prompt ./fetch-pr-unresolved-feedback ./resolve-pr-feedback
```

## Output Format

The fetcher returns structured JSON:

```json
{
  "pull_request": { "owner": "...", "repo": "...", "number": 179, "url": "...", "title": "...", "state": "...", "author": "..." },
  "summary": { "unresolved_review_thread_count": 3, "outstanding_review_count": 1, "conversation_comment_count": 2, "total_attention_items": 6 },
  "unresolved_review_threads": [ { "thread_id": "PRRT_xxx", "path": "src/main.rs", "line": 42, "comments": ["..."] } ],
  "outstanding_reviews": [ { "body": "...", "author": "...", "url": "..." } ],
  "conversation_comments": [ { "id": 123, "body": "...", "author": "..." } ]
}
```

The resolver batch output:

```json
{ "count": 3, "results": [ { "index": 1, "ok": true, "result": { "..." } } ] }
```

## License

[Apache License 2.0](LICENSE)
