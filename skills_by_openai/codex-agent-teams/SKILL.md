---
name: codex-agent-teams
description: Use when coordinating 2+ specialized Codex sub-agents on one objective, especially for parallel workstreams, competing implementation options, or split implementation and review lanes that need explicit handoffs.
---

# Codex Agent Teams

## Overview

Run one lead agent and multiple specialist teammates to emulate Anthropic Agent Teams patterns in Codex. Keep teammates narrowly scoped, use explicit communication and task-state protocols, and converge through shared quality gates.

This skill mirrors documented Anthropic patterns such as:
- shared task list with `pending`, `in_progress`, `completed`
- teammates claiming independent tasks
- direct teammate messaging and broadcasts
- explicit debate lifecycle (`start-debate` -> `add-position` -> `decide`/`apply`)
- automatic debate loop orchestration with decision-to-task reflection
- explicit approval checkpoints before implementation
- delegate mode where lead coordinates and does not code

See `references/anthropic-pattern-map.md` for native-vs-emulated feature mapping.

## Routing Gate

Choose the execution mode before dispatch:
- `single-agent`: one clear change, no coordination benefit.
- `single-subagent`: moderate scope, one main workstream.
- `agent-teams`: 2+ independent workstreams, high ambiguity, or strong parallelism benefit.

Promote from `single-subagent` to `agent-teams` if scope expands across subsystems or repeated blockers appear.

## Startup Sequence

### 1) Team bootstrap

Initialize a shared team workspace:

```bash
python3 scripts/team_ops.py init \
  --team-name "<team-name>" \
  --goal "<goal>" \
  --members "lead,implementer-a,implementer-b,reviewer"
```

### 2) Charter and initial backlog

Create team charter and seed workstreams:

```bash
python3 scripts/create_team_brief.py \
  --team-name "<team-name>" \
  --goal "<goal>" \
  --workstreams "stream-a,stream-b" \
  --roles "lead,implementer,reviewer,tester" \
  --communication-mode "direct" \
  --delegate-mode \
  --output ".codex/teams/<team-name>/brief.md"
```

For each work item:

```bash
python3 scripts/team_ops.py add-task \
  --team-name "<team-name>" \
  --title "Implement X" \
  --owner "unassigned" \
  --status "pending" \
  --depends-on ""
```

### 3) Approval checkpoint

Before coding, present team plan and wait for explicit user approval.
For strict planning phases, pair with:
- `superpowers:brainstorming`
- `superpowers:writing-plans`

### 4) Optional delegate mode

If delegate mode is on, lead does orchestration only:
- assign and re-balance work
- resolve cross-stream conflicts
- enforce convergence gates
- avoid direct code edits unless user asks

## Communication Protocol

Use shared mailbox commands to emulate direct teammate communication:

Send direct message:

```bash
python3 scripts/team_ops.py message \
  --team-name "<team-name>" \
  --from "implementer-a" \
  --to "reviewer" \
  --body "Please review task-3 patch and edge-case notes."
```

Broadcast update:

```bash
python3 scripts/team_ops.py broadcast \
  --team-name "<team-name>" \
  --from "lead" \
  --body "Task dependency changed: api-schema now blocks frontend-sync."
```

Read inbox:

```bash
python3 scripts/team_ops.py inbox --team-name "<team-name>" --member "reviewer"
```

Rules:
- Every teammate reports decisions and blockers through messages.
- Critical dependency changes are broadcast to all members.
- Lead records final decisions in brief and task board.

## Task-State Protocol

State model:
- `pending`: not started or waiting on dependency
- `in_progress`: claimed by one teammate
- `completed`: done and verified

Claim task:

```bash
python3 scripts/team_ops.py claim \
  --team-name "<team-name>" \
  --task-id "task-2" \
  --member "implementer-b"
```

Update task status:

```bash
python3 scripts/team_ops.py update-task \
  --team-name "<team-name>" \
  --task-id "task-2" \
  --status "completed" \
  --note "Unit and integration checks passed."
```

List task board:

```bash
python3 scripts/team_ops.py list-tasks --team-name "<team-name>"
```

## Debate Protocol (Conflict to Decision)

When two or more approaches conflict, run a formal debate and persist the outcome.
Debates are stored in `.codex/teams/<team-name>/debates.json`.

Start a debate:

```bash
python3 scripts/team_ops.py start-debate \
  --team-name "<team-name>" \
  --topic "Choose retry strategy for API sync" \
  --task-id "task-2" \
  --options "fixed-backoff,exponential-backoff" \
  --members "implementer-a,implementer-b,reviewer" \
  --decider "lead" \
  --notify
```

Each member submits a position:

```bash
python3 scripts/team_ops.py add-position \
  --team-name "<team-name>" \
  --debate-id "debate-1" \
  --member "implementer-a" \
  --option "exponential-backoff" \
  --confidence 0.8 \
  --rationale "Lower p95 under burst failures."
```

Lead decides and applies to the linked task:

```bash
python3 scripts/team_ops.py decide-debate \
  --team-name "<team-name>" \
  --debate-id "debate-1" \
  --rationale "Best reliability/cost tradeoff from submitted evidence." \
  --require-all-positions \
  --apply \
  --status-on-apply "in_progress" \
  --owner-map "fixed-backoff:implementer-a,exponential-backoff:implementer-b"
```

Review debate state:

