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


# Exclusions qu'un seuil propre à une étiquette peut lever (liquidité, volume, écart, prix extrême) ; jamais un marché fermé ou incohérent.
RELAXABLE = {"liquidité insuffisante", "volume insuffisant", "écart achat/vente trop large", "issue quasi certaine (match joué ou info déjà connue)"}


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


def _add_holding_markets(gamma: GammaClient, now: datetime, cfg: dict, result: ScanResult, seen: set[str]) -> None:
    """Marchés LONGS à récompense de détention (19/10/2026) : l'horizon normal (30 jours) les
    écartait tous (la plupart se règlent en 2027-2028), parking_politique ne pouvait rien acheter.
    `holding_pages` pages de plus, triées par volume, jusqu'à `holding_max_horizon_days` ; seuls
    les marchés éligibles à la récompense sont gardés, avec les mêmes filtres que les autres."""
    pages = int(cfg.get("holding_pages", 0) or 0)
    if pages <= 0:
        return
    days = float(cfg.get("holding_max_horizon_days", 900))
    long_cfg = dict(cfg, max_horizon_hours=days * 24)
    try:
        raw = gamma.list_markets(end_min=now + timedelta(hours=cfg["max_horizon_hours"]),
                                 end_max=now + timedelta(days=days), page_size=cfg["page_size"], max_pages=pages)
    except Exception as exc:  # noqa: BLE001 - un complément ne doit jamais bloquer le scan normal
        log.warning("scanner : marchés à récompense de détention indisponibles (%s)", exc)
        return
    for item in raw:
        market = parse_market(item)
        if market.market_id in seen or not market.holding_rewards:
            continue
        seen.add(market.market_id)
        market.exclusion = classify(market, now, long_cfg)
        market.eligible = not market.exclusion
        if market.exclusion:
            result.excluded[market.exclusion] = result.excluded.get(market.exclusion, 0) + 1
        result.markets.append(market)


def _add_long_markets(gamma: GammaClient, now: datetime, cfg: dict, result: ScanResult, seen: set[str]) -> None:
    """Marchés LONGS, toutes sortes (21/10/2026, « réveiller les muettes ») : au-delà de l'horizon
    normal (30 jours), le scanner ne lisait que les marchés à récompense de détention ; échelle de
    dates, implication politique, échelles de seuils et jumeaux ne voyaient donc presque rien
    (diagnostic du 21/10 : echelle_dates 12 marchés au lieu de 433, implication_politique 0 au lieu
    de 60 ; experiments/muettes.md). `long_pages` pages triées par volume jusqu'à
    `long_max_horizon_days`, mêmes filtres sauf le volume du jour (`long_min_volume_24h` : un marché
    de 2028 s'échange peu chaque jour). Ces marchés n'entrent PAS dans la liste de surveillance de
    base (`extra_only`) : seulement si une stratégie les demande."""
    pages = int(cfg.get("long_pages", 0) or 0)
    if pages <= 0:
        return
    days = float(cfg.get("long_max_horizon_days", 900))
    long_cfg = dict(cfg, max_horizon_hours=days * 24,
                    min_volume_24h=cfg.get("long_min_volume_24h", cfg.get("min_volume_24h", 0)))
    try:
        raw = gamma.list_markets(end_min=now + timedelta(hours=cfg["max_horizon_hours"]),
                                 end_max=now + timedelta(days=days), page_size=cfg["page_size"], max_pages=pages)
    except Exception as exc:  # noqa: BLE001 - un complément ne doit jamais bloquer le scan normal
        log.warning("scanner : marchés longs indisponibles (%s)", exc)
        return
    for item in raw:
        market = parse_market(item)
        if market.market_id in seen:
            continue
        seen.add(market.market_id)
        market.exclusion = classify(market, now, long_cfg)
        market.eligible = not market.exclusion
        market.extra_only = True
        if market.exclusion:
            result.excluded[market.exclusion] = result.excluded.get(market.exclusion, 0) + 1
        result.markets.append(market)


def _add_tag_markets(gamma: GammaClient, now: datetime, cfg: dict, result: ScanResult, seen: set[str]) -> None:
    """Marchés d'une étiquette précise (21/10/2026) : les marchés « mentions » (étiquette 100343)
    ont peu de volume et n'entraient presque jamais dans les pages triées par volume ;
    mots_prononces (retenue sur le passé) ne voyait aucun marché. `tag_pages: {étiquette: pages}` ou
    `{étiquette: {pages, min_liquidity, ...}}` : filtres propres à l'étiquette (ces marchés ont
    rarement 1 000 $ de liquidité ; le carnet réel décide de toute façon de ce qui s'exécute). Une étiquette peut aussi
    fixer son propre `max_horizon_hours` (24/10/2026 : compteurs annuels de SpaceX, fin dans 68 jours)."""
    by_id = {m.market_id: m for m in result.markets}
    for tag, spec in (cfg.get("tag_pages") or {}).items():
        spec = dict(spec) if isinstance(spec, dict) else {"pages": spec}
        pages = int(spec.pop("pages", 0) or 0)
        if pages <= 0:
            continue
        tag_cfg = dict(cfg, **spec)
        try:
            raw = gamma.list_markets(end_min=now, end_max=now + timedelta(hours=tag_cfg["max_horizon_hours"]),
                                     page_size=cfg["page_size"], max_pages=pages, tag_id=tag)
        except Exception as exc:  # noqa: BLE001 - un complément ne doit jamais bloquer le scan normal
            log.warning("scanner : marchés de l'étiquette %s indisponibles (%s)", tag, exc)
            continue
        for item in raw:
            market = parse_market(item)
            if market.market_id in seen:
                # 24/10/2026 : un marché déjà lu dans les pages normales y a été jugé avec les seuils GÉNÉRAUX (liquidité 1 000 $, écart 0,05) ; les seuils propres
                # à l'étiquette (séismes : 200 $, écart 0,15) ne s'appliquaient qu'aux marchés vus pour la première fois par l'étiquette.
                known = by_id.get(market.market_id)
                if known is not None and not known.eligible and known.exclusion in RELAXABLE and not classify(known, now, tag_cfg):
                    left = result.excluded.get(known.exclusion, 0) - 1
                    result.excluded[known.exclusion] = left
                    if left <= 0:
                        del result.excluded[known.exclusion]
                    known.exclusion, known.eligible = "", True
                continue
            seen.add(market.market_id)
            by_id[market.market_id] = market
            market.exclusion = classify(market, now, tag_cfg)
            market.eligible = not market.exclusion
            if market.exclusion:
                result.excluded[market.exclusion] = result.excluded.get(market.exclusion, 0) + 1
            result.markets.append(market)


