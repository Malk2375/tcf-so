from django.urls import path

from accounts import views

# Placées avant les adresses d'allauth : « password/reset/ » remplace la vue d'allauth.
account_urlpatterns = [
    path("password/reset/", views.password_reset, name="account_reset_password"),
    path(
        "reset/<uidb64>/<token>/",
        views.PasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path("reset/done/", views.PasswordResetCompleteView.as_view(), name="password_reset_complete"),
]

profile_urlpatterns = [
    path("", views.profile, name="profile"),
    path("delete/", views.delete_account, name="delete_account"),
]
