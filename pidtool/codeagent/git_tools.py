from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

GIT_TIMEOUT = 30.0


def _creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def run_git(args: list[str], cwd: Path, timeout: float = GIT_TIMEOUT) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=_creation_flags(),
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 128, f"git 执行失败: {exc}"
    out = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode, out


def is_repo(path: Path) -> bool:
    code, out = run_git(["rev-parse", "--is-inside-work-tree"], Path(path))
    return code == 0 and out.strip() == "true"


def has_head(path: Path) -> bool:
    code, _ = run_git(["rev-parse", "--verify", "HEAD"], Path(path))
    return code == 0


def head_short(path: Path) -> str:
    code, out = run_git(["rev-parse", "--short", "HEAD"], Path(path))
    return out.strip() if code == 0 else ""


def status_entries(path: Path) -> list[tuple[str, str]]:
    code, out = run_git(["status", "--porcelain", "-z"], Path(path))
    if code != 0:
        return []
    entries: list[tuple[str, str]] = []
    for part in out.split("\0"):
        if len(part) < 4:
            continue
        status = part[:2]
        rel = part[3:]
        if status == "!!":
            continue
        entries.append((status, rel))
    return entries


def tracked_changes(path: Path) -> list[tuple[str, str]]:
    return [(status, rel) for status, rel in status_entries(path) if status != "??"]


def untracked_files(path: Path) -> set[str]:
    return {rel for status, rel in status_entries(path) if status == "??"}


def diff_text(path: Path, max_chars: int = 400_000) -> str:
    code, out = run_git(["diff", "HEAD", "--"], Path(path))
    if code not in (0, 1):
        return out
    return out[:max_chars]


def diff_stat(path: Path) -> str:
    code, out = run_git(["diff", "HEAD", "--stat"], Path(path))
    return out.strip() if code in (0, 1) else out.strip()


def changed_files(path: Path) -> list[str]:
    code, out = run_git(["diff", "HEAD", "--name-only"], Path(path))
    if code not in (0, 1):
        return []
    return [line for line in out.splitlines() if line.strip()]


def revert(workdir: Path, before_untracked: set[str]) -> list[str]:
    workdir = Path(workdir)
    staged_new = [rel for status, rel in status_entries(workdir) if status.startswith("A")]
    code, _ = run_git(["reset", "--hard", "HEAD"], workdir)
    if code != 0:
        run_git(["checkout", "--", "."], workdir)
    removed: list[str] = []
    root = workdir.resolve()
    candidates = set(staged_new) | (untracked_files(workdir) - set(before_untracked))
    for rel in sorted(candidates):
        target = (workdir / rel).resolve()
        if target != root and root not in target.parents:
            continue
        try:
            if target.is_dir():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()
        except OSError:
            continue
        removed.append(rel)
    return removed
