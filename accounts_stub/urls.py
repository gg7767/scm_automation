"""
URL configuration for the accounts_stub app.
"""
from django.urls import path

from accounts_stub import views

app_name = "accounts_stub"

urlpatterns = [
    path("users/", views.user_list, name="user_list"),
    path("users/create/", views.user_create, name="user_create"),
    path("users/<int:pk>/", views.user_detail, name="user_detail"),
    path("users/<int:pk>/delete/", views.user_delete, name="user_delete"),
]
