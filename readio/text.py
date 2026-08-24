from __future__ import annotations

from collections.abc import Iterable, Iterator


def iter_live_paragraphs(lines: Iterable[str]) -> Iterator[str]:
    """Yield paragraphs as soon as a blank line closes them.

    This is intentionally a small live-input framing rule. For non-live input,
    PyKokoro itself remains responsible for document parsing and segmentation.
    """
    buffer: list[str] = []
    for line in lines:
        if line.strip():
            buffer.append(line)
            continue
        if buffer:
            text = "".join(buffer).strip()
            buffer.clear()
            if text:
                yield text
    if buffer:
        text = "".join(buffer).strip()
        if text:
            yield text
