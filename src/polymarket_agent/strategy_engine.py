"""Strategy Engine : la stratégie PROPOSE, le contrôleur de sécurité décide.

Chaque stratégie a un identifiant, une version, une date de création et des paramètres bornés
(config/strategies.yaml). Laboratoire dédié : la seule classe est « naive_favorite » (favori), jouée
par nf_sport_patiente_prix_850_875 avec un filtre (`Filtered`) et le mode patient (`PatientOrders`).
"""

from __future__ import annotations

import dataclasses
import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from polymarket_agent.fees import UnknownFees, taker_fee
from polymarket_agent.ledger import LedgerState
from polymarket_agent.models import MarketInfo, OrderBook
from polymarket_agent.portfolio_manager import size_buy
from polymarket_agent.safety_controller import Proposal, RiskLimits

log = logging.getLogger(__name__)
ONE = Decimal("1")


class StrategyConfigError(Exception):
    pass


def make_op_id(bucket: str, strategy_id: str, version: str, token_id: str, side: str) -> str:
    """Identifiant déterministe : relancer le même cycle redonne le même identifiant,
    donc l'opération ne peut pas être comptabilisée deux fois."""
    raw = f"{bucket}|{strategy_id}|{version}|{token_id}|{side}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def load_params(spec: dict) -> dict[str, Decimal]:
    """Lit les paramètres et vérifie qu'ils restent dans leurs bornes."""
    params: dict[str, Decimal] = {}
    for name, item in (spec or {}).items():
        try:
            value, low, high = (Decimal(str(item[k])) for k in ("value", "min", "max"))
        except (KeyError, TypeError, ArithmeticError) as exc:
            raise StrategyConfigError(f"paramètre {name} mal formé") from exc
        if not (low <= value <= high):
            raise StrategyConfigError(f"paramètre {name}={value} hors des bornes [{low}, {high}]")
        params[name] = value
    return params


@dataclass
class StrategyOutput:
    proposals: list[Proposal] = field(default_factory=list)
    # Ordres à cours limité à POSER dans le carnet (max_price = prix limite). Exécutés plus tard,
    # seulement si le marché passe au travers du prix (execution_simulator.simulate_resting_buy).
    limit_orders: list[Proposal] = field(default_factory=list)
    abstentions: dict[str, int] = field(default_factory=dict)

    def abstain(self, reason: str) -> None:
        self.abstentions[reason] = self.abstentions.get(reason, 0) + 1


