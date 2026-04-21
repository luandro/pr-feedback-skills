# Test Suite Implementation Plan

## Objective

Add a maintainable pytest suite for `pr-feedback-skills` that covers the four Python scripts and `update-skills.sh` with high confidence and low brittleness.

The suite must validate current behavior, not an imagined refactor target. Where the current implementation is arguably imperfect, tests should document the behavior explicitly unless this plan calls for a production change first.

## Current Code Reality

- The repo has four Python executables:
  - `fetch-pr-unresolved-feedback/scripts/fetch_unresolved_pr_feedback.py`
  - `fetch-pr-unresolved-feedback/scripts/build_actions.py`
  - `resolve-pr-feedback/scripts/resolve_pr_feedback.py`
  - `resolve-pr-feedback/scripts/apply_pr_feedback_actions.py`
- The repo has one shell deployment script:
  - `update-skills.sh`
- There is no existing test harness, fixture set, or pytest config.
- The fetcher and resolver both wrap `gh` CLI calls through `subprocess`, but they do not share an implementation. They must be tested independently.
- `apply_pr_feedback_actions.py` imports `resolve_pr_feedback.py` via a local `sys.path.insert`, so test imports must match that layout.
- The fetcher has a live text-rendering path (`--format text`) via `_at()` and `_format_text()`.
- The fetcher default is effectively “include everything except when `--exclude-bots` is passed”. The hidden `--include-all` flag exists only for backwards compatibility.
- `build_unresolved_threads()` does not drop unresolved threads just because all comments are excluded from attention or because the thread has zero comments. It preserves the thread and annotates comments with `excluded_from_attention`.
- `build_actions.py` defaults generated actions to `decision="addressed"` and an empty summary. `validate_actions()` intentionally warns on addressed review-thread actions without a summary.
- The resolver is materially less robust than the fetcher around CLI JSON handling. In particular, `resolve_pr_feedback.py` currently lacks the fetcher-style NO_COLOR environment override, ANSI stripping, and rich empty-output diagnostics in `run()` / `run_json()`. Field logs show this causing `root_comment_id_for_thread()` to fail when `gh api graphql` returns non-JSON output.
- `_GRAPHQL_PR_CORE` in the fetcher is dead code. It is out of scope for test coverage and should be removed in a separate cleanup change.

## Test Design Principles

1. Prefer semantic contract checks over brittle implementation snapshots.
   - Verify required fields, behavior, and control flow.
   - Do not lock tests to exact list lengths or query whitespace when a less brittle assertion is enough.

2. Test the two duplicated helper stacks independently.
   - `run()` / `run_json()`
   - `parse_repo_name()` / `parse_pr_url()`
   - `resolve_pr_ref()`

3. Distinguish three layers cleanly.
   - Pure data shaping functions: no mocks.
   - CLI wrapper functions: patch `subprocess.run` or local wrappers.
   - `main()` orchestration: patch dependency functions and assert stdout, stderr, files, and exit codes.

4. Document known edge cases without normalizing them away.
   - `comment_id: 0` fallback behavior in batch apply
   - addressed review-thread actions without summaries producing validation warnings
   - `--minimal` being ignored when `--format text` is selected

## Implementation Plan

### Phase 1: Test Infrastructure

- [x] Add `pytest` configuration via `pytest.ini` or `pyproject.toml`
  - Set `testpaths = tests`
  - Add both skill `scripts/` directories to `pythonpath`
  - Make the import order explicit so `fetch_unresolved_pr_feedback`, `build_actions`, `resolve_pr_feedback`, and `apply_pr_feedback_actions` are imported from the repo under test, not from any installed skill copy elsewhere on disk

- [x] Create `tests/`
  - `conftest.py`
  - `fixtures/`
  - `__init__.py`

- [ ] In `tests/conftest.py`, add shared fixtures for:
  - `PullRequestRef` instances
  - GraphQL thread, review, and comment node factories
  - representative fetcher output payloads
  - `subprocess.run` result factories
  - temp JSON file helpers using `tmp_path`
  - a small import bootstrap helper that inserts both `scripts/` directories into `sys.path` before importing modules under test, matching the local `sys.path.insert` assumption in `apply_pr_feedback_actions.py`

