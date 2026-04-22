from __future__ import annotations

import json
from argparse import Namespace

import pytest

import resolve_pr_feedback as resolver


def test_pull_request_ref_full_repo_formats_owner_and_repo() -> None:
    assert resolver.PullRequestRef("octo", "repo", 17).full_repo == "octo/repo"


@pytest.mark.parametrize(
    ("repo", "expected"),
    [
        ("octo/repo", ("octo", "repo")),
        ("acme/widgets", ("acme", "widgets")),
    ],
)
def test_parse_repo_name_accepts_owner_name_pairs(
    repo: str,
    expected: tuple[str, str],
) -> None:
    assert resolver.parse_repo_name(repo) == expected


@pytest.mark.parametrize("repo", ["", "octo", "/repo", "octo/"])
def test_parse_repo_name_rejects_invalid_shapes(repo: str) -> None:
    with pytest.raises(ValueError, match="Invalid repo"):
        resolver.parse_repo_name(repo)


def test_parse_pr_url_extracts_pull_request_reference() -> None:
    assert resolver.parse_pr_url(
        "https://github.com/octo/repo/pull/17/files#r123"
    ) == resolver.PullRequestRef("octo", "repo", 17)


def test_parse_pr_url_rejects_non_github_host() -> None:
    with pytest.raises(ValueError, match="Could not parse PR URL"):
        resolver.parse_pr_url("https://example.com/octo/repo/pull/44/files#r123")


def test_load_item_prefers_inline_json_and_reads_from_file(tmp_path) -> None:
    item_path = tmp_path / "item.json"
    item_path.write_text(json.dumps({"kind": "issue_comment", "comment_id": 7}), encoding="utf-8")

    inline = resolver.load_item(
        Namespace(
            item_json='{"kind":"review_thread","thread_id":"T1"}',
            item_file=str(item_path),
        )
    )
    from_file = resolver.load_item(Namespace(item_json=None, item_file=str(item_path)))

    assert inline == {"kind": "review_thread", "thread_id": "T1"}
    assert from_file == {"kind": "issue_comment", "comment_id": 7}


def test_resolve_pr_ref_cli_repo_and_pr_override_item_pull_request_metadata() -> None:
    pr_ref = resolver.resolve_pr_ref(
        Namespace(
            url=None,
            repo="acme/widgets",
            pr=23,
        ),
        {
            "pull_request": {"owner": "old", "repo": "repo", "number": 1},
            "repo": "stale/fallback",
            "pr": 2,
        },
        dry_run=False,
    )

    assert pr_ref == resolver.PullRequestRef("acme", "widgets", 23)


def test_resolve_pr_ref_url_still_has_highest_precedence_over_cli_and_item() -> None:
    pr_ref = resolver.resolve_pr_ref(
        Namespace(
            url="https://github.com/final/app/pull/99",
            repo="acme/widgets",
            pr=23,
        ),
        {
            "pull_request": {"owner": "old", "repo": "repo", "number": 1},
            "repo": "stale/fallback",
            "pr": 2,
        },
        dry_run=False,
    )

    assert pr_ref == resolver.PullRequestRef("final", "app", 99)


@pytest.mark.parametrize(
    ("args_kind", "item", "expected"),
    [
        ("review_thread", {}, "review_thread"),
        (None, {"kind": "review_comment"}, "review_comment"),
        (None, {"type": "issue_comment"}, "issue_comment"),
        (None, {"kind": "review"}, "review"),
    ],
)
def test_normalize_kind_accepts_supported_values(
    args_kind: str | None,
    item: dict[str, object],
    expected: str,
) -> None:
    assert resolver.normalize_kind(Namespace(kind=args_kind), item) == expected


def test_normalize_kind_rejects_unknown_values() -> None:
    with pytest.raises(ValueError, match="kind must be one of"):
        resolver.normalize_kind(Namespace(kind="note"), {})


@pytest.mark.parametrize("decision", ["addressed", "not_relevant"])
def test_normalize_decision_accepts_supported_values(decision: str) -> None:
    assert resolver.normalize_decision(Namespace(decision=decision), {}) == decision


def test_normalize_decision_rejects_unknown_values() -> None:
    with pytest.raises(ValueError, match="decision must be 'addressed' or 'not_relevant'"):
        resolver.normalize_decision(Namespace(decision="ignore"), {})


