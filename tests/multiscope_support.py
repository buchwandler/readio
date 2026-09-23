from __future__ import annotations

from pathlib import Path

from audiobook_support import make_epub

from readio.audiobook import init_audiobook_project
from readio.config import ReaderSettings, ReadioConfig
from readio.plan import InputRequest, OutputRequest, PlanRequest, SynthesisRequest


def make_audiobook_project(tmp_path: Path, chapters: str = "2-4,5"):
    source = tmp_path / "book.epub"
    make_epub(source)
    project = init_audiobook_project(
        source,
        tmp_path / "book.readio",
        chapters=chapters,
    )
    cfg = ReadioConfig(reader=ReaderSettings(engine="fake", voice="fake-voice", spacy="off"))
    request = PlanRequest(
        "render",
        InputRequest(project.load_document_scope(project.document_scopes()[0])),
        SynthesisRequest(engine="fake", voice="fake-voice"),
        OutputRequest(mode="file", requested_format="wav", force=True),
    )
    return project, cfg, request
