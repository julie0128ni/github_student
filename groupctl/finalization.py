from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import (
    GroupConfig,
    GroupCtlError,
    PROTOCOL_VERSION,
    write_json,
)
from .events import (
    cache_remote_events,
    common_event,
    iso_z,
    read_all_events,
    write_event,
)
from .git_ops import (
    canonical_remote_ref,
    commit_is_ancestor,
    fetch,
    git,
)


def _change_sort_key(event: dict[str, Any]) -> tuple[int, str]:
    change_id = str(event.get("change_id", ""))

    if change_id.startswith("C-"):
        try:
            return int(change_id[2:]), change_id
        except ValueError:
            pass

    return 999999, change_id


def _event_time_key(event: dict[str, Any]) -> str:
    return str(event.get("created_at", ""))


def _commit_in_canonical(
    cfg: GroupConfig,
    commit: str | None,
    canonical_commit: str,
) -> bool:
    if not commit:
        return False

    # Make sure the referenced commit actually exists.
    exists = git(
        cfg.root,
        "cat-file",
        "-e",
        f"{commit}^{{commit}}",
        check=False,
    )

    # git cat-file -e normally prints nothing even on success.
    # Verify with rev-parse instead.
    verified = git(
        cfg.root,
        "rev-parse",
        "--verify",
        commit,
        check=False,
    )

    if not verified:
        return False

    return commit_is_ancestor(
        cfg.root,
        commit,
        canonical_commit,
    )


def _latest_event_for_change(
    events: list[dict[str, Any]],
    change_id: str,
    event_type: str,
) -> dict[str, Any] | None:
    matches = [
        e
        for e in events
        if e.get("event_type") == event_type
        and e.get("change_id") == change_id
    ]

    if not matches:
        return None

    matches.sort(key=_event_time_key)
    return matches[-1]


def _pending_description(
    event: dict[str, Any],
    all_events: list[dict[str, Any]],
) -> str:
    change_id = str(event.get("change_id", ""))
    summary = str(event.get("summary", ""))

    latest_review = _latest_event_for_change(
        all_events,
        change_id,
        "review",
    )

    latest_integration = _latest_event_for_change(
        all_events,
        change_id,
        "integration_result",
    )

    evidence: list[str] = []

    if latest_review:
        evidence.append(
            f"latest review={latest_review.get('verdict', 'unknown')}"
        )

    if latest_integration:
        evidence.append(
            "latest integration="
            f"{latest_integration.get('result', 'unknown')}"
        )

    suffix = ""

    if evidence:
        suffix = " (" + ", ".join(evidence) + ")"

    return (
        f"- {change_id}: {summary}"
        f" — not incorporated into canonical state{suffix}"
    )


def final_state(cfg: GroupConfig) -> dict[str, Any]:
    """
    Build the group state used by FINAL.

    Important rules:
    - Fetch Git/GitHub first.
    - Discover events from all remote personal branches.
    - Git history determines whether a published change is actually
      incorporated into the canonical group result.
    """

    fetch(cfg.root)

    # Events remain authoritative in Git refs.
    # The local cache only makes events on teammates' branches discoverable.
    cache_remote_events(cfg)

    canonical_ref = canonical_remote_ref(
        cfg.root,
        cfg.canonical_branch,
    )

    canonical_commit = git(
        cfg.root,
        "rev-parse",
        canonical_ref,
    )

    all_events = read_all_events(cfg)

    published_changes = [
        e
        for e in all_events
        if e.get("event_type") == "change_published"
    ]

    published_changes.sort(key=_change_sort_key)

    accepted_changes: list[dict[str, Any]] = []
    pending_changes: list[dict[str, Any]] = []

    for change in published_changes:
        commit = change.get("commit")

        if _commit_in_canonical(
            cfg,
            str(commit) if commit else None,
            canonical_commit,
        ):
            accepted_changes.append(change)
        else:
            pending_changes.append(change)

    tests = [
        e
        for e in all_events
        if e.get("event_type") == "test_result"
    ]

    # Only test evidence whose tested commit belongs to the
    # canonical history is evidence for the final group result.
    canonical_tests: list[dict[str, Any]] = []

    for test in tests:
        commit = test.get("commit")

        if _commit_in_canonical(
            cfg,
            str(commit) if commit else None,
            canonical_commit,
        ):
            canonical_tests.append(test)

    canonical_tests.sort(key=_event_time_key)

    reviews = [
        e
        for e in all_events
        if e.get("event_type") == "review"
    ]

    blockers = [
        e
        for e in all_events
        if e.get("event_type") == "blocker"
    ]

    successful_integrations = [
        e
        for e in all_events
        if e.get("event_type") == "integration_result"
        and e.get("result") == "success"
    ]

    return {
        "canonical_ref": canonical_ref,
        "canonical_commit": canonical_commit,
        "events": all_events,
        "published_changes": published_changes,
        "accepted_changes": accepted_changes,
        "pending_changes": pending_changes,
        "tests": canonical_tests,
        "reviews": reviews,
        "blockers": blockers,
        "successful_integrations": successful_integrations,
    }


