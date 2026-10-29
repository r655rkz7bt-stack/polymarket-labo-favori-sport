"""Scanner, client Gamma, client CLOB, client HTTP."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
import requests

from conftest import NOW, raw_market
from polymarket_agent.http_client import HttpClient, SourceUnavailable
from polymarket_agent.market_scanner import classify, scan
from polymarket_agent.sources.polymarket_clob import parse_book
from polymarket_agent.sources.polymarket_gamma import parse_market

D = Decimal
CFG = {"page_size": 100, "max_pages": 3, "min_horizon_minutes": 30, "max_horizon_hours": 72,
       "min_liquidity": 1000, "min_volume_24h": 1000, "max_spread": 0.05, "watchlist_size": 30}


def test_parse_real_format():
    m = parse_market(raw_market())
    assert m.data_ok, m.issues
    assert m.token_ids == ["tokA", "tokB"]
    assert m.outcome_prices == [D("0.895"), D("0.105")]
    assert m.fees.rate == D("0.04") and m.fees.exponent == D("1") and m.fees.enabled
    assert m.event_id == "ev1"
    assert classify(m, NOW, CFG) == ""


@pytest.mark.parametrize("override,issue", [
    ({"bestBid": None}, "meilleur prix"),
    ({"outcomePrices": '["0.9", "0.5"]'}, "incohérents"),
    ({"clobTokenIds": '["a"]'}, "binaire"),
    ({"feeSchedule": None, "feesEnabled": True}, "barème absent"),
    ({"feesEnabled": None}, "frais inconnu"),
    ({"endDate": None}, "échéance"),
    ({"bestBid": 0.95, "bestAsk": 0.90}, "meilleurs prix incohérents"),
])
def test_inconsistent_data_is_flagged(override, issue):
    raw = raw_market()
    raw.update(override)
    if override.get("feeSchedule", 1) is None:
        raw.pop("feeSchedule")
    m = parse_market(raw)
    assert any(issue in i for i in m.issues), m.issues
    assert classify(m, NOW, CFG) == "données incohérentes"


def test_classification_filters():
    assert classify(parse_market(raw_market(end=NOW + timedelta(minutes=10))), NOW, CFG) == "hors horizon"
    assert classify(parse_market(raw_market(end=NOW + timedelta(days=5))), NOW, CFG) == "hors horizon"
    assert classify(parse_market(raw_market(spread=0.2)), NOW, CFG) == "écart achat/vente trop large"
    assert classify(parse_market(raw_market(liquidityNum=10)), NOW, CFG) == "liquidité insuffisante"
    assert classify(parse_market(raw_market(acceptingOrders=False)), NOW, CFG) == "carnet indisponible"
    decided = parse_market(raw_market(yes_price="0.9995", best_bid=0.999, best_ask=1, spread=0.001))
    assert classify(decided, NOW, CFG).startswith("issue quasi certaine")


class FakeGamma:
    def __init__(self, markets):
        self.markets = markets

    def list_markets(self, **kwargs):
        return self.markets


def test_scan_builds_watchlist_sorted_by_volume():
    markets = [raw_market("1", volume24hr=5000), raw_market("2", volume24hr=90000),
               raw_market("3", bestBid=None), raw_market("2", volume24hr=90000)]
    result = scan(FakeGamma(markets), NOW, CFG)
    assert [m.market_id for m in result.watchlist] == ["2", "1"]
    assert result.excluded == {"données incohérentes": 1}
    assert len(result.markets) == 3  # doublon ignoré


def test_parse_book_sorts_best_first():
    raw = {"asset_id": "tokA", "timestamp": "1792009734447", "tick_size": "0.001",
           "min_order_size": "5",
           "bids": [{"price": "0.001", "size": "10"}, {"price": "0.003", "size": "5"}],
           "asks": [{"price": "0.999", "size": "1"}, {"price": "0.005", "size": "20"},
                    {"price": "abc", "size": "1"}]}
    book = parse_book(raw, NOW)
    assert book.best_bid == D("0.003") and book.best_ask == D("0.005")
    assert len(book.asks) == 2  # niveau illisible ignoré
    assert book.timestamp.year == 2026


class FakeResponse:
    def __init__(self, status, payload=None, headers=None):
        self.status_code, self._payload, self.headers = status, payload, headers or {}

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.responses, self.headers, self.calls = list(responses), {}, 0

    def request(self, *args, **kwargs):
        self.calls += 1
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def test_http_retries_then_succeeds():
    session = FakeSession([requests.ConnectionError(), FakeResponse(503), FakeResponse(200, [1])])
    client = HttpClient(session=session, sleep=lambda s: None, max_retries=3)
    assert client.get_json("https://x") == [1]
    assert session.calls == 3


def test_unavailable_source_raises_cleanly():
    session = FakeSession([FakeResponse(503)] * 4)
    client = HttpClient(session=session, sleep=lambda s: None, max_retries=3)
    with pytest.raises(SourceUnavailable):
        client.get_json("https://x")
    blocked = HttpClient(session=FakeSession([FakeResponse(403)]), sleep=lambda s: None)
    with pytest.raises(SourceUnavailable):
        blocked.get_json("https://x")


def test_long_markets_with_holding_rewards_are_scanned_too():
    """19/10/2026 : l'horizon normal (ici 72 h) écartait les marchés à récompense de détention, qui
    se règlent souvent dans un ou deux ans ; une page de plus les ajoute, eux seuls."""
    class TwoCalls:
        def __init__(self):
            self.calls = []

        def list_markets(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return [raw_market("court")]
            return [raw_market("long", end=NOW + timedelta(days=400), holdingRewardsEnabled=True),
                    raw_market("long_sans", end=NOW + timedelta(days=400)),
                    raw_market("court", holdingRewardsEnabled=True)]
    g = TwoCalls()
    result = scan(g, NOW, dict(CFG, holding_pages=1, holding_max_horizon_days=900))
    assert [m.market_id for m in result.markets] == ["court", "long"]
    assert all(m.eligible for m in result.markets) and g.calls[1]["max_pages"] == 1
    assert len(TwoCalls().calls) == 0 and len(scan(TwoCalls(), NOW, CFG).markets) == 1  # désactivé par défaut


def test_gamma_pages_are_read_in_parallel_in_order_and_stop_at_the_first_short_page():
    from polymarket_agent.sources.polymarket_gamma import GammaClient

    class Http:
        def __init__(self, pages):
            self.pages, self.offsets = pages, []

        def get_json(self, url, params=None, headers=None):
            self.offsets.append(params["offset"])
            n = params["offset"] // params["limit"]
            return [{"id": f"{n}-{i}"} for i in range(self.pages[n] if n < len(self.pages) else 0)]
    http = Http([2, 2, 2, 1, 2, 2])
    out = GammaClient(http).list_markets(end_min=NOW, end_max=NOW + timedelta(days=1), page_size=2, max_pages=10)
    assert [m["id"] for m in out] == ["0-0", "0-1", "1-0", "1-1", "2-0", "2-1", "3-0"]  # ordre gardé
    assert max(http.offsets) < 8  # arrêt après le premier groupe qui contient une page incomplète


def test_tag_pages_add_markets_of_a_tag_once():
    """21/10/2026 : les marchés « mentions » (étiquette 100343) n'entraient pas dans les pages
    triées par volume ; `tag_pages` les ajoute, sans doublon et avec les mêmes filtres."""
    class TagGamma(FakeGamma):
        def list_markets(self, **kwargs):
            if kwargs.get("tag_id") == 100343:
                return [raw_market("1"), raw_market("9", volume24hr=40, liquidityNum=500),
                        raw_market("8", liquidityNum=10)]
            if kwargs.get("tag_id") is not None:
                raise SourceUnavailable("étiquette en panne (test)")
            return self.markets
    tags = {100343: {"pages": 2, "min_liquidity": 100, "min_volume_24h": 0}, 7: 1, 5: 0}
    result = scan(TagGamma([raw_market("1")]), NOW, dict(CFG, tag_pages=tags))
    assert sorted(m.market_id for m in result.markets) == ["1", "8", "9"]
    assert [m.market_id for m in result.watchlist if m.market_id == "9"] == ["9"]
    assert result.excluded == {"liquidité insuffisante": 1}


def test_a_tag_market_already_read_in_the_normal_pages_gets_the_tag_thresholds():
    """24/10/2026 : un marché lu dans les pages normales y était jugé avec les seuils GÉNÉRAUX, puis ignoré par l'étiquette (déjà vu) : 21 tranches de box-office
    (liquidité 800-1 900 $, écart 0,06-0,10) restaient inexploitables alors que l'étiquette (51) les accepte ; box_office_favori voyait « moins de deux tranches »."""
    class TagGamma(FakeGamma):
        def list_markets(self, **kwargs):
            if kwargs.get("tag_id") == 51:
                return [raw_market("1", liquidityNum=300, spread=0.09), raw_market("2", liquidityNum=300, spread=0.09, active=False)]
            return self.markets
    tags = {51: {"pages": 1, "min_liquidity": 0, "min_volume_24h": 0, "max_spread": 0.60}}
    normal = [raw_market("1", liquidityNum=300, spread=0.09), raw_market("2", liquidityNum=300, spread=0.09, active=False)]
    result = scan(TagGamma(normal), NOW, dict(CFG, tag_pages=tags))
    by_id = {m.market_id: m for m in result.markets}
    assert by_id["1"].eligible and by_id["1"].exclusion == ""        # jugé avec le seuil de l'étiquette
    assert not by_id["2"].eligible and by_id["2"].exclusion == "inactif ou fermé"  # jamais un marché fermé
    assert result.excluded == {"inactif ou fermé": 1}               # le compteur d'exclusions suit
    plain = scan(TagGamma(normal), NOW, dict(CFG))
    assert not {m.market_id: m for m in plain.markets}["1"].eligible  # sans étiquette : seuils généraux


