#!/usr/bin/env python3
"""Team operations for codex-agent-teams.

Provides simple shared-state primitives:
- team initialization
- task board management
- task claiming and status updates
- direct teammate messaging and broadcast inbox
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

TEAM_ROOT = Path(".codex/teams")
TASK_STATUSES = {"pending", "in_progress", "completed"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def team_dir(team_name: str) -> Path:
    return TEAM_ROOT / team_name


def team_file(team_name: str) -> Path:
    return team_dir(team_name) / "team.json"


def task_file(team_name: str) -> Path:
    return team_dir(team_name) / "tasks.json"


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
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload) + "\n")


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
    message_file(args.team_name).touch(exist_ok=True)
    print(f"Initialized team '{args.team_name}' at {tdir}")


def cmd_add_task(args: argparse.Namespace) -> None:
    require_team(args.team_name)
    if args.status not in TASK_STATUSES:
        raise SystemExit(f"--status must be one of: {', '.join(sorted(TASK_STATUSES))}")
    board = load_json(task_file(args.team_name), {"tasks": [], "next_id": 1})
    assert isinstance(board, dict)
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
    board["tasks"].append(task)
    board["next_id"] += 1
    write_json(task_file(args.team_name), board)
    print(f"Added {task_id}: {args.title}")


def find_task(board: dict[str, object], task_id: str) -> dict[str, object]:
    tasks = board.get("tasks", [])
    assert isinstance(tasks, list)
    for task in tasks:
        if isinstance(task, dict) and task.get("id") == task_id:
            return task
    raise SystemExit(f"Task not found: {task_id}")


def cmd_claim(args: argparse.Namespace) -> None:
    require_team(args.team_name)
    board = load_json(task_file(args.team_name), {"tasks": [], "next_id": 1})
    assert isinstance(board, dict)
    task = find_task(board, args.task_id)
    status = task.get("status")
    if status == "completed":
        raise SystemExit(f"Cannot claim completed task: {args.task_id}")
    task["owner"] = args.member
    task["status"] = "in_progress"
    task["updated_at"] = utc_now()
    write_json(task_file(args.team_name), board)
    print(f"{args.member} claimed {args.task_id}")


def cmd_update_task(args: argparse.Namespace) -> None:
    require_team(args.team_name)
    if args.status and args.status not in TASK_STATUSES:
        raise SystemExit(f"--status must be one of: {', '.join(sorted(TASK_STATUSES))}")
    board = load_json(task_file(args.team_name), {"tasks": [], "next_id": 1})
    assert isinstance(board, dict)
    task = find_task(board, args.task_id)
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
    print(f"Updated {args.task_id}")


def cmd_list_tasks(args: argparse.Namespace) -> None:
    require_team(args.team_name)
    board = load_json(task_file(args.team_name), {"tasks": [], "next_id": 1})
    assert isinstance(board, dict)
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
            print(
                f"[{payload.get('at')}] {payload.get('from')} -> {target}: {payload.get('body')}"
            )
    if not found:
        print(f"No inbox messages for {args.member}.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Team operations for codex-agent-teams.")
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

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
