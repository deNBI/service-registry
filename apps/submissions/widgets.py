"""
Custom Form Widgets
===================
EdamAutocompleteWidget: a searchable multi-select for EDAM ontology terms.

Renders as a standard <select multiple> that is progressively enhanced
by Tom Select (a lightweight Select2 alternative, ~17 KB gzipped) via
the base template.

Why Tom Select instead of plain <select>?
  - 4000 options in a flat <select> are unusable on mobile and slow in browsers
  - Tom Select virtualises the option list and filters by typing
  - It degrades gracefully to a plain <select> if JS is unavailable
  - No jQuery dependency (unlike Select2)

The widget groups options by branch for clarity when the user opens the
full option list without typing.

SpdxLicenseAutocompleteWidget: Reuses EdamAutocompleteWidget's CSS/JS for
SPDX license selection. Has no Media of its own to avoid duplicate loading.

AffiliationComboboxWidget: Tom Select combobox for institute/affiliation autocomplete.
  - Allows custom input if the user's affiliation isn't in the list
  - Pre-populated with suggestions from PI institutes and past submissions
  - Single-select only

CompactSelectWidget: Compact multi-select with search and checkboxes.
  - Shows selected items as pills at the top
  - Searchable dropdown list with checkboxes
  - Used for fields like service_categories and responsible_pis

CompactSelectSingleWidget: Single-select variant of compact select.
  - Similar UX to CompactSelectWidget but only one item selectable
  - Used for fields like service_center
"""

from django import forms


class EdamAutocompleteWidget(forms.SelectMultiple):
    """
    Searchable multi-select widget for EDAM terms.

    Usage:
        class MyForm(forms.ModelForm):
            class Meta:
                widgets = {
                    "edam_topics": EdamAutocompleteWidget(attrs={"data-max-items": "6"}),
                }

    Attributes:
        data-max-items  : Maximum number of terms the user can select (default: 6)
        data-branch     : EDAM branch to filter (topic, operation, data, format)
                          Set automatically by the form based on the field name.
        data-placeholder: Placeholder text shown when nothing is selected.
    """

    def __init__(
        self, attrs=None, branch: str = "", placeholder: str = "Search EDAM terms…"
    ):
        default_attrs = {
            "class": "edam-autocomplete",
            "data-branch": branch,
            "data-placeholder": placeholder,
            "data-max-items": "6",
        }
        if attrs:
            default_attrs.update(attrs)
        super().__init__(attrs=default_attrs)

    class Media:
        # Tom Select 2.3.1 — vendored locally in static/ (no CDN dependency)
        css = {
            "all": ["css/tom-select.bootstrap5.min.css"],
        }
        js = [
            "js/tom-select.complete.min.js",
        ]


class SpdxLicenseAutocompleteWidget(forms.SelectMultiple):
    """
    Searchable multi-select widget for SPDX licenses.

    Reuses the same CSS class and JS picker as EdamAutocompleteWidget
    (buildEdamPicker in static/js/edam-autocomplete.js) so selected
    licenses render as pills on top of a type-to-search list.

    Usage:
        class MyForm(forms.ModelForm):
            class Meta:
                widgets = {
                    "licenses": SpdxLicenseAutocompleteWidget(),
                }

    Attributes:
        data-max-items  : Maximum number of licenses selectable (default: 6,
                          matching EdamAutocompleteWidget — comfortably covers
                          dual/triple-licensed services without hard-capping.
                          Backend enforces no count limit, so this is UX only).
        data-placeholder: Placeholder text shown when nothing is selected.
    """

    def __init__(
        self,
        attrs=None,
        placeholder: str = "Search licenses (e.g. MIT, Apache-2.0)…",
    ):
        default_attrs = {
            "class": "edam-autocomplete",
            "data-placeholder": placeholder,
            "data-max-items": "6",
        }
        if attrs:
            default_attrs.update(attrs)
        super().__init__(attrs=default_attrs)


class AffiliationComboboxWidget(forms.Select):
    """
    Combobox widget for institute/affiliation selection using Tom Select.

    Features:
      - Autocomplete suggestions from PI institutes and past submissions
      - Allows custom input if the user's affiliation is not in the list
      - Creates new values on blur (persist: false keeps it clean)
      - Single-select only

    Usage:
        class SubmissionForm(forms.ModelForm):
            class Meta:
                widgets = {
                    "submitter_affiliation": AffiliationComboboxWidget(
                        placeholder="e.g. Forschungszentrum Jülich"
                    ),
                }

    Attributes:
        data-affiliation-combobox: Set to "true" to trigger Tom Select initialization
        placeholder: Placeholder text shown in the input field
    """

    def __init__(self, attrs=None, placeholder: str = "e.g. Your Institute"):
        default_attrs = {
            "class": "form-select",
            "data-affiliation-combobox": "true",
            "data-placeholder": placeholder,
        }
        if attrs:
            default_attrs.update(attrs)
        super().__init__(attrs=default_attrs)

    class Media:
        css = {
            "all": ["css/tom-select.bootstrap5.min.css"],
        }
        js = [
            "js/tom-select.complete.min.js",
        ]


class CompactSelectWidget(forms.SelectMultiple):
    """
    Compact multi-select widget with search and checkboxes.

    Features:
      - Shows selected items as pills at the top
      - Searchable dropdown list with checkboxes
      - Visual feedback on selection
      - Progressive enhancement — degrades to plain select without JS

    Usage:
        class SubmissionForm(forms.ModelForm):
            class Meta:
                widgets = {
                    "service_categories": CompactSelectWidget(
                        label="Service category"
                    ),
                }

    Attributes:
        data-compact-select: Set to the field label for display in the search placeholder
    """

    def __init__(self, attrs=None, label: str = "Options"):
        default_attrs = {
            "class": "form-select",
            "data-compact-select": label,
        }
        if attrs:
            default_attrs.update(attrs)
        super().__init__(attrs=default_attrs)

    class Media:
        js = [
            "js/edam-autocomplete.js",  # Contains buildCompactSelect()
        ]


class CompactSelectSingleWidget(forms.Select):
    """
    Single-select variant of the compact select widget.

    Features:
      - Similar UX to CompactSelectWidget but limited to one item
      - Shows selected item as a pill
      - Searchable dropdown list
      - Clean, modern interface

    Usage:
        class SubmissionForm(forms.ModelForm):
            class Meta:
                widgets = {
                    "service_center": CompactSelectSingleWidget(
                        label="de.NBI Service Center"
                    ),
                }

    Attributes:
        data-compact-select-single: Set to the field label for display
    """

    def __init__(self, attrs=None, label: str = "Option"):
        default_attrs = {
            "class": "form-select",
            "data-compact-select-single": label,
        }
        if attrs:
            default_attrs.update(attrs)
        super().__init__(attrs=default_attrs)

    class Media:
        js = [
            "js/edam-autocomplete.js",  # Will extend to include buildCompactSelectSingle()
        ]
