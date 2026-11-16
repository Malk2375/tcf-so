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

MAX_TOKENS = 16000

SYSTEM_PROMPT = """\
Vous rédigez des conseils personnalisés pour une personne qui prépare le TCF tout public \
(test de connaissance du français). Votre but : l'aider à corriger ses erreurs, pas seulement \
les lui signaler.

Règles :
- Écrivez en français, en vouvoyant la personne, sur un ton encourageant et direct.
- Ne citez que les chiffres fournis dans les données ; n'en inventez aucun.
- Un conseil par constat fourni (sauf « progress », qui sert seulement aux « progress_note »), \
dans l'ordre des constats ; « finding_id » reprend exactement l'identifiant du constat.
- Chaque conseil doit permettre d'agir sans autre recherche :
  - « diagnostic » : ce que montrent les réponses, avec les chiffres fournis ;
  - « lesson » : la règle ou la méthode, de 80 à 150 mots, avec 2 exemples ;
  - « error_reviews » : pour chaque question manquée fournie avec le constat, pourquoi l'option \
choisie ne convient pas et l'indice qui menait à la bonne réponse ; « test_item_id » reprend \
l'identifiant fourni. Pour « time_pressure » et « answer_changes », cette liste peut être vide ;
  - « exam_strategy » : quoi faire pendant l'épreuve face à ce type de question ;
  - « practice » : exactement 3 questions d'entraînement originales, de même type et de même \
niveau que les erreurs, chacune avec exactement 4 options différentes, l'indice (0 à 3) de la \
bonne réponse et une explication ;
  - « week_plan » : 2 ou 3 actions concrètes, avec une durée en minutes et une fréquence ; \
« resource_id » est l'identifiant d'une ressource de la liste fournie, ou null. N'inventez \
jamais de ressource ni d'adresse web ;
  - « progress_note » : l'évolution depuis le conseil précédent si le constat « progress » la \
donne, sinon null.
- Pour « time_pressure », la leçon est une méthode de gestion du temps qui s'appuie sur les \
repères chiffrés fournis (temps cible par question et par niveau).
- « summary » : 2 phrases, où en est la personne et par quoi commencer. « next_goal » : un \
objectif mesurable pour le prochain test.
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


def user_message(findings: list[dict], context: dict) -> str:
    """Données envoyées au modèle : constats, erreurs jointes, ressources. Aucune donnée
    personnelle (ni e-mail, ni nom)."""
    payload = {
        "context": context,
        "findings": [{k: v for k, v in f.items() if k != "filter"} for f in findings],
        "resources": [{"id": r["id"], "label": r["label"], "for": r["for"]} for r in resources()],
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
