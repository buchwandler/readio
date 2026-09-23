from __future__ import annotations

from pathlib import Path

from ebooklib import epub


def make_epub(path: Path, *, with_navigation: bool = True) -> None:
    book = epub.EpubBook()
    book.set_identifier("readio-test-book")
    book.set_title("The Example")
    book.add_author("A. Writer")
    book.set_language("en")
    chapters = []
    for index, (title, body) in enumerate(
        (
            ("Chapter One", "Alpha passage."),
            ("Chapter Two", "Alpha passage."),
            ("Chapter Three", "Gamma passage."),
            ("Chapter Four", "Delta passage."),
            ("Epilogue", "Epsilon passage."),
        ),
        1,
    ):
        chapter = epub.EpubHtml(
            title=title,
            file_name=f"chapter{index}.xhtml",
            lang="en",
        )
        chapter.content = f"<html><head></head><body><h1>{title}</h1><p>{body}</p></body></html>"
        book.add_item(chapter)
        chapters.append(chapter)
    book.add_item(epub.EpubNav())
    book.add_item(epub.EpubNcx())
    if with_navigation:
        book.toc = (
            (
                epub.Section("Part One"),
                (
                    epub.Link("chapter1.xhtml", "Chapter One", "chapter-one"),
                    epub.Link("chapter2.xhtml", "Chapter Two", "chapter-two"),
                ),
            ),
            (
                epub.Section("Part Two"),
                (
                    epub.Link("chapter3.xhtml", "Chapter Three", "chapter-three"),
                    epub.Link("chapter4.xhtml", "Chapter Four", "chapter-four"),
                ),
            ),
            epub.Link("chapter5.xhtml", "Epilogue", "epilogue"),
        )
        book.spine = ["nav", *chapters]
    else:
        book.toc = ()
        book.spine = [*chapters]
    epub.write_epub(str(path), book)
