"""Source : CLOB public de Polymarket (lecture seule, gratuite, sans clé).

Endpoints vérifiés le 14/10/2026 par appel réel :
  GET  {base}/book?token_id=...      carnet d'un jeton
  POST {base}/books  [{"token_id"}]  plusieurs carnets
Le CLOB renvoie les offres d'achat (bids) en ordre croissant et les offres de
vente (asks) en ordre décroissant : on les retrie ici, meilleur prix en premier.
Aucune fonction de ce module ne passe d'ordre : il n'y a pas de clé, pas de
portefeuille, et le mode réel n'existe pas.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from polymarket_agent.http_client import HttpClient
from polymarket_agent.models import BookLevel, OrderBook


def _dec(value: Any) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _levels(raw: Any) -> list[BookLevel]:
    levels = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        price, size = _dec(item.get("price")), _dec(item.get("size"))
        if price is None or size is None or not (0 < price < 1) or size <= 0:
            continue
        levels.append(BookLevel(price, size))
    return levels


def parse_book(raw: dict, fetched_at: datetime) -> OrderBook:
    ts = None
    stamp = raw.get("timestamp")
    if stamp is not None and str(stamp).isdigit():
        ts = datetime.fromtimestamp(int(stamp) / 1000, tz=timezone.utc)
    return OrderBook(
        token_id=str(raw.get("asset_id", "")),
        bids=sorted(_levels(raw.get("bids")), key=lambda lvl: lvl.price, reverse=True),
        asks=sorted(_levels(raw.get("asks")), key=lambda lvl: lvl.price),
        timestamp=ts,
        fetched_at=fetched_at,
        tick_size=_dec(raw.get("tick_size")) or Decimal("0.01"),
        min_order_size=_dec(raw.get("min_order_size")) or Decimal("5"),
        last_trade_price=_dec(raw.get("last_trade_price")),
    )


class ClobClient:
    def __init__(self, http: HttpClient, base_url: str = "https://clob.polymarket.com",
                 batch_size: int = 20, clock=None):
        self.http = http
        self.base_url = base_url.rstrip("/")
        self.batch_size = batch_size
        self._clock = clock or (lambda: datetime.now(timezone.utc))

    def get_book(self, token_id: str) -> OrderBook:
        raw = self.http.get_json(f"{self.base_url}/book", params={"token_id": token_id})
        return parse_book(raw, self._clock())

    def get_books(self, token_ids: list[str]) -> dict[str, OrderBook]:
        books: dict[str, OrderBook] = {}
        for start in range(0, len(token_ids), self.batch_size):
            chunk = token_ids[start:start + self.batch_size]
            raw = self.http.post_json(f"{self.base_url}/books", [{"token_id": t} for t in chunk])
            fetched = self._clock()
            for item in raw if isinstance(raw, list) else []:
                if isinstance(item, dict):
                    book = parse_book(item, fetched)
                    books[book.token_id] = book
        return books