- [ ] Add fixture files only where realism improves confidence
  - `fixtures/sample_feedback.json`
  - `fixtures/sample_actions.json`
  - `fixtures/graphql/threads_page1.json`
  - `fixtures/graphql/threads_page2.json`
  - `fixtures/graphql/threads_with_meta.json`
  - `fixtures/graphql/reviews.json`
  - `fixtures/graphql/comments.json`
  - `fixtures/graphql/error_response.json`
  - `fixtures/graphql/get_thread_query.json`

### Phase 2: Pure Unit Tests for the Fetcher

Target: `fetch-pr-unresolved-feedback/scripts/fetch_unresolved_pr_feedback.py`

- [ ] Test parsing and actor helpers
  - `parse_repo_name()`
  - `parse_pr_url()`
  - `is_bot_login()`
  - `is_bot_author()`
  - `keep_actor()`

- [x] Test fetcher PR resolution fallbacks
  - explicit `--url`
  - explicit `--repo` + `--pr`
  - `--pr` without `--repo` falling back to `gh repo view`
  - bare invocation with no `--url`, `--repo`, or `--pr` falling back to `gh pr view`
  - helpful error when branch PR metadata cannot be resolved

- [ ] Test `build_unresolved_threads()` against actual behavior
  - unresolved threads are included
  - resolved threads are excluded
  - `filter_path` filters by exact path
  - comments are preserved even when excluded from attention
  - threads with only excluded comments are dropped when `include_all=False`
  - threads with empty comment lists are retained only when `include_all=True`
  - `comments: None`, `comments: {"nodes": None}`, and `comments: {"nodes": []}` are handled
  - `root_comment_id` picks the first non-`None` `databaseId`
  - field mapping is correct for `path`, `line`, `original_line`, `diff_side`, and `is_outdated`

- [ ] Test `build_outstanding_reviews()`
  - `CHANGES_REQUESTED` and `COMMENTED` with non-empty bodies are candidates
  - empty-body `COMMENTED` reviews are excluded
  - latest-state-wins behavior for `APPROVED` and `DISMISSED`
  - bot and PR-author filtering with `include_all=False`
  - results preserve `excluded_from_attention` semantics
  - order follows sorted `submittedAt`

- [ ] Test `build_conversation_comments()`
  - regular comments included with correct field mapping
  - bot comments excluded when `include_all=False`
  - PR author comments excluded when `include_all=False`
  - empty bodies excluded
  - `html_url` preferred over `url`
  - missing `user` handled safely

- [ ] Test `_apply_minimal()`
  - body truncation at 200 chars for threads/comments and 400 chars for reviews
  - `line` falls back to `original_line`
  - missing comments produce `author=None` and empty body
  - summary and pull request metadata pass through
  - verbose thread-only fields are dropped from minimal output

- [ ] Test `_at()` and `_format_text()`
  - `@(unknown)` fallback
  - thread, review, and conversation-comment sections render when present
  - `[OUTDATED]` and `[bot]` markers render when expected
  - “No unresolved feedback found.” renders for all-empty results
  - output ends with a trailing newline

### Phase 3: Unit Tests for `build_actions.py`

Target: `fetch-pr-unresolved-feedback/scripts/build_actions.py`

- [ ] Test `_pr_meta()`
  - valid pull request metadata
  - missing owner/repo
  - missing `pull_request`

- [x] Test `build_actions()`
  - unresolved threads become `review_thread` actions
  - conversation comments become `issue_comment` actions
  - outstanding reviews are intentionally skipped
  - `root_comment_id` becomes `comment_id` only when present
  - `_context` formatting for `path:line`, bare path, and `@author`
  - default decision applied to every action
  - summary only attached when `default_decision == "addressed"` and `default_summary` is non-empty
  - empty feedback returns `[]`
  - malformed feedback with missing `pull_request` produces actions with `repo=None` / `pr=None` so downstream validation catches the schema regression

