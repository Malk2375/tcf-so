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
   Avant de commencer, l'écran liste les lieux demandés sans bruit de fond actif : ces documents seront générés sans bruit de fond.
   Chaque réplique synthétisée est gardée en cache (`tts_cache/`) : refaire un mixage (autre bruit, autre niveau) ne consomme aucun caractère.
   Un script modifié rend son audio « périmé » : « Régénérer cet audio », présent sous une question dès que son audio existe, refait cet item seul.
   « Approuver les N questions sans alerte » (dans le bilan, avec confirmation) approuve d'un coup les questions
   en brouillon où le relecteur est d'accord, sans doute ni avertissement ; les autres se traitent une par une.
7. « Publier » : possible seulement quand toutes les questions sont approuvées et tous les audio à jour.
   Le test est alors figé ; seule action possible ensuite : « Archiver ».
   La publication donne au test son **numéro public** (« Test 1 », « Test 2 »… par mode, jamais réutilisé), seul montré
   aux utilisateurs ; le back-office affiche les deux (« test n° 4 · publié comme Test 2 »).

Autres pages : `/bo/` (stock, quota vocal du mois, dépense API, derniers imports), `/bo/voices/` (écoute des voix
françaises et québécoises, distribution), `/bo/ambiences/` (bruits de fond : envoi, écoute, « Essayer »),
`/bo/usage/` (journal des consommations). `uv run python manage.py tts_list_voices` affiche les voix `fr-FR` et `fr-CA`
du fournisseur configuré.

**Bruits de fond, canaux et voix québécoises.** Le brief donne à chaque emplacement de CO un lieu (`setting` : gare, train,
café…) et un canal (`channel` : direct, haut-parleur, téléphone, radio), et à quelques emplacements un accent québécois
(`accent_quotas` des réglages du site : 1, 2 et 4 par mode ; 0 désactive). À la génération : voix québécoise pour un locuteur
`fr-CA`, traitement de la voix selon le canal, carillon avant une annonce au haut-parleur, bip avant un message vocal,
bruit de fond du lieu sous les répliques seulement (jamais sous la question ni les options), réglé par le rapport voix / bruit
`ambience_snr_db` (20 dB de A1 à B1, 16 dB de B2 à C2) mesuré sur l'extrait utilisé, puis corrigé par le réglage fin du son.
Un fichier d'import sans ces champs s'importe et se génère exactement comme avant ; l'audio d'un test publié n'est jamais touché.
Les sons viennent de `/bo/ambiences/` (licences CC0, CC BY, CC BY-NC ; 150 Mo au plus ; début et fin facultatifs)
ou du dossier `assets/sound/` avec son manifeste `ambiences.json` : double-cliquez sur `importer_sons.cmd`
(ou `uv run python manage.py import_ambiences_folder assets\sound` pour la base réelle). Une entrée dont la licence vaut
encore `A_VERIFIER` est refusée ; relancer ne crée pas de doublon. Les fichiers audio de `assets/sound/` ne vont pas
dans le dépôt git (seul le manifeste est suivi). La page publique `/credits/` (« Crédits sonores », en bas de chaque page)
liste les sons actifs et leurs licences. La démonstration utilise des bruits factices (bruit rose ou brun, carillon).

Le schéma utilisé par l'application est `bank/data/schema-import.json`, copie de `docs/schema-import.json`.
Les exemples de `docs/exemples/` sont recopiés dans `tests/fixtures/`. Un test signale toute différence
quand `docs/` est présent : après une modification de `docs/`, recopier les fichiers.

## Passer un test

Sur le tableau de bord, choisir un mode puis « Commencer » : la plateforme lance le plus ancien test publié
pas encore terminé. Pour en choisir un autre, ouvrir la liste du mode : chaque test à passer a son bouton « Commencer ».
Le lecteur enchaîne les consignes et les trois épreuves (CO, MSL, CE), puis affiche
les résultats, la correction (`/attempts/<id>/review/`) et l'historique (`/history/`).
En compréhension orale, chaque document à options écrites commence par 15 secondes de lecture (« L'enregistrement
commence dans 15 s ») ; les options lues à voix haute (lettres A à D) démarrent tout de suite. Le temps de lecture vaut
pour tous les tests, publiés compris, sans régénérer l'audio (`read_before_seconds` dans l'admin, par mode).
Pas de passage anticipé : chaque question se termine à la fin de son décompte. L'option
`allow_early_next` d'une épreuve (bouton « Valider », désactivé partout) reste disponible dans l'admin Django.