def test_root_comment_id_for_thread_returns_first_database_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], str | None, bool]] = []

    def fake_run_json(cmd, stdin=None, dry_run=False):
        calls.append((cmd, stdin, dry_run))
        return {
            "data": {
                "node": {
                    "__typename": "PullRequestReviewThread",
                    "comments": {
                        "nodes": [
                            {"databaseId": None},
                            {"databaseId": 123},
                            {"databaseId": 456},
                        ]
                    },
                }
            }
        }

    monkeypatch.setattr(resolver, "run_json", fake_run_json)

    comment_id = resolver.root_comment_id_for_thread("PRRT_1", dry_run=False)

    assert comment_id == 123
    assert calls == [
        (
            ["gh", "api", "graphql", "-F", "query=@-", "-F", "threadId=PRRT_1"],
            resolver.GET_THREAD_QUERY,
            False,
        )
    ]


def test_root_comment_id_for_thread_returns_none_for_dry_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dry_run_calls: list[bool] = []

    def fake_run_json(cmd, stdin=None, dry_run=False):
        dry_run_calls.append(dry_run)
        return {"dry_run": True}

    monkeypatch.setattr(resolver, "run_json", fake_run_json)

    assert resolver.root_comment_id_for_thread("PRRT_2", dry_run=True) is None
    assert dry_run_calls == [True]


def test_root_comment_id_for_thread_rejects_wrong_node_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        resolver,
        "run_json",
        lambda *args, **kwargs: {"data": {"node": {"__typename": "IssueComment"}}},
    )

    with pytest.raises(RuntimeError, match="did not return a PullRequestReviewThread"):
        resolver.root_comment_id_for_thread("PRRT_wrong", dry_run=False)


def test_create_review_reply_requires_pr_number() -> None:
    with pytest.raises(RuntimeError, match="PR number is required"):
        resolver.create_review_reply(
            resolver.PullRequestRef("octo", "repo", None),
            comment_id=7,
            summary="Fixed",
            dry_run=False,
        )


def test_create_review_reply_posts_to_pull_comment_replies_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], bool]] = []

    def fake_run_json(cmd, stdin=None, dry_run=False):
        calls.append((cmd, dry_run))
        return {"id": 91}

    monkeypatch.setattr(resolver, "run_json", fake_run_json)

    payload = resolver.create_review_reply(
        resolver.PullRequestRef("octo", "repo", 17),
        comment_id=7,
        summary="Fixed in abc1234",
        dry_run=False,
    )

    assert payload == {"id": 91}
    assert calls == [
        (
            [
                "gh",
                "api",
                "repos/octo/repo/pulls/17/comments/7/replies",
                "-X",
                "POST",
                "-f",
                "body=Fixed in abc1234",
            ],
            False,
        )
    ]


def test_resolve_review_thread_uses_graphql_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], str | None, bool]] = []

    def fake_run_json(cmd, stdin=None, dry_run=False):
        calls.append((cmd, stdin, dry_run))
        return {"data": {"resolveReviewThread": {"thread": {"id": "PRRT_1"}}}}

    monkeypatch.setattr(resolver, "run_json", fake_run_json)

    payload = resolver.resolve_review_thread("PRRT_1", dry_run=False)

    assert payload["data"]["resolveReviewThread"]["thread"]["id"] == "PRRT_1"
    assert calls == [
        (
            ["gh", "api", "graphql", "-F", "query=@-", "-F", "threadId=PRRT_1"],
            resolver.RESOLVE_THREAD_MUTATION,
            False,
        )
    ]


@pytest.mark.parametrize(
    ("kind", "expected_endpoint"),
    [
        ("issue_comment", "repos/octo/repo/issues/comments/7/reactions"),
        ("review_comment", "repos/octo/repo/pulls/comments/7/reactions"),
        ("review_thread", "repos/octo/repo/pulls/comments/7/reactions"),
    ],
)
def test_add_reaction_selects_endpoint_by_kind(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    expected_endpoint: str,
) -> None:
    calls: list[tuple[list[str], bool]] = []

    def fake_run_json(cmd, stdin=None, dry_run=False):
        calls.append((cmd, dry_run))
        return {"content": "+1"}

    monkeypatch.setattr(resolver, "run_json", fake_run_json)

    payload = resolver.add_reaction(
        repo="octo/repo",
        kind=kind,
        comment_id=7,
        reaction="+1",
        dry_run=False,
    )

    assert payload == {"content": "+1"}
    assert calls == [
        (
            ["gh", "api", expected_endpoint, "-X", "POST", "-f", "content=+1"],
            False,
        )
    ]


