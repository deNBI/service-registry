from django.urls import path

from . import views

app_name = "catalogue"

urlpatterns = [
    path("", views.catalogue_view, name="index"),
    path("grid/", views.catalogue_grid_view, name="grid"),
    path("filters/", views.catalogue_filters_view, name="filters"),
]
