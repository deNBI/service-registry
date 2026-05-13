"""
Widget rendering tests.
"""

import json

import pytest
from django.forms import ModelMultipleChoiceField

from apps.edam.models import EdamTerm
from apps.submissions.widgets import EdamAutocompleteWidget


@pytest.mark.django_db
class TestEdamAutocompleteWidgetSynonyms:
    def _make_term(self, synonyms):
        return EdamTerm.objects.create(
            uri="http://edamontology.org/topic_9999",
            accession="topic_9999",
            branch="topic",
            label="Test Topic",
            definition="A test topic.",
            synonyms=synonyms,
            sort_order=9999,
            edam_version="1.0",
        )

    def _render(self, term):
        qs = EdamTerm.objects.filter(pk=term.pk)
        widget = EdamAutocompleteWidget(branch="topic")
        field = ModelMultipleChoiceField(queryset=qs, widget=widget)
        widget.choices = field.choices
        return widget.render("edam_topics", [])

    def test_data_synonyms_attr_present_when_synonyms_exist(self):
        term = self._make_term(["alignment", "pairwise alignment"])
        html = self._render(term)
        assert "data-synonyms=" in html

    def test_data_synonyms_contains_synonym_text(self):
        term = self._make_term(["alignment", "pairwise alignment"])
        html = self._render(term)
        assert "alignment" in html

    def test_data_synonyms_is_valid_json_array(self):
        term = self._make_term(["alignment", "pairwise alignment"])
        html = self._render(term)
        # Extract the raw attribute value (HTML-escaped in the output)
        import re

        m = re.search(r'data-synonyms="([^"]*)"', html)
        assert m is not None, "data-synonyms attribute not found"

        # We must unescape the HTML entities before parsing JSON
        from html import unescape

        val = unescape(m.group(1))
        parsed = json.loads(val)
        assert parsed == ["alignment", "pairwise alignment"]

    def test_no_data_synonyms_attr_when_synonyms_empty(self):
        term = self._make_term([])
        html = self._render(term)
        assert "data-synonyms=" not in html

    def test_synonyms_capped_at_ten_in_attribute(self):
        term = self._make_term([f"syn{i}" for i in range(15)])
        html = self._render(term)
        import re

        m = re.search(r'data-synonyms="([^"]*)"', html)
        assert m is not None
        from html import unescape

        val = unescape(m.group(1))
        parsed = json.loads(val)
        assert len(parsed) == 10

    def test_synonyms_with_special_chars_are_safe(self):
        term = self._make_term(['<script>alert("xss")</script>', "safe synonym"])
        html = self._render(term)
        # The dangerous string must not appear as raw HTML — it must be JSON-escaped
        assert "<script>" not in html
        assert "data-synonyms=" in html

    def test_widget_with_empty_choices_renders_no_data_synonyms(self):
        widget = EdamAutocompleteWidget(branch="topic")
        widget.choices = []
        html = widget.render("field", [])
        assert "data-synonyms=" not in html
