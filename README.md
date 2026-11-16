# TCF SO — plateforme d'entraînement

Plateforme web personnelle et non commerciale de préparation au TCF tout public sur ordinateur.

## Prérequis

- [uv](https://docs.astral.sh/uv/) (il installe lui-même Python 3.12 si besoin).
- Aucune installation de Node.js : Tailwind CSS est utilisé en exécutable autonome.

## Installation

```bash
uv sync                               # installer Python 3.12 et les dépendances
cp .env.example .env                  # facultatif en développement : les valeurs par défaut suffisent
uv run python manage.py migrate       # créer la base SQLite dans data/
uv run python manage.py runserver     # lancer le site sur http://localhost:8000
```

Par défaut, `TTS_PROVIDER=fake` et `LLM_PROVIDER=fake` : aucun service externe n'est appelé.

## Données de départ

```bash
uv run python manage.py seed_formats                   # crée ou remet à jour les 3 formats et leurs 9 épreuves
uv run python manage.py make_dev_test --mode rapide    # test factice publié, audio factice (développement seulement)
```

`make_dev_test` accepte `--mode rapide|moyen|reel` et refuse de tourner si `DJANGO_DEBUG=false`.
Avec `--draft`, il passe par le vrai circuit (brief, fichier d'import, validation) et laisse un test **en brouillon,
sans audio**, prêt à être relu dans le back-office.
Ses textes sont faits de pseudo-mots : ils ont la bonne longueur, pas de sens.

## Produire un test (back-office)

1. `/bo/briefs/` : choisir un mode, télécharger le brief (`brief_<format>_<numéro>.json`).
2. Le joindre à une conversation du Projet Claude « rédaction », récupérer le ou les fichiers JSON.
3. `/bo/import/` : envoyer 1 à 3 fichiers. La plateforme valide tout (schéma, nombre d'items par niveau,
   bonne réponse imposée par le brief, longueurs, doublons…). Une seule erreur et aucune question n'est enregistrée :
   seul l'import refusé est gardé, avec son rapport, que la liste « Derniers imports » permet de rouvrir
   pour le coller dans la conversation de rédaction. Sinon, le test est créé en brouillon.

4. `/bo/tests/<id>/` (relecture) : pour chaque question, support ou script, options, bonne réponse, explication,
   avertissements de l'import ; boutons « Approuver », « Rejeter », « Modifier » (mêmes contrôles qu'à l'import ;
   la question repasse alors en brouillon).
5. Contrôle à l'aveugle : « Télécharger blind.json » (questions sans les réponses), le faire passer dans le Projet Claude
   « relecture », puis envoyer le fichier rendu : chaque désaccord et chaque doute est signalé.
6. « Générer l'audio du test » : un seul clic ; la page enchaîne la synthèse des questions de CO avec une barre de progression.
   Fermer la page n'est pas grave : un nouveau clic reprend là où la synthèse s'était arrêtée, sans rien refaire.
   La génération est refusée si le test ne tient pas dans le quota du mois (95 % du quota gratuit).
   Un script modifié rend son audio « périmé » : « Régénérer l'audio » refait cet item seul.
7. « Publier » : possible seulement quand toutes les questions sont approuvées et tous les audio à jour.
   Le test est alors figé ; seule action possible ensuite : « Archiver ».

Autres pages : `/bo/` (stock, quota vocal du mois, dépense API, derniers imports), `/bo/voices/` (écoute des voix,
distribution), `/bo/usage/` (journal des consommations). `uv run python manage.py tts_list_voices` affiche les voix
du fournisseur configuré.

Le schéma utilisé par l'application est `bank/data/schema-import.json`, copie de `docs/schema-import.json`.
Les exemples de `docs/exemples/` sont recopiés dans `tests/fixtures/`. Un test signale toute différence
quand `docs/` est présent : après une modification de `docs/`, recopier les fichiers.

## Passer un test

Sur le tableau de bord, choisir un mode puis « Commencer » : la plateforme propose le plus ancien test publié
pas encore terminé. Le lecteur enchaîne les consignes et les trois épreuves (CO, MSL, CE), puis affiche
les résultats, la correction (`/attempts/<id>/review/`) et l'historique (`/history/`).

Les échéances sont tenues par le serveur. Un passage interrompu se reprend depuis le tableau de bord ;
il est abandonné d'office 30 minutes après la durée prévue du test.

## Démarrer et tester

Pour une personne qui ne développe pas, sous Windows, dans PowerShell, depuis le dossier du projet.
Les essais se font **toujours dans la base de démonstration** (`data/demo.sqlite3`), jamais dans la base réelle
(`data/db.sqlite3`, qui contient les vrais tests), et sans les clés d'API de `.env`.

**Dans chaque fenêtre PowerShell utilisée pour les essais**, taper d'abord :

```powershell
$env:DATABASE_URL = "sqlite:///data/demo.sqlite3"
$env:MEDIA_ROOT = "data/demo-media"
$env:TTS_PROVIDER = "fake"
$env:LLM_PROVIDER = "fake"
```

Ces quatre lignes ne valent que pour la fenêtre où elles sont tapées. Sans elles, ce sont la base réelle et les valeurs
de `.env` qui s'appliquent : avec `google`, chaque audio généré consomme le quota Google réel ; avec `anthropic`,
chaque jeu de conseils coûte environ 0,05 $.

**Préparer la base de démonstration** (la première fois, puis chaque fois qu'on veut repartir de zéro ;
arrêter le site avant) :

```powershell
uv sync
uv run python manage.py reset_demo
```

`reset_demo` efface la base de démonstration et ses fichiers audio, la recrée avec les trois formats d'examen
et un compte administrateur de démonstration : **admin@demo.test**, mot de passe **demo-tcf-2026**.
Elle refuse de s'exécuter sans les quatre lignes ci-dessus.

**Lancer le site :**

```powershell
uv run python manage.py runserver
```

Ouvrir <http://localhost:8000> et se connecter avec le compte de démonstration. Arrêter le site avec Ctrl+C.

**Créer des tests factices** (dans une seconde fenêtre PowerShell, après y avoir tapé les quatre lignes) :

```powershell
uv run python manage.py make_dev_test --mode rapide           # test publié, prêt à passer
uv run python manage.py make_dev_test --mode rapide --draft   # test en brouillon, à relire et publier
```

`make_dev_test` ne s'exécute que dans la base de démonstration.

**Audio factice et Google.** Avec `TTS_PROVIDER=google`, un audio produit par le fournisseur factice n'est jamais à jour :
l'écran de relecture affiche le badge « Audio factice », la publication est refusée, et « Générer l'audio du test »
le refait avec Google. Les synthèses factices sont journalisées mais ne comptent pas dans le quota du mois.

**Supprimer un test** en brouillon ou archivé : bouton « Supprimer » en bas de l'écran de relecture, après avoir retapé
le numéro du test. Impossible pour un test publié (l'archiver d'abord) ou passé par un compte non administrateur ;
le message nomme alors le ou les comptes en cause.

**Conseils personnalisés** (`/advice/`) : disponibles après 3 tests rapides, ou 2 moyens, ou 1 réel (terminés).
Ils sont rédigés à l'ouverture de la page de résultats. Avec `LLM_PROVIDER=fake`, ils viennent des modèles de secours
(`advice/fallbacks/`).

**Base réelle, après chaque étape validée :** `uv run python manage.py migrate` (sans les quatre lignes) met sa
structure à jour. Sans cela, les nouvelles pages échouent (par exemple, « Supprimer » après l'étape 5).

**Vérifier tous les tests automatiques :** `uv run pytest` (aucun appel réseau, `.env` est ignoré).

## Commandes

```bash
uv run pytest                         # tests (aucun appel réseau)
uv run ruff check .                   # analyse du code
uv run ruff format .                  # mise en forme
```

### Compte administrateur

```bash
uv run python manage.py createsuperuser
```

La commande demande un nom d'utilisateur (interne, jamais affiché), une adresse e-mail et un mot de passe.
On se connecte ensuite avec **l'adresse e-mail** sur `/accounts/login/`. Le compte a accès à l'admin Django (`/admin/`)
et au back-office (`/bo/`). Pour donner l'accès au back-office à un autre compte, cocher « Statut équipe » dans l'admin.

### Comptes et mots de passe

- Inscription libre, ou réservée aux personnes qui connaissent `SIGNUP_CODE` si cette variable est renseignée.
- Avec `EMAIL_ENABLED=false` (valeur par défaut), « Mot de passe oublié » invite à contacter l'administrateur.
  L'administrateur ouvre `/bo/users/`, clique sur « Générer un lien de réinitialisation » et transmet le lien lui-même.
  Le lien ne sert qu'une fois (il devient invalide dès que le mot de passe change) et expire après 3 jours.
- Avec `EMAIL_ENABLED=true` et les variables SMTP renseignées, le lien est envoyé par e-mail.

### Styles (Tailwind CSS)

La feuille compilée `static/css/app.css` est versionnée : le site fonctionne sans recompiler.
Après avoir modifié un gabarit ou `assets/css/app.css` :

```bash
uv run python scripts/tailwind.py build   # compile et minifie
uv run python scripts/tailwind.py watch   # recompile à chaque modification
```

Au premier lancement, le script télécharge l'exécutable Tailwind dans `.tools/` (vérification SHA-256).

## Organisation

| Dossier | Rôle |
| --- | --- |
| `config/` | Réglages, adresses, WSGI |
| `accounts/` | Comptes |
| `bank/` | Formats, questions, tests, import, audio |
| `exam/` | Passages, lecteur, score, résultats |
| `advice/` | Conseils personnalisés |
| `backoffice/` | Pages d'administration (`/bo/`) |
| `usage/` | Consommations et quotas |
| `templates/`, `static/` | Gabarits, styles, `static/vendor/` (HTMX, Alpine.js) |
| `data/` | Base SQLite et fichiers audio (ignoré par git) |

## Configuration

Les variables sont lues dans `.env` ; la liste est dans `.env.example` et décrite dans la section 3 de la spécification.
Une variable laissée vide prend sa valeur par défaut.
