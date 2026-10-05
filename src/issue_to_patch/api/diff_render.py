"""Render a unified-diff / git-format-patch blob as colored HTML.

GitHub-style split view with aligned original and modified lines, independent
line numbers, and highlighted edits. No templating library is required.

Unlike that renderer, this one displays arbitrary repository source, so
every line of code content is ``html.escape()``d before being embedded.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from itertools import zip_longest
from pathlib import Path

_FILE_HEADER_RE = re.compile(r"^diff --git a/(.+) b/(.+)$")
_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")

_STYLE = "<style>" + Path(__file__).with_name("diff.css").read_text() + "</style>"


@dataclass
class _Line:
    kind: str  # "add" | "del" | "ctx"
    old_no: int | None
    new_no: int | None
    text: str


def render_diff_html(patch_text: str) -> str:
    """Parse ``patch_text`` into a self-contained HTML fragment: one block
    per changed file, each line colored and gutter-numbered."""
    files = _parse(patch_text)
    if not files:
        return f"{_STYLE}<div class='itp-diff'><div class='file-header'>(empty diff)</div></div>"
    added = sum(line.kind == "add" for _, lines in files for line in lines)
    removed = sum(line.kind == "del" for _, lines in files for line in lines)
    links = "".join(
        f"<a href='#diff-file-{index}'>{html.escape(path)}</a>"
        for index, (path, _) in enumerate(files)
    )
    blocks = [_render_file(path, lines, index) for index, (path, lines) in enumerate(files)]
    return (
        _STYLE + "<section class='diff-review' aria-label='Patch changes'>"
        f"<div class='diff-summary'><b>{len(files)} changed files</b>"
        f"<span class='diff-added'>+{added} added</span>"
        f"<span class='diff-removed'>&minus;{removed} removed</span></div>"
        "<p class='diff-help'>Current version on the left; modified version on the right. "
        "Showing changed sections with patch context. "
        "Darker highlights show changed text. Long lines wrap automatically.</p>"
        f"<nav class='diff-files' aria-label='Changed files'>{links}</nav>"
        + "\n".join(blocks)
        + "</section>"
    )


def _parse(patch_text: str) -> list[tuple[str, list[_Line]]]:
    files: list[tuple[str, list[_Line]]] = []
    current_path: str | None = None
    current_lines: list[_Line] = []
    old_no = new_no = 0

    for raw_line in patch_text.splitlines():
        header = _FILE_HEADER_RE.match(raw_line)
        if header is not None:
            if current_path is not None:
                files.append((current_path, current_lines))
            current_path = header.group(2)
            current_lines = []
            continue
        if raw_line.startswith(("--- ", "+++ ")) and not current_lines:
            continue
        hunk = _HUNK_HEADER_RE.match(raw_line)
        if hunk is not None:
            old_no, new_no = int(hunk.group(1)), int(hunk.group(2))
            current_lines.append(_Line("hunk", None, None, raw_line))
            continue
        if current_path is None:
            continue  # preamble (From/Date/Subject/--- stat lines) before the first file
        if raw_line.startswith("+"):
            current_lines.append(_Line("add", None, new_no, raw_line[1:]))
            new_no += 1
        elif raw_line.startswith("-"):
            current_lines.append(_Line("del", old_no, None, raw_line[1:]))
            old_no += 1
        elif raw_line.startswith(" "):
            current_lines.append(_Line("ctx", old_no, new_no, raw_line[1:]))
            old_no += 1
            new_no += 1
        # anything else (e.g. "\ No newline at end of file") is skipped

    if current_path is not None:
        files.append((current_path, current_lines))
    return files


def _highlight_changes(lines: list[_Line]) -> dict[int, str]:
    """Compare contiguous change blocks, including many-to-one reformatted lines."""
    result: dict[int, str] = {}
    cursor = 0
    while cursor < len(lines):
        if lines[cursor].kind not in {"add", "del"}:
            cursor += 1
            continue
        end = cursor
        while end < len(lines) and lines[end].kind in {"add", "del"}:
            end += 1
        groups = [
            [i for i in range(cursor, end) if lines[i].kind == kind] for kind in ("del", "add")
        ]
        texts = ["\n".join(lines[i].text for i in group) for group in groups]
        # Bound comparison work for generated files; wrapping still shows every line.
        if all(groups) and sum(map(len, texts)) <= 40000:
            tokens = [re.findall(r"\w+|[^\w\s]|\s+", text) for text in texts]
            if sum(map(len, tokens)) <= 6000:
                marked: list[list[str]] = [[], []]
                matcher = SequenceMatcher(None, tokens[0], tokens[1], autojunk=False)
                for tag, a, b, c, d in matcher.get_opcodes():
                    for side, start, stop in ((0, a, b), (1, c, d)):
                        fragment = "".join(tokens[side][start:stop])
                        # Close marks at newlines so each table cell is valid HTML.
                        marked[side].append(
                            "\n".join(
                                f"<mark>{html.escape(part)}</mark>"
                                if tag != "equal" and part.strip()
                                else html.escape(part)
                                for part in fragment.split("\n")
                            )
                        )
                for group, fragments in zip(groups, marked, strict=True):
                    for index, rendered in zip(group, "".join(fragments).split("\n"), strict=True):
                        result[index] = rendered
        cursor = end
    return result


def _render_file(path: str, lines: list[_Line], index: int) -> str:
    highlighted = _highlight_changes(lines)
    rows = _render_split_rows(lines, highlighted)
    added = sum(line.kind == "add" for line in lines)
    removed = sum(line.kind == "del" for line in lines)
    return (
        f"<div class='itp-diff' id='diff-file-{index}'>"
        f"<div class='file-header'>{html.escape(path)}"
        f"<span class='file-counts'>+{added} / &minus;{removed}</span></div>"
        "<details open><summary>Show / hide changes</summary>"
        "<div class='diff-table-wrap'><table aria-label='Code changes'>"
        "<colgroup><col class='number-col'><col><col class='number-col'><col></colgroup>"
        "<thead><tr><th colspan='2' scope='colgroup'>Current version</th>"
        "<th colspan='2' scope='colgroup'>Modified version</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div></details></div>"
    )


def _split_cell(line: _Line | None, side: str, highlighted: str | None = None) -> str:
    if line is None:
        return "<td class='ln blank'></td><td class='code blank'></td>"
    number = line.old_no if side == "old" else line.new_no
    content = highlighted if highlighted is not None else html.escape(line.text)
    return (
        f"<td class='ln {line.kind}'>{number}</td>"
        f"<td class='code {line.kind}' title='{html.escape(line.text)}'>{content}</td>"
    )


def _render_split_rows(lines: list[_Line], highlighted: dict[int, str]) -> str:
    rows: list[str] = []
    cursor = 0
    while cursor < len(lines):
        line = lines[cursor]
        if line.kind == "hunk":
            rows.append(f"<tr class='hunk'><td colspan='4'>{html.escape(line.text)}</td></tr>")
            cursor += 1
        elif line.kind == "ctx":
            rows.append(
                "<tr class='ctx'>" + _split_cell(line, "old") + _split_cell(line, "new") + "</tr>"
            )
            cursor += 1
        else:
            removed: list[int] = []
            added: list[int] = []
            while cursor < len(lines) and lines[cursor].kind in {"add", "del"}:
                (removed if lines[cursor].kind == "del" else added).append(cursor)
                cursor += 1
            for old, new in zip_longest(removed, added):
                rows.append(
                    "<tr class='change'>"
                    + _split_cell(
                        lines[old] if old is not None else None,
                        "old",
                        highlighted.get(old) if old is not None else None,
                    )
                    + _split_cell(
                        lines[new] if new is not None else None,
                        "new",
                        highlighted.get(new) if new is not None else None,
                    )
                    + "</tr>"
                )
    return "\n".join(rows)
