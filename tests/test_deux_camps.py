"""Deux camps d'un même match et mise plafonnée (enquête du 23/10/2026)."""
from decimal import Decimal as D
from types import SimpleNamespace

from polymarket_agent.ledger import Position
from polymarket_agent.main import _cap_stake, _opposite_side
from polymarket_agent.safety_controller import Proposal


def _p(token, price, shares="100", market="m1", op="o"):
    return Proposal(op_id=op + token, side="BUY", market_id=market, event_id="e1", token_id=token, outcome=token,
                    est_probability=D(price) + D("0.05"), prob_low=D(price), prob_high=D("0.99"), max_price=D(price),
                    shares=D(shares), net_edge=D("0.02"), sources=("test",), uncertainty="", invalidation="",
                    horizon_hours=D("4"), strategy_id="nf_tennis_prix_bas", strategy_version="1.0.0")


def _portfolio(positions=(), total="1000", name="principal"):
    state = SimpleNamespace(positions={p.token_id: p for p in positions}, total_estimated=lambda marks: D(total))
    return SimpleNamespace(name=name, ledger=SimpleNamespace(state=state), marks={})


def _pos(token, avg, market="m1"):
    return Position(token_id=token, market_id=market, event_id="e1", outcome=token, shares=D("100"),
                    cost_basis=D(avg) * 100, opened_at="2026-10-22T04:32:00+00:00", strategy_id="nf_tennis_prix_bas",
                    strategy_version="1.0.0")


DEPS = SimpleNamespace(settings={"deux_camps": {"prix_min": 0.70}, "principal": {"max_mise": 0.25}})


def test_the_other_side_of_a_held_favourite_is_not_bought():
    # 22/10 : Mary Stoiana achetée à 0,85 à 04 h 32, puis Moyuka Uchijima (même match) à 0,85 à 05 h 46.
    pf = _portfolio([_pos("stoiana", "0.85")])
    assert _opposite_side(DEPS, pf, [_p("uchijima", "0.85")], {}) == []
    # Renforcer le même camp, un autre match ou une jambe à bas prix reste permis.
    kept = _opposite_side(DEPS, pf, [_p("stoiana", "0.86"), _p("x", "0.85", market="m2"), _p("uchijima", "0.12")], {})
    assert [p.token_id for p in kept] == ["stoiana", "x", "uchijima"]


def test_both_sides_proposed_in_the_same_cycle_keep_only_the_first():
    held_now: dict = {}
    kept = _opposite_side(DEPS, _portfolio(), [_p("a", "0.80")], held_now)
    kept += _opposite_side(DEPS, _portfolio(), [_p("b", "0.75")], held_now)  # proposé plus tard dans le même cycle
    assert [p.token_id for p in kept] == ["a"]


def test_arbitrage_legs_and_disabled_setting_pass():
    pf = _portfolio([_pos("oui", "0.48")])
    assert len(_opposite_side(DEPS, pf, [_p("non", "0.49")], {})) == 1
    off = SimpleNamespace(settings={})
    assert len(_opposite_side(off, _portfolio([_pos("a", "0.9")]), [_p("b", "0.9")], {})) == 1


def test_stake_is_capped_at_a_quarter_of_the_capital():
    # 22/10 : nf_court_prix_haut misait 489 € sur 922 € (53 %) ; plafond 25 % → 250 € sur 1 000 €.
    capped = _cap_stake(DEPS, _portfolio(), [_p("a", "0.95", shares="515"), _p("b", "0.90", shares="100")])
    assert capped[0].shares == D("263.15") and capped[0].shares * capped[0].max_price <= D("250")
    assert capped[1].shares == D("100")
    assert _cap_stake(SimpleNamespace(settings={}), _portfolio(), [_p("a", "0.95", shares="515")])[0].shares == D("515")
