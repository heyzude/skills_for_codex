---
name: create-system-walkthrough
description: Use when a user wants a codebase, service, platform, runtime, or technical architecture explained as a self-contained HTML walkthrough, especially when the result must be approachable to general developers, printable, offline-capable, or suitable for onboarding and operational follow-up.
---

# Create System Walkthrough

## Purpose

Turn a live codebase into one evidence-based, self-contained HTML document that teaches how the system works. Explain behavior in ordinary developer language, define unavoidable terminology, connect architecture to source files, and optimize both screen reading and paper printing.

Do not write a diff report unless the user explicitly asks for one. Describe the current system as a coherent whole.

## Required Workflow

### 1. Establish scope and output

- Identify the repository or workspace that is the source of truth.
- Infer the audience from the request; ask only when audience or scope cannot be discovered safely.
- Default to the user's language for the walkthrough.
- Save the final HTML outside the inspected repository unless the user requests another location.
- Use a dated filename: `YYYY-MM-DD-<system>-walkthrough.html`.
- Preserve unrelated working-tree changes. Do not modify the product while documenting it.

### 2. Build an evidence ledger before drafting

Read [references/research-and-content.md](references/research-and-content.md). Inspect implementation, configuration, deployment files, tests, and current operational docs. Record each important claim with its source and classify it as:

- current code behavior,
- configurable default,
- deployment example,
- optional component,
- or unresolved uncertainty.

Never publish credentials, private keys, tokens, or secret environment values. Mention variable names and ownership boundaries instead.

### 3. Design the teaching sequence

Start with a five-sentence mental model. Then move from user-visible behavior toward internal detail:

1. what the system is and is not,
2. design intuition and invariants,
3. service or module topology,
4. one request's end-to-end lifecycle,
5. state, context, files, queues, and tools,
6. failure recovery and observability,
7. concurrency and scaling,
8. code-reading map,
9. glossary and comprehension check.

Adapt sections to the system. Do not force irrelevant categories.

### 4. Write one self-contained HTML file

Read [references/html-and-print-design.md](references/html-and-print-design.md). Requirements:

- One HTML file with inline CSS and JavaScript.
- No CDN, remote font, remote script, remote stylesheet, or remote image dependency.
- External links are allowed only as citations or navigation, not runtime dependencies.
- Use semantic HTML: one `h1`, `nav`, `main`, and linked `section` elements.
- Use HTML/CSS diagrams for flows and topology; do not use ASCII-art diagrams.
- Wrap code and event examples in `<pre><code>` and preserve whitespace.
- Define unfamiliar terms at first use and include a glossary.
- Include exactly five medium-difficulty interactive multiple-choice quizzes unless the user requests another count. Vary the correct option position and explain every answer.
- Add controls for print, text size, and expanding explanatory details when useful.
- Add responsive layout and `@page` plus `@media print` rules.
- Open every `<details>` element during printing and restore its prior state afterward.
- Hide interactive quiz options in print and show answer explanations.
- Include an inline data favicon to avoid noisy local-server 404 errors.

Choose a visual language that fits the subject. Avoid a generic dashboard, nested cards, decorative gradients, and one-hue palettes. The content hierarchy must remain legible in monochrome printing.

### 5. Validate mechanically

Run:

```bash
python "$CODEX_HOME/skills/create-system-walkthrough/scripts/validate_walkthrough.py" \
  /absolute/path/to/walkthrough.html
```

When `CODEX_HOME` is unset, use `~/.codex`. Fix every error. Warnings require judgment but must be reviewed.

The validator checks deterministic structure, not truth or visual quality. It cannot prove that diagrams are accurate or that pages look good.

### 6. Validate in a real browser

Read [references/browser-verification.md](references/browser-verification.md). At minimum:

- open the HTML in Chromium,
- inspect a desktop viewport,
- inspect a narrow mobile viewport,
- verify `scrollWidth == clientWidth`,
- click a correct and incorrect quiz option,
- expand and collapse details,
- print to PDF and inspect the first, middle, and last pages,
- confirm no blank first/last page and no clipped diagrams, tables, or code.

Use a temporary local HTTP server only when `file://` loading is insufficient. Stop it after validation.

### 7. Deliver succinctly

Give the user a clickable absolute path to the HTML. State what was covered and what was verified. Do not leave validation screenshots, temporary PDFs, HTTP servers, or browser sessions behind.

## Quality Gates

Do not finish when any of these are true:

- a major architecture claim has no source,
- deployment-specific numbers are presented as universal defaults,
- the table of contents contains broken anchors,
- the HTML needs the internet to render,
- a diagram becomes unreadable on mobile or paper,
- a dropdown's content disappears in print,
- quizzes have repeated answer positions or no explanations,
- the final response links to a file that was not opened and validated.

## Resource Map

- [references/research-and-content.md](references/research-and-content.md): source hierarchy, evidence ledger, content architecture, terminology.
- [references/html-and-print-design.md](references/html-and-print-design.md): responsive visual and print requirements, diagrams, quizzes, accessibility.
- [references/browser-verification.md](references/browser-verification.md): static and browser verification commands and failure checks.
- `scripts/validate_walkthrough.py`: deterministic HTML contract validator. Run with `--help` for options.
