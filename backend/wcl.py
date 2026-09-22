"""Client de l'API REST v1 de Warcraft Logs (espacement des appels, 429, pagination, cache)."""
import math
import threading
import time
from collections import deque

import requests

BASE_URL = "https://www.warcraftlogs.com/v1"
MIN_INTERVAL_S = 0.25        # espacement minimal entre deux appels
MAX_EVENT_PAGES = 200        # sécurité anti-boucle infinie sur la pagination
CACHE_TTL_S = 30 * 60

CLASS_FR = {
    "DeathKnight": "Chevalier de la mort", "DemonHunter": "Chasseur de démons", "Druid": "Druide",
    "Evoker": "Évocateur", "Hunter": "Chasseur", "Mage": "Mage", "Monk": "Moine", "Paladin": "Paladin",
    "Priest": "Prêtre", "Rogue": "Voleur", "Shaman": "Chaman", "Warlock": "Démoniste", "Warrior": "Guerrier",
}
SPEC_FR = {
    "Blood": "Sang", "Frost": "Givre", "Unholy": "Impie", "Havoc": "Dévastation", "Vengeance": "Vengeance",
    "Devourer": "Dévoreur", "Balance": "Équilibre", "Feral": "Farouche", "Guardian": "Gardien",
    "Restoration": "Restauration", "Devastation": "Dévastation", "Preservation": "Préservation",
    "Augmentation": "Augmentation", "BeastMastery": "Maîtrise des bêtes", "Marksmanship": "Précision",
    "Survival": "Survie", "Arcane": "Arcanes", "Fire": "Feu", "Brewmaster": "Maître brasseur",
    "Mistweaver": "Tisse-brume", "Windwalker": "Marche-vent", "Holy": "Sacré", "Protection": "Protection",
    "Retribution": "Vindicte", "Discipline": "Discipline", "Shadow": "Ombre", "Assassination": "Assassinat",
    "Outlaw": "Hors-la-loi", "Subtlety": "Finesse", "Elemental": "Élémentaire", "Enhancement": "Amélioration",
    "Affliction": "Affliction", "Demonology": "Démonologie", "Destruction": "Destruction", "Arms": "Armes",
    "Fury": "Fureur",
}


class WCLError(Exception):
    """Erreur destinée à être affichée telle quelle à l'utilisateur."""

    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


class WCLRateLimitError(WCLError):
    def __init__(self, message: str):
        super().__init__(message, status=429)


class WCLClient:
    def __init__(self, api_key: str, hourly_budget: int = 3600):
        self.api_key = api_key
        self.hourly_budget = hourly_budget
        self.session = requests.Session()
        self._lock = threading.Lock()
        self._last_call = 0.0
        self._calls: deque[float] = deque()   # horodatage des appels de la dernière heure
        self._cache: dict = {}

    # ------------------------------------------------------------------ bas niveau
    def calls_last_hour(self) -> int:
        with self._lock:
            self._prune()
            return len(self._calls)

    def _prune(self) -> None:
        cutoff = time.time() - 3600
        while self._calls and self._calls[0] < cutoff:
            self._calls.popleft()

    def _minutes_until_slot(self) -> int:
        if not self._calls:
            return 60
        return max(1, math.ceil((self._calls[0] + 3600 - time.time()) / 60))

    def _get(self, path: str, params: dict) -> dict:
        if not self.api_key:
            raise WCLError("Clé API Warcraft Logs manquante : renseigne WCL_API_KEY dans le fichier .env.", 500)

        with self._lock:
            self._prune()
            if len(self._calls) >= self.hourly_budget:
                raise WCLRateLimitError(
                    f"Limite API Warcraft Logs atteinte, réessaie dans {self._minutes_until_slot()} minutes."
                )
            wait = MIN_INTERVAL_S - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.time()
            self._calls.append(self._last_call)

        try:
            resp = self.session.get(
                BASE_URL + path, params={**params, "api_key": self.api_key}, timeout=45
            )
        except requests.Timeout:
            raise WCLError("Warcraft Logs ne répond pas (délai dépassé). Réessaie dans un instant.", 504)
        except requests.RequestException:
            raise WCLError("Impossible de joindre Warcraft Logs. Vérifie ta connexion internet.", 502)

        if resp.status_code == 200:
            try:
                return resp.json()
            except ValueError:
                raise WCLError("Réponse illisible de Warcraft Logs.", 502)

        detail = ""
        try:
            detail = str(resp.json().get("error", ""))
        except ValueError:
            pass
        low = detail.lower()

        if resp.status_code == 429:
            retry = resp.headers.get("Retry-After")
            minutes = math.ceil(int(retry) / 60) if retry and retry.isdigit() else self._minutes_until_slot()
            raise WCLRateLimitError(f"Limite API Warcraft Logs atteinte, réessaie dans {max(1, minutes)} minutes.")
        if "private" in low or "does not exist" in low or resp.status_code == 404:
            raise WCLError("Rapport introuvable ou privé. Vérifie le lien et que le rapport est public ou non listé.", 404)
        if resp.status_code in (401, 403) or "api key" in low:
            raise WCLError("Clé API Warcraft Logs refusée : vérifie WCL_API_KEY dans le fichier .env.", 401)
        if resp.status_code >= 500:
            raise WCLError(f"Warcraft Logs rencontre un problème (HTTP {resp.status_code}). Réessaie plus tard.", 502)
        raise WCLError(f"Erreur Warcraft Logs (HTTP {resp.status_code}) : {detail or 'requête refusée'}.", 502)

    def _cached(self, key, loader):
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL_S:
            return hit[1]
        value = loader()
        self._cache[key] = (time.time(), value)
        return value

    # ------------------------------------------------------------------ endpoints
    def fights(self, code: str) -> dict:
        return self._cached(
            ("fights", code), lambda: self._get(f"/report/fights/{code}", {"translate": "true"})
        )

    def damage_done_table(self, code: str, start: int, end: int) -> dict:
        return self._cached(
            ("damage", code, start, end),
            lambda: self._get(
                f"/report/tables/damage-done/{code}", {"start": start, "end": end, "translate": "true"}
            ),
        )

    def events(self, code: str, start: int, end: int, source_id: int, on_progress=None) -> tuple[list, bool]:
        """Tous les events d'une source sur [start, end], pagination incluse.

        Renvoie (events, tronqué) ; tronqué vaut True si la limite de pages a été atteinte.
        """
        key = ("events", code, start, end, source_id)
        hit = self._cache.get(key)
        if hit and time.time() - hit[0] < CACHE_TTL_S:
            if on_progress:
                on_progress(1.0)
            return hit[1]

        events: list = []
        cursor = start
        truncated = True
        for _ in range(MAX_EVENT_PAGES):
            data = self._get(
                f"/report/events/{code}",
                {"start": cursor, "end": end, "sourceid": source_id, "translate": "true"},
            )
            events.extend(data.get("events") or [])
            nxt = data.get("nextPageTimestamp")
            if on_progress:
                on_progress(min(1.0, ((nxt or end) - start) / max(1, end - start)))
            if not nxt or nxt >= end or nxt <= cursor:
                truncated = False
                break
            cursor = nxt

        result = (events, truncated)
        self._cache[key] = (time.time(), result)
        return result


