#!/usr/bin/env python3
"""Team operations for codex-agent-teams.

Provides simple shared-state primitives:
- team initialization
- task board management
- task claiming and status updates
- direct teammate messaging and broadcast inbox
- debate lifecycle and automatic decision-to-task orchestration
"""

from __future__ import annotations

import argparse
import contextlib
import difflib
import json
import math
import os
import re
import shutil
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

TEAM_ROOT = Path(".codex/teams")
TASK_STATUSES = {"pending", "in_progress", "completed"}
DEBATE_STATUSES = {"open", "decided", "applied"}
MONITORING_ENV = "TEAM_OPS_MONITORING"
MONITOR_LOG_ENV = "TEAM_OPS_MONITOR_LOG_FILE"
TEAM_ROOT_ENV = "TEAM_OPS_ROOT"
UNASSIGNED_OWNER = "unassigned"
TEAM_ROOT_IS_EXPLICIT = False
LOCK_WAIT_ENV = "TEAM_OPS_LOCK_WAIT_SECONDS"
LOCK_STALE_ENV = "TEAM_OPS_LOCK_STALE_SECONDS"
DEFAULT_LOCK_WAIT_SECONDS = 15.0
DEFAULT_LOCK_STALE_SECONDS = 300.0


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_iso_datetime(value: str) -> datetime | None:
    normalized = value.strip()
    if not normalized:
        return None
    if normalized.endswith("Z") or normalized.endswith("z"):
        normalized = normalized[:-1] + "+00:00"
    else:
        # Accept common ISO-8601 offset variants:
        # - basic form: +0000 / -0530
        # - hour-only form: +00 / -05
        match_hhmm = re.match(
            r"^(.*[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)([+-]\d{2})(\d{2})$",
            normalized,
        )
        if match_hhmm:
            normalized = f"{match_hhmm.group(1)}{match_hhmm.group(2)}:{match_hhmm.group(3)}"
        else:
            match_hh = re.match(
                r"^(.*[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?)([+-]\d{2})$",
                normalized,
            )
            if match_hh:
                normalized = f"{match_hh.group(1)}{match_hh.group(2)}:00"
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_owner_map(value: str | None) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in parse_csv(value):
        if ":" not in item:
            raise SystemExit(
                "--owner-map entries must be in 'option:owner' format, separated by commas."
            )
        option, owner = item.split(":", 1)
        option = option.strip()
        owner = owner.strip()
        if not option or not owner:
            raise SystemExit(
                "--owner-map entries must include both option and owner (option:owner)."
            )
        if option in mapping:
            raise SystemExit(
                f"--owner-map contains duplicate option '{option}'. Provide one owner per option."
            )
        mapping[option] = owner
    return mapping


def discover_workspace_root(start: Path) -> Path:
    current = start.resolve(strict=False)
    for candidate in (current, *current.parents):
        if (candidate / ".codex").is_dir():
            return candidate
    return current


def resolve_team_root(explicit: str | None) -> Path:
    configured = explicit or os.getenv(TEAM_ROOT_ENV)
    if configured:
        return Path(configured).expanduser().resolve(strict=False)
    return discover_workspace_root(Path.cwd()) / ".codex" / "teams"


def ensure_team_root_usable(path: Path) -> None:
    if path.exists() and not path.is_dir():
        raise SystemExit(
            f"Team root '{path}' is not a directory. "
            "Set --team-root (or TEAM_OPS_ROOT) to a writable directory path."
        )


def parse_positive_float(value: str | None, default: float) -> float:
    if value is None:
        return default
    try:
        parsed = float(value)
    except ValueError:
        return default
    if not math.isfinite(parsed) or parsed <= 0:
        return default
    return parsed


def team_lock_path(team_name: str) -> Path:
    return TEAM_ROOT / f".{validate_team_name(team_name)}.lock"


def read_lock_pid(lock_path: Path) -> int | None:
    try:
        lines = lock_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    if len(lines) < 2:
        return None
    try:
        pid = int(lines[1].strip())
    except ValueError:
        return None
    return pid if pid > 0 else None


def process_is_running(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def reclaim_stale_lock(lock_path: Path, *, stale_seconds: float) -> bool:
    try:
        observed = lock_path.stat()
    except OSError:
        return False

    if (time.time() - observed.st_mtime) <= stale_seconds:
        return False

    lock_pid = read_lock_pid(lock_path)
    if process_is_running(lock_pid):
        return False

    # Avoid deleting a lock that was replaced by another process after our first stat.
    try:
        current = lock_path.stat()
    except OSError:
        return False
    if current.st_ino != observed.st_ino or current.st_dev != observed.st_dev:
        return False

    try:
        lock_path.unlink()
        return True
    except OSError:
        return False


@contextlib.contextmanager
def team_state_lock(team_name: str) -> object:
    lock_path = team_lock_path(team_name)
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SystemExit(
            f"Unable to prepare team lock directory at {lock_path.parent}: {exc.strerror or exc}."
        ) from exc

    wait_seconds = parse_positive_float(
        os.getenv(LOCK_WAIT_ENV), DEFAULT_LOCK_WAIT_SECONDS
    )
    stale_seconds = parse_positive_float(
        os.getenv(LOCK_STALE_ENV), DEFAULT_LOCK_STALE_SECONDS
    )
    start = time.monotonic()

    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            if reclaim_stale_lock(lock_path, stale_seconds=stale_seconds):
                continue
            if (time.monotonic() - start) >= wait_seconds:
                raise SystemExit(
                    f"Timed out waiting for team lock at {lock_path}. "
                    f"Another command may still be writing state. Retry, or tune {LOCK_WAIT_ENV}."
                )
            time.sleep(0.05)
            continue
        except OSError as exc:
            raise SystemExit(
                f"Unable to acquire team lock at {lock_path}: {exc.strerror or exc}."
            ) from exc

        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(f"{time.time()}\n{os.getpid()}\n")
            yield
        finally:
            try:
                lock_path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                raise SystemExit(
                    f"Unable to release team lock at {lock_path}: {exc.strerror or exc}."
                ) from exc
        return


def validate_team_name(team_name: str) -> str:
    if not isinstance(team_name, str) or not team_name:
        raise SystemExit("--team-name must be a non-empty string.")
    if team_name in {".", ".."} or "/" in team_name or "\\" in team_name:
        raise SystemExit(
            "--team-name must be a single identifier and cannot contain path separators or traversal segments."
        )
    if team_name.strip() != team_name or any(ch.isspace() for ch in team_name):
        raise SystemExit("--team-name cannot contain whitespace.")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in team_name):
        raise SystemExit("--team-name cannot contain control characters.")
    return team_name


def team_dir(team_name: str) -> Path:
    team_name = validate_team_name(team_name)
    return TEAM_ROOT / team_name


def team_file(team_name: str) -> Path:
    return team_dir(team_name) / "team.json"


def task_file(team_name: str) -> Path:
    return team_dir(team_name) / "tasks.json"


def debate_file(team_name: str) -> Path:
    return team_dir(team_name) / "debates.json"


def message_file(team_name: str) -> Path:
    return team_dir(team_name) / "messages.jsonl"


def monitor_file(team_name: str) -> Path:
    return team_dir(team_name) / "monitor.jsonl"


def discover_team_roots_for_name(team_name: str) -> list[Path]:
    roots: list[Path] = []
    seen: set[Path] = set()
    cwd = Path.cwd().resolve(strict=False)
    for candidate in (cwd, *cwd.parents):
        root = candidate / ".codex" / "teams"
        meta = root / team_name / "team.json"
        if not meta.is_file():
            continue
        normalized = root.resolve(strict=False)
        if normalized in seen:
            continue
        seen.add(normalized)
        roots.append(normalized)
    return roots


def require_team(team_name: str) -> None:
    global TEAM_ROOT
    if team_file(team_name).exists():
        return

    if TEAM_ROOT_IS_EXPLICIT:
        raise SystemExit(
            f"Team '{team_name}' is not initialized under {TEAM_ROOT}. "
            f"Run: team_ops.py init --team-name {team_name} ..."
        )

    discovered_roots = discover_team_roots_for_name(team_name)
    if len(discovered_roots) == 1:
        fallback_root = discovered_roots[0]
        previous_root = TEAM_ROOT
        TEAM_ROOT = fallback_root
        if team_file(team_name).exists():
            print(
                f"Warning: Team '{team_name}' was not found under {previous_root}. "
                f"Auto-switched --team-root to {fallback_root}.",
                file=sys.stderr,
            )
            return
        TEAM_ROOT = previous_root

    if len(discovered_roots) > 1:
        raise SystemExit(
            f"Team '{team_name}' is not initialized under {TEAM_ROOT}. "
            "Found the same team name in multiple ancestor roots: "
            + ", ".join(str(root) for root in discovered_roots)
            + ". Pass --team-root explicitly."
        )

    raise SystemExit(
        f"Team '{team_name}' is not initialized under {TEAM_ROOT}. "
        f"Run: team_ops.py init --team-name {team_name} ..."
    )


