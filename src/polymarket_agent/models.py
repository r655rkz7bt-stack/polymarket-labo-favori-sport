"""Structures de données partagées (marchés, carnets d'ordres)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal


@dataclass(frozen=True)
class FeeSchedule:
    enabled: bool | None  # None = inconnu
    rate: Decimal | None
    exponent: Decimal | None
    taker_only: bool = False  # True seulement si le barème le dit (ordres posés sans frais)
    fee_type: str | None = None
    # Part des frais des preneurs reversée aux ordres posés (docs Polymarket, 16/10/2026 : sport
    # 0,15, crypto 0,20, autres 0,25). Information seulement : jamais comptée dans le capital.
    rebate_rate: Decimal | None = None


@dataclass
class MarketInfo:
    market_id: str
    condition_id: str
    question: str
    slug: str
    event_id: str
    event_title: str
    outcomes: list[str]
    token_ids: list[str]
    outcome_prices: list[Decimal]
    best_bid: Decimal | None  # du jeton n°0 (en général « Yes »)
    best_ask: Decimal | None
    spread: Decimal | None
    last_trade_price: Decimal | None
    volume_24h: Decimal
    volume_total: Decimal  # volume cumulé depuis l'ouverture
    liquidity: Decimal
    end_date: datetime | None
    active: bool
    closed: bool
    accepting_orders: bool
    enable_order_book: bool
    fees: FeeSchedule
    min_order_size: Decimal
    tick_size: Decimal
    neg_risk: bool
    issues: list[str] = field(default_factory=list)
    # Ce qui tranchera le marché (Gamma : resolutionSource, description).
    resolution_source: str = ""
    description: str = ""
    eligible: bool = False
    exclusion: str = ""
    # Récompenses de liquidité (Gamma : rewardsMinSize, rewardsMaxSpread en centimes,
    # clobRewards[].rewardsDailyRate en $ par jour), lues mais plus estimées depuis le 29/10/2026.
    rewards_min_size: Decimal | None = None
    rewards_max_spread: Decimal | None = None
    rewards_daily_rate: Decimal = Decimal("0")
    game_start: datetime | None = None  # sport : coup d'envoi (Gamma gameStartTime) ; l'échéance n'est pas fiable
    # Sport : type de pari (Gamma sportsMarketType : "moneyline" = vainqueur, "totals", "spreads",
    # "child_moneyline" = une manche/carte…) ; None hors sport ou si Gamma ne le dit pas.
    sports_market_type: str | None = None
    holding_rewards: bool = False  # Gamma holdingRewardsEnabled : récompense de détention (4 %/an)
    # 20/10/2026 : libellé de l'issue dans son événement (« 50+ bps »,
    # « 200-219 »…), création du marché, adresse de l'événement, issue « Autre » d'un événement
    # augmenté, états de la résolution UMA (« proposed », « disputed »…).
    group_title: str = ""
    created_at: datetime | None = None
    event_slug: str = ""
    neg_risk_other: bool = False
    uma_statuses: tuple[str, ...] = ()
    # Volume des 7 derniers jours et variation du prix du OUI sur 24 h (Gamma volume1wk, oneDayPriceChange).
    volume_1wk: Decimal = Decimal("0")
    price_change_1d: float | None = None

    @property
    def data_ok(self) -> bool:
        return not self.issues


@dataclass(frozen=True)
class BookLevel:
    price: Decimal
    size: Decimal


@dataclass
class OrderBook:
    token_id: str
    bids: list[BookLevel]  # triés du meilleur (plus haut) au moins bon
    asks: list[BookLevel]  # triés du meilleur (plus bas) au moins bon
    timestamp: datetime | None  # horodatage fourni par le CLOB
    fetched_at: datetime
    tick_size: Decimal
    min_order_size: Decimal
    last_trade_price: Decimal | None = None

    @property
    def best_bid(self) -> Decimal | None:
        return self.bids[0].price if self.bids else None

    @property
    def best_ask(self) -> Decimal | None:
        return self.asks[0].price if self.asks else None

    @property
    def mid(self) -> Decimal | None:
        if self.best_bid is None or self.best_ask is None:
            return None
        return (self.best_bid + self.best_ask) / 2
