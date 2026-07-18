#!/usr/bin/env python3
"""Validate a self-contained, print-ready system walkthrough HTML file.

The validator intentionally checks deterministic structure only. Visual quality,
factual accuracy, and print pagination still require browser inspection.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote


VOID_ELEMENTS = {
    "area",
    "base",
    "br",
    "col",
    "embed",
    "hr",
    "img",
    "input",
    "link",
    "meta",
    "param",
    "source",
    "track",
    "wbr",
}


@dataclass
class ValidationResult:
    path: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metrics: dict[str, int | str | bool] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.errors


class WalkthroughParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.parse_errors: list[str] = []
        self.ids: list[str] = []
        self.local_links: list[str] = []
        self.external_dependencies: list[str] = []
        self.tags: Counter[str] = Counter()
        self.attrs_by_tag: dict[str, list[dict[str, str]]] = {}
        self.title_depth = 0
        self.title_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        values = {key.lower(): value or "" for key, value in attrs}
        self.tags[tag] += 1
        self.attrs_by_tag.setdefault(tag, []).append(values)
        if values.get("id"):
            self.ids.append(values["id"])
        if tag == "a" and values.get("href", "").startswith("#"):
            self.local_links.append(unquote(values["href"][1:]))
        self._collect_dependency(tag, values)
        if tag == "title":
            self.title_depth += 1
        if tag not in VOID_ELEMENTS:
            self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag.lower() not in VOID_ELEMENTS and self.stack:
            self.stack.pop()

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "title" and self.title_depth:
            self.title_depth -= 1
        if tag in VOID_ELEMENTS:
            return
        if not self.stack:
            self.parse_errors.append(f"unexpected closing tag </{tag}>")
            return
        if self.stack[-1] == tag:
            self.stack.pop()
            return
        if tag not in self.stack:
            self.parse_errors.append(f"unmatched closing tag </{tag}>")
            return
        self.parse_errors.append(f"misnested closing tag </{tag}> after <{self.stack[-1]}>")
        while self.stack and self.stack[-1] != tag:
            self.stack.pop()
        if self.stack:
            self.stack.pop()

    def handle_data(self, data: str) -> None:
        if self.title_depth:
            self.title_text.append(data)

    def _collect_dependency(self, tag: str, attrs: dict[str, str]) -> None:
        candidate = ""
        if tag == "script":
            candidate = attrs.get("src", "")
        elif tag == "link" and "stylesheet" in attrs.get("rel", "").lower():
            candidate = attrs.get("href", "")
        elif tag in {"img", "source", "video", "audio", "iframe", "embed"}:
            candidate = attrs.get("src", "")
        if candidate.lower().startswith(("http://", "https://", "//")):
            self.external_dependencies.append(f"<{tag}> {candidate}")


def _has_class(attrs: dict[str, str], class_name: str) -> bool:
    return class_name in attrs.get("class", "").split()


def validate(
    path: str | Path,
    *,
    expected_quizzes: int = 5,
    min_sections: int = 1,
) -> ValidationResult:
    source_path = Path(path).expanduser().resolve()
    result = ValidationResult(path=str(source_path))
    if not source_path.is_file():
        result.errors.append(f"HTML file does not exist: {source_path}")
        return result

    try:
        source = source_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as error:
        result.errors.append(f"HTML must be UTF-8: {error}")
        return result

    parser = WalkthroughParser()
    try:
        parser.feed(source)
        parser.close()
    except Exception as error:  # HTMLParser may raise for malformed declarations.
        result.errors.append(f"HTML parser failed: {error}")
        return result

    result.errors.extend(parser.parse_errors)
    if parser.stack:
        result.errors.append(f"unclosed tags remain: {', '.join(parser.stack[-8:])}")

    html_attrs = parser.attrs_by_tag.get("html", [{}])[0]
    if not html_attrs.get("lang"):
        result.errors.append("<html> must declare a lang attribute")
    if not any("charset" in attrs for attrs in parser.attrs_by_tag.get("meta", [])):
        result.errors.append("a UTF-8 <meta charset> declaration is required")
    if not "".join(parser.title_text).strip():
        result.errors.append("a non-empty <title> is required")
    for required_tag in ("nav", "main", "section"):
        if parser.tags[required_tag] == 0:
            result.errors.append(f"at least one <{required_tag}> is required")
    if parser.tags["section"] < min_sections:
        result.errors.append(
            f"expected at least {min_sections} sections, found {parser.tags['section']}"
        )

    duplicate_ids = sorted(name for name, count in Counter(parser.ids).items() if count > 1)
    if duplicate_ids:
        result.errors.append(f"duplicate id values: {', '.join(duplicate_ids)}")
    missing_links = sorted(set(parser.local_links) - set(parser.ids))
    if missing_links:
        result.errors.append(f"missing local anchor targets: {', '.join(missing_links)}")

    external_css = re.findall(
        r"@import\s+(?:url\()?\s*['\"]?(?:https?:)?//[^;)'\"]+",
        source,
        flags=re.IGNORECASE,
    )
    external_urls = re.findall(
        r"url\(\s*['\"]?(?:https?:)?//[^)'\"]+",
        source,
        flags=re.IGNORECASE,
    )
    dependencies = parser.external_dependencies + external_css + external_urls
    if dependencies:
        result.errors.append("external dependency found: " + "; ".join(dependencies[:8]))

    if "@media print" not in source:
        result.errors.append("print stylesheet is required: missing @media print")
    if not re.search(r"@page\s*\{", source, flags=re.IGNORECASE):
        result.errors.append("print page definition is required: missing @page")
    if not re.search(
        r"white-space\s*:\s*(?:pre|pre-wrap|break-spaces)",
        source,
        flags=re.IGNORECASE,
    ):
        result.errors.append("code blocks need an explicit white-space preservation rule")

    quiz_cards = [
        attrs
        for attrs in parser.attrs_by_tag.get("article", [])
        if _has_class(attrs, "quiz-card")
    ]
    quiz_options = [
        attrs
        for attrs in parser.attrs_by_tag.get("button", [])
        if _has_class(attrs, "quiz-option")
    ]
    quiz_feedback = [
        attrs
        for attrs in parser.attrs_by_tag.get("div", [])
        if _has_class(attrs, "quiz-feedback")
    ]
    if len(quiz_cards) != expected_quizzes:
        result.errors.append(
            f"quiz count mismatch: expected {expected_quizzes}, found {len(quiz_cards)}"
        )
    if quiz_cards:
        if len(quiz_options) < len(quiz_cards) * 2:
            result.errors.append("every quiz needs at least two .quiz-option buttons")
        if len(quiz_feedback) != len(quiz_cards):
            result.errors.append("every quiz needs exactly one .quiz-feedback block")
        for index, attrs in enumerate(quiz_cards, start=1):
            answer = attrs.get("data-answer", "")
            if not answer.isdigit():
                result.errors.append(f"quiz {index} needs an integer data-answer")
        if parser.tags["script"] == 0:
            result.errors.append("interactive quizzes require inline JavaScript")

    if parser.tags["details"] and "beforeprint" not in source:
        result.errors.append(
            "documents with <details> must open them from a beforeprint handler"
        )

    if parser.tags["h1"] != 1:
        result.warnings.append(f"prefer exactly one <h1>; found {parser.tags['h1']}")
    if parser.tags["pre"] == 0:
        result.warnings.append("no <pre> code or event example was found")
    if not re.search(r"@media\s*\([^)]*max-width", source, flags=re.IGNORECASE):
        result.warnings.append("no responsive max-width media query was found")
    has_icon = any(
        "icon" in attrs.get("rel", "").lower().split()
        for attrs in parser.attrs_by_tag.get("link", [])
    )
    if not has_icon:
        result.warnings.append("consider an inline data favicon to avoid file-server 404 noise")

    result.metrics = {
        "bytes": len(source.encode("utf-8")),
        "sections": parser.tags["section"],
        "toc_links": len(parser.local_links),
        "ids": len(parser.ids),
        "quizzes": len(quiz_cards),
        "details": parser.tags["details"],
        "code_blocks": parser.tags["pre"],
        "self_contained": not dependencies,
    }
    return result


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("html", help="path to the walkthrough HTML")
    parser.add_argument(
        "--expected-quizzes",
        type=int,
        default=5,
        help="required number of .quiz-card elements (default: 5)",
    )
    parser.add_argument(
        "--min-sections",
        type=int,
        default=8,
        help="minimum number of explanatory sections (default: 8)",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    result = validate(
        args.html,
        expected_quizzes=args.expected_quizzes,
        min_sections=args.min_sections,
    )
    if args.json:
        payload = asdict(result)
        payload["ok"] = result.ok
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        status = "PASS" if result.ok else "FAIL"
        print(f"[{status}] {result.path}")
        for error in result.errors:
            print(f"ERROR: {error}")
        for warning in result.warnings:
            print(f"WARNING: {warning}")
        if result.metrics:
            summary = " ".join(f"{key}={value}" for key, value in result.metrics.items())
            print(f"METRICS: {summary}")
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
