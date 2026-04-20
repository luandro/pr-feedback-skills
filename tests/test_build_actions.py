from __future__ import annotations

import build_actions


def test_pr_meta_missing_pull_request_returns_nulls() -> None:
    assert build_actions._pr_meta({}) == {"repo": None, "pr": None}
    assert build_actions._pr_meta({"pull_request": None}) == {"repo": None, "pr": None}


def test_build_actions_missing_pull_request_keeps_null_repo_and_validate_flags_it() -> None:
    feedback = {
        "unresolved_review_threads": [
            {
                "thread_id": "T1",
                "root_comment_id": 42,
                "path": "src/main.py",
                "line": 17,
            }
        ],
        "conversation_comments": [],
    }

    actions = build_actions.build_actions(
        feedback,
        default_decision="addressed",
        default_summary="Fixed in abc1234",
    )

    assert actions == [
        {
            "repo": None,
            "pr": None,
            "kind": "review_thread",
            "thread_id": "T1",
            "decision": "addressed",
            "comment_id": 42,
            "summary": "Fixed in abc1234",
            "_context": "src/main.py:17",
        }
    ]

    warnings = build_actions.validate_actions(actions)

    assert "#1: no repo/url/pull_request — resolver will reject" in warnings
    assert "#1: review_thread addressed without 'pr' — reply will fail (resolver hits /pulls/{pr}/comments/{id}/replies)" in warnings
