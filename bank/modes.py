"""Noms des modes montrés aux utilisateurs (SPEC 4.1, choix du 20 novembre 2026).

Une seule fonction donne le nom, le pluriel et l'article selon le mode : tous les textes vus par
les utilisateurs passent par elle. Le back-office et le code gardent leur vocabulaire
(`Test`, `rapide`, `moyen`, `reel`).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ModeNames:
    name: str  # « Entraînement rapide »
    item: str  # « Entraînement », pour « Entraînement 1 »
    noun: str  # « entraînement »
    nouns: str  # « entraînements »
    singular: str  # « entraînement rapide »
    plural: str  # « entraînements rapides »
    the: str  # « l'entraînement rapide »
    this: str  # « cet entraînement »
    next: str  # « l'entraînement suivant »

    def count(self, n: int) -> str:
        """« 1 entraînement rapide », « 2 entraînements rapides »."""
        return f"{n} {self.singular if n == 1 else self.plural}"

    @property
    def already_done(self) -> str:
        return f"Vous avez déjà passé {self.this}."

    @property
    def see_all(self) -> str:
        return f"Voir les {self.nouns}"

    def all_done(self, available: int) -> str:
        if available == 1:
            passed = f"Vous avez passé le seul {self.singular} disponible."
        else:
            passed = f"Vous avez passé les {available} {self.plural} disponibles."
        return f"{passed} De nouveaux {self.nouns} seront ajoutés."


TRAINING = {
    "item": "Entraînement",
    "noun": "entraînement",
    "nouns": "entraînements",
    "this": "cet entraînement",
    "next": "l'entraînement suivant",
}

MODE_NAMES = {
    "rapide": ModeNames(
        name="Entraînement rapide",
        singular="entraînement rapide",
        plural="entraînements rapides",
        the="l'entraînement rapide",
        **TRAINING,
    ),
    "moyen": ModeNames(
        name="Entraînement moyen",
        singular="entraînement moyen",
        plural="entraînements moyens",
        the="l'entraînement moyen",
        **TRAINING,
    ),
    "reel": ModeNames(
        name="Test en conditions réelles",
        item="Test",
        noun="test",
        nouns="tests",
        singular="test en conditions réelles",
        plural="tests en conditions réelles",
        the="le test en conditions réelles",
        this="ce test",
        next="le test suivant",
    ),
}


def mode_names(mode: str) -> ModeNames:
    return MODE_NAMES[mode]
