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
