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


# ---------------------------------------------------------------------------
# Fieldset branch (RadioSelect / CheckboxSelectMultiple): legend + the same
# widget and feedback partials as the <div> branch
# ---------------------------------------------------------------------------

FIELDSET_FIELDS = ["is_toolbox", "register_as_elixir", "survey_participation"]


def _render_field(form, name):
    from django.template.loader import render_to_string

    return render_to_string(
        "submissions/partials/field.html", {"field": form[name], "required": True}
    )


@pytest.mark.parametrize("name", FIELDSET_FIELDS)
def test_fieldset_field_markup(name):
    from apps.submissions.forms import SubmissionForm

    form = SubmissionForm()
    assert form[name].use_fieldset
    html = _render_field(form, name)
    assert html.strip().startswith(f'<fieldset class="mb-3" id="field-wrapper-{name}">')
    assert '<legend class="form-label">' in html
    assert '<span class="required-star" aria-label="required">*</span>' in html
    assert html.count(f'hx-target="#field-errors-{name}"') == 1
    assert 'hx-trigger="change"' in html
    assert html.count(f'<div id="field-errors-{name}">') == 1
    assert 'role="alert"' not in html
    assert "is-invalid" not in html


@pytest.mark.parametrize("name", FIELDSET_FIELDS)
def test_fieldset_field_markup_with_errors(name):
    from apps.submissions.forms import SubmissionForm

    form = SubmissionForm(data={})
    form.is_valid()
    form.add_error(name, "Pick one")
    html = _render_field(form, name)
    assert f'<fieldset class="mb-3 has-error" id="field-wrapper-{name}">' in html
    assert '<legend class="form-label text-danger fw-semibold">' in html
    assert f"document.getElementById('field-wrapper-{name}')" in html
    assert "el.classList.add('is-invalid');" in html
    assert '<div class="invalid-feedback d-block" role="alert">' in html
    assert "<strong>⚠ Pick one</strong>" in html
    assert html.count('role="alert"') == len(form.errors[name])
