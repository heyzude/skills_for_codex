# Anthropic Pattern Map

This file maps documented Anthropic Agent Teams patterns to Codex emulation behavior in this skill.

## Source basis

- Anthropic release notes mentioning Agent Teams and direct teammate coordination.
- Anthropic Claude Code sub-agents documentation.

## Parity Table

| Anthropic pattern | Codex-agent-teams mapping | Notes |
| --- | --- | --- |
| Team startup with scoped teammates | `team_ops.py init` + `create_team_brief.py` | Shared team directory under `.codex/teams/<team>` |
| Shared task list with claim semantics | `add-task`, `claim`, `update-task`, `list-tasks` | Uses statuses `pending`, `in_progress`, `completed` |
| Teammates communicate directly | `message`, `broadcast`, `inbox` commands | Emulated through shared mailbox log |
| Lead can operate in coordination-only mode | `--delegate-mode` in team brief | Process rule; not hard runtime lock |
| Explicit approval before implementation | Startup Sequence step 3 in `SKILL.md` | Pair with planning skills and user checkpoint |
| Parallel workstreams | Superpowers skill routing by workstream | Uses `dispatching-parallel-agents` and/or `subagent-driven-development` |
| Conflict adjudication lane | Challenger reviewer pattern + decision log | Codified in workflow and templates |
| Debate state persistence | `start-debate`, `add-position`, `list/show-debate` | Stored in `.codex/teams/<team>/debates.json` |
| Decision reflected into execution state | `decide-debate --apply` | Updates linked task owner/status/note and emits broadcast |
| Automatic orchestration loop | `orchestrate-debate` | Create/load -> remind -> auto-decide -> auto-apply |
| Optional execution observability | `--monitoring` + `monitor-report` | Default OFF; opt-in JSONL monitor events and derived metrics |
| Convergence with quality gates | Spec, quality, verification, closeout gates | Integrates with existing superpowers guardrails |

## Runtime caveats

- Native Anthropic UI affordances (for example interaction shortcuts and product-specific toggles) are not guaranteed in Codex.
- This skill preserves behavioral intent through explicit file-backed protocols and command-driven coordination.
