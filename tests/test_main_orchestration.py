from __future__ import annotations

import io
import json
from argparse import Namespace

import pytest

import build_actions
import resolve_pr_feedback as resolver
import fetch_unresolved_pr_feedback as fetcher


def test_build_actions_main_reads_stdin_drops_context_and_validates_successfully(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    args = Namespace(
        file=None,
        output=None,
        decision="addressed",
        summary="Fixed in abc1234",
        drop_context=True,
        validate=True,
    )
    monkeypatch.setattr(build_actions.argparse.ArgumentParser, "parse_args", lambda self: args)
    monkeypatch.setattr(
        build_actions.sys,
        "stdin",
        io.StringIO(
            json.dumps(
                {
                    "pull_request": {"owner": "octo", "repo": "repo", "number": 17},
                    "unresolved_review_threads": [
                        {
                            "thread_id": "T1",
                            "root_comment_id": 42,
                            "path": "src/main.py",
                            "line": 17,
                        }
                    ],
                    "conversation_comments": [
                        {"id": 91, "author": "teammate", "body": "Can you add a test?"}
                    ],
                }
            ),
        ),
    )

    build_actions.main()

    captured = capsys.readouterr()
    assert captured.err == ""
    assert json.loads(captured.out) == [
        {
            "repo": "octo/repo",
            "pr": 17,
            "kind": "review_thread",
            "thread_id": "T1",
            "decision": "addressed",
            "comment_id": 42,
            "summary": "Fixed in abc1234",
        },
        {
            "repo": "octo/repo",
            "kind": "issue_comment",
            "comment_id": 91,
            "decision": "addressed",
        },
    ]


def test_build_actions_main_validates_malformed_feedback_and_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys,
) -> None:
    feedback_path = tmp_path / "feedback.json"
    output_path = tmp_path / "actions.json"
    feedback_path.write_text(
        json.dumps(
            {
                "unresolved_review_threads": [
                    {
                        "thread_id": "T1",
                        "root_comment_id": 42,
                        "path": "src/main.py",
                        "line": 17,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    args = Namespace(
        file=str(feedback_path),
        output=str(output_path),
        decision="addressed",
        summary="Fixed in abc1234",
        drop_context=False,
        validate=True,
    )
    monkeypatch.setattr(build_actions.argparse.ArgumentParser, "parse_args", lambda self: args)

    with pytest.raises(SystemExit) as excinfo:
        build_actions.main()

    assert excinfo.value.code == 1
    assert output_path.read_text(encoding="utf-8") == (
        '[\n'
        '  {\n'
        '    "repo": null,\n'
        '    "pr": null,\n'
        '    "kind": "review_thread",\n'
        '    "thread_id": "T1",\n'
        '    "decision": "addressed",\n'
        '    "comment_id": 42,\n'
        '    "summary": "Fixed in abc1234",\n'
        '    "_context": "src/main.py:17"\n'
        '  }\n'
        ']\n'
    )

    captured = capsys.readouterr()
    assert "#1: no repo/url/pull_request — resolver will reject" in captured.err


def test_resolver_main_cli_flags_override_item_file_and_emit_verbose_stderr(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys,
) -> None:
    item_path = tmp_path / "action.json"
    item_path.write_text(
        json.dumps(
            {
                "pull_request": {"owner": "old", "repo": "repo", "number": 1},
                "kind": "review_thread",
                "decision": "not_relevant",
                "thread_id": "OLD_THREAD",
                "comment_id": 7,
                "summary": "old summary",
            }
        ),
        encoding="utf-8",
    )
    args = Namespace(
        repo=None,
        pr=None,
        url="https://github.com/acme/widgets/pull/23",
        kind="review_thread",
        thread_id="NEW_THREAD",
        comment_id=99,
        decision="addressed",
        summary="Fixed in abc1234",
        item_json=None,
        item_file=str(item_path),
        dry_run=False,
        verbose=True,
    )
    execute_calls: list[dict[str, object]] = []

    def fake_execute_action(**kwargs):
        execute_calls.append(kwargs)
        return {
            "ok": True,
            "repo": kwargs["pr_ref"].full_repo,
            "pr": kwargs["pr_ref"].number,
            "thread_id": kwargs["thread_id"],
            "comment_id": kwargs["comment_id"],
            "summary": kwargs["summary"],
        }

    monkeypatch.setattr(resolver.argparse.ArgumentParser, "parse_args", lambda self: args)
    monkeypatch.setattr(resolver, "ensure_gh_auth", lambda dry_run: None)
    monkeypatch.setattr(resolver, "execute_action", fake_execute_action)

    resolver.main()

    captured = capsys.readouterr()
    assert execute_calls == [
        {
            "pr_ref": resolver.PullRequestRef("acme", "widgets", 23),
            "kind": "review_thread",
            "decision": "addressed",
            "thread_id": "NEW_THREAD",
            "comment_id": 99,
            "summary": "Fixed in abc1234",
            "dry_run": False,
            "verbose": True,
        }
    ]
    assert json.loads(captured.out) == {
        "ok": True,
        "repo": "acme/widgets",
        "pr": 23,
        "thread_id": "NEW_THREAD",
        "comment_id": 99,
        "summary": "Fixed in abc1234",
    }
    assert "[resolve] kind=review_thread decision=addressed thread=NEW_THREAD comment=99" in captured.err


def test_resolver_main_repo_and_pr_override_item_pull_request_metadata(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys,
) -> None:
    item_path = tmp_path / "action.json"
    item_path.write_text(
        json.dumps(
            {
                "pull_request": {"owner": "old", "repo": "repo", "number": 1},
                "kind": "issue_comment",
                "decision": "addressed",
                "comment_id": 7,
            }
        ),
        encoding="utf-8",
    )
    args = Namespace(
        repo="acme/widgets",
        pr=23,
        url=None,
        kind=None,
        thread_id=None,
        comment_id=None,
        decision=None,
        summary=None,
        item_json=None,
        item_file=str(item_path),
        dry_run=False,
        verbose=False,
    )
    execute_calls: list[dict[str, object]] = []

    def fake_execute_action(**kwargs):
        execute_calls.append(kwargs)
        return {
            "ok": True,
            "repo": kwargs["pr_ref"].full_repo,
            "pr": kwargs["pr_ref"].number,
        }

    monkeypatch.setattr(resolver.argparse.ArgumentParser, "parse_args", lambda self: args)
    monkeypatch.setattr(resolver, "ensure_gh_auth", lambda dry_run: None)
    monkeypatch.setattr(resolver, "execute_action", fake_execute_action)

    resolver.main()

    assert execute_calls == [
        {
            "pr_ref": resolver.PullRequestRef("acme", "widgets", 23),
            "kind": "issue_comment",
            "decision": "addressed",
            "thread_id": None,
            "comment_id": 7,
            "summary": "",
            "dry_run": False,
            "verbose": False,
        }
    ]
    assert json.loads(capsys.readouterr().out) == {
        "ok": True,
        "repo": "acme/widgets",
        "pr": 23,
    }


def test_resolver_main_uses_current_branch_pr_fallback_without_repo_or_pr(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    args = Namespace(
        repo=None,
        pr=None,
        url=None,
        kind=None,
        thread_id=None,
        comment_id=None,
        decision=None,
        summary=None,
        item_json='{"kind":"issue_comment","decision":"addressed","comment_id":7}',
        item_file=None,
        dry_run=False,
        verbose=False,
    )

    run_json_calls: list[list[str]] = []
    execute_calls: list[dict[str, object]] = []

    def fake_run_json(cmd, stdin=None, dry_run=False):
        run_json_calls.append(cmd)
        assert stdin is None
        assert dry_run is False
        return {"number": 39, "url": "https://github.com/acme/widgets/pull/39"}

    def fake_execute_action(**kwargs):
        execute_calls.append(kwargs)
        return {"ok": True, "repo": kwargs["pr_ref"].full_repo, "pr": kwargs["pr_ref"].number}

    monkeypatch.setattr(resolver.argparse.ArgumentParser, "parse_args", lambda self: args)
    monkeypatch.setattr(resolver, "run_json", fake_run_json)
    monkeypatch.setattr(resolver, "ensure_gh_auth", lambda dry_run: None)
    monkeypatch.setattr(resolver, "execute_action", fake_execute_action)

    resolver.main()

    assert run_json_calls == [["gh", "pr", "view", "--json", "number,url"]]
    assert execute_calls[0]["pr_ref"].full_repo == "acme/widgets"
    assert execute_calls[0]["pr_ref"].number == 39

    captured = capsys.readouterr()
    assert '"ok": true' in captured.out


def test_resolver_main_dry_run_review_thread_uses_placeholder_pr_for_current_branch_fallback(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    args = Namespace(
        repo=None,
        pr=None,
        url=None,
        kind=None,
        thread_id=None,
        comment_id=None,
        decision=None,
        summary=None,
        item_json='{"kind":"review_thread","decision":"addressed","thread_id":"PRRT_1","comment_id":7,"summary":"Fixed in abc1234"}',
        item_file=None,
        dry_run=True,
        verbose=False,
    )

    execute_calls: list[dict[str, object]] = []

    def fake_execute_action(**kwargs):
        execute_calls.append(kwargs)
        return {"ok": True, "repo": kwargs["pr_ref"].full_repo, "pr": kwargs["pr_ref"].number}

    monkeypatch.setattr(resolver.argparse.ArgumentParser, "parse_args", lambda self: args)
    monkeypatch.setattr(resolver, "ensure_gh_auth", lambda dry_run: None)
    monkeypatch.setattr(resolver, "execute_action", fake_execute_action)

    resolver.main()

    assert execute_calls[0]["pr_ref"].full_repo == "OWNER/REPO"
    assert execute_calls[0]["pr_ref"].number == 1
    assert execute_calls[0]["dry_run"] is True

    captured = capsys.readouterr()
    assert '"ok": true' in captured.out


def test_fetch_main_writes_json_output_file_with_provenance(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
    capsys,
) -> None:
    output_path = tmp_path / "feedback.json"
    args = Namespace(
        repo=None,
        pr=None,
        url="https://github.com/octo/repo/pull/17",
        exclude_bots=False,
        include_all=False,
        filter_path=None,
        threads_only=False,
        output=str(output_path),
        minimal=False,
        format="json",
    )
    pr_ref = fetcher.PullRequestRef("octo", "repo", 17)
    meta = {
        "number": 17,
        "url": "https://github.com/octo/repo/pull/17",
        "title": "Fix parser edge case",
        "state": "OPEN",
        "author": "luandro",
    }
    threads = [
        {
            "thread_id": "T1",
            "root_comment_id": 12,
            "path": "src/app.py",
            "line": 44,
            "original_line": 44,
            "comments": [{"author": "reviewer", "body": "Please fix this."}],
        }
    ]
    reviews = [{"id": "R1", "author": "reviewer", "state": "COMMENTED", "body": "Still looks off."}]
    comments = [{"id": 91, "author": "teammate", "body": "Can you add a test?"}]

    monkeypatch.setattr(fetcher.argparse.ArgumentParser, "parse_args", lambda self: args)
    monkeypatch.setattr(fetcher, "ensure_gh_auth", lambda: None)
    monkeypatch.setattr(fetcher, "resolve_pr_ref", lambda parsed_args: pr_ref)
    monkeypatch.setattr(fetcher, "fetch_pr_meta_and_threads", lambda ref: (meta, [{}]))
    monkeypatch.setattr(fetcher, "fetch_reviews", lambda ref: [{}])
    monkeypatch.setattr(fetcher, "fetch_issue_comments", lambda ref: [{}])
    monkeypatch.setattr(fetcher, "build_unresolved_threads", lambda *a, **k: threads)
    monkeypatch.setattr(fetcher, "build_outstanding_reviews", lambda *a, **k: reviews)
    monkeypatch.setattr(fetcher, "build_conversation_comments", lambda *a, **k: comments)

    fetcher.main()

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["pull_request"]["owner"] == "octo"
    assert payload["summary"]["unresolved_review_thread_count"] == 1
    assert payload["summary"]["outstanding_review_count"] == 1
    assert payload["summary"]["conversation_comment_count"] == 1
    assert payload["summary"]["total_attention_items"] == 3
    assert payload["provenance"] == {
        "graph_source": "gh api graphql reviewThreads/reviews",
        "rest_source": "gh api issues/{n}/comments",
    }
    assert capsys.readouterr().out == ""


def test_fetch_main_minimal_json_applies_minimal_transform(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    args = Namespace(
        repo=None,
        pr=None,
        url="https://github.com/octo/repo/pull/17",
        exclude_bots=False,
        include_all=False,
        filter_path=None,
        threads_only=False,
        output=None,
        minimal=True,
        format="json",
    )
    pr_ref = fetcher.PullRequestRef("octo", "repo", 17)
    full_result = {
        "pull_request": {"owner": "octo", "repo": "repo", "number": 17},
        "summary": {"total_attention_items": 1},
        "unresolved_review_threads": [{"thread_id": "T1", "comments": [{"id": "C1"}]}],
        "outstanding_reviews": [],
        "conversation_comments": [],
        "provenance": {"graph_source": "graphql"},
    }

    monkeypatch.setattr(fetcher.argparse.ArgumentParser, "parse_args", lambda self: args)
    monkeypatch.setattr(fetcher, "ensure_gh_auth", lambda: None)
    monkeypatch.setattr(fetcher, "resolve_pr_ref", lambda parsed_args: pr_ref)
    monkeypatch.setattr(
        fetcher,
        "fetch_pr_meta_and_threads",
        lambda ref: (
            {
                "number": 17,
                "url": "https://github.com/octo/repo/pull/17",
                "title": "Fix parser edge case",
                "state": "OPEN",
                "author": "luandro",
            },
            [],
        ),
    )
    monkeypatch.setattr(fetcher, "fetch_reviews", lambda ref: [])
    monkeypatch.setattr(fetcher, "fetch_issue_comments", lambda ref: [])
    monkeypatch.setattr(fetcher, "build_unresolved_threads", lambda *a, **k: full_result["unresolved_review_threads"])
    monkeypatch.setattr(fetcher, "build_outstanding_reviews", lambda *a, **k: [])
    monkeypatch.setattr(fetcher, "build_conversation_comments", lambda *a, **k: [])
    monkeypatch.setattr(
        fetcher,
        "_apply_minimal",
        lambda result: {
            "mode": "minimal",
            "threads": [{"thread_id": thread["thread_id"]} for thread in result["unresolved_review_threads"]],
        },
    )

    fetcher.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload == {"mode": "minimal", "threads": [{"thread_id": "T1"}]}


def test_fetch_main_text_format_uses_text_renderer(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    args = Namespace(
        repo=None,
        pr=None,
        url="https://github.com/octo/repo/pull/17",
        exclude_bots=False,
        include_all=True,
        filter_path=None,
        threads_only=False,
        output=None,
        minimal=False,
        format="text",
    )
    pr_ref = fetcher.PullRequestRef("octo", "repo", 17)

    monkeypatch.setattr(fetcher.argparse.ArgumentParser, "parse_args", lambda self: args)
    monkeypatch.setattr(fetcher, "ensure_gh_auth", lambda: None)
    monkeypatch.setattr(fetcher, "resolve_pr_ref", lambda parsed_args: pr_ref)
    monkeypatch.setattr(
        fetcher,
        "fetch_pr_meta_and_threads",
        lambda ref: (
            {
                "number": 17,
                "url": "https://github.com/octo/repo/pull/17",
                "title": "Fix parser edge case",
                "state": "OPEN",
                "author": "luandro",
            },
            [],
        ),
    )
    monkeypatch.setattr(fetcher, "fetch_reviews", lambda ref: [])
    monkeypatch.setattr(fetcher, "fetch_issue_comments", lambda ref: [])
    monkeypatch.setattr(fetcher, "build_unresolved_threads", lambda *a, **k: [])
    monkeypatch.setattr(fetcher, "build_outstanding_reviews", lambda *a, **k: [])
    monkeypatch.setattr(fetcher, "build_conversation_comments", lambda *a, **k: [])
    monkeypatch.setattr(fetcher, "_format_text", lambda result: "TEXT OUTPUT\n")

    fetcher.main()

    captured = capsys.readouterr()
    assert captured.out == "TEXT OUTPUT\n"
    assert captured.err == ""


def test_fetch_main_minimal_text_warns_and_does_not_apply_minimal(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    args = Namespace(
        repo=None,
        pr=None,
        url="https://github.com/octo/repo/pull/17",
        exclude_bots=False,
        include_all=False,
        filter_path=None,
        threads_only=False,
        output=None,
        minimal=True,
        format="text",
    )
    pr_ref = fetcher.PullRequestRef("octo", "repo", 17)

    monkeypatch.setattr(fetcher.argparse.ArgumentParser, "parse_args", lambda self: args)
    monkeypatch.setattr(fetcher, "ensure_gh_auth", lambda: None)
    monkeypatch.setattr(fetcher, "resolve_pr_ref", lambda parsed_args: pr_ref)
    monkeypatch.setattr(
        fetcher,
        "fetch_pr_meta_and_threads",
        lambda ref: (
            {
                "number": 17,
                "url": "https://github.com/octo/repo/pull/17",
                "title": "Fix parser edge case",
                "state": "OPEN",
                "author": "luandro",
            },
            [],
        ),
    )
    monkeypatch.setattr(fetcher, "fetch_reviews", lambda ref: [])
    monkeypatch.setattr(fetcher, "fetch_issue_comments", lambda ref: [])
    monkeypatch.setattr(fetcher, "build_unresolved_threads", lambda *a, **k: [])
    monkeypatch.setattr(fetcher, "build_outstanding_reviews", lambda *a, **k: [])
    monkeypatch.setattr(fetcher, "build_conversation_comments", lambda *a, **k: [])
    monkeypatch.setattr(
        fetcher,
        "_apply_minimal",
        lambda result: pytest.fail("_apply_minimal should not be called in text mode"),
    )
    monkeypatch.setattr(fetcher, "_format_text", lambda result: "TEXT OUTPUT\n")

    fetcher.main()

    captured = capsys.readouterr()
    assert captured.out == "TEXT OUTPUT\n"
    assert "Warning: --minimal is ignored when --format text is used" in captured.err


def test_fetch_main_include_all_flag_does_not_override_exclude_bots_current_behavior(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    args = Namespace(
        repo=None,
        pr=None,
        url="https://github.com/octo/repo/pull/17",
        exclude_bots=True,
        include_all=True,
        filter_path=None,
        threads_only=False,
        output=None,
        minimal=False,
        format="json",
    )
    pr_ref = fetcher.PullRequestRef("octo", "repo", 17)

    monkeypatch.setattr(fetcher.argparse.ArgumentParser, "parse_args", lambda self: args)
    monkeypatch.setattr(fetcher, "ensure_gh_auth", lambda: None)
    monkeypatch.setattr(fetcher, "resolve_pr_ref", lambda parsed_args: pr_ref)
    monkeypatch.setattr(
        fetcher,
        "fetch_pr_meta_and_threads",
        lambda ref: (
            {
                "number": 17,
                "url": "https://github.com/octo/repo/pull/17",
                "title": "Fix parser edge case",
                "state": "OPEN",
                "author": "luandro",
            },
            ["thread-node"],
        ),
    )
    monkeypatch.setattr(fetcher, "fetch_reviews", lambda ref: [])
    monkeypatch.setattr(fetcher, "fetch_issue_comments", lambda ref: [])

    include_all_flags: list[bool] = []

    def fake_build_unresolved_threads(review_threads, pr_author, include_all, filter_path):
        include_all_flags.append(include_all)
        return []

    monkeypatch.setattr(fetcher, "build_unresolved_threads", fake_build_unresolved_threads)
    monkeypatch.setattr(fetcher, "build_outstanding_reviews", lambda *a, **k: [])
    monkeypatch.setattr(fetcher, "build_conversation_comments", lambda *a, **k: [])

    fetcher.main()

    payload = json.loads(capsys.readouterr().out)
    assert include_all_flags == [False]
    assert payload["summary"]["total_attention_items"] == 0


def test_fetch_main_defaults_to_json_output_when_format_is_omitted(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    pr_ref = fetcher.PullRequestRef("octo", "repo", 17)

    monkeypatch.setattr(
        fetcher.sys,
        "argv",
        ["fetch_unresolved_pr_feedback.py", "--url", "https://github.com/octo/repo/pull/17"],
    )
    monkeypatch.setattr(fetcher, "ensure_gh_auth", lambda: None)
    monkeypatch.setattr(fetcher, "resolve_pr_ref", lambda parsed_args: pr_ref)
    monkeypatch.setattr(
        fetcher,
        "fetch_pr_meta_and_threads",
        lambda ref: (
            {
                "number": 17,
                "url": "https://github.com/octo/repo/pull/17",
                "title": "Fix parser edge case",
                "state": "OPEN",
                "author": "luandro",
            },
            [],
        ),
    )
    monkeypatch.setattr(fetcher, "fetch_reviews", lambda ref: [])
    monkeypatch.setattr(fetcher, "fetch_issue_comments", lambda ref: [])
    monkeypatch.setattr(fetcher, "build_unresolved_threads", lambda *a, **k: [])
    monkeypatch.setattr(fetcher, "build_outstanding_reviews", lambda *a, **k: [])
    monkeypatch.setattr(fetcher, "build_conversation_comments", lambda *a, **k: [])
    monkeypatch.setattr(
        fetcher,
        "_format_text",
        lambda result: pytest.fail("_format_text should not be used for default JSON mode"),
    )

    fetcher.main()

    payload = json.loads(capsys.readouterr().out)
    assert payload["pull_request"]["number"] == 17
    assert payload["summary"]["total_attention_items"] == 0
