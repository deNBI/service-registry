"""Output matrix: every surface that outputs service_description, for both
states of the markdown_descriptions flag, against a fixed set of input
classes, with the EXACT expected output per cell.

Expectations are literals written from the spec, not computed by the code
under test. The rendered-HTML surfaces (catalogue list, API detail with the
flag on, admin view-only row) are specified as "identical to
render_markdown(stored)", so those cells also assert equality to it.

Rows are created through the model, so every surface sees what save()
stores (NFC-normalised and stripped, see STORED); the preview endpoint gets
the typed input and normalises it itself. Display surfaces decode legacy
HTML entities exactly once in both flag states; source surfaces (raw API
field, admin textarea, exports) show the stored value.

Each (surface, flag, input) cell is its own test id, e.g.
``api_detail_html-flag_off-legacy_entities``.
"""

import copy
import csv
import hashlib
import io
import json
import re
import secrets
from io import StringIO

import pytest
from django.conf import settings as django_settings
from django.core import mail
from django.core.cache import cache
from django.core.management import call_command
from django.core.management.base import CommandError
from django.urls import reverse
from rest_framework.test import APIClient

from apps.submissions.diff_utils import _display
from apps.submissions.markdown_render import render_markdown
from tests.factories import ServiceSubmissionFactory

pytestmark = pytest.mark.django_db

# ---------------------------------------------------------------------------
# Input classes (as typed / submitted)
# ---------------------------------------------------------------------------

INPUTS = {
    "plain": "A plain service description for researchers.",
    "markdown": "# Title\n\n**bold** text\n\n- one\n- two\n\n[site](https://example.org)",
    "md_bullets": "- first\n- second",
    "legacy_entities": "x &gt; 5 &amp; y",
    "raw_html_xss": "<script>alert(1)</script> <img src=x onerror=1>",
    "legacy_encoded_markup": "&lt;script&gt;x&lt;/script&gt;",
    "blocked_link": "[x](javascript:alert(1))",
    "relative_link": "[x](/rel)",
    # Decomposed e + U+0301 and a CRLF line break.
    "crlf_unicode": "Zoë\r\nline two e\u0301",
    "empty": "",
    "whitespace": "  \n  ",
}

# What ServiceSubmission.save() stores: LF line endings, NFC-normalised (e +
# U+0301 becomes U+00E9) and stripped (whitespace-only becomes empty).
STORED = {**INPUTS, "crlf_unicode": "Zoë\nline two \u00e9", "whitespace": ""}

ABSENT = object()  # the surface does not show the description at all
NOT_FOUND = object()  # the surface answers 404

# ---------------------------------------------------------------------------
# Shared expectation tables (literals)
# ---------------------------------------------------------------------------

# Django autoescape of the stored value (source surfaces: admin textarea).
ESCAPED_STORED = {
    "plain": "A plain service description for researchers.",
    "markdown": "# Title\n\n**bold** text\n\n- one\n- two\n\n[site](https://example.org)",
    "md_bullets": "- first\n- second",
    "legacy_entities": "x &amp;gt; 5 &amp;amp; y",
    "raw_html_xss": "&lt;script&gt;alert(1)&lt;/script&gt; &lt;img src=x onerror=1&gt;",
    "legacy_encoded_markup": "&amp;lt;script&amp;gt;x&amp;lt;/script&amp;gt;",
    "blocked_link": "[x](javascript:alert(1))",
    "relative_link": "[x](/rel)",
    "crlf_unicode": "Zoë\nline two \u00e9",
    "empty": "",
    "whitespace": "",
}

# decode_legacy_entities(stored): html.unescape applied once (plain-text
# display surfaces: .txt emails).
DECODED = {
    **STORED,
    "legacy_entities": "x > 5 & y",
    "legacy_encoded_markup": "<script>x</script>",
}

# Decoded once, then escaped once (flag-off HTML display surfaces and every
# HTML email body).
DECODED_ESCAPED = {
    **ESCAPED_STORED,
    "legacy_entities": "x &gt; 5 &amp; y",
    "legacy_encoded_markup": "&lt;script&gt;x&lt;/script&gt;",
}

