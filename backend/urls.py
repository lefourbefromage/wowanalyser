"""Analyse des liens Warcraft Logs."""
import re
from urllib.parse import parse_qs, urlparse

REPORT_RE = re.compile(r"/reports/([A-Za-z0-9]{16})(?:[/?#]|$)")


class InvalidReportUrl(ValueError):
    pass


def parse_report_url(url: str) -> tuple[str, str | None]:
    """Renvoie (code_du_rapport, fight) ; fight vaut None, "last" ou un id numérique (str).

    Accepte `?fight=4` comme `#fight=4` (Warcraft Logs utilise les deux).
    """
    url = (url or "").strip()
    if not url:
        raise InvalidReportUrl("Le lien est vide.")
    if "://" not in url:
        url = "https://" + url

    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if not host.endswith("warcraftlogs.com"):
        raise InvalidReportUrl("Ce lien ne pointe pas vers warcraftlogs.com.")

    match = REPORT_RE.search(parsed.path + "/")
    if not match:
        raise InvalidReportUrl(
            "Code de rapport introuvable : le lien doit ressembler à "
            "https://fr.warcraftlogs.com/reports/XXXXXXXXXXXXXXXX?fight=4"
        )
    code = match.group(1)

    params = parse_qs(parsed.query)
    params.update({k: v for k, v in parse_qs(parsed.fragment).items() if k not in params})
    fight = (params.get("fight") or [None])[0]
    if fight is not None:
        fight = fight.strip().lower()
        if fight != "last" and not fight.isdigit():
            raise InvalidReportUrl(f"Paramètre fight invalide : « {fight} » (attendu : un nombre ou « last »).")
    return code, fight
