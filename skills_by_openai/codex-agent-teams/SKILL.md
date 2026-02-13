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
- If two solutions conflict, dispatch a challenger reviewer and record decision.

## Limitations

This skill emulates Agent Teams patterns in Codex, but some native Anthropic UX features may not exist in every runtime. When unavailable, fall back to:
- explicit message commands via `scripts/team_ops.py`
- shared files for task board and decisions
- lead-mediated routing while preserving protocol semantics

## Resources

- `scripts/create_team_brief.py`: generate a reusable team charter and task board.
- `scripts/team_ops.py`: manage team members, task states, dependencies, and mailbox.
- `references/anthropic-pattern-map.md`: what is mirrored from Anthropic docs.
- `references/team-topologies.md`: choose collaboration pattern by coupling and risk profile.
- `references/prompt-templates.md`: copy/paste lead, specialist, reviewer, and challenger prompts.
