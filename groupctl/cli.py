from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from .config import (
    GroupCtlError, get_person_id, load_group_config, read_json, set_local_identity, write_json,
)
from .events import (
    REVIEW_VERDICTS, cache_remote_events, common_event, find_event_by_change, next_change_id,
    read_all_events, validate_event, write_event,
)
from .finalization import complete_final, prepare_candidate
from .git_ops import (
    canonical_remote_ref, commit_is_ancestor, conflict_files, current_branch, current_commit,
    fetch, git, grep_conflict_markers, is_clean, merge, porcelain, push_current,
)
from .status import collect_status


def echo_json(data) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def ensure_personal_branch(branch: str, person_id: str) -> None:
    prefix = f"people/{person_id}/"
    if not branch.startswith(prefix):
        raise GroupCtlError(
            f"PUB must run on this person's work branch ({prefix}<topic>), not {branch!r}."
        )


def commit_event_and_push(cfg, path: Path, message: str) -> None:
    rel = str(path.relative_to(cfg.root))
    git(cfg.root, "add", "--", rel)
    git(cfg.root, "commit", "-m", message)
    push_current(cfg.root)


def cmd_configure(args) -> int:
    cfg = load_group_config()
    member = set_local_identity(cfg, args.person_id)
    print(f"Configured local identity: {member.get('person_id')} ({member.get('display_name', '')})")
    return 0


def cmd_validate(args) -> int:
    cfg = load_group_config()
    errors = []
    if not (cfg.root / cfg.assignment_file).is_file():
        errors.append(f"Missing assignment file: {cfg.assignment_file}")
    for path in sorted(cfg.members_dir.glob("*.json")):
        try:
            data = read_json(path)
            for key in ("protocol_version", "person_id", "display_name", "joined_at"):
                if key not in data:
                    raise GroupCtlError(f"{path}: missing {key}")
        except GroupCtlError as e:
            errors.append(str(e))
    for e in read_all_events(cfg):
        try:
            validate_event(e, cfg)
        except GroupCtlError as exc:
            errors.append(f"{e.get('event_id', '?')}: {exc}")
    if errors:
        print("Validation failed:")
        for e in errors:
            print(f"- {e}")
        return 1
    print("Repository collaboration structure is valid for protocol 0.1.")
    return 0


def cmd_events(args) -> int:
    cfg = load_group_config()
    if args.refresh:
        fetch(cfg.root)
        cache_remote_events(cfg)
    events = read_all_events(cfg)
    if args.type:
        events = [e for e in events if e.get("event_type") == args.type]
    if args.change_id:
        events = [e for e in events if e.get("change_id") == args.change_id]
    if args.json:
        echo_json(events)
    else:
        for e in events:
            extra = e.get("change_id") or e.get("question_id") or ""
            summary = e.get("summary") or e.get("text") or ""
            print(f"{e.get('event_id')}  {e.get('event_type'):20} {extra:8} {summary}")
    return 0


def cmd_status(args) -> int:
    cfg = load_group_config()
    data = collect_status(cfg, do_fetch=not args.no_fetch)
    echo_json(data)
    return 0


def cmd_sync(args) -> int:
    cfg = load_group_config()
    person_id = get_person_id(cfg)
    before_path = cfg.local_dir / "last_sync.json"
    old_ids = set(read_json(before_path).get("event_ids", [])) if before_path.exists() else set()
    branch_before = current_branch(cfg.root)
    commit_before = current_commit(cfg.root)
    dirty_before = porcelain(cfg.root)

    fetch(cfg.root)
    remote_events = cache_remote_events(cfg)
    all_events = read_all_events(cfg)
    new_events = [e for e in all_events if e.get("event_id") not in old_ids]
    canonical_ref = canonical_remote_ref(cfg.root, cfg.canonical_branch)
    canonical_commit = git(cfg.root, "rev-parse", canonical_ref)

    merged = False
    if args.merge_canonical:
        if dirty_before:
            raise GroupCtlError("Refusing to merge canonical state with uncommitted work. Protect/commit it first.")
        if branch_before == cfg.canonical_branch:
            git(cfg.root, "merge", "--ff-only", canonical_ref)
        else:
            merge(cfg.root, canonical_ref)
        merged = commit_before != current_commit(cfg.root)

    if merged and args.quick_test:
        proc = subprocess.run(args.quick_test, cwd=cfg.root, shell=True)
        if proc.returncode != 0:
            print("WARNING: synchronization changed code and quick validation failed.", file=sys.stderr)

    write_json(before_path, {
        "person_id": person_id,
        "event_ids": [e.get("event_id") for e in all_events],
        "canonical_commit": canonical_commit,
        "branch": current_branch(cfg.root),
        "commit": current_commit(cfg.root),
    })
    print(f"SYNC: fetched remote state; discovered {len(remote_events)} remote events.")
    print(f"Canonical: {cfg.canonical_branch} @ {canonical_commit}")
    print(f"New events since last SYNC: {len(new_events)}")
    for e in new_events:
        label = e.get("change_id") or e.get("question_id") or ""
        text = e.get("summary") or e.get("text") or ""
        print(f"- {e.get('event_type')} {label}: {text}")
    if not args.merge_canonical:
        print("Canonical code was NOT automatically merged. Use --merge-canonical only after the agent judges it safe.")
    return 0


