"""Génère le tableau de bord (site/index.html et site/data.json) depuis les journaux vérifiés.

Le cycle le régénère automatiquement (dashboard.every_minutes) ; ce script force une génération.
Usage : python scripts/build_dashboard.py [--dev-key] [--out site] [--fragment fichier.html]
  --fragment : écrit aussi le contenu seul (sans <html>/<head>), pour un aperçu en ligne.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from _common import ROOT, get_key, load_settings, resolve

from polymarket_agent import dashboard
from polymarket_agent.persistence import Database


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev-key", action="store_true")
    parser.add_argument("--out", help="dossier de sortie (défaut : réglage dashboard.output_dir)")
    parser.add_argument("--fragment", help="écrit aussi le contenu seul dans ce fichier")
    args = parser.parse_args()
    settings = load_settings()
    key = get_key(args.dev_key)
    db = Database(resolve(ROOT, settings["paths"]["database"]))
    try:
        data = dashboard.collect(dashboard.open_ledger(settings, ROOT, key), db, settings, ROOT)
    finally:
        db.close()
    out = Path(args.out) if args.out else resolve(ROOT, settings["dashboard"]["output_dir"])
    page = dashboard.write_site(data, out)
    print(f"Tableau de bord : {page}")
    if args.fragment:
        Path(args.fragment).write_text(dashboard.render(data, full_document=False), encoding="utf-8")
        print(f"Contenu seul : {args.fragment}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
