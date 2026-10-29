"""Vue du fantôme (29/10/2026) : les marchés que ses voisines de l'ancien labo lui faisaient voir."""

from __future__ import annotations

import json

from conftest import NOW, ROOT, raw_market
from polymarket_agent import voisines
from polymarket_agent.config import load_settings
from polymarket_agent.main import run_cycle
from polymarket_agent.sources.polymarket_gamma import parse_market
from test_cycle import FakeClob, FakeGamma, env, make_book  # noqa: F401 - `env` : fixture partagée

SPORT = {"feeType": "sports_fees_v3"}


def _itf(market_id="itf1", underdog="0.15"):
    """Match du circuit ITF (source itftennis.com), outsider à 0,15 : demandé par outsider_itf."""
    return raw_market(market_id, yes_token=f"{market_id}Y", no_token=f"{market_id}N", yes_price=str(1 - float(underdog)),
                      best_bid=0.84, best_ask=0.85, resolutionSource="https://www.itftennis.com/en/tournament/", **SPORT)


def test_the_neighbours_are_those_of_the_old_lab():
    loaded = {v.strategy_id: v for v in voisines.charger(ROOT / "config" / "voisines.yaml")}
    assert sorted(loaded) == sorted(["outsider_itf", "favori_esport_milieu", "vague_volume_suivre", "vague_volume_suivre_x5",
                                     "moins_de_cartes", "moins_de_cartes_franche", "moins_de_kills", "non_aux_drames",
                                     "cross_market", "echelle_dates", "rallies_non", "rallies_non_favori", "mentions_non",
                                     "mentions_non_outsider", "statu_quo_ia", "detroits_non_normal", "ia_oui_sorties"])
    itf = loaded["outsider_itf"]
    assert itf.filters == {"sources": ["itftennis.com"], "ecart_max": 0.05} and itf.inner.families == ("sports",)
    assert itf.inner.params["max_candidates"] == 40 and itf.inner.params["underdog"] == 1
    # Aucune voisine ne trade ici : seule leur demande de marchés est reprise.
    assert not any(hasattr(v, "propose") for v in loaded.values())


def test_requests_keep_only_sports_markets():
    loaded = voisines.charger(ROOT / "config" / "voisines.yaml")
    itf = parse_market(_itf())
    cs = parse_market(raw_market("cs1", yes_token="cY", no_token="cN", question="Counter-Strike: Vitality vs FaZe (BO3)",
                                 sportsMarketType="moneyline", **SPORT))
    politics = parse_market(raw_market("pol", yes_token="pY", no_token="pN", yes_price="0.9", question="Will X win the election?"))
    asked = {m.market_id for wish in voisines.demandes(loaded, [itf, cs, politics]) for m in wish}
    assert asked == {"itf1", "cs1"}  # outsider_itf, favori_esport_milieu ; jamais hors du sport


def test_a_market_asked_by_a_neighbour_is_seen_and_bought_by_the_strategy(env, paths):
    """Liste des 150 plus échangés vide : le match ITF n'est vu que parce qu'outsider_itf le demande, comme dans l'ancien labo."""
    from polymarket_agent.ledger import Ledger
    from conftest import KEY
    from polymarket_agent.strategy_engine import load_strategies
    make, db, _ = env
    settings = load_settings()
    settings["scanner"] = dict(settings["scanner"], watchlist_size=0)
    book = make_book("itf1Y", bids=(("0.84", "500"),), asks=(("0.85", "40"), ("0.86", "500")), ts=NOW)
    strategy = load_strategies(ROOT / "config" / "strategies.yaml")
    deps = make(FakeGamma([_itf()]), FakeClob({"itf1Y": book}), settings, strategy)
    run_cycle(deps)
    assert json.loads(db.get_value("abstentions"))["strategies"]["nf_sport_patiente_prix_850_875"]["marches"] == 0
    deps.voisines = voisines.charger(ROOT / "config" / "voisines.yaml")
    report = run_cycle(deps)
    assert report.counts["demandes_voisines"] == 1
    seen = json.loads(db.get_value("abstentions"))["strategies"]["nf_sport_patiente_prix_850_875"]
    assert seen["marches"] == 1 and seen["ordres"] == 1  # vu grâce à la voisine : ordre posé 1 cran au-dessus de 0,84
    assert not Ledger.open(paths[0], KEY, paths[1]).state.positions  # posé, pas encore servi
