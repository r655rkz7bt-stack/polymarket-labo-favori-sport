"""Récompenses de liquidité de Polymarket : estimation pour nos ordres POSÉS (16/10/2026).

Formule officielle (docs.polymarket.com/programs/liquidity-rewards.md, lue le 16/10/2026) :
- score d'un ordre : S = ((v − s) / v)² × taille, v = écart maximal du marché (centimes,
  Gamma `rewardsMaxSpread`), s = distance de l'ordre au prix du milieu (centimes) ; 0 si s ≥ v ou
  si la taille est sous `rewardsMinSize` ;
- côté 1 = achats du OUI (+ ventes du NON) ; côté 2 = ventes du OUI (+ achats du NON) ;
- Q_min = max(min(Q1, Q2), max(Q1, Q2) / 3) si le milieu est entre 0,10 et 0,90 (un seul côté
  compte pour un tiers), sinon min(Q1, Q2) (les deux côtés sont obligatoires) ;
- photo du carnet chaque minute ; part du jour = notre Q_min / total ; cagnotte du marché
  (`clobRewards[].rewardsDailyRate`, $ par jour) ; versement si au moins 1 $ par jour.
Vérifié le 16/10/2026 : le carnet du NON est le MIROIR de celui du OUI (vente du OUI à p = achat
du NON à 1 − p, mêmes ordres) : les concurrents ne sont comptés qu'une fois, sur le carnet du OUI.

LIMITES (affichées avec le résultat) : une photo par cycle (toutes les 2 minutes, `cycle.bucket_minutes`) au lieu de chaque minute ;
milieu simple au lieu du « milieu ajusté » officiel ; multiplicateur « en match » ignoré. C'est une
ESTIMATION : seul le journal signé fait foi.

Décisions d'Etienne (17/10/2026) :
- option A : l'arène CLASSE chaque stratégie avec ses récompenses estimées (journées terminées,
  au moins 1 $, plafonnées à la cagnotte, × prudence), SANS les mettre dans son capital fantôme ;
- le capital PUBLIC (portefeuille principal), et lui seul, les reçoit dans son journal signé
  (écriture REWARD, une par journée terminée) dès que le noyau l'accepte
  (proposals/2026-10-17_recompenses_au_capital.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from polymarket_agent.models import MarketInfo, OrderBook

SCALING = 3.0  # « c », actuellement sur tous les marchés (docs, 16/10/2026)
PAYOUT_MIN = 1.0  # $ par jour


def order_score(price: float, mid: float, size: float, max_spread_cents: float, min_size: float) -> float:
    s = abs(mid - price) * 100
    if max_spread_cents <= 0 or s >= max_spread_cents or size < min_size:
        return 0.0
    return ((max_spread_cents - s) / max_spread_cents) ** 2 * size


def q_min(q1: float, q2: float, mid: float) -> float:
    if 0.10 <= mid <= 0.90:
        return max(min(q1, q2), max(q1, q2) / SCALING)
    return min(q1, q2)


@dataclass(frozen=True)
class Quote:
    """Un de nos ordres posés, ramené au carnet du OUI : `side` 1 = achat du OUI (ou vente du NON),
    2 = vente du OUI (ou achat du NON) ; `yes_price` = son prix exprimé en OUI."""
    side: int
    yes_price: float
    size: float


def to_quote(market: MarketInfo, token_id: str, order_side: str, price: float, size: float) -> Quote | None:
    if len(market.token_ids) != 2 or token_id not in market.token_ids:
        return None
    is_yes = token_id == market.token_ids[0]
    if is_yes:
        return Quote(1 if order_side == "BUY" else 2, price, size)
    return Quote(2 if order_side == "BUY" else 1, 1 - price, size)


@dataclass(frozen=True)
class Estimate:
    share: float          # notre part du score du marché (0 à 1)
    ours: float
    others: float
    amount: float         # $ estimés pour la durée donnée
    eligible: bool        # au moins un ordre dans l'écart et à la bonne taille


def estimate(market: MarketInfo, yes_book: OrderBook, quotes: list[Quote], minutes: float) -> Estimate | None:
    """Notre part de la cagnotte du marché pendant `minutes` (concurrents = carnet du OUI)."""
    v = float(market.rewards_max_spread or 0)
    rate = float(market.rewards_daily_rate or 0)
    min_size = float(market.rewards_min_size or 0)
    if v <= 0 or rate <= 0 or yes_book.best_bid is None or yes_book.best_ask is None or not quotes:
        return None
    mid = float(yes_book.best_bid + yes_book.best_ask) / 2
    o1 = sum(order_score(float(l.price), mid, float(l.size), v, min_size) for l in yes_book.bids)
    o2 = sum(order_score(float(l.price), mid, float(l.size), v, min_size) for l in yes_book.asks)
    m1 = sum(order_score(q.yes_price, mid, q.size, v, min_size) for q in quotes if q.side == 1)
    m2 = sum(order_score(q.yes_price, mid, q.size, v, min_size) for q in quotes if q.side == 2)
    ours, others = q_min(m1, m2, mid), q_min(o1, o2, mid)
    share = ours / (ours + others) if ours > 0 else 0.0
    return Estimate(round(share, 6), round(ours, 3), round(others, 3),
                    round(share * rate * minutes / 1440, 6), ours > 0)


SCHEMA = """
CREATE TABLE IF NOT EXISTS recompenses_estimees (
    cycle_id TEXT, estime_at TEXT, portefeuille TEXT, market_id TEXT,
    part REAL, montant REAL, cagnotte_jour REAL,
    PRIMARY KEY (cycle_id, portefeuille, market_id)
);
"""


def record(db, cycle_id: str, now, portfolio: str, market_id: str, est: Estimate, daily_rate: Decimal) -> None:
    db.conn.executescript(SCHEMA)
    db.conn.execute("INSERT OR REPLACE INTO recompenses_estimees VALUES (?,?,?,?,?,?,?)",
                    (cycle_id, now.isoformat(timespec="seconds"), portfolio, market_id, est.share,
                     est.amount, float(daily_rate)))
    db.conn.commit()


def daily_totals(db, since_iso: str) -> dict[str, dict[str, float]]:
    """{portefeuille: {jour: $ estimés}} ; un jour sous 1 $ n'est pas versé par Polymarket."""
    db.conn.executescript(SCHEMA)
    out: dict[str, dict[str, float]] = {}
    for p, day, total in db.conn.execute(
            "SELECT portefeuille, substr(estime_at, 1, 10), SUM(montant) FROM recompenses_estimees "
            "WHERE estime_at >= ? GROUP BY 1, 2 ORDER BY 2", (since_iso,)):
        out.setdefault(p, {})[day] = round(total, 4)
    return out


