from importlib import resources

from readio.templates import packaged_template_names


def test_packaged_templates_are_available_as_resources():
    assert packaged_template_names() == ("briefing", "dialogue", "podcast")
    root = resources.files("readio.resources.templates")
    for name in packaged_template_names():
        resource = root.joinpath(f"{name}.ssmd")
        assert resource.is_file()
        assert resource.read_text(encoding="utf-8").strip()
