"""Signal receivers for the submissions app."""

from django.db.models import F
from django.db.models.signals import m2m_changed, post_save
from django.utils import timezone

from apps.submissions.models import ServiceSubmission, SubmissionStatusEvent


def _column_to(through, model) -> str:
    """Name of the through-table column that points at ``model``."""
    for field in through._meta.get_fields():
        if getattr(field, "related_model", None) is model:
            return field.attname
    raise LookupError(f"{through.__name__} has no column for {model.__name__}")


def raise_version_on_link_change(sender, instance, action, reverse, pk_set, **kwargs):
    """Raise the version of every service whose links change.

    Links (categories, PIs, EDAM terms, licences) live in their own tables, so
    changing them does not save the service row. Without this, a form loaded
    before such a change could still be saved and put the old links back.
    """
    if not reverse:
        # ``instance`` is the service. An add of rows that are all linked
        # already sends no ids. A remove sends the ids it was given, linked or
        # not, so removing an unlinked row still raises the version; set()
        # only removes rows that are linked.
        if action == "post_clear" or (action in ("post_add", "post_remove") and pk_set):
            ServiceSubmission.objects.filter(pk=instance.pk).update(
                version=F("version") + 1
            )
            # Keep this copy current, so the caller can save it again.
            instance.version += 1
        return
    # ``instance`` is the linked row; the services are in pk_set, or, for a
    # clear, every service linked to the row before the clear.
    if action in ("post_add", "post_remove"):
        pks = list(pk_set or ())
    elif action == "pre_clear":
        pks = list(
            sender.objects.filter(
                **{_column_to(sender, type(instance)): instance.pk}
            ).values_list(_column_to(sender, ServiceSubmission), flat=True)
        )
    else:
        return
    if pks:
        ServiceSubmission.objects.filter(pk__in=pks).update(version=F("version") + 1)


def record_creation_event(sender, instance, created, raw=False, **kwargs):
    """Record the initial status of every new submission, whatever created it.

    The actor is always "system": the receiver sees only the saved row, not
    whether the public form, the admin, an import or a script created it. The
    version is not touched: a new row starts at version 1.
    """
    if not created or raw:
        return
    SubmissionStatusEvent.objects.create(
        submission=instance,
        from_status=None,
        to_status=instance.status,
        changed_at=timezone.now(),
        changed_by="system",
    )


def connect_signals() -> None:
    post_save.connect(
        record_creation_event,
        sender=ServiceSubmission,
        dispatch_uid="submission_creation_status_event",
    )
    for field in ServiceSubmission._meta.many_to_many:
        m2m_changed.connect(
            raise_version_on_link_change,
            sender=field.remote_field.through,
            dispatch_uid=f"submission_version_on_{field.name}_change",
        )
