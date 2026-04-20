from __future__ import annotations

import json
from argparse import Namespace

import pytest

import build_actions
import resolve_pr_feedback as resolver


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
