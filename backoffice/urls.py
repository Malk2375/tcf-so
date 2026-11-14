from django.urls import path

from backoffice import views

app_name = "backoffice"

urlpatterns = [
    path("users/", views.users, name="users"),
]
