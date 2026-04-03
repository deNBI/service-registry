from django.apps import AppConfig


class ApiConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.api"
    verbose_name = "REST API"

    def ready(self):
        # Register drf-spectacular OpenAPI auth extensions so Swagger UI
        # shows the Authorize button for AdminKey and SubmissionApiKey schemes.
        import apps.api.spectacular_extensions  # noqa: F401
