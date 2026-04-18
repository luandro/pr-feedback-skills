#!/usr/bin/env python3
"""Resolve or react to a single piece of GitHub PR feedback.

Supports review threads (reply + resolve), review comments (reaction),
and issue comments (reaction).  Outputs a JSON result to stdout.
"""
from __future__ import annotations

import argparse
import json
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


@dataclass
class PullRequestRef:
    owner: str
    repo: str
    number: int | None

    @property
    def full_repo(self) -> str:
        return f"{self.owner}/{self.repo}"


def run(cmd: list[str], stdin: str | None = None, dry_run: bool = False) -> str:
    if dry_run:
        return json.dumps({"dry_run": True, "command": cmd})
    proc = subprocess.run(cmd, input=stdin, text=True, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(
            f"Command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr.strip()}"
        )
    return proc.stdout


def run_json(cmd: list[str], stdin: str | None = None, dry_run: bool = False) -> Any:
    out = run(cmd, stdin=stdin, dry_run=dry_run)
    if dry_run:
        return {"dry_run": True, "command": cmd, "stdin": stdin}
    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Failed to parse JSON from {' '.join(cmd)}") from exc


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

    if item.get("pull_request"):
        pr_meta = item["pull_request"]
        return PullRequestRef(
            owner=pr_meta["owner"],
            repo=pr_meta["repo"],
            number=pr_meta.get("number"),
        )

    repo_value = args.repo or item.get("repo")
    pr_value = args.pr if args.pr is not None else item.get("pr")

    if repo_value:
        owner, repo = parse_repo_name(repo_value)
        return PullRequestRef(owner=owner, repo=repo, number=pr_value)

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
    payload = run_json(
        ["gh", "api", "graphql", "-F", "query=@-", "-F", f"threadId={thread_id}"],
        stdin=GET_THREAD_QUERY,
        dry_run=dry_run,
    )
    if dry_run:
        return None

    node = payload.get("data", {}).get("node")
    if not node or node.get("__typename") != "PullRequestReviewThread":
        raise RuntimeError(f"Could not load review thread '{thread_id}'")

    comments = (node.get("comments") or {}).get("nodes") or []
    for comment in comments:
        if comment.get("databaseId") is not None:
            return int(comment["databaseId"])
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
            result["action_taken"] = "replied_and_resolved" if summary else "resolved"
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
    comment_id = args.comment_id or item.get("comment_id") or item.get("root_comment_id")
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
