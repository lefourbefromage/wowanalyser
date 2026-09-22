"""Analyse de la comparaison par Claude (API Anthropic)."""
import json

import anthropic

from . import config

SYSTEM_PROMPT = """Tu es un coach expert de World of Warcraft (retail), spécialiste de l'optimisation des rotations DPS en Mythique+.
On te donne une comparaison chiffrée, déjà calculée, entre deux joueurs de la même spécialisation sur le même donjon Dans les données, les deux joueurs sont désignés par les clés « A » et « B » ; leurs noms sont donnés dans context.A.player et context.B.player. Tu n'as pas les logs bruts, seulement ce résumé.

Réponds en français, au format Markdown, avec ces sections :
1. **Résumé** : en 3-4 phrases, d'où vient l'essentiel de l'écart de DPS.
2. **Erreurs de rotation probables, par ordre d'impact** : pour chacune, cite les chiffres qui la prouvent pour chacun des deux joueurs, explique pourquoi ça coûte des dégâts et estime l'impact (élevé / moyen / faible).
3. **Conseils concrets pour le joueur le moins performant** : des actions précises et applicables dès la prochaine clé (quel sort utiliser plus ou moins, quel debuff surveiller, quand dépenser la ressource, quoi corriger dans l'ouverture sur les boss...).
4. **Limites de l'analyse** : ce que ces chiffres ne permettent pas de conclure.

Règles :
- Désigne toujours les joueurs par leur nom, jamais par « A », « B », « joueur A » ou « joueur B ».
- Concentre-toi sur les différences de rotation ; mentionne le stuff (niveau d'objet) ou les morts seulement s'ils expliquent une part notable de l'écart.
- Les noms de sorts sont en anglais dans les données : garde le nom anglais et ajoute le nom français entre parenthèses seulement si tu en es sûr.
- Le « temps mort » compte les trous entre deux casts supérieurs au seuil pendant le combat. Après un sort canalisé ou pendant une phase d'immunité ou de déplacement imposée, un trou peut être normal. Il peut aussi venir d'une attente de ressource (énergie, rage...) : croise-le avec le gaspillage et le % de casts à ressource pleine. Sers-toi du détail « after_spell » pour faire la part des choses.
- Les écarts de fréquence de sorts peuvent venir de talents différents ou de routes différentes dans le donjon : signale-le quand c'est plausible, sans l'affirmer.
- Si ta connaissance d'un talent ou d'un sort récent est incertaine, dis-le plutôt que d'inventer.
- Sois direct et concret, pas de remplissage."""


class ClaudeError(Exception):
    """Erreur affichable ; `code` permet à l'interface de réagir (ex. afficher l'écran de configuration)."""

    def __init__(self, message: str, code: str = "error", status: int = 502):
        super().__init__(message)
        self.code = code
        self.status = status


def _client(api_key: str | None = None) -> anthropic.Anthropic:
    key = api_key or config.anthropic_api_key()
    if not key:
        raise ClaudeError("Aucune clé API Anthropic configurée.", "missing_key", 400)
    return anthropic.Anthropic(api_key=key, timeout=180.0, max_retries=2)


def _translate_error(exc: Exception) -> ClaudeError:
    if isinstance(exc, anthropic.AuthenticationError):
        return ClaudeError("Clé API Anthropic invalide ou révoquée.", "invalid_key", 401)
    if isinstance(exc, anthropic.PermissionDeniedError):
        return ClaudeError("Cette clé API Anthropic n'a pas accès à ce modèle ou à cette organisation.", "invalid_key", 403)
    if isinstance(exc, anthropic.NotFoundError):
        return ClaudeError(
            f"Modèle « {config.anthropic_model()} » introuvable. Vérifie ANTHROPIC_MODEL dans le fichier .env.",
            "bad_model", 400,
        )
    if isinstance(exc, anthropic.RateLimitError):
        retry = exc.response.headers.get("retry-after") if exc.response is not None else None
        wait = f" Réessaie dans {retry} secondes." if retry else " Réessaie dans une minute."
        return ClaudeError("Limite de débit de l'API Anthropic atteinte." + wait, "rate_limit", 429)
    if isinstance(exc, anthropic.BadRequestError):
        msg = str(getattr(exc, "message", exc))
        if "credit" in msg.lower():
            return ClaudeError(
                "Crédits Anthropic insuffisants : ajoute des crédits sur console.anthropic.com (Settings > Billing).",
                "no_credit", 402,
            )
        return ClaudeError(f"Requête refusée par l'API Anthropic : {msg}", "bad_request", 400)
    if isinstance(exc, anthropic.APIStatusError):
        if exc.status_code == 529 or exc.status_code >= 500:
            return ClaudeError("L'API Anthropic est surchargée ou indisponible. Réessaie dans quelques minutes.", "overloaded", 503)
        return ClaudeError(f"Erreur de l'API Anthropic (HTTP {exc.status_code}).", "error", 502)
    if isinstance(exc, anthropic.APITimeoutError):
        return ClaudeError("L'API Anthropic a mis trop de temps à répondre. Réessaie.", "timeout", 504)
    if isinstance(exc, anthropic.APIConnectionError):
        return ClaudeError("Impossible de joindre l'API Anthropic. Vérifie ta connexion internet.", "network", 502)
    return ClaudeError(f"Erreur inattendue : {exc}", "error", 500)


def validate_key(api_key: str) -> None:
    """Vérifie la clé en interrogeant le modèle configuré (appel gratuit)."""
    try:
        _client(api_key).models.retrieve(config.anthropic_model())
    except anthropic.APIError as exc:
        raise _translate_error(exc)


def _user_message(diff: dict) -> str:
    payload = json.dumps(diff, ensure_ascii=False, separators=(",", ":"))
    ctx = diff.get("context") or {}
    name_a = (ctx.get("A") or {}).get("player", "A")
    name_b = (ctx.get("B") or {}).get("player", "B")
    weaker = name_a if ctx.get("weaker_player") == "A" else name_b
    return (
        f"Dans les données, « A » = {name_a} et « B » = {name_b}. "
        f"Le joueur le moins performant est {weaker}. "
        "Dans ta réponse, utilise uniquement leurs noms.\n\n"
        "Voici la comparaison chiffrée des deux joueurs (JSON) :\n\n" + payload
    )


def manual_prompt(diff: dict) -> str:
    """Texte à coller tel quel dans une conversation claude.ai (sans clé API)."""
    return SYSTEM_PROMPT + "\n\n---\n\n" + _user_message(diff)


def analyze(diff: dict) -> dict:
    client = _client()
    try:
        response = client.messages.create(
            model=config.anthropic_model(),
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            output_config={"effort": "medium"},
            messages=[{"role": "user", "content": _user_message(diff)}],
        )
    except anthropic.APIError as exc:
        raise _translate_error(exc)

    if response.stop_reason == "refusal":
        raise ClaudeError("Claude a refusé de traiter cette demande.", "refusal", 502)

    text = "".join(b.text for b in response.content if b.type == "text").strip()
    if response.stop_reason == "max_tokens":
        text += "\n\n*(Réponse tronquée : limite de longueur atteinte.)*"
    return {
        "markdown": text,
        "model": response.model,
        "usage": {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens},
    }
