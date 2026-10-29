"""Market Scanner : découvre les marchés, détecte les incohérences, choisit la liste de surveillance."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from polymarket_agent.models import MarketInfo
from polymarket_agent.sources.polymarket_gamma import GammaClient, parse_market

log = logging.getLogger(__name__)


@dataclass
class ScanResult:
    markets: list[MarketInfo] = field(default_factory=list)
    watchlist: list[MarketInfo] = field(default_factory=list)
    excluded: dict[str, int] = field(default_factory=dict)


def classify(market: MarketInfo, now: datetime, cfg: dict) -> str:
    """Retourne '' si le marché est exploitable, sinon la raison d'exclusion."""
    if market.issues:
        return "données incohérentes"
    if not market.active or market.closed:
        return "inactif ou fermé"
    if not market.enable_order_book or not market.accepting_orders:
        return "carnet indisponible"
    end_min = now + timedelta(minutes=cfg["min_horizon_minutes"])
    end_max = now + timedelta(hours=cfg["max_horizon_hours"])
    if market.end_date is None or not (end_min <= market.end_date <= end_max):
        return "hors horizon"
    if market.liquidity < Decimal(str(cfg["min_liquidity"])):
        return "liquidité insuffisante"
    if market.volume_24h < Decimal(str(cfg["min_volume_24h"])):
        return "volume insuffisant"
    if market.spread is None or market.spread > Decimal(str(cfg["max_spread"])):
        return "écart achat/vente trop large"
    extreme = Decimal(str(cfg.get("max_favorite_price", 0.99)))
    if market.best_ask >= extreme or market.best_bid <= 1 - extreme:
        return "issue quasi certaine (match joué ou info déjà connue)"
    return ""


def scan(gamma: GammaClient, now: datetime, cfg: dict) -> ScanResult:
    raw = gamma.list_markets(
        end_min=now + timedelta(minutes=cfg["min_horizon_minutes"]),
        end_max=now + timedelta(hours=cfg["max_horizon_hours"]),
        page_size=cfg["page_size"], max_pages=cfg["max_pages"],
    )
    result = ScanResult()
    seen: set[str] = set()
    for item in raw:
        market = parse_market(item)
        if market.market_id in seen:
            continue
        seen.add(market.market_id)
        market.exclusion = classify(market, now, cfg)
        market.eligible = not market.exclusion
        if market.exclusion:
            result.excluded[market.exclusion] = result.excluded.get(market.exclusion, 0) + 1
        result.markets.append(market)
    eligible = sorted((m for m in result.markets if m.eligible),
                      key=lambda m: m.volume_24h, reverse=True)
    result.watchlist = eligible[: cfg["watchlist_size"]]
    log.info("scanner : %d marchés, %d exploitables, %d surveillés, exclusions %s",
             len(result.markets), len(eligible), len(result.watchlist), result.excluded)
    return result
