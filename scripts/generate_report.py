"""Génère le rapport (reports/latest.md et reports/latest.json) depuis le journal vérifié.

Usage : python scripts/generate_report.py [--dev-key]
"""

from __future__ import annotations

import argparse
from decimal import Decimal

from _common import ROOT, get_key, load_settings, resolve

from polymarket_agent.ledger import Ledger
from polymarket_agent.persistence import Database
from polymarket_agent.reporting import build_report, write_report


def latest_marks(db, tokens) -> dict[str, Decimal]:
    """Prix de marque = dernier meilleur bid observé (valeur de revente prudente)."""
    marks = {}
    for token in tokens:
        row = db.conn.execute("SELECT best_bid FROM book_snapshots WHERE token_id=? "
                              "ORDER BY collected_at DESC LIMIT 1", (token,)).fetchone()
        if row and row[0] is not None:
            marks[token] = Decimal(row[0])
    return marks


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dev-key", action="store_true")
    args = parser.parse_args()
    settings = load_settings()
    paths = settings["paths"]
    ledger = Ledger.open(resolve(ROOT, paths["ledger"]), get_key(args.dev_key),
                         resolve(ROOT, paths["bankruptcy_lock"]))
    db = Database(resolve(ROOT, paths["database"]))
    outputs = []
    try:
        report = build_report(ledger, db, latest_marks(db, ledger.state.positions))
        outputs.append(write_report(report, ROOT / "reports"))
    finally:
        db.close()
    for path in outputs:
        print(f"===== {path.relative_to(ROOT)}")
        print(path.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