```bash
python3 scripts/team_ops.py list-debates --team-name "<team-name>"
python3 scripts/team_ops.py show-debate --team-name "<team-name>" --debate-id "debate-1"
```

## Automatic Orchestration Loop

Use one command to run the loop:
1. create/load debate
2. remind missing members
3. auto-decide when all positions are present (weighted confidence)
4. reflect decision into linked task (`status`, optional owner mapping, note, broadcast)

```bash
python3 scripts/team_ops.py orchestrate-debate \
  --team-name "<team-name>" \
  --topic "Choose cache invalidation strategy" \
  --task-id "task-4" \
  --options "ttl-only,event-driven" \
  --members "implementer-a,implementer-b,reviewer" \
  --decider "lead" \
  --send-reminders \
  --status-on-apply "in_progress" \
  --owner-map "ttl-only:implementer-a,event-driven:implementer-b"
```

Re-run `orchestrate-debate` until applied. It is idempotent once a decision is applied.

## Monitoring (Optional, Default OFF)

`team_ops.py` supports opt-in monitor logging for command-level observability.
Monitoring is disabled by default and no monitor file is created unless explicitly enabled.

Enable monitoring for a command:

```bash
python3 scripts/team_ops.py orchestrate-debate \
  --team-name "<team-name>" \
  --debate-id "debate-1" \
  --monitoring
```

Override monitor file path:

```bash
python3 scripts/team_ops.py update-task \
  --team-name "<team-name>" \
  --task-id "task-1" \
  --status "completed" \
  --monitoring \
  --monitor-log-file "/tmp/team-monitor.jsonl"
```

Environment alternatives:
- `TEAM_OPS_MONITORING=1`
- `TEAM_OPS_MONITOR_LOG_FILE=/path/to/monitor.jsonl`

Generate monitor summary report:

```bash
python3 scripts/team_ops.py monitor-report \
  --team-name "<team-name>" \
  --output ".codex/teams/<team-name>/monitor-report.json"
```

Monitoring event schema (`monitor.jsonl`, one JSON object per line):

| Field | Type | Description |
| --- | --- | --- |
| `at` | string (ISO-8601 UTC) | Event timestamp |
| `event_type` | string | Event name (for example `message.sent`, `debate.started`, `debate.applied`, `task.updated`) |
| `command` | string | `team_ops.py` command that emitted the event |
| `team_name` | string | Team identifier |
| `actor` | string | Member or system actor that performed the action |
| `entity_type` | string | Domain object type (`team`, `task`, `debate`, `message`) |
| `entity_id` | string | Object identifier (`task-1`, `debate-2`, `lead->reviewer`) |
| `before` | object or `null` | Snapshot before change (when applicable) |
| `after` | object or `null` | Snapshot after change (when applicable) |
| `metadata` | object | Additional event context (option, rationale, missing members, etc.) |
| `correlation_id` | string | Correlation id to group related command runs |

Example line:

```json
{"at":"2026-02-16T06:00:00+00:00","event_type":"debate.applied","command":"apply-decision","team_name":"example-team","actor":"lead","entity_type":"debate","entity_id":"debate-1","before":{"task":{"owner":"unassigned","status":"pending"}},"after":{"task":{"owner":"implementer-a","status":"in_progress"}},"metadata":{"task_id":"task-1","option":"session"},"correlation_id":"2e5db8f0d25d4f08b6f2f4f13a4ed8ad"}
```

## Superpowers Integration

Map workstreams to one control skill:
- plan refinement and ambiguity reduction: `superpowers:brainstorming` then `superpowers:writing-plans`
- independent implementation in this session: `superpowers:subagent-driven-development`
- multiple unrelated failures or investigations: `superpowers:dispatching-parallel-agents`
- execution from approved written plan in batches: `superpowers:executing-plans`
- isolated workspace setup: `superpowers:using-git-worktrees`
- pre-completion validation: `superpowers:verification-before-completion`
- final quality check: `superpowers:requesting-code-review`
- closeout: `superpowers:finishing-a-development-branch`

## Convergence Gates

Before merging teammate outputs:
1. Spec compliance gate
2. Code quality gate
3. Verification gate (`superpowers:verification-before-completion`)
4. Final review for major diffs (`superpowers:requesting-code-review`)
5. Branch closeout (`superpowers:finishing-a-development-branch`)

## Operating Rules

- Keep teammates specialized and replaceable.
- Prefer teams of 2 to 5 members.
- Keep one source of truth for status in team task board.
- Do not mark task complete without command output.
- Escalate unresolved blockers after two failed attempts.
- If two solutions conflict, start a debate and link it to the blocked task.
- Do not close debates without recorded rationale and selected option.
- Reflect debate outcomes into task state before resuming implementation.

## Limitations

This skill emulates Agent Teams patterns in Codex, but some native Anthropic UX features may not exist in every runtime. When unavailable, fall back to:
- explicit message commands via `scripts/team_ops.py`
- shared files for task board and decisions
- lead-mediated routing while preserving protocol semantics

## Resources

- `scripts/create_team_brief.py`: generate a reusable team charter and task board.
- `scripts/team_ops.py`: manage team members, task states, mailbox, debates, and auto-orchestration.
- `references/anthropic-pattern-map.md`: what is mirrored from Anthropic docs.
- `references/team-topologies.md`: choose collaboration pattern by coupling and risk profile.
- `references/prompt-templates.md`: copy/paste lead, specialist, reviewer, and challenger prompts.