class NaiveFavorite:
    """Acheter le favori (prix 0,80–0,95) d'un marché à deux issues.

    Probabilité estimée = milieu du carnet + biais a priori (NON calibré), avec un intervalle
    ± incertitude. L'avantage est calculé sur la borne basse, après frais, au prix exécutable (pas
    au dernier prix affiché). Mise : Kelly × `kelly_fraction` sur cette borne basse.
    """

    strategy_id = "naive_favorite"
    required = ("min_ask", "max_ask", "bias", "uncertainty", "min_net_edge",
                "max_extra_ticks", "max_proposals_per_cycle")

    def __init__(self, version: str, created_at: str, params: dict[str, Decimal]):
        missing = [p for p in self.required if p not in params]
        if missing:
            raise StrategyConfigError(f"{self.strategy_id} : paramètres manquants {missing}")
        self.version = version
        self.created_at = created_at
        self.params = params

    def propose(self, watchlist: list[MarketInfo], books: dict[str, OrderBook],
                state: LedgerState, limits: RiskLimits, bucket: str,
                marks: dict[str, Decimal] | None = None) -> StrategyOutput:
        out = StrategyOutput()
        p = self.params
        min_edge = p["min_net_edge"]
        candidates: list[tuple[Decimal, Proposal]] = []
        for market in watchlist:
            if not market.outcome_prices or len(market.token_ids) != 2:
                out.abstain("marché non binaire")
                continue
            idx = 0 if market.outcome_prices[0] >= market.outcome_prices[1] else 1
            token_id = market.token_ids[idx]
            if token_id in state.positions:
                out.abstain("position déjà ouverte")
                continue
            book = books.get(token_id)
            if book is None or book.best_ask is None or book.mid is None:
                out.abstain("carnet indisponible")
                continue
            ask = book.best_ask
            if not (p["min_ask"] <= ask <= p["max_ask"]):
                out.abstain("favori hors de la plage de prix")
                continue
            q = min(book.mid + p["bias"], p.get("q_max", Decimal("0.99")))
            low = q - p["uncertainty"]
            high = min(q + p["uncertainty"], Decimal("0.995"))
            best: tuple[Decimal, Decimal] | None = None  # (prix limite, coût par part)
            try:
                for k in range(int(p["max_extra_ticks"]) + 1):
                    price = ask + book.tick_size * k
                    if price >= ONE:
                        break
                    cost_ps = price + taker_fee(ONE, price, market.fees)
                    if low - cost_ps >= min_edge:
                        best = (price, cost_ps)
            except UnknownFees:
                out.abstain("frais inconnus")
                continue
            if best is None:
                out.abstain("avantage net insuffisant")
                continue
            max_price, cost_ps = best
            shares = size_buy(state, prob_low=low, cost_per_share=cost_ps,
                              kelly_fraction=p.get("kelly_fraction", Decimal("1")), marks=marks)
            if shares < max(market.min_order_size, book.min_order_size):
                out.abstain("taille autorisée trop faible")
                continue
            edge = low - cost_ps
            candidates.append((edge, Proposal(
                op_id=make_op_id(bucket, self.strategy_id, self.version, token_id, "BUY"),
                side="BUY", market_id=market.market_id, event_id=market.event_id,
                token_id=token_id, outcome=market.outcomes[idx], est_probability=q,
                prob_low=low, prob_high=high, max_price=max_price, shares=shares,
                net_edge=edge, sources=("polymarket_gamma", "polymarket_clob"),
                uncertainty=f"a priori non calibré ± {p['uncertainty']}",
                invalidation="prix du favori < 0,70 ou règles de résolution ambiguës",
                horizon_hours=Decimal(str(round(
                    max((market.end_date - book.fetched_at).total_seconds(), 0) / 3600, 2))),
                strategy_id=self.strategy_id, strategy_version=self.version,
            )))
        candidates.sort(key=lambda item: item[0], reverse=True)
        out.proposals = [prop for _, prop in candidates[: int(p["max_proposals_per_cycle"])]]
        return out


WEATHER_SLUGS = ("highest-temperature", "lowest-temperature", "precipitation", "where-will-it-rain", "will-it-rain", "rain-in-", "snow-in-")
# 24/10/2026 : Gamma a aussi passé la POLITIQUE en « zero_fees » (19-20/10) : elle se reconnaît à l'adresse de l'événement.
POLITICS_SLUGS = re.compile(r"election|senate|governor|mayor|midterm|primary|nominee|prime-minister|presidential|president-of|parliament|balance-of-power|"
                            r"win-the-house|house-election|control-the-house|house-margin|margin-of-victory|speaker|referendum|coalition")


def market_family(market: MarketInfo) -> str:
    """Famille d'un marché d'après son barème de frais (Gamma `feeType`, vérifié le 16/10/2026) :
    « politics_fees » → politics, « sports_fees_v3 » → sports, « weather_fees » → weather… ;
    marché SANS frais → sans_frais ; barème inconnu → other."""
    if market.fees.enabled is False:
        return "sans_frais"
    if market.fees.enabled and market.fees.fee_type:
        family = market.fees.fee_type.split("_")[0]
        # 19-20/10/2026 : Gamma passe la météo (et la politique, la finance…) en « zero_fees » : le barème ne dit plus la famille.
        if family == "zero" and (market.event_slug or market.slug or "").startswith(WEATHER_SLUGS):
            return "weather"
        if family == "zero" and POLITICS_SLUGS.search((market.event_slug or market.slug or "").lower()):
            return "politics"
        return family
    return "other"