def _add_series_markets(gamma: GammaClient, now: datetime, cfg: dict, result: ScanResult, seen: set[str]) -> None:
    """Marchés d'une SÉRIE de sport (23/10/2026, piste « moins de cartes » en e-sport,
    experiments/pistes_sport_coherence.md) : `series_pages: {série: {types, titre_contient, ...}}`.
    Ces marchés s'échangent souvent moins de 1 000 $ par jour et n'entraient jamais dans le scan ;
    seuls les types demandés (`types`, Gamma sportsMarketType) des événements dont le titre contient
    l'un des `titre_contient` sont gardés, avec les filtres propres à la série. Comme les marchés
    longs, ils ne sont vus que par les stratégies qui les demandent (`extra_only`)."""
    for series, spec in (cfg.get("series_pages") or {}).items():
        spec = dict(spec or {})
        types = set(spec.pop("types", []) or [])
        words = list(spec.pop("titre_contient", []) or [])
        series_cfg = dict(cfg, **spec)
        try:
            events = gamma.list_series_events(series, end_min=now)
        except Exception as exc:  # noqa: BLE001 - un complément ne doit jamais bloquer le scan normal
            log.warning("scanner : série %s indisponible (%s)", series, exc)
            continue
        for event in events:
            title = str(event.get("title") or "")
            if words and not any(w in title for w in words):
                continue
            for item in event.get("markets") or []:
                if not isinstance(item, dict) or (types and item.get("sportsMarketType") not in types):
                    continue
                item = dict(item, events=[{"id": event.get("id"), "slug": event.get("slug"), "title": title}])
                if item.get("spread") is None and item.get("bestAsk") is not None and item.get("bestBid") is not None:
                    item["spread"] = float(item["bestAsk"]) - float(item["bestBid"])
                market = parse_market(item)
                if market.market_id in seen:
                    continue
                seen.add(market.market_id)
                market.exclusion = classify(market, now, series_cfg)
                market.eligible = not market.exclusion
                market.extra_only = True
                if market.exclusion:
                    result.excluded[market.exclusion] = result.excluded.get(market.exclusion, 0) + 1
                result.markets.append(market)


def _add_ended_markets(gamma: GammaClient, now: datetime, cfg: dict, result: ScanResult, seen: set[str]) -> None:
    """Marchés dont l'échéance vient de PASSER (20/10/2026, stratégie apres_echeance) : encore
    échangés en attendant la résolution officielle. `ended_pages` pages, échéance entre
    `ended_max_hours` et `ended_min_hours` heures dans le passé, mêmes filtres sinon."""
    pages = int(cfg.get("ended_pages", 0) or 0)
    if pages <= 0:
        return
    oldest, newest = float(cfg.get("ended_max_hours", 72)), float(cfg.get("ended_min_hours", 1))
    past_cfg = dict(cfg, min_horizon_minutes=-oldest * 60, max_horizon_hours=-newest)
    try:
        raw = gamma.list_markets(end_min=now - timedelta(hours=oldest), end_max=now - timedelta(hours=newest),
                                 page_size=cfg["page_size"], max_pages=pages)
    except Exception as exc:  # noqa: BLE001 - un complément ne doit jamais bloquer le scan normal
        log.warning("scanner : marchés échus indisponibles (%s)", exc)
        return
    for item in raw:
        market = parse_market(item)
        if market.market_id in seen:
            continue
        seen.add(market.market_id)
        market.exclusion = classify(market, now, past_cfg)
        market.eligible = not market.exclusion
        market.ended = True
        if market.exclusion:
            result.excluded[market.exclusion] = result.excluded.get(market.exclusion, 0) + 1
        result.markets.append(market)


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
    _add_holding_markets(gamma, now, cfg, result, seen)
    _add_long_markets(gamma, now, cfg, result, seen)
    _add_tag_markets(gamma, now, cfg, result, seen)
    _add_series_markets(gamma, now, cfg, result, seen)
    _add_ended_markets(gamma, now, cfg, result, seen)
    eligible = sorted((m for m in result.markets if m.eligible and not m.ended and not m.extra_only),
                      key=lambda m: m.volume_24h, reverse=True)
    result.watchlist = eligible[: cfg["watchlist_size"]]
    log.info("scanner : %d marchés, %d exploitables, %d surveillés, exclusions %s",
             len(result.markets), len(eligible), len(result.watchlist), result.excluded)
    return result