def test_long_pages_add_any_long_market_only_for_strategies_that_ask():
    """« Réveiller les muettes » (21/10/2026) : au-delà de 30 jours, le scanner ne lisait que les
    marchés à récompense de détention ; `long_pages` ajoute tous les marchés longs (volume du jour
    plus bas), jamais dans la liste de surveillance de base (`extra_only`)."""
    class TwoCalls:
        def __init__(self):
            self.calls = []

        def list_markets(self, **kwargs):
            self.calls.append(kwargs)
            if len(self.calls) == 1:
                return [raw_market("court", volume24hr=5000)]
            return [raw_market("2028", end=NOW + timedelta(days=700), volume24hr=500),
                    raw_market("calme", end=NOW + timedelta(days=200), volume24hr=50),
                    raw_market("court", volume24hr=5000)]
    g = TwoCalls()
    result = scan(g, NOW, dict(CFG, long_pages=2, long_max_horizon_days=900, long_min_volume_24h=100))
    by = {m.market_id: m for m in result.markets}
    assert set(by) == {"court", "2028", "calme"} and g.calls[1]["max_pages"] == 2
    assert by["2028"].eligible and by["2028"].extra_only and not by["court"].extra_only
    assert by["calme"].exclusion == "volume insuffisant"
    assert [m.market_id for m in result.watchlist] == ["court"]  # le marché long n'y entre que s'il est demandé
    assert len(scan(TwoCalls(), NOW, CFG).markets) == 1  # désactivé par défaut


