"""Favori naïf : familles de marchés, filtres (`Filtered`) et mode patient (`PatientOrders`)."""

from __future__ import annotations

import copy

from datetime import timedelta
from decimal import Decimal as D

from conftest import KEY, NOW, ROOT, make_book, raw_market
from polymarket_agent.ledger import Ledger
from polymarket_agent.safety_controller import RiskLimits
from polymarket_agent.sources.polymarket_gamma import parse_market
from polymarket_agent.strategy_engine import Filtered, NaiveFavorite, PatientOrders, load_strategies, market_family

BASE = {"min_ask": D("0.85"), "max_ask": D("0.97"), "bias": D("0"), "uncertainty": D("0.01"),
        "min_net_edge": D("0.01"), "max_extra_ticks": D("2"), "max_proposals_per_cycle": D("3"),
        "kelly_fraction": D("0.5")}


def _setup(tmp_path, fee_type="politics_fees", enabled=True, end_hours=48, bid="0.90", ask="0.91", **raw):
    m = parse_market(raw_market("p1", yes_token="Y", no_token="N", yes_price="0.905", best_bid=float(bid),
                                best_ask=float(ask), end=NOW + timedelta(hours=end_hours), fees_enabled=enabled,
                                feeType=fee_type, **raw))
    books = {"Y": make_book("Y", bids=((bid, "500"),), asks=((ask, "500"),)),
             "N": make_book("N", bids=((str(1 - D(ask)), "500"),), asks=((str(1 - D(bid)), "500"),))}
    ledger = Ledger.create(tmp_path / "l.jsonl", KEY, tmp_path / "l.lock", "1000", now=NOW)
    return m, books, ledger, RiskLimits.from_file(ROOT / "config" / "risk_limits.yaml")


def _strategy(**params):
    return NaiveFavorite("1.0.0", "2026-10-16", dict(BASE, **{k: D(str(v)) for k, v in params.items()}))


def test_market_family_comes_from_the_fee_schedule(tmp_path):
    assert market_family(_setup(tmp_path)[0]) == "politics"
    assert market_family(_setup(tmp_path / "b", fee_type="sports_fees_v3")[0]) == "sports"
    assert market_family(_setup(tmp_path / "c", fee_type=None, enabled=False)[0]) == "sans_frais"


def test_zero_fee_weather_markets_stay_in_the_weather_family(tmp_path):
    """19-20/10/2026 : Gamma passe les marchés de température en « zero_fees » ; sans l'adresse de l'événement la famille devenait
    « zero » et loterie_meteo (familles [weather]) ne voyait plus rien : dernier achat le 20/10 à 16 h 27."""
    events = [{"id": "e1", "slug": "highest-temperature-in-paris-on-october-24-2026", "title": "Highest temperature in Paris"}]
    paris = _setup(tmp_path, fee_type="zero_fees", events=events)[0]
    assert market_family(paris) == "weather"
    fed = _setup(tmp_path / "b", fee_type="zero_fees", events=[{"id": "e2", "slug": "fed-decision-in-december", "title": "Fed"}])[0]
    assert market_family(fed) == "zero"  # un marché sans frais qui n'est pas de la météo ne devient pas « météo »
    old = _setup(tmp_path / "c", fee_type="weather_fees", events=events)[0]
    assert market_family(old) == "weather"  # ancien barème inchangé