def cmd_pub(args) -> int:
    cfg = load_group_config()
    person_id = get_person_id(cfg)
    branch = current_branch(cfg.root)
    ensure_personal_branch(branch, person_id)
    if not args.all and not args.path:
        raise GroupCtlError("PUB requires explicit --path entries or --all, so unrelated local work is not published accidentally.")

    if args.quick_test:
        for command in args.quick_test:
            print(f"$ {command}")
            proc = subprocess.run(command, cwd=cfg.root, shell=True)
            if proc.returncode != 0:
                raise GroupCtlError(f"Quick test failed; PUB stopped: {command}")

    paths = ["."] if args.all else args.path
    git(cfg.root, "add", "--", *paths)
    staged = git(cfg.root, "diff", "--cached", "--name-only").splitlines()
    if not staged:
        raise GroupCtlError("No staged changes to publish.")
    # Basic protection against obvious private/junk files.
    bad = [p for p in staged if any(token in p.lower() for token in (".env", "secret", "credentials", "__pycache__"))]
    if bad and not args.allow_suspicious:
        git(cfg.root, "reset")
        raise GroupCtlError(f"Refusing suspicious/generated paths: {', '.join(bad)}")

    git(cfg.root, "commit", "-m", args.commit_message or args.summary)
    commit = current_commit(cfg.root)
    push_current(cfg.root)

    # Refresh other published events before allocating an ID.
    fetch(cfg.root)
    cache_remote_events(cfg)
    change_id = args.change_id or next_change_id(cfg)
    ev = common_event(cfg, person_id, "change_published")
    ev.update({
        "change_id": change_id,
        "branch": branch,
        "commit": commit,
        "summary": args.summary,
        "files_changed": staged,
        "interface_changes": args.interface or [],
        "tests_run": args.quick_test or [],
        "known_issues": args.known_issue or [],
        "proposed_for_integration": args.propose,
    })
    event_file = write_event(cfg, ev)
    commit_event_and_push(cfg, event_file, f"group: publish {change_id}")
    print(f"PUB succeeded: {change_id}")
    print(f"Branch: {branch}")
    print(f"Commit: {commit}")
    print(f"Event: {event_file.relative_to(cfg.root)}")
    return 0


def cmd_review(args) -> int:
    cfg = load_group_config()
    person_id = get_person_id(cfg)
    fetch(cfg.root); cache_remote_events(cfg)
    change = find_event_by_change(cfg, args.change_id)
    commit = change["commit"]
    if git(cfg.root, "cat-file", "-e", f"{commit}^{{commit}}", check=False) == "":
        # cat-file has no stdout; inspect via rev-parse instead
        if git(cfg.root, "rev-parse", "--verify", commit, check=False) == "":
            raise GroupCtlError(f"Referenced commit not available: {commit}")
    ev = common_event(cfg, person_id, "review", related_events=[change["event_id"]])
    ev.update({
        "change_id": args.change_id,
        "verdict": args.verdict,
        "summary": args.summary,
        "issues": args.issue or [],
        "tests_run": args.test or [],
    })
    path = write_event(cfg, ev)
    commit_event_and_push(cfg, path, f"group: review {args.change_id}")
    print(f"Review published for {args.change_id}: {args.verdict}")
    return 0


def cmd_test(args) -> int:
    cfg = load_group_config()
    person_id = get_person_id(cfg)
    scope = args.scope or "current_branch"
    commit = current_commit(cfg.root)
    commands = args.command or []
    passed_commands = 0
    failed_commands = 0
    if commands:
        for command in commands:
            print(f"$ {command}")
            proc = subprocess.run(command, cwd=cfg.root, shell=True)
            if proc.returncode == 0:
                passed_commands += 1
            else:
                failed_commands += 1
    if not commands:
        result = "not_available"
    elif failed_commands:
        result = "fail"
    else:
        result = "pass"
    markers = grep_conflict_markers(cfg.root)
    if markers:
        result = "fail"
    summary = args.summary or (
        "All configured automated commands passed." if result == "pass" else
        "One or more automated checks failed." if result == "fail" else
        "No automated test command was supplied."
    )
    ev = common_event(cfg, person_id, "test_result")
    ev.update({
        "scope": scope,
        "commit": commit,
        "commands": commands,
        "automated_result": result,
        "passed": passed_commands,
        "failed": failed_commands,
        "skipped": 0,
        "human_checks_required": args.human_check or [],
        "summary": summary,
    })
    path = write_event(cfg, ev)
    commit_event_and_push(cfg, path, f"group: test result {commit[:8]}")
    print(f"TEST published: {result} for {commit}")
    return 0 if result != "fail" else 1


