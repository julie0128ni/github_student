from __future__ import annotations

import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .config import GroupConfig, GroupCtlError, PROTOCOL_VERSION, read_json, write_json

SUPPORTED_EVENT_TYPES = {
    "change_published", "finding", "question", "answer", "review", "test_result",
    "integration_result", "blocker", "final_candidate", "final_result",
}
REVIEW_VERDICTS = {"accept", "accept_with_changes", "reject", "needs_more_evidence"}
TEST_RESULTS = {"pass", "fail", "partial", "not_available"}
INTEGRATION_RESULTS = {"success", "failed", "needs_human"}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def make_event_id(person_id: str, now: datetime | None = None) -> str:
    now = now or utc_now()
    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    return f"E-{stamp}-{person_id}-{secrets.token_hex(2)}"


def iso_z(now: datetime | None = None) -> str:
    now = now or utc_now()
    return now.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def next_change_id(cfg: GroupConfig) -> str:
    max_n = 0
    for event in read_all_events(cfg):
        cid = event.get("change_id")
        if isinstance(cid, str) and cid.startswith("C-"):
            try:
                max_n = max(max_n, int(cid[2:]))
            except ValueError:
                continue
    return f"C-{max_n + 1}"


def common_event(cfg: GroupConfig, person_id: str, event_type: str, related_events: Iterable[str] = ()) -> dict[str, Any]:
    if event_type not in SUPPORTED_EVENT_TYPES:
        raise GroupCtlError(f"Unsupported event_type: {event_type}")
    eid = make_event_id(person_id)
    return {
        "protocol_version": PROTOCOL_VERSION,
        "event_id": eid,
        "event_type": event_type,
        "group_id": cfg.group_id,
        "person_id": person_id,
        "created_at": iso_z(),
        "related_events": list(related_events),
    }


def event_path(cfg: GroupConfig, event_id: str) -> Path:
    return cfg.events_dir / f"{event_id}.json"


def write_event(cfg: GroupConfig, event: dict[str, Any]) -> Path:
    validate_event(event, cfg)
    path = event_path(cfg, event["event_id"])
    if path.exists():
        raise GroupCtlError(f"Event already exists and is immutable: {path.name}")
    write_json(path, event)
    return path


def read_events(cfg: GroupConfig) -> list[dict[str, Any]]:
    if not cfg.events_dir.exists():
        return []
    result = []
    for path in sorted(cfg.events_dir.glob("E-*.json")):
        data = read_json(path)
        try:
            validate_event(data, cfg)
        except GroupCtlError as e:
            data = dict(data)
            data["_validation_error"] = str(e)
        result.append(data)
    return result


def find_event_by_change(cfg: GroupConfig, change_id: str) -> dict[str, Any]:
    matches = [e for e in read_all_events(cfg) if e.get("event_type") == "change_published" and e.get("change_id") == change_id]
    if not matches:
        raise GroupCtlError(f"No change_published event found for {change_id}.")
    # IDs are immutable; duplicate change IDs are invalid/ambiguous.
    if len(matches) > 1:
        raise GroupCtlError(f"Multiple change_published events found for {change_id}; cannot choose safely.")
    return matches[0]


def validate_event(e: dict[str, Any], cfg: GroupConfig | None = None) -> None:
    required = ["protocol_version", "event_id", "event_type", "group_id", "person_id", "created_at", "related_events"]
    missing = [k for k in required if k not in e]
    if missing:
        raise GroupCtlError(f"Event missing fields: {', '.join(missing)}")
    if e["protocol_version"] != PROTOCOL_VERSION:
        raise GroupCtlError("Event protocol_version must be 0.1.")
    if e["event_type"] not in SUPPORTED_EVENT_TYPES:
        raise GroupCtlError(f"Unsupported event_type {e['event_type']!r}.")
    if not isinstance(e["related_events"], list):
        raise GroupCtlError("related_events must be a list.")
    if cfg and e["group_id"] != cfg.group_id:
        raise GroupCtlError(f"Event group_id {e['group_id']!r} does not match {cfg.group_id!r}.")

    t = e["event_type"]
    requirements = {
        "change_published": ["change_id", "branch", "commit", "summary", "files_changed", "interface_changes", "tests_run", "known_issues", "proposed_for_integration"],
        "finding": ["summary", "evidence"],
        "question": ["question_id", "text", "blocking"],
        "answer": ["question_id", "text"],
        "review": ["change_id", "verdict", "summary", "issues", "tests_run"],
        "test_result": ["scope", "commit", "commands", "automated_result", "human_checks_required", "summary"],
        "integration_result": ["change_id", "target_branch", "result", "conflicts", "conflict_resolution_summary", "verification", "summary"],
        "blocker": ["summary"],
        "final_candidate": ["canonical_commit", "candidate_report_path", "tests_evidence", "known_issues"],
        "final_result": ["canonical_commit", "final_report_path", "test_evidence", "known_limitations", "initiated_by"],
    }
    miss = [k for k in requirements.get(t, []) if k not in e]
    if miss:
        raise GroupCtlError(f"{t} event missing fields: {', '.join(miss)}")
    if t == "review" and e.get("verdict") not in REVIEW_VERDICTS:
        raise GroupCtlError(f"Invalid review verdict: {e.get('verdict')!r}")
    if t == "test_result" and e.get("automated_result") not in TEST_RESULTS:
        raise GroupCtlError(f"Invalid automated_result: {e.get('automated_result')!r}")
    if t == "integration_result" and e.get("result") not in INTEGRATION_RESULTS:
        raise GroupCtlError(f"Invalid integration result: {e.get('result')!r}")


def read_cached_remote_events(cfg: GroupConfig) -> list[dict[str, Any]]:
    cache = cfg.local_dir / "remote_events"
    if not cache.exists():
        return []
    result = []
    for path in sorted(cache.glob("E-*.json")):
        try:
            data = read_json(path)
            validate_event(data, cfg)
            result.append(data)
        except GroupCtlError:
            continue
    return result


def read_all_events(cfg: GroupConfig) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for e in [*read_events(cfg), *read_cached_remote_events(cfg)]:
        eid = e.get("event_id")
        if isinstance(eid, str):
            by_id[eid] = e
    return [by_id[k] for k in sorted(by_id)]


def cache_remote_events(cfg: GroupConfig) -> list[dict[str, Any]]:
    """Discover immutable event files on all origin/* branches and cache them locally.

    This cache lives under .group/local and is not authoritative; pushed Git refs remain authoritative.
    """
    from .git_ops import git
    refs_out = git(cfg.root, "for-each-ref", "--format=%(refname:short)", "refs/remotes/origin")
    refs = [r for r in refs_out.splitlines() if r and not r.endswith("/HEAD")]
    cache = cfg.local_dir / "remote_events"
    cache.mkdir(parents=True, exist_ok=True)
    found: dict[str, dict[str, Any]] = {}
    for ref in refs:
        listing = git(cfg.root, "ls-tree", "-r", "--name-only", ref, ".group/events", check=False)
        for rel in listing.splitlines():
            if not rel.endswith(".json"):
                continue
            raw = git(cfg.root, "show", f"{ref}:{rel}", check=False)
            if not raw:
                continue
            try:
                data = json.loads(raw)
                validate_event(data, cfg)
            except (json.JSONDecodeError, GroupCtlError):
                continue
            eid = data["event_id"]
            found[eid] = data
    for eid, data in found.items():
        write_json(cache / f"{eid}.json", data)
    return [found[k] for k in sorted(found)]