def load_team_state(team_name: str) -> dict[str, object]:
    require_team(team_name)
    payload = load_json(team_file(team_name), {})
    if not isinstance(payload, dict):
        raise SystemExit(
            f"Team metadata is corrupted for team '{team_name}': expected JSON object."
        )
    return payload


def load_team_members(team_name: str) -> list[str]:
    payload = load_team_state(team_name)
    members = payload.get("members")
    if not isinstance(members, list):
        raise SystemExit(
            f"Team metadata is corrupted for team '{team_name}': 'members' must be a JSON array."
        )
    normalized: list[str] = []
    for index, member in enumerate(members):
        if not isinstance(member, str) or not member:
            raise SystemExit(
                f"Team metadata is corrupted for team '{team_name}': members[{index}] must be a non-empty string."
            )
        normalized.append(member)
    if len(set(normalized)) != len(normalized):
        raise SystemExit(
            f"Team metadata is corrupted for team '{team_name}': duplicate members are not allowed."
        )
    return normalized


def suggest_closest(value: str, candidates: list[str]) -> str | None:
    if not value or not candidates:
        return None
    matches = difflib.get_close_matches(value, candidates, n=1, cutoff=0.6)
    if not matches:
        return None
    return matches[0]


def ensure_registered_member(*, team_name: str, member: str, field_name: str) -> None:
    members = load_team_members(team_name)
    if member not in set(members):
        suggestion = suggest_closest(member, members)
        suggestion_hint = f" Did you mean '{suggestion}'?" if suggestion else ""
        raise SystemExit(
            f"{field_name} '{member}' is not a registered member of team '{team_name}'. "
            f"Registered members: {', '.join(members)}.{suggestion_hint}"
        )


def ensure_registered_member_list(*, team_name: str, members: list[str], field_name: str) -> None:
    registered_members = load_team_members(team_name)
    team_members = set(registered_members)
    unknown = sorted({member for member in members if member not in team_members})
    if unknown:
        unknown_with_hints: list[str] = []
        for member in unknown:
            suggestion = suggest_closest(member, registered_members)
            if suggestion:
                unknown_with_hints.append(f"{member} (did you mean {suggestion}?)")
            else:
                unknown_with_hints.append(member)
        raise SystemExit(
            f"{field_name} includes non-team member(s) for team '{team_name}': "
            + ", ".join(unknown_with_hints)
            + f". Registered members: {', '.join(registered_members)}."
        )


def resolve_new_debate_decider(
    *,
    team_name: str,
    requested_decider: str,
    debate_members: list[str],
    field_name: str,
) -> str:
    registered_members = load_team_members(team_name)
    registered_set = set(registered_members)

    if requested_decider in registered_set and requested_decider in debate_members:
        return requested_decider

    # Fail-soft behavior for common omission: default lead not present in roster.
    if requested_decider == "lead" and "lead" not in registered_set and debate_members:
        fallback = debate_members[0]
        if fallback in registered_set:
            print(
                f"Warning: {field_name} defaulted to 'lead', but team '{team_name}' has no lead. "
                f"Using '{fallback}' from debate members. Set {field_name} explicitly to silence this warning.",
                file=sys.stderr,
            )
            return fallback

    if requested_decider not in registered_set:
        suggestion = suggest_closest(requested_decider, registered_members)
        suggestion_hint = f" Did you mean '{suggestion}'?" if suggestion else ""
        raise SystemExit(
            f"{field_name} '{requested_decider}' is not a registered member of team '{team_name}'. "
            f"Registered members: {', '.join(registered_members)}.{suggestion_hint}"
        )

    if requested_decider not in debate_members:
        raise SystemExit(
            f"{field_name} '{requested_decider}' must be one of debate members: "
            + ", ".join(debate_members)
        )

    return requested_decider


def resolve_notify_sender(
    *,
    team_name: str,
    requested_sender: str,
    fallback_sender: str,
    reason: str,
    required: bool,
) -> str:
    if not required:
        return requested_sender

    members = load_team_members(team_name)
    member_set = set(members)
    if requested_sender in member_set:
        return requested_sender

    # Default notify sender is "lead"; if lead is not in team, fall back to the decider.
    if requested_sender == "lead" and fallback_sender in member_set:
        return fallback_sender

    suggestion = suggest_closest(requested_sender, members)
    suggestion_hint = f" Did you mean '{suggestion}'?" if suggestion else ""
    fallback_hint = ""
    if fallback_sender in member_set:
        fallback_hint = f" Use --notify-from '{fallback_sender}' to proceed."
    raise SystemExit(
        f"--notify-from '{requested_sender}' is not a registered member of team '{team_name}' "
        f"for {reason}. Registered members: {', '.join(members)}."
        f"{suggestion_hint}{fallback_hint}"
    )


def load_json(path: Path, default: object) -> object:
    if not path.exists():
        return default
    if path.is_dir():
        raise SystemExit(f"Invalid state path: {path} is a directory, expected a JSON file.")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(
            f"Unable to read JSON file at {path}: {exc.strerror or exc}."
        ) from exc
    try:
        return json.loads(raw, parse_constant=reject_nonstandard_json_number)
    except (json.JSONDecodeError, ValueError) as exc:
        raise SystemExit(
            f"Invalid JSON in {path}. Fix the file or run init --reset for a clean state."
        ) from exc


def write_text_file(path: Path, content: str, *, label: str) -> None:
    temp_path: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.is_dir():
            raise SystemExit(f"Cannot write {label}: {path} is a directory, expected a file.")
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=str(path.parent),
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temp_path = Path(handle.name)
        os.replace(temp_path, path)
    except OSError as exc:
        raise SystemExit(
            f"Unable to write {label} at {path}: {exc.strerror or exc}."
        ) from exc
    finally:
        if temp_path is not None and temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def read_text_file(path: Path, *, label: str) -> str:
    if path.is_dir():
        raise SystemExit(f"Invalid {label} path: {path} is a directory, expected a file.")
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SystemExit(
            f"Unable to read {label} at {path}: {exc.strerror or exc}."
        ) from exc


def write_json(path: Path, payload: object) -> None:
    try:
        serialized = json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"Unable to serialize JSON payload for {path}: {exc}.") from exc
    write_text_file(path, serialized, label="JSON file")