def test_child_strategy_plays_only_its_piece_of_the_markets(tmp_path):
    """La stratégie sur un morceau des marchés : les filtres de marché passent AVANT elle (elle choisit ses
    meilleurs paris dans le morceau), le prix APRÈS."""
    import pytest
    from polymarket_agent.strategy_engine import Filtered, StrategyConfigError
    pol, pb, ledger, limits = _setup(tmp_path, end_hours=48)
    spo, sb, _, _ = _setup(tmp_path / "s", fee_type="sports_fees_v3", end_hours=48)
    spo.market_id, spo.token_ids = "s1", ["SY", "SN"]
    books = dict(pb, SY=make_book("SY", bids=(("0.90", "500"),), asks=(("0.91", "500"),)),
                 SN=make_book("SN", bids=(("0.09", "500"),), asks=(("0.10", "500"),)))
    mother = _strategy(bias=0.03)
    both = {p.market_id for p in mother.propose([pol, spo], books, ledger.state, limits, "b").proposals}
    assert both == {"p1", "s1"}
    sport = Filtered(_strategy(bias=0.03), {"familles": ["sports"]})
    assert [p.market_id for p in sport.propose([pol, spo], books, ledger.state, limits, "b").proposals] == ["s1"]
    assert not Filtered(_strategy(bias=0.03), {"prix_max": 0.9}).propose([pol, spo], books, ledger.state, limits, "b").proposals
    assert not Filtered(_strategy(bias=0.03), {"heures_min": 72}).propose([pol, spo], books, ledger.state, limits, "b").proposals
    with pytest.raises(StrategyConfigError):
        Filtered(_strategy(), {"couleur": "bleu"})


def test_hour_of_day_filter(tmp_path):
    from datetime import timedelta
    from polymarket_agent.strategy_engine import Filtered
    m, books, ledger, limits = _setup(tmp_path, end_hours=48)  # carnets à 12 h UTC (NOW)
    assert Filtered(_strategy(bias=0.03), {"heure_utc": [12, 24]}).propose([m], books, ledger.state, limits, "b").proposals
    assert not Filtered(_strategy(bias=0.03), {"heure_utc": [4, 12]}).propose([m], books, ledger.state, limits, "b").proposals
    out = Filtered(_strategy(bias=0.03), {"heure_utc": [4, 12]}).propose([m], books, ledger.state, limits, "b")
    assert out.abstentions == {"hors de la plage horaire (heure_utc 4-12)": 1}  # 28/10/2026 : plus d'abstention vide hors de l'heure
    assert Filtered(_strategy(bias=0.03), {"heure_utc": [20, 13]}).propose([m], books, ledger.state, limits, "b").proposals  # passe minuit


def test_price_filter_of_a_patient_daughter_reads_the_posted_order_price(tmp_path):
    """21/10 : la fille « prix payé 0,875-0,90 » filtrait le prix du VENDEUR (0,89) puis l'ordre posé au meilleur
    acheteur (0,84) était servi sous la plage : le morceau testé n'était pas celui annoncé (`prix_final`)."""
    from polymarket_agent.strategy_engine import Filtered, PatientOrders
    m, books, ledger, limits = _setup(tmp_path, fee_type="sports_fees_v3", end_hours=24, bid="0.83", ask="0.89")
    rng = {"prix_min": 0.875, "prix_max": 0.90}
    old = PatientOrders(Filtered(_strategy(min_ask=0.80, max_ask=0.95, bias=0.06), rng), 1)
    out = old.propose([m], books, ledger.state, limits, "b")
    assert [o.max_price for o in out.limit_orders] == [D("0.84")]  # l'ancien montage laisse passer 0,84
    new = Filtered(PatientOrders(_strategy(min_ask=0.80, max_ask=0.95, bias=0.06), 1), rng)
    assert not new.propose([m], books, ledger.state, limits, "b").limit_orders
    tight, tb, tl, _ = _setup(tmp_path / "t", fee_type="sports_fees_v3", end_hours=24, bid="0.88", ask="0.89")
    assert [o.max_price for o in new.propose([tight], tb, tl.state, limits, "b").limit_orders] == [D("0.88")]  # dans la plage
    loaded = {s.strategy_id: s for s in load_strategies(ROOT / "config" / "strategies.yaml")}
    assert isinstance(loaded["nf_sport_patiente_prix_850_875"], PatientOrders)  # sans `prix_final` : filtre sur le prix du vendeur
    assert isinstance(loaded["nf_sport_patiente_prix_850_875"].inner, Filtered)


