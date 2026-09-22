"""Lecture et écriture de la configuration locale (.env)."""
import os
from pathlib import Path

from dotenv import load_dotenv, set_key

ROOT_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = ROOT_DIR / ".env"

DEFAULT_MODEL = "claude-sonnet-5"


def load_config() -> None:
    """Charge le .env dans os.environ (sans écraser les variables déjà définies)."""
    load_dotenv(ENV_PATH)


def wcl_api_key() -> str:
    return os.environ.get("WCL_API_KEY", "").strip()


def anthropic_api_key() -> str:
    return os.environ.get("ANTHROPIC_API_KEY", "").strip()


def anthropic_model() -> str:
    return os.environ.get("ANTHROPIC_MODEL", "").strip() or DEFAULT_MODEL


def wcl_hourly_budget() -> int:
    try:
        return int(os.environ.get("WCL_HOURLY_BUDGET", "") or 3600)
    except ValueError:
        return 3600


def save_env_value(name: str, value: str) -> None:
    """Écrit une variable dans le .env local et la rend active immédiatement."""
    if not ENV_PATH.exists():
        ENV_PATH.touch()
    set_key(str(ENV_PATH), name, value, quote_mode="never")
    os.environ[name] = value