def append_jsonl(path: Path, payload: dict[str, object]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SystemExit(
            f"Unable to prepare JSONL directory at {path.parent}: {exc.strerror or exc}."
        ) from exc
    if path.exists() and path.is_dir():
        raise SystemExit(f"Cannot append JSONL: {path} is a directory, expected a file.")
    try:
        serialized = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise SystemExit(f"Unable to serialize JSONL payload for {path}: {exc}.") from exc
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(serialized + "\n")
    except OSError as exc:
        raise SystemExit(
            f"Unable to append JSONL at {path}: {exc.strerror or exc}."
        ) from exc


def remove_state_path(path: Path, *, label: str) -> None:
    if not path.exists() and not path.is_symlink():
        return
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
            return
        if path.is_dir():
            shutil.rmtree(path)
            return
        path.unlink()
    except OSError as exc:
        raise SystemExit(
            f"Unable to clear {label} at {path}: {exc.strerror or exc}."
        ) from exc


def reject_nonstandard_json_number(value: str) -> None:
    raise ValueError(f"Invalid JSON numeric constant '{value}'.")


def env_flag_true(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def is_monitoring_enabled(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "monitoring", False) or env_flag_true(MONITORING_ENV))


def resolve_monitor_path(args: argparse.Namespace, team_name: str) -> Path:
    custom = getattr(args, "monitor_log_file", None) or os.getenv(MONITOR_LOG_ENV)
    if custom:
        return Path(custom)
    return monitor_file(team_name)


def log_event(
    args: argparse.Namespace,
    *,
    team_name: str,
    event_type: str,
    command: str,
    actor: str,
    entity_type: str,
    entity_id: str,
    before: dict[str, object] | None = None,
    after: dict[str, object] | None = None,
    metadata: dict[str, object] | None = None,
) -> None:
    if not is_monitoring_enabled(args):
        return
    payload = {
        "at": utc_now(),
        "event_type": event_type,
        "command": command,
        "team_name": team_name,
        "actor": actor,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "before": before,
        "after": after,
        "metadata": metadata or {},
        "correlation_id": getattr(args, "_correlation_id", uuid.uuid4().hex),
    }
    append_jsonl(resolve_monitor_path(args, team_name), payload)


def load_task_board(team_name: str) -> dict[str, object]:
    board = load_json(task_file(team_name), {"tasks": [], "next_id": 1})
    if not isinstance(board, dict):
        raise SystemExit(f"Task board is corrupted for team '{team_name}': expected JSON object.")

    tasks = board.get("tasks", [])
    if not isinstance(tasks, list):
        raise SystemExit(
            f"Task board is corrupted for team '{team_name}': 'tasks' must be a JSON array."
        )
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            raise SystemExit(
                f"Task board is corrupted for team '{team_name}': tasks[{index}] must be an object."
            )

    next_id = board.get("next_id")
    if not isinstance(next_id, int) or next_id < 1:
        max_id = 0
        for task in tasks:
            task_id = task.get("id")
            if not isinstance(task_id, str) or not task_id.startswith("task-"):
                continue
            suffix = task_id.removeprefix("task-")
            if suffix.isdigit():
                max_id = max(max_id, int(suffix))
        next_id = max_id + 1 if max_id else 1

    return {"tasks": tasks, "next_id": next_id}


def load_debate_board(team_name: str) -> dict[str, object]:
    board = load_json(debate_file(team_name), {"debates": [], "next_id": 1})
    if not isinstance(board, dict):
        raise SystemExit(
            f"Debate board is corrupted for team '{team_name}': expected JSON object."
        )

    debates = board.get("debates", [])
    if not isinstance(debates, list):
        raise SystemExit(
            f"Debate board is corrupted for team '{team_name}': 'debates' must be a JSON array."
        )
    for index, debate in enumerate(debates):
        if not isinstance(debate, dict):
            raise SystemExit(
                f"Debate board is corrupted for team '{team_name}': debates[{index}] must be an object."
            )

    next_id = board.get("next_id")
    if not isinstance(next_id, int) or next_id < 1:
        max_id = 0
        for debate in debates:
            debate_id = debate.get("id")
            if not isinstance(debate_id, str) or not debate_id.startswith("debate-"):
                continue
            suffix = debate_id.removeprefix("debate-")
            if suffix.isdigit():
                max_id = max(max_id, int(suffix))
        next_id = max_id + 1 if max_id else 1

    return {"debates": debates, "next_id": next_id}


def find_task(board: dict[str, object], task_id: str) -> dict[str, object]:
    tasks = board.get("tasks", [])
    if not isinstance(tasks, list):
        raise SystemExit("Task board is corrupted: 'tasks' must be a JSON array.")
    known_ids: list[str] = []
    for task in tasks:
        if not isinstance(task, dict):
            continue
        tid = task.get("id")
        if isinstance(tid, str) and tid:
            known_ids.append(tid)
            if tid == task_id:
                return task
    suggestion = suggest_closest(task_id, known_ids)
    suggestion_hint = f" Did you mean '{suggestion}'?" if suggestion else ""
    known_text = ", ".join(known_ids) if known_ids else "(none)"
    raise SystemExit(f"Task not found: {task_id}.{suggestion_hint} Available tasks: {known_text}.")


def find_debate(board: dict[str, object], debate_id: str) -> dict[str, object]:
    debates = board.get("debates", [])
    if not isinstance(debates, list):
        raise SystemExit("Debate board is corrupted: 'debates' must be a JSON array.")
    known_ids: list[str] = []
    for debate in debates:
        if not isinstance(debate, dict):
            continue
        did = debate.get("id")
        if isinstance(did, str) and did:
            known_ids.append(did)
            if did == debate_id:
                return debate
    suggestion = suggest_closest(debate_id, known_ids)
    suggestion_hint = f" Did you mean '{suggestion}'?" if suggestion else ""
    known_text = ", ".join(known_ids) if known_ids else "(none)"
    raise SystemExit(f"Debate not found: {debate_id}.{suggestion_hint} Available debates: {known_text}.")


def latest_positions(debate: dict[str, object]) -> dict[str, dict[str, object]]:
    latest: dict[str, dict[str, object]] = {}
    positions = debate.get("positions", [])
    if not isinstance(positions, list):
        return latest
    for item in positions:
        if not isinstance(item, dict):
            continue
        member = item.get("member")
        if isinstance(member, str) and member:
            latest[member] = item
    return latest


def choose_decision(
    debate: dict[str, object],
) -> tuple[str, str, dict[str, float], dict[str, dict[str, object]]]:
    debate_id = debate.get("id")
    if not isinstance(debate_id, str) or not debate_id:
        debate_id = "<unknown>"
    options = require_string_list(
        debate.get("options", []),
        field_name="options",
        context=f"Debate {debate_id}",
    )
    option_set = {opt for opt in options}
    if not option_set:
        raise SystemExit("Debate has no options to decide from.")

    latest = latest_positions(debate)
    if not latest:
        raise SystemExit("No positions submitted yet; cannot decide debate.")

    scores = {opt: 0.0 for opt in option_set}
    for position in latest.values():
        option = position.get("option")
        if option not in option_set:
            continue
        confidence = position.get("confidence", 1.0)
        if not isinstance(confidence, (int, float)):
            confidence = 1.0
        scores[str(option)] += float(confidence)

    if not any(score > 0 for score in scores.values()):
        raise SystemExit("All position scores are zero; cannot auto-decide.")

    max_score = max(scores.values())
    winners = sorted([opt for opt, score in scores.items() if score == max_score])

    if len(winners) == 1:
        return winners[0], "score", scores, latest

    decider = debate.get("decider")
    if isinstance(decider, str) and decider in latest:
        decider_pick = latest[decider].get("option")
        if isinstance(decider_pick, str) and decider_pick in winners:
            return decider_pick, "tie_decider", scores, latest

    return winners[0], "tie_lexical", scores, latest


def ensure_valid_task_status(status: str) -> None:
    if status not in TASK_STATUSES:
        raise SystemExit(f"status must be one of: {', '.join(sorted(TASK_STATUSES))}")


def require_string_list(value: object, *, field_name: str, context: str) -> list[str]:
    if not isinstance(value, list):
        raise SystemExit(f"{context} is corrupted: '{field_name}' must be a JSON array.")
    output: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item:
            raise SystemExit(
                f"{context} is corrupted: {field_name}[{index}] must be a non-empty string."
            )
        output.append(item)
    return output


def validate_owner_map(
    owner_map: dict[str, str], *, options: list[str], allowed_owners: list[str], context: str
) -> None:
    option_set = set(options)
    owner_set = set(allowed_owners)

    unknown_options = sorted(option for option in owner_map if option not in option_set)
    if unknown_options:
        raise SystemExit(
            f"--owner-map has unknown option(s) for {context}: {', '.join(unknown_options)}"
        )

    invalid_owners = sorted({
        owner for owner in owner_map.values() if owner not in owner_set and owner != UNASSIGNED_OWNER
    })
    if invalid_owners:
        owners_with_hints: list[str] = []
        for owner in invalid_owners:
            suggestion = suggest_closest(owner, allowed_owners)
            if suggestion:
                owners_with_hints.append(f"{owner} (did you mean {suggestion}?)")
            else:
                owners_with_hints.append(owner)
        raise SystemExit(
            f"--owner-map has owner(s) not in team members for {context}: "
            + ", ".join(owners_with_hints)
            + f". Registered members: {', '.join(allowed_owners)} (or '{UNASSIGNED_OWNER}')."
        )


def ensure_new_debate_shape(*, options: list[str], members: list[str]) -> None:
    unique_options = {option for option in options}
    unique_members = {member for member in members}
    if len(unique_options) < 2:
        raise SystemExit("--options must include at least two unique values.")
    if len(unique_members) < 2:
        raise SystemExit("--members must include at least two unique registered members.")


def create_debate(
    team_name: str,
    topic: str,
    task_id: str | None,
    options: list[str],
    members: list[str],
    decider: str,
) -> dict[str, object]:
    normalized_options = sorted(set(options))
    normalized_members = sorted(set(members))
    normalized_task_id = task_id.strip() if isinstance(task_id, str) else ""
    if not normalized_task_id:
        normalized_task_id = None

    if len(normalized_options) < 2:
        raise SystemExit("Debate requires at least two options.")
    if len(normalized_members) < 2:
        raise SystemExit("Debate requires at least two members.")
    if decider not in normalized_members:
        raise SystemExit("--decider must be one of the registered debate members.")

    if normalized_task_id:
        find_task(load_task_board(team_name), normalized_task_id)

    board = load_debate_board(team_name)
    debate_id = f"debate-{board['next_id']}"
    debate = {
        "id": debate_id,
        "topic": topic,
        "task_id": normalized_task_id,
        "options": normalized_options,
        "members": normalized_members,
        "decider": decider,
        "status": "open",
        "positions": [],
        "decision": None,
        "applied": None,
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }
    debates = board.get("debates")
    if not isinstance(debates, list):
        raise SystemExit(
            f"Debate board is corrupted for team '{team_name}': 'debates' must be a JSON array."
        )
    debates.append(debate)
    board["next_id"] += 1
    write_json(debate_file(team_name), board)
    return debate


def apply_decision_to_task(
    *,
    args: argparse.Namespace,
    team_name: str,
    debate: dict[str, object],
    selected_option: str,
    selected_status: str,
    owner_map: dict[str, str],
    applied_by: str,
    sender: str,
) -> str:
    task_id = debate.get("task_id")
    if not isinstance(task_id, str) or not task_id:
        return "No task linked; skipped apply."

    if debate.get("applied"):
        return f"Decision already applied to {task_id}; skipped apply."

    ensure_valid_task_status(selected_status)
    task_board = load_task_board(team_name)
    task = find_task(task_board, task_id)
    before_task = {
        "owner": task.get("owner"),
        "status": task.get("status"),
        "notes_len": len(task.get("notes", [])) if isinstance(task.get("notes"), list) else 0,
    }

    owner = owner_map.get(selected_option)
    if owner:
        task["owner"] = owner
    task["status"] = selected_status

    notes = task.get("notes")
    if not isinstance(notes, list):
        notes = []
        task["notes"] = notes

    decision = debate.get("decision")
    rationale = ""
    if isinstance(decision, dict):
        rationale = str(decision.get("rationale") or "")

    note_text = f"Debate {debate.get('id')} chose '{selected_option}'."
    if rationale:
        note_text = f"{note_text} Rationale: {rationale}"
    notes.append({"at": utc_now(), "text": note_text})
    task["updated_at"] = utc_now()
    write_json(task_file(team_name), task_board)

    debate["status"] = "applied"
    debate["applied"] = {
        "at": utc_now(),
        "by": applied_by,
        "task_id": task_id,
        "status": selected_status,
        "owner": task.get("owner"),
        "option": selected_option,
    }
    debate["updated_at"] = utc_now()

    append_jsonl(
        message_file(team_name),
        {
            "at": utc_now(),
            "type": "broadcast",
            "from": sender,
            "to": "*",
            "body": (
                f"Debate {debate.get('id')} decided '{selected_option}' and applied to "
                f"{task_id} (status={selected_status}, owner={task.get('owner')})."
            ),
        },
    )
    after_task = {
        "owner": task.get("owner"),
        "status": task.get("status"),
        "notes_len": len(task.get("notes", [])) if isinstance(task.get("notes"), list) else 0,
    }
    log_event(
        args,
        team_name=team_name,
        event_type="debate.applied",
        command="apply-decision",
        actor=applied_by,
        entity_type="debate",
        entity_id=str(debate.get("id")),
        before={"task": before_task},
        after={"task": after_task},
        metadata={
            "task_id": task_id,
            "option": selected_option,
            "status": selected_status,
            "sender": sender,
        },
    )

    return f"Applied decision to {task_id} (status={selected_status}, owner={task.get('owner')})"


def cmd_init(args: argparse.Namespace) -> None:
    members = [m.strip() for m in args.members.split(",") if m.strip()]
    if not members:
        raise SystemExit("--members must include at least one member.")
    if len(set(members)) != len(members):
        raise SystemExit("--members must not contain duplicates.")
    tdir = team_dir(args.team_name)
    team_path_conflict = tdir.exists() and not tdir.is_dir()
    team_meta_path = team_file(args.team_name)
    team_meta_exists = team_meta_path.exists() or team_meta_path.is_symlink()
    team_meta_usable = team_meta_path.is_file()
    team_meta_corrupted = False
    if team_meta_usable:
        try:
            load_team_members(args.team_name)
        except SystemExit:
            team_meta_corrupted = True
            team_meta_usable = False
    sidecar_state_exists = any(
        path.exists() or path.is_symlink()
        for path in (
            task_file(args.team_name),
            debate_file(args.team_name),
            message_file(args.team_name),
            monitor_file(args.team_name),
        )
    )
    existing_state = team_meta_exists or sidecar_state_exists or team_path_conflict
    recovering_partial_state = (
        (existing_state and not team_meta_usable)
        or team_meta_corrupted
        or team_path_conflict
    )
    if team_meta_usable and not args.reset:
        print(
            f"Team '{args.team_name}' already exists at {tdir}. "
            "No changes made. Re-run with --reset to reinitialize."
        )
        return
    if recovering_partial_state and not args.reset:
        print(
            f"Team '{args.team_name}' has partial or corrupted state at {tdir}. "
            "Reinitializing and clearing stale state."
        )
    if args.reset or recovering_partial_state:
        for path in (
            team_file(args.team_name),
            task_file(args.team_name),
            debate_file(args.team_name),
            message_file(args.team_name),
            monitor_file(args.team_name),
        ):
            remove_state_path(path, label="team state path")
        if team_path_conflict:
            remove_state_path(tdir, label="team directory path")
    try:
        tdir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise SystemExit(
            f"Unable to prepare team directory at {tdir}: {exc.strerror or exc}."
        ) from exc
    team_payload = {
        "team_name": args.team_name,
        "goal": args.goal,
        "members": members,
        "created_at": utc_now(),
    }
    write_json(team_file(args.team_name), team_payload)
    write_json(task_file(args.team_name), {"tasks": [], "next_id": 1})
    write_json(debate_file(args.team_name), {"debates": [], "next_id": 1})
    write_text_file(message_file(args.team_name), "", label="message log")
    if (args.reset or recovering_partial_state) and monitor_file(args.team_name).exists():
        write_text_file(monitor_file(args.team_name), "", label="monitor log")
    init_event = "team.initialized"
    init_command = "init"
    if args.reset and existing_state:
        init_event = "team.reinitialized"
        init_command = "init --reset"
    elif recovering_partial_state:
        init_event = "team.recovered"
        init_command = "init (recovery)"
    log_event(
        args,
        team_name=args.team_name,
        event_type=init_event,
        command=init_command,
        actor="lead",
        entity_type="team",
        entity_id=args.team_name,
        after={
            "members": members,
            "goal": args.goal,
            "team_root": str(TEAM_ROOT),
            "recovered_partial_state": recovering_partial_state,
        },
    )
    if args.reset and existing_state:
        print(f"Reinitialized team '{args.team_name}' at {tdir}")
    elif recovering_partial_state:
        print(f"Recovered team '{args.team_name}' at {tdir}")
    else:
        print(f"Initialized team '{args.team_name}' at {tdir}")


def cmd_add_task(args: argparse.Namespace) -> None:
    require_team(args.team_name)
    if args.status not in TASK_STATUSES:
        raise SystemExit(f"--status must be one of: {', '.join(sorted(TASK_STATUSES))}")
    if args.owner != UNASSIGNED_OWNER:
        ensure_registered_member(
            team_name=args.team_name,
            member=args.owner,
            field_name="--owner",
        )
    board = load_task_board(args.team_name)
    task_id = f"task-{board['next_id']}"
    depends_on = [d.strip() for d in (args.depends_on or "").split(",") if d.strip()]
    task = {
        "id": task_id,
        "title": args.title,
        "owner": args.owner,
        "status": args.status,
        "depends_on": depends_on,
        "notes": [],
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }
    tasks = board.get("tasks")
    if not isinstance(tasks, list):
        raise SystemExit(
            f"Task board is corrupted for team '{args.team_name}': 'tasks' must be a JSON array."
        )
    tasks.append(task)
    board["next_id"] += 1
    write_json(task_file(args.team_name), board)
    log_event(
        args,
        team_name=args.team_name,
        event_type="task.created",
        command="add-task",
        actor=args.owner,
        entity_type="task",
        entity_id=task_id,
        after={"status": args.status, "owner": args.owner, "depends_on": depends_on},
    )
    print(f"Added {task_id}: {args.title}")


def cmd_claim(args: argparse.Namespace) -> None:
    require_team(args.team_name)
    ensure_registered_member(
        team_name=args.team_name,
        member=args.member,
        field_name="--member",
    )
    board = load_task_board(args.team_name)
    task = find_task(board, args.task_id)
    status = task.get("status")
    if status == "completed":
        raise SystemExit(f"Cannot claim completed task: {args.task_id}")
    before = {"owner": task.get("owner"), "status": task.get("status")}
    task["owner"] = args.member
    task["status"] = "in_progress"
    task["updated_at"] = utc_now()
    write_json(task_file(args.team_name), board)
    log_event(
        args,
        team_name=args.team_name,
        event_type="task.claimed",
        command="claim",
        actor=args.member,
        entity_type="task",
        entity_id=args.task_id,
        before=before,
        after={"owner": task.get("owner"), "status": task.get("status")},
    )
    print(f"{args.member} claimed {args.task_id}")


def cmd_update_task(args: argparse.Namespace) -> None:
    require_team(args.team_name)
    if args.status:
        ensure_valid_task_status(args.status)
    if args.owner and args.owner != UNASSIGNED_OWNER:
        ensure_registered_member(
            team_name=args.team_name,
            member=args.owner,
            field_name="--owner",
        )
    board = load_task_board(args.team_name)
    task = find_task(board, args.task_id)
    before = {
        "owner": task.get("owner"),
        "status": task.get("status"),
        "depends_on": task.get("depends_on"),
        "notes_len": len(task.get("notes", [])) if isinstance(task.get("notes"), list) else 0,
    }
    if args.status:
        task["status"] = args.status
    if args.owner:
        task["owner"] = args.owner
    if args.note:
        notes = task.get("notes")
        if not isinstance(notes, list):
            notes = []
            task["notes"] = notes
        notes.append({"at": utc_now(), "text": args.note})
    if args.depends_on is not None:
        task["depends_on"] = [d.strip() for d in args.depends_on.split(",") if d.strip()]
    task["updated_at"] = utc_now()
    write_json(task_file(args.team_name), board)
    log_event(
        args,
        team_name=args.team_name,
        event_type="task.updated",
        command="update-task",
        actor=args.owner or "lead",
        entity_type="task",
        entity_id=args.task_id,
        before=before,
        after={
            "owner": task.get("owner"),
            "status": task.get("status"),
            "depends_on": task.get("depends_on"),
            "notes_len": len(task.get("notes", [])) if isinstance(task.get("notes"), list) else 0,
        },
        metadata={"note_added": bool(args.note)},
    )
    print(f"Updated {args.task_id}")


def cmd_list_tasks(args: argparse.Namespace) -> None:
    require_team(args.team_name)
    board = load_task_board(args.team_name)
    tasks = board.get("tasks", [])
    if not isinstance(tasks, list):
        raise SystemExit(
            f"Task board is corrupted for team '{args.team_name}': 'tasks' must be a JSON array."
        )
    if not tasks:
        print("No tasks.")
        return
    for index, task in enumerate(tasks):
        if not isinstance(task, dict):
            raise SystemExit(
                f"Task board is corrupted for team '{args.team_name}': tasks[{index}] must be an object."
            )
        depends_on = task.get("depends_on", [])
        if not isinstance(depends_on, list):
            raise SystemExit(
                f"Task board is corrupted for team '{args.team_name}': "
                f"tasks[{index}].depends_on must be a JSON array."
            )
        for dep_index, dep in enumerate(depends_on):
            if not isinstance(dep, str) or not dep:
                raise SystemExit(
                    f"Task board is corrupted for team '{args.team_name}': "
                    f"tasks[{index}].depends_on[{dep_index}] must be a non-empty string."
                )
        deps = ",".join(depends_on) if depends_on else "-"
        print(
            f"{task.get('id')}  {task.get('status')}  owner={task.get('owner')}  deps={deps}  {task.get('title')}"
        )


def cmd_message(args: argparse.Namespace) -> None:
    require_team(args.team_name)
    ensure_registered_member(
        team_name=args.team_name,
        member=args.sender,
        field_name="--from",
    )
    ensure_registered_member(
        team_name=args.team_name,
        member=args.to,
        field_name="--to",
    )
    payload = {
        "at": utc_now(),
        "type": "direct",
        "from": args.sender,
        "to": args.to,
        "body": args.body,
    }
    append_jsonl(message_file(args.team_name), payload)
    log_event(
        args,
        team_name=args.team_name,
        event_type="message.sent",
        command="message",
        actor=args.sender,
        entity_type="message",
        entity_id=f"{args.sender}->{args.to}",
        metadata={"body": args.body},
    )
    print(f"Message sent to {args.to}")


def cmd_broadcast(args: argparse.Namespace) -> None:
    require_team(args.team_name)
    ensure_registered_member(
        team_name=args.team_name,
        member=args.sender,
        field_name="--from",
    )
    payload = {
        "at": utc_now(),
        "type": "broadcast",
        "from": args.sender,
        "to": "*",
        "body": args.body,
    }
    append_jsonl(message_file(args.team_name), payload)
    log_event(
        args,
        team_name=args.team_name,
        event_type="message.broadcast",
        command="broadcast",
        actor=args.sender,
        entity_type="message",
        entity_id=f"{args.sender}->*",
        metadata={"body": args.body},
    )
    print("Broadcast sent")


def cmd_inbox(args: argparse.Namespace) -> None:
    require_team(args.team_name)
    ensure_registered_member(
        team_name=args.team_name,
        member=args.member,
        field_name="--member",
    )
    mfile = message_file(args.team_name)
    if not mfile.exists():
        print("No messages.")
        return
    lines = read_text_file(
        mfile,
        label=f"message log for team '{args.team_name}'",
    ).splitlines()
    found = False
    invalid_lines = 0
    for line in lines:
        if not line.strip():
            continue
        try:
            payload = json.loads(line, parse_constant=reject_nonstandard_json_number)
        except (json.JSONDecodeError, ValueError):
            invalid_lines += 1
            continue
        if not isinstance(payload, dict):
            invalid_lines += 1
            continue
        mtype = payload.get("type")
        target = payload.get("to")
        if mtype == "broadcast" or target == args.member:
            found = True
            print(f"[{payload.get('at')}] {payload.get('from')} -> {target}: {payload.get('body')}")
    if invalid_lines:
        print(f"Warning: skipped {invalid_lines} invalid message log line(s).")
    if not found:
        print(f"No inbox messages for {args.member}.")


def cmd_start_debate(args: argparse.Namespace) -> None:
    require_team(args.team_name)
    options = parse_csv(args.options)
    members = parse_csv(args.members)
    requested_decider = args.decider or "lead"
    ensure_registered_member_list(
        team_name=args.team_name,
        members=members,
        field_name="--members",
    )
    ensure_new_debate_shape(options=options, members=members)
    decider = resolve_new_debate_decider(
        team_name=args.team_name,
        requested_decider=requested_decider,
        debate_members=members,
        field_name="--decider",
    )
    notify_sender = resolve_notify_sender(
        team_name=args.team_name,
        requested_sender=args.notify_from,
        fallback_sender=decider,
        reason="start-debate notifications",
        required=bool(args.notify),
    )

    debate = create_debate(
        team_name=args.team_name,
        topic=args.topic,
        task_id=args.task_id,
        options=options,
        members=members,
        decider=decider,
    )

    if args.notify:
        for member in debate["members"]:
            append_jsonl(
                message_file(args.team_name),
                {
                    "at": utc_now(),
                    "type": "direct",
                    "from": notify_sender,
                    "to": member,
                    "body": (
                        f"Debate {debate['id']} started for topic '{args.topic}'. "
                        f"Submit your position with add-position. Options: {', '.join(debate['options'])}"
                    ),
                },
            )
            log_event(
                args,
                team_name=args.team_name,
                event_type="message.sent",
                command="start-debate",
                actor=notify_sender,
                entity_type="message",
                entity_id=f"{notify_sender}->{member}",
                metadata={"debate_id": debate["id"], "topic": args.topic},
            )

    log_event(
        args,
        team_name=args.team_name,
        event_type="debate.started",
        command="start-debate",
        actor=decider,
        entity_type="debate",
        entity_id=str(debate["id"]),
        after={
            "topic": args.topic,
            "task_id": args.task_id,
            "options": debate["options"],
            "members": debate["members"],
            "status": debate["status"],
        },
    )

    print(f"Started {debate['id']} topic='{args.topic}' task={debate['task_id']}")


def cmd_add_position(args: argparse.Namespace) -> None:
    require_team(args.team_name)
    ensure_registered_member(
        team_name=args.team_name,
        member=args.member,
        field_name="--member",
    )
    if not math.isfinite(args.confidence) or args.confidence < 0 or args.confidence > 1:
        raise SystemExit("--confidence must be between 0 and 1.")

    board = load_debate_board(args.team_name)
    debate = find_debate(board, args.debate_id)

    if debate.get("status") != "open":
        raise SystemExit(
            f"Debate {args.debate_id} is {debate.get('status')}; cannot add positions."
        )

    members = require_string_list(
        debate.get("members", []),
        field_name="members",
        context=f"Debate {args.debate_id}",
    )
    options = require_string_list(
        debate.get("options", []),
        field_name="options",
        context=f"Debate {args.debate_id}",
    )

    if args.member not in members:
        suggestion = suggest_closest(args.member, members)
        suggestion_hint = f" Did you mean '{suggestion}'?" if suggestion else ""
        raise SystemExit(
            f"--member '{args.member}' is not registered for {args.debate_id}. "
            f"Debate members: {', '.join(members)}.{suggestion_hint}"
        )
    if args.option not in options:
        raise SystemExit(
            f"--option must be one of: {', '.join(str(opt) for opt in options)}"
        )

    positions = debate.get("positions")
    if not isinstance(positions, list):
        positions = []
        debate["positions"] = positions

    positions.append(
        {
            "at": utc_now(),
            "member": args.member,
            "option": args.option,
            "confidence": args.confidence,
            "rationale": args.rationale,
        }
    )
    debate["updated_at"] = utc_now()

    write_json(debate_file(args.team_name), board)
    log_event(
        args,
        team_name=args.team_name,
        event_type="debate.position_added",
        command="add-position",
        actor=args.member,
        entity_type="debate",
        entity_id=args.debate_id,
        metadata={
            "option": args.option,
            "confidence": args.confidence,
        },
    )
    print(f"Recorded position from {args.member} on {args.debate_id}")


def cmd_list_debates(args: argparse.Namespace) -> None:
    require_team(args.team_name)
    board = load_debate_board(args.team_name)
    debates = board.get("debates", [])
    if not isinstance(debates, list):
        raise SystemExit(
            f"Debate board is corrupted for team '{args.team_name}': 'debates' must be a JSON array."
        )

    filtered = []
    for debate in debates:
        if not isinstance(debate, dict):
            continue
        status = debate.get("status")
        if args.status and status != args.status:
            continue
        filtered.append(debate)

    if not filtered:
        print("No debates.")
        return

    for debate in filtered:
        debate_id = debate.get("id")
        if not isinstance(debate_id, str) or not debate_id:
            debate_id = "<unknown>"
        members = require_string_list(
            debate.get("members", []),
            field_name="members",
            context=f"Debate {debate_id}",
        )
        submitted = len(latest_positions(debate))
        print(
            f"{debate.get('id')}  {debate.get('status')}  task={debate.get('task_id') or '-'}  "
            f"positions={submitted}/{len(members)}  topic={debate.get('topic')}"
        )


def cmd_show_debate(args: argparse.Namespace) -> None:
    require_team(args.team_name)
    board = load_debate_board(args.team_name)
    debate = find_debate(board, args.debate_id)
    print(json.dumps(debate, indent=2, ensure_ascii=False))


def cmd_monitor_report(args: argparse.Namespace) -> None:
    require_team(args.team_name)
    monitor_path = resolve_monitor_path(args, args.team_name)
    if not monitor_path.exists():
        payload = {
            "team_name": args.team_name,
            "monitor_file": str(monitor_path),
            "events_total": 0,
            "invalid_event_lines": 0,
            "other_team_event_lines": 0,
            "message_count": 0,
            "debate_count": 0,
            "debate_applied_count": 0,
            "task_reflection_count": 0,
            "reflection_latency_seconds": 0.0,
            "orchestrate_wait_cycles": 0,
        }
        if args.output:
            write_json(Path(args.output), payload)
        print(json.dumps(payload, ensure_ascii=False))
        return

    events = []
    invalid_lines = 0
    other_team_lines = 0
    for line in read_text_file(
        monitor_path,
        label=f"monitor log for team '{args.team_name}'",
    ).splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line, parse_constant=reject_nonstandard_json_number)
        except (json.JSONDecodeError, ValueError):
            invalid_lines += 1
            continue
        if not isinstance(payload, dict):
            invalid_lines += 1
            continue
        payload_team = payload.get("team_name")
        if not isinstance(payload_team, str) or not payload_team:
            invalid_lines += 1
            continue
        event_type = payload.get("event_type")
        command = payload.get("command")
        actor = payload.get("actor")
        entity_type = payload.get("entity_type")
        entity_id = payload.get("entity_id")
        if not isinstance(event_type, str) or not event_type:
            invalid_lines += 1
            continue
        if not isinstance(command, str) or not command:
            invalid_lines += 1
            continue
        if not isinstance(actor, str) or not actor:
            invalid_lines += 1
            continue
        if not isinstance(entity_type, str) or not entity_type:
            invalid_lines += 1
            continue
        if not isinstance(entity_id, str) or not entity_id:
            invalid_lines += 1
            continue
        at_value = payload.get("at")
        if not isinstance(at_value, str) or not at_value:
            invalid_lines += 1
            continue
        if parse_iso_datetime(at_value) is None:
            invalid_lines += 1
            continue
        if payload_team != args.team_name:
            other_team_lines += 1
            continue
        events.append(payload)

    message_count = 0
    debate_ids: set[str] = set()
    debate_applied_count = 0
    task_reflection_count = 0
    orchestrate_wait_cycles = 0
    start_times: dict[str, datetime] = {}
    latencies: list[float] = []

    for event in events:
        etype = event["event_type"]
        entity_id = event["entity_id"]
        if etype.startswith("message."):
            message_count += 1
        if etype.startswith("debate."):
            debate_ids.add(entity_id)
        if etype == "debate.started":
            debate_ids.add(entity_id)
            at_raw = event.get("at")
            if isinstance(at_raw, str):
                parsed = parse_iso_datetime(at_raw)
                if parsed is not None:
                    start_times[entity_id] = parsed
        if etype == "debate.applied":
            debate_applied_count += 1
            task_reflection_count += 1
            at_raw = event.get("at")
            if isinstance(at_raw, str) and entity_id in start_times:
                applied_at = parse_iso_datetime(at_raw)
                if applied_at is not None:
                    latencies.append((applied_at - start_times[entity_id]).total_seconds())
        if etype == "orchestrate.waiting":
            orchestrate_wait_cycles += 1

    payload = {
        "team_name": args.team_name,
        "monitor_file": str(monitor_path),
        "events_total": len(events),
        "invalid_event_lines": invalid_lines,
        "other_team_event_lines": other_team_lines,
        "message_count": message_count,
        "debate_count": len(debate_ids),
        "debate_applied_count": debate_applied_count,
        "task_reflection_count": task_reflection_count,
        "reflection_latency_seconds": (sum(latencies) / len(latencies)) if latencies else 0.0,
        "orchestrate_wait_cycles": orchestrate_wait_cycles,
    }
    if args.output:
        write_json(Path(args.output), payload)
    print(json.dumps(payload, ensure_ascii=False))


