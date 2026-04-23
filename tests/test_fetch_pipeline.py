from __future__ import annotations

import argparse
import copy
import json

import pytest

import fetch_unresolved_pr_feedback as fetcher
from conftest import load_fixture_json


def _pr_ref() -> fetcher.PullRequestRef:
    return fetcher.PullRequestRef("coolabnet", "community-box", 39)


def test_run_graphql_builds_gh_graphql_command_and_includes_cursor_only_when_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[list[str], str | None]] = []
    payload = {"data": {"ok": True}}

    def fake_run_json(cmd, stdin=None):
        calls.append((cmd, stdin))
        return payload

    monkeypatch.setattr(fetcher, "run_json", fake_run_json)

    assert fetcher._run_graphql("QUERY_1", _pr_ref()) == payload
    assert fetcher._run_graphql("QUERY_2", _pr_ref(), "threadsCursor", "CURSOR_1") == payload

    assert calls[0] == (
        [
            "gh", "api", "graphql", "-F", "query=@-",
            "-F", "owner=coolabnet",
            "-F", "repo=community-box",
            "-F", "number=39",
        ],
        "QUERY_1",
    )
    assert calls[1] == (
        [
            "gh", "api", "graphql", "-F", "query=@-",
            "-F", "owner=coolabnet",
            "-F", "repo=community-box",
            "-F", "number=39",
            "-F", "threadsCursor=CURSOR_1",
        ],
        "QUERY_2",
    )


def test_run_graphql_raises_runtime_error_on_graphql_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(fetcher, "run_json", lambda *args, **kwargs: {"errors": [{"message": "boom"}]})

    with pytest.raises(RuntimeError, match=r'"message": "boom"'):
        fetcher._run_graphql("QUERY", _pr_ref())


def test_paginate_collects_all_pages_and_captures_pr_meta_from_first_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page1 = load_fixture_json("graphql", "threads_page1.json")
    page2 = load_fixture_json("graphql", "threads_page2.json")
    calls: list[tuple[str | None, str | None]] = []

    def fake_run_graphql(query, pr_ref, cursor_name=None, cursor_val=None):
        calls.append((cursor_name, cursor_val))
        if cursor_val is None:
            return page1
        assert cursor_name == "threadsCursor"
        assert cursor_val == "CURSOR_1"
        return page2

    monkeypatch.setattr(fetcher, "_run_graphql", fake_run_graphql)

    meta_capture: dict[str, object] = {}
    items = fetcher._paginate(
        fetcher.GRAPHQL_QUERY_THREADS,
        _pr_ref(),
        "threadsCursor",
        ["repository", "pullRequest", "reviewThreads"],
        meta_capture=meta_capture,
    )

    assert [item["id"] for item in items] == [
        "PRRT_kwDOOemAKc58EO_X",
        "PRRT_kwDOOemAKc58EO_Z",
        "PRRT_kwDOOemAKc58VP6A",
    ]
    assert calls == [(None, None), ("threadsCursor", "CURSOR_1")]
    assert meta_capture == {
        "number": 39,
        "url": "https://github.com/coolabnet/community-box/pull/39",
        "title": "fix: Phase 1 critical bug fixes and safety nets",
        "state": "OPEN",
        "author": {"__typename": "User", "login": "luandro"},
    }