# render_markdown(stored): sanitized HTML.
RENDERED = {
    "plain": "<p>A plain service description for researchers.</p>",
    "markdown": (
        "<h4>Title</h4>\n<p><strong>bold</strong> text</p>\n"
        "<ul>\n<li>one</li>\n<li>two</li>\n</ul>\n"
        '<p><a href="https://example.org" rel="nofollow noopener noreferrer"'
        ' target="_blank">site</a></p>'
    ),
    "md_bullets": "<ul>\n<li>first</li>\n<li>second</li>\n</ul>",
    "legacy_entities": "<p>x &gt; 5 &amp; y</p>",
    "raw_html_xss": (
        "<p>&lt;script&gt;alert(1)&lt;/script&gt; &lt;img src=x onerror=1&gt;</p>"
    ),
    "legacy_encoded_markup": "<p>&lt;script&gt;x&lt;/script&gt;</p>",
    "blocked_link": "<p>x</p>",
    "relative_link": "<p>x</p>",
    "crlf_unicode": "<p>Zoë<br>\nline two \u00e9</p>",
    "empty": "",
    "whitespace": "",
}

_ALL = tuple(INPUTS)


def _both(table):
    return {"off": table, "on": table}


NOTICE = (
    '<div class="md-editor__alert" role="status">Some formatting was removed: '
    "images, code, horizontal rules, very deep nesting and links that do not "
    "start with http://, https:// or mailto: are not supported.</div>"
)
NOTHING = '<p class="md-editor__empty">Nothing to preview.</p>'


def _preview(inner, removed=False):
    frag = f'<div class="md-rendered">{inner}</div>'
    return f"{NOTICE}\n  \n  {frag}" if removed else frag


_NO_DESCRIPTION = {"empty": ABSENT, "whitespace": ABSENT}

EXPECTED = {
    # Catalogue list view inner HTML; the block is hidden when nothing
    # renders. Flag off: legacy entities decoded once, autoescaped.
    "catalogue_list": {
        "off": {**DECODED_ESCAPED, **_NO_DESCRIPTION},
        "on": {**RENDERED, **_NO_DESCRIPTION},
    },
    # Catalogue card snippet. Flag off: decoded, autoescaped.
    # Flag on: markdown_to_text(stored), autoescaped.
    "catalogue_card": {
        "off": DECODED_ESCAPED,
        "on": {
            "plain": "A plain service description for researchers.",
            "markdown": "Title bold text one two site",
            "md_bullets": "first second",
            "legacy_entities": "x &gt; 5 &amp; y",
            "raw_html_xss": (
                "&lt;script&gt;alert(1)&lt;/script&gt; &lt;img src=x onerror=1&gt;"
            ),
            "legacy_encoded_markup": "&lt;script&gt;x&lt;/script&gt;",
            "blocked_link": "x",
            "relative_link": "x",
            "crlf_unicode": "Zoë line two \u00e9",
            "empty": "",
            "whitespace": "",
        },
    },
    "api_detail_html": {"off": DECODED_ESCAPED, "on": RENDERED},
    # Raw field is always exactly what is stored.
    "api_detail_raw": _both(STORED),
    # List: no *_html field, raw present.
    "api_list": _both(STORED),
    # Admin read-only rendered row for view-only staff: flag on only.
    "admin_rendered_row": {
        "off": dict.fromkeys(_ALL, ABSENT),
        "on": RENDERED,
    },
    # Admin change-form textarea: stored value, escaped once, both flags.
    "admin_textarea": _both(ESCAPED_STORED),
    # Preview endpoint: the typed input, NFC-normalised and stripped before
    # rendering.
    "preview": {
        "off": dict.fromkeys(_ALL, NOT_FOUND),
        "on": {
            **{k: _preview(v) for k, v in RENDERED.items()},
            "blocked_link": _preview("<p>x</p>", removed=True),
            "relative_link": _preview("<p>x</p>", removed=True),
            "empty": NOTHING,
            "whitespace": NOTHING,
        },
    },
    # Plain-text admin notification: the "Description:" section.
    "email_txt_description": _both(DECODED),
    # Plain-text change table: the description's "After:" value.
    "email_txt_change": _both({**DECODED, "empty": "—", "whitespace": "—"}),
    # HTML change table: decoded once, autoescaped once.
    "email_html_change": _both({**DECODED_ESCAPED, "empty": "—", "whitespace": "—"}),
    # CSV export: stored + csv_safe (formula guard prefixes "-").
    "csv_export": _both({**STORED, "md_bullets": "'- first\n- second"}),
    # JSON export: stored.
    "json_export": _both(STORED),
    # audit_markdown_descriptions reasons (flag-independent).
    "audit": _both(
        {
            **dict.fromkeys(_ALL, ()),
            "markdown": ("text_changed",),
            "md_bullets": ("text_changed",),
            "blocked_link": ("text_changed", "content_removed"),
            "relative_link": ("text_changed", "content_removed"),
        }
    ),
}

