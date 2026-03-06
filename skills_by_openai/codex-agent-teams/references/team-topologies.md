# Team Topologies

Use this reference to pick a collaboration pattern before dispatching teammates.

## lead-hub

Use when workstreams are mostly independent and one lead should integrate outputs.

Strengths:
- Fast setup
- Clear ownership
- Easy status tracking

Risks:
- Lead can become a bottleneck
- Weak integration if the lead skips review gates

## pipeline

Use when outputs must flow in strict order (for example: research -> implementation -> review).

Strengths:
- High consistency
- Strong handoff quality

Risks:
- Slowest lane controls total time
- Rework propagates downstream

## red-blue

Use when architectural or debugging uncertainty is high and two approaches should compete.

Strengths:
- Better decisions under uncertainty
- Finds hidden tradeoffs early

Risks:
- Extra cost from duplicate work
- Needs explicit adjudication criteria

Execution note:
- Use `start-debate`/`add-position` and finish with `decide-debate --apply` (or `orchestrate-debate`).

## parallel-pods

Use when several subsystems can be changed independently with minimal overlap.

Strengths:
- Highest throughput on large scopes
- Local failures stay contained

Risks:
- Integration drift across pods
- Inconsistent conventions without shared rules

## Selection Guide

- If integration is simple and speed matters most, choose `lead-hub`.
- If sequence and correctness matter more than speed, choose `pipeline`.
- If solution direction is unclear, choose `red-blue`.
- If scope is large and modular, choose `parallel-pods`.
