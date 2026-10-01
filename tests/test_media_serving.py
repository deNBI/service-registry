"""
Uploaded media (service logos) served from /media/.

Logos are displayed with <img>, where the response's own policy header has no
effect. The strict policy applies when a logo URL is opened directly.
"""

import pytest

SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><rect width="1" height="1"/></svg>'


@pytest.fixture
def media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    (tmp_path / "logos").mkdir()
    (tmp_path / "logos" / "a.svg").write_bytes(SVG)
    return tmp_path


def _directives(resp) -> dict[str, str]:
    header = resp["Content-Security-Policy"]
    parts = [p.strip() for p in header.split(";") if p.strip()]
    return {p.split(" ", 1)[0]: (p.split(" ", 1) + [""])[1] for p in parts}


@pytest.mark.django_db
class TestMediaPolicyHeader:
    def test_file_is_served_unchanged(self, client, media):
        resp = client.get("/media/logos/a.svg")
        assert resp.status_code == 200
        assert b"".join(resp.streaming_content) == SVG

    def test_media_responses_are_sandboxed(self, client, media):
        d = _directives(client.get("/media/logos/a.svg"))
        assert "sandbox" in d
        assert d["default-src"] == "'none'"

    def test_media_policy_allows_only_inline_styles_and_data_resources(
        self, client, media
    ):
        d = _directives(client.get("/media/logos/a.svg"))
        assert d["style-src"] == "'unsafe-inline'"
        assert d["img-src"] == "data:"
        assert d["font-src"] == "data:"
        assert "script-src" not in d  # falls back to default-src 'none'

    def test_site_pages_keep_the_site_policy(self, client):
        d = _directives(client.get("/"))
        assert "sandbox" not in d
        assert "'self'" in d["default-src"]
