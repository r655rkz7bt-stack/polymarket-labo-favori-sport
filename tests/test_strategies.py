"""Stratégie de référence naïve et chargement des paramètres bornés."""

from __future__ import annotations

from decimal import Decimal

import pytest

from conftest import NOW, ROOT, make_book, raw_market
from polymarket_agent.safety_controller import evaluate
from polymarket_agent.main import market_context
from polymarket_agent.sources.polymarket_gamma import parse_market
from polymarket_agent.strategy_engine import (NaiveFavorite, StrategyConfigError, load_params,
                                              load_strategies, make_op_id)

D = Decimal


def strategy(**overrides):
    params = {"min_ask": D("0.80"), "max_ask": D("0.95"), "bias": D("0.05"),
              "uncertainty": D("0.015"), "min_net_edge": D("0.02"), "max_extra_ticks": D("2"),
              "max_proposals_per_cycle": D("3")}
    params.update(overrides)
    return NaiveFavorite("1.0.0", "2026-10-14", params)


def test_repository_config_loads():
    """Laboratoire dédié : une seule stratégie, le favori de sport payé 0,85-0,875 en mode patient (ordre posé)."""
    from polymarket_agent.strategy_engine import Filtered, PatientOrders
    loaded = load_strategies(ROOT / "config" / "strategies.yaml")
    assert [s.strategy_id for s in loaded] == ["nf_sport_patiente_prix_850_875"]
    s = loaded[0]
    assert isinstance(s, PatientOrders) and isinstance(s.inner, Filtered)
    assert s.inner.filters == {"familles": ["sports"], "prix_min": 0.85, "prix_max": 0.875}
    assert s.params["kelly_fraction"] == D("0.5") and s.params["bias"] == D("0.05")
    assert s.annuler_au_coup_d_envoi is False


def test_param_outside_bounds_is_refused():
    with pytest.raises(StrategyConfigError):
        load_params({"bias": {"value": 0.5, "min": 0, "max": 0.06}})


def test_proposes_buy_on_favorite_and_controller_accepts(ledger, limits):
    market = parse_market(raw_market())
    book = make_book("tokA", bids=(("0.89", "500"),), asks=(("0.90", "300"),))
    out = strategy().propose([market], {"tokA": book}, ledger.state, limits, "B1")
    assert len(out.proposals) == 1
    p = out.proposals[0]
    assert p.side == "BUY" and p.token_id == "tokA" and p.outcome == "Yes"
    assert p.prob_low < p.est_probability < p.prob_high
    assert p.net_edge >= D("0.02")  # le min_net_edge de la stratégie
    verdict = evaluate(p, ledger.state, market_context(market, book, "tokA"), limits, NOW)
    assert verdict.accepted, verdict.reasons


def test_picks_no_token_when_no_is_favorite(ledger, limits):
    market = parse_market(raw_market(yes_price="0.1", best_bid=0.09, best_ask=0.10))
    book = make_book("tokB", bids=(("0.89", "500"),), asks=(("0.90", "300"),))
    out = strategy().propose([market], {"tokB": book}, ledger.state, limits, "B1")
    assert out.proposals[0].token_id == "tokB" and out.proposals[0].outcome == "No"


def test_abstains_without_data_or_edge(ledger, limits):
    market = parse_market(raw_market())
    s = strategy()
    assert s.propose([market], {}, ledger.state, limits, "B1").abstentions == {"carnet indisponible": 1}
    wide = make_book("tokA", bids=(("0.80", "500"),), asks=(("0.90", "300"),))  # milieu 0,85
    assert s.propose([market], {"tokA": wide}, ledger.state, limits, "B1").abstentions == {
        "avantage net insuffisant": 1}
    cheap = make_book("tokA", bids=(("0.50", "500"),), asks=(("0.51", "300"),))
    assert not s.propose([market], {"tokA": cheap}, ledger.state, limits, "B1").proposals


def test_strategy_is_free_to_lower_its_own_edge_threshold(ledger, limits):
    """Liberté totale : plus de plancher imposé, la stratégie choisit son seuil."""
    market = parse_market(raw_market())
    book = make_book("tokA", bids=(("0.89", "500"),), asks=(("0.90", "300"),))
    strict = strategy(min_net_edge=D("0.02"), bias=D("0.03"))
    free = strategy(min_net_edge=D("0"), bias=D("0.03"))
    assert not strict.propose([market], {"tokA": book}, ledger.state, limits, "B1").proposals
    assert free.propose([market], {"tokA": book}, ledger.state, limits, "B1").proposals


def test_op_id_is_deterministic():
    assert make_op_id("B1", "s", "1", "t", "BUY") == make_op_id("B1", "s", "1", "t", "BUY")
    assert make_op_id("B1", "s", "1", "t", "BUY") != make_op_id("B2", "s", "1", "t", "BUY")


def test_regression_default_config_can_trade_a_tight_favorite(ledger, limits):
    """Défaut du 14/10/2026 : avec biais 0,04 et incertitude 0,02, la borne basse
    (milieu + 0,02) ne pouvait jamais dépasser prix + frais + 0,02 : aucune position possible."""
    s = strategy()  # paramètres de config/strategies.yaml au 14/10/2026 (biais 0,05, incertitude 0,015)
    market = parse_market(raw_market())
    book = make_book("tokA", bids=(("0.89", "500"),), asks=(("0.90", "300"),))
    assert len(s.propose([market], {"tokA": book}, ledger.state, limits, "B1").proposals) == 1
