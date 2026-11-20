"""Règles de contenu des questions (SPEC 4.3, 4.4, 6.1 et 6.3), regroupées en un seul endroit."""

from bank.language_points import points_at

SECTIONS = ["CO", "MSL", "CE"]
SECTION_LABELS = {
    "CO": "Compréhension orale",
    "MSL": "Maîtrise des structures de la langue",
    "CE": "Compréhension écrite",
}
LEVELS = ["A1", "A2", "B1", "B2", "C1", "C2"]
LETTERS = ["A", "B", "C", "D"]

# --- Longueurs cibles (4.3) ----------------------------------------------------------------------

# Audio de CO hors question, en secondes.
CO_SECONDS = {
    "A1": (5, 10),
    "A2": (10, 15),
    "B1": (15, 30),
    "B2": (30, 45),
    "C1": (40, 60),
    "C2": (45, 70),
}
# Sous-type « question_reponse » : une simple question, quel que soit le niveau.
QUESTION_REPONSE_SECONDS = (2, 8)
# Texte de CE, en mots.
CE_WORDS = {
    "A1": (20, 40),
    "A2": (40, 80),
    "B1": (80, 130),
    "B2": (130, 200),
    "C1": (200, 280),
    "C2": (250, 350),
}
# Phrase de MSL, en mots (le blanc « ___ » ne compte pas : voir content.word_count).
MSL_WORDS = {
    "A1": (6, 12),
    "A2": (8, 15),
    "B1": (8, 20),
    "B2": (8, 25),
    "C1": (10, 30),
    "C2": (10, 35),
}
LENGTH_TOLERANCE = 0.15
# Durée estimée d'un script avant synthèse : caractères des répliques / 15.
CHARS_PER_SECOND = 15

# --- Sous-types et étiquettes (4.4) --------------------------------------------------------------

CO_SUBTYPES = {
    "A1": ["question_reponse", "annonce"],
    "A2": ["annonce", "message_vocal", "dialogue"],
    "B1": ["dialogue", "message_vocal", "interview"],
    "B2": ["interview", "emission_radio"],
    "C1": ["emission_radio", "expose"],
    "C2": ["expose", "debat"],
}
CE_SUBTYPES = {
    "A1": ["affiche_annonce", "message"],
    "A2": ["message", "courrier", "affiche_annonce"],
    "B1": ["courrier", "article"],
    "B2": ["article", "texte_argumentatif"],
    "C1": ["texte_argumentatif", "texte_specialise"],
    "C2": ["texte_specialise", "texte_litteraire"],
}
MSL_SUBTYPE = "phrase_a_trou"
MSL_BLANK = "___"

QUESTION_REPONSE = "question_reponse"
QUESTION_REPONSE_TEXT = "Choisissez la réponse qui convient."
# Niveaux où les options d'une question-réponse sont lues à voix haute (6.1).
AUDIO_LETTERS_LEVELS = ["A1", "A2"]

# Points de langue valides par niveau, lus dans bank/data/language_points.json (familles : voir
# bank/language_points.py).
LANGUAGE_POINTS = {level: points_at(level) for level in LEVELS}

SKILLS = {
    "CO": ["explicite", "global", "inference", "opinion"],
    "CE": ["explicite", "global", "inference", "opinion"],
    "MSL": ["grammaire", "lexique", "registre"],
}

DOC_LENGTH = {
    "A1": "court",
    "A2": "court",
    "B1": "moyen",
    "B2": "moyen",
    "C1": "long",
    "C2": "long",
}

MAX_SPEAKERS = 3

# --- Import (6.2, 6.3) ---------------------------------------------------------------------------

MAX_IMPORT_FILES = 3
MAX_IMPORT_FILE_BYTES = 2 * 1024 * 1024
NEAR_DUPLICATE_RATIO = 85
# Avertissements de longueur (6.3), en CO et CE seulement : la bonne réponse dépasse la moyenne des
# trois autres options d'au moins 40 % et d'au moins 12 caractères…
CORRECT_LENGTH_EXCESS = 0.40
CORRECT_LENGTH_MIN_CHARS = 12
# … ou elle est strictement la plus longue dans plus de 40 % des items d'une épreuve.
CORRECT_LONGEST_SHARE = 0.40
LENGTH_WARNING_SECTIONS = ("CO", "CE")

# --- Brief (6.1) ---------------------------------------------------------------------------------

AVOID_MAX_LINES = 40
AVOID_SUPPORT_CHARS = 60


def subtypes_for(section: str, level: str) -> list[str]:
    if section == "MSL":
        return [MSL_SUBTYPE]
    return (CO_SUBTYPES if section == "CO" else CE_SUBTYPES)[level]


