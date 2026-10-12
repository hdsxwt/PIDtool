from __future__ import annotations

from pathlib import Path

from pidtool.codeagent import git_tools


def _make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git_tools.run_git(["init"], repo)
    (repo / "pid.c").write_text("float kp = 1.0f;\n", encoding="utf-8")
    git_tools.run_git(["add", "."], repo)
    git_tools.run_git(
        ["-c", "user.name=test", "-c", "user.email=test@test", "commit", "-m", "init"], repo
    )
    return repo


def test_is_repo(tmp_path):
    assert not git_tools.is_repo(tmp_path)
    repo = _make_repo(tmp_path)
    assert git_tools.is_repo(repo)


def test_clean_then_modified(tmp_path):
    repo = _make_repo(tmp_path)
    assert git_tools.tracked_changes(repo) == []
    (repo / "pid.c").write_text("float kp = 2.0f;\n", encoding="utf-8")
    changes = git_tools.tracked_changes(repo)
    assert len(changes) == 1
    assert changes[0][1] == "pid.c"
    assert "kp = 2.0f" in git_tools.diff_text(repo)
    assert "pid.c" in git_tools.diff_stat(repo)
    assert git_tools.changed_files(repo) == ["pid.c"]


def test_untracked_detection(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "notes.txt").write_text("keep me", encoding="utf-8")
    assert git_tools.untracked_files(repo) == {"notes.txt"}
    assert git_tools.tracked_changes(repo) == []


def test_revert_restores_tracked_and_removes_new(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "notes.txt").write_text("keep me", encoding="utf-8")
    before = git_tools.untracked_files(repo)
    (repo / "pid.c").write_text("float kp = 9.0f;\n", encoding="utf-8")
    (repo / "new.c").write_text("int x;\n", encoding="utf-8")
    removed = git_tools.revert(repo, before)
    assert removed == ["new.c"]
    assert (repo / "pid.c").read_text(encoding="utf-8") == "float kp = 1.0f;\n"
    assert (repo / "notes.txt").exists()
    assert git_tools.tracked_changes(repo) == []
    assert git_tools.untracked_files(repo) == {"notes.txt"}
