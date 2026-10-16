from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import GroupConfig, PROTOCOL_VERSION, write_json
from .events import common_event, read_events, write_event, iso_z
from .git_ops import canonical_remote_ref, git


def final_state(cfg: GroupConfig) -> dict[str, Any]:
    cref = canonical_remote_ref(cfg.root, cfg.canonical_branch)
    canonical_commit = git(cfg.root, "rev-parse", cref)
    events = read_events(cfg)
    published = [e for e in events if e.get("event_type") == "change_published"]
    integrated_ids = {
        e.get("change_id") for e in events
        if e.get("event_type") == "integration_result" and e.get("result") == "success"
    }
    pending = [e for e in published if e.get("change_id") not in integrated_ids]
    blockers = [e for e in events if e.get("event_type") == "blocker"]
    tests = [e for e in events if e.get("event_type") == "test_result"]
    reviews = [e for e in events if e.get("event_type") == "review"]
    return {
        "canonical_commit": canonical_commit,
        "pending_changes": pending,
        "blockers": blockers,
        "tests": tests,
        "reviews": reviews,
    }


def prepare_candidate(cfg: GroupConfig, person_id: str, known_issues: list[str] | None = None) -> tuple[Path, Path]:
    state = final_state(cfg)
    known_issues = known_issues or []
    cfg.final_dir.mkdir(parents=True, exist_ok=True)
    candidate = cfg.final_dir / "candidate.md"
    pending = state["pending_changes"]
    lines = [
        "# Final Candidate", "", "## Assignment", f"See `{cfg.assignment_file}`.", "",
        "## Proposed Result", f"Canonical branch: `{cfg.canonical_branch}`", f"Canonical commit: `{state['canonical_commit']}`", "",
        "## Pending Published Changes",
    ]
    if pending:
        lines.extend([f"- {e.get('change_id')}: {e.get('summary', '')}" for e in pending])
    else:
        lines.append("- None detected from published change events.")
    lines += ["", "## Validation Evidence"]
    if state["tests"]:
        for e in state["tests"][-10:]:
            lines.append(f"- {e.get('automated_result')}: {e.get('summary', '')} (commit {e.get('commit', '')})")
    else:
        lines.append("- No test_result events found.")
    lines += ["", "## Known Issues"]
    all_issues = [b.get("summary", "blocker") for b in state["blockers"]] + known_issues
    if all_issues:
        lines.extend([f"- {x}" for x in all_issues])
    else:
        lines.append("- None recorded.")
    candidate.write_text("\n".join(lines) + "\n", encoding="utf-8")

    ev = common_event(cfg, person_id, "final_candidate")
    ev.update({
        "canonical_commit": state["canonical_commit"],
        "candidate_report_path": ".group/final/candidate.md",
        "tests_evidence": [e.get("event_id") for e in state["tests"]],
        "known_issues": all_issues,
    })
    event_file = write_event(cfg, ev)
    return candidate, event_file


def complete_final(cfg: GroupConfig, person_id: str, known_limitations: list[str] | None = None) -> tuple[Path, Path, Path]:
    state = final_state(cfg)
    known_limitations = known_limitations or []
    final_report = cfg.final_dir / "final_report.md"
    members = []
    for path in sorted(cfg.members_dir.glob("*.json")):
        try:
            import json
            m = json.loads(path.read_text(encoding="utf-8"))
            members.append(f"{m.get('person_id')} ({m.get('display_name', '')})")
        except Exception:
            continue
    change_lines = [f"- {e.get('change_id')}: {e.get('summary', '')}" for e in read_events(cfg) if e.get("event_type") == "change_published"]
    test_lines = [f"- {e.get('automated_result')}: {e.get('summary', '')}" for e in state["tests"]]
    lines = [
        "# Group Final Report", "", "## Assignment", f"See `{cfg.assignment_file}`.", "",
        "## Result", "The group submits the accepted canonical repository state described below.", "",
        "## Canonical Commit", state["canonical_commit"], "", "## Important Changes",
        *(change_lines or ["- No change_published events recorded."]), "", "## Validation",
        *(test_lines or ["- No test_result events recorded."]), "", "## Known Limitations",
        *([f"- {x}" for x in known_limitations] or ["- None recorded."]), "", "## Contributors",
        *([f"- {x}" for x in members] or [f"- {person_id}"]), "",
    ]
    final_report.write_text("\n".join(lines), encoding="utf-8")

    final_json = cfg.final_dir / "final.json"
    status = "completed_with_known_limitations" if known_limitations else "completed"
    write_json(final_json, {
        "protocol_version": PROTOCOL_VERSION,
        "group_id": cfg.group_id,
        "canonical_branch": cfg.canonical_branch,
        "canonical_commit": state["canonical_commit"],
        "report_file": ".group/final/final_report.md",
        "initiated_by": person_id,
        "created_at": iso_z(),
        "status": status,
        "known_limitations": known_limitations,
    })

    ev = common_event(cfg, person_id, "final_result")
    ev.update({
        "canonical_commit": state["canonical_commit"],
        "final_report_path": ".group/final/final_report.md",
        "test_evidence": [e.get("event_id") for e in state["tests"]],
        "known_limitations": known_limitations,
        "initiated_by": person_id,
    })
    event_file = write_event(cfg, ev)
    return final_report, final_json, event_file
