"""The bundled host nginx vhost lets logo uploads through on every upload
endpoint: the server-wide 64k body limit is raised to 12m on each of them."""

import re
from pathlib import Path

import pytest

CONF = (
    Path(__file__).resolve().parent.parent
    / "nginx/host/service-registry.bi.denbi.de.conf"
)


def _location_body(conf: str, location: str) -> str:
    """Directives of the (uncommented) `location <location> {` block; these
    blocks contain no nested braces."""
    m = re.search(
        r"^\s*location\s+" + re.escape(location) + r"\s*\{([^{}]*)\}", conf, re.M
    )
    assert m, f"no active location {location} block"
    return "\n".join(line.split("#", 1)[0] for line in m.group(1).splitlines())


@pytest.mark.parametrize(
    "location", ["/api/", "~ ^/(register|update)/", "/admin-denbi/"]
)
def test_upload_locations_allow_12m_bodies(location):
    body = _location_body(CONF.read_text(), location)
    assert re.search(r"client_max_body_size\s+12m;", body)
    assert "proxy_pass http://registry_backend;" in body


def test_admin_location_matches_the_default_admin_prefix():
    # The bundled file is written for the default prefix; deployment.md says
    # to change the location path together with ADMIN_URL_PREFIX.
    from config.settings import _sc_admin

    default = _sc_admin.get("url_prefix", "admin-denbi").strip("/")
    _location_body(CONF.read_text(), f"/{default}/")