# ---------------------------------------------------------------------- mise en forme
def _fmt_duration(ms: int) -> str:
    s = max(0, ms) // 1000
    return f"{s // 60}:{s % 60:02d}"


def split_icon(icon: str, class_type: str) -> tuple[str, str]:
    parts = (icon or "").split("-", 1)
    cls = parts[0] or class_type
    spec = parts[1] if len(parts) > 1 else ""
    return cls, spec


def summarize_report(code: str, data: dict, requested_fight: str | None) -> dict:
    """Transforme la réponse /report/fights en structure pour l'interface."""
    fights = [f for f in (data.get("fights") or []) if f.get("end_time", 0) > f.get("start_time", 0)]
    if not fights:
        raise WCLError("Ce rapport ne contient aucun combat.", 404)

    if requested_fight in (None, "last"):
        selected = fights[-1]["id"]
    else:
        fid = int(requested_fight)
        if not any(f["id"] == fid for f in fights):
            raise WCLError(f"Le combat {fid} n'existe pas dans ce rapport ({len(fights)} combats disponibles).", 404)
        selected = fid

    fight_list = []
    for f in fights:
        dur = f["end_time"] - f["start_time"]
        level = f.get("keystoneLevel")
        label = f"#{f['id']} · {f.get('name', '?')}"
        if level:
            label += f" +{level}"
        label += f" · {_fmt_duration(dur)}"
        if level:
            label += " · réussie" if f.get("kill") else " · non timée"
        elif f.get("boss"):
            label += " · kill" if f.get("kill") else " · wipe"
        fight_list.append({
            "id": f["id"], "name": f.get("name"), "label": label, "keystoneLevel": level,
            "start": f["start_time"], "end": f["end_time"], "duration": dur, "kill": f.get("kill"),
        })

    players = []
    for p in data.get("friendlies") or []:
        if p.get("type") in ("Pet", "NPC", None):
            continue
        cls, spec = split_icon(p.get("icon", ""), p.get("type", ""))
        if cls not in CLASS_FR:
            continue
        players.append({
            "id": p["id"], "name": p.get("name"), "class": cls, "spec": spec,
            "specKey": f"{cls}-{spec}",
            "label": f"{p.get('name')} ({CLASS_FR.get(cls, cls)} - {SPEC_FR.get(spec, spec or '?')})",
            "fights": [x["id"] for x in p.get("fights") or []],
        })

    return {
        "code": code, "title": data.get("title"), "fights": fight_list,
        "selectedFight": selected, "players": players,
    }


def actor_names(data: dict) -> dict:
    """id d'acteur -> {name, type} pour résoudre les cibles des events."""
    names = {}
    for group in ("friendlies", "enemies", "friendlyPets", "enemyPets"):
        for a in data.get(group) or []:
            names[a["id"]] = {"name": a.get("name"), "type": a.get("type")}
    return names


def boss_ids(data: dict, fight_id: int) -> set:
    return {
        e["id"] for e in data.get("enemies") or []
        if e.get("type") == "Boss" and any(x.get("id") == fight_id for x in e.get("fights") or [])
    }
