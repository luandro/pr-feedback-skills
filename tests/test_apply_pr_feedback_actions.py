from __future__ import annotations

import io
import json
from argparse import Namespace

import pytest

import apply_pr_feedback_actions as batch


def test_load_actions_reads_json_array_from_file(tmp_path) -> None:
    path = tmp_path / "actions.json"
    path.write_text(
        json.dumps([{"kind": "issue_comment", "comment_id": 7, "decision": "addressed"}]),
        encoding="utf-8",
    )

    assert batch.load_actions(str(path)) == [
        {"kind": "issue_comment", "comment_id": 7, "decision": "addressed"}
    ]


def test_load_actions_reads_actions_wrapper_from_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        batch.sys,
        "stdin",
        io.StringIO('{"actions":[{"kind":"review_thread","thread_id":"T1","decision":"addressed"}]}'),
    )

    assert batch.load_actions(None) == [
        {"kind": "review_thread", "thread_id": "T1", "decision": "addressed"}
    ]


def test_load_actions_rejects_invalid_payload_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(batch.sys, "stdin", io.StringIO('{"kind":"issue_comment"}'))

    with pytest.raises(ValueError, match="Expected a JSON array or an object with an 'actions' array"):
        batch.load_actions(None)


def test_resolve_pr_ref_from_action_supports_url_pull_request_and_repo() -> None:
    assert batch.resolve_pr_ref_from_action(
        {"url": "https://github.com/octo/repo/pull/17#discussion_r1"},
        dry_run=False,
    ) == batch.PullRequestRef("octo", "repo", 17)

    assert batch.resolve_pr_ref_from_action(
        {"pull_request": {"owner": "acme", "repo": "widgets", "number": 19}},
        dry_run=False,
    ) == batch.PullRequestRef("acme", "widgets", 19)

    assert batch.resolve_pr_ref_from_action(
        {"repo": "coolabnet/community-box", "pr": None},
        dry_run=False,
    ) == batch.PullRequestRef("coolabnet", "community-box", None)


def test_resolve_pr_ref_from_action_rejects_actions_without_repo_sources() -> None:
    with pytest.raises(ValueError, match="has no repo/url/pull_request"):
        batch.resolve_pr_ref_from_action({"kind": "issue_comment"}, dry_run=False)


def test_format_plan_renders_kinds_verbs_context_and_breakdown() -> None:
    text = batch.format_plan(
        [
            {
                "kind": "review_thread",
                "decision": "addressed",
                "thread_id": "T1",
                "comment_id": 17,
                "summary": "Fixed in abc1234",
                "_context": "src/app.py:44",
            },
            {
                "kind": "review_thread",
                "decision": "addressed",
                "thread_id": "T2",
            },
            {
                "kind": "review_thread",
                "decision": "not_relevant",
                "thread_id": "T3",
                "comment_id": 0,
            },
            {
                "kind": "review_thread",
                "decision": "not_relevant",
                "thread_id": "T4",
            },
            {"kind": "review_comment", "decision": "addressed", "comment_id": 71},
            {"kind": "issue_comment", "decision": "not_relevant", "comment_id": 72},
            {"kind": "review", "decision": "addressed", "id": "R1"},
            {"kind": "note", "decision": "addressed"},
        ]
    )

    assert text.startswith("Plan")
    assert "review_thread/addressed=2" in text
    assert "review_thread/not_relevant=2" in text
    assert "review_comment/addressed=1" in text
    assert "issue_comment/not_relevant=1" in text
    assert "review/addressed=1" in text
    assert "note/addressed=1" in text
    assert "reply+resolve ('Fixed in abc1234') @ thread T1 [src/app.py:44]" in text
    assert "resolve (no reply) @ thread T2" in text
    assert "-1 reaction on root (comment_id=0), thread left unresolved @ thread T3" in text
    assert "-1 reaction on root (auto-fetch root), thread left unresolved @ thread T4" in text
    assert "+1 @ review_comment 71" in text
    assert "-1 @ issue_comment 72" in text
    assert "SKIP (top-level review body) @ review R1" in text
    assert "? unknown kind='note' @ ?" in text


def test_format_plan_handles_empty_action_lists() -> None:
    assert batch.format_plan([]) == "Plan \u2014 0 action(s):\n"


