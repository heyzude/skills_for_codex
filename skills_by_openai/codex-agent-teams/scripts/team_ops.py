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
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

TEAM_ROOT = Path(".codex/teams")
TASK_STATUSES = {"pending", "in_progress", "completed"}
DEBATE_STATUSES = {"open", "decided", "applied"}
MONITORING_ENV = "TEAM_OPS_MONITORING"
MONITOR_LOG_ENV = "TEAM_OPS_MONITOR_LOG_FILE"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


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
        mapping[option] = owner
    return mapping


def team_dir(team_name: str) -> Path:
    return TEAM_ROOT / team_name


def team_file(team_name: str) -> Path:
    return team_dir(team_name) / "team.json"


def task_file(team_name: str) -> Path:
    return team_dir(team_name) / "tasks.json"


def debate_file(team_name: str) -> Path:
    return team_dir(team_name) / "debates.json"


def message_file(team_name: str) -> Path:
    return team_dir(team_name) / "messages.jsonl"


def require_team(team_name: str) -> None:
    if not team_file(team_name).exists():
        raise SystemExit(
            f"Team '{team_name}' is not initialized. Run: team_ops.py init --team-name {team_name} ..."
        )


def load_json(path: Path, default: object) -> object:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def env_flag_true(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def is_monitoring_enabled(args: argparse.Namespace) -> bool:
    return bool(getattr(args, "monitoring", False) or env_flag_true(MONITORING_ENV))


def resolve_monitor_path(args: argparse.Namespace, team_name: str) -> Path:
    custom = getattr(args, "monitor_log_file", None) or os.getenv(MONITOR_LOG_ENV)
    if custom:
        return Path(custom)
    return team_dir(team_name) / "monitor.jsonl"


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
    assert isinstance(board, dict)
    return board


def load_debate_board(team_name: str) -> dict[str, object]:
    board = load_json(debate_file(team_name), {"debates": [], "next_id": 1})
    assert isinstance(board, dict)
    return board


def find_task(board: dict[str, object], task_id: str) -> dict[str, object]:
    tasks = board.get("tasks", [])
    assert isinstance(tasks, list)
    for task in tasks:
        if isinstance(task, dict) and task.get("id") == task_id:
            return task
    raise SystemExit(f"Task not found: {task_id}")


def find_debate(board: dict[str, object], debate_id: str) -> dict[str, object]:
    debates = board.get("debates", [])
    assert isinstance(debates, list)
    for debate in debates:
        if isinstance(debate, dict) and debate.get("id") == debate_id:
            return debate
    raise SystemExit(f"Debate not found: {debate_id}")


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
    options = debate.get("options", [])
    assert isinstance(options, list)
    option_set = {opt for opt in options if isinstance(opt, str)}
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


def create_debate(
    team_name: str,
    topic: str,
    task_id: str | None,
    options: list[str],
    members: list[str],
    decider: str,
) -> dict[str, object]:
    if len(options) < 2:
        raise SystemExit("Debate requires at least two options.")
    if len(members) < 2:
        raise SystemExit("Debate requires at least two members.")

    if task_id:
        find_task(load_task_board(team_name), task_id)

    board = load_debate_board(team_name)
    debate_id = f"debate-{board['next_id']}"
    debate = {
        "id": debate_id,
        "topic": topic,
        "task_id": task_id,
        "options": sorted(set(options)),
        "members": sorted(set(members)),
        "decider": decider,
        "status": "open",
        "positions": [],
        "decision": None,
        "applied": None,
        "created_at": utc_now(),
        "updated_at": utc_now(),
    }
    debates = board.get("debates")
    assert isinstance(debates, list)
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
    tdir = team_dir(args.team_name)
    tdir.mkdir(parents=True, exist_ok=True)
    team_payload = {
        "team_name": args.team_name,
        "goal": args.goal,
        "members": members,
        "created_at": utc_now(),
    }
    write_json(team_file(args.team_name), team_payload)
    write_json(task_file(args.team_name), {"tasks": [], "next_id": 1})
    write_json(debate_file(args.team_name), {"debates": [], "next_id": 1})
    message_file(args.team_name).touch(exist_ok=True)
    log_event(
        args,
        team_name=args.team_name,
        event_type="team.initialized",
        command="init",
        actor="lead",
        entity_type="team",
        entity_id=args.team_name,
        after={"members": members, "goal": args.goal},
    )
    print(f"Initialized team '{args.team_name}' at {tdir}")


def cmd_add_task(args: argparse.Namespace) -> None:
    require_team(args.team_name)
    if args.status not in TASK_STATUSES:
        raise SystemExit(f"--status must be one of: {', '.join(sorted(TASK_STATUSES))}")
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
    assert isinstance(tasks, list)
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
    assert isinstance(tasks, list)
    if not tasks:
        print("No tasks.")
        return
    for task in tasks:
        assert isinstance(task, dict)
        deps = ",".join(task.get("depends_on", [])) if task.get("depends_on") else "-"
        print(
            f"{task.get('id')}  {task.get('status')}  owner={task.get('owner')}  deps={deps}  {task.get('title')}"
        )


def cmd_message(args: argparse.Namespace) -> None:
    require_team(args.team_name)
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
    mfile = message_file(args.team_name)
    if not mfile.exists():
        print("No messages.")
        return
    lines = mfile.read_text(encoding="utf-8").splitlines()
    found = False
    for line in lines:
        payload = json.loads(line)
        mtype = payload.get("type")
        target = payload.get("to")
        if mtype == "broadcast" or target == args.member:
            found = True
            print(f"[{payload.get('at')}] {payload.get('from')} -> {target}: {payload.get('body')}")
    if not found:
        print(f"No inbox messages for {args.member}.")


def cmd_start_debate(args: argparse.Namespace) -> None:
    require_team(args.team_name)
    options = parse_csv(args.options)
    members = parse_csv(args.members)
    decider = args.decider or "lead"

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
                    "from": args.notify_from,
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
                actor=args.notify_from,
                entity_type="message",
                entity_id=f"{args.notify_from}->{member}",
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
    if args.confidence < 0 or args.confidence > 1:
        raise SystemExit("--confidence must be between 0 and 1.")

    board = load_debate_board(args.team_name)
    debate = find_debate(board, args.debate_id)

    if debate.get("status") not in {"open", "decided"}:
        raise SystemExit(
            f"Debate {args.debate_id} is {debate.get('status')}; cannot add positions."
        )

    members = debate.get("members", [])
    options = debate.get("options", [])
    assert isinstance(members, list)
    assert isinstance(options, list)

    if args.member not in members:
        raise SystemExit(f"Member '{args.member}' is not registered for {args.debate_id}.")
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
    assert isinstance(debates, list)

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
        members = debate.get("members", [])
        assert isinstance(members, list)
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
    for line in monitor_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        events.append(json.loads(line))

    message_count = 0
    debate_ids: set[str] = set()
    debate_applied_count = 0
    task_reflection_count = 0
    orchestrate_wait_cycles = 0
    start_times: dict[str, datetime] = {}
    latencies: list[float] = []

    for event in events:
        etype = str(event.get("event_type"))
        entity_id = str(event.get("entity_id"))
        if etype.startswith("message."):
            message_count += 1
        if etype.startswith("debate."):
            debate_ids.add(entity_id)
        if etype == "debate.started":
            debate_ids.add(entity_id)
            at_raw = event.get("at")
            if isinstance(at_raw, str):
                try:
                    start_times[entity_id] = datetime.fromisoformat(at_raw)
                except ValueError:
                    pass
        if etype == "debate.applied":
            debate_applied_count += 1
            task_reflection_count += 1
            at_raw = event.get("at")
            if isinstance(at_raw, str) and entity_id in start_times:
                try:
                    applied_at = datetime.fromisoformat(at_raw)
                    latencies.append((applied_at - start_times[entity_id]).total_seconds())
                except ValueError:
                    pass
        if etype == "orchestrate.waiting":
            orchestrate_wait_cycles += 1

    payload = {
        "team_name": args.team_name,
        "monitor_file": str(monitor_path),
        "events_total": len(events),
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
    owner_map = parse_owner_map(args.owner_map)
    board = load_debate_board(args.team_name)
    debate = find_debate(board, args.debate_id)

    status = debate.get("status")
    if status == "applied":
        print(f"{args.debate_id} is already applied.")
        return

    options = debate.get("options", [])
    assert isinstance(options, list)

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
        members = debate.get("members", [])
        assert isinstance(members, list)
        missing = sorted(set(members) - set(latest))
        if missing:
            raise SystemExit(
                "Cannot decide yet; missing positions from: " + ", ".join(missing)
            )

    debate["decision"] = {
        "at": utc_now(),
        "decider": args.decider,
        "option": selected,
        "method": method,
        "scores": scores,
        "rationale": args.rationale,
    }
    debate["status"] = "decided"
    debate["updated_at"] = utc_now()
    log_event(
        args,
        team_name=args.team_name,
        event_type="debate.decided",
        command="decide-debate",
        actor=args.decider,
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
        apply_note = apply_decision_to_task(
            args=args,
            team_name=args.team_name,
            debate=debate,
            selected_option=selected,
            selected_status=args.status_on_apply,
            owner_map=owner_map,
            applied_by=args.decider,
            sender=args.notify_from,
        )

    write_json(debate_file(args.team_name), board)
    print(
        f"Decided {args.debate_id}: option='{selected}' method={method} "
        f"status={debate.get('status')}"
    )
    if apply_note:
        print(apply_note)


def cmd_orchestrate_debate(args: argparse.Namespace) -> None:
    require_team(args.team_name)
    owner_map = parse_owner_map(args.owner_map)

    board = load_debate_board(args.team_name)

    debate: dict[str, object]
    if args.debate_id:
        debate = find_debate(board, args.debate_id)
    else:
        options = parse_csv(args.options)
        members = parse_csv(args.members)
        decider = args.decider or "lead"
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

    members = debate.get("members", [])
    assert isinstance(members, list)
    latest = latest_positions(debate)
    missing = sorted(set(members) - set(latest))

    if missing:
        if args.send_reminders:
            for member in missing:
                append_jsonl(
                    message_file(args.team_name),
                    {
                        "at": utc_now(),
                        "type": "direct",
                        "from": args.notify_from,
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
                    actor=args.notify_from,
                    entity_type="message",
                    entity_id=f"{args.notify_from}->{member}",
                    metadata={"debate_id": debate_id, "reason": "missing_position"},
                )
        log_event(
            args,
            team_name=args.team_name,
            event_type="orchestrate.waiting",
            command="orchestrate-debate",
            actor=args.decider or "lead",
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
            "decider": debate.get("decider") or args.decider,
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
            actor=str(debate.get("decider") or args.decider or "lead"),
            entity_type="debate",
            entity_id=debate_id,
            after={"option": selected, "method": method, "scores": scores, "status": debate["status"]},
            metadata={"rationale": args.auto_rationale},
        )

    selected_option = decision.get("option")
    if not isinstance(selected_option, str) or not selected_option:
        raise SystemExit(f"{debate_id} has invalid decision option.")

    apply_note = apply_decision_to_task(
        args=args,
        team_name=args.team_name,
        debate=debate,
        selected_option=selected_option,
        selected_status=args.status_on_apply,
        owner_map=owner_map,
        applied_by=args.decider or str(debate.get("decider") or "lead"),
        sender=args.notify_from,
    )

    write_json(debate_file(args.team_name), board)
    log_event(
        args,
        team_name=args.team_name,
        event_type="orchestrate.completed",
        command="orchestrate-debate",
        actor=args.decider or str(debate.get("decider") or "lead"),
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
    p_decide_debate.add_argument("--decider", default="lead")
    p_decide_debate.add_argument("--decision", help="Manual winning option.")
    p_decide_debate.add_argument("--rationale", required=True)
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
        if "--monitoring" not in child._option_string_actions:
            child.add_argument(
                "--monitoring",
                action="store_true",
                help="Enable monitoring event logging for this command run.",
            )
        if "--monitor-log-file" not in child._option_string_actions:
            child.add_argument(
                "--monitor-log-file",
                help=(
                    "Override monitor log path. Default: .codex/teams/<team>/monitor.jsonl. "
                    "Can also be set via TEAM_OPS_MONITOR_LOG_FILE."
                ),
            )
        if "--correlation-id" not in child._option_string_actions:
            child.add_argument(
                "--correlation-id",
                help="Optional correlation id for linking events across command invocations.",
            )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args._correlation_id = args.correlation_id or uuid.uuid4().hex

    if args.command == "orchestrate-debate" and not args.debate_id:
        if not args.topic:
            raise SystemExit("--topic is required when --debate-id is not provided.")
        if not args.options:
            raise SystemExit("--options is required when --debate-id is not provided.")
        if not args.members:
            raise SystemExit("--members is required when --debate-id is not provided.")

    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