def test_paginate_handles_three_pages_and_keeps_first_page_meta_capture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = [
        {
            "data": {
                "repository": {
                    "pullRequest": {
                        "number": 39,
                        "url": "https://github.com/coolabnet/community-box/pull/39",
                        "title": "first title",
                        "state": "OPEN",
                        "author": {"__typename": "User", "login": "luandro"},
                        "reviewThreads": {
                            "pageInfo": {"hasNextPage": True, "endCursor": "CURSOR_1"},
                            "nodes": [{"id": "T1"}],
                        },
                    }
                }
            }
        },
        {
            "data": {
                "repository": {
                    "pullRequest": {
                        "number": 999,
                        "url": "https://github.com/example/other/pull/999",
                        "title": "second title should not replace meta",
                        "state": "CLOSED",
                        "author": {"__typename": "Bot", "login": "capy-ai"},
                        "reviewThreads": {
                            "pageInfo": {"hasNextPage": True, "endCursor": "CURSOR_2"},
                            "nodes": [{"id": "T2"}],
                        },
                    }
                }
            }
        },
        {
            "data": {
                "repository": {
                    "pullRequest": {
                        "reviewThreads": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "nodes": [{"id": "T3"}],
                        }
                    }
                }
            }
        },
    ]
    seen_cursors: list[str | None] = []

    def fake_run_graphql(query, pr_ref, cursor_name=None, cursor_val=None):
        seen_cursors.append(cursor_val)
        return pages[len(seen_cursors) - 1]

    monkeypatch.setattr(fetcher, "_run_graphql", fake_run_graphql)

    meta_capture: dict[str, object] = {}
    items = fetcher._paginate(
        fetcher.GRAPHQL_QUERY_THREADS,
        _pr_ref(),
        "threadsCursor",
        ["repository", "pullRequest", "reviewThreads"],
        meta_capture=meta_capture,
    )

    assert [item["id"] for item in items] == ["T1", "T2", "T3"]
    assert seen_cursors == [None, "CURSOR_1", "CURSOR_2"]
    assert meta_capture == {
        "number": 39,
        "url": "https://github.com/coolabnet/community-box/pull/39",
        "title": "first title",
        "state": "OPEN",
        "author": {"__typename": "User", "login": "luandro"},
    }


def test_paginate_raises_clean_runtime_error_when_connection_field_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = load_fixture_json("graphql", "threads_with_meta.json")
    payload["data"]["repository"]["pullRequest"]["reviewThreads"] = None
    monkeypatch.setattr(fetcher, "_run_graphql", lambda *args, **kwargs: payload)

    with pytest.raises(RuntimeError, match=r"repository\.pullRequest\.reviewThreads"):
        fetcher._paginate(
            fetcher.GRAPHQL_QUERY_THREADS,
            _pr_ref(),
            "threadsCursor",
            ["repository", "pullRequest", "reviewThreads"],
        )


@pytest.mark.parametrize("payload", [{}, {"data": None}])
def test_paginate_raises_clean_runtime_error_when_top_level_data_is_missing_or_null(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, object],
) -> None:
    monkeypatch.setattr(fetcher, "_run_graphql", lambda *args, **kwargs: payload)

    meta_capture: dict[str, object] = {}
    with pytest.raises(RuntimeError, match=r"repository\.pullRequest\.reviewThreads"):
        fetcher._paginate(
            fetcher.GRAPHQL_QUERY_THREADS,
            _pr_ref(),
            "threadsCursor",
            ["repository", "pullRequest", "reviewThreads"],
            meta_capture=meta_capture,
        )

    assert meta_capture == {}