def prepare_candidate(
    cfg: GroupConfig,
    person_id: str,
    known_issues: list[str] | None = None,
) -> tuple[Path, Path]:
    state = final_state(cfg)
    known_issues = known_issues or []

    cfg.final_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    candidate = cfg.final_dir / "candidate.md"

    lines = [
        "# Final Candidate",
        "",
        "## Assignment",
        f"See `{cfg.assignment_file}`.",
        "",
        "## Proposed Result",
        f"Canonical branch: `{cfg.canonical_branch}`",
        f"Canonical commit: `{state['canonical_commit']}`",
        "",
        "## Accepted Changes",
    ]

    accepted = state["accepted_changes"]

    if accepted:
        for e in accepted:
            lines.append(
                f"- {e.get('change_id')}: "
                f"{e.get('summary', '')}"
            )
    else:
        lines.append(
            "- No published changes were verified as incorporated "
            "into the canonical commit."
        )

    lines += [
        "",
        "## Pending Published Changes",
    ]

    pending = state["pending_changes"]

    if pending:
        for e in pending:
            lines.append(
                _pending_description(
                    e,
                    state["events"],
                )
            )
    else:
        lines.append(
            "- None detected outside the canonical state."
        )

    lines += [
        "",
        "## Validation Evidence",
    ]

    if state["tests"]:
        for e in state["tests"][-10:]:
            lines.append(
                f"- {e.get('automated_result')}: "
                f"{e.get('summary', '')} "
                f"(commit {e.get('commit', '')})"
            )
    else:
        lines.append(
            "- No test_result evidence was found for a commit "
            "incorporated into the canonical state."
        )

    lines += [
        "",
        "## Known Issues",
    ]

    blocker_issues = [
        b.get("summary", "Unspecified blocker")
        for b in state["blockers"]
    ]

    all_issues = blocker_issues + known_issues

    if all_issues:
        for issue in all_issues:
            lines.append(f"- {issue}")
    else:
        lines.append("- None recorded.")

    candidate.write_text(
        "\n".join(lines) + "\n",
        encoding="utf-8",
    )

    event = common_event(
        cfg,
        person_id,
        "final_candidate",
    )

    event.update(
        {
            "canonical_commit": state["canonical_commit"],
            "candidate_report_path": (
                ".group/final/candidate.md"
            ),
            "tests_evidence": [
                e.get("event_id")
                for e in state["tests"]
            ],
            "known_issues": all_issues,
        }
    )

    event_file = write_event(
        cfg,
        event,
    )

    return candidate, event_file


def _material_contributors(
    cfg: GroupConfig,
    state: dict[str, Any],
) -> list[str]:
    """
    Determine contributors from actual accepted engineering work.

    A person counts if they:
    - authored a change incorporated into canonical state;
    - published a successful integration result for an accepted change;
    - published test evidence applicable to canonical history.
    """

    contributor_ids: set[str] = set()

    accepted_change_ids = {
        e.get("change_id")
        for e in state["accepted_changes"]
        if e.get("change_id")
    }

    for change in state["accepted_changes"]:
        person_id = change.get("person_id")

        if person_id:
            contributor_ids.add(str(person_id))

    for integration in state["successful_integrations"]:
        if integration.get("change_id") in accepted_change_ids:
            person_id = integration.get("person_id")

            if person_id:
                contributor_ids.add(str(person_id))

    for test in state["tests"]:
        person_id = test.get("person_id")

        if person_id:
            contributor_ids.add(str(person_id))

    member_names: dict[str, str] = {}

    for path in sorted(
        cfg.members_dir.glob("*.json")
    ):
        try:
            member = json.loads(
                path.read_text(
                    encoding="utf-8"
                )
            )

            pid = str(
                member.get("person_id", "")
            )

            if pid:
                member_names[pid] = str(
                    member.get(
                        "display_name",
                        "",
                    )
                )
        except Exception:
            continue

    result: list[str] = []

    for person_id in sorted(contributor_ids):
        display_name = member_names.get(
            person_id,
            "",
        )

        if display_name:
            result.append(
                f"{person_id} ({display_name})"
            )
        else:
            result.append(person_id)

    return result


