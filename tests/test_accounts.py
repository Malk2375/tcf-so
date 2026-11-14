import pytest
from django.contrib.auth import get_user_model
from django.core import mail
from django.core.cache import cache
from django.urls import reverse

from accounts.reset import make_reset_link

PASSWORD = "Entrainement-TCF-2026"
NEW_PASSWORD = "Nouveau-mot-de-passe-77"

pytestmark = pytest.mark.django_db
User = get_user_model()


@pytest.fixture(autouse=True)
def _clear_rate_limits():
    # Les limites de tentatives d'allauth sont stockées dans le cache.
    cache.clear()


def signup(client, email="candidat@example.com", password=PASSWORD, **extra):
    data = {"email": email, "password1": password, "password2": password, **extra}
    return client.post(reverse("account_signup"), data)


def login(client, email="candidat@example.com", password=PASSWORD):
    return client.post(reverse("account_login"), {"login": email, "password": password})


def is_logged_in(client):
    return "_auth_user_id" in client.session


# --- Parcours principal ---------------------------------------------------------------------


def test_signup_logout_login_change_password(client):
    response = signup(client)
    assert response.status_code == 302
    assert response["Location"] == reverse("dashboard")
    assert is_logged_in(client)
    assert User.objects.get().email == "candidat@example.com"

    client.post(reverse("account_logout"))
    assert not is_logged_in(client)

    response = login(client)
    assert response.status_code == 302
    assert is_logged_in(client)

    response = client.post(
        reverse("account_change_password"),
        {"oldpassword": PASSWORD, "password1": NEW_PASSWORD, "password2": NEW_PASSWORD},
    )
    assert response.status_code == 302
    assert is_logged_in(client)

    client.post(reverse("account_logout"))
    login(client, password=PASSWORD)
    assert not is_logged_in(client)
    login(client, password=NEW_PASSWORD)
    assert is_logged_in(client)


def test_login_with_wrong_password_fails(client):
    signup(client)
    client.post(reverse("account_logout"))
    response = login(client, password="mauvais-mot-de-passe")
    assert response.status_code == 200
    assert not is_logged_in(client)


def test_duplicate_email_is_refused(client):
    signup(client)
    client.post(reverse("account_logout"))
    signup(client, email="CANDIDAT@example.com")
    assert User.objects.count() == 1


def test_superuser_logs_in_with_email(client):
    User.objects.create_superuser(username="chef", email="chef@example.com", password=PASSWORD)
    login(client, email="chef@example.com")
    assert is_logged_in(client)


def test_signup_page_is_in_french(client):
    html = client.get(reverse("account_signup")).content.decode()
    assert '<html lang="fr">' in html
    assert "Inscription" in html


def test_home_redirects_to_dashboard_when_logged_in(client):
    assert client.get(reverse("home")).status_code == 200
    signup(client)
    response = client.get(reverse("home"))
    assert response.status_code == 302
    assert response["Location"] == reverse("dashboard")


def test_dashboard_requires_login(client):
    response = client.get(reverse("dashboard"))
    assert response.status_code == 302
    assert reverse("account_login") in response["Location"]


# --- Code d'invitation ----------------------------------------------------------------------


def test_signup_without_code_when_not_configured(client, settings):
    settings.SIGNUP_CODE = ""
    assert "signup_code" not in client.get(reverse("account_signup")).content.decode()
    signup(client)
    assert User.objects.count() == 1


@pytest.mark.parametrize("code", [None, "", "mauvais"])
def test_signup_with_missing_or_wrong_code_fails(client, settings, code):
    settings.SIGNUP_CODE = "bienvenue-2026"
    extra = {} if code is None else {"signup_code": code}
    response = signup(client, **extra)
    assert response.status_code == 200
    assert User.objects.count() == 0
    assert not is_logged_in(client)


def test_signup_with_right_code_succeeds(client, settings):
    settings.SIGNUP_CODE = "bienvenue-2026"
    assert "signup_code" in client.get(reverse("account_signup")).content.decode()
    signup(client, signup_code=" bienvenue-2026 ")
    assert User.objects.count() == 1


# --- Mot de passe oublié, selon EMAIL_ENABLED -----------------------------------------------


