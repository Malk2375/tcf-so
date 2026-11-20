"""Contenu factice de bonne longueur, pour `make_dev_test` et les tests automatisés.

Les textes sont faits de pseudo-mots tirés au hasard : deux items factices ne se ressemblent pas,
ce qui leur permet de passer le contrôle des quasi-doublons.
"""

import random

from bank.constants import (
    CHARS_PER_SECOND,
    LETTERS,
    MSL_BLANK,
    QUESTION_REPONSE,
    QUESTION_REPONSE_TEXT,
    SKILLS,
)
from bank.language_points import expected_skill

_CONSONANTS = "bcdfglmnprstvz"
_VOWELS = ["a", "e", "i", "o", "u", "é", "ou", "an", "on"]


def _word(rng: random.Random, syllables: int | None = None) -> str:
    count = syllables or rng.randint(1, 3)
    return "".join(rng.choice(_CONSONANTS) + rng.choice(_VOWELS) for _ in range(count))


def _words(rng: random.Random, n: int) -> list[str]:
    return [_word(rng) for _ in range(n)]


def _sentence(rng: random.Random, n: int) -> str:
    words = _words(rng, n)
    return " ".join(words).capitalize() + "."


def _text_of_words(rng: random.Random, n: int) -> str:
    """Texte de n mots exactement, découpé en phrases."""
    sentences, left = [], n
    while left:
        size = min(left, rng.randint(6, 12))
        sentences.append(_sentence(rng, size))
        left -= size
    return " ".join(sentences)


def _script(rng: random.Random, subtype: str, seconds: float) -> dict:
    speakers = {"F1": {"gender": "F"}}
    if subtype in {"dialogue", "interview", "debat"}:
        speakers["M1"] = {"gender": "M"}
    order = list(speakers)
    lines, chars, target = [], 0, round(seconds * CHARS_PER_SECOND)
    while chars < target:
        text = _sentence(rng, rng.randint(4, 9))
        remaining = target - chars
        if len(text) > remaining:
            # Dernière réplique raccourcie pour tomber sur la durée visée.
            text = text[:remaining].rstrip(" .") + "."
        lines.append({"speaker": order[len(lines) % len(order)], "text": text})
        chars += len(text)
    return {"style": "document factice, ton neutre", "speakers": speakers, "lines": lines}


def fake_item(slot: dict, rng: random.Random) -> dict:
    """Item factice, conforme au schéma d'import, qui répond à un emplacement de brief."""
    section, level, subtype = slot["section"], slot["level"], slot["subtype"]
    item = {
        "slot": slot["slot"],
        "section": section,
        "level": level,
        "subtype": subtype,
        "stimulus_text": None,
        "audio_script": None,
        "question": f"Question factice : {' '.join(_words(rng, 3))} ?",
        "options": [f"Réponse {letter} {_word(rng)}" for letter in LETTERS],
        "correct_index": LETTERS.index(slot["correct_letter"]),
        "options_mode": slot["options_mode"],
        "explanation": (
            f"Explication factice : la bonne réponse est {slot['correct_letter']}, "
            "les trois autres options ne correspondent pas au document."
        ),
        "tags": {"skill": rng.choice(SKILLS[section]), "theme": slot["theme"]},
    }
    if section == "CO":
        low, high = slot["target"]["audio_seconds"]
        item["audio_script"] = _script(rng, subtype, (low + high) / 2)
        # Lieu, canal et accent de l'emplacement (7.5.2), quand le brief les donne.
        for key in ("setting", "channel"):
            if key in slot:
                item["audio_script"][key] = slot[key]
        if slot.get("accent") == "fr-CA":
            item["audio_script"]["speakers"]["F1"]["accent"] = "fr-CA"
        if subtype == QUESTION_REPONSE:
            item["question"] = QUESTION_REPONSE_TEXT
    else:
        low, high = slot["target"]["words"]
        n = (low + high) // 2
        if section == "MSL":
            words = _words(rng, n - 1)
            words.insert(rng.randint(0, len(words)), MSL_BLANK)
            item["stimulus_text"] = " ".join(words) + "."
            item["question"] = ""
            # Options de même longueur, comme les options d'un seul mot de l'examen.
            item["options"] = [_word(rng, 2) + letter.lower() for letter in LETTERS]
            item["tags"]["language_point"] = slot["language_point"]
            item["tags"]["skill"] = expected_skill(slot["language_point"])
        else:
            item["stimulus_text"] = _text_of_words(rng, n)
    return item


def fake_import_file(brief_content: dict, rng: random.Random | None = None) -> dict:
    rng = rng or random.Random()
    return {
        "schema_version": 1,
        "format": brief_content["format"],
        "brief_id": brief_content["brief_id"],
        "items": [fake_item(slot, rng) for slot in brief_content["slots"]],
    }
