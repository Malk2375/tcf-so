"""Adaptateur d'allauth : compteur d'échecs de connexion par compte, indépendant de l'adresse
du site (SPEC 13).

Sans le framework `sites`, allauth range les échecs d'un compte sous la clé
« <hôte de la requête>:<email> ». Un même compte aurait alors un compteur par adresse du site
(adresse IP, nom de domaine, localhost:8000…) : le blocage se contournerait en changeant
d'adresse, et `unlock_login`, lancé hors de toute requête, ne saurait pas quelle clé effacer.
La clé est donc l'adresse e-mail seule, en minuscules.

`_get_login_attempts_cache_key` est une méthode interne d'allauth (vérifiée sur 65.22.1) ;
tests/test_login_lockout.py échoue si elle n'est plus utilisée.
"""

from allauth.account.adapter import DefaultAccountAdapter


def login_attempts_key(email: str) -> str:
    return (email or "").strip().lower()


class AccountAdapter(DefaultAccountAdapter):
    def _get_login_attempts_cache_key(self, request, **credentials) -> str:
        return login_attempts_key(credentials.get("email", credentials.get("username", "")))