def cmd_decide_debate(args: argparse.Namespace) -> None:
    require_team(args.team_name)
    team_members = load_team_members(args.team_name)
    board = load_debate_board(args.team_name)
    debate = find_debate(board, args.debate_id)

    status = debate.get("status")
    if status == "applied":
        print(f"{args.debate_id} is already applied.")
        return

    options = require_string_list(
        debate.get("options", []),
        field_name="options",
        context=f"Debate {args.debate_id}",
    )
    members = require_string_list(
        debate.get("members", []),
        field_name="members",
        context=f"Debate {args.debate_id}",
    )
    owner_map: dict[str, str] = {}
    has_linked_task = bool(isinstance(debate.get("task_id"), str) and debate.get("task_id"))
    if args.apply and has_linked_task:
        owner_map = parse_owner_map(args.owner_map)
        validate_owner_map(
            owner_map,
            options=options,
            allowed_owners=team_members,
            context=f"Debate {args.debate_id}",
        )
    configured_decider = debate.get("decider")
    effective_decider = args.decider
    if not effective_decider and isinstance(configured_decider, str) and configured_decider:
        effective_decider = configured_decider
    if not effective_decider:
        effective_decider = "lead"
    ensure_registered_member(
        team_name=args.team_name,
        member=effective_decider,
        field_name="--decider",
    )

    if effective_decider not in members:
        raise SystemExit("--decider must be one of the registered debate members.")
    if (
        isinstance(configured_decider, str)
        and configured_decider
        and effective_decider != configured_decider
    ):
        raise SystemExit(
            f"--decider must match debate decider '{configured_decider}' for {args.debate_id}."
        )

    selected: str
    method: str
    scores: dict[str, float]
    decision_recorded = False

    if status == "decided":
        existing = debate.get("decision")
        if not isinstance(existing, dict):
            raise SystemExit(
                f"Debate {args.debate_id} is marked decided but has no valid decision payload."
            )
        existing_option = existing.get("option")
        if not isinstance(existing_option, str) or existing_option not in options:
            raise SystemExit(
                f"Debate {args.debate_id} has an invalid decided option; cannot proceed."
            )
        if args.decision and args.decision != existing_option:
            raise SystemExit(
                f"{args.debate_id} is already decided as '{existing_option}'. "
                "Use the existing decision or open a new debate."
            )
        selected = existing_option
        raw_method = existing.get("method")
        method = raw_method if isinstance(raw_method, str) and raw_method else "existing"
        raw_scores = existing.get("scores")
        scores = raw_scores if isinstance(raw_scores, dict) else {option: 0.0 for option in options}

        if args.require_all_positions:
            latest = latest_positions(debate)
            missing = sorted(set(members) - set(latest))
            if missing:
                raise SystemExit(
                    "Cannot apply decided debate yet; missing positions from: "
                    + ", ".join(missing)
                )

        if not args.apply:
            print(
                f"{args.debate_id} is already decided as '{selected}'. "
                "Use --apply to reflect that decision to the linked task."
            )
            return
    else:
        if not args.rationale:
            raise SystemExit("--rationale is required when finalizing a new debate decision.")
        if args.decision:
            if args.decision not in options:
                raise SystemExit(
                    f"--decision must be one of: {', '.join(str(opt) for opt in options)}"
                )
            selected = args.decision
            latest = latest_positions(debate)
            scores = {option: 0.0 for option in options}
            method = "manual"
        else:
            selected, method, scores, latest = choose_decision(debate)

        if args.require_all_positions:
            missing = sorted(set(members) - set(latest))
            if missing:
                raise SystemExit(
                    "Cannot decide yet; missing positions from: " + ", ".join(missing)
                )

        debate["decision"] = {
            "at": utc_now(),
            "decider": effective_decider,
            "option": selected,
            "method": method,
            "scores": scores,
            "rationale": args.rationale,
        }
        debate["status"] = "decided"
        debate["updated_at"] = utc_now()
        decision_recorded = True
        log_event(
            args,
            team_name=args.team_name,
            event_type="debate.decided",
            command="decide-debate",
            actor=effective_decider,
            entity_type="debate",
            entity_id=args.debate_id,
            after={
                "option": selected,
                "method": method,
                "status": debate["status"],
                "scores": scores,
            },
            metadata={"rationale": args.rationale},
        )

    apply_note = ""
    if args.apply:
        will_broadcast_apply = bool(
            isinstance(debate.get("task_id"), str)
            and debate.get("task_id")
            and not debate.get("applied")
        )
        notify_sender = resolve_notify_sender(
            team_name=args.team_name,
            requested_sender=args.notify_from,
            fallback_sender=effective_decider,
            reason="decide-debate apply broadcast",
            required=will_broadcast_apply,
        )
        apply_note = apply_decision_to_task(
            args=args,
            team_name=args.team_name,
            debate=debate,
            selected_option=selected,
            selected_status=args.status_on_apply,
            owner_map=owner_map,
            applied_by=effective_decider,
            sender=notify_sender,
        )

    write_json(debate_file(args.team_name), board)
    if decision_recorded:
        print(
            f"Decided {args.debate_id}: option='{selected}' method={method} "
            f"status={debate.get('status')}"
        )
    else:
        print(
            f"Applied existing decision for {args.debate_id}: "
            f"option='{selected}' status={debate.get('status')}"
        )
    if apply_note:
        print(apply_note)