def test_long_markets_are_shown_only_to_the_strategies_that_asked_for_them():
    """Un marché des pages longues (2028…) n'est montré qu'aux stratégies qui l'ont demandé ; les
    marchés échus seulement à celles qui les veulent (`wants_ended`)."""
    from polymarket_agent.main import _visible
    court, long_, echu = (parse_market(raw_market(i)) for i in ("court", "2028", "echu"))
    long_.extra_only, echu.ended = True, True

    class S:
        asked_markets = {"2028"}

    class Other:
        pass

    class Late:
        wants_ended = True
    assert [m.market_id for m in _visible(S(), [court, long_, echu])] == ["court", "2028"]
    assert [m.market_id for m in _visible(Other(), [court, long_, echu])] == ["court"]
    assert [m.market_id for m in _visible(Late(), [court, long_, echu])] == ["court", "echu"]


def test_series_pages_add_esport_totals_seen_only_by_who_asks():
    """23/10/2026 : les matchs d'e-sport ne se trouvent que par série ; `series_pages` ajoute les
    « Games Total » des BO3/BO5, avec les filtres de la série, en `extra_only`."""
    class SeriesGamma(FakeGamma):
        def list_series_events(self, series, end_min):
            if series != 10309:
                raise SourceUnavailable("série en panne (test)")
            tot = raw_market("t", volume24hr=0, liquidityNum=0, sportsMarketType="totals", spread=None)
            del tot["events"]
            win = raw_market("w", sportsMarketType="moneyline")
            bo1 = raw_market("b", sportsMarketType="totals")
            return [{"id": "e1", "slug": "dota2-1win-og", "title": "Dota 2: 1win vs OG (BO3) - PARI", "markets": [tot, win]},
                    {"id": "e2", "title": "Dota 2: A vs B (BO1)", "markets": [bo1]}]
    spec = {"types": ["totals"], "titre_contient": ["(BO3)", "(BO5)"], "min_liquidity": 0, "min_volume_24h": 0, "max_spread": 0.2}
    result = scan(SeriesGamma([raw_market("1")]), NOW, dict(CFG, series_pages={10309: spec, 10310: spec}))
    added = [m for m in result.markets if m.market_id == "t"]
    assert len(result.markets) == 2 and added[0].eligible and added[0].extra_only
    assert added[0].event_title.startswith("Dota 2: 1win vs OG (BO3)") and added[0].event_id == "e1"
    assert [m.market_id for m in result.watchlist] == ["1"]
