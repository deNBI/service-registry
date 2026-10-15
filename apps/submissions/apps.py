from django.apps import AppConfig


class SubmissionsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.submissions"
    verbose_name = "Service Submissions"

    def ready(self) -> None:
        from apps.submissions import signals  # noqa: F401  (connect receivers)

        # Validate SUBMISSION_NO_RESET_FIELDS at startup so any typos or
        # unrecognised field names are surfaced immediately in the server log
        # rather than silently at request time.
        from apps.submissions.lifecycle import get_no_reset_fields

        get_no_reset_fields()
