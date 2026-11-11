"""Lossless prompt projection and shared Markdown body validation.

Only exact prompt labels introduce sections. Unclassified body text is returned
verbatim for editing rather than silently omitted from an execution snapshot.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_POSITIVE = {"正向提示词", "正面提示词", "正向", "positive prompt", "positive"}
_NEGATIVE = {"反向提示词", "负向提示词", "负面提示词", "反向", "负向", "negative prompt", "negative"}
_GENERIC = {"生成提示词", "提示词", "prompt", "generation prompt"}
_WARNING = "提示词章节无法完整区分，已保留完整原文，请编辑并核对正向和反向提示词。"
_ATX = re.compile(r"^ {0,3}(#{1,6})(?:\s+(.+?)|\s*)$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_SETEXT = re.compile(r"^ {0,3}(=+|-+)\s*$")
_INLINE_LABEL = re.compile(
    "(?:" + "|".join(re.escape(name) for name in _POSITIVE | _NEGATIVE | _GENERIC)
    + r")[*_]*\s*[:：]", re.I,
)


@dataclass(frozen=True)
class PromptContent:
    positive_prompt: str
    negative_prompt: str = ""
    warning: str | None = None


@dataclass(frozen=True)
class _Heading:
    start: int
    end: int
    level: int
    title: str
    inline: str = ""


def _normal(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().strip("*_:： \t")).casefold()


def _kind(title: str) -> str:
    title = _normal(title)
    if title in _POSITIVE:
        return "positive"
    if title in _NEGATIVE:
        return "negative"
    return "generic" if title in _GENERIC else ""


def _label(line: str) -> tuple[str, str] | None:
    text = re.sub(r"^\s*(?:[-+*]|\d+[.)、])\s+", "", line).strip()
    # Colons may be inside or outside emphasis: **Prompt:** text / **Prompt**: text.
    match = re.match(r"^(.*?)[:：]\s*(.*)$", text)
    if match and _kind(match[1]):
        body = match[2]
        prefix = match[1].strip()
        for emphasis in ("**", "__"):
            if prefix.startswith(emphasis) and not prefix.endswith(emphasis):
                body = re.sub(r"^" + re.escape(emphasis) + r"\s*", "", body)
        return match[1], body
    if _kind(text):
        return text, ""
    return None


def _unwrap(doc: str) -> str:
    """Unwrap only a fence enclosing the entire document, never an inner block."""
    text = doc.strip()
    while True:
        lines = text.splitlines()
        opening = _FENCE.match(lines[0]) if lines else None
        if not opening or len(lines) < 2:
            return text
        marker = opening[1]
        closing = re.compile(r"^ {0,3}" + re.escape(marker[0]) + r"{" + str(len(marker)) + r",}\s*$")
        ends = [i for i, line in enumerate(lines[1:], 1) if closing.match(line)]
        if ends != [len(lines) - 1]:
            return text
        text = "\n".join(lines[1:-1]).strip()


def _headings(lines: list[str]) -> list[_Heading]:
    result = []
    fence = ""
    index = 0
    while index < len(lines):
        line = lines[index]
        marker = _FENCE.match(line)
        if marker:
            if not fence:
                fence = marker[1]
            elif marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = ""
            index += 1
            continue
        if fence:
            index += 1
            continue
        atx = _ATX.match(line)
        if atx:
            title = re.sub(r"\s+#+\s*$", "", atx[2] or "")
            label = _label(title)
            result.append(_Heading(index, index + 1, len(atx[1]),
                                   label[0] if label else title, label[1] if label else ""))
        elif line.strip() and index + 1 < len(lines) and _SETEXT.match(lines[index + 1]):
            level = 1 if lines[index + 1].lstrip().startswith("=") else 2
            label = _label(line)
            result.append(_Heading(index, index + 2, level,
                                   label[0] if label else line.strip(),
                                   label[1] if label else ""))
            index += 1
        else:
            label = _label(line)
            if label:
                # Bold/inline labels delimit prompt sections, but ordinary bold text does not.
                result.append(_Heading(index, index + 1, 7, *label))
        index += 1
    return result


def has_prompt_body(text: str) -> bool:
    """True for meaningful body text, false for headings, fences and empty markup."""
    lines = [line for line in _unwrap(text).splitlines() if not _FENCE.match(line)]
    headings = _headings(lines)
    ignored = {i for heading in headings for i in range(heading.start, heading.end)}
    if any(any(char.isalnum() for char in heading.inline) for heading in headings):
        return True
    for index, line in enumerate(lines):
        if index not in ignored and any(char.isalnum() for char in line):
            return True
    return False


def extract_prompt(doc: str) -> PromptContent:
    """Extract complete sections, or preserve the original with a repair warning.

    An empty result is deliberately invalid: callers must reject it before
    constructing a prepared prompt. No warning is added to GenerationData.
    """
    if not has_prompt_body(doc):
        return PromptContent("")
    text = _unwrap(doc)
    lines = text.splitlines()
    fence = ""
    for line in lines:
        marker = _FENCE.match(line)
        if marker:
            if not fence:
                fence = marker[1]
            elif marker[1][0] == fence[0] and len(marker[1]) >= len(fence) and not marker[2].strip():
                fence = ""
    if fence:
        return PromptContent(doc, warning=_WARNING)
    headings = _headings(lines)
    exact_positive = any(_kind(heading.title) == "positive" for heading in headings)
    candidates = [heading for heading in headings if _kind(heading.title)]
    selected = [heading for heading in candidates
                if _kind(heading.title) != "generic" or not exact_positive]
    # A generic parent is a document title when it wraps explicit prompt sections.
    selected = [heading for heading in selected if not (
        _kind(heading.title) == "generic" and any(
            other.start > heading.start and other.level > heading.level
            and _kind(other.title) in {"positive", "negative"} for other in candidates
        )
    )]
    if not selected:
        ambiguous = bool(headings or _INLINE_LABEL.search(text)
                         or any(_FENCE.match(line) for line in lines))
        return PromptContent(doc if ambiguous else text, warning=_WARNING if ambiguous else None)

    parts: dict[str, list[str]] = {"positive": [], "negative": []}
    covered: set[int] = set()
    ambiguous = False
    for heading in selected:
        end = len(lines)
        for other in headings:
            if other.start <= heading.start:
                continue
            if other in selected or (heading.level != 7 and other.level <= heading.level):
                end = other.start
                if other in selected and other.level != heading.level:
                    ambiguous = True
                break
        body = "\n".join(([heading.inline] if heading.inline else []) + lines[heading.end:end]).strip()
        kind = "negative" if _kind(heading.title) == "negative" else "positive"
        parts[kind].append(body)
        covered.update(range(heading.start, end))
        # A second label in an inline body cannot safely be treated as positive text.
        if _INLINE_LABEL.search(heading.inline):
            ambiguous = True

    remaining = "\n".join(line for index, line in enumerate(lines) if index not in covered)
    positive = parts["positive"][0] if parts["positive"] else ""
    negative = parts["negative"][0] if parts["negative"] else ""
    if (ambiguous or has_prompt_body(remaining) or not has_prompt_body(positive)
            or len(parts["positive"]) > 1 or len(parts["negative"]) > 1):
        return PromptContent(doc, warning=_WARNING)
    return PromptContent(positive, negative if has_prompt_body(negative) else "")