def test_batch_main_plan_mode_returns_early_and_skips_auth(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    args = Namespace(file=None, dry_run=False, plan=True, verbose=False, exit_code=False)

    monkeypatch.setattr(batch.argparse.ArgumentParser, "parse_args", lambda self: args)
    monkeypatch.setattr(batch, "load_actions", lambda file_path: [{"kind": "review"}])
    monkeypatch.setattr(batch, "format_plan", lambda actions: "Plan text")
    monkeypatch.setattr(
        batch,
        "ensure_gh_auth",
        lambda dry_run: pytest.fail("ensure_gh_auth should not be called in --plan mode"),
    )

    batch.main()

    captured = capsys.readouterr()
    assert captured.out == "Plan text\n"
    assert captured.err == ""


def test_batch_main_authenticates_once_processes_all_actions_and_supports_verbose_dry_run(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    args = Namespace(file=None, dry_run=True, plan=False, verbose=True, exit_code=False)
    actions = [
        {
            "repo": "octo/repo",
            "pr": 17,
            "kind": "issue_comment",
            "comment_id": 7,
            "decision": "addressed",
        },
        {
            "pull_request": {"owner": "octo", "repo": "repo", "number": 17},
            "kind": "review_thread",
            "thread_id": "PRRT_1",
            "comment_id": 8,
            "decision": "not_relevant",
        },
    ]
    auth_calls: list[bool] = []
    execute_calls: list[dict[str, object]] = []

    def fake_execute_action(**kwargs):
        execute_calls.append(kwargs)
        return {
            "repo": kwargs["pr_ref"].full_repo,
            "pr": kwargs["pr_ref"].number,
            "kind": kwargs["kind"],
            "decision": kwargs["decision"],
            "dry_run": kwargs["dry_run"],
        }

    monkeypatch.setattr(batch.argparse.ArgumentParser, "parse_args", lambda self: args)
    monkeypatch.setattr(batch, "load_actions", lambda file_path: actions)
    monkeypatch.setattr(batch, "ensure_gh_auth", lambda dry_run: auth_calls.append(dry_run))
    monkeypatch.setattr(batch, "execute_action", fake_execute_action)

    batch.main()

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert auth_calls == [True]
    assert [call["dry_run"] for call in execute_calls] == [True, True]
    assert [call["verbose"] for call in execute_calls] == [True, True]
    assert payload["count"] == 2
    assert all(item["ok"] for item in payload["results"])
    assert payload["results"][0]["result"]["repo"] == "octo/repo"
    assert payload["results"][1]["result"]["kind"] == "review_thread"
    assert "[batch 1/2] kind=issue_comment decision=addressed" in captured.err
    assert "[batch 2/2] kind=review_thread decision=not_relevant" in captured.err


def test_batch_main_continues_after_failures_and_exit_code_reports_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    args = Namespace(file=None, dry_run=False, plan=False, verbose=True, exit_code=True)
    actions = [
        {"repo": "octo/repo", "kind": "issue_comment", "comment_id": 7, "decision": "addressed"},
        {"repo": "octo/repo", "kind": "review_comment", "comment_id": 8, "decision": "not_relevant"},
    ]
    seen_kinds: list[str] = []

    def fake_execute_action(**kwargs):
        seen_kinds.append(kwargs["kind"])
        if kwargs["kind"] == "issue_comment":
            raise RuntimeError("boom")
        return {"kind": kwargs["kind"]}

    monkeypatch.setattr(batch.argparse.ArgumentParser, "parse_args", lambda self: args)
    monkeypatch.setattr(batch, "load_actions", lambda file_path: actions)
    monkeypatch.setattr(batch, "ensure_gh_auth", lambda dry_run: None)
    monkeypatch.setattr(batch, "execute_action", fake_execute_action)

    with pytest.raises(SystemExit) as excinfo:
        batch.main()

    assert excinfo.value.code == 1
    assert seen_kinds == ["issue_comment", "review_comment"]

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["count"] == 2
    assert payload["results"][0]["ok"] is False
    assert payload["results"][0]["error"] == "boom"
    assert payload["results"][1]["ok"] is True
    assert "[batch 1/2] FAILED: boom" in captured.err
    assert "[batch 2/2] kind=review_comment decision=not_relevant" in captured.err


def test_batch_main_treats_comment_id_zero_as_missing_and_falls_back_to_root_comment_id(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    args = Namespace(file=None, dry_run=False, plan=False, verbose=False, exit_code=False)
    comment_ids: list[int | None] = []

    def fake_execute_action(**kwargs):
        comment_ids.append(kwargs["comment_id"])
        return {"comment_id": kwargs["comment_id"]}

    monkeypatch.setattr(batch.argparse.ArgumentParser, "parse_args", lambda self: args)
    monkeypatch.setattr(
        batch,
        "load_actions",
        lambda file_path: [
            {
                "repo": "octo/repo",
                "kind": "review_thread",
                "thread_id": "PRRT_1",
                "comment_id": 0,
                "root_comment_id": 55,
                "decision": "not_relevant",
            }
        ],
    )
    monkeypatch.setattr(batch, "ensure_gh_auth", lambda dry_run: None)
    monkeypatch.setattr(batch, "execute_action", fake_execute_action)

    batch.main()

    payload = json.loads(capsys.readouterr().out)
    assert comment_ids == [55]
    assert payload["results"][0]["result"]["comment_id"] == 55


def test_batch_main_empty_actions_still_outputs_trailing_newline(
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    args = Namespace(file=None, dry_run=False, plan=False, verbose=False, exit_code=False)

    monkeypatch.setattr(batch.argparse.ArgumentParser, "parse_args", lambda self: args)
    monkeypatch.setattr(batch, "load_actions", lambda file_path: [])
    monkeypatch.setattr(batch, "ensure_gh_auth", lambda dry_run: None)

    batch.main()

    captured = capsys.readouterr()
    assert captured.out.endswith("\n")
    assert json.loads(captured.out) == {"count": 0, "results": []}
