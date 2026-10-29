"""Mêmes conditions que l'ancien fantôme (29/10/2026) : antécédents relus, mise aux règles du fantôme, capital à 1 000 €."""

from __future__ import annotations

import gzip
import json
from datetime import timedelta
from decimal import Decimal as D

from conftest import KEY, NOW, ROOT, raw_market
from polymarket_agent import antecedents, dashboard, sante
from polymarket_agent.config import load_settings
from polymarket_agent.ledger import Ledger
from polymarket_agent.main import run_cycle
from polymarket_agent.persistence import Database
from test_cycle import FakeClob, FakeGamma, env, make_book  # noqa: F401 - `env` : fixture partagée

ARCHIVE = ROOT / "archives" / "labo_origine" / "nf_sport_patiente_prix_850_875_v1.0.0.jsonl.gz"


def test_the_old_ghost_journal_is_read_and_its_hash_chain_checked(tmp_path):
    rows = antecedents.load(ARCHIVE)
    assert len(rows) == 846 and rows[0]["type"] == "GENESIS" and rows[-1]["ts"].startswith("2026-10-28T23:42")
    trades = antecedents.trades(rows, [])
    assert len(trades) == 85 and round(sum(float(t["pnl"]) for t in trades), 2) == 484.44
    assert rows[-1]["data"]["total_estimated"] == "1472.5856876955"  # dernière valeur signée : la reprise
    # Un prix retouché casse la chaîne de hash : plus aucun antécédent.
    lines = gzip.open(ARCHIVE, "rt", encoding="utf-8").read().splitlines()
    row = json.loads(lines[61])
    assert row["type"] == "BUY"
    row["data"]["price"] = "0.50"
    lines[61] = json.dumps(row)
    bad = tmp_path / "faux.jsonl.gz"
    with gzip.open(bad, "wt", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    assert antecedents.load(bad) == [] and antecedents.load(tmp_path / "absent.gz") == [] and antecedents.load(None) == []


def test_ghost_sizing_rules_on_its_own_past():
    """À la fin de son journal, le fantôme misait Kelly entier : prévisions prouvées meilleures que le prix (85 jugées,
    avance > 0), pas de renfort (confiance par jour 78 % < 80 %), pas de suspension."""
    settings = load_settings()
    rows = antecedents.load(ARCHIVE)
    h = sante.assess(rows, [], antecedents.trades(rows, []), settings)
    assert h["jugees"] == 85 and h["avance"] > 0 and h["prudence"] == 1.0
    assert h["closed_trades"] == 85 and h["day_confidence"] < 0.8 and h["renfort"] == 1.0
    assert h["suspendue"] is None and h["facteur"] == 1.0
    fresh = sante.assess([], [], [], settings)
    assert fresh["prudence"] == 0.5 and fresh["facteur"] == 0.5  # sans passé : ½ Kelly, comme un fantôme neuf


def test_renfort_and_suspension_follow_the_old_rules():
    cfg = sante.config(load_settings())
    row = {"closed_trades": 60, "excess_wins": 6.8, "assumed_edge": 0.0509, "day_confidence": 0.95}
    assert sante.boost(row, cfg) == round(6.8 / 90 / 0.0509, 4)  # ≈ 1,48
    assert sante.boost(dict(row, excess_wins=40), cfg) == 2.0  # plafond
    assert sante.boost(dict(row, day_confidence=0.7), cfg) == 1.0 and sante.boost(dict(row, closed_trades=20), cfg) == 1.0
    burner = [{"pnl": D("-2"), "fees": D("3")}] * 25  # brut +1 par pari, frais 3 : les frais mangent tout
    assert sante.suspension(burner, cfg) and sante.suspension(burner[:5], cfg) is None
    assert sante.suspension([{"pnl": D("-5"), "fees": D("1")}] * 25, cfg) is None  # perd déjà avant frais


def test_the_cycle_bets_like_the_ghost_would_with_its_past(env, paths):
    """Sans passé : ½ Kelly ; avec les antécédents (prévisions prouvées) : Kelly entier, comme le fantôme à la fin."""
    make, db, clock = env
    deep = {"tokA": make_book("tokA", bids=(("0.89", "5000"),), asks=(("0.90", "100000"),))}
    settings = load_settings()
    settings["principal"] = {}  # sans plafond de mise : on compare les deux mises
    run_cycle(make(FakeGamma([raw_market()]), FakeClob(deep), settings))
    half = Ledger.open(paths[0], KEY, paths[1]).state.positions["tokA"].shares
    assert json.loads(db.get_value("sante"))["facteur"] == 0.5
    deps = make(FakeGamma([raw_market("2", yes_token="tokC", no_token="tokD", event_id="e2")]),
                FakeClob({"tokC": make_book("tokC", bids=(("0.89", "5000"),), asks=(("0.90", "100000"),),
                                            ts=NOW + timedelta(minutes=5))}), settings)
    deps.history = antecedents.load(ARCHIVE)
    clock["now"] = NOW + timedelta(minutes=5)
    run_cycle(deps)
    whole = Ledger.open(paths[0], KEY, paths[1]).state.positions["tokC"].shares
    assert json.loads(db.get_value("sante"))["facteur"] == 1.0
    assert whole > half * D("1.5")  # même pari, mise du fantôme prouvé (le capital a un peu baissé entre-temps)


def test_the_dashboard_counts_the_past_but_the_capital_starts_at_1000(tmp_path):
    """29/10/2026 (Etienne) : le capital repart de 1 000 € ; les 85 paris du fantôme comptent dans la confiance."""
    rows = antecedents.load(ARCHIVE)
    live = Ledger.create(tmp_path / "l.jsonl", KEY, tmp_path / "l.lock", "1000", now=NOW + timedelta(days=15))
    db = Database(tmp_path / "db.sqlite")
    data = dashboard.collect(live, db, {"cycle": {"bucket_minutes": 2}}, ROOT, now=NOW + timedelta(days=15), history=rows)
    conf = data["strategie"]["confiance"]
    assert conf["operations"] == 85 and conf["anterieures"] == 85 and conf["antecedents_fin"] == "2026-10-28T23:42"
    assert data["capital_initial"] == 1000.0 and data["capital"]["capital_total_estime"] == "1000.0000"
    assert len(data["courbe"]) == 1  # la courbe du capital est celle d'ici seulement
    assert "aux conditions de son ancien fantôme" in data["situation"]["phrase"]
    assert "dont 85 dans son ancien fantôme" in data["situation"]["detail"]
    db.close()
