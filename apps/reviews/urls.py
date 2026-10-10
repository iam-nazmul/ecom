from django.urls import path

from . import views

app_name = "reviews"

urlpatterns = [
    path("products/<slug:slug>/reviews/", views.submit, name="submit"),
    path("products/<slug:slug>/reviews/delete/", views.delete, name="delete"),
]
