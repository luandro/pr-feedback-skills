#!/usr/bin/env python3
"""Convert fetch-pr-unresolved-feedback JSON into a resolve-pr-feedback actions.json.

Reads the JSON emitted by ``fetch_unresolved_pr_feedback.py`` (from a file or
stdin) and produces the action list consumed by
``resolve-pr-feedback/scripts/apply_pr_feedback_actions.py``.

Every unresolved review thread and conversation comment becomes one action.
Outstanding reviews are skipped (top-level review bodies are report-only).

Defaults:
  - decision: ``addressed``
  - summary: empty (agent should fill in per-thread, or pass --summary)

The agent / user is expected to review the output, edit ``decision`` /
``summary`` per item, drop items that don't apply, then feed it to
``apply_pr_feedback_actions.py``.

Use ``--validate`` to surface warnings before handing the file to the
resolver (e.g. ``addressed`` threads with no reply summary, actions missing
required ids).
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any


def _pr_meta(feedback: dict[str, Any]) -> dict[str, Any]:
    pr = feedback.get("pull_request") or {}
    repo = f"{pr.get('owner')}/{pr.get('repo')}" if pr.get("owner") and pr.get("repo") else None
    return {"repo": repo, "pr": pr.get("number")}


def build_actions(
    feedback: dict[str, Any],
    default_decision: str = "addressed",
    default_summary: str = "",
) -> list[dict[str, Any]]:
    meta = _pr_meta(feedback)
    actions: list[dict[str, Any]] = []

    for thread in feedback.get("unresolved_review_threads") or []:
        action: dict[str, Any] = {
            "repo": meta["repo"],
            "pr": meta["pr"],
            "kind": "review_thread",
            "thread_id": thread.get("thread_id"),
            "decision": default_decision,
        }
        root = thread.get("root_comment_id")
        if root is not None:
            action["comment_id"] = root
        if default_decision == "addressed" and default_summary:
            action["summary"] = default_summary
        # Hint line: which file/line the thread targets, for human review
        path = thread.get("path")
        line = thread.get("line") or thread.get("original_line")
        if path:
            action["_context"] = f"{path}:{line}" if line else path
        actions.append(action)

    for comment in feedback.get("conversation_comments") or []:
        action = {
            "repo": meta["repo"],
            "kind": "issue_comment",
            "comment_id": comment.get("id"),
            "decision": default_decision,
        }
        author = comment.get("author")
        if author:
            action["_context"] = f"@{author}"
        actions.append(action)

    return actions


def validate_actions(actions: list[dict[str, Any]]) -> list[str]:
    """Return a list of warning strings for common problems.

    Empty list means the action plan looks safe to run.
    """
    warnings: list[str] = []
    for i, a in enumerate(actions, start=1):
        kind = a.get("kind")
        decision = a.get("decision")

        if not a.get("repo") and not a.get("url") and not a.get("pull_request"):
            warnings.append(f"#{i}: no repo/url/pull_request — resolver will reject")

        if kind == "review_thread":
            if not a.get("thread_id"):
                warnings.append(f"#{i}: review_thread has no thread_id")
            if decision == "addressed" and not (a.get("summary") or "").strip():
                warnings.append(
                    f"#{i}: review_thread addressed without summary — "
                    f"reviewer gets no SHA reference. Add a 'summary'."
                )
            if decision == "addressed" and not a.get("pr") and not a.get("url"):
                warnings.append(
                    f"#{i}: review_thread addressed without 'pr' — reply will fail "
                    f"(resolver hits /pulls/{{pr}}/comments/{{id}}/replies)"
                )
        elif kind in {"review_comment", "issue_comment"}:
            if a.get("comment_id") is None:
                warnings.append(f"#{i}: {kind} has no comment_id")
        elif kind is None:
            warnings.append(f"#{i}: missing 'kind' field")
        elif kind not in {"review", "review_thread", "review_comment", "issue_comment"}:
            warnings.append(f"#{i}: unknown kind {kind!r}")

        if decision not in {"addressed", "not_relevant"}:
            warnings.append(f"#{i}: decision must be 'addressed' or 'not_relevant' (got {decision!r})")

    return warnings


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert fetcher feedback.json into resolver actions.json"
    )
    parser.add_argument(
        "--file", "-f",
        help="path to fetcher JSON output (reads stdin if omitted)",
    )
    parser.add_argument(
        "--output", "-o",
        help="write actions JSON here (stdout if omitted)",
    )
    parser.add_argument(
        "--decision",
        choices=["addressed", "not_relevant"],
        default="addressed",
        help="default decision for every generated action (default: addressed)",
    )
    parser.add_argument(
        "--summary",
        default="",
        help="default reply summary for review_thread actions when decision is addressed",
    )
    parser.add_argument(
        "--drop-context",
        action="store_true",
        help="omit the `_context` hint field (path:line or @author) from the output",
    )
    parser.add_argument(
        "--validate",
        action="store_true",
        help="print warnings for risky actions (no summary, missing ids, etc.) to stderr; exit 1 if any",
    )
    args = parser.parse_args()

    if args.file:
        with open(args.file, "r", encoding="utf-8") as handle:
            feedback = json.load(handle)
    else:
        feedback = json.load(sys.stdin)

    actions = build_actions(
        feedback,
        default_decision=args.decision,
        default_summary=args.summary,
    )

    if args.drop_context:
        for action in actions:
            action.pop("_context", None)

    warnings: list[str] = []
    if args.validate:
        warnings = validate_actions(actions)
        for w in warnings:
            print(f"warning: {w}", file=sys.stderr)

    payload = json.dumps(actions, indent=2) + "\n"

    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(payload)
    else:
        sys.stdout.write(payload)

    if args.validate and warnings:
        sys.exit(1)


if __name__ == "__main__":
    main()
