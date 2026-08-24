from readio.config import ReadioConfig
from readio.document import InputDocument
from readio.reader import pipeline_config_for_document


def test_pipeline_config_for_ssmd_uses_default_only_bindings():
    document = InputDocument(
        text='---\nvoice_bindings:\n  kokoro:\n    host: af_bella\n---\n<div voice="host">Hello.</div>',
        source_path=None,
        format="ssmd",
    )

    pipeline = pipeline_config_for_document(document, ReadioConfig())

    assert pipeline.voice == "af_sarah"
    assert pipeline.ssmd.provider == "kokoro"
    assert dict(pipeline.ssmd.voice_bindings["kokoro"]) == {
        "analyst": "am_michael",
        "guest": "af_bella",
        "narrator": "af_sarah",
    }


def test_pipeline_config_for_text_does_not_add_role_bindings():
    document = InputDocument("Hello", None, "text")

    pipeline = pipeline_config_for_document(document, ReadioConfig())

    assert dict(pipeline.ssmd.voice_bindings) == {}
