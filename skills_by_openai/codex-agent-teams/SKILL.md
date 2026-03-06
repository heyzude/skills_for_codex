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

### 0) Path-safe command setup

Set script paths once so commands work from any directory:

```bash
TEAM_SKILL_DIR="${TEAM_SKILL_DIR:-$HOME/.agents/skills/skills_by_openai/codex-agent-teams}"
TEAM_OPS_SCRIPT="$TEAM_SKILL_DIR/scripts/team_ops.py"
TEAM_BRIEF_SCRIPT="$TEAM_SKILL_DIR/scripts/create_team_brief.py"
# Optional explicit override. Leave unset to use auto-discovery.
# TEAM_OPS_ROOT="/absolute/path/to/<workspace>/.codex/teams"
```

`team_ops.py` auto-discovers the nearest `.codex` workspace root when `TEAM_OPS_ROOT` is not set.
Set `TEAM_OPS_ROOT` only when you intentionally want a non-default storage location.

### 1) Team bootstrap

Initialize a shared team workspace:

```bash
python3 "$TEAM_OPS_SCRIPT" init \
  --team-name "<team-name>" \
  --goal "<goal>" \
  --members "lead,implementer-a,implementer-b,reviewer"
```

If the team already exists, `init` is a no-op by default.
Use `--reset` only when you intentionally want to wipe and recreate team state:

```bash
python3 "$TEAM_OPS_SCRIPT" init \
  --team-name "<team-name>" \
  --goal "<goal>" \
  --members "lead,implementer-a,implementer-b,reviewer" \
  --reset
```

If team metadata is partial/corrupted, `init` automatically recovers state even without `--reset`.

### 2) Charter and initial backlog

Create team charter and seed workstreams:

```bash
python3 "$TEAM_BRIEF_SCRIPT" \
  --team-name "<team-name>" \
  --goal "<goal>" \
  --workstreams "stream-a,stream-b" \
  --roles "lead,implementer,reviewer,tester" \
  --communication-mode "direct" \
  --delegate-mode \
  --output "<teams-root>/<team-name>/brief.md"
```

`<teams-root>` is the same root used by `team_ops.py`:
- explicit `TEAM_OPS_ROOT`, if set
- otherwise the auto-discovered `<workspace>/.codex/teams`

For each work item:

```bash
python3 "$TEAM_OPS_SCRIPT" add-task \
  --team-name "<team-name>" \
  --title "Implement X" \
  --owner "unassigned" \
  --status "pending" \
  --depends-on ""
```

Role prompt hand-off (manual, no auto-generator):
- Build per-role prompts from `references/prompt-templates.md` by filling placeholders from `brief.md`, task ownership, and current debate/task state.
- `team_ops.py` manages state/logs only; it does not synthesize role prompts.
- For traceability, include role id, owned scope, required outputs, and exact `team_ops.py` commands each sub-agent should run.

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
python3 "$TEAM_OPS_SCRIPT" message \
  --team-name "<team-name>" \
  --from "implementer-a" \
  --to "reviewer" \
  --body "Please review task-3 patch and edge-case notes."
```

Broadcast update:

```bash
python3 "$TEAM_OPS_SCRIPT" broadcast \
  --team-name "<team-name>" \
  --from "lead" \
  --body "Task dependency changed: api-schema now blocks frontend-sync."
