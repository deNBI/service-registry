"""
drf-spectacular authentication extensions
==========================================
Registers OpenAPI 3.0 security scheme definitions for both custom
authentication backends so Swagger UI shows the Authorize button.

These are auto-loaded via ApiConfig.ready() in apps.py.
"""

from drf_spectacular.extensions import OpenApiAuthenticationExtension


class AdminAPIKeyAuthenticationExtension(OpenApiAuthenticationExtension):
    """
    Maps AdminAPIKeyAuthentication → the "AdminKey" OpenAPI security scheme.
    Appears in Swagger UI as an apiKey header field pre-labelled AdminKey.
    """

    target_class = "apps.api.authentication.AdminAPIKeyAuthentication"
    name = "AdminKey"

    def get_security_definition(self, auto_schema):
        return {
            "type": "apiKey",
            "in": "header",
            "name": "Authorization",
            "description": (
                "Admin API key with **read** or **full** scope.  "
                "Create one in the Django admin under **API → Admin API Keys**.\n\n"
                "Enter the value as:\n```\nAdminKey <your-key>\n```"
            ),
        }


class SubmissionAPIKeyAuthenticationExtension(OpenApiAuthenticationExtension):
    """
    Maps SubmissionAPIKeyAuthentication → the "SubmissionApiKey" OpenAPI security scheme.
    Appears in Swagger UI as an apiKey header field pre-labelled ApiKey.
    """

    target_class = "apps.api.authentication.SubmissionAPIKeyAuthentication"
    name = "SubmissionApiKey"

    def get_security_definition(self, auto_schema):
        return {
            "type": "apiKey",
            "in": "header",
            "name": "Authorization",
            "description": (
                "Submission API key issued once when a service is registered.  "
                "Scopes: **read** (GET only) or **write** (GET + PATCH).\n\n"
                "Enter the value as:\n```\nApiKey <your-key>\n```"
            ),
        }
