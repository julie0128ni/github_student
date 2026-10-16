from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = "0.1"

class GroupCtlError(RuntimeError):
    pass


def find_repo_root(start: Path | None = None) -> Path:
    p = (start or Path.cwd()).resolve()
    for candidate in (p, *p.parents):
        if (candidate / ".group" / "group.json").is_file():
            return candidate
    raise GroupCtlError("Could not find .group/group.json. Run inside a group repository.")


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise GroupCtlError(f"Missing required file: {path}") from e
    except json.JSONDecodeError as e:
        raise GroupCtlError(f"Invalid JSON in {path}: {e}") from e


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


@dataclass(frozen=True)
class GroupConfig:
    root: Path
    protocol_version: str
    group_id: str
    canonical_branch: str
    assignment_file: str

    @property
    def group_dir(self) -> Path:
        return self.root / ".group"

    @property
    def events_dir(self) -> Path:
        return self.group_dir / "events"

    @property
    def members_dir(self) -> Path:
        return self.group_dir / "members"

    @property
    def final_dir(self) -> Path:
        return self.group_dir / "final"

    @property
    def local_dir(self) -> Path:
        return self.group_dir / "local"


def load_group_config(root: Path | None = None) -> GroupConfig:
    repo = find_repo_root(root)
    data = read_json(repo / ".group" / "group.json")
    required = ["protocol_version", "group_id", "canonical_branch", "assignment_file"]
    missing = [k for k in required if not data.get(k)]
    if missing:
        raise GroupCtlError(f"group.json missing required fields: {', '.join(missing)}")
    if data["protocol_version"] != PROTOCOL_VERSION:
        raise GroupCtlError(
            f"Unsupported protocol_version {data['protocol_version']!r}; expected {PROTOCOL_VERSION!r}."
        )
    return GroupConfig(
        root=repo,
        protocol_version=data["protocol_version"],
        group_id=data["group_id"],
        canonical_branch=data["canonical_branch"],
        assignment_file=data["assignment_file"],
    )


def local_identity_path(cfg: GroupConfig) -> Path:
    return cfg.local_dir / "identity.json"


def set_local_identity(cfg: GroupConfig, person_id: str) -> dict[str, Any]:
    member_file = cfg.members_dir / f"{person_id}.json"
    member = read_json(member_file)
    if member.get("person_id") != person_id:
        raise GroupCtlError(f"Member file {member_file} does not identify {person_id}.")
    if member.get("protocol_version") != PROTOCOL_VERSION:
        raise GroupCtlError("Member protocol_version does not match 0.1.")
    cfg.local_dir.mkdir(parents=True, exist_ok=True)
    data = {"protocol_version": PROTOCOL_VERSION, "person_id": person_id}
    write_json(local_identity_path(cfg), data)
    return member


def get_person_id(cfg: GroupConfig, explicit: str | None = None) -> str:
    if explicit:
        member = cfg.members_dir / f"{explicit}.json"
        if not member.is_file():
            raise GroupCtlError(f"Unknown person_id {explicit!r}; missing {member}.")
        return explicit
    path = local_identity_path(cfg)
    if not path.is_file():
        raise GroupCtlError(
            "Local identity is not configured. Run: python -m groupctl configure --person-id <ID>"
        )
    person_id = read_json(path).get("person_id")
    if not person_id:
        raise GroupCtlError("Invalid .group/local/identity.json: missing person_id.")
    if not (cfg.members_dir / f"{person_id}.json").is_file():
        raise GroupCtlError(f"Configured person {person_id!r} is not a recognized group member.")
    return person_id
