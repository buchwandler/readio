from importlib import resources


def test_readio_does_not_package_authoring_templates():
    readio_resources = resources.files("readio.resources")
    template_resources = readio_resources.joinpath("templates")
    assert not template_resources.is_dir()
