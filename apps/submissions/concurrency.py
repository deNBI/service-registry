"""
Stale-form guard
================
Edit forms (the owner's web form and the admin change form) post every field
of the service. If the service changed after the form was loaded, saving the
whole form would silently put the old values back.

Every service has a version number that each save and each change of its
links raises (``ServiceSubmission.version``). The form carries the version it
was loaded with in a hidden ``loaded_version`` input; before the save the
posted number is put on the instance, and ``ServiceSubmission.save()`` refuses
the save with ``EditConflict`` when the stored version differs, so nothing of
the edit is stored. A form posted without the input (a page rendered before
this check existed) is checked against the version read for the request.
"""

from .models import ServiceSubmission

VERSION_FIELD = "loaded_version"


def posted_version(data) -> int | None:
    """The version a posted form was loaded with, or None when it carries no
    usable value."""
    raw = (data.get(VERSION_FIELD) or "").strip()
    if not raw.isdecimal():
        return None
    return int(raw)


def use_loaded_version(submission: ServiceSubmission, data) -> None:
    """Make the next save of ``submission`` check against the version the
    posted form was loaded with."""
    version = posted_version(data)
    if version is not None:
        submission.version = version