def test_add_reaction_rejects_unsupported_kinds() -> None:
    with pytest.raises(RuntimeError, match="Unsupported reaction target kind"):
        resolver.add_reaction("octo/repo", "note", 7, "+1", dry_run=False)


def test_execute_action_skips_top_level_reviews() -> None:
    result = resolver.execute_action(
        pr_ref=resolver.PullRequestRef("octo", "repo", 17),
        kind="review",
        decision="addressed",
        thread_id=None,
        comment_id=None,
        summary="",
        dry_run=False,
    )

    assert result["action_taken"] == "skipped"
    assert "not directly resolvable" in result["details"]["reason"]


def test_execute_action_review_thread_addressed_with_summary_replies_and_resolves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reply_calls: list[tuple[resolver.PullRequestRef, int, str, bool]] = []
    resolve_calls: list[tuple[str, bool]] = []

    def fake_create_review_reply(pr_ref, comment_id, summary, dry_run):
        reply_calls.append((pr_ref, comment_id, summary, dry_run))
        return {"id": 91}

    def fake_resolve_review_thread(thread_id, dry_run):
        resolve_calls.append((thread_id, dry_run))
        return {"resolved": True}

    monkeypatch.setattr(resolver, "create_review_reply", fake_create_review_reply)
    monkeypatch.setattr(resolver, "resolve_review_thread", fake_resolve_review_thread)

    result = resolver.execute_action(
        pr_ref=resolver.PullRequestRef("octo", "repo", 17),
        kind="review_thread",
        decision="addressed",
        thread_id="PRRT_1",
        comment_id=7,
        summary="Fixed in abc1234",
        dry_run=False,
    )

    assert result["action_taken"] == "replied_and_resolved"
    assert reply_calls == [
        (resolver.PullRequestRef("octo", "repo", 17), 7, "Fixed in abc1234", False)
    ]
    assert resolve_calls == [("PRRT_1", False)]


def test_execute_action_review_thread_addressed_without_summary_only_resolves(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        resolver,
        "create_review_reply",
        lambda *args, **kwargs: pytest.fail("create_review_reply should not be called"),
    )
    monkeypatch.setattr(
        resolver,
        "resolve_review_thread",
        lambda thread_id, dry_run: {"thread_id": thread_id, "dry_run": dry_run},
    )

    result = resolver.execute_action(
        pr_ref=resolver.PullRequestRef("octo", "repo", 17),
        kind="review_thread",
        decision="addressed",
        thread_id="PRRT_2",
        comment_id=8,
        summary="",
        dry_run=False,
    )

    assert result["action_taken"] == "resolved"
    assert result["details"]["reply"] is None
    assert result["details"]["resolve_thread"] == {"thread_id": "PRRT_2", "dry_run": False}


def test_execute_action_review_thread_not_relevant_reacts_with_minus_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reaction_calls: list[tuple[str, str, int, str, bool]] = []

    def fake_add_reaction(repo, kind, comment_id, reaction, dry_run):
        reaction_calls.append((repo, kind, comment_id, reaction, dry_run))
        return {"content": reaction}

    monkeypatch.setattr(resolver, "add_reaction", fake_add_reaction)

    result = resolver.execute_action(
        pr_ref=resolver.PullRequestRef("octo", "repo", 17),
        kind="review_thread",
        decision="not_relevant",
        thread_id="PRRT_3",
        comment_id=9,
        summary="",
        dry_run=False,
    )

    assert result["action_taken"] == "reacted"
    assert result["details"]["reaction_content"] == "-1"
    assert reaction_calls == [("octo/repo", "review_thread", 9, "-1", False)]


