from __future__ import annotations

import fetch_unresolved_pr_feedback as fetcher
import pytest


@pytest.mark.parametrize(
    ("repo", "expected"),
    [
        ("octo/repo", ("octo", "repo")),
        ("owner/nested-name", ("owner", "nested-name")),
    ],
)
def test_parse_repo_name_accepts_owner_name_pairs(
    repo: str,
    expected: tuple[str, str],
) -> None:
    assert fetcher.parse_repo_name(repo) == expected


@pytest.mark.parametrize("repo", ["", "octo", "/repo", "octo/"])
def test_parse_repo_name_rejects_invalid_shapes(repo: str) -> None:
    with pytest.raises(ValueError, match="Invalid repo"):
        fetcher.parse_repo_name(repo)


def test_parse_pr_url_extracts_pull_request_from_pull_or_review_urls() -> None:
    assert fetcher.parse_pr_url("https://github.com/octo/repo/pull/7") == fetcher.PullRequestRef(
        "octo",
        "repo",
        7,
    )
    assert fetcher.parse_pr_url(
        "https://github.com/octo/repo/pull/18/files#r123"
    ) == fetcher.PullRequestRef("octo", "repo", 18)


def test_parse_pr_url_rejects_non_pr_urls() -> None:
    with pytest.raises(ValueError, match="Could not parse PR URL"):
        fetcher.parse_pr_url("https://github.com/octo/repo/issues/7")


@pytest.mark.parametrize(
    ("login", "expected"),
    [
        ("dependabot[bot]", True),
        ("merge-bot", True),
        ("reviewer", False),
        (None, False),
    ],
)
def test_is_bot_login_detects_supported_suffixes(
    login: str | None,
    expected: bool,
) -> None:
    assert fetcher.is_bot_login(login) is expected


@pytest.mark.parametrize(
    ("author", "expected"),
    [
        ({"__typename": "Bot", "login": "capy-ai"}, True),
        ({"__typename": "User", "login": "reviewer"}, False),
        (None, False),
    ],
)
def test_is_bot_author_detects_bot_typename(
    author: dict[str, str] | None,
    expected: bool,
) -> None:
    assert fetcher.is_bot_author(author) is expected


@pytest.mark.parametrize(
    ("login", "pr_author", "include_all", "author", "expected"),
    [
        ("reviewer", "luandro", False, {"__typename": "User", "login": "reviewer"}, True),
        ("luandro", "luandro", False, {"__typename": "User", "login": "luandro"}, False),
        ("dependabot[bot]", "luandro", False, {"__typename": "Bot", "login": "dependabot[bot]"}, False),
        (None, "luandro", False, None, False),
        (None, "luandro", True, None, True),
    ],
)
def test_keep_actor_matches_include_all_author_and_bot_rules(
    login: str | None,
    pr_author: str | None,
    include_all: bool,
    author: dict[str, str] | None,
    expected: bool,
) -> None:
    assert fetcher.keep_actor(login, pr_author, include_all, author) is expected


def test_apply_minimal_preserves_metadata_and_drops_verbose_thread_fields() -> None:
    thread_body = "x" * 205
    review_body = "y" * 405
    result = {
        "pull_request": {"owner": "octo", "repo": "repo", "number": 17},
        "summary": {"total_attention_items": 3},
        "unresolved_review_threads": [
            {
                "thread_id": "T1",
                "root_comment_id": 12,
                "path": "src/app.py",
                "line": None,
                "original_line": 44,
                "diff_side": "RIGHT",
                "is_outdated": True,
                "comments": [
                    {
                        "id": "C1",
                        "author": "reviewer",
                        "body": thread_body,
                        "url": "https://github.com/example/repo/pull/1#discussion_r1",
                    }
                ],
            },
            {
                "thread_id": "T2",
                "root_comment_id": None,
                "path": None,
                "line": None,
                "original_line": None,
                "comments": [],
            },
        ],
        "outstanding_reviews": [
            {
                "id": "R1",
                "author": "reviewer",
                "state": "CHANGES_REQUESTED",
                "body": review_body,
                "url": "https://github.com/example/repo/pull/1#pullrequestreview-1",
            }
        ],
        "conversation_comments": [
            {
                "id": 91,
                "author": "teammate",
                "body": thread_body,
                "url": "https://github.com/example/repo/pull/1#issuecomment-91",
            }
        ],
        "provenance": {"graph_source": "graphql"},
    }

    minimal = fetcher._apply_minimal(result)

    assert minimal["pull_request"] == result["pull_request"]
    assert minimal["summary"] == result["summary"]
    assert minimal["unresolved_review_threads"][0] == {
        "thread_id": "T1",
        "root_comment_id": 12,
        "path": "src/app.py",
        "line": 44,
        "author": "reviewer",
        "body": ("x" * 199) + "\u2026",
    }
    assert minimal["unresolved_review_threads"][1] == {
        "thread_id": "T2",
        "root_comment_id": None,
        "path": None,
        "line": None,
        "author": None,
        "body": "",
    }
    assert minimal["outstanding_reviews"] == [
        {
            "id": "R1",
            "author": "reviewer",
            "state": "CHANGES_REQUESTED",
            "body": ("y" * 399) + "\u2026",
        }
    ]
    assert minimal["conversation_comments"] == [
        {"id": 91, "author": "teammate", "body": ("x" * 199) + "\u2026"}
    ]
    assert "provenance" not in minimal