```

Read inbox:

```bash
python3 "$TEAM_OPS_SCRIPT" inbox --team-name "<team-name>" --member "reviewer"
```

Rules:
- Every teammate reports decisions and blockers through messages.
- Critical dependency changes are broadcast to all members.
- Lead records final decisions in brief and task board.
- For visible team behavior, prefer multiple message rounds (direct messages and inbox reads) instead of a single end-state broadcast.

Sub-agent guardrails:
- `--team-name` must be a single identifier (no `/`, `\\`, `.`, `..`, whitespace, or control characters) for both `team_ops.py` and `create_team_brief.py`.
- `--from`, `--to`, `--member`, `--members`, `--decider` must refer to registered team members.
- `--owner` accepts either a registered team member or `unassigned`.
- `create_team_brief.py --roles` defines descriptive role labels only; command flags still require actual team member IDs from `team.json`.
- `--owner-map` owners must be registered team members or `unassigned` (one mapping per option; duplicates are rejected). Owners are not limited to debate participants.
- `start-debate` and new-debate `orchestrate-debate` default `--decider` to `lead`; if team has no `lead`, the command falls back to the first debate member with a warning (explicit `--decider` is still recommended).
- New debate creation requires at least two unique `--options` and at least two unique registered `--members`; otherwise commands fail fast with explicit input guidance.
- `add-position --member` must be one of the debate's registered members (not just any team member).
- Unknown member/task/debate ids include contextual guidance (registered members or available ids, plus typo suggestions when detectable), including `--notify-from` validation errors.
- If a team is missing under the current root and `--team-root` is not explicitly set, commands auto-switch to a uniquely discovered ancestor `.codex/teams` root with a warning.
- If `--team-root` is explicitly set (or `TEAM_OPS_ROOT` is set), commands do not auto-switch roots; they fail under the specified root for deterministic behavior.
- `--team-root` (or `TEAM_OPS_ROOT`) must resolve to a directory path (not a file path).
- Mutating commands serialize writes with a per-team lock at the resolved team root (after implicit auto-switch when applicable) to prevent concurrent state clobbering; if lock wait times out, retry or tune `TEAM_OPS_LOCK_WAIT_SECONDS`.
- Stale lock eviction checks recorded PID liveness and lock identity; active or newly replaced locks are not reclaimed solely by age.
- Commands fail fast on non-member identities to prevent mailbox/task-board drift.

## Task-State Protocol

State model:
- `pending`: not started or waiting on dependency
- `in_progress`: claimed by one teammate
- `completed`: done and verified

Claim task:

```bash
python3 "$TEAM_OPS_SCRIPT" claim \
  --team-name "<team-name>" \
  --task-id "task-2" \
  --member "implementer-b"
```

Update task status:

```bash
python3 "$TEAM_OPS_SCRIPT" update-task \
  --team-name "<team-name>" \
  --task-id "task-2" \
  --status "completed" \
  --note "Unit and integration checks passed."
```

List task board:

```bash
python3 "$TEAM_OPS_SCRIPT" list-tasks --team-name "<team-name>"
```

## Debate Protocol (Conflict to Decision)

When two or more approaches conflict, run a formal debate and persist the outcome.
Debates are stored in `<teams-root>/<team-name>/debates.json`.

Storage boundaries:
- `debates.json` stores structured debate state only (topic, options, members, positions, decision, applied state).
- `messages.jsonl` stores communication events only (direct/broadcast messages).
- `start-debate --notify` fans out one direct message per debate member in `messages.jsonl` (not a single broadcast record).
- Sending a message does not create a debate position; only `add-position` appends to `debates.json`.

Start a debate:

```bash
python3 "$TEAM_OPS_SCRIPT" start-debate \
  --team-name "<team-name>" \
  --topic "Choose retry strategy for API sync" \
  --task-id "task-2" \
  --options "fixed-backoff,exponential-backoff" \
  --members "lead,implementer-a,implementer-b,reviewer" \
  --decider "lead" \
  --notify
```

Each member submits a position:

```bash
python3 "$TEAM_OPS_SCRIPT" add-position \
  --team-name "<team-name>" \
  --debate-id "debate-1" \
  --member "implementer-a" \
  --option "exponential-backoff" \
  --confidence 0.8 \
  --rationale "Lower p95 under burst failures."
```

`--confidence` must be a finite numeric value in `[0, 1]`.

Lead decides and applies to the linked task:

```bash
python3 "$TEAM_OPS_SCRIPT" decide-debate \
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
python3 "$TEAM_OPS_SCRIPT" list-debates --team-name "<team-name>"
python3 "$TEAM_OPS_SCRIPT" show-debate --team-name "<team-name>" --debate-id "debate-1"
```

Interaction fidelity recommendation:
- When you want explicit teammate interaction traces, it is desirable to run multiple rounds of `message` and `add-position`, while keeping `decide-debate` as a single final step.
- Suggested pattern:
1. `start-debate` with `--notify` to open the decision and notify participants.
2. Each member reads `inbox` and sends at least one direct `message` to challenge or clarify assumptions.
3. Each member submits `add-position` while debate status is `open` (revisions are allowed only before decision).
4. Lead sends a final synthesis `message` or `broadcast`, then runs `decide-debate`.
- This produces an auditable collaboration trail in `messages.jsonl` and `debates.json` rather than a one-shot decision record.

Decision semantics:
- `decide-debate` defaults the decider to the debate's configured decider when `--decider` is omitted.
- `--rationale` is required when recording a new decision.
- For an already `decided` debate, `decide-debate --apply` reflects the existing decision to task state; it does not allow changing the selected option.
- For an already `applied` debate, `decide-debate` exits as no-op before `--owner-map` parsing/validation.
- `--owner-map` is validated only when applying to a linked task (`task_id` present); debates without linked tasks ignore owner mapping.
- To change direction after a decision, open a new debate linked to the blocked task.

## Automatic Orchestration Loop

Use one command to run the loop:
1. create/load debate
2. remind missing members
3. auto-decide when all positions are present (weighted confidence)
4. reflect decision into linked task (`status`, optional owner mapping, note, broadcast)

```bash
python3 "$TEAM_OPS_SCRIPT" orchestrate-debate \
  --team-name "<team-name>" \
  --topic "Choose cache invalidation strategy" \
  --task-id "task-4" \
  --options "ttl-only,event-driven" \
  --members "lead,implementer-a,implementer-b,reviewer" \
  --decider "lead" \
  --send-reminders \
  --status-on-apply "in_progress" \
  --owner-map "ttl-only:implementer-a,event-driven:implementer-b"
