from __future__ import annotations

from typing import Any

from .config import GroupConfig, get_person_id
from .events import read_events
from .git_ops import (
    ahead_behind, canonical_remote_ref, current_branch, current_commit, fetch, porcelain,
)


def collect_status(cfg: GroupConfig, do_fetch: bool = True) -> dict[str, Any]:
    if do_fetch:
        fetch(cfg.root)
    person_id = get_person_id(cfg)
    branch = current_branch(cfg.root)
    commit = current_commit(cfg.root)
    canonical_ref = canonical_remote_ref(cfg.root, cfg.canonical_branch)
    canonical_commit = __import__("groupctl.git_ops", fromlist=["git"]).git(cfg.root, "rev-parse", canonical_ref)
    ahead, behind = ahead_behind(cfg.root, "HEAD", canonical_ref)
    events = read_events(cfg)
    blockers = [e for e in events if e.get("event_type") == "blocker"]
    tests = [e for e in events if e.get("event_type") == "test_result"]
    local_changes = [e for e in events if e.get("event_type") == "change_published" and e.get("person_id") == person_id]
    return {
        "person_id": person_id,
        "group_id": cfg.group_id,
        "branch": branch,
        "commit": commit,
        "uncommitted": porcelain(cfg.root).splitlines(),
        "canonical_branch": cfg.canonical_branch,
        "canonical_commit": canonical_commit,
        "ahead": ahead,
        "behind": behind,
        "published_local_changes": [e.get("change_id") for e in local_changes],
        "latest_test": tests[-1] if tests else None,
        "blockers": blockers,
        "event_count": len(events),
    }
