"""Calculs sur le contenu d'un item, communs à l'import, au brief et aux modèles.

Les fonctions prennent les champs bruts (ceux du fichier d'import) :
elles servent avant comme après l'enregistrement.
"""

import hashlib
import json
import re
import unicodedata

from bank.constants import (
    CE_WORDS,
    CHARS_PER_SECOND,
    CO_SECONDS,
    DOC_LENGTH,
    LENGTH_TOLERANCE,
    LETTERS,
    MSL_WORDS,
    QUESTION_REPONSE,
    QUESTION_REPONSE_SECONDS,
)


def support_text(section: str, stimulus_text: str | None, audio_script: dict | None) -> str:
    if section == "CO":
        return " ".join(line["text"] for line in (audio_script or {}).get("lines", []))
    return stimulus_text or ""


def word_count(text: str) -> int:
    """Nombre de mots : éléments séparés par des espaces (« ___ » compte pour un mot)."""
    return len(text.split())


def script_chars(audio_script: dict | None) -> int:
    return sum(len(line["text"]) for line in (audio_script or {}).get("lines", []))


def estimated_seconds(audio_script: dict | None) -> float:
    """Durée estimée d'un script avant synthèse : caractères des répliques / 15."""
    return script_chars(audio_script) / CHARS_PER_SECOND


def doc_length(level: str) -> str:
    return DOC_LENGTH[level]


def length_check(item: dict) -> tuple[float, int, int, str]:
    """Mesure de longueur d'un item et sa fourchette : (valeur, minimum, maximum, unité)."""
    section, level = item["section"], item["level"]
    if section == "CO":
        if item["subtype"] == QUESTION_REPONSE:
            low, high = QUESTION_REPONSE_SECONDS
        else:
            low, high = CO_SECONDS[level]
        return estimated_seconds(item.get("audio_script")), low, high, "s"
    low, high = (CE_WORDS if section == "CE" else MSL_WORDS)[level]
    return word_count(item.get("stimulus_text") or ""), low, high, "mots"


def within_tolerance(
    value: float, low: int, high: int, tolerance: float = LENGTH_TOLERANCE
) -> bool:
    return low * (1 - tolerance) <= value <= high * (1 + tolerance)


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFC", text).casefold()
    text = text.replace("’", "'")
    return re.sub(r"\s+", " ", text).strip()


def content_hash(item: dict) -> str:
    """SHA-256 du texte normalisé : épreuve, support, question et options."""
    parts = [
        item["section"],
        support_text(item["section"], item.get("stimulus_text"), item.get("audio_script")),
        item.get("question") or "",
        *item["options"],
    ]
    return hashlib.sha256(normalize("\n".join(parts)).encode()).hexdigest()


def comparison_text(item: dict) -> str:
    """Texte comparé pour les quasi-doublons : support + question.

    La question fixe des questions-réponses n'apporte rien : elle est écartée.
    """
    support = support_text(item["section"], item.get("stimulus_text"), item.get("audio_script"))
    question = "" if item.get("subtype") == QUESTION_REPONSE else item.get("question") or ""
    return normalize(f"{support} {question}")


def spoken_texts(item: dict) -> list[str]:
    """Textes envoyés à la synthèse vocale pour un item de CO (7.3)."""
    if item["section"] != "CO":
        return []
    texts = [line["text"] for line in (item.get("audio_script") or {}).get("lines", [])]
    if item.get("subtype") != QUESTION_REPONSE and item.get("question"):
        texts.append(item["question"])
    if item.get("options_mode") == "audio_letters":
        pairs = zip(LETTERS, item["options"], strict=True)
        texts += [f"{letter}. {option}" for letter, option in pairs]
    return texts


def tts_chars(item: dict) -> int:
    return sum(len(text) for text in spoken_texts(item))


def script_hash(item: dict) -> str:
    """Empreinte de tout ce qui compose l'audio : un changement rend l'audio périmé."""
    payload = {
        "audio_script": item.get("audio_script"),
        "spoken": spoken_texts(item),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()


def item_fields(item) -> dict:
    """Champs bruts d'un objet `Item`, au format du fichier d'import."""
    return {
        "section": item.section,
        "level": item.level,
        "subtype": item.subtype,
        "stimulus_text": item.stimulus_text or None,
        "audio_script": item.audio_script,
        "question": item.question,
        "options": item.options,
        "correct_index": item.correct_index,
        "options_mode": item.options_mode,
        "tags": item.tags,
    }
