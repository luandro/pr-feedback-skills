#!/usr/bin/env python3
"""Apply a batch of PR feedback decisions in a single process.

Reads a JSON array (or object with an ``actions`` key) from a file or stdin
and resolves each action using the same logic as ``resolve_pr_feedback.py``
without spawning subprocesses.

Actions can also be piped via stdin:

    echo '[{"repo":"owner/repo","kind":"issue_comment","comment_id":123,"decision":"addressed"}]' \
      | python3 scripts/apply_pr_feedback_actions.py --dry-run
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import argparse
import json
from typing import Any

# Import the shared logic from the single-item resolver
from resolve_pr_feedback import (
    PullRequestRef,
    ensure_gh_auth,
    execute_action,
    normalize_decision,
    normalize_kind,
    parse_repo_name,
)


def load_actions(file_path: str | None) -> list[dict[str, Any]]:
    if file_path:
        with open(file_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
    else:
        payload = json.load(sys.stdin)

    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and isinstance(payload.get("actions"), list):
        return payload["actions"]
    raise ValueError("Expected a JSON array or an object with an 'actions' array")


def resolve_pr_ref_from_action(action: dict[str, Any], dry_run: bool) -> PullRequestRef:
    """Resolve a PullRequestRef from a batch action item."""
    repo_value = action.get("repo")
    pr_value = action.get("pr")
    url = action.get("url")

    if url:
        from resolve_pr_feedback import parse_pr_url
        return parse_pr_url(url)

    if action.get("pull_request"):
        pr_meta = action["pull_request"]
        return PullRequestRef(
            owner=pr_meta["owner"],
            repo=pr_meta["repo"],
            number=pr_meta.get("number"),
        )

    if repo_value:
        owner, repo = parse_repo_name(repo_value)
        return PullRequestRef(owner=owner, repo=repo, number=pr_value)

    raise ValueError(f"Action at index has no repo/url/pull_request: {json.dumps(action)[:200]}")


def format_plan(actions: list[dict[str, Any]]) -> str:
    """Render a human-readable plan of what the batch will do.

    No API calls. Useful as a lighter-weight alternative to ``--dry-run`` for
    getting user approval before running the batch.
    """
    lines: list[str] = []
    totals: dict[str, int] = {}
    for index, action in enumerate(actions, start=1):
        kind = action.get("kind") or action.get("type") or "?"
        decision = action.get("decision") or "?"
        context = action.get("_context") or ""
        context_part = f" [{context}]" if context else ""

        if kind == "review_thread":
            thread_id = action.get("thread_id") or "?"
            comment_id = action.get("comment_id")
            summary = action.get("summary") or ""
            if decision == "addressed":
                verb = "resolve"
                if summary:
                    verb = f"reply+resolve ({summary!r})"
                else:
                    verb = "resolve (no reply)"
            else:
                cid = f"comment_id={comment_id}" if comment_id is not None else "auto-fetch root"
                verb = f"-1 reaction on root ({cid}), thread left unresolved"
            target = f"thread {thread_id}"
        elif kind == "review_comment":
            verb = "+1" if decision == "addressed" else "-1"
            target = f"review_comment {action.get('comment_id')}"
        elif kind == "issue_comment":
            verb = "+1" if decision == "addressed" else "-1"
            target = f"issue_comment {action.get('comment_id')}"
        elif kind == "review":
            verb = "SKIP (top-level review body)"
            target = f"review {action.get('id') or action.get('comment_id') or '?'}"
        else:
            verb = f"? unknown kind={kind!r}"
            target = "?"

        key = f"{kind}/{decision}"
        totals[key] = totals.get(key, 0) + 1
        lines.append(f"  {index:>3}. {kind:<14} {decision:<13} {verb} @ {target}{context_part}")

    header = [f"Plan — {len(actions)} action(s):"]
    if totals:
        breakdown = ", ".join(f"{k}={v}" for k, v in sorted(totals.items()))
        header.append(f"  breakdown: {breakdown}")
    return "\n".join(header + [""] + lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply a batch of PR feedback decisions")
    parser.add_argument("--file", help="path to a JSON action list (reads stdin if omitted)")
    parser.add_argument("--dry-run", action="store_true", help="build the API calls but don't execute them (returns full dry-run JSON)")
    parser.add_argument("--plan", action="store_true", help="print a human-readable plan and exit, no API calls")
    parser.add_argument("--verbose", "-v", action="store_true", help="print per-action progress to stderr")
    parser.add_argument(
        "--exit-code",
        action="store_true",
        help="exit non-zero if any action failed (default: always exit 0 and report per-action status in JSON)",
    )
    args = parser.parse_args()

    actions = load_actions(args.file)

    if args.plan:
        sys.stdout.write(format_plan(actions) + "\n")
        return

    # Auth check once for the whole batch
    ensure_gh_auth(args.dry_run)

    results: list[dict[str, Any]] = []
    for index, action in enumerate(actions, start=1):
        try:
            kind = normalize_kind(
                argparse.Namespace(kind=action.get("kind")),
                action,
            )
            decision = normalize_decision(
                argparse.Namespace(decision=action.get("decision")),
                action,
            )
            pr_ref = resolve_pr_ref_from_action(action, args.dry_run)

            thread_id = action.get("thread_id")
            comment_id = action.get("comment_id")
            if comment_id is None:
                comment_id = action.get("root_comment_id")
            summary = action.get("summary") or ""

            if args.verbose:
                print(
                    f"[batch {index}/{len(actions)}] kind={kind} decision={decision}",
                    file=sys.stderr,
                )

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
            results.append({"index": index, "ok": True, "result": result})

        except Exception as exc:
            results.append({
                "index": index,
                "ok": False,
                "action": action,
                "error": str(exc),
            })
            if args.verbose:
                print(f"[batch {index}/{len(actions)}] FAILED: {exc}", file=sys.stderr)

    json.dump({"count": len(results), "results": results}, sys.stdout, indent=2)
    sys.stdout.write("\n")

    if args.exit_code and any(not r["ok"] for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
