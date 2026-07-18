# HTML and Print Design Reference

## Contents

1. Self-contained contract
2. Page structure
3. Visual language
4. Diagrams and tables
5. Interaction and accessibility
6. Print behavior
7. Quiz contract
8. Common failures

## 1. Self-contained contract

Produce one UTF-8 HTML file with inline CSS and JavaScript. It must render without network access. Do not load remote fonts, CSS frameworks, scripts, icons, or images. Data URLs are acceptable for small embedded assets. Normal citation links may point to the web because the document does not depend on them to render.

Add `<link rel="icon" href="data:,">` to prevent local HTTP servers from producing a misleading favicon 404.

## 2. Page structure

Use semantic landmarks and stable anchors:

```html
<header>...</header>
<div class="layout">
  <aside><nav>...</nav></aside>
  <main>
    <section id="background">...</section>
    <section id="architecture">...</section>
  </main>
</div>
```

Use one `h1`. Section headings should be `h2`; subsections should be `h3`. Keep every table-of-contents link local and ensure every target id is unique.

Provide a fast reading path near the beginning. A product integrator, runtime engineer, and operator rarely need the same sections first.

## 3. Visual language

Choose a visual direction appropriate to the system: technical handbook, annotated field guide, architecture notebook, or operations manual. Do not default to a marketing landing page or dashboard.

Use:

- a restrained multi-color palette that still works in grayscale,
- one display face from system fonts and one readable body stack,
- consistent spacing and border rules,
- section numbers and short explanatory decks,
- callouts only for warnings, invariants, and decisions.

Avoid:

- nested cards,
- decorative gradients or blobs,
- huge headings inside compact panels,
- negative letter spacing,
- viewport-based font scaling,
- horizontal overflow,
- low-contrast code and table text.

## 4. Diagrams and tables

Create diagrams with semantic HTML and CSS Grid/Flexbox. A diagram should have a text fallback through its actual DOM order. Use arrows as text or CSS separators; do not encode the whole explanation in a bitmap.

For topology, group by responsibility: client, ingress, asynchronous work, execution, state, and external providers. For sequences, show numbered stages and clearly mark optional branches.

Place wide tables inside an overflow wrapper on screen:

```css
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; }
```

In print, reduce table type modestly or switch selected tables to row blocks. Never allow important columns to be silently clipped.

Use `<pre><code>` for JSON, shell commands, event streams, and source snippets:

```css
pre { overflow-x: auto; }
pre code { white-space: pre-wrap; overflow-wrap: anywhere; }
```

## 5. Interaction and accessibility

- All controls must be real `<button>` elements.
- Provide visible focus states.
- Use `aria-label` for icon-only or ambiguous controls.
- Keep interactive controls stable in size.
- Add text-size controls only when they change a root custom property predictably.
- A sticky table of contents may be used on wide screens but must become ordinary flow on narrow screens.
- Use a scroll progress indicator only as a secondary aid.

If explanatory details are collapsible, use `<details><summary>`. Do not hide information exclusively behind JavaScript.

## 6. Print behavior

Define page and print rules explicitly:

```css
@page { size: A4; margin: 15mm 14mm 17mm; }
@media print {
  nav, .screen-controls { display: none !important; }
  body { background: #fff; color: #111; }
  section { break-before: page; }
  pre, table, figure, .quiz-card { break-inside: avoid; }
}
```

Use JavaScript to open details before print and restore them afterward:

```js
const states = new Map();
window.addEventListener("beforeprint", () => {
  document.querySelectorAll("details").forEach((node) => {
    states.set(node, node.open);
    node.open = true;
  });
});
window.addEventListener("afterprint", () => {
  states.forEach((open, node) => { node.open = open; });
  states.clear();
});
```

Do not generate blank cover or closing pages. The first and last printed pages must contain meaningful content. Confirm this from the rendered PDF, not from CSS inspection alone.

## 7. Quiz contract

Unless the user requests otherwise, include exactly five quizzes. Each quiz must:

- test system understanding rather than identifier memorization,
- offer three or four plausible options,
- store the correct zero-based option in `data-answer`,
- vary the correct option position across the document,
- show immediate correct/incorrect state,
- include a concise explanation,
- disable or make the selected state unambiguous after answering.

Use these classes so the validator can inspect the contract:

```html
<article class="quiz-card" data-answer="2">
  <h3>Question</h3>
  <div class="quiz-options">
    <button type="button" class="quiz-option">...</button>
  </div>
  <div class="quiz-feedback">Explanation</div>
</article>
```

In print, hide `.quiz-option` and reveal `.quiz-feedback` so the paper copy includes the answer explanation without wasting pages on controls.

## 8. Common failures

- **The document is attractive but shallow**: return to the evidence ledger and request lifecycle.
- **The page looks like a product dashboard**: remove panel chrome and restore editorial flow.
- **Mobile overflows**: inspect `document.documentElement.scrollWidth`, tables, `pre`, and fixed-width diagrams.
- **Dropdown content is absent in PDF**: add and test `beforeprint` handling.
- **Print uses unexpected paper size**: set `@page`, then select A4 in the native print dialog; automation may need `preferCSSPageSize`.
- **The file works only through HTTP**: remove path assumptions and external dependencies; retest with `file://`.
- **A diagram is meaningless to a screen reader**: ensure the DOM text conveys the same flow in reading order.
