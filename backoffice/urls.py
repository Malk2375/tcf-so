from django.urls import path

from backoffice import views

app_name = "backoffice"

urlpatterns = [
    path("briefs/", views.briefs, name="briefs"),
    path("briefs/<int:pk>/download/", views.brief_download, name="brief_download"),
    path("import/", views.import_view, name="import"),
    path("users/", views.users, name="users"),
]
