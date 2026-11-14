from allauth.account.decorators import secure_admin_login
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from accounts.urls import account_urlpatterns, profile_urlpatterns
from config import views
from exam import views as exam_views

# L'admin passe par la connexion d'allauth (limitation des tentatives).
admin.site.login = secure_admin_login(admin.site.login)

urlpatterns = [
    path("", views.home, name="home"),
    path("accounts/", include(account_urlpatterns)),
    path("accounts/", include("allauth.urls")),
    path("profile/", include(profile_urlpatterns)),
    path("dashboard/", exam_views.dashboard, name="dashboard"),
    path("bo/", include("backoffice.urls")),
    path("admin/", admin.site.urls),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