STRATEGY_CLASSES: dict[str, Any] = {NaiveFavorite.strategy_id: NaiveFavorite}


class PatientOrders:
    """Mode « patient » : au lieu d'acheter tout de suite au vendeur (ask), la stratégie pose un ordre
    d'achat à cours limité `ticks` crans au-dessus du meilleur acheteur, sans jamais toucher le vendeur.
    Elle paie moins cher (et souvent sans frais), mais n'est servie que si le marché redescend au
    travers de son prix — souvent jamais."""

    def __init__(self, strategy, ticks: int):
        if ticks < 0:
            raise StrategyConfigError("limit_ticks doit être >= 0")
        self.inner, self.ticks = strategy, ticks
        self.strategy_id, self.version = strategy.strategy_id, strategy.version

    def propose(self, watchlist, books, state, limits, bucket, marks=None) -> StrategyOutput:
        from polymarket_agent.execution_simulator import maker_fee  # import tardif

        out = self.inner.propose(watchlist, books, state, limits, bucket, marks)
        markets = {m.market_id: m for m in watchlist}
        kept = []
        for p in out.proposals:
            book, market = books.get(p.token_id), markets.get(p.market_id)
            if p.side != "BUY" or book is None or market is None or book.best_bid is None \
                    or book.best_ask is None:
                kept.append(p)
                continue
            limit = min(book.best_bid + book.tick_size * self.ticks, book.best_ask - book.tick_size)
            if limit <= 0 or limit <= book.best_bid - book.tick_size:
                out.abstain("écart trop serré pour poser un ordre")
                continue
            try:
                edge = p.prob_low - limit - maker_fee(ONE, limit, market.fees)
            except UnknownFees:
                out.abstain("frais inconnus")
                continue
            out.limit_orders.append(dataclasses.replace(p, max_price=limit, net_edge=edge))
        out.proposals = kept
        return out

    def __getattr__(self, name):
        return getattr(self.inner, name)


FILTER_KEYS = ("familles", "prix_min", "prix_max", "prix_final", "heures_min", "heures_max", "heure_utc",
               "ecart_max", "un_match", "question_exclut")


