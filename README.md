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
   « relecture », puis envoyer sa réponse : le fichier, ou le texte collé dans la zone prévue (le JSON seul, le bloc
   ```json``` copié tel quel, ou à défaut la ligne « grille » `1A · 2C · 3B …`). Chaque désaccord et chaque doute est signalé ; une question sans réponse du relecteur (par exemple remplacée depuis par une correction) est « Non contrôlée » et ne compte pas dans les désaccords.
   « Importer des corrections » : un fichier (ou un texte collé) au format d'import, avec le même `brief_id`,
   ne contenant que les items à remplacer. Mêmes contrôles qu'à l'import ; tout ou rien. Chaque item remplace celui
   du même emplacement, repasse en brouillon et perd son résultat de contrôle à l'aveugle ; un item identique
   à l'actuel garde son statut.
6. « Générer l'audio du test » (en haut de la page, dans le bilan) : un seul clic ; la page enchaîne la synthèse des questions de CO avec une barre de progression.
   Fermer la page n'est pas grave : un nouveau clic reprend là où la synthèse s'était arrêtée, sans rien refaire.
   La génération est refusée si le test ne tient pas dans le quota du mois (95 % du quota gratuit).
   Un script modifié rend son audio « périmé » : « Régénérer cet audio », présent sous une question dès que son audio existe, refait cet item seul.
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

Pour une personne qui ne développe pas, sous Windows. Aucune variable à taper : deux fichiers à double-cliquer,
à la racine du projet. Chaque fenêtre affiche au démarrage la base utilisée, les fournisseurs (voix, conseils)
et le compte à utiliser. Pour arrêter un site, fermer sa fenêtre.

| Fichier | Site | Adresse | Base | Voix et conseils |
| --- | --- | --- | --- | --- |
| `demo.cmd` | Démonstration, pour les essais | <http://localhost:8001> | `data/demo.sqlite3` | factices, toujours |
| `demarrer.cmd` | Site réel | <http://localhost:8000> | `data/db.sqlite3` | ceux de `.env` (payants si configurés) |
| `telephone.cmd` | Démonstration, depuis un téléphone | `https://….trycloudflare.com` (affichée) | `data/demo.sqlite3` | factices, toujours |

**Une seule fois :** dans PowerShell, depuis le dossier du projet, `uv sync` (installe les dépendances).

**`demo.cmd`** crée la base de démonstration la première fois (`reset_demo` : formats d'examen et compte
**admin@demo.test**, mot de passe **demo-tcf-2026**), met sa structure à jour, lance le site sur le port 8001 et ouvre
le navigateur. Chaque page affiche un bandeau orange « DÉMONSTRATION ». Ses cookies portent d'autres noms que ceux
du site réel : on peut être connecté aux deux en même temps. Même si `.env` contient des clés, la démonstration
n'appelle jamais Google ni Claude, et ses fichiers audio vont dans `data/demo-media`.

**`telephone.cmd`** lance le site de démonstration et un tunnel HTTPS Cloudflare (« Quick Tunnel », sans compte),
puis affiche en gros une adresse `https://….trycloudflare.com` (copiée aussi dans le presse-papiers) : ouvrez-la sur
le téléphone, en Wi-Fi ou en 4G, pour passer un test et installer l'application. L'adresse change à chaque lancement
et n'est autorisée que dans ce mode. Fermez d'abord `demo.cmd` (même port 8001). La première fois, `cloudflared.exe`
est téléchargé depuis les publications officielles de Cloudflare sur GitHub dans `.tools/`, avec vérification de son
empreinte. Tant que la fenêtre est ouverte, toute personne qui connaît l'adresse peut voir la démonstration.

**`demarrer.cmd`** met à jour la structure de la base réelle (`migrate`), lance le site sur le port 8000 et ouvre
le navigateur. À utiliser après chaque étape validée, pour que les nouvelles pages fonctionnent.

**Tests factices et remise à zéro de la démonstration** (dans PowerShell, depuis le dossier du projet,
en recopiant les quatre premières lignes : elles ne valent que pour cette fenêtre) :

```powershell
$env:DATABASE_URL = "sqlite:///data/demo.sqlite3"
$env:MEDIA_ROOT = "data/demo-media"
$env:TTS_PROVIDER = "fake"
$env:LLM_PROVIDER = "fake"
uv run python manage.py make_dev_test --mode rapide           # test publié, prêt à passer
uv run python manage.py make_dev_test --mode rapide --draft   # test en brouillon, à relire et publier
uv run python manage.py reset_demo                            # repartir de zéro (fermer demo.cmd avant)
```

`make_dev_test` et `reset_demo` refusent de s'exécuter ailleurs que dans la base de démonstration.

**Audio factice et Google.** Avec `TTS_PROVIDER=google`, un audio produit par le fournisseur factice n'est jamais à jour :
l'écran de relecture affiche le badge « Audio factice », la publication est refusée, et « Générer l'audio du test »
le refait avec Google. Les synthèses factices sont journalisées mais ne comptent pas dans le quota du mois.

**Supprimer un test** en brouillon ou archivé : bouton « Supprimer » en bas de l'écran de relecture, après avoir retapé
le numéro du test. Impossible pour un test publié (l'archiver d'abord) ou passé par un compte non administrateur ;
le message nomme alors le ou les comptes en cause.

**Conseils personnalisés** (`/advice/`) : disponibles après 3 tests rapides, ou 2 moyens, ou 1 réel (terminés).
Ils sont rédigés à l'ouverture de la page de résultats. Avec `LLM_PROVIDER=fake`, ils viennent des modèles de secours
(`advice/fallbacks/`).

**Application installable.** Le site est une application web installable (manifeste, icônes, service worker,
page hors ligne). La page « Installer l'application » (lien en bas de chaque page, `/install/`) explique l'installation
sur Android, iPhone et ordinateur. Un téléphone n'installe l'application que depuis une adresse en HTTPS
(ou `localhost` sur l'ordinateur lui-même).

**Mise en ligne.** Les fichiers de production sont dans `deploy/` ; le pas-à-pas est `docs/DEPLOIEMENT.md`.
Les tests publiés sur l'ordinateur passent sur le serveur sans régénérer l'audio : `uv run python manage.py export_tests`
(dans PowerShell, base réelle) crée `tests_<date>.zip`, que `import_tests` recrée sur le serveur (section 8 du pas-à-pas).

**Vérifier tous les tests automatiques :** `uv run pytest` (aucun appel réseau, `.env` est ignoré ; environ 3 minutes).
Les tests de bout en bout pilotent Chromium : la première fois, `uv run playwright install chromium`
(sans lui, ces tests sont ignorés avec un message). Les icônes se régénèrent avec
`uv run python scripts/make_icons.py` à partir de `assets/icons/icon.svg`.

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
