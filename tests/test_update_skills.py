from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _require_rsync() -> None:
    if shutil.which("rsync") is None:
        pytest.skip("rsync is not available in this environment")


def _stage_repo_copy(tmp_path: Path) -> Path:
    staged = tmp_path / "repo"
    staged.mkdir()
    shutil.copy2(ROOT / "update-skills.sh", staged / "update-skills.sh")
    for skill in ("fetch-pr-unresolved-feedback", "resolve-pr-feedback"):
        shutil.copytree(ROOT / skill, staged / skill)
    (staged / "update-skills.sh").chmod(0o755)
    return staged


def _run_update_skills(repo_dir: Path, home_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "HOME": str(home_dir)}
    try:
        return subprocess.run(
            ["bash", str(repo_dir / "update-skills.sh"), *args],
            cwd=repo_dir,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(
            args=["bash", str(repo_dir / "update-skills.sh"), *args],
            returncode=-1,
            stdout="",
            stderr="subprocess timed out after 30 s",
        )


def test_update_skills_dry_run_prints_header_and_does_not_mutate_targets(tmp_path: Path) -> None:
    _require_rsync()
    repo_dir = _stage_repo_copy(tmp_path)
    home_dir = tmp_path / "home"
    target_base = home_dir / ".codex" / "skills"
    target_dir = target_base / "fetch-pr-unresolved-feedback"
    target_dir.mkdir(parents=True)
    sentinel = target_dir / "stale.txt"
    sentinel.write_text("leave me alone during dry run", encoding="utf-8")

    result = _run_update_skills(repo_dir, home_dir, "--dry-run")

    assert result.returncode == 0
    assert "[DRY RUN] No changes will be made." in result.stdout
    assert "Syncing: fetch-pr-unresolved-feedback" in result.stdout
    assert "Updated:" not in result.stdout
    assert sentinel.read_text(encoding="utf-8") == "leave me alone during dry run"


def test_update_skills_rejects_unknown_args_without_running_sync(tmp_path: Path) -> None:
    repo_dir = _stage_repo_copy(tmp_path)
    home_dir = tmp_path / "home"
    target_base = home_dir / ".codex" / "skills"
    target_dir = target_base / "fetch-pr-unresolved-feedback"
    target_dir.mkdir(parents=True)
    sentinel = target_dir / "stale.txt"
    sentinel.write_text("must survive invalid args", encoding="utf-8")

    result = _run_update_skills(repo_dir, home_dir, "--dryrun")

    assert result.returncode != 0
    assert "[ERROR] Unknown argument: --dryrun" in result.stderr
    assert "Usage: ./update-skills.sh [--dry-run]" in result.stderr
    assert "Syncing:" not in result.stdout
    assert sentinel.read_text(encoding="utf-8") == "must survive invalid args"


def test_update_skills_syncs_into_temp_home_deletes_stale_files_and_excludes_pyc(tmp_path: Path) -> None:
    _require_rsync()
    repo_dir = _stage_repo_copy(tmp_path)
    home_dir = tmp_path / "home"

    for base in (
        home_dir / ".claude" / "skills",
        home_dir / ".codex" / "skills",
        home_dir / "forge" / "skills",
    ):
        base.mkdir(parents=True)

    stale_file = home_dir / ".codex" / "skills" / "fetch-pr-unresolved-feedback" / "stale.txt"
    stale_file.parent.mkdir(parents=True)
    stale_file.write_text("old", encoding="utf-8")

    (repo_dir / "fetch-pr-unresolved-feedback" / "__pycache__").mkdir()
    (repo_dir / "fetch-pr-unresolved-feedback" / "__pycache__" / "junk.pyc").write_text(
        "compiled",
        encoding="utf-8",
    )
    (repo_dir / "resolve-pr-feedback" / "scripts" / "junk.pyc").write_text(
        "compiled",
        encoding="utf-8",
    )

    result = _run_update_skills(repo_dir, home_dir)

    assert result.returncode == 0
    assert not stale_file.exists()
    assert not (
        home_dir
        / ".codex"
        / "skills"
        / "fetch-pr-unresolved-feedback"
        / "__pycache__"
        / "junk.pyc"
    ).exists()
    assert not (
        home_dir
        / ".codex"
        / "skills"
        / "resolve-pr-feedback"
        / "scripts"
        / "junk.pyc"
    ).exists()
    assert "Updated: 6  Failed: 0  Skipped: 0" in result.stdout


def test_update_skills_reports_missing_targets_and_missing_sources_without_exiting_early(
    tmp_path: Path,
) -> None:
    _require_rsync()
    repo_dir = _stage_repo_copy(tmp_path)
    home_dir = tmp_path / "home"

    (home_dir / ".codex" / "skills").mkdir(parents=True)
    shutil.rmtree(repo_dir / "resolve-pr-feedback")

    result = _run_update_skills(repo_dir, home_dir)

    assert result.returncode == 1
    assert "[WARN] Target directory does not exist:" in result.stdout
    assert "[ERROR] Source skill not found:" in result.stdout
    assert "Updated: 1  Failed: 1  Skipped: 2" in result.stdout


def test_update_skills_uses_pr_feedback_targets_env_var(tmp_path: Path) -> None:
    _require_rsync()
    repo_dir = _stage_repo_copy(tmp_path)
    home_dir = tmp_path / "home"

    # Create a custom target directory
    custom_target = home_dir / "custom" / "skills"
    custom_target.mkdir(parents=True)

    env = {
        **os.environ,
        "HOME": str(home_dir),
        "PR_FEEDBACK_TARGETS": str(custom_target),
    }
    result = subprocess.run(
        ["bash", str(repo_dir / "update-skills.sh")],
        cwd=repo_dir,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0
    assert "Updated: 2  Failed: 0  Skipped: 0" in result.stdout
    # Verify files were actually synced
    assert (custom_target / "fetch-pr-unresolved-feedback" / "SKILL.md").exists()
    assert (custom_target / "resolve-pr-feedback" / "SKILL.md").exists()


def test_update_skills_uses_multiple_colon_separated_targets(tmp_path: Path) -> None:
    _require_rsync()
    repo_dir = _stage_repo_copy(tmp_path)
    home_dir = tmp_path / "home"

    target_a = home_dir / "agent_a" / "skills"
    target_b = home_dir / "agent_b" / "skills"
    target_a.mkdir(parents=True)
    target_b.mkdir(parents=True)

    env = {
        **os.environ,
        "HOME": str(home_dir),
        "PR_FEEDBACK_TARGETS": f"{target_a}:{target_b}",
    }
    result = subprocess.run(
        ["bash", str(repo_dir / "update-skills.sh")],
        cwd=repo_dir,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert result.returncode == 0
    assert "Updated: 4  Failed: 0  Skipped: 0" in result.stdout
    assert (target_a / "fetch-pr-unresolved-feedback" / "SKILL.md").exists()
    assert (target_b / "resolve-pr-feedback" / "SKILL.md").exists()