def cmd_integrate(args) -> int:
    cfg = load_group_config()
    person_id = get_person_id(cfg)
    fetch(cfg.root); cache_remote_events(cfg)
    change = find_event_by_change(cfg, args.change_id)
    commit = change["commit"]
    branch = current_branch(cfg.root)
    if not is_clean(cfg.root):
        raise GroupCtlError("Integration requires a clean working tree. Protect unfinished work first.")
    try:
        merge(cfg.root, commit)
    except GroupCtlError as exc:
        conflicts = conflict_files(cfg.root)
        print(f"Git integration stopped: {exc}")
        if conflicts:
            print("Conflicting files:")
            for f in conflicts:
                print(f"- {f}")
            print("Do not choose one side arbitrarily. The AI/human must inspect both intents, resolve, then run:")
            print(f"  python -m groupctl integration-result {args.change_id} --result success --semantic pass --summary \"...\"")
        return 2

    tests_status = "not_run"
    if args.test:
        failures = 0
        for command in args.test:
            print(f"$ {command}")
            if subprocess.run(command, cwd=cfg.root, shell=True).returncode != 0:
                failures += 1
        tests_status = "fail" if failures else "pass"
    incorporated = commit_is_ancestor(cfg.root, commit, "HEAD")
    markers = grep_conflict_markers(cfg.root)
    build_status = "pass" if incorporated and not markers else "fail"
    semantic = args.semantic or "needs_human"
    result = "success" if incorporated and not markers and tests_status != "fail" and semantic == "pass" else "needs_human"
    ev = common_event(cfg, person_id, "integration_result", related_events=[change["event_id"]])
    ev.update({
        "change_id": args.change_id,
        "target_branch": branch,
        "result": result,
        "merge_commit": current_commit(cfg.root),
        "conflicts": [],
        "conflict_resolution_summary": "No textual conflict." if result != "failed" else "",
        "verification": {"build": build_status, "tests": tests_status, "semantic_review": semantic},
        "summary": args.summary or f"Integrated {args.change_id}; semantic review={semantic}.",
    })
    path = write_event(cfg, ev)
    commit_event_and_push(cfg, path, f"group: integration result {args.change_id}")
    print(f"Integration recorded: {result}")
    if semantic != "pass":
        print("Git merge is not treated as sufficient. Semantic review still needs a human/AI judgment.")
    return 0 if result == "success" else 3


def cmd_integration_result(args) -> int:
    cfg = load_group_config(); person_id = get_person_id(cfg)
    fetch(cfg.root); cache_remote_events(cfg)
    change = find_event_by_change(cfg, args.change_id)
    ev = common_event(cfg, person_id, "integration_result", related_events=[change["event_id"]])
    ev.update({
        "change_id": args.change_id,
        "target_branch": current_branch(cfg.root),
        "result": args.result,
        "merge_commit": current_commit(cfg.root),
        "conflicts": args.conflict or [],
        "conflict_resolution_summary": args.conflict_summary or "",
        "verification": {"build": args.build, "tests": args.tests, "semantic_review": args.semantic},
        "summary": args.summary,
    })
    path = write_event(cfg, ev)
    commit_event_and_push(cfg, path, f"group: integration result {args.change_id}")
    print(f"Integration result published: {args.result}")
    return 0


def cmd_blocker(args) -> int:
    cfg = load_group_config(); person_id = get_person_id(cfg)
    ev = common_event(cfg, person_id, "blocker")
    ev.update({"summary": args.summary})
    if args.change_id:
        ev["blocking_change_id"] = args.change_id
    if args.requested_action:
        ev["requested_action"] = args.requested_action
    path = write_event(cfg, ev)
    commit_event_and_push(cfg, path, "group: publish blocker")
    print(f"Blocker published: {ev['event_id']}")
    return 0


