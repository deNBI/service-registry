"""
Stale-form guard
================
Edit forms (the owner's web form and the admin change form) post every field
of the service. If the service changed after the form was loaded, saving the
whole form would silently put the old values back.

To prevent that, the form carries a fingerprint of the content it was loaded
with (a hidden ``loaded_fingerprint`` input). When the form is saved, the
stored row is locked and its fingerprint is compared with the posted one; a
difference raises ``StaleFormError`` and the save is refused, so nothing of
the edit is stored.

The fingerprint is built from the same snapshots that drive the change
history (``diff_utils.snapshot`` / ``snapshot_m2m``), so it covers every
tracked field and the many-to-many links, and is stable for unchanged data.
A form posted without a fingerprint (a page rendered before this check
existed) is accepted as before.
"""

import hashlib
import json

from .diff_utils import snapshot, snapshot_m2m
from .models import ServiceSubmission

FINGERPRINT_FIELD = "loaded_fingerprint"


class StaleFormError(Exception):
    """The service changed after the posted form was loaded."""

    message = (
        "This service was changed while you were editing. Nothing was saved. "
        "Reload the page to see the current version, then make your changes "
        "again."
    )


def content_fingerprint(submission: ServiceSubmission) -> str:
    """Hash of the tracked content of ``submission`` as currently stored."""
    state = {"fields": snapshot(submission), "links": snapshot_m2m(submission)}
    encoded = json.dumps(state, sort_keys=True, ensure_ascii=True).encode()
    return hashlib.sha256(encoded).hexdigest()


def ensure_unchanged(submission_pk, loaded_fingerprint: str) -> None:
    """Lock the stored row and refuse a save made from an outdated form.

    Call inside the transaction that stores the edit, before writing. The row
    stays locked until that transaction ends, so no other edit can slip in
    between this check and the write. An empty ``loaded_fingerprint`` skips
    the check.
    """
    if not loaded_fingerprint:
        return
    stored = (
        ServiceSubmission.objects.select_for_update().filter(pk=submission_pk).first()
    )
    # A service deleted since the form was loaded counts as changed.
    if stored is None or content_fingerprint(stored) != loaded_fingerprint:
        raise StaleFormError(submission_pk)