def test_estimate_cap_lets_a_quasi_certain_favorite_priced_0_99_show_an_edge(tmp_path):
    """22/10 : 23 des 27 marchés « 1re place dans l'État » du 2e tour brésilien étaient à 0,98-0,995 (même vainqueur qu'au 1er tour) ;
    `favori_politique_quasi_certain` (estimation plafonnée à 0,99, borne basse 0,987) n'achetait jamais au-dessus de 0,985."""
    m, books, ledger, limits = _setup(tmp_path, bid="0.985", ask="0.99")
    quasi = dict(min_ask=0.97, max_ask=0.995, uncertainty=0.003, min_net_edge=0.002, max_extra_ticks=1, bias=0.02)
    assert not _strategy(**quasi).propose([m], books, ledger.state, limits, "b").proposals
    out = _strategy(q_max=0.996, **quasi).propose([m], books, ledger.state, limits, "b")
    assert len(out.proposals) == 1 and out.proposals[0].prob_low == D("0.993") and out.proposals[0].est_probability == D("0.996")


def test_match_only_filter_refuses_season_and_award_markets(tmp_path):
    """23/10/2026 : « Mbappé gagne le Ballon d'Or, NON » (pas de `sportsMarketType`) était pris par la
    famille « sports » : 359 € à 0,94 pour une estimation de 0,99. `un_match` ne garde que les paris de match."""
    from polymarket_agent.strategy_engine import Filtered
    m, books, ledger, limits = _setup(tmp_path, fee_type="sports_fees_v3", end_hours=48)
    only = Filtered(_strategy(bias=0.03), {"familles": ["sports"], "un_match": True})
    assert not only.propose([m], books, ledger.state, limits, "b").proposals  # marché de saison : pas de type
    m.sports_market_type = "moneyline"
    assert only.propose([m], books, ledger.state, limits, "b").proposals
    assert Filtered(_strategy(bias=0.03), {"familles": ["sports"]}).propose([m], books, ledger.state, limits, "b").proposals
    season = Filtered(_strategy(bias=0.03), {"familles": ["sports"], "un_match": False})
    assert not season.propose([m], books, ledger.state, limits, "b").proposals  # un match : refusé par le filtre « saison »
    m.sports_market_type = None
    assert season.propose([m], books, ledger.state, limits, "b").proposals


def test_filtered_strategy_says_which_filter_dropped_the_markets_it_never_saw(tmp_path):
    """25/10/2026 : une stratégie voyait 438 marchés, 0 proposition et AUCUNE raison (un filtre écartait tout) : impossible de dire
    pourquoi elle restait muette. Les marchés écartés par un filtre sont comptés dans les abstentions."""
    cfg = Filtered(_strategy(bias=0.03), {"question_exclut": ["fed"]})
    limits = RiskLimits.from_file(ROOT / "config" / "risk_limits.yaml")
    m, books, ledger, _ = _setup(tmp_path, fee_type="zero_fees", end_hours=24 * 14, bid="0.15", ask="0.16", negRisk=True)
    m.question = "Will the Fed cut rates in December?"
    out = cfg.propose([m, copy.copy(m)], books, ledger.state, limits, "b")
    assert not out.proposals and out.abstentions == {"hors du filtre question_exclut": 2}


def test_zero_fee_political_markets_are_politics_again(tmp_path):
    """24/10/2026 : Gamma passe la politique en « zero_fees » (19-20/10) : les marchés d'élection se reconnaissent à l'adresse de
    l'événement ; la Fed, le box-office, les IPO restent « zero ». (Le filtre `familles: [sports]` en dépend.)"""
    def fam(slug, sub):
        return market_family(_setup(tmp_path / sub, fee_type="zero_fees", events=[{"id": "e", "slug": slug, "title": "t"}])[0])
    assert fam("texas-senate-election-winner", "a") == "politics"
    assert fam("which-party-will-win-the-house-in-2026", "b") == "politics"
    assert fam("next-prime-minister-of-serbia-20260629223938642", "c") == "politics"
    assert fam("fl-27-house-margin-of-victory-2026", "d") == "politics"
    assert fam("fed-decision-in-december", "e") == "zero"
    assert fam("largest-ipo-by-market-cap-in-2026-287", "f") == "zero"
    assert fam("white-house-posts-october-27-november-3-2026", "g") == "zero"
    assert fam("highest-temperature-in-paris-on-october-24-2026", "h") == "weather"