def summary(db, since_iso: str) -> dict[str, dict[str, float]]:
    """Par portefeuille : total estimé, total « versable » (jours ≥ 1 $), nombre de jours."""
    out = {}
    for p, days in daily_totals(db, since_iso).items():
        out[p] = {"estime": round(sum(days.values()), 2),
                  "versable": round(sum(v for v in days.values() if v >= PAYOUT_MIN), 2),
                  "jours": len(days)}
    return out


def payable_days(db, since_iso: str, before: date) -> dict[str, dict[str, list[dict]]]:
    """{portefeuille: {jour: [{market_id, montant, cagnotte}]}} des journées TERMINÉES (avant
    `before`) dont le total atteint 1 $ (règle de versement de Polymarket). Le montant d'un
    marché ne dépasse jamais sa cagnotte du jour."""
    db.conn.executescript(SCHEMA)
    raw: dict[str, dict[str, list[dict]]] = {}
    for p, day, market, amount, pool in db.conn.execute(
            "SELECT portefeuille, substr(estime_at, 1, 10), market_id, SUM(montant), MAX(cagnotte_jour) "
            "FROM recompenses_estimees WHERE estime_at >= ? AND substr(estime_at, 1, 10) < ? "
            "GROUP BY 1, 2, 3 ORDER BY 2, 3", (since_iso, before.isoformat())):
        amount = min(float(amount or 0), float(pool or 0))
        if amount > 0:
            raw.setdefault(p, {}).setdefault(day, []).append(
                {"market_id": market, "montant": round(amount, 6), "cagnotte": round(float(pool), 4)})
    return {p: {d: ms for d, ms in days.items() if sum(m["montant"] for m in ms) >= PAYOUT_MIN}
            for p, days in raw.items()}


def ranking_bonus(db, starts: dict[str, datetime | None], prudence: float, now: datetime) -> dict[str, float]:
    """Option A : {nom de l'arène: récompenses estimées comptées pour le CLASSEMENT}, depuis le début
    de l'époque de chaque fantôme (base : « fantome:<nom> »). Jamais ajouté à un capital."""
    known = [s for s in starts.values() if s is not None]
    if not known or prudence <= 0:
        return {}
    days = payable_days(db, min(known).isoformat(timespec="seconds"), now.date())
    out = {}
    for name, start in starts.items():
        mine = days.get("fantome:" + name, {})
        total = sum(m["montant"] for d, ms in mine.items() if start is None or d >= start.date().isoformat()
                    for m in ms)
        if total > 0:
            out[name] = round(total * prudence, 4)
    return out


def payable_day_counts(db, starts: dict[str, datetime | None], now: datetime) -> dict[str, int]:
    """{nom de l'arène: journées terminées de récompenses versables (≥ 1 $)} depuis le début de chaque
    fantôme : sert au mode « recompenses » de l'arène (la tenue de marché clôture peu d'opérations)."""
    known = [s for s in starts.values() if s is not None]
    if not known:
        return {}
    days = payable_days(db, min(known).isoformat(timespec="seconds"), now.date())
    return {name: n for name, start in starts.items()
            if (n := sum(1 for d in days.get("fantome:" + name, {}) if start is None or d >= start.date().isoformat()))}
