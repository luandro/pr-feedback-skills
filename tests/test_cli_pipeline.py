from __future__ import annotations

import json
from argparse import Namespace
from pathlib import Path

import pytest

import apply_pr_feedback_actions as batch
import build_actions
import fetch_unresolved_pr_feedback as fetcher


def _write_feedback_via_fetch_main(
    monkeypatch: pytest.MonkeyPatch,
    output_path: Path,
    *,
    minimal: bool = False,
) -> None:
    args = Namespace(
        repo=None,
        pr=None,
        url="https://github.com/octo/repo/pull/17",
        exclude_bots=False,
        include_all=False,
        filter_path=None,
        threads_only=False,
        output=str(output_path),
        minimal=minimal,
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
    unresolved_threads = [
        {
            "thread_id": "T1",
            "root_comment_id": 42,
            "path": "src/app.py",
            "line": 17,
            "original_line": 17,
            "diff_side": "RIGHT",
            "is_outdated": False,
            "comments": [
                {
                    "id": "C1",
                    "database_id": 42,
                    "url": "https://github.com/octo/repo/pull/17#discussion_r42",
                    "author": "reviewer1",
                    "body": "Please add a test.",
                    "created_at": "2026-04-21T00:00:00Z",
                    "updated_at": "2026-04-21T00:00:00Z",
                    "excluded_from_attention": False,
                }
            ],
        }
    ]
    conversation_comments = [
        {
            "id": 91,
            "url": "https://github.com/octo/repo/pull/17#issuecomment-91",
            "author": "teammate",
            "created_at": "2026-04-21T00:01:00Z",
            "updated_at": "2026-04-21T00:01:00Z",
            "body": "Can you add a regression test?",
        }
    ]

    monkeypatch.setattr(fetcher.argparse.ArgumentParser, "parse_args", lambda self: args)
    monkeypatch.setattr(fetcher, "ensure_gh_auth", lambda: None)
    monkeypatch.setattr(fetcher, "resolve_pr_ref", lambda parsed_args: pr_ref)
    monkeypatch.setattr(fetcher, "fetch_pr_meta_and_threads", lambda ref: (meta, ["raw-thread"]))
    monkeypatch.setattr(fetcher, "fetch_reviews", lambda ref: [])
    monkeypatch.setattr(fetcher, "fetch_issue_comments", lambda ref: ["raw-comment"])
    monkeypatch.setattr(fetcher, "build_unresolved_threads", lambda *args, **kwargs: unresolved_threads)
    monkeypatch.setattr(fetcher, "build_outstanding_reviews", lambda *args, **kwargs: [])
    monkeypatch.setattr(fetcher, "build_conversation_comments", lambda *args, **kwargs: conversation_comments)

    fetcher.main()


def test_pipeline_fetch_build_actions_and_format_plan(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    feedback_path = tmp_path / "feedback.json"
    actions_path = tmp_path / "actions.json"

    _write_feedback_via_fetch_main(monkeypatch, feedback_path)

    build_args = Namespace(
        file=str(feedback_path),
        output=str(actions_path),
        decision="addressed",
        summary="Fixed in abc1234",
        drop_context=False,
        validate=False,
    )
    monkeypatch.setattr(build_actions.argparse.ArgumentParser, "parse_args", lambda self: build_args)

    build_actions.main()

    actions = json.loads(actions_path.read_text(encoding="utf-8"))
    plan = batch.format_plan(actions)

    assert [action["kind"] for action in actions] == ["review_thread", "issue_comment"]
    assert actions[0]["_context"] == "src/app.py:17"
    assert actions[1]["_context"] == "@teammate"
    assert "reply+resolve ('Fixed in abc1234') @ thread T1 [src/app.py:17]" in plan
    assert "+1 @ issue_comment 91 [@teammate]" in plan


def test_pipeline_fetch_build_actions_and_batch_dry_run_round_trip_files_and_streams(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    feedback_path = tmp_path / "feedback.json"
    actions_path = tmp_path / "actions.json"

    _write_feedback_via_fetch_main(monkeypatch, feedback_path)
    capsys.readouterr()

    build_args = Namespace(
        file=str(feedback_path),
        output=str(actions_path),
        decision="addressed",
        summary="Fixed in abc1234",
        drop_context=False,
        validate=False,
    )
    monkeypatch.setattr(build_actions.argparse.ArgumentParser, "parse_args", lambda self: build_args)
    build_actions.main()
    build_capture = capsys.readouterr()

    batch_args = Namespace(
        file=str(actions_path),
        dry_run=True,
        plan=False,
        verbose=True,
        exit_code=False,
    )
    monkeypatch.setattr(batch.argparse.ArgumentParser, "parse_args", lambda self: batch_args)

    batch.main()

    batch_capture = capsys.readouterr()
    payload = json.loads(batch_capture.out)

    assert build_capture.out == ""
    assert build_capture.err == ""
    assert payload["count"] == 2
    assert all(item["ok"] for item in payload["results"])
    assert payload["results"][0]["result"]["dry_run"] is True
    assert payload["results"][0]["result"]["action_taken"] == "replied_and_resolved"
    assert payload["results"][1]["result"]["action_taken"] == "reacted"
    assert "[batch 1/2] kind=review_thread decision=addressed" in batch_capture.err
    assert "[batch 2/2] kind=issue_comment decision=addressed" in batch_capture.err


def test_minimal_json_build_actions_warns_without_summary_and_passes_with_summary(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    minimal_feedback_path = tmp_path / "minimal-feedback.json"
    warned_actions_path = tmp_path / "warned-actions.json"
    clean_actions_path = tmp_path / "clean-actions.json"

    _write_feedback_via_fetch_main(monkeypatch, minimal_feedback_path, minimal=True)
    capsys.readouterr()

    warning_args = Namespace(
        file=str(minimal_feedback_path),
        output=str(warned_actions_path),
        decision="addressed",
        summary="",
        drop_context=False,
        validate=True,
    )
    monkeypatch.setattr(build_actions.argparse.ArgumentParser, "parse_args", lambda self: warning_args)

    with pytest.raises(SystemExit) as excinfo:
        build_actions.main()

    assert excinfo.value.code == 1
    warned_actions = json.loads(warned_actions_path.read_text(encoding="utf-8"))
    warning_capture = capsys.readouterr()
    assert warned_actions[0]["kind"] == "review_thread"
    assert warned_actions[0]["comment_id"] == 42
    assert warned_actions[1]["kind"] == "issue_comment"
    assert "review_thread addressed without summary" in warning_capture.err
    assert warning_capture.out == ""

    clean_args = Namespace(
        file=str(minimal_feedback_path),
        output=str(clean_actions_path),
        decision="addressed",
        summary="Fixed in abc1234",
        drop_context=False,
        validate=True,
    )
    monkeypatch.setattr(build_actions.argparse.ArgumentParser, "parse_args", lambda self: clean_args)

    build_actions.main()

    clean_capture = capsys.readouterr()
    clean_actions = json.loads(clean_actions_path.read_text(encoding="utf-8"))
    assert clean_capture.out == ""
    assert clean_capture.err == ""
    assert clean_actions[0]["summary"] == "Fixed in abc1234"
    assert clean_actions[0]["_context"] == "src/app.py:17"
