# Research and Content Reference

## Contents

1. Source hierarchy
2. Evidence ledger
3. Repository exploration
4. Content architecture
5. Terminology and examples
6. Accuracy traps

## 1. Source hierarchy

Prefer evidence in this order, while recognizing that each source answers a different question:

1. **Runtime code**: actual branches, validation, persistence, retries, and emitted events.
2. **Tests**: intended invariants and edge cases that may not be obvious from a single function.
3. **Configuration and deployment files**: defaults, optional profiles, replica counts, limits, ports, and environment overrides.
4. **Operational documentation**: supported procedures and contracts; verify important claims against code.
5. **README and comments**: orientation only when newer evidence is unavailable.

Use version control status and recent history to distinguish current code from stale docs. Never revert or normalize unrelated changes while documenting.

## 2. Evidence ledger

Build a compact private ledger before writing. A useful shape is:

| Claim | Source | Classification | Confidence | Walkthrough section |
|---|---|---|---|---|
| API enqueues long work | exact file/function | code behavior | high | request lifecycle |
| worker concurrency is 16 | config field and override | deployment example | high | scaling |
| optional OCR service exists | compose profile | optional component | high | files |
| upstream timeout cause | logs only | inference | medium | operations |

Classification prevents common errors:

- **Code behavior**: always true for this checkout unless another branch overrides it.
- **Configurable default**: a starting value, not guaranteed in production.
- **Deployment example**: specific to one Compose override or environment.
- **Optional component**: present only when a profile, feature flag, or dependency is enabled.
- **Inference**: clearly label it and explain the observed evidence.

## 3. Repository exploration

Start broad, then follow one request path deeply.

```bash
git status --short
rg --files | sed -n '1,240p'
rg -n "route|endpoint|queue|worker|event|artifact|timeout|concurrency" src docs *.yml
rg -n "DEFAULT|TIMEOUT|CONCURRENCY|MAX_|PORT" src *.yml .env.example
```

Recommended reading order:

1. service manifests and dependency graph,
2. configuration schema,
3. HTTP request schema and routes,
4. queue producer and worker consumer,
5. coordinator or orchestration loop,
6. tool and external-provider adapters,
7. persistence and object storage,
8. tests for cancellation, replay, retry, and limits,
9. UI event handling and operational dashboards.

Trace one ordinary request and one complex request involving a file or tool. Record exact state transitions and durable writes. This is more reliable than summarizing directory names.

## 4. Content architecture

Teach in layers so a reader can stop at the depth they need.

### Quick mental model

State five durable truths, not marketing claims. Examples:

- where requests enter,
- who performs long-running work,
- which store is canonical,
- where bytes live,
- how progress reaches the user.

### Background

Explain what problem the system solves and what it is not. Define session, request/turn, event, tool, worker, artifact, and queue in the system's own context.

### Intuition and invariants

Explain why boundaries exist. Useful invariants include:

- ingress is separate from execution,
- durable state is separate from wake-up signals,
- metadata is separate from binary bytes,
- same-session ordering differs from cross-session concurrency,
- transport completion differs from business success.

### Topology and request lifecycle

Show service ownership before showing a long sequence. Then trace one request from client to final response, including failure and cancellation branches.

### Specialized flows

Cover only those supported by evidence: file preprocessing, RAG, sandbox execution, package installation, artifact selection, cards, subagents, context compaction, or observability.

### Code-reading map

Map symptoms and concepts to exact files or functions. Do not dump a directory tree without explaining why each entry matters.

## 5. Terminology and examples

Use the general term first, then the local identifier:

> A **lease** (temporary ownership of a queued job) prevents two workers from processing the same request at once.

Prefer concrete examples over definitions alone:

- show a minimal request JSON,
- show a short event sequence,
- show one object-storage key pattern,
- show one capacity calculation,
- show one failure investigation path.

Keep examples structurally accurate but remove credentials, hostnames that expose private infrastructure, and real user data.

## 6. Accuracy traps

- Do not infer behavior from service names alone.
- Do not call every helper or tool invocation a separate LLM agent.
- Do not confuse file existence in a workspace with artifact registration.
- Do not treat queue capacity as execution concurrency.
- Do not treat a terminal transport event as a successful outcome.
- Do not state that indexed content is already in model context unless retrieval or direct injection occurred.
- Do not describe fallback providers without their trigger conditions.
- Do not copy secrets from `.env` files into examples.
- Do not claim a validation was performed merely because code contains a validation function; inspect the actual call path and result handling.