# Surfaces specified as "identical to render_markdown(stored)" when the flag
# is on.
RENDER_IDENTICAL = {"catalogue_list", "api_detail_html", "admin_rendered_row"}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate(settings):
    cache.clear()
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.CELERY_TASK_EAGER_PROPAGATES = True
    yield
    cache.clear()


def _set_flag(settings, on: bool) -> None:
    cfg = copy.deepcopy(getattr(django_settings, "SITE_CONFIG", {}) or {})
    features = cfg.setdefault("features", {})
    features["markdown_descriptions"] = on
    features["catalogue"] = True
    features["biotools_prefill"] = False
    features["edam_annotations"] = False
    settings.SITE_CONFIG = cfg


def _api_client():
    from apps.api.models import AdminAPIKey

    plaintext = secrets.token_urlsafe(48)
    AdminAPIKey.objects.create(
        label="Matrix Key",
        key_hash=hashlib.sha256(plaintext.encode()).hexdigest(),
        scope="full",
        is_active=True,
    )
    c = APIClient()
    c.credentials(HTTP_AUTHORIZATION=f"AdminKey {plaintext}")
    return c


def _viewer_client():
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission
    from django.test import Client

    user = get_user_model().objects.create_user(
        username="matrixviewer", password="viewpass123", is_staff=True
    )
    user.user_permissions.add(Permission.objects.get(codename="view_servicesubmission"))
    c = Client()
    c.force_login(user)
    return c


def _sub(raw):
    return ServiceSubmissionFactory(
        status="approved",
        biotools_url="",
        service_name="Matrix Tool",
        service_description=raw,
        internal_contact_email="pi@example.com",
    )


def _one(pattern, text):
    found = re.findall(pattern, text, re.DOTALL)
    assert len(found) <= 1, f"{pattern!r} matched {len(found)} times"
    return found[0] if found else ABSENT


# ---------------------------------------------------------------------------
# Surface readers: return the observed output for a typed raw value
# ---------------------------------------------------------------------------


def _catalogue_list(raw, **_):
    from django.test import Client

    _sub(raw)
    html = Client().get(reverse("catalogue:index") + "?view=list").content.decode()
    assert "Matrix Tool" in html
    return _one(r'<div class="catalogue-list-desc-text md-rendered">(.*?)</div>', html)


def _catalogue_card(raw, **_):
    from django.test import Client

    _sub(raw)
    html = Client().get(reverse("catalogue:index")).content.decode()
    assert "Matrix Tool" in html
    return _one(
        r'<p class="text-muted small catalogue-description mb-3">(.*?)</p>', html
    )


def _api_detail(raw):
    sub = _sub(raw)
    resp = _api_client().get(f"/api/v1/submissions/{sub.id}/")
    assert resp.status_code == 200, resp.content
    return resp.json()


def _api_detail_html(raw, **_):
    return _api_detail(raw)["service_description_html"]


def _api_detail_raw(raw, **_):
    return _api_detail(raw)["service_description"]


def _api_list(raw, **_):
    _sub(raw)
    resp = _api_client().get("/api/v1/submissions/")
    assert resp.status_code == 200, resp.content
    payload = resp.json()
    rows = payload["results"] if isinstance(payload, dict) else payload
    assert len(rows) == 1
    assert "service_description_html" not in rows[0]
    return rows[0]["service_description"]


def _change_url(sub):
    return reverse("admin:submissions_servicesubmission_change", args=[sub.pk])


def _admin_rendered_row(raw, **_):
    sub = _sub(raw)
    resp = _viewer_client().get(_change_url(sub))
    assert resp.status_code == 200
    html = resp.content.decode()
    inner = _one(r'<div class="md-rendered">(.*?)</div>', html)
    # The row label is present exactly when the rendered row is.
    assert ("Description (rendered)" in html) == (inner is not ABSENT)
    return inner


def _admin_textarea(raw, superuser_client, **_):
    sub = _sub(raw)
    resp = superuser_client.get(_change_url(sub))
    assert resp.status_code == 200
    return _one(
        r'<textarea[^>]*name="service_description"[^>]*>\n(.*?)</textarea>',
        resp.content.decode(),
    )


def _preview_fragment(raw, client, **_):
    resp = client.post(
        reverse("submissions:markdown-preview"), {"service_description": raw}
    )
    if resp.status_code == 404:
        return NOT_FOUND
    assert resp.status_code == 200
    return resp.content.decode().strip()


