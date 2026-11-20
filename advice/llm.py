"""Rédaction des conseils par l'API Claude (SPEC 12.3), derrière une interface.

`AnthropicProvider` n'est utilisé que si `LLM_PROVIDER=anthropic` et que `ANTHROPIC_API_KEY`
est renseignée. Avec `LLM_PROVIDER=fake`, aucun appel : les modèles de secours sont utilisés.
Les tests injectent un fournisseur qui renvoie une réponse préparée.
"""

import contextlib
import json
from dataclasses import dataclass
from typing import Protocol

from django.conf import settings

from advice.content import resources
from bank.language_points import FAMILY_LABELS, language_points

MAX_TOKENS = 16000

SYSTEM_PROMPT = """\
Vous rédigez des conseils personnalisés pour une personne qui prépare le TCF tout public (test \
de connaissance du français). But : l'aider à progresser sur ses vrais points faibles. Un \
conseil n'existe que s'il repose sur un constat net : mieux vaut aucun conseil, ou un seul, \
qu'un conseil vague.

Ton : français, vouvoiement, mots simples, direct et encourageant.

Règles :
- Aucun chiffre de réussite : ni pourcentage, ni taux, ni points d'écart, ni « N questions sur \
N ». Dites les choses avec des mots : « souvent », « dès le niveau B2 », « dans les textes \
argumentatifs ». Les données ne contiennent aucun taux : n'en inventez pas.
- De 0 à 3 conseils, au plus un par constat fourni (« progress » ne donne pas de conseil : il \
sert seulement aux « progress_note »). « finding_id » reprend exactement l'identifiant du \
constat. Rien qui ne découle d'un constat. Omettez un conseil qui serait vague.
- Interdits : conseiller de refaire les questions manquées ou de repasser un test ; les astuces \
générales de QCM sans lien avec le constat (éliminer les options « absolues », lire les \
questions avant le document, etc.) ; les nombres d'exercices, de minutes ou de séances inventés \
; toute ressource hors de la liste fournie.
- « summary » : 1 à 3 phrases, où en est la personne et par quoi commencer.
- « title » : le point travaillé, en quelques mots. « diagnostic » : 1 ou 2 phrases, avec des \
mots.
- « lesson » : la règle ou la méthode propre au constat, de 60 à 150 mots, avec deux exemples \
nouveaux. Pour « msl_family », traitez les points de langue précis des erreurs jointes (par \
exemple : « après à moins que, le verbe se met au subjonctif »). Pour « time_pressure » et « \
answer_changes », la leçon est une méthode de travail pendant l'épreuve, appuyée pour « \
time_pressure » sur les repères de temps fournis.
- « error_reviews » : 1 ou 2 des questions manquées jointes au constat (« test_item_id » repris \
tel quel) : pourquoi l'option choisie ne convient pas, et l'indice qui menait à la bonne \
réponse. Pour « time_pressure » et « answer_changes », la liste peut être vide.
- « practice » : facultatif, de 0 à 3 exercices nouveaux, jamais une reprise des questions \
jointes, chacun avec 4 options différentes, l'indice (0 à 3) de la bonne réponse et une \
explication. Aucun exercice pour un constat de compréhension orale (il n'y a pas d'audio).
- « resource » : null, ou une seule ressource de la liste, choisie par son identifiant, avec ce \
qu'il faut y faire précisément, et seulement si elle correspond à l'épreuve et au niveau du \
constat.
- « progress_note » : l'évolution depuis le conseil précédent si le constat « progress » la \
donne (« trend »), dite avec des mots ; sinon null.
"""


class LLMError(Exception):
    """Appel impossible ou réponse inexploitable : message en français, pour le journal."""


@dataclass
class LLMResult:
    content: object  # JSON décodé (dict attendu), ou None si la réponse n'est pas du JSON
    model: str
    input_tokens: int
    output_tokens: int


class LLMProvider(Protocol):
    name: str

    def generate(self, model: str, system: str, user: str, schema: dict) -> LLMResult: ...


ERROR_KEYS = ["test_item_id", "section", "level", "support", "question", "options"]


def _error_for_model(error: dict) -> dict:
    data = {key: error[key] for key in ERROR_KEYS}
    data["chosen_index"] = error["chosen_index"]
    data["correct_index"] = error["correct_index"]
    data["explanation"] = error["explanation"]
    if error.get("language_point"):
        point = language_points().get(error["language_point"], {})
        data["language_point"] = error["language_point"]
        data["language_hint"] = point.get("hint", "")
    return data