def cmd_final(args) -> int:
    cfg = load_group_config(); person_id = get_person_id(cfg)
    fetch(cfg.root); cache_remote_events(cfg)
    if args.action == "prepare":
        candidate, event = prepare_candidate(cfg, person_id, args.known_issue)
        # candidate + event are deliberately committed together and pushed, but not declared final.
        git(cfg.root, "add", "--", str(candidate.relative_to(cfg.root)), str(event.relative_to(cfg.root)))
        git(cfg.root, "commit", "-m", "group: prepare final candidate")
        push_current(cfg.root)
        print(f"Prepared final candidate: {candidate.relative_to(cfg.root)}")
        print("Human confirmation is required before final complete.")
        return 0
    if not args.confirm:
        raise GroupCtlError("FINAL complete requires explicit --confirm after the human reviewed candidate.md.")
    report, envelope, event = complete_final(cfg, person_id, args.known_limitation)
    git(cfg.root, "add", "--", str(report.relative_to(cfg.root)), str(envelope.relative_to(cfg.root)), str(event.relative_to(cfg.root)))
    git(cfg.root, "commit", "-m", "group: final result")
    push_current(cfg.root)
    print(f"Final result published for canonical commit recorded in {envelope.relative_to(cfg.root)}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="groupctl", description="Group collaboration helper (protocol 0.1)")
    sub = p.add_subparsers(dest="cmd", required=True)

    q = sub.add_parser("configure", help="Configure local person identity")
    q.add_argument("--person-id", required=True); q.set_defaults(func=cmd_configure)

    q = sub.add_parser("validate", help="Validate collaboration repository structure/events")
    q.set_defaults(func=cmd_validate)

    q = sub.add_parser("events", help="List structured collaboration events")
    q.add_argument("--refresh", action="store_true"); q.add_argument("--type")
    q.add_argument("--change-id"); q.add_argument("--json", action="store_true")
    q.set_defaults(func=cmd_events)

    q = sub.add_parser("status", help="Report work relative to canonical state")
    q.add_argument("--no-fetch", action="store_true"); q.set_defaults(func=cmd_status)

    q = sub.add_parser("sync", help="Fetch group state and discover collaboration events")
    q.add_argument("--merge-canonical", action="store_true")
    q.add_argument("--quick-test", help="Quick validation command after code-changing sync")
    q.set_defaults(func=cmd_sync)

    q = sub.add_parser("pub", help="Publish coherent local work as a change")
    q.add_argument("--summary", required=True); q.add_argument("--commit-message")
    q.add_argument("--path", action="append"); q.add_argument("--all", action="store_true")
    q.add_argument("--interface", action="append"); q.add_argument("--known-issue", action="append")
    q.add_argument("--quick-test", action="append"); q.add_argument("--propose", action="store_true")
    q.add_argument("--change-id"); q.add_argument("--allow-suspicious", action="store_true")
    q.set_defaults(func=cmd_pub)

    q = sub.add_parser("review", help="Publish a review event for a change")
    q.add_argument("change_id"); q.add_argument("--verdict", required=True, choices=sorted(REVIEW_VERDICTS))
    q.add_argument("--summary", required=True); q.add_argument("--issue", action="append")
    q.add_argument("--test", action="append"); q.set_defaults(func=cmd_review)

    q = sub.add_parser("test", help="Run test commands and publish test_result evidence")
    q.add_argument("scope", nargs="?"); q.add_argument("--command", action="append")
    q.add_argument("--human-check", action="append"); q.add_argument("--summary")
    q.set_defaults(func=cmd_test)

    q = sub.add_parser("integrate", help="Mechanically integrate a published change and record verification")
    q.add_argument("change_id"); q.add_argument("--test", action="append")
    q.add_argument("--semantic", choices=["pass", "fail", "needs_human"], default="needs_human")
    q.add_argument("--summary"); q.set_defaults(func=cmd_integrate)

    q = sub.add_parser("integration-result", help="Record integration after manual/AI conflict resolution")
    q.add_argument("change_id"); q.add_argument("--result", required=True, choices=["success", "failed", "needs_human"])
    q.add_argument("--conflict", action="append"); q.add_argument("--conflict-summary")
    q.add_argument("--build", default="not_run"); q.add_argument("--tests", default="not_run")
    q.add_argument("--semantic", default="needs_human"); q.add_argument("--summary", required=True)
    q.set_defaults(func=cmd_integration_result)

    q = sub.add_parser("blocker", help="Publish a blocker event")
    q.add_argument("--summary", required=True); q.add_argument("--change-id")
    q.add_argument("--requested-action"); q.set_defaults(func=cmd_blocker)

    q = sub.add_parser("final", help="Prepare or complete group finalization")
    q.add_argument("action", choices=["prepare", "complete"])
    q.add_argument("--known-issue", action="append"); q.add_argument("--known-limitation", action="append")
    q.add_argument("--confirm", action="store_true"); q.set_defaults(func=cmd_final)
    return p


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except GroupCtlError as e:
        print(f"groupctl: error: {e}", file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
