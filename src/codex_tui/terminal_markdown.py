from __future__ import annotations

import re
import textwrap


FENCE_RE = re.compile(r"^\s*(```|~~~)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
LIST_RE = re.compile(r"^(\s*)((?:[-*+]|\d+[.)])\s+)(.*)$")
QUOTE_RE = re.compile(r"^\s*>\s?(.*)$")
RULE_RE = re.compile(r"^\s*([-*_])(?:\s*\1){2,}\s*$")


def render_markdown_lines(text: str, *, width: int) -> list[str]:
    """Render a conservative Markdown subset into terminal-width rows."""
    rows: list[str] = []
    paragraph: list[str] = []
    in_code = False

    def flush_paragraph() -> None:
        if not paragraph:
            return
        rows.extend(wrap_text(" ".join(paragraph), width=width))
        paragraph.clear()

    for raw_line in text.rstrip().splitlines():
        line = raw_line.expandtabs(4).rstrip()
        stripped = line.strip()
        if FENCE_RE.match(stripped):
            flush_paragraph()
            rows.extend(wrap_code_line(stripped, width=width))
            in_code = not in_code
            continue
        if in_code:
            rows.extend(wrap_code_line(line, width=width))
            continue
        if not stripped:
            flush_paragraph()
            rows.append("")
            continue

        heading = HEADING_RE.match(stripped)
        if heading:
            flush_paragraph()
            prefix = f"{heading.group(1)} "
            rows.extend(wrap_text(heading.group(2), width=width, initial=prefix, subsequent=" " * len(prefix)))
            continue

        list_item = LIST_RE.match(line)
        if list_item:
            flush_paragraph()
            indent, marker, body = list_item.groups()
            prefix = f"{indent}{marker}"
            rows.extend(wrap_text(body.strip(), width=width, initial=prefix, subsequent=" " * len(prefix)))
            continue

        quote = QUOTE_RE.match(line)
        if quote:
            flush_paragraph()
            rows.extend(wrap_text(quote.group(1), width=width, initial="> ", subsequent="> "))
            continue

        if RULE_RE.match(stripped):
            flush_paragraph()
            rows.append("-" * max(3, min(width, 72)))
            continue

        paragraph.append(stripped)

    flush_paragraph()
    return rows or [""]


def render_code_block_lines(text: str, *, width: int, language: str = "") -> list[str]:
    fence = f"```{language}" if language else "```"
    rows = wrap_code_line(fence, width=width)
    for line in text.rstrip().splitlines():
        rows.extend(wrap_code_line(line.expandtabs(4).rstrip(), width=width))
    rows.extend(wrap_code_line("```", width=width))
    return rows


def wrap_text(text: str, *, width: int, initial: str = "", subsequent: str = "") -> list[str]:
    safe_width = max(1, width)
    wrapper = textwrap.TextWrapper(
        width=safe_width,
        initial_indent=initial,
        subsequent_indent=subsequent,
        replace_whitespace=True,
        drop_whitespace=True,
        break_long_words=True,
        break_on_hyphens=False,
    )
    wrapped = wrapper.wrap(text)
    if not wrapped:
        return [initial.rstrip()]
    return [chunk for line in wrapped for chunk in hard_wrap(line, safe_width)]


def wrap_code_line(line: str, *, width: int) -> list[str]:
    safe_width = max(1, width)
    if len(line) <= safe_width:
        return [line]
    rows = [line[:safe_width]]
    rest = line[safe_width:]
    indent_len = len(line) - len(line.lstrip(" "))
    continuation = " " * min(indent_len + 2, max(0, safe_width - 1))
    chunk_width = max(1, safe_width - len(continuation))
    while rest:
        rows.append(continuation + rest[:chunk_width])
        rest = rest[chunk_width:]
    return rows


def hard_wrap(line: str, width: int) -> list[str]:
    if len(line) <= width:
        return [line]
    return [line[index : index + width] for index in range(0, len(line), width)]