def complete_final(
    cfg: GroupConfig,
    person_id: str,
    known_limitations: list[str] | None = None,
) -> tuple[Path, Path, Path]:
    state = final_state(cfg)
    known_limitations = known_limitations or []

    # The specification says important unresolved work must not be
    # silently finalized.
    if state["pending_changes"]:
        pending_ids = ", ".join(
            str(e.get("change_id", "?"))
            for e in state["pending_changes"]
        )

        raise GroupCtlError(
            "FINAL cannot complete while published changes remain "
            "outside the canonical state without an explicit "
            "resolution. Pending: "
            + pending_ids
        )

    # Require at least some applicable validation evidence.
    if not state["tests"]:
        raise GroupCtlError(
            "FINAL cannot complete without test_result evidence "
            "for the canonical history."
        )

    # Do not silently complete when the latest applicable automated
    # validation is a failure.
    latest_test = state["tests"][-1]
    latest_result = latest_test.get(
        "automated_result"
    )

    if latest_result == "fail":
        raise GroupCtlError(
            "FINAL cannot complete because the latest applicable "
            "test_result is fail."
        )

    final_report = (
        cfg.final_dir / "final_report.md"
    )

    change_lines = [
        f"- {e.get('change_id')}: "
        f"{e.get('summary', '')}"
        for e in state["accepted_changes"]
    ]

    test_lines = [
        f"- {e.get('automated_result')}: "
        f"{e.get('summary', '')} "
        f"(commit {e.get('commit', '')})"
        for e in state["tests"]
    ]

    contributors = _material_contributors(
        cfg,
        state,
    )

    lines = [
        "# Group Final Report",
        "",
        "## Assignment",
        f"See `{cfg.assignment_file}`.",
        "",
        "## Result",
        (
            "The group submits the accepted canonical "
            "repository state described below."
        ),
        "",
        "## Canonical Commit",
        state["canonical_commit"],
        "",
        "## Important Changes",
        *(
            change_lines
            or [
                "- No accepted published changes "
                "were recorded."
            ]
        ),
        "",
        "## Validation",
        *(
            test_lines
            or [
                "- No applicable test_result "
                "events recorded."
            ]
        ),
        "",
        "## Known Limitations",
        *(
            [
                f"- {x}"
                for x in known_limitations
            ]
            or ["- None recorded."]
        ),
        "",
        "## Contributors",
        *(
            [
                f"- {x}"
                for x in contributors
            ]
            or [f"- {person_id}"]
        ),
        "",
    ]

    final_report.write_text(
        "\n".join(lines),
        encoding="utf-8",
    )

    final_json = (
        cfg.final_dir / "final.json"
    )

    status = (
        "completed_with_known_limitations"
        if known_limitations
        else "completed"
    )

    write_json(
        final_json,
        {
            "protocol_version": (
                PROTOCOL_VERSION
            ),
            "group_id": cfg.group_id,
            "canonical_branch": (
                cfg.canonical_branch
            ),
            "canonical_commit": (
                state["canonical_commit"]
            ),
            "report_file": (
                ".group/final/final_report.md"
            ),
            "initiated_by": person_id,
            "created_at": iso_z(),
            "status": status,
            "known_limitations": (
                known_limitations
            ),
        },
    )

    event = common_event(
        cfg,
        person_id,
        "final_result",
    )

    event.update(
        {
            "canonical_commit": (
                state["canonical_commit"]
            ),
            "final_report_path": (
                ".group/final/"
                "final_report.md"
            ),
            "test_evidence": [
                e.get("event_id")
                for e in state["tests"]
            ],
            "known_limitations": (
                known_limitations
            ),
            "initiated_by": person_id,
        }
    )

    event_file = write_event(
        cfg,
        event,
    )

    return (
        final_report,
        final_json,
        event_file,
    )