def test_execute_action_auto_fetches_root_comment_id_for_review_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(resolver, "root_comment_id_for_thread", lambda thread_id, dry_run: 77)
    monkeypatch.setattr(
        resolver,
        "create_review_reply",
        lambda pr_ref, comment_id, summary, dry_run: {"comment_id": comment_id, "summary": summary},
    )
    monkeypatch.setattr(
        resolver,
        "resolve_review_thread",
        lambda thread_id, dry_run: {"thread_id": thread_id},
    )

    result = resolver.execute_action(
        pr_ref=resolver.PullRequestRef("octo", "repo", 17),
        kind="review_thread",
        decision="addressed",
        thread_id="PRRT_auto",
        comment_id=None,
        summary="Fixed in abc1234",
        dry_run=False,
    )

    assert result["comment_id"] == 77
    assert result["details"]["reply"]["comment_id"] == 77


def test_execute_action_review_thread_without_resolvable_comment_id_only_reports_resolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(resolver, "root_comment_id_for_thread", lambda thread_id, dry_run: None)
    monkeypatch.setattr(
        resolver,
        "create_review_reply",
        lambda *args, **kwargs: pytest.fail("create_review_reply should not be called"),
    )
    monkeypatch.setattr(
        resolver,
        "resolve_review_thread",
        lambda thread_id, dry_run: {"thread_id": thread_id, "dry_run": dry_run},
    )

    result = resolver.execute_action(
        pr_ref=resolver.PullRequestRef("octo", "repo", 17),
        kind="review_thread",
        decision="addressed",
        thread_id="PRRT_missing_root",
        comment_id=None,
        summary="Fixed in abc1234",
        dry_run=False,
    )

    assert result["comment_id"] is None
    assert result["action_taken"] == "resolved"
    assert result["details"]["reply"] is None
    assert result["details"]["resolve_thread"] == {
        "thread_id": "PRRT_missing_root",
        "dry_run": False,
    }


@pytest.mark.parametrize(
    ("kind", "decision", "expected_reaction"),
    [
        ("issue_comment", "addressed", "+1"),
        ("issue_comment", "not_relevant", "-1"),
        ("review_comment", "addressed", "+1"),
        ("review_comment", "not_relevant", "-1"),
    ],
)
def test_execute_action_comment_kinds_use_expected_reactions(
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    decision: str,
    expected_reaction: str,
) -> None:
    reaction_calls: list[tuple[str, str, int, str, bool]] = []

    def fake_add_reaction(repo, kind, comment_id, reaction, dry_run):
        reaction_calls.append((repo, kind, comment_id, reaction, dry_run))
        return {"content": reaction}

    monkeypatch.setattr(resolver, "add_reaction", fake_add_reaction)

    result = resolver.execute_action(
        pr_ref=resolver.PullRequestRef("octo", "repo", 17),
        kind=kind,
        decision=decision,
        thread_id=None,
        comment_id=15,
        summary="",
        dry_run=False,
    )

    assert result["action_taken"] == "reacted"
    assert result["details"]["reaction_content"] == expected_reaction
    assert reaction_calls == [("octo/repo", kind, 15, expected_reaction, False)]


def test_execute_action_requires_thread_id_or_comment_id() -> None:
    with pytest.raises(RuntimeError, match="review_thread requires thread_id"):
        resolver.execute_action(
            pr_ref=resolver.PullRequestRef("octo", "repo", 17),
            kind="review_thread",
            decision="addressed",
            thread_id=None,
            comment_id=7,
            summary="Fixed",
            dry_run=False,
        )

    with pytest.raises(RuntimeError, match="issue_comment requires comment_id"):
        resolver.execute_action(
            pr_ref=resolver.PullRequestRef("octo", "repo", 17),
            kind="issue_comment",
            decision="addressed",
            thread_id=None,
            comment_id=None,
            summary="",
            dry_run=False,
        )


def test_execute_action_dry_run_returns_structured_helper_payloads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run_json(cmd, stdin=None, dry_run=False):
        return {"dry_run": dry_run, "command": cmd, "stdin": stdin}

    monkeypatch.setattr(resolver, "run_json", fake_run_json)

    result = resolver.execute_action(
        pr_ref=resolver.PullRequestRef("octo", "repo", 17),
        kind="review_thread",
        decision="addressed",
        thread_id="PRRT_dry",
        comment_id=7,
        summary="Fixed in abc1234",
        dry_run=True,
    )

    assert result["dry_run"] is True
    assert result["action_taken"] == "replied_and_resolved"
    assert result["details"]["reply"]["dry_run"] is True
    assert result["details"]["resolve_thread"]["dry_run"] is True
