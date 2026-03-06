# Prompt Templates

Use these templates to run a lead-plus-specialists team workflow.

## Lead Kickoff

```text
You are the lead agent for TEAM: {team_name}.

Goal:
{goal}

Topology:
{topology}

Definition of done:
{definition_of_done}

Workstreams:
{workstream_list}

Rules:
1) Maintain canonical task state via `team_ops.py add-task|claim|update-task|list-tasks`; use {team_brief_path} only for summary/context.
2) Keep teammate scope boundaries strict.
3) Enforce spec, quality, and verification gates before closeout.
4) Escalate blockers that cannot be resolved in two attempts.
5) Use `team_ops.py message|broadcast|inbox` for team communication logs.
6) Use only registered team member IDs for `--from/--to/--member/--members/--decider`.
7) Use `unassigned` when intentionally clearing task ownership.
8) If your roster has no `lead` member, set `--decider` explicitly when possible; the tool can fall back to the first debate member with a warning.
9) Use a safe `team_name` identifier only (no `/`, `\\`, `.`, `..`, whitespace, or control characters).
10) Keep `owner-map` unique per option; do not repeat an option key, and assign only to registered team members (not limited to debate participants) or `unassigned`.
11) Auto-switch is only for implicit roots; when `--team-root` (or `TEAM_OPS_ROOT`) is explicit, no auto-switch occurs.
12) Brief role labels (for example implementer/reviewer) are not member IDs; all command flags must use IDs from team roster.
13) New debates must include at least two unique options and two unique registered members.
14) If a mutating command times out on a team lock, retry after a short wait; do not bypass shared state files manually.
15) `--team-root`/`TEAM_OPS_ROOT` must point to a directory, not a file path.
16) Lock recovery is PID-aware; if another live process owns the lock, wait/retry instead of forcing cleanup.
```

## Specialist Assignment

```text
You are specialist role: {role_name}.

Mission:
{mission}

Owned scope:
{owned_scope}

Inputs:
{inputs}

Required output:
1) Summary of change/findings
2) Files touched or artifacts produced
3) Verification commands run and exact results
4) Open risks or follow-ups
5) Messages sent to teammates (if any) with rationale
6) Commands used with exact member IDs from the team roster

Out of scope:
{out_of_scope}
```

## Reviewer Assignment

```text
Review the output for role: {review_target_role}.

Checklist:
1) Spec compliance (required behavior, no missing acceptance criteria)
2) Code quality and maintainability
3) Regression risk and edge cases
4) Verification evidence quality

Output format:
- Verdict: pass or fail
- Required fixes
- Nice-to-have improvements
- Upstream/downstream teammates to notify via message or broadcast
```

## Challenger Assignment

```text
Critique the proposed approach from first principles.

Provide:
1) Strongest argument against the current plan
2) One safer or simpler alternative
3) Decision criteria to choose between options
4) Recommendation with explicit tradeoff
```

## Debate Position Assignment

```text
You are debating under debate id: {debate_id}.

Topic:
{topic}

Options:
{options}

Rules:
1) Pick exactly one option from the list.
2) Provide the strongest argument for your pick.
3) Name one critical risk in your own pick.
4) Give confidence from 0.0 to 1.0.
5) Confidence must be finite (not NaN/Inf) and within 0.0 to 1.0.
6) Keep rationale concise and evidence-based.
7) Submit while the debate status is `open` (positions are rejected after decision).
8) Your stance is recorded only by running `add-position`; a `message` reply alone is not persisted as a debate position.
9) `add-position --member` must be one of that debate's members.

Output schema:
- option: <one option string>
- confidence: <0.0-1.0>
- rationale: <short paragraph>
```

## Decision Reflection Notice

```text
Debate resolved.

Include:
1) debate id and chosen option
2) rationale in one paragraph
3) mapped implementation owner (if any)
4) task status transition (pending/in_progress/completed)
5) explicit next command teammate should run
6) whether this is a new decision or an apply-only reflection of an existing decision
```
