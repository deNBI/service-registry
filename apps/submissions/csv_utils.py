"""CSV export helpers shared by the admin export and management commands."""

CSV_FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(value):
    """Keep spreadsheet apps from evaluating an exported CSV cell as a formula.

    Strings starting with a formula trigger (``= + - @``, tab or CR) get a
    leading ``'`` so spreadsheet apps treat them as text. Markdown bullet
    lists (``- item``) are the common benign case. Non-strings pass through.
    """
    if isinstance(value, str) and value.startswith(CSV_FORMULA_TRIGGERS):
        return "'" + value
    return value
