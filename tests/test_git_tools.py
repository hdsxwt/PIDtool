from __future__ import annotations

from pathlib import Path

import pytest

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


def test_run_git_reports_failure(tmp_path):
    missing = tmp_path / "does-not-exist"
    code, out = git_tools.run_git(["status"], missing)
    assert code == 128
    assert "git 执行失败" in out


def test_helpers_on_non_repo(tmp_path):
    assert git_tools.head_short(tmp_path) == ""
    assert git_tools.status_entries(tmp_path) == []
    assert git_tools.tracked_changes(tmp_path) == []
    assert git_tools.untracked_files(tmp_path) == set()
    assert git_tools.changed_files(tmp_path) == []


def test_head_short_on_repo(tmp_path):
    repo = _make_repo(tmp_path)
    head = git_tools.head_short(repo)
    assert len(head) >= 7
    assert git_tools.status_entries(repo) == []


def test_status_entries_skips_ignored(monkeypatch, tmp_path):
    fake = "!! .pidtool/\0?? new.c\0"
    monkeypatch.setattr(git_tools, "run_git", lambda *a, **k: (0, fake))
    assert git_tools.status_entries(tmp_path) == [("??", "new.c")]
    assert git_tools.untracked_files(tmp_path) == {"new.c"}


def test_status_entries_ignores_short_parts(monkeypatch, tmp_path):
    monkeypatch.setattr(git_tools, "run_git", lambda *a, **k: (0, "ab\0M  pid.c\0"))
    assert git_tools.status_entries(tmp_path) == [("M ", "pid.c")]


def test_diff_text_truncates(tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "pid.c").write_text("float kp = 123456789.0f;\n", encoding="utf-8")
    assert len(git_tools.diff_text(repo, max_chars=10)) == 10


def test_diff_text_and_changed_files_errors(monkeypatch, tmp_path):
    monkeypatch.setattr(git_tools, "run_git", lambda *a, **k: (128, "fatal: not a repo"))
    assert git_tools.diff_text(tmp_path) == "fatal: not a repo"
    assert git_tools.changed_files(tmp_path) == []
    assert git_tools.diff_stat(tmp_path) == "fatal: not a repo"


def test_revert_removes_new_directory(tmp_path):
    repo = _make_repo(tmp_path)
    before = git_tools.untracked_files(repo)
    new_dir = repo / "generated"
    new_dir.mkdir()
    (new_dir / "a.c").write_text("int a;\n", encoding="utf-8")
    (new_dir / "b.c").write_text("int b;\n", encoding="utf-8")
    removed = git_tools.revert(repo, before)
    assert removed == ["generated/"]
    assert not new_dir.exists()


def test_revert_skips_paths_outside_workdir(monkeypatch, tmp_path):
    repo = _make_repo(tmp_path)
    outside = tmp_path / "outside.c"
    outside.write_text("int x;\n", encoding="utf-8")
    monkeypatch.setattr(git_tools, "untracked_files", lambda path: {"../outside.c"})
    removed = git_tools.revert(repo, set())
    assert removed == []
    assert outside.exists()


def test_revert_ignores_unlink_errors(monkeypatch, tmp_path):
    repo = _make_repo(tmp_path)
    (repo / "stuck.c").write_text("int x;\n", encoding="utf-8")

    def broken_unlink(self, *args, **kwargs):
        raise OSError("locked")

    monkeypatch.setattr(Path, "unlink", broken_unlink)
    removed = git_tools.revert(repo, set())
    assert removed == []
