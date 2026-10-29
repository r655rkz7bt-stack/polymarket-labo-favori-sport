"""Source : API Gamma de Polymarket (lecture seule, gratuite, sans clé).

Endpoint : GET {base_url}/markets — vérifié le 14/10/2026 par appel réel :
pagination `limit` (100 max) + `offset`, filtres `active`, `closed`,
`end_date_min`, `end_date_max`, tri `order=volume24hr&ascending=false`.
Les champs `outcomes`, `outcomePrices`, `clobTokenIds` sont des chaînes JSON.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from polymarket_agent.http_client import HttpClient
from polymarket_agent.models import FeeSchedule, MarketInfo


def _iso(ts: datetime) -> str:
    return ts.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dec(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def _json_list(value: Any) -> list | None:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            return None
        return parsed if isinstance(parsed, list) else None
    return None


def _date(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def _float_or_none(value) -> float | None:
    try:
        return None if value is None or value == "" else float(value)
    except (TypeError, ValueError):
        return None


def parse_market(raw: dict) -> MarketInfo:
    """Transforme une réponse Gamma en MarketInfo et liste les incohérences."""
    issues: list[str] = []
    outcomes = _json_list(raw.get("outcomes")) or []
    token_ids = [str(t) for t in (_json_list(raw.get("clobTokenIds")) or [])]
    prices_raw = _json_list(raw.get("outcomePrices")) or []
    prices = [_dec(p) for p in prices_raw]
    if len(outcomes) != 2 or len(token_ids) != 2:
        issues.append("marché non binaire ou jetons manquants")
    if len(prices) != len(outcomes) or any(p is None for p in prices):
        issues.append("prix des issues manquants")
        prices = []
    elif prices and abs(sum(prices) - 1) > Decimal("0.05"):
        issues.append("prix des issues incohérents (somme loin de 1)")
    best_bid, best_ask = _dec(raw.get("bestBid")), _dec(raw.get("bestAsk"))
    if best_bid is None or best_ask is None:
        issues.append("meilleur prix achat/vente manquant")
    elif not (0 <= best_bid <= best_ask <= 1):
        issues.append("meilleurs prix incohérents")
    end_date = _date(raw.get("endDate"))
    if end_date is None:
        issues.append("date d'échéance manquante")

    fee_schedule = raw.get("feeSchedule") if isinstance(raw.get("feeSchedule"), dict) else None
    fees_enabled = raw.get("feesEnabled")
    if fees_enabled is True and fee_schedule is None:
        issues.append("frais activés mais barème absent")
    fees = FeeSchedule(
        enabled=fees_enabled if isinstance(fees_enabled, bool) else None,
        rate=_dec(fee_schedule.get("rate")) if fee_schedule else None,
        exponent=_dec(fee_schedule.get("exponent")) if fee_schedule else None,
        # Prudence : sans « takerOnly » explicite, un ordre posé paie les frais taker.
        taker_only=fee_schedule.get("takerOnly") is True if fee_schedule else False,
        fee_type=raw.get("feeType"),
        rebate_rate=_dec(fee_schedule.get("rebateRate")) if fee_schedule else None,
    )
    if fees.enabled is None:
        issues.append("statut des frais inconnu")

    events = raw.get("events") if isinstance(raw.get("events"), list) else []
    event = events[0] if events and isinstance(events[0], dict) else {}
    return MarketInfo(
        market_id=str(raw.get("id", "")),
        condition_id=str(raw.get("conditionId", "")),
        question=str(raw.get("question", "")),
        slug=str(raw.get("slug", "")),
        # Sans événement connu, chaque marché est son propre événement.
        event_id=str(event.get("id") or f"market:{raw.get('id', '')}"),
        event_title=str(event.get("title", "")),
        outcomes=[str(o) for o in outcomes],
        token_ids=token_ids,
        outcome_prices=prices,
        best_bid=best_bid,
        best_ask=best_ask,
        spread=_dec(raw.get("spread")),
        last_trade_price=_dec(raw.get("lastTradePrice")),
        volume_24h=_dec(raw.get("volume24hr")) or Decimal("0"),
        volume_total=_dec(raw.get("volumeNum")) or _dec(raw.get("volume")) or Decimal("0"),
        volume_1wk=_dec(raw.get("volume1wk")) or Decimal("0"),
        price_change_1d=_float_or_none(raw.get("oneDayPriceChange")),
        liquidity=_dec(raw.get("liquidityNum")) or _dec(raw.get("liquidity")) or Decimal("0"),
        end_date=end_date,
        active=raw.get("active") is True,
        closed=raw.get("closed") is not False,
        accepting_orders=raw.get("acceptingOrders") is True,
        enable_order_book=raw.get("enableOrderBook") is True,
        fees=fees,
        min_order_size=_dec(raw.get("orderMinSize")) or Decimal("5"),
        tick_size=_dec(raw.get("orderPriceMinTickSize")) or Decimal("0.01"),
        neg_risk=raw.get("negRisk") is True,
        issues=issues,
        resolution_source=str(raw.get("resolutionSource") or ""),
        description=str(raw.get("description") or ""),
        game_start=_date(raw.get("gameStartTime")),
        sports_market_type=str(raw["sportsMarketType"]) if raw.get("sportsMarketType") else None,
        holding_rewards=raw.get("holdingRewardsEnabled") is True,
        group_title=str(raw.get("groupItemTitle") or ""),
        created_at=_date(raw.get("createdAt")),
        event_slug=str(((raw.get("events") or [{}])[0] or {}).get("slug") or "") if isinstance(raw.get("events"), list) else "",
        neg_risk_other=raw.get("negRiskOther") is True,
        uma_statuses=_statuses(raw.get("umaResolutionStatuses")),
        rewards_min_size=_dec(raw.get("rewardsMinSize")),
        rewards_max_spread=_dec(raw.get("rewardsMaxSpread")),
        rewards_daily_rate=sum((_dec(r.get("rewardsDailyRate")) or Decimal("0")
                                for r in (raw.get("clobRewards") or []) if isinstance(r, dict)), Decimal("0")),
    )


def _statuses(raw) -> tuple[str, ...]:
    """umaResolutionStatuses : liste JSON (souvent encodée en texte) ; vide si illisible."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return ()
    return tuple(str(s).lower() for s in raw) if isinstance(raw, list) else ()


