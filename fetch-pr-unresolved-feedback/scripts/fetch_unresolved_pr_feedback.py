#!/usr/bin/env python3
"""Fetch unresolved GitHub PR review threads, outstanding review bodies, and
conversation comments in a single deterministic run.

Outputs a JSON object to stdout with structured feedback data suitable for
agent consumption and piping into resolve-pr-feedback.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

# ---------------------------------------------------------------------------
# GraphQL queries
# ---------------------------------------------------------------------------

_GRAPHQL_PR_CORE = """\
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      number
      url
      title
      state
      author { __typename login }
    }
  }
"""

GRAPHQL_QUERY_THREADS = """\
query(
  $owner: String!, $repo: String!, $number: Int!, $threadsCursor: String
) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      number url title state
      author { __typename login }
      reviewThreads(first: 100, after: $threadsCursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id isResolved isOutdated
          path line originalLine startLine originalStartLine
          diffSide startDiffSide
          comments(first: 100) {
            nodes {
              id databaseId url body createdAt updatedAt
              author { __typename login }
            }
          }
        }
      }
    }
  }
}
"""

GRAPHQL_QUERY_REVIEWS = """\
query(
  $owner: String!, $repo: String!, $number: Int!, $reviewsCursor: String
) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      reviews(first: 100, after: $reviewsCursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id url state body submittedAt
          author { __typename login }
        }
      }
    }
  }
}
"""

GRAPHQL_QUERY_COMMENTS = """\
query(
  $owner: String!, $repo: String!, $number: Int!, $commentsCursor: String
) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      comments(first: 100, after: $commentsCursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id url body createdAt updatedAt
          author { __typename login }
        }
      }
    }
  }
}
"""

# ---------------------------------------------------------------------------
# Constants & helpers
# ---------------------------------------------------------------------------

PR_URL_RE = re.compile(
    r"https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)"
)

BOT_LOGIN_SUFFIXES = ("[bot]", "-bot")


@dataclass
class PullRequestRef:
    owner: str
    repo: str
    number: int


# ANSI escape sequence pattern (covers color codes, cursor movement, etc.)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


def run(cmd: list[str], stdin: str | None = None) -> str:
    # Disable color output from gh and other CLI tools so JSON parsing works.
    env = {**os.environ, "NO_COLOR": "1", "GH_CONFIG_PREFS_NO_COLOR": "true"}
    proc = subprocess.run(cmd, input=stdin, text=True, capture_output=True, env=env)
    if proc.returncode != 0:
        raise RuntimeError(
            f"Command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr.strip()}"
        )
    # Strip any ANSI escape codes that leaked through (belt-and-suspenders)
    return _ANSI_RE.sub("", proc.stdout)


def run_json(cmd: list[str], stdin: str | None = None) -> Any:
    out = run(cmd, stdin=stdin)
    if not out or not out.strip():
        raise RuntimeError(
            f"Empty output from {' '.join(cmd)}; expected JSON. "
            f"This often means a gh extension is interfering or the command returned nothing. "
            f"Try `gh --version` and `gh extension list` to check the environment."
        )
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        preview = out[:200].replace("\n", " ")
        raise RuntimeError(
            f"Failed to parse JSON from {' '.join(cmd)}\n"
            f"First 200 chars of output: {preview!r}"
        ) from exc


def ensure_gh_auth() -> None:
    run(["gh", "auth", "status"])


def is_bot_login(login: str | None) -> bool:
    return login is not None and any(login.endswith(s) for s in BOT_LOGIN_SUFFIXES)


def is_bot_author(author: dict[str, Any] | None) -> bool:
    return author is not None and author.get("__typename") == "Bot"


def parse_repo_name(repo: str) -> tuple[str, str]:
    parts = repo.split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError(f"Invalid repo '{repo}', expected owner/name")
    return parts[0], parts[1]


def parse_pr_url(url: str) -> PullRequestRef:
    match = PR_URL_RE.search(url)
    if not match:
        raise ValueError(f"Could not parse PR URL from '{url}'")
    return PullRequestRef(
        owner=match.group("owner"),
        repo=match.group("repo"),
        number=int(match.group("number")),
    )


def resolve_pr_ref(args: argparse.Namespace) -> PullRequestRef:
    if args.url:
        return parse_pr_url(args.url)

    if args.pr is not None:
        if args.repo:
            owner, repo = parse_repo_name(args.repo)
            return PullRequestRef(owner=owner, repo=repo, number=args.pr)

        repo_name = run(
            ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"]
        ).strip()
        if not repo_name:
            raise RuntimeError(
                "Could not determine current repo from `gh repo view`. "
                "Pass --repo owner/name or --url explicitly."
            )
        owner, repo = parse_repo_name(repo_name)
        return PullRequestRef(owner=owner, repo=repo, number=args.pr)

    pr_payload = run_json(["gh", "pr", "view", "--json", "number,url"])
    if not pr_payload or "url" not in pr_payload:
        raise RuntimeError(
            "Could not determine the PR for the current branch. "
            "Pass --repo + --pr or --url explicitly."
        )
    return parse_pr_url(pr_payload["url"])


# ---------------------------------------------------------------------------
# GraphQL fetching (separate queries for independent pagination)
# ---------------------------------------------------------------------------

def _run_graphql(query: str, pr_ref: PullRequestRef, cursor_name: str | None = None, cursor_val: str | None = None) -> dict[str, Any]:
    cmd = [
        "gh", "api", "graphql", "-F", "query=@-",
        "-F", f"owner={pr_ref.owner}",
        "-F", f"repo={pr_ref.repo}",
        "-F", f"number={pr_ref.number}",
    ]
    if cursor_name and cursor_val:
        cmd += ["-F", f"{cursor_name}={cursor_val}"]
    payload = run_json(cmd, stdin=query)
    if payload.get("errors"):
        raise RuntimeError(json.dumps(payload["errors"], indent=2))
    return payload


def _paginate(
    query: str,
    pr_ref: PullRequestRef,
    cursor_name: str,
    path_parts: list[str],
    meta_capture: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Paginate through a GraphQL connection field.

    *path_parts* describes the navigation from ``data`` down to the connection,
    e.g. ``["repository", "pullRequest", "reviewThreads"]``.

    If *meta_capture* is provided (a dict), the first response's
    ``data.repository.pullRequest`` node is copied into it. Useful when the
    threads query also carries PR metadata (number/url/title/state/author).
    """
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    first_pass = True
    while True:
        payload = _run_graphql(query, pr_ref, cursor_name if cursor else None, cursor)

        if first_pass and meta_capture is not None:
            pr_node = (
                payload.get("data", {})
                .get("repository", {})
                .get("pullRequest")
            )
            if pr_node:
                for key in ("number", "url", "title", "state", "author"):
                    if key in pr_node:
                        meta_capture[key] = pr_node[key]
            first_pass = False

        node = payload.get("data")
        for part in path_parts:
            if node is None:
                raise RuntimeError(
                    f"GraphQL response missing field at path {'.'.join(path_parts)}; "
                    f"check that the PR exists and you have access to "
                    f"{pr_ref.owner}/{pr_ref.repo}#{pr_ref.number}."
                )
            node = node.get(part) if isinstance(node, dict) else None

        if node is None or not isinstance(node, dict):
            raise RuntimeError(
                f"GraphQL response missing field at path {'.'.join(path_parts)}; "
                f"check that the PR exists and you have access to "
                f"{pr_ref.owner}/{pr_ref.repo}#{pr_ref.number}."
            )

        page_info = node.get("pageInfo")
        if not isinstance(page_info, dict):
            raise RuntimeError(
                f"GraphQL response missing field at path {'.'.join(path_parts)}.pageInfo; "
                f"check that the PR exists and you have access to "
                f"{pr_ref.owner}/{pr_ref.repo}#{pr_ref.number}."
            )
        items.extend(node.get("nodes") or [])

        if page_info.get("hasNextPage"):
            cursor = page_info["endCursor"]
        else:
            break
    return items