- [x] Test `validate_actions()`
  - valid action lists return `[]`
  - missing `repo` / `url` / `pull_request`
  - `review_thread` without `thread_id`
  - addressed review-thread action without summary
  - addressed review-thread action without `pr` or `url`
  - `issue_comment` or `review_comment` without `comment_id`
  - missing `kind`
  - unknown `kind`
  - invalid `decision`
  - multiple warnings can be emitted for one action

### Phase 4: GraphQL and Fetch-Layer Contract Tests

Target: fetcher GraphQL helpers

- [x] Test `_run_graphql()`
  - expected `gh api graphql` base command includes owner, repo, number, and query via stdin
  - cursor flag included only when both cursor name and cursor value are supplied
  - GraphQL `errors` raises `RuntimeError`
  - successful payload returns unchanged

- [ ] Test `_paginate()`
  - single-page collection
  - two-page and three-page pagination
  - `meta_capture` populated from the first page only
  - missing path segments raise a contextual `RuntimeError`
  - empty `nodes` and missing `pageInfo` are handled safely

- [x] Test `fetch_pr_meta_and_threads()`
  - returns `(meta_dict, threads_list)`
  - author extraction from nested metadata
  - empty `meta_capture` raises with PR context

- [x] Test wrapper helpers
  - `fetch_review_threads()`
  - `fetch_reviews()`
  - `fetch_issue_comments()`

- [x] Add realistic fixture-driven parsing tests
  - raw thread pages into `_paginate()` then `build_unresolved_threads()`
  - raw review payload into `build_outstanding_reviews()`
  - raw issue comments payload into `build_conversation_comments()`

- [ ] Add query contract checks at the semantic level
  - required connection names and requested fields are present
  - do not assert full-string equality or exact argument counts

### Phase 5: Resolver Unit and Integration Tests

Target: `resolve-pr-feedback/scripts/resolve_pr_feedback.py`

- [ ] Test `PullRequestRef.full_repo`

- [ ] Test parsing and normalization helpers
  - `parse_repo_name()`
  - `parse_pr_url()`
  - `load_item()`
  - `normalize_kind()`
  - `normalize_decision()`

- [x] Test `run()` and `run_json()`
  - dry-run behavior
  - successful JSON parsing
  - invalid JSON error path
  - subprocess failure path
  - empty output and whitespace-only output from `gh api graphql`
  - ANSI-contaminated or otherwise non-JSON output from `gh api graphql`
  - regression coverage for the logged `root_comment_id_for_thread()` failure path so resolver hardening can be verified after the production fix lands

- [x] Test `resolve_pr_ref()`
  - `--url`
  - `item["pull_request"]`
  - explicit `--repo` / `--pr`
  - `--repo` with `pr=None`
  - dry-run repo fallback
  - real repo fallback through `gh repo view`
  - bare invocation with no `--repo` or `--pr`, relying on current-branch PR resolution via `gh pr view`

- [ ] Test GitHub API helpers
  - `root_comment_id_for_thread()`
  - `create_review_reply()`
  - `resolve_review_thread()`
  - `add_reaction()`

- [ ] Test `execute_action()`
  - `review` kind skips cleanly
  - `review_thread` addressed with summary replies and resolves
  - `review_thread` addressed without summary resolves only
  - `review_thread` not relevant reacts with `-1`
  - auto-fetch of `comment_id` from thread
  - `issue_comment` and `review_comment` use `+1` or `-1`
  - missing `thread_id` or `comment_id` raises
  - dry-run path returns structured synthetic details

### Phase 6: Batch Resolver Tests

Target: `resolve-pr-feedback/scripts/apply_pr_feedback_actions.py`

- [ ] Test `load_actions()`
  - JSON array input
  - `{ "actions": [...] }` input
  - invalid payload shape
  - file and stdin paths

- [ ] Test `resolve_pr_ref_from_action()`
  - `url`
  - `pull_request`
  - `repo` + optional `pr`
  - missing all repo sources raises

- [ ] Test `format_plan()`
  - review-thread addressed with summary
  - review-thread addressed without summary
  - review-thread not relevant with explicit `comment_id`
  - review-thread not relevant with auto-fetch root
  - issue/review comment reaction verbs
  - review skip case
  - unknown kind fallback
  - `_context` rendering
  - empty-action list
  - breakdown counts

