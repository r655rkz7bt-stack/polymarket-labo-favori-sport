"""Outils partagés par les scripts : chemins, clé du journal, journaux (logs)."""

from __future__ import annotations

import logging
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from polymarket_agent.config import load_settings, resolve  # noqa: E402
from polymarket_agent.ledger import MIN_KEY_BYTES, load_key_from_env  # noqa: E402

DEV_KEY_PATH = ROOT / "state" / "dev_ledger_key"


def get_key(dev_key: bool) -> bytes:
    """LEDGER_KEY vient de l'environnement. --dev-key : clé locale de développement.

    La clé de développement est stockée dans state/dev_ledger_key (ignoré par Git)
    et n'est JAMAIS utilisée en production (GitHub fournit LEDGER_KEY).
    """
    if os.environ.get("LEDGER_KEY"):
        return load_key_from_env()
    if not dev_key:
        raise SystemExit("LEDGER_KEY absente. En local, ajoute l'option --dev-key.")
    if not DEV_KEY_PATH.exists():
        DEV_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
        DEV_KEY_PATH.write_text(secrets.token_hex(MIN_KEY_BYTES), encoding="utf-8")
        DEV_KEY_PATH.chmod(0o600)
        print("Clé de développement créée dans state/dev_ledger_key (ignorée par Git).")
    return DEV_KEY_PATH.read_text(encoding="utf-8").strip().encode("utf-8")


def setup_logging(settings: dict) -> None:
    log_dir = resolve(ROOT, settings["paths"]["logs"])
    log_dir.mkdir(parents=True, exist_ok=True)
    fmt = "%(asctime)s %(levelname)s %(name)s : %(message)s"
    logging.basicConfig(level=logging.INFO, format=fmt, handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(log_dir / "cycle.log", encoding="utf-8"),
    ])
