"""Antécédents de la stratégie : son journal de fantôme dans le labo d'origine (20/10 → 28/10/2026).

Décision d'Etienne (29/10/2026) : « mets les mêmes conditions que sur l'ancien fantôme, comme si c'était un fantôme et
que tu reprenais la suite juste après son passé dans l'autre labo ». Le journal d'origine (archives/labo_origine/) est
signé avec la clé de l'autre labo : il ne s'importe pas dans le journal signé d'ici. Il est relu comme ANTÉCÉDENTS :
- ses paris terminés comptent dans les indices de confiance et dans la santé de la stratégie (mise prudente, renfort,
  suspension : sante.py), comme si le fantôme continuait ;
- sa courbe de capital précède celle d'ici sur le tableau de bord, une fois le capital principal repris à sa valeur
  (workflow reset-capital, voir docs/CONTEXTE.md §6).
Garde-fou : la chaîne de hash est revérifiée (chaque ligne porte le hash de la précédente) ; la signature ne peut pas
l'être sans la clé de l'autre labo. Un fichier absent, illisible ou à la chaîne cassée donne « aucun antécédent ».
"""

from __future__ import annotations

import gzip
import json
import logging
from pathlib import Path

from polymarket_agent.ledger import GENESIS_PREV, entry_hash
from polymarket_agent.reporting import closed_trades

log = logging.getLogger(__name__)


def load(path: Path | None) -> list[dict]:
    """Lignes du journal d'origine, dans l'ordre, ou [] s'il est absent ou altéré."""
    if path is None or not Path(path).exists():
        return []
    try:
        opener = gzip.open if str(path).endswith(".gz") else open
        with opener(path, "rt", encoding="utf-8") as handle:
            rows = [json.loads(line) for line in handle if line.strip()]
    except (OSError, ValueError) as exc:
        log.warning("antécédents illisibles (%s) : ignorés", exc)
        return []
    prev = GENESIS_PREV
    for number, row in enumerate(rows):
        try:
            ok = (row["seq"] == number and row["prev"] == prev
                  and row["hash"] == entry_hash(row["seq"], row["ts"], row["type"], row["data"], prev))
        except (KeyError, TypeError):
            ok = False
        if not ok or (number == 0 and row.get("type") != "GENESIS"):
            log.warning("antécédents : chaîne de hash cassée à la ligne %d, ignorés", number + 1)
            return []
        prev = row["hash"]
    return rows


def since_last_start(entries: list[dict]) -> list[dict]:
    """Lignes de l'époque en cours (depuis le dernier GENESIS ou RESET) : un journal remis à zéro repart de rien."""
    start = max((i for i, e in enumerate(entries) if e.get("type") in ("GENESIS", "RESET")), default=0)
    return entries[start:]


def trades(history: list[dict], live: list[dict]) -> list[dict]:
    """Paris terminés des antécédents puis du journal d'ici (époque en cours), dans l'ordre. Une position encore
    ouverte à la fin des antécédents n'est jamais terminée ici (elle a été comptée à sa valeur de revente)."""
    return closed_trades(history) + closed_trades(since_last_start(live))


def resumed(history: list[dict], live: list[dict]) -> bool:
    """Vrai si le capital principal a repris la suite : une remise à zéro (RESET) postérieure aux antécédents."""
    if not history:
        return False
    end = history[-1]["ts"]
    return any(e.get("type") == "RESET" and e["ts"] >= end for e in live)