def test_password_reset_page_is_informative_when_email_disabled(client, settings):
    settings.EMAIL_ENABLED = False
    signup(client)
    client.post(reverse("account_logout"))

    response = client.get(reverse("account_reset_password"))
    assert "Contactez l'administrateur pour recevoir un lien de réinitialisation" in (
        response.content.decode()
    )
    client.post(reverse("account_reset_password"), {"email": "candidat@example.com"})
    assert len(mail.outbox) == 0


def test_password_reset_sends_email_when_email_enabled(client, settings):
    settings.EMAIL_ENABLED = True
    signup(client)
    client.post(reverse("account_logout"))

    response = client.get(reverse("account_reset_password"))
    assert "Contactez l'administrateur" not in response.content.decode()
    response = client.post(reverse("account_reset_password"), {"email": "candidat@example.com"})
    assert response.status_code == 302
    assert len(mail.outbox) == 1
    assert mail.outbox[0].to == ["candidat@example.com"]


def test_login_page_links_to_password_reset(client):
    html = client.get(reverse("account_login")).content.decode()
    assert reverse("account_reset_password") in html


# --- Lien de réinitialisation généré par l'administrateur -----------------------------------


def test_admin_generated_link_works_only_once(staff_client, settings):
    settings.SITE_URL = "https://192.0.2.10"
    user = User.objects.create_user(username="c", email="c@example.com", password=PASSWORD)

    response = staff_client.post(reverse("backoffice:users"), {"user_id": user.pk})
    link = response.context["generated"]["link"]
    assert link.startswith("https://192.0.2.10/accounts/reset/")
    assert link in response.content.decode()

    path = link.removeprefix(settings.SITE_URL)
    staff_client.post(reverse("account_logout"))

    # Django remplace le jeton par « set-password » dans l'adresse avant d'afficher le formulaire.
    response = staff_client.get(path)
    assert response.status_code == 302
    set_password_url = response["Location"]
    response = staff_client.post(
        set_password_url, {"new_password1": NEW_PASSWORD, "new_password2": NEW_PASSWORD}
    )
    assert response.status_code == 302
    assert response["Location"] == reverse("password_reset_complete")

    user.refresh_from_db()
    assert user.check_password(NEW_PASSWORD)
    login(staff_client, email="c@example.com", password=NEW_PASSWORD)
    assert is_logged_in(staff_client)
    staff_client.post(reverse("account_logout"))

    # Second usage : refusé, le mot de passe ne change pas.
    response = staff_client.get(path, follow=True)
    assert response.context["validlink"] is False
    assert "n'est plus valide" in response.content.decode()
    user.refresh_from_db()
    assert user.check_password(NEW_PASSWORD)


def test_reset_link_is_built_from_site_url(settings):
    settings.SITE_URL = "http://localhost:8000"
    user = User.objects.create_user(username="d", email="d@example.com", password=PASSWORD)
    assert make_reset_link(user).startswith("http://localhost:8000/accounts/reset/")


def test_backoffice_users_requires_staff(client):
    response = client.get(reverse("backoffice:users"))
    assert response.status_code == 302
    assert reverse("account_login") in response["Location"]

    signup(client)
    assert client.get(reverse("backoffice:users")).status_code == 403
    user = User.objects.get()
    assert client.post(reverse("backoffice:users"), {"user_id": user.pk}).status_code == 403


def test_admin_uses_allauth_login(client):
    response = client.get(reverse("admin:login"))
    assert response.status_code == 302
    assert reverse("account_login") in response["Location"]


# --- Profil et suppression du compte --------------------------------------------------------


def test_profile_shows_email(client):
    assert client.get(reverse("profile")).status_code == 302
    signup(client)
    html = client.get(reverse("profile")).content.decode()
    assert "candidat@example.com" in html
    assert reverse("account_change_password") in html
    assert reverse("delete_account") in html


def test_delete_account_requires_password_and_confirmation(client):
    signup(client)
    client.post(reverse("delete_account"), {"password": "mauvais", "confirm": "on"})
    client.post(reverse("delete_account"), {"password": PASSWORD})
    assert User.objects.count() == 1


def test_delete_account(client):
    signup(client)
    response = client.post(reverse("delete_account"), {"password": PASSWORD, "confirm": "on"})
    assert response.status_code == 302
    assert response["Location"] == reverse("home")
    assert User.objects.count() == 0
    assert not is_logged_in(client)