def length_target(section: str, level: str, subtype: str) -> dict:
    """Fourchette cible telle qu'elle figure dans le brief (`target`)."""
    if section == "CO":
        low, high = QUESTION_REPONSE_SECONDS if subtype == QUESTION_REPONSE else CO_SECONDS[level]
        return {"audio_seconds": [low, high]}
    low, high = (CE_WORDS if section == "CE" else MSL_WORDS)[level]
    return {"words": [low, high]}


def expected_options_mode(section: str, level: str, subtype: str) -> str:
    if section == "CO" and subtype == QUESTION_REPONSE and level in AUDIO_LETTERS_LEVELS:
        return "audio_letters"
    return "text"


# --- Lieux, canaux et accents de la CO (7.5) -----------------------------------------------------

SETTINGS = [
    "aucun",
    "gare",
    "train",
    "aeroport",
    "metro_bus",
    "rue",
    "cafe_restaurant",
    "magasin",
    "marche",
    "bureau",
    "maison",
    "ecole",
    "lieu_public",
    "exterieur",
    "salle_conference",
]
SETTING_LABELS = {
    "aucun": "Aucun",
    "gare": "Gare",
    "train": "Train",
    "aeroport": "Aéroport",
    "metro_bus": "Métro, bus",
    "rue": "Rue",
    "cafe_restaurant": "Café, restaurant",
    "magasin": "Magasin",
    "marche": "Marché",
    "bureau": "Bureau",
    "maison": "Maison",
    "ecole": "École",
    "lieu_public": "Lieu public (musée, mairie, piscine…)",
    "exterieur": "Extérieur",
    "salle_conference": "Salle de conférence",
}
CHANNELS = ["direct", "haut_parleur", "telephone", "radio"]
CHANNEL_LABELS = {
    "direct": "Direct",
    "haut_parleur": "Haut-parleur",
    "telephone": "Téléphone",
    "radio": "Radio",
}
ACCENTS = ["fr-FR", "fr-CA"]
DEFAULT_SETTING, DEFAULT_CHANNEL, DEFAULT_ACCENT = "aucun", "direct", "fr-FR"
CHIME = "carillon"
# Catégories de la bibliothèque sonore : les lieux (sauf « aucun ») et le carillon.
AMBIENCE_CATEGORIES = [s for s in SETTINGS if s != DEFAULT_SETTING] + [CHIME]
AMBIENCE_LABELS = {**SETTING_LABELS, CHIME: "Carillon"}

# Combinaisons permises par sous-type : canal → lieux (7.5.1).
SOUND_COMBINATIONS = {
    "question_reponse": {
        "direct": ["aucun", "maison", "bureau", "magasin", "cafe_restaurant", "rue"]
    },
    "annonce": {
        "haut_parleur": [
            "gare",
            "aeroport",
            "train",
            "metro_bus",
            "magasin",
            "ecole",
            "lieu_public",
        ],
        "radio": ["aucun"],
    },
    "message_vocal": {"telephone": ["aucun", "rue", "gare", "cafe_restaurant"]},
    "dialogue": {
        "direct": [
            "maison",
            "bureau",
            "cafe_restaurant",
            "rue",
            "magasin",
            "train",
            "gare",
            "marche",
            "ecole",
            "lieu_public",
            "exterieur",
        ],
        "telephone": ["aucun"],
    },
    "interview": {
        "radio": ["aucun"],
        "direct": ["rue", "marche", "exterieur", "salle_conference"],
    },
    "emission_radio": {"radio": ["aucun"]},
    "debat": {"radio": ["aucun"]},
    "expose": {"direct": ["salle_conference"]},
}
# Sous-types où un locuteur peut avoir l'accent québécois.
ACCENT_SUBTYPES = ["message_vocal", "dialogue", "interview", "emission_radio", "debat", "expose"]
# Lieux où une annonce au haut-parleur commence par un carillon (7.5.3).
CHIME_SETTINGS = ["gare", "aeroport", "train", "metro_bus", "magasin"]


def allowed_combinations(subtype: str) -> list[tuple[str, str]]:
    """(canal, lieu) permis pour un sous-type de CO."""
    table = SOUND_COMBINATIONS.get(subtype, {})
    return [(channel, setting) for channel, settings in table.items() for setting in settings]


def script_sound(audio_script: dict | None) -> tuple[str, str]:
    """(lieu, canal) d'un script, valeurs par défaut comprises."""
    script = audio_script or {}
    return script.get("setting", DEFAULT_SETTING), script.get("channel", DEFAULT_CHANNEL)


def speaker_accent(speaker: dict) -> str:
    return speaker.get("accent", DEFAULT_ACCENT)