def cmd_orchestrate_debate(args: argparse.Namespace) -> None:
    require_team(args.team_name)
    team_members = load_team_members(args.team_name)

    board = load_debate_board(args.team_name)
    owner_map: dict[str, str] = {}

    debate: dict[str, object]
    if args.debate_id:
        debate = find_debate(board, args.debate_id)
    else:
        options = parse_csv(args.options)
        members = parse_csv(args.members)
        requested_decider = args.decider or "lead"
        ensure_registered_member_list(
            team_name=args.team_name,
            members=members,
            field_name="--members",
        )
        ensure_new_debate_shape(options=options, members=members)
        decider = resolve_new_debate_decider(
            team_name=args.team_name,
            requested_decider=requested_decider,
            debate_members=members,
            field_name="--decider",
        )
        debate = create_debate(
            team_name=args.team_name,
            topic=args.topic,
            task_id=args.task_id,
            options=options,
            members=members,
            decider=decider,
        )
        board = load_debate_board(args.team_name)
        debate = find_debate(board, str(debate["id"]))
        log_event(
            args,
            team_name=args.team_name,
            event_type="debate.started",
            command="orchestrate-debate",
            actor=str(debate.get("decider") or args.decider or "lead"),
            entity_type="debate",
            entity_id=str(debate["id"]),
            after={
                "topic": debate.get("topic"),
                "task_id": debate.get("task_id"),
                "options": debate.get("options"),
                "members": debate.get("members"),
                "status": debate.get("status"),
            },
        )

    debate_id = str(debate.get("id"))
    if debate.get("status") == "applied":
        print(f"{debate_id} already applied. Nothing to do.")
        return

    members = require_string_list(
        debate.get("members", []),
        field_name="members",
        context=f"Debate {debate_id}",
    )
    options = require_string_list(
        debate.get("options", []),
        field_name="options",
        context=f"Debate {debate_id}",
    )
    effective_decider = str(debate.get("decider") or args.decider or "lead")
    if effective_decider not in members:
        raise SystemExit(
            f"Debate {debate_id} decider '{effective_decider}' is not a registered member."
        )
    ensure_registered_member(
        team_name=args.team_name,
        member=effective_decider,
        field_name="--decider",
    )
    latest = latest_positions(debate)
    missing = sorted(set(members) - set(latest))

    if missing:
        if args.send_reminders:
            notify_sender = resolve_notify_sender(
                team_name=args.team_name,
                requested_sender=args.notify_from,
                fallback_sender=effective_decider,
                reason="orchestrate-debate reminders",
                required=True,
            )
            for member in missing:
                append_jsonl(
                    message_file(args.team_name),
                    {
                        "at": utc_now(),
                        "type": "direct",
                        "from": notify_sender,
                        "to": member,
                        "body": (
                            f"Debate {debate_id} is waiting for your position. "
                            f"Run add-position with one of: {', '.join(str(x) for x in debate.get('options', []))}"
                        ),
                    },
                )
                log_event(
                    args,
                    team_name=args.team_name,
                    event_type="message.sent",
                    command="orchestrate-debate",
                    actor=notify_sender,
                    entity_type="message",
                    entity_id=f"{notify_sender}->{member}",
                    metadata={"debate_id": debate_id, "reason": "missing_position"},
                )
        log_event(
            args,
            team_name=args.team_name,
            event_type="orchestrate.waiting",
            command="orchestrate-debate",
            actor=effective_decider,
            entity_type="debate",
            entity_id=debate_id,
            metadata={"missing_members": missing},
        )
        print(f"{debate_id} waiting for positions from: {', '.join(missing)}")
        return

    decision = debate.get("decision")
    if not isinstance(decision, dict):
        selected, method, scores, _ = choose_decision(debate)
        debate["decision"] = {
            "at": utc_now(),
            "decider": effective_decider,
            "option": selected,
            "method": method,
            "scores": scores,
            "rationale": args.auto_rationale,
        }
        debate["status"] = "decided"
        debate["updated_at"] = utc_now()
        decision = debate["decision"]
        log_event(
            args,
            team_name=args.team_name,
            event_type="debate.decided",
            command="orchestrate-debate",
            actor=effective_decider,
            entity_type="debate",
            entity_id=debate_id,
            after={"option": selected, "method": method, "scores": scores, "status": debate["status"]},
            metadata={"rationale": args.auto_rationale},
        )

    selected_option = decision.get("option")
    if not isinstance(selected_option, str) or selected_option not in options:
        raise SystemExit(
            f"{debate_id} has invalid decision option; expected one of: "
            + ", ".join(str(option) for option in options)
        )

    has_linked_task = bool(isinstance(debate.get("task_id"), str) and debate.get("task_id"))
    if has_linked_task:
        owner_map = parse_owner_map(args.owner_map)
        validate_owner_map(
            owner_map,
            options=options,
            allowed_owners=team_members,
            context=f"Debate {debate_id}",
        )

    will_broadcast_apply = bool(
        isinstance(debate.get("task_id"), str)
        and debate.get("task_id")
        and not debate.get("applied")
    )
    notify_sender = resolve_notify_sender(
        team_name=args.team_name,
        requested_sender=args.notify_from,
        fallback_sender=effective_decider,
        reason="orchestrate-debate apply broadcast",
        required=will_broadcast_apply,
    )

    apply_note = apply_decision_to_task(
        args=args,
        team_name=args.team_name,
        debate=debate,
        selected_option=selected_option,
        selected_status=args.status_on_apply,
        owner_map=owner_map,
        applied_by=effective_decider,
        sender=notify_sender,
    )

    write_json(debate_file(args.team_name), board)
    log_event(
        args,
        team_name=args.team_name,
        event_type="orchestrate.completed",
        command="orchestrate-debate",
        actor=effective_decider,
        entity_type="debate",
        entity_id=debate_id,
        metadata={"selected_option": selected_option, "apply_note": apply_note},
    )
    print(
        f"{debate_id} orchestrated: option='{selected_option}' "
        f"status={debate.get('status')}"
    )
    print(apply_note)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Team operations for codex-agent-teams.")
    parser.add_argument(
        "--team-root",
        help=(
            "Override team storage root. Defaults to nearest ancestor containing '.codex' "
            "(or current directory) plus '/.codex/teams'. Can also be set via TEAM_OPS_ROOT."
        ),
    )
    parser.add_argument(
        "--monitoring",
        action="store_true",
        help="Enable monitoring event logging for this command run.",
    )
    parser.add_argument(
        "--monitor-log-file",
        help=(
            "Override monitor log path. Default: .codex/teams/<team>/monitor.jsonl. "
            "Can also be set via TEAM_OPS_MONITOR_LOG_FILE."
        ),
    )
    parser.add_argument(
        "--correlation-id",
        help="Optional correlation id for linking events across multiple command invocations.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Initialize a team workspace.")
    p_init.add_argument("--team-name", required=True)
    p_init.add_argument("--goal", required=True)
    p_init.add_argument("--members", required=True, help="Comma-separated members.")
    p_init.add_argument(
        "--reset",
        action="store_true",
        help="Explicitly reset existing team state (tasks/debates/messages/monitor).",
    )
    p_init.set_defaults(func=cmd_init)

    p_add = sub.add_parser("add-task", help="Add a task to the shared board.")
    p_add.add_argument("--team-name", required=True)
    p_add.add_argument("--title", required=True)
    p_add.add_argument("--owner", default="unassigned")
    p_add.add_argument("--status", default="pending")
    p_add.add_argument("--depends-on", default="")
    p_add.set_defaults(func=cmd_add_task)

    p_claim = sub.add_parser("claim", help="Claim a task and mark in progress.")
    p_claim.add_argument("--team-name", required=True)
    p_claim.add_argument("--task-id", required=True)
    p_claim.add_argument("--member", required=True)
    p_claim.set_defaults(func=cmd_claim)

    p_update = sub.add_parser("update-task", help="Update task state, owner, deps, notes.")
    p_update.add_argument("--team-name", required=True)
    p_update.add_argument("--task-id", required=True)
    p_update.add_argument("--status")
    p_update.add_argument("--owner")
    p_update.add_argument("--depends-on")
    p_update.add_argument("--note")
    p_update.set_defaults(func=cmd_update_task)

    p_list = sub.add_parser("list-tasks", help="List tasks from the board.")
    p_list.add_argument("--team-name", required=True)
    p_list.set_defaults(func=cmd_list_tasks)

    p_msg = sub.add_parser("message", help="Send direct teammate message.")
    p_msg.add_argument("--team-name", required=True)
    p_msg.add_argument("--from", dest="sender", required=True)
    p_msg.add_argument("--to", required=True)
    p_msg.add_argument("--body", required=True)
    p_msg.set_defaults(func=cmd_message)

    p_brd = sub.add_parser("broadcast", help="Send broadcast message to all teammates.")
    p_brd.add_argument("--team-name", required=True)
    p_brd.add_argument("--from", dest="sender", required=True)
    p_brd.add_argument("--body", required=True)
    p_brd.set_defaults(func=cmd_broadcast)

    p_inbox = sub.add_parser("inbox", help="Read a teammate inbox.")
    p_inbox.add_argument("--team-name", required=True)
    p_inbox.add_argument("--member", required=True)
    p_inbox.set_defaults(func=cmd_inbox)

    p_start_debate = sub.add_parser(
        "start-debate", help="Create a debate to resolve competing approaches."
    )
    p_start_debate.add_argument("--team-name", required=True)
    p_start_debate.add_argument("--topic", required=True)
    p_start_debate.add_argument("--task-id")
    p_start_debate.add_argument("--options", required=True, help="Comma-separated options.")
    p_start_debate.add_argument("--members", required=True, help="Comma-separated members.")
    p_start_debate.add_argument("--decider", default="lead")
    p_start_debate.add_argument("--notify", action="store_true")
    p_start_debate.add_argument("--notify-from", default="lead")
    p_start_debate.set_defaults(func=cmd_start_debate)

    p_add_position = sub.add_parser(
        "add-position", help="Submit a member position for a debate option."
    )
    p_add_position.add_argument("--team-name", required=True)
    p_add_position.add_argument("--debate-id", required=True)
    p_add_position.add_argument("--member", required=True)
    p_add_position.add_argument("--option", required=True)
    p_add_position.add_argument("--confidence", type=float, default=1.0)
    p_add_position.add_argument("--rationale", required=True)
    p_add_position.set_defaults(func=cmd_add_position)

    p_list_debates = sub.add_parser("list-debates", help="List debates.")
    p_list_debates.add_argument("--team-name", required=True)
    p_list_debates.add_argument("--status", choices=sorted(DEBATE_STATUSES))
    p_list_debates.set_defaults(func=cmd_list_debates)

    p_show_debate = sub.add_parser("show-debate", help="Show one debate in JSON.")
    p_show_debate.add_argument("--team-name", required=True)
    p_show_debate.add_argument("--debate-id", required=True)
    p_show_debate.set_defaults(func=cmd_show_debate)

    p_monitor = sub.add_parser("monitor-report", help="Summarize monitor events for a team.")
    p_monitor.add_argument("--team-name", required=True)
    p_monitor.add_argument("--output", help="Optional JSON output path.")
    p_monitor.set_defaults(func=cmd_monitor_report)

    p_decide_debate = sub.add_parser(
        "decide-debate", help="Finalize debate decision and optionally apply to task."
    )
    p_decide_debate.add_argument("--team-name", required=True)
    p_decide_debate.add_argument("--debate-id", required=True)
    p_decide_debate.add_argument(
        "--decider",
        default=None,
        help="Decision actor. Defaults to the decider registered on the debate.",
    )
    p_decide_debate.add_argument("--decision", help="Manual winning option.")
    p_decide_debate.add_argument(
        "--rationale",
        help="Decision rationale. Required when recording a new decision.",
    )
    p_decide_debate.add_argument("--require-all-positions", action="store_true")
    p_decide_debate.add_argument("--apply", action="store_true")
    p_decide_debate.add_argument("--status-on-apply", default="in_progress")
    p_decide_debate.add_argument("--owner-map", default="")
    p_decide_debate.add_argument("--notify-from", default="lead")
    p_decide_debate.set_defaults(func=cmd_decide_debate)

    p_orchestrate = sub.add_parser(
        "orchestrate-debate",
        help=(
            "Automatic loop: create or load debate, remind missing members, "
            "auto-decide when ready, and apply decision to linked task."
        ),
    )
    p_orchestrate.add_argument("--team-name", required=True)
    p_orchestrate.add_argument("--debate-id")
    p_orchestrate.add_argument("--topic")
    p_orchestrate.add_argument("--task-id")
    p_orchestrate.add_argument("--options", default="")
    p_orchestrate.add_argument("--members", default="")
    p_orchestrate.add_argument("--decider", default="lead")
    p_orchestrate.add_argument("--status-on-apply", default="in_progress")
    p_orchestrate.add_argument("--owner-map", default="")
    p_orchestrate.add_argument("--auto-rationale", default="Auto-selected by weighted confidence score.")
    p_orchestrate.add_argument("--send-reminders", action="store_true")
    p_orchestrate.add_argument("--notify-from", default="lead")
    p_orchestrate.set_defaults(func=cmd_orchestrate_debate)

    # Allow monitoring flags both before and after subcommand for ergonomic CLI usage.
    for child in sub.choices.values():
        if "--team-root" not in child._option_string_actions:
            child.add_argument(
                "--team-root",
                default=argparse.SUPPRESS,
                help=(
                    "Override team storage root. Defaults to nearest ancestor containing "
                    "'.codex' (or current directory) plus '/.codex/teams'. "
                    "Can also be set via TEAM_OPS_ROOT."
                ),
            )
        if "--monitoring" not in child._option_string_actions:
            child.add_argument(
                "--monitoring",
                action="store_true",
                default=argparse.SUPPRESS,
                help="Enable monitoring event logging for this command run.",
            )
        if "--monitor-log-file" not in child._option_string_actions:
            child.add_argument(
                "--monitor-log-file",
                default=argparse.SUPPRESS,
                help=(
                    "Override monitor log path. Default: .codex/teams/<team>/monitor.jsonl. "
                    "Can also be set via TEAM_OPS_MONITOR_LOG_FILE."
                ),
            )
        if "--correlation-id" not in child._option_string_actions:
            child.add_argument(
                "--correlation-id",
                default=argparse.SUPPRESS,
                help="Optional correlation id for linking events across command invocations.",
            )

    return parser


