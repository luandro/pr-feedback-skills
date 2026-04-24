#!/usr/bin/env python3
"""Resolve or react to a single piece of GitHub PR feedback.

Supports review threads (reply + resolve), review comments (reaction),
and issue comments (reaction).  Outputs a JSON result to stdout.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from typing import Any

# ---------------------------------------------------------------------------
# Constants & helpers
# ---------------------------------------------------------------------------

PR_URL_RE = re.compile(
    r"https://github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/(?P<number>\d+)"
)

GET_THREAD_QUERY = """\
query($threadId: ID!) {
  node(id: $threadId) {
    __typename
    ... on PullRequestReviewThread {
      id
      isResolved
      comments(first: 20) {
        nodes {
          id
          databaseId
          url
        }
      }
    }
  }
}
"""

RESOLVE_THREAD_MUTATION = """\
mutation($threadId: ID!) {
  resolveReviewThread(input: {threadId: $threadId}) {
    thread {
      id
      isResolved
    }
  }
}
"""

# ANSI escape sequence pattern (covers color codes, cursor movement, etc.)
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")


@dataclass
class PullRequestRef:
    owner: str
    repo: str
    number: int | None

    @property
    def full_repo(self) -> str:
        return f"{self.owner}/{self.repo}"


COMMAND_TIMEOUT_SECONDS = 60


def run(cmd: list[str], stdin: str | None = None, dry_run: bool = False, timeout: int = COMMAND_TIMEOUT_SECONDS) -> str:
    if dry_run:
        return json.dumps({"dry_run": True, "command": cmd})
    env = {**os.environ, "NO_COLOR": "1", "GH_CONFIG_PREFS_NO_COLOR": "true"}
    try:
        proc = subprocess.run(cmd, input=stdin, text=True, capture_output=True, env=env, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Command timed out after {timeout}s: {' '.join(cmd)}"
        ) from exc
    if proc.returncode != 0:
        stderr = _ANSI_RE.sub("", proc.stderr.strip())
        raise RuntimeError(
            f"Command failed ({proc.returncode}): {' '.join(cmd)}\n{stderr}"
        )
    return _ANSI_RE.sub("", proc.stdout)


def run_json(cmd: list[str], stdin: str | None = None, dry_run: bool = False) -> Any:
    out = run(cmd, stdin=stdin, dry_run=dry_run)
    if dry_run:
        return {"dry_run": True, "command": cmd, "stdin": stdin}
    if not out or not out.strip():
        raise RuntimeError(
            f"Empty output from {' '.join(cmd)}; expected JSON. "
            f"This usually means `gh` returned nothing or a wrapper altered the output."
        )
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        preview = out[:200].replace("\n", " ")
        raise RuntimeError(
            f"Failed to parse JSON from {' '.join(cmd)}\n"
            f"First 200 chars of output: {preview!r}"
        ) from exc


def ensure_gh_auth(dry_run: bool) -> None:
    run(["gh", "auth", "status"], dry_run=dry_run)


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


def load_item(args: argparse.Namespace) -> dict[str, Any]:
    if args.item_json:
        return json.loads(args.item_json)
    if args.item_file:
        with open(args.item_file, "r", encoding="utf-8") as handle:
            return json.load(handle)
    return {}


def resolve_pr_ref(args: argparse.Namespace, item: dict[str, Any], dry_run: bool) -> PullRequestRef:
    if args.url:
        return parse_pr_url(args.url)

    raw_pr_meta = item.get("pull_request")
    pr_meta = raw_pr_meta if isinstance(raw_pr_meta, dict) else {}

    repo_value = args.repo
    pr_meta_owner = pr_meta.get("owner")
    pr_meta_repo = pr_meta.get("repo")
    if (
        repo_value is None
        and isinstance(pr_meta_owner, str)
        and pr_meta_owner
        and isinstance(pr_meta_repo, str)
        and pr_meta_repo
    ):
        repo_value = f"{pr_meta_owner}/{pr_meta_repo}"
    if repo_value is None:
        candidate_repo = item.get("repo")
        if isinstance(candidate_repo, str):
            repo_value = candidate_repo

    pr_value = args.pr if args.pr is not None else pr_meta.get("number")
    if pr_value is None:
        pr_value = item.get("pr")

    if pr_value is not None and not isinstance(pr_value, int):
        try:
            pr_value = int(pr_value)
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                f"PR number must be an integer, got {pr_value!r}"
            ) from exc

    if repo_value:
        owner, repo = parse_repo_name(repo_value)
        return PullRequestRef(owner=owner, repo=repo, number=pr_value)

    if pr_value is None:
        pr_payload = run_json(
            ["gh", "pr", "view", "--json", "number,url"],
            dry_run=dry_run,
        )
        if dry_run:
            return PullRequestRef(owner="OWNER", repo="REPO", number=1)
        if not isinstance(pr_payload, dict):
            raise RuntimeError(
                "Could not determine the PR for the current branch. "
                "Pass --repo + --pr or --url explicitly."
            )
        pr_number = pr_payload.get("number")
        pr_url = pr_payload.get("url")
        if not isinstance(pr_number, int):
            try:
                pr_number = int(pr_number)
            except (TypeError, ValueError) as exc:
                raise RuntimeError(
                    "Could not determine the PR number for the current branch. "
                    "Pass --repo + --pr or --url explicitly."
                ) from exc
        if not isinstance(pr_url, str) or not pr_url:
            raise RuntimeError(
                "Could not determine the PR for the current branch. "
                "Pass --repo + --pr or --url explicitly."
            )

        pr_host_match = re.search(r"https://[^/]+/(?P<owner>[^/]+)/(?P<repo>[^/]+)/pull/\d+", pr_url)
        if not pr_host_match:
            raise RuntimeError(
                "Could not determine the PR repository for the current branch. "
                "Pass --repo + --pr or --url explicitly."
            )
        return PullRequestRef(
            owner=pr_host_match.group("owner"),
            repo=pr_host_match.group("repo"),
            number=pr_number,
        )

    repo_name = run(
        ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
        dry_run=dry_run,
    ).strip()
    if dry_run:
        return PullRequestRef(owner="OWNER", repo="REPO", number=pr_value)
    owner, repo = parse_repo_name(repo_name)
    return PullRequestRef(owner=owner, repo=repo, number=pr_value)


# ---------------------------------------------------------------------------
# GitHub API operations
# ---------------------------------------------------------------------------

def root_comment_id_for_thread(thread_id: str, dry_run: bool) -> int | None:
    try:
        payload = run_json(
            ["gh", "api", "graphql", "-F", "query=@-", "-F", f"threadId={thread_id}"],
            stdin=GET_THREAD_QUERY,
            dry_run=dry_run,
        )
    except RuntimeError as exc:
        raise RuntimeError(
            f"Failed to load review thread '{thread_id}' to determine its root comment id. "
            "Check that the thread id is current, belongs to the target repository, and that "
            "`gh api graphql` is returning JSON (not an auth or formatting error). "
            "If you already have a root comment id, pass `--comment-id` to skip this lookup."
        ) from exc
    if dry_run:
        return None

    data = payload.get("data") if isinstance(payload, dict) else None
    node = data.get("node") if isinstance(data, dict) else None
    if not isinstance(node, dict) or node.get("__typename") != "PullRequestReviewThread":
        raise RuntimeError(
            f"GitHub did not return a PullRequestReviewThread for '{thread_id}'. "
            "The thread may be stale, already resolved, or from another repository."
        )

    comments_connection = node.get("comments")
    if not isinstance(comments_connection, dict):
        raise RuntimeError(
            f"Malformed response for thread '{thread_id}': "
            "'comments' is not a dict."
        )

    nodes = comments_connection.get("nodes")
    if nodes is None:
        return None
    if not isinstance(nodes, list):
        raise RuntimeError(
            f"Malformed response for thread '{thread_id}': "
            "'nodes' is not a list."
        )

    for comment in nodes:
        if not isinstance(comment, dict):
            continue
        db_id = comment.get("databaseId")
        if db_id is not None:
            try:
                return int(db_id)
            except (TypeError, ValueError):
                continue
    return None


def create_review_reply(
    pr_ref: PullRequestRef,
    comment_id: int,
    summary: str,
    dry_run: bool,
) -> Any:
    if pr_ref.number is None:
        raise RuntimeError("PR number is required to reply to a review comment")
    return run_json(
        [
            "gh", "api",
            f"repos/{pr_ref.full_repo}/pulls/{pr_ref.number}/comments/{comment_id}/replies",
            "-X", "POST",
            "-f", f"body={summary}",
        ],
        dry_run=dry_run,
    )


def resolve_review_thread(thread_id: str, dry_run: bool) -> Any:
    return run_json(
        ["gh", "api", "graphql", "-F", "query=@-", "-F", f"threadId={thread_id}"],
        stdin=RESOLVE_THREAD_MUTATION,
        dry_run=dry_run,
    )


def add_reaction(
    repo: str,
    kind: str,
    comment_id: int,
    reaction: str,
    dry_run: bool,
) -> Any:
    if kind == "issue_comment":
        endpoint = f"repos/{repo}/issues/comments/{comment_id}/reactions"
    elif kind in {"review_comment", "review_thread"}:
        endpoint = f"repos/{repo}/pulls/comments/{comment_id}/reactions"
    else:
        raise RuntimeError(f"Unsupported reaction target kind '{kind}'")

    return run_json(
        ["gh", "api", endpoint, "-X", "POST", "-f", f"content={reaction}"],
        dry_run=dry_run,
    )


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def normalize_kind(args: argparse.Namespace, item: dict[str, Any]) -> str:
    kind = args.kind or item.get("kind") or item.get("type")
    if kind == "review_thread":
        return kind
    if kind in {"review_comment", "issue_comment", "review"}:
        return kind
    raise ValueError(
        "kind must be one of: review_thread, review_comment, issue_comment, review"
    )


def normalize_decision(args: argparse.Namespace, item: dict[str, Any]) -> str:
    decision = args.decision or item.get("decision")
    if decision not in {"addressed", "not_relevant"}:
        raise ValueError("decision must be 'addressed' or 'not_relevant'")
    return decision


# ---------------------------------------------------------------------------
# Core action logic (reusable by batch script without subprocess)
# ---------------------------------------------------------------------------

def execute_action(
    *,
    pr_ref: PullRequestRef,
    kind: str,
    decision: str,
    thread_id: str | None,
    comment_id: int | None,
    summary: str,
    dry_run: bool,
    verbose: bool = False,
) -> dict[str, Any]:
    """Execute a single feedback action and return the result dict."""
    result: dict[str, Any] = {
        "repo": pr_ref.full_repo,
        "pr": pr_ref.number,
        "kind": kind,
        "decision": decision,
        "thread_id": thread_id,
        "comment_id": comment_id,
        "summary": summary,
        "dry_run": dry_run,
        "action_taken": None,
        "details": {},
    }

    if kind == "review":
        result["action_taken"] = "skipped"
        result["details"] = {
            "reason": "top-level review bodies are not directly resolvable with this workflow"
        }
        return result

    if kind == "review_thread":
        if not thread_id:
            raise RuntimeError("review_thread requires thread_id")

        if comment_id is None and decision in {"addressed", "not_relevant"}:
            comment_id = root_comment_id_for_thread(thread_id, dry_run)
            result["comment_id"] = comment_id

        if decision == "addressed":
            reply_payload = None
            if summary and comment_id is not None:
                reply_payload = create_review_reply(
                    pr_ref=pr_ref,
                    comment_id=int(comment_id),
                    summary=summary,
                    dry_run=dry_run,
                )
            resolve_payload = resolve_review_thread(thread_id, dry_run)
            result["action_taken"] = (
                "replied_and_resolved" if reply_payload is not None else "resolved"
            )
            result["details"] = {
                "reply": reply_payload,
                "resolve_thread": resolve_payload,
            }
        else:
            if comment_id is None:
                raise RuntimeError(
                    "not_relevant review_thread requires a numeric root comment id or a resolvable thread id"
                )
            reaction_payload = add_reaction(
                repo=pr_ref.full_repo,
                kind="review_thread",
                comment_id=int(comment_id),
                reaction="-1",
                dry_run=dry_run,
            )
            result["action_taken"] = "reacted"
            result["details"] = {"reaction": reaction_payload, "reaction_content": "-1"}

        return result

    # review_comment or issue_comment
    if comment_id is None:
        raise RuntimeError(f"{kind} requires comment_id")

    reaction = "+1" if decision == "addressed" else "-1"
    reaction_payload = add_reaction(
        repo=pr_ref.full_repo,
        kind=kind,
        comment_id=int(comment_id),
        reaction=reaction,
        dry_run=dry_run,
    )
    result["action_taken"] = "reacted"
    result["details"] = {"reaction": reaction_payload, "reaction_content": reaction}
    return result


# ---------------------------------------------------------------------------
# Main (single-item CLI)
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve or react to GitHub PR feedback")
    parser.add_argument("--repo", help="owner/name")
    parser.add_argument("--pr", type=int, help="pull request number")
    parser.add_argument("--url", help="pull request URL or review URL")
    parser.add_argument(
        "--kind",
        help="review_thread, review_comment, issue_comment, or review",
    )
    parser.add_argument("--thread-id", help="GraphQL review thread id")
    parser.add_argument("--comment-id", type=int, help="numeric REST comment id")
    parser.add_argument(
        "--decision",
        help="addressed or not_relevant",
    )
    parser.add_argument("--summary", help="optional short reply summary for review threads")
    parser.add_argument("--item-json", help="single action as inline JSON")
    parser.add_argument("--item-file", help="path to a single action JSON object")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", "-v", action="store_true", help="print extra details to stderr")
    args = parser.parse_args()

    item = load_item(args)
    kind = normalize_kind(args, item)
    decision = normalize_decision(args, item)
    pr_ref = resolve_pr_ref(args, item, args.dry_run)

    ensure_gh_auth(args.dry_run)

    thread_id = args.thread_id or item.get("thread_id")
    comment_id = args.comment_id
    if comment_id is None:
        comment_id = item.get("comment_id")
    if comment_id is None:
        comment_id = item.get("root_comment_id")
    summary = args.summary or item.get("summary") or ""

    if args.verbose:
        print(f"[resolve] kind={kind} decision={decision} thread={thread_id} comment={comment_id}", file=sys.stderr)

    result = execute_action(
        pr_ref=pr_ref,
        kind=kind,
        decision=decision,
        thread_id=thread_id,
        comment_id=comment_id,
        summary=summary,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )

    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
