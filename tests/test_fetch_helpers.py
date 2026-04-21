from __future__ import annotations

import argparse

import pytest

import fetch_unresolved_pr_feedback as fetcher


def test_fetcher_resolve_pr_ref_prefers_explicit_url() -> None:
    args = argparse.Namespace(
        url="https://github.com/octo/repo/pull/7",
        repo=None,
        pr=None,
    )

    assert fetcher.resolve_pr_ref(args) == fetcher.PullRequestRef("octo", "repo", 7)


def test_fetcher_resolve_pr_ref_uses_explicit_repo_and_pr() -> None:
    args = argparse.Namespace(url=None, repo="octo/repo", pr=8)

    assert fetcher.resolve_pr_ref(args) == fetcher.PullRequestRef("octo", "repo", 8)


def test_fetcher_resolve_pr_ref_uses_repo_view_when_only_pr_is_given(monkeypatch) -> None:
    monkeypatch.setattr(fetcher, "run", lambda *args, **kwargs: "octo/repo")

    pr_ref = fetcher.resolve_pr_ref(argparse.Namespace(url=None, repo=None, pr=9))

    assert pr_ref == fetcher.PullRequestRef("octo", "repo", 9)


def test_fetcher_resolve_pr_ref_uses_current_branch_pr_when_no_args(monkeypatch) -> None:
    def fake_run_json(cmd, stdin=None):
        assert cmd == ["gh", "pr", "view", "--json", "number,url"]
        return {"number": 42, "url": "https://github.com/octo/repo/pull/42"}

    monkeypatch.setattr(fetcher, "run_json", fake_run_json)

    pr_ref = fetcher.resolve_pr_ref(argparse.Namespace(url=None, repo=None, pr=None))

    assert pr_ref == fetcher.PullRequestRef("octo", "repo", 42)


def test_fetcher_resolve_pr_ref_raises_when_current_branch_pr_cannot_be_determined(
    monkeypatch,
) -> None:
    monkeypatch.setattr(fetcher, "run_json", lambda *args, **kwargs: {})

    args = argparse.Namespace(url=None, repo=None, pr=None)
    with pytest.raises(RuntimeError, match="Could not determine the PR for the current branch"):
        fetcher.resolve_pr_ref(args)