- [ ] Test `main()` batch behavior
  - `--plan` returns early and skips auth
  - auth runs once per batch otherwise
  - all-success result shape
  - partial failure still processes remaining actions
  - `--exit-code` raises `SystemExit(1)` when any action fails
  - `--verbose` writes progress to stderr
  - dry-run mode does not hit real subprocess execution

- [ ] Document the `comment_id: 0` fallback behavior explicitly
  - current code uses `action.get("comment_id") or action.get("root_comment_id")`
  - test should capture current behavior, not silently “fix” it

### Phase 7: Main-Orchestration and CLI Surface Tests

- [ ] Fetcher `main()`
  - JSON mode default
  - `--output`
  - `--minimal`
  - `--format text`
  - `--minimal --format text` warning to stderr
  - `--threads-only`
  - `--exclude-bots`
  - hidden compatibility `--include-all`
  - summary counts and provenance in JSON mode

- [ ] `build_actions.py` `main()`
  - stdin input
  - `--output`
  - `--validate` success and failure exit paths
  - `--drop-context`

- [ ] Resolver `main()`
  - CLI values overriding item payload
  - `--item-json`
  - `--item-file`
  - `--thread-id`
  - `--comment-id`
  - `--summary`
  - `--url`
  - bare invocation using current-branch PR resolution
  - `--dry-run`
  - `--verbose`

- [ ] Batch resolver `main()`
  - empty actions list
  - trailing newline in JSON output

### Phase 8: End-to-End Pipeline Tests

- [ ] Fetch -> build-actions -> format-plan
- [ ] Fetch -> build-actions -> batch-apply dry-run
- [ ] Minimal JSON -> build-actions
  - expect generated actions to be structurally correct
  - if using default `decision="addressed"` and empty summary, expect validation warnings
  - add a second test using a supplied summary to validate a clean round-trip
- [ ] File round-trip tests for `--output` and `--file`
- [ ] Stdout/stderr separation across all four Python scripts
- [ ] Exit-code behavior across success and failure paths

### Phase 9: Shell Script Tests

Target: `update-skills.sh`

- [ ] Establish an isolation harness for `update-skills.sh`
  - run the script under a temporary `HOME`
  - create only the target directories needed under that temp `HOME`
  - stage source directories in a temp repo copy so `rsync --delete` cannot touch the real workstation
  - skip or xfail cleanly if `rsync` is unavailable

- [ ] Test `--dry-run`
  - header printed
  - rsync dry-run invoked
  - no target mutations

- [ ] Test normal sync behavior in isolated temp directories
  - existing target dirs
  - sync success counters
  - `__pycache__` and `*.pyc` excluded
  - `--delete` behavior present

- [ ] Test missing target directory handling
  - warning printed
  - script continues
  - `skipped` increments correctly under `set -e`

- [ ] Test missing source directory handling
  - error printed
  - script continues
  - `failed` increments correctly under `set -e`

- [ ] Test final summary output

## Suggested Test File Layout

```text
tests/
├── conftest.py
├── test_fetch_helpers.py
├── test_fetch_graphql.py
├── test_fetch_rendering.py
├── test_build_actions.py
├── test_resolve_helpers.py
├── test_resolve_execute_action.py
├── test_apply_pr_feedback_actions.py
├── test_main_orchestration.py
├── test_pipeline.py
├── test_update_skills.py
└── fixtures/
```

## Acceptance Criteria

- [x] `pytest -q` passes locally
- [ ] Every executable has both helper-level and `main()`-level coverage
- [ ] Every `kind` / `decision` combination in resolver behavior is covered
- [ ] Fetcher JSON mode and text mode are both covered
- [ ] Dry-run paths are covered and verified not to call real GitHub endpoints
- [ ] Shell tests are isolated and non-destructive
- [ ] Tests describe current behavior accurately, including documented quirks
- [ ] Query contract tests fail on real schema-shape regressions, not harmless formatting edits

## Out of Scope

- Removing `_GRAPHQL_PR_CORE`
- Refactoring duplicated helper code into a shared module
- Fixing the `comment_id: 0` fallback bug
- Changing production behavior solely to make tests easier to write
