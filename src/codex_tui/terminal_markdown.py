from __future__ import annotations

import re
import textwrap


FENCE_RE = re.compile(r"^\s*(```|~~~)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
LIST_RE = re.compile(r"^(\s*)((?:[-*+]|\d+[.)])\s+)(.*)$")
QUOTE_RE = re.compile(r"^\s*>\s?(.*)$")
RULE_RE = re.compile(r"^\s*([-*_])(?:\s*\1){2,}\s*$")
TABLE_SEPARATOR_CELL_RE = re.compile(r"^:?-{3,}:?$")


def render_markdown_lines(text: str, *, width: int) -> list[str]:
    """Render a conservative Markdown subset into terminal-width rows."""
    rows: list[str] = []
    paragraph: list[str] = []
    in_code = False
    raw_lines = text.rstrip().splitlines()
    index = 0

    def flush_paragraph() -> None:
        if not paragraph:
            return
        rows.extend(wrap_text(" ".join(paragraph), width=width))
        paragraph.clear()

    while index < len(raw_lines):
        raw_line = raw_lines[index]
        line = raw_line.expandtabs(4).rstrip()
        stripped = line.strip()
        if FENCE_RE.match(stripped):
            flush_paragraph()
            rows.extend(wrap_code_line(stripped, width=width))
            in_code = not in_code
            index += 1
            continue
        if in_code:
            rows.extend(wrap_code_line(line, width=width))
            index += 1
            continue
        if not stripped:
            flush_paragraph()
            rows.append("")
            index += 1
            continue

        if starts_table(raw_lines, index):
            flush_paragraph()
            table_lines: list[str] = []
            while index < len(raw_lines):
                table_line = raw_lines[index].expandtabs(4).rstrip()
                table_stripped = table_line.strip()
                if not table_stripped or not is_table_row(table_stripped):
                    break
                table_lines.append(table_line)
                index += 1
            rows.extend(render_table_lines(table_lines, width=width))
            continue

        heading = HEADING_RE.match(stripped)
        if heading:
            flush_paragraph()
            prefix = f"{heading.group(1)} "
            rows.extend(wrap_text(heading.group(2), width=width, initial=prefix, subsequent=" " * len(prefix)))
            index += 1
            continue

        list_item = LIST_RE.match(line)
        if list_item:
            flush_paragraph()
            indent, marker, body = list_item.groups()
            prefix = f"{indent}{marker}"
            rows.extend(wrap_text(body.strip(), width=width, initial=prefix, subsequent=" " * len(prefix)))
            index += 1
            continue

        quote = QUOTE_RE.match(line)
        if quote:
            flush_paragraph()
            rows.extend(wrap_text(quote.group(1), width=width, initial="> ", subsequent="> "))
            index += 1
            continue

        if RULE_RE.match(stripped):
            flush_paragraph()
            rows.append("-" * max(3, min(width, 72)))
            index += 1
            continue

        paragraph.append(stripped)
        index += 1

    flush_paragraph()
    return rows or [""]


def render_code_block_lines(text: str, *, width: int, language: str = "") -> list[str]:
    fence = f"```{language}" if language else "```"
    rows = wrap_code_line(fence, width=width)
    for line in text.rstrip().splitlines():
        rows.extend(wrap_code_line(line.expandtabs(4).rstrip(), width=width))
    rows.extend(wrap_code_line("```", width=width))
    return rows


def starts_table(lines: list[str], index: int) -> bool:
    if index + 1 >= len(lines):
        return False
    current = lines[index].strip()
    separator = lines[index + 1].strip()
    return is_table_row(current) and is_table_separator(separator)


def is_table_row(line: str) -> bool:
    return len(table_cells(line)) >= 2


def is_table_separator(line: str) -> bool:
    cells = table_cells(line)
    return len(cells) >= 2 and all(TABLE_SEPARATOR_CELL_RE.fullmatch(cell.strip()) for cell in cells)


def table_cells(line: str) -> list[str]:
    if "|" not in line:
        return []
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def render_table_lines(lines: list[str], *, width: int) -> list[str]:
    parsed = [table_cells(line) for line in lines]
    column_count = max((len(row) for row in parsed), default=0)
    if column_count < 2:
        return []
    normalized = [row + [""] * (column_count - len(row)) for row in parsed]
    widths = [
        max(3, *(len(row[column]) for row in normalized if not is_separator_cells(row)))
        for column in range(column_count)
    ]
    rendered: list[str] = []
    for row in normalized:
        if is_separator_cells(row):
            cells = ["-" * column_width for column_width in widths]
        else:
            cells = [cell.ljust(widths[column]) for column, cell in enumerate(row)]
        rendered.append(f"| {' | '.join(cells)} |")
    if all(len(row) <= width for row in rendered):
        return rendered
    fallback: list[str] = []
    for line in lines:
        fallback.extend(wrap_code_line(line.strip(), width=width))
    return fallback


def is_separator_cells(cells: list[str]) -> bool:
    return cells and all(TABLE_SEPARATOR_CELL_RE.fullmatch(cell.strip()) for cell in cells)


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
