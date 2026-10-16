from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Iterable

from .config import GroupCtlError


def git(root: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if check and proc.returncode != 0:
        msg = proc.stderr.strip() or proc.stdout.strip() or f"git {' '.join(args)} failed"
        raise GroupCtlError(msg)
    return proc.stdout.strip()


def ensure_git_repo(root: Path) -> None:
    if git(root, "rev-parse", "--is-inside-work-tree", check=False) != "true":
        raise GroupCtlError(f"{root} is not a Git working tree.")


def current_branch(root: Path) -> str:
    return git(root, "branch", "--show-current") or "(detached)"


def current_commit(root: Path) -> str:
    return git(root, "rev-parse", "HEAD")


def short_commit(root: Path, rev: str = "HEAD") -> str:
    return git(root, "rev-parse", "--short", rev)


def is_clean(root: Path) -> bool:
    return git(root, "status", "--porcelain") == ""


def porcelain(root: Path) -> str:
    return git(root, "status", "--porcelain")


def fetch(root: Path) -> None:
    git(root, "fetch", "--all", "--prune")


def ref_exists(root: Path, ref: str) -> bool:
    return git(root, "rev-parse", "--verify", ref, check=False) != ""


def canonical_remote_ref(root: Path, branch: str) -> str:
    remote = f"origin/{branch}"
    if ref_exists(root, remote):
        return remote
    if ref_exists(root, branch):
        return branch
    raise GroupCtlError(f"Canonical branch {branch!r} does not exist locally or at origin.")


def ahead_behind(root: Path, local: str, upstream: str) -> tuple[int, int]:
    out = git(root, "rev-list", "--left-right", "--count", f"{local}...{upstream}")
    left, right = out.split()
    return int(left), int(right)


def changed_files(root: Path, base: str | None = None) -> list[str]:
    if base:
        out = git(root, "diff", "--name-only", f"{base}...HEAD")
    else:
        out = git(root, "status", "--porcelain")
        files = []
        for line in out.splitlines():
            if len(line) >= 4:
                files.append(line[3:].strip())
        return files
    return [x for x in out.splitlines() if x.strip()]


def add_commit(root: Path, paths: Iterable[str], message: str) -> str:
    paths = list(paths)
    if not paths:
        raise GroupCtlError("No paths provided for commit.")
    git(root, "add", "--", *paths)
    if git(root, "diff", "--cached", "--quiet", check=False) == "":
        # git diff --quiet emits no output for both pass/fail, so check return code separately below.
        pass
    proc = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=root)
    if proc.returncode == 0:
        raise GroupCtlError("Nothing staged to commit.")
    git(root, "commit", "-m", message)
    return current_commit(root)


def push_current(root: Path) -> None:
    branch = current_branch(root)
    if branch == "(detached)":
        raise GroupCtlError("Cannot push from detached HEAD.")
    # Try ordinary push first; then establish upstream for a new personal branch.
    proc = subprocess.run(
        ["git", "push"], cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if proc.returncode != 0:
        git(root, "push", "-u", "origin", branch)


def merge(root: Path, commit: str, no_ff: bool = False) -> None:
    args = ["merge"]
    if no_ff:
        args.append("--no-ff")
    args.append(commit)
    git(root, *args)


def abort_merge(root: Path) -> None:
    git(root, "merge", "--abort", check=False)


def conflict_files(root: Path) -> list[str]:
    out = git(root, "diff", "--name-only", "--diff-filter=U")
    return [x for x in out.splitlines() if x.strip()]


def commit_is_ancestor(root: Path, ancestor: str, descendant: str = "HEAD") -> bool:
    proc = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant], cwd=root
    )
    return proc.returncode == 0


def grep_conflict_markers(root: Path) -> list[str]:
    proc = subprocess.run(
        ["git", "grep", "-n", "-E", r"^(<<<<<<<|=======|>>>>>>>)"],
        cwd=root, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if proc.returncode == 1:
        return []
    if proc.returncode not in (0, 1):
        raise GroupCtlError(proc.stderr.strip() or "git grep failed")
    return proc.stdout.splitlines()
