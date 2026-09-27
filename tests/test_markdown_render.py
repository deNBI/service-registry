from apps.submissions.markdown_render import markdown_enabled


def test_markdown_enabled_reads_flag(settings):
    settings.SITE_CONFIG = {"features": {"markdown_descriptions": True}}
    assert markdown_enabled() is True


def test_markdown_enabled_defaults_false(settings):
    settings.SITE_CONFIG = {"features": {}}
    assert markdown_enabled() is False
