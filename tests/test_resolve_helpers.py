from __future__ import annotations

import subprocess
from argparse import Namespace

import pytest

import resolve_pr_feedback as resolver


def make_completed_process(
    *,
    stdout: str = "",
    stderr: str = "",
    returncode: int = 0,
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=["gh"], returncode=returncode, stdout=stdout, stderr=stderr)


def test_run_dry_run_returns_encoded_command() -> None:
    result = resolver.run(["gh", "api", "graphql"], dry_run=True)

    assert '"dry_run": true' in result
    assert '"gh"' in result


def test_run_sets_no_color_and_strips_ansi(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_subprocess_run(cmd, **kwargs):
        assert cmd == ["gh", "api", "graphql"]
        assert kwargs.get("input") == "query"
        assert kwargs.get("text") is True
        assert kwargs.get("capture_output") is True
        env = kwargs.get("env")
        assert env is not None
        assert env["NO_COLOR"] == "1"
        assert env["GH_CONFIG_PREFS_NO_COLOR"] == "true"
        return make_completed_process(stdout="\x1b[32m{\"ok\": true}\x1b[0m\n")

    monkeypatch.setattr(resolver.subprocess, "run", fake_subprocess_run)

    assert resolver.run(["gh", "api", "graphql"], stdin="query") == '{"ok": true}\n'


def test_run_raises_on_subprocess_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_subprocess_run(cmd, **kwargs):
        return make_completed_process(
            stderr="\x1b[31mauth failed\x1b[0m",
            returncode=1,
        )

    monkeypatch.setattr(resolver.subprocess, "run", fake_subprocess_run)

    with pytest.raises(RuntimeError, match=r"Command failed \(1\): gh auth status\nauth failed"):
        resolver.run(["gh", "auth", "status"])


def test_run_json_parses_valid_json(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(resolver, "run", lambda *args, **kwargs: '{"ok": true}')

    assert resolver.run_json(["gh", "api", "graphql"]) == {"ok": True}


@pytest.mark.parametrize("stdout", ["", "   \n\t"])
def test_run_json_rejects_empty_or_whitespace_output(
    monkeypatch: pytest.MonkeyPatch,
    stdout: str,
) -> None:
    monkeypatch.setattr(resolver, "run", lambda *args, **kwargs: stdout)

    with pytest.raises(RuntimeError, match=r"Empty output from gh api graphql; expected JSON"):
        resolver.run_json(["gh", "api", "graphql"])


def test_run_json_reports_preview_for_non_json_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(resolver, "run", lambda *args, **kwargs: "\x1b[31mnot-json\x1b[0m")

    with pytest.raises(RuntimeError, match=r"Failed to parse JSON from gh api graphql"):
        resolver.run_json(["gh", "api", "graphql"])


def test_resolve_pr_ref_prefers_explicit_url() -> None:
    args = Namespace(url="https://github.com/octo/repo/pull/7", repo=None, pr=None)

    assert resolver.resolve_pr_ref(args, {}, dry_run=False) == resolver.PullRequestRef("octo", "repo", 7)


def test_resolve_pr_ref_uses_item_pull_request() -> None:
    args = Namespace(url=None, repo=None, pr=None)
    item = {"pull_request": {"owner": "octo", "repo": "repo", "number": 8}}

    assert resolver.resolve_pr_ref(args, item, dry_run=False) == resolver.PullRequestRef("octo", "repo", 8)


def test_resolve_pr_ref_uses_explicit_repo_and_pr() -> None:
    args = Namespace(url=None, repo="octo/repo", pr=9)

    assert resolver.resolve_pr_ref(args, {}, dry_run=False) == resolver.PullRequestRef("octo", "repo", 9)


def test_resolve_pr_ref_allows_repo_without_pr() -> None:
    args = Namespace(url=None, repo="octo/repo", pr=None)

    assert resolver.resolve_pr_ref(args, {}, dry_run=False) == resolver.PullRequestRef("octo", "repo", None)


def test_resolve_pr_ref_uses_repo_view_when_only_pr_is_given(monkeypatch: pytest.MonkeyPatch) -> None:
    args = Namespace(url=None, repo=None, pr=10)
    monkeypatch.setattr(resolver, "run", lambda *args, **kwargs: "octo/repo")

    assert resolver.resolve_pr_ref(args, {}, dry_run=False) == resolver.PullRequestRef("octo", "repo", 10)


def test_resolve_pr_ref_dry_run_with_only_pr_returns_placeholder_repo() -> None:
    args = Namespace(url=None, repo=None, pr=10)

    assert resolver.resolve_pr_ref(args, {}, dry_run=True) == resolver.PullRequestRef("OWNER", "REPO", 10)


def test_resolve_pr_ref_uses_current_branch_pr_when_repo_and_pr_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = Namespace(url=None, repo=None, pr=None)

    def fake_run_json(cmd, stdin=None, dry_run=False):
        assert cmd == ["gh", "pr", "view", "--json", "number,url"]
        assert stdin is None
        assert dry_run is False
        return {
            "number": 179,
            "url": "https://github.com/acme/widgets/pull/179",
        }

    monkeypatch.setattr(resolver, "run_json", fake_run_json)
    monkeypatch.setattr(resolver, "run", lambda *a, **k: pytest.fail("run() should not be called"))

    ref = resolver.resolve_pr_ref(args, {}, dry_run=False)

    assert ref == resolver.PullRequestRef(owner="acme", repo="widgets", number=179)


def test_resolve_pr_ref_uses_current_branch_pr_from_github_enterprise_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args = Namespace(url=None, repo=None, pr=None)

    def fake_run_json(cmd, stdin=None, dry_run=False):
        assert cmd == ["gh", "pr", "view", "--json", "number,url"]
        assert stdin is None
        assert dry_run is False
        return {
            "number": 179,
            "url": "https://github.example.com/acme/widgets/pull/179",
        }

    monkeypatch.setattr(resolver, "run_json", fake_run_json)
    monkeypatch.setattr(resolver, "run", lambda *a, **k: pytest.fail("run() should not be called"))

    ref = resolver.resolve_pr_ref(args, {}, dry_run=False)

    assert ref == resolver.PullRequestRef(owner="acme", repo="widgets", number=179)


def test_resolve_pr_ref_dry_run_without_repo_or_pr_returns_placeholder_pr() -> None:
    args = Namespace(url=None, repo=None, pr=None)

    ref = resolver.resolve_pr_ref(args, {}, dry_run=True)

    assert ref == resolver.PullRequestRef(owner="OWNER", repo="REPO", number=1)


def test_root_comment_id_for_thread_wraps_json_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run_json(*args, **kwargs):
        raise RuntimeError("Failed to parse JSON from gh api graphql -F query=@- -F threadId=T123")

    monkeypatch.setattr(resolver, "run_json", fake_run_json)

    with pytest.raises(RuntimeError) as excinfo:
        resolver.root_comment_id_for_thread("T123", dry_run=False)

    message = str(excinfo.value)
    assert "Failed to load review thread 'T123'" in message
    assert "--comment-id" in message
