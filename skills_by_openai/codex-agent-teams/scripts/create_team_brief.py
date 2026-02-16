#!/usr/bin/env python3
"""Generate a markdown team charter for the codex-agent-teams skill."""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path


ROLE_PRESETS = {
    "lead": (
        "Coordinate priorities, unblock teammates, and integrate final output.",
        "Plan, decision log, final integration summary",
    ),
    "implementer": (
        "Deliver scoped code or artifact changes for assigned workstreams.",
        "Diff summary, verification notes",
    ),
    "researcher": (
        "Collect evidence and reduce ambiguity before implementation.",
        "Findings memo, option matrix",
    ),
    "reviewer": (
        "Check spec alignment, quality, and regression risk.",
        "Review verdict, required fixes",
    ),
    "challenger": (
        "Stress-test assumptions and propose safer alternatives.",
        "Risk critique, alternative proposal",
    ),
    "tester": (
        "Run verification commands and report reproducible outcomes.",
        "Test report, failing scenarios",
    ),
}

TOPOLOGY_NOTES = {
    "lead-hub": "Lead assigns and integrates independent specialists.",
    "pipeline": "Each role hands output to the next role in sequence.",
    "red-blue": "Two competing implementers plus one adjudicator.",
    "parallel-pods": "Multiple small pods each own a subsystem boundary.",
}

DEFAULT_DONE = [
    "All workstreams have a named owner and status.",
    "Verification commands pass for changed scope.",
    "Open risks and follow-ups are recorded.",
]

DEFAULT_SKILLS = [
    "superpowers:brainstorming",
    "superpowers:writing-plans",
    "superpowers:subagent-driven-development",
    "superpowers:dispatching-parallel-agents",
    "superpowers:verification-before-completion",
]


def parse_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def role_rows(roles: list[str]) -> str:
    rows: list[str] = []
    for role in roles:
        mission, deliverables = ROLE_PRESETS.get(
            role,
            ("Define mission for this role.", "Define expected deliverables."),
        )
        rows.append(f"| {role} | {mission} | {deliverables} |")
    return "\n".join(rows)


def workstream_rows(workstreams: list[str], verification: list[str]) -> str:
    verify = "; ".join(verification) if verification else "TBD"
    return "\n".join(
        f"| {workstream} | TBD | planned | {verify} |" for workstream in workstreams
    )


def markdown_list(items: list[str], empty_value: str) -> str:
    if not items:
        return f"- {empty_value}"
    return "\n".join(f"- {item}" for item in items)


def build_brief(
    team_name: str,
    goal: str,
    topology: str,
    workstreams: list[str],
    roles: list[str],
    done_criteria: list[str],
    constraints: list[str],
    verification: list[str],
    skill_refs: list[str],
    communication_mode: str,
    delegate_mode: bool,
) -> str:
    topology_note = TOPOLOGY_NOTES[topology]
    delegate_mode_value = "enabled" if delegate_mode else "disabled"
    return f"""# Team Brief: {team_name}

Date: {date.today().isoformat()}

## Goal
{goal}

## Topology
- Choice: {topology}
- Note: {topology_note}

## Team Modes
- Communication: {communication_mode}
- Delegate mode: {delegate_mode_value}

## Definition of Done
{markdown_list(done_criteria, "Define completion criteria.")}

## Constraints
{markdown_list(constraints, "No additional constraints supplied.")}

## Skill Links
{markdown_list(skill_refs, "No skill links supplied.")}

## Roles
| Role | Mission | Deliverables |
| --- | --- | --- |
{role_rows(roles)}

## Workstreams
| Workstream | Owner | Status | Verification |
| --- | --- | --- | --- |
{workstream_rows(workstreams, verification)}

## Handoff Protocol
1. Lead posts assignment with owner, boundary, and deadline.
2. Specialist returns summary, touched scope, verification output, and risks.
3. Reviewer validates spec and quality before status changes to complete.
4. Lead records decision and next action in this brief.

## Debate and Reflection Protocol
1. For conflicting approaches, open a formal debate linked to the blocked task.
2. Require one position per debate member with option, rationale, and confidence.
3. Decide with explicit rationale, then apply outcome to task status/owner.
4. Broadcast the chosen path and resume implementation on the linked task.

## Decision Log
- [ ] Decision:
  Context:
  Chosen option:
  Why:
  Follow-up:
"""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a markdown team brief for codex-agent-teams."
    )
    parser.add_argument("--team-name", required=True, help="Team label.")
    parser.add_argument("--goal", required=True, help="Primary objective.")
    parser.add_argument(
        "--topology",
        choices=sorted(TOPOLOGY_NOTES),
        default="lead-hub",
        help="Team collaboration topology.",
    )
    parser.add_argument(
        "--workstreams",
        required=True,
        help="Comma-separated workstream list.",
    )
    parser.add_argument(
        "--roles",
        default="lead,implementer,reviewer",
        help="Comma-separated role list.",
    )
    parser.add_argument(
        "--done-criteria",
        help="Comma-separated definition of done items.",
    )
    parser.add_argument(
        "--constraints",
        help="Comma-separated constraints.",
    )
    parser.add_argument(
        "--verification",
        help="Comma-separated verification commands or checks.",
    )
    parser.add_argument(
        "--skill-refs",
        help="Comma-separated skill references to include.",
    )
    parser.add_argument(
        "--communication-mode",
        choices=["lead-mediated", "direct"],
        default="lead-mediated",
        help="How teammates exchange updates.",
    )
    parser.add_argument(
        "--delegate-mode",
        action="store_true",
        help="Mark the brief as orchestration-only for the lead.",
    )
    parser.add_argument(
        "--output",
        help="Write markdown to this path. Print to stdout when omitted.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    workstreams = parse_csv(args.workstreams)
    roles = parse_csv(args.roles)
    done_criteria = parse_csv(args.done_criteria) or DEFAULT_DONE
    constraints = parse_csv(args.constraints)
    verification = parse_csv(args.verification)
    skill_refs = parse_csv(args.skill_refs) or DEFAULT_SKILLS

    if not workstreams:
        raise SystemExit("--workstreams must include at least one item.")
    if not roles:
        raise SystemExit("--roles must include at least one item.")

    content = build_brief(
        team_name=args.team_name,
        goal=args.goal,
        topology=args.topology,
        workstreams=workstreams,
        roles=roles,
        done_criteria=done_criteria,
        constraints=constraints,
        verification=verification,
        skill_refs=skill_refs,
        communication_mode=args.communication_mode,
        delegate_mode=args.delegate_mode,
    )

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
    else:
        print(content)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
