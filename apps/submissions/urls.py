"""Submission app URL patterns."""

from django.urls import path
from . import views

app_name = "submissions"

urlpatterns = [
    path("", views.home, name="home"),
    path("register/", views.RegisterView.as_view(), name="register"),
    path("register/success/", views.SuccessView.as_view(), name="success"),
    path("register/validate/", views.validate_field, name="validate_field"),
    path("captcha/", views.AltchaChallengeView.as_view(), name="altcha_challenge"),
    path("update/", views.UpdateView.as_view(), name="update"),
    path("update/edit/", views.EditView.as_view(), name="edit"),
    path("update/success/", views.update_success, name="update_success"),
]
