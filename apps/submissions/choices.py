"""
Choices that keep what a service already has
============================================
Pickers only offer active reference rows (categories, PIs, centres) and
non-obsolete EDAM terms. A service that already links to a row that was
deactivated or marked obsolete since must still be able to keep it: if the
row were missing from the allowed choices, an edit would either fail
validation or silently drop the link.

``keep_linked`` widens a picker's queryset by the rows the service being
edited already links to; rows it does not link to stay hidden.
"""

from django.db.models import Model, QuerySet


def linked_pks(instance, field_name: str) -> list:
    """Primary keys of the rows ``instance`` links to through ``field_name``."""
    if not isinstance(instance, Model) or instance._state.adding:
        return []
    field = instance._meta.get_field(field_name)
    if field.many_to_many:
        return list(getattr(instance, field_name).values_list("pk", flat=True))
    value = getattr(instance, field.attname)
    return [] if value is None else [value]


def keep_linked(queryset: QuerySet, instance, field_name: str) -> QuerySet:
    """``queryset`` plus the rows ``instance`` already links to via ``field_name``."""
    pks = linked_pks(instance, field_name)
    if not pks:
        return queryset
    return queryset | queryset.model._default_manager.filter(pk__in=pks)