def fetch_pr_meta_and_threads(
    pr_ref: PullRequestRef,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Fetch PR metadata and all review threads in a single paginated pass.

    The threads GraphQL query already selects PR meta (number, url, title,
    state, author), so we piggy-back on it instead of making a separate
    ``gh pr view`` call. That call is fragile: some ``gh`` extensions return
    empty output and break JSON parsing.
    """
    meta_capture: dict[str, Any] = {}
    threads = _paginate(
        GRAPHQL_QUERY_THREADS, pr_ref, "threadsCursor",
        ["repository", "pullRequest", "reviewThreads"],
        meta_capture=meta_capture,
    )
    if not meta_capture:
        raise RuntimeError(
            f"GraphQL returned no pullRequest node for {pr_ref.owner}/{pr_ref.repo}#{pr_ref.number}. "
            f"Check the PR exists and you have access."
        )
    author = (meta_capture.get("author") or {}).get("login")
    return {
        "number": meta_capture.get("number"),
        "url": meta_capture.get("url"),
        "title": meta_capture.get("title"),
        "state": meta_capture.get("state"),
        "author": author,
    }, threads


def fetch_review_threads(pr_ref: PullRequestRef) -> list[dict[str, Any]]:
    """Backward-compatible wrapper: threads without meta capture."""
    return _paginate(
        GRAPHQL_QUERY_THREADS, pr_ref, "threadsCursor",
        ["repository", "pullRequest", "reviewThreads"],
    )


def fetch_reviews(pr_ref: PullRequestRef) -> list[dict[str, Any]]:
    return _paginate(
        GRAPHQL_QUERY_REVIEWS, pr_ref, "reviewsCursor",
        ["repository", "pullRequest", "reviews"],
    )


def fetch_issue_comments(pr_ref: PullRequestRef) -> list[dict[str, Any]]:
    payload = run_json([
        "gh", "api", "--paginate",
        f"repos/{pr_ref.owner}/{pr_ref.repo}/issues/{pr_ref.number}/comments",
    ])
    if isinstance(payload, list):
        return payload
    raise RuntimeError("Unexpected issue comments payload shape")


# ---------------------------------------------------------------------------
# Filtering & building
# ---------------------------------------------------------------------------

def keep_actor(
    login: str | None,
    pr_author: str | None,
    include_all: bool,
    author: dict[str, Any] | None = None,
) -> bool:
    if include_all:
        return True
    if not login:
        return False
    if pr_author and login == pr_author:
        return False
    if is_bot_login(login) or is_bot_author(author):
        return False
    return True


def _is_suppressible_excluded_comment(
    login: str | None,
    pr_author: str | None,
    author: dict[str, Any] | None = None,
) -> bool:
    """Return True only for comments that are safe to ignore (PR author or bot).

    Unlike ``keep_actor``, which excludes unknown-author comments from
    attention, comments with login=None are treated as NOT suppressible here —
    so a thread whose only comments have missing author metadata is kept rather
    than silently dropped.
    """
    if pr_author and login == pr_author:
        return True
    if login and is_bot_login(login):
        return True
    if is_bot_author(author):
        return True
    return False


def build_unresolved_threads(
    threads: list[dict[str, Any]],
    pr_author: str | None,
    include_all: bool,
    filter_path: str | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for thread in threads:
        if thread.get("isResolved"):
            continue
        thread_path = thread.get("path")
        if filter_path and thread_path != filter_path:
            continue

        raw_comments = (thread.get("comments") or {}).get("nodes") or []
        comments: list[dict[str, Any]] = []
        for comment in raw_comments:
            author_obj = comment.get("author")
            login = (author_obj or {}).get("login")
            actor_kept = keep_actor(login, pr_author, include_all, author_obj)
            comments.append({
                "id": comment["id"],
                "database_id": comment.get("databaseId"),
                "url": comment.get("url"),
                "author": login,
                "body": comment.get("body") or "",
                "created_at": comment.get("createdAt"),
                "updated_at": comment.get("updatedAt"),
                "excluded_from_attention": not actor_kept,
            })

        if not include_all and not comments:
            continue

        if not include_all and comments and all(
            _is_suppressible_excluded_comment(
                comment.get("author"),
                pr_author,
                raw_comment.get("author"),
            )
            for comment, raw_comment in zip(comments, raw_comments, strict=True)
        ):
            continue

        results.append({
            "thread_id": thread["id"],
            "root_comment_id": next(
                (c.get("database_id") for c in comments if c.get("database_id") is not None),
                None,
            ),
            "path": thread_path,
            "line": thread.get("line"),
            "original_line": thread.get("originalLine"),
            "start_line": thread.get("startLine"),
            "original_start_line": thread.get("originalStartLine"),
            "diff_side": thread.get("diffSide"),
            "start_diff_side": thread.get("startDiffSide"),
            "is_outdated": bool(thread.get("isOutdated")),
            "comments": comments,
        })
    return results


def build_outstanding_reviews(
    reviews: list[dict[str, Any]],
    pr_author: str | None,
    include_all: bool,
) -> list[dict[str, Any]]:
    # Single pass: track latest state per user, then collect qualifying reviews
    sorted_reviews = sorted(reviews, key=lambda r: r.get("submittedAt") or "")
    latest_state_by_user: dict[str, str] = {}
    for review in sorted_reviews:
        login = (review.get("author") or {}).get("login")
        if login:
            latest_state_by_user[login] = review.get("state") or ""

    results: list[dict[str, Any]] = []
    for review in sorted_reviews:
        author_obj = review.get("author")
        login = (author_obj or {}).get("login")
        if not keep_actor(login, pr_author, include_all, author_obj):
            continue
        latest_state = latest_state_by_user.get(login or "", "")
        if latest_state in {"APPROVED", "DISMISSED"}:
            continue
        state = review.get("state")
        body = (review.get("body") or "").strip()
        if state not in {"CHANGES_REQUESTED", "COMMENTED"} or not body:
            continue
        results.append({
            "id": review["id"],
            "url": review.get("url"),
            "author": login,
            "state": state,
            "submitted_at": review.get("submittedAt"),
            "body": body,
            "excluded_from_attention": not keep_actor(login, pr_author, False, author_obj),
        })
    return results


def build_conversation_comments(
    comments: list[dict[str, Any]],
    pr_author: str | None,
    include_all: bool,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for comment in comments:
        login = (comment.get("user") or {}).get("login")
        if not include_all and ((comment.get("user") or {}).get("type") == "Bot" or is_bot_login(login)):
            continue
        if not keep_actor(login, pr_author, include_all):
            continue
        body = (comment.get("body") or "").strip()
        if not body:
            continue
        results.append({
            "id": comment["id"],
            "url": comment.get("html_url") or comment.get("url"),
            "author": login,
            "created_at": comment.get("created_at"),
            "updated_at": comment.get("updated_at"),
            "body": body,
        })
    return results


# ---------------------------------------------------------------------------
# Minimal output transformation (token-saving)
# ---------------------------------------------------------------------------

def _apply_minimal(result: dict[str, Any]) -> dict[str, Any]:
    """Strip verbose fields to reduce output size.

    Keeps everything the resolver + human triage need:
      - PR meta, summary counts
      - thread_id, root_comment_id, path, line for threads
      - author + first-line-ish body excerpt (up to 200 chars) per thread
      - id + author + body excerpt per conversation comment
      - id + author + state + body excerpt per outstanding review
    Drops:
      - per-comment ids/urls/timestamps within threads
      - is_outdated/diff_side/start_* thread fields
      - provenance
    """
    def excerpt(body: str | None, n: int = 200) -> str:
        if not body:
            return ""
        body = body.strip()
        return body if len(body) <= n else body[: n - 1] + "\u2026"

    minimal = {
        "pull_request": result.get("pull_request", {}),
        "summary": result.get("summary", {}),
        "unresolved_review_threads": [
            {
                "thread_id": t["thread_id"],
                "root_comment_id": t.get("root_comment_id"),
                "path": t.get("path"),
                "line": t.get("line") or t.get("original_line"),
                "author": next(
                    (c.get("author") for c in t.get("comments") or [] if c.get("author")),
                    None,
                ),
                "body": excerpt(
                    next(
                        (c.get("body") for c in t.get("comments") or [] if c.get("body")),
                        "",
                    )
                ),
            }
            for t in result.get("unresolved_review_threads") or []
        ],
        "outstanding_reviews": [
            {
                "id": r["id"],
                "author": r.get("author"),
                "state": r.get("state"),
                "body": excerpt(r.get("body"), 400),
            }
            for r in result.get("outstanding_reviews") or []
        ],
        "conversation_comments": [
            {
                "id": c["id"],
                "author": c.get("author"),
                "body": excerpt(c.get("body")),
            }
            for c in result.get("conversation_comments") or []
        ],
    }
    return minimal


# ---------------------------------------------------------------------------
# Text rendering (agent-friendly, no jq needed)
# ---------------------------------------------------------------------------

def _at(login: str | None) -> str:
    return f"@{login}" if login else "@(unknown)"


def _format_text(result: dict[str, Any]) -> str:
    """Render feedback as plain text — full bodies, no JSON parsing required."""
    lines: list[str] = []
    pr = result.get("pull_request", {})
    s = result.get("summary", {})

    lines.append(f"PR #{pr.get('number')} — {pr.get('title') or '(no title)'}")
    lines.append(
        f"Repo: {pr.get('owner')}/{pr.get('repo')}  "
        f"State: {pr.get('state')}  "
        f"Author: {_at(pr.get('author'))}"
    )
    lines.append(f"URL: {pr.get('url')}")
    lines.append("")
    lines.append(
        f"Summary: {s.get('unresolved_review_thread_count', 0)} unresolved thread(s)  "
        f"{s.get('outstanding_review_count', 0)} outstanding review(s)  "
        f"{s.get('conversation_comment_count', 0)} conversation comment(s)"
    )

    threads = result.get("unresolved_review_threads") or []
    if threads:
        lines.append("")
        lines.append("=" * 60)
        lines.append("UNRESOLVED REVIEW THREADS")
        lines.append("=" * 60)
        for i, t in enumerate(threads, 1):
            loc = t.get("path") or "(unknown path)"
            line_no = t.get("line") or t.get("original_line")
            if line_no:
                loc = f"{loc}:{line_no}"
            outdated = "  [OUTDATED]" if t.get("is_outdated") else ""
            lines.append(f"\n[{i}] {loc}{outdated}")
            lines.append(f"    thread_id: {t['thread_id']}")
            if t.get("root_comment_id"):
                lines.append(f"    comment_id: {t['root_comment_id']}")
            for c in t.get("comments") or []:
                bot_flag = "  [bot]" if c.get("excluded_from_attention") else ""
                lines.append(f"    {_at(c.get('author'))}{bot_flag}:")
                body = (c.get("body") or "").strip()
                for body_line in body.splitlines():
                    lines.append(f"      {body_line}")

    reviews = result.get("outstanding_reviews") or []
    if reviews:
        lines.append("")
        lines.append("=" * 60)
        lines.append("OUTSTANDING REVIEWS")
        lines.append("=" * 60)
        for i, r in enumerate(reviews, 1):
            bot_flag = "  [bot]" if r.get("excluded_from_attention") else ""
            lines.append(f"\n[{i}] {_at(r.get('author'))}{bot_flag}  state: {r.get('state')}")
            lines.append(f"    id: {r['id']}")
            body = (r.get("body") or "").strip()
            for body_line in body.splitlines():
                lines.append(f"    {body_line}")

    comments = result.get("conversation_comments") or []
    if comments:
        lines.append("")
        lines.append("=" * 60)
        lines.append("CONVERSATION COMMENTS")
        lines.append("=" * 60)
        for i, c in enumerate(comments, 1):
            lines.append(f"\n[{i}] {_at(c.get('author'))}  id: {c['id']}")
            body = (c.get("body") or "").strip()
            for body_line in body.splitlines():
                lines.append(f"    {body_line}")

    if not threads and not reviews and not comments:
        lines.append("")
        lines.append("No unresolved feedback found.")

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Fetch unresolved GitHub PR review threads and related feedback"
    )
    parser.add_argument("--repo", help="owner/name")
    parser.add_argument("--pr", type=int, help="pull request number")
    parser.add_argument(
        "--url",
        help="GitHub pull request URL or review URL (the pull request is extracted from it)",
    )
    parser.add_argument(
        "--exclude-bots",
        action="store_true",
        help="filter out bot and PR-author comments (default is to include all — bot review threads are common and actionable)",
    )
    parser.add_argument(
        "--include-all",
        action="store_true",
        default=False,
        help=argparse.SUPPRESS,  # kept for backwards compat; include-all is now the default
    )
    parser.add_argument(
        "--filter-path",
        help="only return review threads for this file path",
    )
    parser.add_argument(
        "--threads-only",
        action="store_true",
        help="return only unresolved review threads; skip outstanding reviews and conversation comments",
    )
    parser.add_argument(
        "--output", "-o",
        help="write JSON output to this file instead of stdout",
    )
    parser.add_argument(
        "--minimal",
        action="store_true",
        help="drop comment bodies and verbose per-comment fields to save tokens; retains ids/paths/authors",
    )
    parser.add_argument(
        "--format", "-f",
        choices=["json", "text"],
        default="json",
        help="output format: json (default) or text (human/agent-readable, full bodies, no jq needed)",
    )
    args = parser.parse_args()

    ensure_gh_auth()
    pr_ref = resolve_pr_ref(args)

    # Fetch PR meta and review threads in one paginated pass (GraphQL threads
    # query already carries PR metadata). This avoids a separate `gh pr view`
    # call that can return empty output when a gh extension misbehaves.
    pr_meta, review_threads = fetch_pr_meta_and_threads(pr_ref)
    reviews = fetch_reviews(pr_ref) if not args.threads_only else []
    issue_comments = fetch_issue_comments(pr_ref) if not args.threads_only else []

    pr_author = pr_meta.get("author")
    include_all = not args.exclude_bots
    unresolved_threads = build_unresolved_threads(
        review_threads, pr_author, include_all, args.filter_path
    )
    outstanding_reviews = build_outstanding_reviews(
        reviews, pr_author, include_all
    ) if not args.threads_only else []
    conversation_comments = build_conversation_comments(
        issue_comments, pr_author, include_all
    ) if not args.threads_only else []

    result = {
        "pull_request": {
            "owner": pr_ref.owner,
            "repo": pr_ref.repo,
            "number": pr_ref.number,
            "url": pr_meta["url"],
            "title": pr_meta["title"],
            "state": pr_meta["state"],
            "author": pr_author,
        },
        "summary": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "unresolved_review_thread_count": len(unresolved_threads),
            "unresolved_review_comment_count": sum(
                len(t["comments"]) for t in unresolved_threads
            ),
            "outstanding_review_count": len(outstanding_reviews),
            "conversation_comment_count": len(conversation_comments),
            "total_attention_items": (
                len(unresolved_threads) + len(outstanding_reviews) + len(conversation_comments)
            ),
        },
        "unresolved_review_threads": unresolved_threads,
        "outstanding_reviews": outstanding_reviews,
        "conversation_comments": conversation_comments,
        "provenance": {
            "graph_source": "gh api graphql reviewThreads/reviews",
            "rest_source": "gh api issues/{n}/comments",
        },
    }

    if args.format == "text":
        if args.minimal:
            print("Warning: --minimal is ignored when --format text is used", file=sys.stderr)
        output_str = _format_text(result)
    else:
        if args.minimal:
            result = _apply_minimal(result)
        output_str = json.dumps(result, indent=2) + "\n"

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output_str)
    else:
        sys.stdout.write(output_str)


if __name__ == "__main__":
    main()
