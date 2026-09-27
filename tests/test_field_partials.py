"""Structural tests for the form field partial (field.html) and its
label / widget / feedback sub-partials."""

import re

import pytest
from django.urls import reverse

from tests.helpers import base_form_data

pytestmark = pytest.mark.django_db

FIELDS = ["service_name", "service_description", "public_contact_email"]


def _field_block(html, name):
    m = re.search(
        rf'<div class="mb-3[^"]*" id="field-wrapper-{name}">.*?<div id="field-errors-{name}">.*?</div>\s*</div>',
        html,
        re.S,
    )
    assert m, name
    return m.group(0)


@pytest.mark.parametrize("name", FIELDS)
def test_field_markup_unchanged(client, name):
    html = client.get(reverse("submissions:register")).content.decode()
    block = _field_block(html, name)
    assert f'<label for="id_{name}"' in block
    assert f'hx-target="#field-errors-{name}"' in block
    assert 'hx-trigger="change"' in block
    assert "has-error" not in block
    assert 'role="alert"' not in block


@pytest.mark.parametrize("name", FIELDS)
def test_field_markup_with_errors(client, name):
    data = base_form_data(
        {
            "service_name": "",
            "service_description": "short",
            "public_contact_email": "bad",
        }
    )
    resp = client.post(reverse("submissions:register"), data)
    assert resp.status_code == 422
    html = resp.content.decode()
    m = re.search(
        rf'<div class="mb-3 has-error" id="field-wrapper-{name}">.*?<div id="field-errors-{name}">.*?role="alert".*?</div>\s*</div>\s*</div>',
        html,
        re.S,
    )
    assert m, name
    block = m.group(0)
    assert f'<label for="id_{name}"' in block
    assert "form-label text-danger fw-semibold" in block
    assert f'hx-target="#field-errors-{name}"' in block
    assert f"document.getElementById('field-wrapper-{name}')" in block
    assert "el.classList.add('is-invalid');" in block
    assert '<div class="invalid-feedback d-block" role="alert">' in block
