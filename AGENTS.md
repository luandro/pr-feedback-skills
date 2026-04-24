# Project: pr-feedback-skills

Two complementary Agent Skills for managing GitHub PR feedback: `fetch-pr-unresolved-feedback` (read-only) and `resolve-pr-feedback` (mutations). Designed as a pipeline: fetch -> build actions -> resolve.

## Architecture

```
pr-feedback-skills/
├── fetch-pr-unresolved-feedback/
│   ├── SKILL.md                    # Agent-facing instructions for the fetcher
│   └── scripts/
│       ├── fetch_unresolved_pr_feedback.py   # Main fetcher (GraphQL + REST)
│       └── build_actions.py                  # Converts feedback JSON -> actions JSON
├── resolve-pr-feedback/
│   ├── SKILL.md                    # Agent-facing instructions for the resolver
│   └── scripts/
│       ├── resolve_pr_feedback.py            # Single-action resolver
│       └── apply_pr_feedback_actions.py      # Batch resolver
└── README.md                       # Human-facing overview + pipeline docs
```

- `SKILL.md` files follow the [Agent Skills specification](https://agentskills.io/specification).
- Cross-skill paths use `<fetch path>` / `<resolve path>` placeholders; agents resolve via their skill registry.
- Both skills are Python scripts that call `gh api` (GraphQL + REST). No external Python dependencies.

## Conventions

- Frontmatter includes `allowed-tools: Bash(python3:*) Bash(gh:*) Read Write` to declare tool permissions.
- Description includes trigger keywords for agent discovery (fetch, inspect, triage, resolve, PR feedback, review thread).
- Error tables in SKILL.md map symptoms to fixes -- agents can self-correct without user intervention.
- `--dry-run` / `--plan` flags on all mutating scripts for safe preview before execution.
- Bot comments are included by default with `excluded_from_attention: true` -- agents can filter without losing data.
- The fetcher runs `gh auth status` internally -- never wrap scripts in another auth check.

## Key Commands

```bash
# Validate skills against spec
skills-ref validate ./fetch-pr-unresolved-feedback
skills-ref validate ./resolve-pr-feedback

# Run the full pipeline
python3 fetch-pr-unresolved-feedback/scripts/fetch_unresolved_pr_feedback.py --repo owner/repo --pr 179 --minimal --output feedback.json
python3 fetch-pr-unresolved-feedback/scripts/build_actions.py --file feedback.json --summary "Fixed in abc1234" --validate --output actions.json
python3 resolve-pr-feedback/scripts/apply_pr_feedback_actions.py --file actions.json --plan
python3 resolve-pr-feedback/scripts/apply_pr_feedback_actions.py --file actions.json
```

## Skill Writing Best Practices (from agentskills.io)

These apply to this and all sibling skills:

1. **Frontmatter in YAML, not body** -- `license`, `compatibility`, `metadata` belong in frontmatter per spec.
2. **Description triggers discovery** -- Include specific keywords agents use to match tasks to skills.
3. **Gotchas are highest-value** -- Environment-specific facts that defy reasonable assumptions.
4. **Procedures over declarations** -- Teach *how to approach* a class of problems, not *what to produce* for one instance.
5. **Defaults, not menus** -- Pick a default tool/approach; mention alternatives briefly.
6. **Aim for moderate detail** -- SKILL.md under 500 lines / 5000 tokens. Move deep reference to `references/`.
7. **Progressive disclosure** -- Tell the agent *when* to load each reference file, not just "see references/".
8. **Add what the agent lacks, omit what it knows** -- Skip generic knowledge; focus on project-specific conventions and non-obvious edge cases.
9. **Refine with real execution** -- Run the skill against real tasks, feed results back, iterate.
10. **Match specificity to fragility** -- Be prescriptive for fragile operations; give freedom where multiple approaches work.
