"""WoW Analyser : comparaison de rotations DPS à partir de deux liens Warcraft Logs."""
from flask import Flask, jsonify, render_template, request

from backend import claude, config, jobs
from backend.urls import InvalidReportUrl, parse_report_url
from backend.wcl import WCLClient, WCLError, summarize_report

config.load_config()

app = Flask(__name__, template_folder="frontend/templates", static_folder="frontend/static")
wcl = WCLClient(config.wcl_api_key(), config.wcl_hourly_budget())


def error(message, status=400, code="error"):
    return jsonify({"error": message, "code": code}), status


def json_body():
    # Exiger du JSON bloque les POST envoyés par une autre page web vers ce serveur local.
    if not request.is_json:
        return None
    return request.get_json(silent=True) or {}


@app.before_request
def local_only():
    if request.remote_addr not in ("127.0.0.1", "::1"):
        return error("Cette application n'est accessible qu'en local.", 403)


@app.get("/")
def index():
    return render_template("index.html")


@app.get("/api/config")
def get_config():
    return jsonify({
        "wcl_configured": bool(config.wcl_api_key()),
        "anthropic_configured": bool(config.anthropic_api_key()),
        "model": config.anthropic_model(),
        "wcl_calls_last_hour": wcl.calls_last_hour(),
        "wcl_hourly_budget": wcl.hourly_budget,
    })


@app.post("/api/config/anthropic")
def set_anthropic_key():
    body = json_body()
    if body is None:
        return error("Requête invalide.")
    key = str(body.get("api_key", "")).strip()
    if not key.startswith("sk-ant-"):
        return error("Cette clé ne ressemble pas à une clé Anthropic (elle doit commencer par « sk-ant- »).")
    try:
        claude.validate_key(key)
    except claude.ClaudeError as exc:
        if exc.code in ("invalid_key", "bad_model"):
            return error(str(exc), exc.status, exc.code)
        # Problème réseau ou surcharge : on enregistre quand même, la clé sera testée à l'usage.
        config.save_env_value("ANTHROPIC_API_KEY", key)
        return jsonify({"ok": True, "warning": f"Clé enregistrée sans vérification : {exc}"})
    config.save_env_value("ANTHROPIC_API_KEY", key)
    return jsonify({"ok": True})


@app.post("/api/report")
def load_report():
    body = json_body()
    if body is None:
        return error("Requête invalide.")
    try:
        code, fight = parse_report_url(body.get("url", ""))
        return jsonify(summarize_report(code, wcl.fights(code), fight))
    except InvalidReportUrl as exc:
        return error(str(exc), 400, "invalid_url")
    except WCLError as exc:
        return error(str(exc), exc.status, "wcl")


@app.post("/api/analyze")
def analyze():
    body = json_body()
    if body is None:
        return error("Requête invalide.")
    try:
        sides = [
            {"code": str(s["code"]), "fight": int(s["fight"]), "player": int(s["player"])}
            for s in (body["a"], body["b"])
        ]
        threshold = float(body.get("gap_threshold_s", 1.5))
    except (KeyError, TypeError, ValueError):
        return error("Paramètres d'analyse incomplets : choisis un combat et un joueur pour chaque rapport.")
    if not 0.5 <= threshold <= 10:
        return error("Le seuil de temps mort doit être compris entre 0,5 et 10 secondes.")
    return jsonify({"job": jobs.start(wcl, sides, int(threshold * 1000))})


@app.get("/api/jobs/<job_id>")
def job_status(job_id):
    job = jobs.get(job_id)
    if not job:
        return error("Analyse introuvable (serveur redémarré ?). Relance l'analyse.", 404)
    job.pop("created", None)
    return jsonify(job)


@app.post("/api/ai")
def ai_analysis():
    body = json_body()
    if body is None or not isinstance(body.get("diff"), dict):
        return error("Requête invalide.")
    try:
        return jsonify(claude.analyze(body["diff"]))
    except claude.ClaudeError as exc:
        return error(str(exc), exc.status, exc.code)


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000, debug=False)
