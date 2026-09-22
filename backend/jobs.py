"""Tâches d'analyse en arrière-plan, suivies par l'interface via polling."""
import threading
import time
import traceback
import uuid

from . import compare, rotation
from .wcl import CLASS_FR, SPEC_FR, WCLError, actor_names, boss_ids, split_icon

_jobs: dict[str, dict] = {}
_lock = threading.Lock()
JOB_TTL_S = 3600


def get(job_id: str) -> dict | None:
    with _lock:
        job = _jobs.get(job_id)
        return dict(job) if job else None


def _update(job_id: str, **fields) -> None:
    with _lock:
        _jobs[job_id].update(fields)


def start(wcl, sides: list[dict], gap_threshold_ms: int) -> str:
    job_id = uuid.uuid4().hex
    with _lock:
        now = time.time()
        for k in [k for k, j in _jobs.items() if now - j["created"] > JOB_TTL_S]:
            del _jobs[k]
        _jobs[job_id] = {"status": "running", "progress": 0.0, "message": "Démarrage…",
                         "result": None, "error": None, "created": now}
    threading.Thread(target=_run, args=(job_id, wcl, sides, gap_threshold_ms), daemon=True).start()
    return job_id


def _load_side(job_id, wcl, side, label, base, span, gap_threshold_ms):
    data = wcl.fights(side["code"])
    fight = next((f for f in data.get("fights") or [] if f["id"] == side["fight"]), None)
    if not fight:
        raise WCLError(f"Combat {side['fight']} introuvable dans le rapport {side['code']}.", 404)
    player = next((p for p in data.get("friendlies") or [] if p["id"] == side["player"]), None)
    if not player:
        raise WCLError(f"Joueur introuvable dans le rapport {side['code']}.", 404)

    start_t, end_t = fight["start_time"], fight["end_time"]

    def progress(frac):
        _update(job_id, progress=base + span * 0.9 * frac,
                message=f"Joueur {label} ({player['name']}) : récupération des events… {int(frac * 100)} %")

    progress(0)
    events, truncated = wcl.events(side["code"], start_t, end_t, player["id"], progress)

    _update(job_id, message=f"Joueur {label} : dégâts des familiers…")
    table = wcl.damage_done_table(side["code"], start_t, end_t)
    entry = next((e for e in table.get("entries") or [] if e.get("id") == player["id"]), None)

    _update(job_id, progress=base + span * 0.95, message=f"Joueur {label} : normalisation de la rotation…")
    metrics = rotation.analyze_player(
        events, {"start": start_t, "end": end_t}, player["id"], entry,
        actor_names(data), boss_ids(data, fight["id"]), gap_threshold_ms,
    )
    cls, spec = split_icon(player.get("icon", ""), player.get("type", ""))
    meta = {
        "player": player["name"],
        "spec_label": f"{CLASS_FR.get(cls, cls)} - {SPEC_FR.get(spec, spec)}",
        "fight_name": fight.get("name"),
        "keystone_level": fight.get("keystoneLevel"),
        "timed": fight.get("kill"),
        "duration_s": round((end_t - start_t) / 1000),
        "report": side["code"],
        "fight_id": fight["id"],
        "events": len(events),
        "truncated": truncated,
    }
    return metrics, meta


def _run(job_id, wcl, sides, gap_threshold_ms):
    try:
        ma, meta_a = _load_side(job_id, wcl, sides[0], "A", 0.0, 0.48, gap_threshold_ms)
        mb, meta_b = _load_side(job_id, wcl, sides[1], "B", 0.48, 0.48, gap_threshold_ms)
        _update(job_id, progress=0.97, message="Comparaison des deux joueurs…")
        diff = compare.build_diff(ma, mb, meta_a, meta_b)
        _update(job_id, status="done", progress=1.0, message="Terminé",
                result={"diff": diff, "wcl_calls_last_hour": wcl.calls_last_hour()})
    except WCLError as exc:
        _update(job_id, status="error", error=str(exc))
    except Exception as exc:  # noqa: BLE001 - on veut toujours un message à l'écran
        traceback.print_exc()
        _update(job_id, status="error", error=f"Erreur interne pendant l'analyse : {exc}")
