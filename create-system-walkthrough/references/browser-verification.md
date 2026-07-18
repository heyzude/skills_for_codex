# Browser Verification Reference

## Contents

1. Static validation
2. Local serving
3. Desktop and mobile checks
4. Interaction checks
5. Print checks
6. Completion criteria

## 1. Static validation

Run the bundled validator before opening a browser:

```bash
SKILL_ROOT="${CODEX_HOME:-$HOME/.codex}/skills/create-system-walkthrough"
python "$SKILL_ROOT/scripts/validate_walkthrough.py" \
  /absolute/path/to/system-walkthrough.html
```

Useful options:

```bash
python "$SKILL_ROOT/scripts/validate_walkthrough.py" --help
python "$SKILL_ROOT/scripts/validate_walkthrough.py" walkthrough.html --json
python "$SKILL_ROOT/scripts/validate_walkthrough.py" walkthrough.html --expected-quizzes 3
```

The validator permits external citation links in `<a href>`, but rejects remote rendering dependencies such as script `src`, stylesheet `href`, image `src`, CSS `@import`, and CSS `url(...)`.

## 2. Local serving

Opening the file directly is preferred because it proves offline portability:

```text
file:///absolute/path/to/system-walkthrough.html
```

If browser automation cannot access `file://`, start a temporary server from the output directory:

```bash
python -m http.server 8766 --bind 127.0.0.1 --directory /absolute/output/directory
```

Choose another free port when needed. Stop the server after testing.

## 3. Desktop and mobile checks

At a desktop viewport near 1440×1000, inspect:

- cover hierarchy,
- table of contents and scrollspy,
- topology and sequence diagrams,
- tables and code examples,
- section spacing and heading scale.

At a narrow viewport near 390×844, inspect:

- no clipped title or buttons,
- navigation becomes normal flow or an appropriate compact control,
- diagrams stack in reading order,
- tables and code scroll internally when necessary,
- no page-level horizontal scroll.

Measure overflow in browser context:

```js
({
  scrollWidth: document.documentElement.scrollWidth,
  clientWidth: document.documentElement.clientWidth
})
```

The values should be equal at both viewports.

## 4. Interaction checks

- Follow several table-of-contents links.
- Expand and collapse every style of `<details>` block.
- Click one incorrect quiz answer and confirm incorrect styling plus explanation.
- Reload, click one correct answer, and confirm correct styling plus explanation.
- Test text-size controls at minimum and maximum values.
- Confirm the print button invokes the native print flow.
- Inspect the browser console; resolve document errors. A favicon 404 should be prevented with an inline data favicon.

## 5. Print checks

Print or export the page to PDF. Use A4, 95–100% scale, and background graphics when color callouts matter.

Inspect at least:

- first page: not blank, cover fits,
- one topology page: arrows and labels remain together,
- one table/code page: no clipping,
- quiz pages: explanations visible and option buttons hidden,
- a page containing details: formerly collapsed content is visible,
- last page: not blank and footer text fits.

Automation-generated PDFs may default to Letter unless CSS page size is explicitly preferred. This is an automation setting, not necessarily a failure of the native print dialog. Verify the user's intended print path.

## 6. Completion criteria

Finish only when:

- static validation passes,
- no browser console errors remain,
- desktop and mobile have no page-level horizontal overflow,
- all interactions behave correctly,
- print output contains details and quiz explanations,
- first and last printed pages are meaningful,
- temporary screenshots, PDFs, servers, and browser sessions are removed,
- the final response links to the exact validated HTML path.
