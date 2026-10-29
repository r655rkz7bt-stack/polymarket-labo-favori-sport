"""Sélection adverse des ordres posés (markout) : ordres servis contre ordres manqués."""

from __future__ import annotations

import json
from datetime import timedelta

import pytest

from conftest import NOW, make_book
from polymarket_agent import markout
from polymarket_agent.persistence import Database


def test_markout_compares_filled_and_missed_resting_orders(tmp_path):
    db = Database(tmp_path / "m.sqlite")
    at = NOW
    later = make_book("Y", bids=(("0.40", "100"),), asks=(("0.42", "100"),), ts=at + timedelta(minutes=6))
    db.save_books("c2", [later])
    entries = [{"type": "BUY", "ts": at.isoformat(), "data": {"token_id": "Y", "price": "0.47", "shares": "20",
                                                           "strategy_id": "nf", "extra": {"order_type": "limite"}}},
               {"type": "SETTLE", "ts": (at + timedelta(days=2)).isoformat(),
                "data": {"token_id": "Y", "payout_per_share": "0"}}]
    rep = markout.report(db, {"principal": entries}, at + timedelta(days=3))
    served = rep["principal"]["servis"]
    assert served["5 min"]["moyenne"] == pytest.approx(0.41 - 0.47) and served["résolution"]["moyenne"] == -0.47
    assert served["executions"] == 1 and rep["principal"]["manques"]["ordres"] == 0
    # Un ordre jamais servi (achat posé à 0,30, expiré) : le prix est resté au-dessus, il aurait gagné.
    db.conn.execute("INSERT INTO pending_orders VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    ("o1", "principal", "Y", "BUY", "20", 0, at.isoformat(),
                     (at + timedelta(minutes=12)).isoformat(), "EXPIRE", json.dumps({"max_price": "0.30"}),
                     at.isoformat()))
    rep = markout.report(db, {"principal": entries}, at + timedelta(days=3))
    assert rep["principal"]["manques"]["5 min"]["moyenne"] == pytest.approx(0.41 - 0.30)
    assert rep["principal"]["manques"]["ordres"] == 1
    db.close()