def main() -> int:
    global TEAM_ROOT, TEAM_ROOT_IS_EXPLICIT
    parser = build_parser()
    args = parser.parse_args()
    TEAM_ROOT_IS_EXPLICIT = bool(getattr(args, "team_root", None) or os.getenv(TEAM_ROOT_ENV))
    TEAM_ROOT = resolve_team_root(getattr(args, "team_root", None))
    ensure_team_root_usable(TEAM_ROOT)
    args._correlation_id = args.correlation_id or uuid.uuid4().hex

    if args.command == "orchestrate-debate" and not args.debate_id:
        if not args.topic:
            raise SystemExit("--topic is required when --debate-id is not provided.")
        if not args.options:
            raise SystemExit("--options is required when --debate-id is not provided.")
        if not args.members:
            raise SystemExit("--members is required when --debate-id is not provided.")

    mutating_commands = {
        "init",
        "add-task",
        "claim",
        "update-task",
        "message",
        "broadcast",
        "start-debate",
        "add-position",
        "decide-debate",
        "orchestrate-debate",
    }
    if args.command in mutating_commands:
        # Some commands can auto-switch TEAM_ROOT via require_team(). Resolve that
        # before locking so the lock is taken at the final canonical root.
        if args.command != "init":
            require_team(args.team_name)
        with team_state_lock(args.team_name):
            args.func(args)
    else:
        args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