class Filtered:
    """La stratégie jouée sur un MORCEAU des marchés, défini par `filtres` (YAML) :
      familles : [sports, …] (market_family) ; heures_min / heures_max : délai jusqu'au coup d'envoi
      (à l'échéance hors sport) ; prix_min / prix_max : prix payé (prix du vendeur, ou de l'ordre posé
      avec prix_final) ; heure_utc : [début, fin[ (heure de la journée en temps universel, [20, 4] passe
      minuit) ; ecart_max : écart achat-vente maximal ; un_match : true → seulement les paris sur UN
      match (Gamma `sportsMarketType`), pas les marchés de saison (Ballon d'Or, championnat…) ;
      question_exclut : marché refusé si sa question contient un de ces mots ;
      prix_final : true (avec `limit_ticks`) → prix_min / prix_max portent sur le prix de l'ORDRE POSÉ
      et non sur le prix du vendeur (21/10/2026).
    Les filtres de marché s'appliquent AVANT la stratégie (elle choisit ses meilleurs paris dans le
    morceau) ; celui du prix APRÈS."""

    def __init__(self, strategy, filters: dict):
        unknown = set(filters) - set(FILTER_KEYS)
        if unknown:
            raise StrategyConfigError(f"filtres inconnus : {sorted(unknown)}")
        self.inner, self.filters = strategy, dict(filters)
        self.strategy_id, self.version = strategy.strategy_id, strategy.version

    def _reject(self, m: MarketInfo, now) -> str | None:
        """Nom du premier filtre qui écarte ce marché, ou None s'il passe (25/10/2026 : une raison
        d'abstention par filtre, pour distinguer une stratégie sourde d'une stratégie sans occasion)."""
        f = self.filters
        if "familles" in f and market_family(m) not in f["familles"]:
            return "familles"
        if "un_match" in f and bool(f["un_match"]) != bool(getattr(m, "sports_market_type", None)):
            return "un_match"
        question = (m.question or "").lower()
        if any(str(w).lower() in question for w in f.get("question_exclut", ())):
            return "question_exclut"
        if "ecart_max" in f and (m.spread is None or float(m.spread) > float(f["ecart_max"])):
            return "ecart_max"
        if ("heures_min" in f or "heures_max" in f) and now is not None:
            ref = m.game_start or m.end_date
            if ref is None:
                return "heures"
            hours = (ref - now).total_seconds() / 3600
            if not float(f.get("heures_min", -1e9)) <= hours <= float(f.get("heures_max", 1e9)):
                return "heures"
        return None

    def _order_ok(self, p) -> bool:
        if p.side != "BUY":
            return True
        f = self.filters
        return float(f.get("prix_min", 0)) <= float(p.max_price) <= float(f.get("prix_max", 1))

    def propose(self, watchlist, books, state, limits, bucket, marks=None) -> StrategyOutput:
        now = max((b.fetched_at for b in books.values()), default=None)
        if "heure_utc" in self.filters and now is not None:
            start, end = (int(h) for h in self.filters["heure_utc"])
            hour = now.astimezone(timezone.utc).hour
            if not (start <= hour < end if start <= end else hour >= start or hour < end):
                out = StrategyOutput()  # hors de la plage horaire : aucune nouvelle position
                for _ in watchlist:
                    out.abstain(f"hors de la plage horaire (heure_utc {start}-{end})")
                return out
        kept, dropped = [], {}
        for m in watchlist:
            why = self._reject(m, now)
            if why is None:
                kept.append(m)
            else:
                dropped[why] = dropped.get(why, 0) + 1
        out = self.inner.propose(kept, books, state, limits, bucket, marks)
        for why, n in dropped.items():
            out.abstentions[f"hors du filtre {why}"] = out.abstentions.get(f"hors du filtre {why}", 0) + n
        out.proposals = [p for p in out.proposals if self._order_ok(p)]
        if getattr(out, "limit_orders", None):
            out.limit_orders = [p for p in out.limit_orders if self._order_ok(p)]
        return out

    def __getattr__(self, name):
        return getattr(self.inner, name)


def load_strategies(path: Path) -> list:
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise StrategyConfigError(f"impossible de lire {path}") from exc
    strategies = []
    for strategy_id, spec in raw.items():
        if not isinstance(spec, dict) or not spec.get("enabled"):
            continue
        # « class: » : la classe jouée, sous le nom de la stratégie.
        cls = STRATEGY_CLASSES.get(str(spec.get("class", strategy_id)))
        if cls is None:
            raise StrategyConfigError(f"stratégie inconnue : {spec.get('class', strategy_id)}")
        strategy = cls(str(spec["version"]), str(spec["created_at"]), load_params(spec.get("params")))
        if "class" in spec:
            strategy.strategy_id = strategy_id
        filters = dict(spec.get("filtres") or {})
        if filters.pop("prix_final", False) and "limit_ticks" in spec:
            # Le filtre de prix se lit sur l'ordre posé : le mode patient passe DANS le filtre.
            strategy = Filtered(PatientOrders(strategy, int(spec["limit_ticks"])), filters)
        else:
            if filters:
                strategy = Filtered(strategy, filters)
            if "limit_ticks" in spec:  # mode patient : ordres posés au lieu d'achats immédiats
                strategy = PatientOrders(strategy, int(spec["limit_ticks"]))
        # Ordres posés annulés au coup d'envoi (28/10/2026, enquête « posées en match ») : la stratégie ne subit pas la
        # sélection adverse du mode patient en jeu ; `main._fill_resting` lit cet attribut.
        strategy.annuler_au_coup_d_envoi = bool(spec.get("annuler_au_coup_d_envoi", False))
        strategies.append(strategy)
    return strategies