def _email_txt_description(raw, settings, **_):
    from apps.submissions.tasks import send_submission_notification

    settings.SUBMISSION_NOTIFY_OVERRIDE = "admin@example.com"
    sub = _sub(raw)
    send_submission_notification(str(sub.id), event="created")
    assert len(mail.outbox) == 1
    body = mail.outbox[0].body
    start = body.index("\nDescription:\n") + len("\nDescription:\n")
    return body[start : body.index("\n\nCategories:")]


def _send_description_change(raw, settings):
    from apps.submissions.tasks import send_submission_notification

    settings.SUBMISSION_NOTIFY_OVERRIDE = "admin@example.com"
    sub = _sub(raw)
    changes = [
        {
            "field": "service_description",
            "label": "Service Description",
            "old": _display("old text"),
            # build_diff() compares snapshots of saved rows: the stored value.
            "new": _display(sub.service_description),
        }
    ]
    send_submission_notification(str(sub.id), event="updated", changes=changes)
    assert len(mail.outbox) == 1
    return mail.outbox[0]


def _email_txt_change(raw, settings, **_):
    body = _send_description_change(raw, settings).body
    lead = "    Before: old text\n    After:  "
    start = body.index(lead) + len(lead)
    # The change block ends with the value's own line break plus the blank
    # line the template puts before the next section.
    section = body[start : body.index("\n--- SERVICE DETAILS ---")]
    assert section.endswith("\n\n"), repr(section)
    return section[:-2]


def _email_html_change(raw, settings, **_):
    msg = _send_description_change(raw, settings)
    html, mimetype = msg.alternatives[0]
    assert mimetype == "text/html"
    return _one(r'<td class="after">(.*?)</td>', html)


def _export(action, raw, superuser_client):
    sub = _sub(raw)
    resp = superuser_client.post(
        reverse("admin:submissions_servicesubmission_changelist"),
        {"action": action, "_selected_action": [str(sub.pk)]},
    )
    assert resp.status_code == 200
    return resp.content


def _csv_export(raw, superuser_client, **_):
    content = _export("action_export_csv", raw, superuser_client)
    rows = list(csv.DictReader(io.StringIO(content.decode("utf-8-sig"))))
    assert len(rows) == 1
    return rows[0]["service_description"]


def _json_export(raw, superuser_client, **_):
    content = _export("action_export_json", raw, superuser_client)
    data = json.loads(content)
    assert len(data) == 1
    return data[0]["service_description"]


def _audit(raw, **_):
    sub = _sub(raw)
    out = StringIO()
    try:
        call_command("audit_markdown_descriptions", stdout=out)
    except CommandError:
        pass
    m = re.search(rf"\(id={sub.id}\): (.*)\n", out.getvalue())
    return tuple(m.group(1).split(", ")) if m else ()


SURFACES = {
    "catalogue_list": _catalogue_list,
    "catalogue_card": _catalogue_card,
    "api_detail_html": _api_detail_html,
    "api_detail_raw": _api_detail_raw,
    "api_list": _api_list,
    "admin_rendered_row": _admin_rendered_row,
    "admin_textarea": _admin_textarea,
    "preview": _preview_fragment,
    "email_txt_description": _email_txt_description,
    "email_txt_change": _email_txt_change,
    "email_html_change": _email_html_change,
    "csv_export": _csv_export,
    "json_export": _json_export,
    "audit": _audit,
}

CELLS = [
    pytest.param(surface, flag, name, id=f"{surface}-flag_{flag}-{name}")
    for surface in SURFACES
    for flag in ("off", "on")
    for name in INPUTS
]


def test_matrix_is_complete():
    """Every surface has an expectation for every (flag, input) cell."""
    assert set(EXPECTED) == set(SURFACES)
    for surface, by_flag in EXPECTED.items():
        assert set(by_flag) == {"off", "on"}, surface
        for flag, cells in by_flag.items():
            assert set(cells) == set(INPUTS), (surface, flag)


def test_stored_table_matches_model_save():
    """STORED is what save() really keeps for each typed input."""
    for name, raw in INPUTS.items():
        sub = _sub(raw)
        sub.refresh_from_db()
        assert sub.service_description == STORED[name], name


@pytest.mark.parametrize("surface,flag,name", CELLS)
def test_output_matrix(surface, flag, name, settings, client, superuser_client):
    _set_flag(settings, flag == "on")
    raw = INPUTS[name]
    expected = EXPECTED[surface][flag][name]
    observed = SURFACES[surface](
        raw, settings=settings, client=client, superuser_client=superuser_client
    )
    assert observed == expected
    if flag == "on" and surface in RENDER_IDENTICAL and expected is not ABSENT:
        assert observed == str(render_markdown(STORED[name]))