class GammaClient:
    def __init__(self, http: HttpClient, base_url: str = "https://gamma-api.polymarket.com"):
        self.http = http
        self.base_url = base_url.rstrip("/")

    PARALLEL_PAGES = 4  # pages lues en même temps (19/10/2026 : 15 pages une à une = 16 s par cycle)

    def list_markets(self, *, end_min: datetime, end_max: datetime, page_size: int = 100,
                     max_pages: int = 3, tag_id: int | str | None = None) -> list[dict]:
        """Marchés actifs triés par volume, page par page ; les pages sont lues par groupes de
        PARALLEL_PAGES en même temps, et la lecture s'arrête à la première page incomplète.
        `tag_id` (21/10/2026) : seulement les marchés d'une étiquette (ex. 100343 « Mentions »)."""
        from concurrent.futures import ThreadPoolExecutor

        def page(n: int) -> list:
            params = {"active": "true", "closed": "false", "limit": page_size,
                      "offset": n * page_size, "order": "volume24hr", "ascending": "false",
                      "end_date_min": _iso(end_min), "end_date_max": _iso(end_max)}
            if tag_id is not None:
                params["tag_id"] = str(tag_id)
            batch = self.http.get_json(f"{self.base_url}/markets", params=params)
            if not isinstance(batch, list):
                raise ValueError("Gamma : liste attendue")
            return batch

        markets: list[dict] = []
        with ThreadPoolExecutor(max_workers=self.PARALLEL_PAGES) as pool:
            for start in range(0, max_pages, self.PARALLEL_PAGES):
                for batch in pool.map(page, range(start, min(start + self.PARALLEL_PAGES, max_pages))):
                    markets.extend(m for m in batch if isinstance(m, dict))
                    if len(batch) < page_size:
                        return markets
        return markets

    def list_series_events(self, series_id: int | str, *, end_min: datetime, limit: int = 100) -> list[dict]:
        """Événements ouverts d'une série de sport (23/10/2026) : les matchs d'e-sport (CS2 10310,
        LoL 10311, Dota 2 10309, Valorant 10369) ne se trouvent pas par étiquette, seulement par série
        (docs/ROUTINE_CHERCHEUR.md, astuces). Chaque événement porte ses marchés (`markets`)."""
        rows = self.http.get_json(f"{self.base_url}/events", params={
            "series_id": str(series_id), "closed": "false", "end_date_min": _iso(end_min), "limit": limit})
        if not isinstance(rows, list):
            raise ValueError("Gamma : liste attendue")
        return [r for r in rows if isinstance(r, dict)]

    def get_market(self, market_id: str) -> dict:
        return self.http.get_json(f"{self.base_url}/markets/{market_id}")

    def markets_by_ids(self, market_ids: list[str], *, closed: bool) -> list[dict]:
        """Plusieurs marchés en une requête (21/10/2026, règlements). Gamma ne rend que les marchés
        fermés (`closed=True`) ou que les ouverts : appeler les deux pour tout couvrir. Mêmes champs
        que `get_market` (comparé sur 60 marchés réels)."""
        params = [("id", str(m)) for m in market_ids] + [("closed", "true" if closed else "false"),
                                                          ("limit", str(max(1, len(market_ids))))]
        rows = self.http.get_json(f"{self.base_url}/markets", params=params)
        if not isinstance(rows, list):
            raise ValueError("Gamma : liste attendue")
        return rows