def model_findings(findings: list[dict]) -> list[dict]:
    """Ce que reçoit le modèle pour chaque constat (12.3) : type, intensité, épreuve, niveaux,
    sous-type, compétence ou famille, questions manquées. Aucun taux, pourcentage ni écart
    chiffré ; seule exception, les repères de temps de `time_pressure`."""
    result = []
    for finding in findings:
        details = finding.get("details", {})
        if finding["type"] == "progress":
            result.append(
                {
                    "id": "progress",
                    "type": "progress",
                    "items": [
                        {"finding_id": e["finding_id"], "label": e["label"], "trend": e["trend"]}
                        for e in details.get("items", [])
                        if "trend" in e
                    ],
                }
            )
            continue
        data = {
            "id": finding["id"],
            "type": finding["type"],
            "intensity": finding["intensity"],
            "label": finding["label"],
            "section": finding["section"],
        }
        for key in ("levels", "subtype", "skill", "from_level", "to_level"):
            if details.get(key):
                data[key] = details[key]
        if finding["type"] == "msl_family":
            data["family"] = details["family"]
            data["family_label"] = FAMILY_LABELS[details["family"]]
        if finding["type"] == "time_pressure":
            data["unanswered_at_end"] = details["unanswered_at_end"]
            data["slow_start"] = details["slow_start"]
            data["time_targets_seconds"] = {
                section: {"average": t["average_seconds"], "by_level": t["by_level"]}
                for section, t in details.get("targets", {}).items()
            }
        data["missed_questions"] = [_error_for_model(e) for e in finding.get("errors", [])]
        result.append(data)
    return result


def user_message(findings: list[dict], context: dict) -> str:
    """Données envoyées au modèle : constats sans taux, erreurs jointes, ressources. Aucune
    donnée personnelle (ni e-mail, ni nom)."""
    payload = {
        "context": context,
        "findings": model_findings(findings),
        "resources": [
            {key: r[key] for key in ("id", "label", "for", "levels") if key in r}
            for r in resources()
        ],
    }
    return (
        "Voici les constats calculés à partir des réponses de la personne, avec les questions "
        "manquées et la liste des ressources autorisées. Rédigez les conseils.\n\n"
        + json.dumps(payload, ensure_ascii=False, indent=1)
    )


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str):
        if not api_key:
            raise LLMError("ANTHROPIC_API_KEY est vide : renseignez la clé dans .env.")
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=api_key, timeout=120.0, max_retries=1)

    def generate(self, model: str, system: str, user: str, schema: dict) -> LLMResult:
        anthropic = self._anthropic
        try:
            response = self.client.messages.create(
                model=model,
                max_tokens=MAX_TOKENS,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config={"format": {"type": "json_schema", "schema": schema}},
            )
        except anthropic.APIStatusError as exc:
            raise LLMError(f"API Claude : erreur {exc.status_code}.") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMError("API Claude injoignable.") from exc
        usage = response.usage
        result = LLMResult(None, response.model, usage.input_tokens, usage.output_tokens)
        if response.stop_reason in {"refusal", "max_tokens"}:
            return result  # tokens facturés, contenu inutilisable
        text = next((b.text for b in response.content if b.type == "text"), "")
        with contextlib.suppress(ValueError):
            result.content = json.loads(text)
        return result


class StaticProvider:
    """Fournisseur de test : renvoie toujours la même réponse, sans réseau."""

    name = "static"

    def __init__(self, content, input_tokens: int = 5000, output_tokens: int = 3000):
        self.content = content
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.calls = []

    def generate(self, model: str, system: str, user: str, schema: dict) -> LLMResult:
        self.calls.append({"model": model, "system": system, "user": user, "schema": schema})
        return LLMResult(self.content, model, self.input_tokens, self.output_tokens)


def get_provider() -> LLMProvider | None:
    """Le fournisseur actif, ou None avec `LLM_PROVIDER=fake` (modèles de secours)."""
    if settings.LLM_PROVIDER == "fake":
        return None
    if settings.LLM_PROVIDER == "anthropic":
        return AnthropicProvider(settings.ANTHROPIC_API_KEY)
    raise LLMError(f"LLM_PROVIDER inconnu : {settings.LLM_PROVIDER} (fake ou anthropic).")