```

Re-run `orchestrate-debate` until applied. It is idempotent once a decision is applied.
For an already `applied` debate, `orchestrate-debate` exits as no-op before `--owner-map` parsing/validation.
For debates that are still waiting on positions, `orchestrate-debate` defers `--owner-map` validation until the apply step.
If an existing decision payload is corrupted (for example, decision option not in debate options), orchestration fails fast instead of applying invalid state.

Reminder/broadcast sender behavior:
- `--notify-from` is validated only when reminders or apply broadcasts are actually emitted.
- If `--notify-from` is left as default `lead` and `lead` is not in team members, sender falls back to the active decider.
- The same fallback rule applies to `start-debate --notify`.

## Monitoring (Optional, Default OFF)

`team_ops.py` supports opt-in monitor logging for command-level observability.
Monitoring is disabled by default and no monitor file is created unless explicitly enabled.

Enable monitoring for a command:

```bash
python3 "$TEAM_OPS_SCRIPT" orchestrate-debate \
  --team-name "<team-name>" \
  --debate-id "debate-1" \
  --monitoring
```

Override monitor file path:

```bash
python3 "$TEAM_OPS_SCRIPT" update-task \
  --team-name "<team-name>" \
  --task-id "task-1" \
  --status "completed" \
  --monitoring \
  --monitor-log-file "/tmp/team-monitor.jsonl"
```

Environment alternatives:
- `TEAM_OPS_MONITORING=1`
- `TEAM_OPS_MONITOR_LOG_FILE=/path/to/monitor.jsonl`
- `TEAM_OPS_ROOT=/path/to/.codex/teams`
- `TEAM_OPS_LOCK_WAIT_SECONDS=30` (optional write-lock wait override)
- `TEAM_OPS_LOCK_STALE_SECONDS=600` (optional stale-lock eviction window)

Generate monitor summary report:

```bash
python3 "$TEAM_OPS_SCRIPT" monitor-report \
  --team-name "<team-name>" \
  --output "<teams-root>/<team-name>/monitor-report.json"
```

`monitor-report` is team-filtered by `team_name`, and reports:
- `invalid_event_lines`: malformed lines skipped (including missing/invalid `team_name`)
- `other_team_event_lines`: schema-valid lines skipped because they belong to another team
- ISO timestamps without timezone offset are interpreted as UTC when computing reflection latency.
- `at` accepts common ISO-8601 UTC/offset forms (for example `...Z`, `...+00`, `...+00:00`, `...+0000`).
- Missing required event fields (for example `at`, `event_type`, `command`, `actor`, `entity_type`, `entity_id`) or invalid `at` timestamp formats are treated as invalid monitor lines.

Monitoring event schema (`monitor.jsonl`, one JSON object per line):

| Field | Type | Description |
| --- | --- | --- |
| `at` | string (ISO-8601 timestamp) | Event timestamp |
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

For `debate.applied`, `command` is logged as `apply-decision` (internal apply step used by both `decide-debate --apply` and `orchestrate-debate`).

Example line:

```json
{"at":"2026-02-16T06:00:00+00:00","event_type":"debate.applied","command":"apply-decision","team_name":"example-team","actor":"lead","entity_type":"debate","entity_id":"debate-1","before":{"task":{"owner":"unassigned","status":"pending"}},"after":{"task":{"owner":"implementer-a","status":"in_progress"}},"metadata":{"task_id":"task-1","option":"session","status":"in_progress","sender":"lead"},"correlation_id":"2e5db8f0d25d4f08b6f2f4f13a4ed8ad"}
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

- `scripts/create_team_brief.py`: generate a reusable team charter and workstream summary template (canonical task board state lives in `scripts/team_ops.py`).
- `scripts/team_ops.py`: manage team members, task states, mailbox, debates, and auto-orchestration.
- `references/anthropic-pattern-map.md`: what is mirrored from Anthropic docs.
- `references/team-topologies.md`: choose collaboration pattern by coupling and risk profile.
- `references/prompt-templates.md`: copy/paste lead, specialist, reviewer, and challenger prompts.
