from __future__ import annotations

from html.parser import HTMLParser
from typing import Any

import ssmd
from markdown_it import MarkdownIt
from mdit_py_plugins.gfm import gfm_plugin

_HIDDEN_HTML_TAGS = frozenset({"script", "style", "template", "noscript", "iframe", "object"})


class _VisibleHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._hidden_depth = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() in _HIDDEN_HTML_TAGS:
            self._hidden_depth += 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        if tag.lower() not in _HIDDEN_HTML_TAGS:
            return

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in _HIDDEN_HTML_TAGS and self._hidden_depth:
            self._hidden_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._hidden_depth:
            self.parts.append(data)

    def handle_comment(self, data: str) -> None:
        del data


def _visible_html(source: str) -> str:
    parser = _VisibleHTMLParser()
    parser.feed(source)
    parser.close()
    return "".join(parser.parts)


def _strip_front_matter(source: str) -> str:
    lines = source.splitlines()
    if not lines or lines[0].lstrip("\ufeff").strip() != "---":
        return source
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() in {"---", "..."}:
            return "\n".join(lines[index + 1 :])
    return source


def _normalise_inline(text: str) -> str:
    lines = [" ".join(line.split()) for line in text.splitlines()]
    return "\n".join(lines).strip()


def _sentence(text: str) -> str:
    text = _normalise_inline(text)
    if text and text[-1] not in ".!?;:":
        return f"{text}."
    return text


def _render_inline(token: Any) -> str:
    children = token.children or []
    parts: list[str] = []
    for child in children:
        kind = child.type
        if kind in {"text", "code_inline"}:
            parts.append(child.content)
        elif kind in {"softbreak", "hardbreak"}:
            parts.append(" " if kind == "softbreak" else "\n")
        elif kind == "image":
            alt = child.content.strip()
            if alt:
                parts.append(_sentence(f"Image: {alt}"))
        elif kind == "html_inline":
            parts.append(_visible_html(child.content))
        elif kind == "footnote_ref" or kind in {
            "link_open",
            "link_close",
            "em_open",
            "em_close",
            "strong_open",
            "strong_close",
            "s_open",
            "s_close",
        }:
            continue
        else:
            parts.append(child.content)
    return _normalise_inline("".join(parts))


class _Node:
    def __init__(self, token: Any) -> None:
        self.token = token
        self.children: list[_Node] = []


def _build_tree(tokens: list[Any]) -> list[_Node]:
    root = _Node(None)
    stack = [root]
    for token in tokens:
        if token.nesting == 1:
            node = _Node(token)
            stack[-1].children.append(node)
            stack.append(node)
        elif token.nesting == -1:
            if len(stack) > 1:
                stack.pop()
        else:
            stack[-1].children.append(_Node(token))
    return root.children


def _inline_child(node: _Node) -> str:
    for child in node.children:
        if child.token.type == "inline":
            return _render_inline(child.token)
    return ""


def _render_table(node: _Node) -> list[str]:
    rows: list[tuple[bool, list[str]]] = []

    def visit(current: _Node, in_head: bool = False) -> None:
        kind = current.token.type
        if kind == "thead_open":
            in_head = True
        if kind == "tr_open":
            cells = [
                _inline_child(cell)
                for cell in current.children
                if cell.token.type in {"th_open", "td_open"}
            ]
            rows.append((in_head, cells))
            return
        for child in current.children:
            visit(child, in_head)

    visit(node)
    if not rows:
        return []
    headers = rows[0][1] if rows[0][0] else []
    result = ["Table."]
    for is_header, cells in rows:
        if is_header:
            continue
        if headers:
            values = [
                _sentence(f"{header}: {value}")
                for header, value in zip(headers, cells)
                if header and value
            ]
        else:
            values = [_sentence(value) for value in cells if value]
        if values:
            result.append(" ".join(values))
    if not headers and rows[0][1]:
        result.append(" ".join(_sentence(value) for value in rows[0][1] if value))
    result.append("End table.")
    return result


def _render_list(node: _Node, ordered: bool) -> list[str]:
    start = int((node.token.attrs or {}).get("start", 1)) if ordered else 1
    result: list[str] = []
    number = start
    for child in node.children:
        if child.token.type != "list_item_open":
            continue
        paragraphs = [
            _inline_child(item) for item in child.children if item.token.type == "paragraph_open"
        ]
        nested = [
            nested_block
            for item in child.children
            if item.token.type in {"bullet_list_open", "ordered_list_open"}
            for nested_block in _render_list(item, item.token.type == "ordered_list_open")
        ]
        if paragraphs and paragraphs[0]:
            text = paragraphs[0]
            checked = (child.token.meta or {}).get("checked")
            if checked is True:
                result.append(_sentence(f"Checked: {text}"))
            elif checked is False:
                result.append(_sentence(f"Unchecked: {text}"))
            elif ordered:
                result.append(_sentence(f"{number}. {text}"))
            else:
                result.append(_sentence(f"Item: {text}"))
        result.extend(_normalise_inline(value) for value in paragraphs[1:] if value)
        result.extend(nested)
        number += 1
    return result


def _render_nodes(nodes: list[_Node]) -> list[str]:
    blocks: list[str] = []
    for node in nodes:
        kind = node.token.type
        if kind == "heading_open":
            text = _inline_child(node)
            if text:
                blocks.append(_sentence(text))
        elif kind == "paragraph_open":
            text = _inline_child(node)
            if text:
                blocks.append(text)
        elif kind == "bullet_list_open":
            blocks.extend(_render_list(node, ordered=False))
        elif kind == "ordered_list_open":
            blocks.extend(_render_list(node, ordered=True))
        elif kind == "blockquote_open":
            quoted = _render_nodes(node.children)
            if quoted:
                if quoted[0].startswith("Quote: "):
                    blocks.extend(quoted)
                else:
                    quoted[0] = _sentence(f"Quote: {quoted[0]}")
                    blocks.extend(quoted)
        elif kind in {"fence", "code_block"}:
            code = node.token.content.rstrip("\n")
            blocks.append(
                "\n".join(
                    ["Code block.", code, "End code block."]
                    if code
                    else ["Code block.", "End code block."]
                )
            )
        elif kind == "hr":
            blocks.append("<paragraph pause>")
        elif kind == "table_open":
            blocks.extend(_render_table(node))
        elif kind == "html_block":
            visible = _normalise_inline(_visible_html(node.token.content))
            if visible:
                blocks.append(visible)
        elif kind == "footnote_block_open":
            blocks.extend(_render_nodes(node.children))
        elif kind == "footnote_open":
            label = (node.token.meta or {}).get("label", "")
            content = _render_nodes(node.children)
            if content:
                blocks.append(_sentence(f"Footnote {label}. {content[0]}"))
                blocks.extend(content[1:])
        elif kind == "inline":
            text = _render_inline(node.token)
            if text:
                blocks.append(text)
    return blocks


def markdown_to_speech(text: str) -> str:
    """Project a complete Markdown document into safe, speech-friendly text."""
    source = _strip_front_matter(text)
    parser = MarkdownIt("commonmark", {"html": True})
    gfm_plugin(parser)
    tokens = parser.parse(source)
    blocks = [_normalise_inline(block) for block in _render_nodes(_build_tree(tokens))]
    projected = "\n\n".join(block for block in blocks if block)
    return ssmd.escape_ssmd_syntax(projected).strip()