@pytest.mark.parametrize(
    ("login", "expected"),
    [
        ("reviewer", "@reviewer"),
        (None, "@(unknown)"),
    ],
)
def test_at_formats_usernames_with_unknown_fallback(login: str | None, expected: str) -> None:
    assert fetcher._at(login) == expected


def test_format_text_renders_all_sections_markers_and_trailing_newline() -> None:
    result = {
        "pull_request": {
            "owner": "octo",
            "repo": "repo",
            "number": 17,
            "url": "https://github.com/octo/repo/pull/17",
            "title": "Fix parser edge case",
            "state": "OPEN",
            "author": "luandro",
        },
        "summary": {
            "unresolved_review_thread_count": 1,
            "outstanding_review_count": 1,
            "conversation_comment_count": 1,
        },
        "unresolved_review_threads": [
            {
                "thread_id": "T1",
                "root_comment_id": 12,
                "path": "src/app.py",
                "line": 44,
                "original_line": 41,
                "is_outdated": True,
                "comments": [
                    {"author": "capy-ai", "body": "Automated note.", "excluded_from_attention": True},
                    {"author": None, "body": "Needs a human follow-up.", "excluded_from_attention": False},
                ],
            }
        ],
        "outstanding_reviews": [
            {
                "id": "R1",
                "author": "capy-ai",
                "state": "CHANGES_REQUESTED",
                "body": "Please update the docs.",
                "excluded_from_attention": True,
            }
        ],
        "conversation_comments": [
            {"id": 91, "author": None, "body": "Can you add a regression test?"}
        ],
    }

    text = fetcher._format_text(result)

    assert text.endswith("\n")
    assert "PR #17" in text
    assert "Repo: octo/repo" in text
    assert "UNRESOLVED REVIEW THREADS" in text
    assert "[1] src/app.py:44  [OUTDATED]" in text
    assert "thread_id: T1" in text
    assert "comment_id: 12" in text
    assert "@capy-ai  [bot]:" in text
    assert "@(unknown):" in text
    assert "OUTSTANDING REVIEWS" in text
    assert "@capy-ai  [bot]  state: CHANGES_REQUESTED" in text
    assert "CONVERSATION COMMENTS" in text
    assert "[1] @(unknown)  id: 91" in text


def test_format_text_reports_empty_feedback() -> None:
    text = fetcher._format_text(
        {
            "pull_request": {
                "owner": "octo",
                "repo": "repo",
                "number": 17,
                "url": "https://github.com/octo/repo/pull/17",
                "title": None,
                "state": "OPEN",
                "author": None,
            },
            "summary": {},
            "unresolved_review_threads": [],
            "outstanding_reviews": [],
            "conversation_comments": [],
        }
    )

    assert text.endswith("\n")
    assert "No unresolved feedback found." in text


def test_paginate_handles_empty_nodes_with_page_info(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": None,
                    }
                }
            }
        }
    }
    monkeypatch.setattr(fetcher, "_run_graphql", lambda *args, **kwargs: payload)

    items = fetcher._paginate(
        fetcher.GRAPHQL_QUERY_THREADS,
        fetcher.PullRequestRef("octo", "repo", 17),
        "threadsCursor",
        ["repository", "pullRequest", "reviewThreads"],
        meta_capture={},
    )

    assert items == []


def test_paginate_raises_when_page_info_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "nodes": [],
                    }
                }
            }
        }
    }
    monkeypatch.setattr(fetcher, "_run_graphql", lambda *args, **kwargs: payload)

    with pytest.raises(RuntimeError, match=r"repository\.pullRequest\.reviewThreads\.pageInfo"):
        fetcher._paginate(
            fetcher.GRAPHQL_QUERY_THREADS,
            fetcher.PullRequestRef("octo", "repo", 17),
            "threadsCursor",
            ["repository", "pullRequest", "reviewThreads"],
            meta_capture={},
        )


def test_graphql_queries_keep_required_connection_and_field_contracts() -> None:
    for token in [
        "reviewThreads(first: 100",
        "pageInfo { hasNextPage endCursor }",
        "comments(first: 100)",
        "comments(first: 100) {\n            pageInfo { hasNextPage endCursor }",
        "databaseId",
        "diffSide",
        "author { __typename login }",
    ]:
        assert token in fetcher.GRAPHQL_QUERY_THREADS

    for token in [
        "reviews(first: 100",
        "state body submittedAt",
        "author { __typename login }",
    ]:
        assert token in fetcher.GRAPHQL_QUERY_REVIEWS

    for token in [
        "comments(first: 100",
        "id url body createdAt updatedAt",
        "author { __typename login }",
    ]:
        assert token in fetcher.GRAPHQL_QUERY_COMMENTS
