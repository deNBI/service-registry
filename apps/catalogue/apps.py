from django.apps import AppConfig


class CatalogueConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.catalogue"
    verbose_name = "Registry Catalogue"

    def ready(self) -> None:
        from apps.catalogue import signals  # noqa: F401  (connect receivers)