**Une seule épreuve** (entraînement moyen ; réglage `allow_part_attempts` du format dans l'admin) : sur la liste du mode,
chaque entraînement a un bouton « Test complet » tant qu'aucune épreuve n'est faite, puis une ligne par épreuve avec
son bouton « Commencer ». Chaque épreuve ne se passe qu'une fois ; un entraînement dont les trois épreuves sont faites,
ensemble ou séparément, est « passé » et compte pour les conseils. Résultats d'une épreuve seule : score et niveau
de l'épreuve, sans score global. Quand plus aucun entraînement n'est entier, le bouton de la carte devient
« Voir les épreuves à passer ».

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
| `debloquer.cmd` | Débloque la connexion d'un compte (démonstration ou site réel, au choix) | — | selon le choix | — |
| `importer_sons.cmd` | Importe les bruits de fond de `assets/sound/` (démonstration ou site réel, au choix) | — | selon le choix | — |

**Une seule fois :** dans PowerShell, depuis le dossier du projet, `uv sync` (installe les dépendances).

**`demo.cmd`** crée la base de démonstration la première fois (`reset_demo` : formats d'examen, tests factices,
compte **admin@demo.test**, mot de passe **demo-tcf-2026**, et compte **candidat@demo.test**, même mot de passe,
qui a déjà passé les trois entraînements rapides avec des erreurs sur les pronoms ; deux entraînements moyens à passer
en entier ou épreuve par épreuve ; un bruit de fond factice par lieu), met sa structure à jour, lance le site sur le port 8001 et ouvre
le navigateur. Chaque page affiche un bandeau orange « DÉMONSTRATION ». Ses cookies portent d'autres noms que ceux
du site réel : on peut être connecté aux deux en même temps. Même si `.env` contient des clés, la démonstration
n'appelle jamais Google ni Claude, et ses fichiers audio vont dans `data/demo-media`.

**`telephone.cmd`** lance le site de démonstration et un tunnel HTTPS Cloudflare (« Quick Tunnel », sans compte),
puis affiche en gros une adresse `https://….trycloudflare.com` (copiée aussi dans le presse-papiers) : ouvrez-la sur
le téléphone, en Wi-Fi ou en 4G, pour passer un test et installer l'application. L'adresse change à chaque lancement
et n'est autorisée que dans ce mode. Fermez d'abord `demo.cmd` (même port 8001). La première fois, `cloudflared.exe`
est téléchargé depuis les publications officielles de Cloudflare sur GitHub dans `.tools/`, avec vérification de son
empreinte. Tant que la fenêtre est ouverte, toute personne qui connaît l'adresse peut voir la démonstration.

**`demarrer.cmd`** met à jour la structure de la base réelle (`migrate`), recalcule les avertissements des tests
en brouillon avec les règles en vigueur (`refresh_warnings`), lance le site sur le port 8000 et ouvre
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

**Modes** : « Entraînement rapide » (24 questions, 28 min), « Entraînement moyen » (46 questions, 52 min) et
« Test en conditions réelles » (91 questions, 103 min), temps de lecture en CO compris. Les utilisateurs voient « Entraînement 1, 2… » ou « Test 1, 2… » ;
le back-office garde « test n° … ». Chaque carte du tableau de bord mène à la liste du mode (`/modes/rapide/` …),
rangée dans l'ordre de mise en ligne : date, score et niveau des tests passés, avec leurs liens « Résultats »
et « Correction » (même après archivage). Chaque test à passer y a son propre bouton « Commencer » ; celui de la carte
lance le prochain test non passé (marqué « prochain » dans la liste). Un test déjà passé, en brouillon ou archivé
ne se lance pas, même par une requête forgée.

**Un test ne se passe qu'une fois**, pour tous les comptes. Un test terminé n'est plus proposé ; quand tous les tests
d'un mode sont passés, la carte du mode le dit. Pour qu'un compte repasse un test, supprimez son passage dans
`/admin/` → Passages (la suppression emporte ses réponses et le conseil qu'il a déclenché). Un passage abandonné
ne compte pas.

**Connexions bloquées** : après 5 mots de passe faux en 5 minutes, le compte est bloqué 5 minutes (limitation
de django-allauth, aussi pour `/admin/`). Pour le débloquer aussitôt : bouton « Débloquer la connexion » dans
le back-office (Comptes), ou double-clic sur `debloquer.cmd` (choisir D ou R, puis taper l'adresse e-mail).
Les compteurs sont des fichiers dans `data/cache/` (`data/demo-cache/` pour la démonstration).

**Conseils personnalisés** (`/advice/`) : disponibles après 3 entraînements rapides distincts, ou 2 moyens, ou 1 test en
conditions réelles (terminés ; un entraînement moyen compte quand ses trois épreuves sont faites, ensemble ou séparément).
Ils sont rédigés à l'ouverture de la page de résultats : de 0 à 3 conseils, seulement sur des difficultés nettes,
sans aucun pourcentage. Sans difficulté nette, un résumé seul. Avec `LLM_PROVIDER=fake`, ils viennent des modèles
de secours (`advice/fallbacks/fallbacks.json`). Les anciens conseils (objectif, plan de la semaine) restent affichés
tels quels ; ceux qui vous semblent injustifiés se suppriment dans `/admin/` → Conseils.

**Briefs de MSL** : chaque test reçoit un nombre fixe de questions par famille de points de langue (verbes, pronoms,
prépositions, phrase, vocabulaire, expressions), d'après `bank/data/language_points.json`.

**Application installable.** Le site est une application web installable (manifeste, icônes, service worker,
page hors ligne). La page « Installer l'application » (lien en bas de chaque page, `/install/`) explique l'installation
sur Android, iPhone et ordinateur. Un téléphone n'installe l'application que depuis une adresse en HTTPS
(ou `localhost` sur l'ordinateur lui-même).

**Mise en ligne.** Les fichiers de production sont dans `deploy/` ; le pas-à-pas est `docs/DEPLOIEMENT.md`.
Les tests publiés sur l'ordinateur passent sur le serveur sans régénérer l'audio : `uv run python manage.py export_tests`
(dans PowerShell, base réelle) crée `tests_<date>.zip`, que `import_tests` recrée sur le serveur (section 8 du pas-à-pas).
Les MP3 contiennent déjà leur mixage. Pour produire des tests sur le serveur, la bibliothèque de bruits de fond se transfère
de même : `export_ambiences` (crée `ambiences_<date>.zip`) puis `import_ambiences <archive>`.

**Vérifier tous les tests automatiques :** `uv run pytest` (aucun appel réseau, `.env` est ignoré ; environ 8 minutes).
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
