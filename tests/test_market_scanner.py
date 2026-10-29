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