def test_fetch_pr_meta_and_threads_flattens_author_login_and_raises_when_meta_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = load_fixture_json("graphql", "threads_with_meta.json")
    expected_threads = payload["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]

    def fake_paginate(*args, **kwargs):
        meta_capture = kwargs["meta_capture"]
        meta_capture.update(
            {
                "number": 39,
                "url": "https://github.com/coolabnet/community-box/pull/39",
                "title": "fix: Phase 1 critical bug fixes and safety nets",
                "state": "OPEN",
                "author": {"__typename": "User", "login": "luandro"},
            }
        )
        return expected_threads

    monkeypatch.setattr(fetcher, "_paginate", fake_paginate)
    meta, threads = fetcher.fetch_pr_meta_and_threads(_pr_ref())
    assert meta == {
        "number": 39,
        "url": "https://github.com/coolabnet/community-box/pull/39",
        "title": "fix: Phase 1 critical bug fixes and safety nets",
        "state": "OPEN",
        "author": "luandro",
    }
    assert threads == expected_threads

    monkeypatch.setattr(fetcher, "_paginate", lambda *args, **kwargs: [])
    with pytest.raises(RuntimeError, match=r"returned no pullRequest node"):
        fetcher.fetch_pr_meta_and_threads(_pr_ref())


def test_fetch_reviews_and_fetch_issue_comments_use_expected_backends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    review_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def fake_paginate(*args, **kwargs):
        review_calls.append((args, kwargs))
        return [{"id": "R1"}]

    monkeypatch.setattr(fetcher, "_paginate", fake_paginate)
    assert fetcher.fetch_review_threads(_pr_ref()) == [{"id": "R1"}]
    args, _kwargs = review_calls[0]
    assert args[1] == _pr_ref()
    assert args[2] == "threadsCursor"
    assert args[3] == ["repository", "pullRequest", "reviewThreads"]

    assert fetcher.fetch_reviews(_pr_ref()) == [{"id": "R1"}]
    args, _kwargs = review_calls[1]
    assert args[1] == _pr_ref()
    assert args[2] == "reviewsCursor"
    assert args[3] == ["repository", "pullRequest", "reviews"]

    comments_fixture = load_fixture_json("comments.json")
    monkeypatch.setattr(fetcher, "run_json", lambda *args, **kwargs: comments_fixture)
    assert fetcher.fetch_issue_comments(_pr_ref()) == comments_fixture

    monkeypatch.setattr(fetcher, "run_json", lambda *args, **kwargs: {"unexpected": True})
    with pytest.raises(RuntimeError, match=r"Unexpected issue comments payload shape"):
        fetcher.fetch_issue_comments(_pr_ref())


def test_build_unresolved_threads_filters_resolved_threads_and_preserves_real_comment_fields() -> None:
    payload = load_fixture_json("graphql", "threads_with_meta.json")
    threads = payload["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]

    result = fetcher.build_unresolved_threads(threads, pr_author="luandro", include_all=True)

    assert [thread["thread_id"] for thread in result] == ["PRRT_kwDOOemAKc58VP6A"]
    assert result[0]["root_comment_id"] == 3113772394
    assert result[0]["path"] == "app/src/App.tsx"
    assert result[0]["line"] == 11
    assert result[0]["comments"][0]["author"] == "capy-ai"
    assert result[0]["comments"][0]["excluded_from_attention"] is False


def test_real_thread_pages_flow_through_paginate_into_build_unresolved_threads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page1 = load_fixture_json("graphql", "threads_page1.json")
    page2 = load_fixture_json("graphql", "threads_page2.json")

    def fake_run_graphql(query, pr_ref, cursor_name=None, cursor_val=None):
        if cursor_val is None:
            return page1
        return page2

    monkeypatch.setattr(fetcher, "_run_graphql", fake_run_graphql)

    threads = fetcher._paginate(
        fetcher.GRAPHQL_QUERY_THREADS,
        _pr_ref(),
        "threadsCursor",
        ["repository", "pullRequest", "reviewThreads"],
    )
    unresolved = fetcher.build_unresolved_threads(threads, pr_author="luandro", include_all=True)

    assert [thread["thread_id"] for thread in unresolved] == ["PRRT_kwDOOemAKc58VP6A"]


def test_build_unresolved_threads_exclude_bots_suppresses_bot_only_threads_from_real_fixture() -> None:
    payload = load_fixture_json("graphql", "threads_with_meta.json")
    threads = payload["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]

    result = fetcher.build_unresolved_threads(threads, pr_author="luandro", include_all=False)

    assert result == []


def test_build_unresolved_threads_exclude_bots_keeps_unknown_author_threads() -> None:
    threads = [
        {
            "id": "PRRT_unknown",
            "isResolved": False,
            "isOutdated": False,
            "path": "src/example.py",
            "line": 12,
            "originalLine": 12,
            "startLine": 12,
            "originalStartLine": None,
            "diffSide": "RIGHT",
            "startDiffSide": None,
            "comments": {
                "nodes": [
                    {
                        "id": "PRRC_unknown",
                        "databaseId": 999,
                        "url": "https://github.com/example/repo/pull/1#discussion_r999",
                        "body": "Still relevant, but author metadata is unavailable.",
                        "createdAt": "2026-04-21T00:00:00Z",
                        "updatedAt": "2026-04-21T00:00:00Z",
                        "author": None,
                    }
                ]
            },
        }
    ]

    result = fetcher.build_unresolved_threads(threads, pr_author="luandro", include_all=False)

    assert len(result) == 1
    assert result[0]["thread_id"] == "PRRT_unknown"
    assert result[0]["comments"][0]["author"] is None
    assert result[0]["comments"][0]["excluded_from_attention"] is True


def test_build_unresolved_threads_exclude_bots_keeps_human_reviewer_threads() -> None:
    threads = [
        {
            "id": "PRRT_mixed",
            "isResolved": False,
            "isOutdated": True,
            "path": "src/mixed.py",
            "line": None,
            "originalLine": 27,
            "startLine": None,
            "originalStartLine": 24,
            "diffSide": "RIGHT",
            "startDiffSide": "LEFT",
            "comments": {
                "nodes": [
                    {
                        "id": "PRRC_bot",
                        "databaseId": None,
                        "url": "https://github.com/example/repo/pull/1#discussion_r100",
                        "body": "Automated suggestion.",
                        "createdAt": "2026-04-21T00:00:00Z",
                        "updatedAt": "2026-04-21T00:00:00Z",
                        "author": {"__typename": "Bot", "login": "capy-ai"},
                    },
                    {
                        "id": "PRRC_author",
                        "databaseId": 101,
                        "url": "https://github.com/example/repo/pull/1#discussion_r101",
                        "body": "Author follow-up.",
                        "createdAt": "2026-04-21T00:01:00Z",
                        "updatedAt": "2026-04-21T00:01:00Z",
                        "author": {"__typename": "User", "login": "luandro"},
                    },
                    {
                        "id": "PRRC_reviewer",
                        "databaseId": 202,
                        "url": "https://github.com/example/repo/pull/1#discussion_r202",
                        "body": "Human reviewer feedback.",
                        "createdAt": "2026-04-21T00:02:00Z",
                        "updatedAt": "2026-04-21T00:02:00Z",
                        "author": {"__typename": "User", "login": "reviewer1"},
                    },
                ]
            },
        }
    ]

    result = fetcher.build_unresolved_threads(threads, pr_author="luandro", include_all=False)

    assert len(result) == 1
    assert result[0]["thread_id"] == "PRRT_mixed"
    assert result[0]["root_comment_id"] == 101
    assert result[0]["line"] is None
    assert result[0]["original_line"] == 27
    assert result[0]["is_outdated"] is True
    assert [comment["author"] for comment in result[0]["comments"]] == ["capy-ai", "luandro", "reviewer1"]
    assert [comment["excluded_from_attention"] for comment in result[0]["comments"]] == [True, True, False]



def test_build_unresolved_threads_exclude_bots_keeps_threads_when_comment_page_is_truncated() -> None:
    threads = [
        {
            "id": "PRRT_truncated",
            "isResolved": False,
            "isOutdated": False,
            "path": "src/truncated.py",
            "line": 40,
            "originalLine": 40,
            "startLine": 40,
            "originalStartLine": None,
            "diffSide": "RIGHT",
            "startDiffSide": None,
            "comments": {
                "pageInfo": {"hasNextPage": True, "endCursor": "COMMENTS_CURSOR_1"},
                "nodes": [
                    {
                        "id": "PRRC_bot",
                        "databaseId": 301,
                        "url": "https://github.com/example/repo/pull/1#discussion_r301",
                        "body": "Automated suggestion.",
                        "createdAt": "2026-04-21T00:00:00Z",
                        "updatedAt": "2026-04-21T00:00:00Z",
                        "author": {"__typename": "Bot", "login": "capy-ai"},
                    },
                    {
                        "id": "PRRC_author",
                        "databaseId": 302,
                        "url": "https://github.com/example/repo/pull/1#discussion_r302",
                        "body": "Author reply.",
                        "createdAt": "2026-04-21T00:01:00Z",
                        "updatedAt": "2026-04-21T00:01:00Z",
                        "author": {"__typename": "User", "login": "luandro"},
                    },
                ],
            },
        }
    ]

    result = fetcher.build_unresolved_threads(threads, pr_author="luandro", include_all=False)

    assert len(result) == 1
    assert result[0]["thread_id"] == "PRRT_truncated"
    assert [comment["author"] for comment in result[0]["comments"]] == ["capy-ai", "luandro"]
    assert [comment["excluded_from_attention"] for comment in result[0]["comments"]] == [True, True]


def test_build_unresolved_threads_exclude_bots_keeps_threads_when_truncated_page_has_no_sampled_comments() -> None:
    threads = [
        {
            "id": "PRRT_truncated_empty",
            "isResolved": False,
            "isOutdated": False,
            "path": "src/truncated_empty.py",
            "line": 41,
            "originalLine": 41,
            "startLine": 41,
            "originalStartLine": None,
            "diffSide": "RIGHT",
            "startDiffSide": None,
            "comments": {
                "pageInfo": {"hasNextPage": True, "endCursor": "COMMENTS_CURSOR_2"},
                "nodes": [],
            },
        }
    ]

    result = fetcher.build_unresolved_threads(threads, pr_author="luandro", include_all=False)

    assert len(result) == 1
    assert result[0]["thread_id"] == "PRRT_truncated_empty"
    assert result[0]["comments"] == []



def test_build_unresolved_threads_filter_path_and_empty_comment_shapes_follow_include_all() -> None:
    threads = [
        {
            "id": "PRRT_path_mismatch",
            "isResolved": False,
            "isOutdated": False,
            "path": "src/skip.py",
            "line": 10,
            "originalLine": 10,
            "startLine": None,
            "originalStartLine": None,
            "diffSide": "RIGHT",
            "startDiffSide": None,
            "comments": None,
        },
        {
            "id": "PRRT_comments_none",
            "isResolved": False,
            "isOutdated": False,
            "path": "src/keep.py",
            "line": 11,
            "originalLine": 11,
            "startLine": None,
            "originalStartLine": None,
            "diffSide": "RIGHT",
            "startDiffSide": None,
            "comments": {"nodes": None},
        },
        {
            "id": "PRRT_comments_empty",
            "isResolved": False,
            "isOutdated": False,
            "path": "src/keep.py",
            "line": 12,
            "originalLine": 12,
            "startLine": None,
            "originalStartLine": None,
            "diffSide": "LEFT",
            "startDiffSide": None,
            "comments": {"nodes": []},
        },
    ]

    include_all = fetcher.build_unresolved_threads(
        threads,
        pr_author="luandro",
        include_all=True,
        filter_path="src/keep.py",
    )
    exclude_bots = fetcher.build_unresolved_threads(
        threads,
        pr_author="luandro",
        include_all=False,
        filter_path="src/keep.py",
    )

    assert [thread["thread_id"] for thread in include_all] == [
        "PRRT_comments_none",
        "PRRT_comments_empty",
    ]
    assert all(thread["comments"] == [] for thread in include_all)
    assert exclude_bots == []


def test_build_outstanding_reviews_filters_real_fixture_by_include_all() -> None:
    payload = load_fixture_json("graphql", "reviews.json")
    reviews = payload["data"]["repository"]["pullRequest"]["reviews"]["nodes"]

    include_all = fetcher.build_outstanding_reviews(reviews, pr_author="luandro", include_all=True)
    exclude_bots = fetcher.build_outstanding_reviews(reviews, pr_author="luandro", include_all=False)

    assert [review["id"] for review in include_all] == [
        "PRR_kwDOOemAKc72k3kW",
        "PRR_kwDOOemAKc72lQ5J",
        "PRR_kwDOOemAKc72-uDj",
    ]
    assert all(review["author"] == "capy-ai" for review in include_all)
    assert exclude_bots == []


def test_build_outstanding_reviews_latest_state_wins_and_drops_empty_bodies() -> None:
    reviews = [
        {
            "id": "r1",
            "url": "https://github.com/example/repo/pull/1#pullrequestreview-1",
            "state": "CHANGES_REQUESTED",
            "body": "Please fix this.",
            "submittedAt": "2026-04-21T00:00:00Z",
            "author": {"__typename": "User", "login": "reviewer1"},
        },
        {
            "id": "r2",
            "url": "https://github.com/example/repo/pull/1#pullrequestreview-2",
            "state": "APPROVED",
            "body": "",
            "submittedAt": "2026-04-21T00:10:00Z",
            "author": {"__typename": "User", "login": "reviewer1"},
        },
        {
            "id": "r3",
            "url": "https://github.com/example/repo/pull/1#pullrequestreview-3",
            "state": "COMMENTED",
            "body": "Needs explanation.",
            "submittedAt": "2026-04-21T00:05:00Z",
            "author": {"__typename": "User", "login": "reviewer2"},
        },
        {
            "id": "r4",
            "url": "https://github.com/example/repo/pull/1#pullrequestreview-4",
            "state": "DISMISSED",
            "body": "",
            "submittedAt": "2026-04-21T00:15:00Z",
            "author": {"__typename": "User", "login": "reviewer3"},
        },
        {
            "id": "r5",
            "url": "https://github.com/example/repo/pull/1#pullrequestreview-5",
            "state": "CHANGES_REQUESTED",
            "body": "Bot review body.",
            "submittedAt": "2026-04-21T00:20:00Z",
            "author": {"__typename": "Bot", "login": "capy-ai"},
        },
        {
            "id": "r6",
            "url": "https://github.com/example/repo/pull/1#pullrequestreview-6",
            "state": "COMMENTED",
            "body": "   ",
            "submittedAt": "2026-04-21T00:25:00Z",
            "author": {"__typename": "User", "login": "reviewer4"},
        },
    ]

    include_all = fetcher.build_outstanding_reviews(reviews, pr_author="luandro", include_all=True)
    exclude_bots = fetcher.build_outstanding_reviews(reviews, pr_author="luandro", include_all=False)

    assert [review["id"] for review in include_all] == ["r3", "r5"]
    assert [review["author"] for review in include_all] == ["reviewer2", "capy-ai"]
    assert [review["id"] for review in exclude_bots] == ["r3"]


def test_build_outstanding_reviews_include_all_marks_filtered_authors_and_sorts_by_submitted_at() -> None:
    reviews = [
        {
            "id": "r_pr_author",
            "url": "https://github.com/example/repo/pull/1#pullrequestreview-1",
            "state": "COMMENTED",
            "body": "Author note.",
            "submittedAt": "2026-04-21T00:02:00Z",
            "author": {"__typename": "User", "login": "luandro"},
        },
        {
            "id": "r_human",
            "url": "https://github.com/example/repo/pull/1#pullrequestreview-2",
            "state": "CHANGES_REQUESTED",
            "body": "Please fix this.",
            "submittedAt": "2026-04-21T00:01:00Z",
            "author": {"__typename": "User", "login": "reviewer1"},
        },
        {
            "id": "r_bot",
            "url": "https://github.com/example/repo/pull/1#pullrequestreview-3",
            "state": "COMMENTED",
            "body": "Automated follow-up.",
            "submittedAt": "2026-04-21T00:03:00Z",
            "author": {"__typename": "Bot", "login": "capy-ai"},
        },
    ]

    result = fetcher.build_outstanding_reviews(reviews, pr_author="luandro", include_all=True)

    assert [review["id"] for review in result] == ["r_human", "r_pr_author", "r_bot"]
    assert [review["excluded_from_attention"] for review in result] == [False, True, True]


def test_build_conversation_comments_filters_real_fixture_and_prefers_html_url() -> None:
    comments = load_fixture_json("comments.json")

    include_all = fetcher.build_conversation_comments(comments, pr_author="luandro", include_all=True)
    exclude_author = fetcher.build_conversation_comments(comments, pr_author="luandro", include_all=False)

    assert include_all == [
        {
            "id": 4284332726,
            "url": "https://github.com/coolabnet/community-box/pull/39#issuecomment-4284332726",
            "author": "luandro",
            "created_at": "2026-04-20T21:14:14Z",
            "updated_at": "2026-04-20T21:14:14Z",
            "body": "Addressed all 3 review threads and verified the fixes.",
        }
    ]
    assert exclude_author == []


def test_build_conversation_comments_filters_empty_bodies_bots_and_missing_users() -> None:
    comments = [
        {
            "id": 1,
            "html_url": "https://github.com/example/repo/pull/1#issuecomment-1",
            "url": "https://api.github.com/repos/example/repo/issues/comments/1",
            "created_at": "2026-04-21T00:00:00Z",
            "updated_at": "2026-04-21T00:00:00Z",
            "body": "Human feedback.",
            "user": {"login": "reviewer1", "type": "User"},
        },
        {
            "id": 2,
            "html_url": "https://github.com/example/repo/pull/1#issuecomment-2",
            "url": "https://api.github.com/repos/example/repo/issues/comments/2",
            "created_at": "2026-04-21T00:01:00Z",
            "updated_at": "2026-04-21T00:01:00Z",
            "body": "Bot feedback.",
            "user": {"login": "dependabot[bot]", "type": "Bot"},
        },
        {
            "id": 3,
            "html_url": "https://github.com/example/repo/pull/1#issuecomment-3",
            "url": "https://api.github.com/repos/example/repo/issues/comments/3",
            "created_at": "2026-04-21T00:02:00Z",
            "updated_at": "2026-04-21T00:02:00Z",
            "body": "   ",
            "user": {"login": "reviewer2", "type": "User"},
        },
        {
            "id": 4,
            "url": "https://api.github.com/repos/example/repo/issues/comments/4",
            "created_at": "2026-04-21T00:03:00Z",
            "updated_at": "2026-04-21T00:03:00Z",
            "body": "Unknown user metadata.",
            "user": None,
        },
    ]

    include_all = fetcher.build_conversation_comments(comments, pr_author="luandro", include_all=True)
    exclude_bots = fetcher.build_conversation_comments(comments, pr_author="luandro", include_all=False)

    assert include_all == [
        {
            "id": 1,
            "url": "https://github.com/example/repo/pull/1#issuecomment-1",
            "author": "reviewer1",
            "created_at": "2026-04-21T00:00:00Z",
            "updated_at": "2026-04-21T00:00:00Z",
            "body": "Human feedback.",
        },
        {
            "id": 2,
            "url": "https://github.com/example/repo/pull/1#issuecomment-2",
            "author": "dependabot[bot]",
            "created_at": "2026-04-21T00:01:00Z",
            "updated_at": "2026-04-21T00:01:00Z",
            "body": "Bot feedback.",
        },
        {
            "id": 4,
            "url": "https://api.github.com/repos/example/repo/issues/comments/4",
            "author": None,
            "created_at": "2026-04-21T00:03:00Z",
            "updated_at": "2026-04-21T00:03:00Z",
            "body": "Unknown user metadata.",
        },
    ]
    assert exclude_bots == [
        {
            "id": 1,
            "url": "https://github.com/example/repo/pull/1#issuecomment-1",
            "author": "reviewer1",
            "created_at": "2026-04-21T00:00:00Z",
            "updated_at": "2026-04-21T00:00:00Z",
            "body": "Human feedback.",
        }
    ]


def test_fetch_main_threads_only_skips_reviews_and_issue_comments_and_emits_thread_summary(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    args = argparse.Namespace(
        repo=None,
        pr=None,
        url="https://github.com/coolabnet/community-box/pull/39",
        exclude_bots=False,
        include_all=False,
        filter_path=None,
        threads_only=True,
        output=None,
        minimal=False,
        format="json",
    )
    payload = load_fixture_json("graphql", "threads_with_meta.json")
    pr_data = payload["data"]["repository"]["pullRequest"]
    meta = {
        "number": pr_data["number"],
        "url": pr_data["url"],
        "title": pr_data["title"],
        "state": pr_data["state"],
        "author": pr_data["author"]["login"],
    }
    threads = pr_data["reviewThreads"]["nodes"]

    monkeypatch.setattr(fetcher.argparse.ArgumentParser, "parse_args", lambda self: args)
    monkeypatch.setattr(fetcher, "ensure_gh_auth", lambda: None)
    monkeypatch.setattr(fetcher, "resolve_pr_ref", lambda parsed_args: _pr_ref())
    monkeypatch.setattr(fetcher, "fetch_pr_meta_and_threads", lambda pr_ref: (meta, threads))
    monkeypatch.setattr(fetcher, "fetch_reviews", lambda pr_ref: pytest.fail("fetch_reviews should not be called"))
    monkeypatch.setattr(fetcher, "fetch_issue_comments", lambda pr_ref: pytest.fail("fetch_issue_comments should not be called"))

    fetcher.main()

    result = json.loads(capsys.readouterr().out)
    assert result["summary"]["unresolved_review_thread_count"] == 1
    assert result["summary"]["outstanding_review_count"] == 0
    assert result["summary"]["conversation_comment_count"] == 0
    assert [thread["thread_id"] for thread in result["unresolved_review_threads"]] == ["PRRT_kwDOOemAKc58VP6A"]


def test_fetch_main_exclude_bots_removes_bot_only_threads_from_summary_counts(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    args = argparse.Namespace(
        repo=None,
        pr=None,
        url="https://github.com/coolabnet/community-box/pull/39",
        exclude_bots=True,
        include_all=False,
        filter_path=None,
        threads_only=False,
        output=None,
        minimal=False,
        format="json",
    )
    threads_payload = load_fixture_json("graphql", "threads_with_meta.json")
    reviews_payload = load_fixture_json("graphql", "reviews.json")
    comments_payload = load_fixture_json("comments.json")
    pr_data = threads_payload["data"]["repository"]["pullRequest"]
    meta = {
        "number": pr_data["number"],
        "url": pr_data["url"],
        "title": pr_data["title"],
        "state": pr_data["state"],
        "author": pr_data["author"]["login"],
    }
    threads = copy.deepcopy(pr_data["reviewThreads"]["nodes"])
    reviews = copy.deepcopy(reviews_payload["data"]["repository"]["pullRequest"]["reviews"]["nodes"])
    comments = copy.deepcopy(comments_payload)

    monkeypatch.setattr(fetcher.argparse.ArgumentParser, "parse_args", lambda self: args)
    monkeypatch.setattr(fetcher, "ensure_gh_auth", lambda: None)
    monkeypatch.setattr(fetcher, "resolve_pr_ref", lambda parsed_args: _pr_ref())
    monkeypatch.setattr(fetcher, "fetch_pr_meta_and_threads", lambda pr_ref: (meta, threads))
    monkeypatch.setattr(fetcher, "fetch_reviews", lambda pr_ref: reviews)
    monkeypatch.setattr(fetcher, "fetch_issue_comments", lambda pr_ref: comments)

    fetcher.main()

    result = json.loads(capsys.readouterr().out)
    assert result["summary"]["unresolved_review_thread_count"] == 0
    assert result["summary"]["outstanding_review_count"] == 0
    assert result["summary"]["conversation_comment_count"] == 0
    assert result["summary"]["total_attention_items"] == 0
    assert result["unresolved_review_threads"] == []
    assert result["outstanding_reviews"] == []
    assert result["conversation_comments"] == []
