"""Un cycle complet de simulation (ARCHITECTURE.md §2).

journal → faillite ? → règlements → scanner → carnets → ordres posés → stratégie → contrôleur →
relecture du carnet → simulation → journal → battement de cœur.

Laboratoire dédié (décision d'Etienne, 29/10/2026) : plus d'arène ni de portefeuilles fantômes. La
stratégie de config/strategies.yaml trade directement le capital PRINCIPAL, celui de la règle
« à 0 € tu meurs » ; chaque palier franchi (cliquet ×1,5) est inscrit dans le journal signé.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from pathlib import Path
from typing import Callable

from polymarket_agent.correlation import correlation_group
from polymarket_agent.execution_simulator import simulate_buy, simulate_resting_buy
from polymarket_agent.http_client import SourceUnavailable
from polymarket_agent.ledger import Ledger, LedgerCorrupt, LedgerError, NotInitialized
from polymarket_agent.market_scanner import scan
from polymarket_agent.models import MarketInfo, OrderBook
from polymarket_agent.persistence import Database
from polymarket_agent.safety_controller import (MarketContext, Proposal, RiskLimits,
                                                assert_simulation_mode, evaluate)
from polymarket_agent.settlement import settle_positions
from polymarket_agent.sources.polymarket_clob import ClobClient
from polymarket_agent.sources.polymarket_gamma import GammaClient

log = logging.getLogger(__name__)
PRICE_QUANTUM = Decimal("0.00000001")
PORTFOLIO = "principal"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_HALT = 2  # journal corrompu ou absent : GitHub enverra un e-mail d'échec


@dataclass
class CycleDeps:
    settings: dict
    limits: RiskLimits
    strategies: list
    ledger_path: Path
    lock_path: Path
    key: bytes
    db: Database
    gamma: GammaClient
    clob: ClobClient
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)


@dataclass
class CycleReport:
    cycle_id: str
    status: str = "EN_COURS"
    exit_code: int = EXIT_OK
    counts: dict[str, int] = field(default_factory=dict)
    summary: dict[str, str] = field(default_factory=dict)
    milestones: list[str] = field(default_factory=list)
    messages: list[str] = field(default_factory=list)
    # Minutage par étape (secondes) et raisons d'abstention de chaque stratégie.
    timings: dict[str, float] = field(default_factory=dict)
    abstentions: dict[str, dict] = field(default_factory=dict)
    lap_at: float = 0.0


def _lap(report: CycleReport, name: str) -> None:
    """Ajoute à `report.timings[name]` le temps écoulé depuis l'étape précédente."""
    now = time.monotonic()
    report.timings[name] = round(report.timings.get(name, 0.0) + (now - (report.lap_at or now)), 2)
    report.lap_at = now


@dataclass
class Portfolio:
    name: str
    ledger: Ledger
    strategies: list
    marks: dict[str, Decimal] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)
    # Liquidité déjà prise pendant le cycle : {(jeton, sens): parts}.
    taken: dict = field(default_factory=dict)


def cycle_bucket(now: datetime, minutes: int) -> str:
    now = now.astimezone(timezone.utc)
    floored = now.replace(minute=now.minute - now.minute % minutes, second=0, microsecond=0)
    return floored.strftime("%Y%m%dT%H%MZ")


def market_context(market: MarketInfo, book: OrderBook | None, token_id: str) -> MarketContext:
    return MarketContext(
        token_id=token_id,
        active=market.active,
        closed=market.closed,
        accepting_orders=market.accepting_orders,
        data_ok=market.data_ok and book is not None and book.token_id == token_id,
        best_bid=book.best_bid if book else None,
        best_ask=book.best_ask if book else None,
        # Prudence : on retient le plus ancien des deux horodatages.
        book_time=(min(book.timestamp, book.fetched_at) if book and book.timestamp else None),
        fees_enabled=market.fees.enabled,
        fee_rate=market.fees.rate,
        fee_exponent=market.fees.exponent,
        min_order_size=max(market.min_order_size, book.min_order_size if book else Decimal("0")),
        issues=tuple(market.issues),
        correlation_group=correlation_group(market),
    )


def run_cycle(deps: CycleDeps) -> CycleReport:
    started = deps.now()
    bucket = cycle_bucket(started, int(deps.settings["cycle"]["bucket_minutes"]))
    report = CycleReport(cycle_id=f"{bucket}-{started.strftime('%S%f')}")
    deps.db.start_cycle(report.cycle_id, started)
    if getattr(deps.db, "recovered_from", None):
        report.messages.append(f"base de données illisible mise de côté ({deps.db.recovered_from.name}), "
                               "base neuve créée ; ancrage anti-troncature perdu")
        log.critical("base de données illisible remplacée : %s", deps.db.recovered_from)
    assert_simulation_mode(deps.settings.get("mode"))

    # 1. Journal principal : vérification complète (signatures, chaîne, comptabilité).
    try:
        ledger = Ledger.open(deps.ledger_path, deps.key, deps.lock_path)
    except NotInitialized as exc:
        return _halt(deps, report, "NON_INITIALISE", str(exc))
    except LedgerCorrupt as exc:
        return _halt(deps, report, "CORROMPU", str(exc))
    anchor = deps.db.get_anchor()
    if anchor is not None:
        seq, digest = anchor
        if seq > ledger.state.last_seq or ledger.hash_at(seq) != digest:
            return _halt(deps, report, "CORROMPU",
                         f"le journal ne contient plus l'écriture n°{seq} déjà vue : troncature ou remplacement")

    report.lap_at = time.monotonic()
    _lap(report, "journal")
    main = Portfolio(PORTFOLIO, ledger, list(deps.strategies))
    # Ordres posés laissés par les anciens portefeuilles fantômes (avant le 29/10/2026) : plus personne ne les suit.
    deps.db.cancel_orders_except(PORTFOLIO, started)
    try:
        _trade(deps, main, report, bucket)
    except LedgerError as exc:  # garde-fou : jamais d'état à moitié écrit
        report.status, report.exit_code = "ERREUR_JOURNAL", EXIT_ERROR
        report.messages.append(str(exc))
        log.error("erreur du journal : %s", exc)
    except Exception as exc:  # noqa: BLE001 - un cycle ne doit jamais planter sans trace
        report.status, report.exit_code = "ERREUR", EXIT_ERROR
        report.messages.append(f"{type(exc).__name__}: {exc}")
        log.exception("erreur inattendue pendant le cycle")

    finished = deps.now()
    if not ledger.state.bankrupt:
        for m in ledger.record_milestones(finished):
            report.milestones.append(f"palier {m['target']} en {m['seconds'] / 3600:.1f} h")
            log.info("PALIER %s atteint", m["target"])
    held = {t: m for t, m in main.marks.items() if t in ledger.state.positions}
    ledger.heartbeat(report.cycle_id, report.status, finished, total_estimated=ledger.state.total_estimated(held))
    deps.db.set_anchor(ledger.state.last_seq, ledger.state.last_hash, finished)
    _lap(report, "battement")
    report.timings["total"] = round((deps.now() - started).total_seconds(), 1)
    deps.db.set_value("durees_cycle", json.dumps({"cycle": report.cycle_id, "etapes": report.timings}))
    deps.db.set_value("abstentions", json.dumps({"cycle": report.cycle_id, "strategies": report.abstentions}))
    report.summary = ledger.state.summary(held)
    report.counts.update(main.counts)
    deps.db.finish_cycle(report.cycle_id, finished, report.status, counts=report.counts,
                         summary=report.summary, error="; ".join(report.messages) or None)
    deps.db.prune(float(deps.settings["database"]["max_megabytes"]))
    return report


def _halt(deps: CycleDeps, report: CycleReport, status: str, message: str) -> CycleReport:
    log.critical("ARRÊT %s : %s", status, message)
    report.status, report.exit_code = status, EXIT_HALT
    report.messages.append(message)
    deps.db.finish_cycle(report.cycle_id, deps.now(), status, error=message)
    return report


def _trade(deps: CycleDeps, main: Portfolio, report: CycleReport, bucket: str) -> None:
    """Corps du cycle. La faillite arrête TOUT le trading."""
    ledger = main.ledger
    # 2. Règle n°1 : à 0 € tu meurs.
    if ledger.state.bankrupt:
        report.status = "FAILLITE"
        report.messages.append(ledger.state.bankrupt_reason)
        log.critical("FAILLITE : %s — aucun trading", ledger.state.bankrupt_reason)
        return

    # 3. Règlements : les marchés résolus libèrent le capital avant toute nouvelle entrée. Marchés détenus
    #    lus d'abord par lots (21/10/2026 : relus un par un, avec la pause de politesse, ils prenaient 79 s).
    if ledger.state.positions:
        gamma_once = _OnceGamma(deps.gamma)
        gamma_once.prefetch({p.market_id for p in ledger.state.positions.values()},
                            int(deps.settings.get("cycle", {}).get("reglements_par_lot", 1) or 1))
        main.counts["settled"] = len(settle_positions(ledger, gamma_once, deps.now()))
    _lap(report, "reglements")
    if ledger.state.bankrupt:
        report.status = "FAILLITE"
        return

    # 4. Scanner.
    now = deps.now()
    try:
        result = scan(deps.gamma, now, deps.settings["scanner"])
    except (SourceUnavailable, ValueError) as exc:
        report.status = "DEGRADE"
        report.messages.append(f"scanner indisponible : {exc}")
        log.warning("scanner indisponible, cycle sans trading : %s", exc)
        return
    # Scan complet archivé toutes les N minutes ; sinon, seulement la liste de surveillance.
    full_every = int(deps.settings["scanner"].get("full_snapshot_every_minutes", 30))
    full = now.minute % full_every < int(deps.settings["cycle"]["bucket_minutes"])
    deps.db.save_markets(report.cycle_id, now, result.markets if full else result.watchlist)
    report.counts.update(markets=len(result.markets), watchlist=len(result.watchlist))
    _lap(report, "scanner")

    # 5. Carnets : les deux issues de chaque marché surveillé, les positions détenues (prix de marque)
    #    et les jetons des ordres posés.
    wanted = [t for market in result.watchlist if len(market.token_ids) == 2 for t in market.token_ids]
    wanted += [t for t in ledger.state.positions if t not in wanted]
    wanted += [o["token_id"] for o in deps.db.open_orders(PORTFOLIO) if o["token_id"] not in wanted]
    try:
        books = deps.clob.get_books(wanted) if wanted else {}
    except (SourceUnavailable, ValueError) as exc:
        report.status = "DEGRADE"
        report.messages.append(f"carnets indisponibles : {exc}")
        log.warning("carnets indisponibles, cycle sans trading : %s", exc)
        return
    deps.db.save_books(report.cycle_id, books.values())
    _lap(report, "carnets")
    main.marks.update({t: b.best_bid for t, b in books.items()
                       if t in ledger.state.positions and b.best_bid is not None})

    # 5 bis. Ordres posés aux cycles précédents : exécutés avec ces carnets (relus APRÈS leur pose)
    #        si le marché est passé au travers de leur prix.
    by_market = {m.market_id: m for m in result.markets} | {m.market_id: m for m in result.watchlist}
    _fill_resting(deps, main, by_market, books, report, now)
    _lap(report, "ordres_poses")
    if ledger.state.bankrupt:
        report.status = "FAILLITE"
        return

    # 6-11. Stratégie → propositions → contrôleur → exécution simulée.
    _execute(deps, main, result.watchlist, books, report, bucket, result.markets)
    _lap(report, "strategie")
    report.status = "FAILLITE" if ledger.state.bankrupt else "OK"


# Relecture d'un carnet juste avant d'exécuter (19/10/2026) : un carnet relu il y a moins de
# FRESH_BOOK_SECONDS pendant le même cycle est réutilisé.
FRESH_BOOK_SECONDS = 20


def _reread_book(deps: CycleDeps, token_id: str, cache: dict | None) -> OrderBook:
    hit = cache.get(token_id) if cache is not None else None
    if hit is not None and hit.fetched_at is not None and (deps.now() - hit.fetched_at).total_seconds() <= FRESH_BOOK_SECONDS:
        return hit
    book = deps.clob.get_book(token_id)
    if cache is not None and book is not None:
        cache[token_id] = book
    return book


def _raison_par_defaut(output, vus: int) -> None:
    """Une stratégie qui ne propose rien ET ne note aucune raison dit pourquoi malgré elle (28/10/2026) :
    sinon impossible de distinguer une stratégie sourde d'une stratégie sans occasion."""
    if vus and not output.proposals and not getattr(output, "limit_orders", None) and not output.abstentions:
        output.abstentions["aucun marché vu n'a la forme demandée (raison non notée par la stratégie)"] = vus


def _execute(deps: CycleDeps, portfolio: Portfolio, watchlist: list[MarketInfo],
             books: dict[str, OrderBook], report: CycleReport, bucket: str,
             all_markets: list[MarketInfo] | None = None) -> None:
    ledger, marks = portfolio.ledger, portfolio.marks
    by_market = {m.market_id: m for m in (all_markets or [])} | {m.market_id: m for m in watchlist}
    fresh_books: dict = {}  # carnets relus pendant ce cycle (_reread_book)
    proposals = []
    held_now: dict[str, set[str]] = {}  # marché → jetons de favoris pris ou proposés dans ce cycle
    for strategy in portfolio.strategies:
        seen = watchlist
        started = []
        if getattr(strategy, "annuler_au_coup_d_envoi", False):
            # Ses ordres posés tombent au coup d'envoi (_fill_resting) : elle ne prend donc pas non plus de nouveau pari
            # une fois le match commencé (avant le 29/10/2026, il fallait aussi le filtre `heures_min: 0`).
            now = deps.now()
            started = [m for m in watchlist if m.game_start is not None and m.game_start <= now]
            seen = [m for m in watchlist if m.game_start is None or m.game_start > now]
        output = strategy.propose(seen, books, ledger.state, deps.limits, bucket, marks)
        _raison_par_defaut(output, len(seen))
        if started:
            output.abstentions["match déjà commencé"] = len(started)
        limit_orders = list(getattr(output, "limit_orders", []))
        log.info("%s v%s : %d propositions, %d ordres à poser, abstentions %s", strategy.strategy_id,
                 strategy.version, len(output.proposals), len(limit_orders), output.abstentions)
        # Raisons d'abstention publiées (21/10/2026) : pourquoi la stratégie reste muette.
        reasons = dict(sorted(output.abstentions.items(), key=lambda kv: -kv[1])[:6])
        report.abstentions[strategy.strategy_id] = {
            "marches": len(seen), "propositions": len(output.proposals), "ordres": len(limit_orders), "raisons": reasons}
        # Deux camps d'un même match (enquêtes des 20 et 22/10/2026).
        before = len(output.proposals) + len(limit_orders)
        output.proposals = _opposite_side(deps, portfolio, output.proposals, held_now)
        limit_orders = _opposite_side(deps, portfolio, limit_orders, held_now)
        if before - len(output.proposals) - len(limit_orders):
            reasons["camp opposé d'une position déjà prise"] = before - len(output.proposals) - len(limit_orders)
        # Mise plafonnée (settings.yaml → principal.max_mise).
        output.proposals = _cap_stake(deps, portfolio, output.proposals)
        limit_orders = _cap_stake(deps, portfolio, limit_orders)
        proposals += output.proposals
        proposals += _place_resting(deps, portfolio, limit_orders, by_market, report, fresh_books)
    accepted = fills = 0

    for proposal in proposals:
        market = by_market.get(proposal.market_id)
        if market is None or proposal.side != "BUY":
            continue
        verdict = evaluate(proposal, ledger.state, market_context(market, books.get(proposal.token_id),
                                                                  proposal.token_id),
                           deps.limits, deps.now(), marks)
        # Carnet du début de cycle vieilli : pas un motif de rejet, il est relu juste après et contrôlé de nouveau.
        if not verdict.accepted and not all(r.startswith("carnet périmé") for r in verdict.reasons):
            deps.db.save_decision(report.cycle_id, deps.now(), proposal, verdict)
            log.info("rejet %s : %s", proposal.op_id, verdict.reasons)
            continue

        # Relecture du carnet juste avant l'exécution, puis nouveau contrôle.
        try:
            fresh = _reread_book(deps, proposal.token_id, fresh_books)
        except (SourceUnavailable, ValueError) as exc:
            verdict.accepted = False
            verdict.reasons.append(f"carnet non relu : {exc}")
            deps.db.save_decision(report.cycle_id, deps.now(), proposal, verdict)
            continue
        verdict = evaluate(proposal, ledger.state, market_context(market, fresh, proposal.token_id),
                           deps.limits, deps.now(), marks)
        if not verdict.accepted:
            deps.db.save_decision(report.cycle_id, deps.now(), proposal, verdict)
            log.info("rejet après relecture %s : %s", proposal.op_id, verdict.reasons)
            continue
        accepted += 1
        drop = _price_drop(deps, proposal, books.get(proposal.token_id), fresh)
        if drop is not None:
            verdict.accepted = False
            verdict.reasons.append(drop)
            deps.db.save_decision(report.cycle_id, deps.now(), proposal, verdict)
            log.info("rejet après relecture %s : %s", proposal.op_id, drop)
            continue
        room = _cash_room(deps, portfolio)
        if room is not None and Decimal(verdict.checks.get("cost_bound", "0")) > room:
            # Réserve de liquidités (27/10/2026) : l'achat est réduit à ce qui reste au-dessus de la réserve, sinon abandonné.
            per_share = Decimal(verdict.checks["cost_bound"]) / proposal.shares
            shares = (room / per_share).quantize(Decimal("0.01"), rounding=ROUND_DOWN) if room > 0 else Decimal("0")
            if shares > 0:
                proposal = dataclasses.replace(proposal, shares=shares)
                verdict = evaluate(proposal, ledger.state, market_context(market, fresh, proposal.token_id),
                                   deps.limits, deps.now(), marks)
            if shares <= 0 or not verdict.accepted:
                verdict.accepted = False
                verdict.reasons.append(f"réserve de liquidités : {max(room, Decimal('0')):.2f} disponibles au-dessus de la réserve")
                deps.db.save_decision(report.cycle_id, deps.now(), proposal, verdict)
                log.info("%s abandonné : réserve de liquidités", proposal.op_id)
                continue
        key = (proposal.token_id, "BUY")
        fill = simulate_buy(fresh, proposal.shares, proposal.max_price, market.fees,
                            portfolio.taken.get(key, Decimal("0")))
        if fill.executed:
            portfolio.taken[key] = portfolio.taken.get(key, Decimal("0")) + fill.filled_shares
            # Écriture signée. Le journal revérifie solde, doublon et faillite.
            try:
                ledger.record_buy(
                    op_id=proposal.op_id, token_id=proposal.token_id, market_id=proposal.market_id,
                    event_id=proposal.event_id, outcome=proposal.outcome,
                    shares=fill.filled_shares,
                    price=fill.avg_price.quantize(PRICE_QUANTUM, rounding=ROUND_UP),
                    fee=fill.fee, strategy_id=proposal.strategy_id,
                    strategy_version=proposal.strategy_version, now=deps.now(),
                    group=correlation_group(market),
                    extra={"cycle_id": report.cycle_id, "portfolio": portfolio.name,
                           "fill_status": fill.status, "levels": fill.levels,
                           "est_probability": str(proposal.est_probability),
                           "prob_low": str(proposal.prob_low), "prob_high": str(proposal.prob_high),
                           "net_edge": str(proposal.net_edge),
                           "horizon_hours": str(proposal.horizon_hours),
                           **_depuis_coup_d_envoi(market, deps.now())})
                fills += 1
                log.info("exécution simulée %s : %s parts à %s (+%s de frais), %s",
                         proposal.op_id, fill.filled_shares, fill.avg_price, fill.fee, fill.status)
            except LedgerError as exc:
                fill.status, fill.reason = "REFUSE_JOURNAL", str(exc)
                log.warning("le journal refuse %s : %s", proposal.op_id, exc)
        deps.db.save_decision(report.cycle_id, deps.now(), proposal, verdict, fill)
        if ledger.state.bankrupt:
            break
    portfolio.counts.update(proposals=len(proposals), accepted=accepted, fills=fills)


def _depuis_coup_d_envoi(market: MarketInfo, now: datetime) -> dict[str, str]:
    """Heures écoulées depuis le coup d'envoi (négatif : avant). `horizon_hours` est l'échéance Gamma, une
    date placeholder pour un match ; ce champ dit si l'achat a eu lieu avant ou pendant le jeu (enquête 20/10)."""
    if getattr(market, "game_start", None) is None:
        return {}
    return {"depuis_coup_d_envoi_h": f"{(now - market.game_start).total_seconds() / 3600:.2f}"}


class _OnceGamma:
    """`get_market` lu une seule fois par marché pendant un cycle (réponse ou erreur gardée)."""

    def __init__(self, gamma):
        self.gamma, self.cache = gamma, {}

    down = False  # Gamma en panne pendant ce cycle (prefetch) : pas de lecture une par une

    def get_market(self, market_id):
        market_id = str(market_id)
        if market_id not in self.cache and self.down:
            raise SourceUnavailable("Gamma indisponible pendant ce cycle (lots refusés)")
        if market_id not in self.cache:
            try:
                self.cache[market_id] = (True, self.gamma.get_market(market_id))
            except (SourceUnavailable, ValueError) as exc:
                self.cache[market_id] = (False, exc)
        ok, value = self.cache[market_id]
        if not ok:
            raise value
        return value

    def prefetch(self, market_ids, batch: int = 100) -> None:
        """Lit d'avance les marchés pas encore lus, par lots (`GammaClient.markets_by_ids`, fermés puis
        ouverts). Un lot refusé est coupé en deux (un marché supprimé ne bloque pas tout le lot). Si
        tout échoue, Gamma est jugé en panne pour ce cycle : les marchés non lus ne sont pas relus un
        par un ; leur règlement attend le cycle suivant. Un marché simplement absent des lots est lu
        un par un, comme avant."""
        todo = [str(m) for m in market_ids if str(m) not in self.cache]
        if batch <= 1 or len(todo) < 2 or not hasattr(self.gamma, "markets_by_ids"):
            return
        for k in range(0, len(todo), batch):
            chunk, any_ok = todo[k:k + batch], False
            for closed in (True, False):
                rows, ok = self._lot(chunk, closed)
                any_ok = any_ok or ok
                for raw in rows:
                    if isinstance(raw, dict) and str(raw.get("id")) in chunk:
                        self.cache[str(raw["id"])] = (True, raw)
            if not any_ok:
                self.down = True
                log.warning("règlements : Gamma ne répond pas aux lots, règlements remis au cycle suivant")
                return

    def _lot(self, chunk: list[str], closed: bool, depth: int = 0) -> tuple[list, bool]:
        try:
            return self.gamma.markets_by_ids(chunk, closed=closed), True
        except (SourceUnavailable, ValueError) as exc:
            if len(chunk) < 8 or depth >= 3:
                log.warning("règlements : lot de %d marchés illisible (%s)", len(chunk), exc)
                return [], False
            half = len(chunk) // 2
            a, ok_a = self._lot(chunk[:half], closed, depth + 1)
            b, ok_b = self._lot(chunk[half:], closed, depth + 1)
            return a + b, ok_a or ok_b

    def __getattr__(self, name):
        return getattr(self.gamma, name)


def _price_drop(deps: CycleDeps, proposal: Proposal, before: OrderBook | None, fresh: OrderBook | None) -> str | None:
    """Enquête du 20/10/2026 : l'estimation de la stratégie est calculée sur le carnet lu au début du
    cycle ; si le prix de vente a chuté de plus de `relecture.chute_max` avant l'exécution (mauvaise
    nouvelle en plein match), cette estimation est périmée et la mise, calculée sur elle, trop grosse.
    On ne joue pas : la stratégie redécidera au cycle suivant avec un carnet frais."""
    cfg = deps.settings.get("relecture") or {}
    if (proposal.side != "BUY" or not cfg or before is None or fresh is None
            or before.best_ask is None or fresh.best_ask is None):
        return None
    fall = before.best_ask - fresh.best_ask
    if fall > Decimal(str(cfg.get("chute_max", "1"))):
        return (f"le prix a chuté de {before.best_ask} à {fresh.best_ask} pendant le cycle : "
                "estimation périmée, décision reportée au prochain cycle")
    return None


def _opposite_side(deps: CycleDeps, portfolio: Portfolio, proposals: list[Proposal],
                   held_now: dict[str, set[str]]) -> list[Proposal]:
    """Deux camps d'un même match (enquêtes des 20 et 22/10/2026, contrôle `deux_camps`) : un favori
    acheté à 0,85 s'effondre en plein match, l'autre camp devient favori et la même stratégie l'achète
    aussi : la mise est doublée sur un seul match et l'un des deux paris perd forcément. On n'achète
    donc pas, au-dessus de `deux_camps.prix_min`, un jeton d'un marché où l'on tient déjà un AUTRE
    jeton payé au-dessus de ce prix."""
    cfg = deps.settings.get("deux_camps") or {}
    if not cfg:
        return proposals
    floor = Decimal(str(cfg.get("prix_min", "0.70")))
    held = {m: set(t) for m, t in held_now.items()}
    for pos in portfolio.ledger.state.positions.values():
        if pos.shares > 0 and pos.avg_cost >= floor:
            held.setdefault(pos.market_id, set()).add(pos.token_id)
    out = []
    for p in proposals:
        if p.side == "BUY" and p.max_price >= floor:
            if held.get(p.market_id, set()) - {p.token_id}:
                log.info("%s écarté : camp opposé d'une position déjà prise sur %s", p.op_id, p.market_id)
                continue
            held.setdefault(p.market_id, set()).add(p.token_id)
            held_now.setdefault(p.market_id, set()).add(p.token_id)
        out.append(p)
    return out


def _cap_stake(deps: CycleDeps, portfolio: Portfolio, proposals: list[Proposal]) -> list[Proposal]:
    """Mise plafonnée à `principal.max_mise` du capital estimé (23/10/2026, dans l'ancien fantôme : 25 %).
    Kelly sur une estimation gonflée (biais fixe +0,05) misait jusqu'à la moitié du capital sur un seul
    pari ; un seul pari faisait alors tout le résultat. Les paris restent les mêmes, seule la taille change."""
    cap = (deps.settings.get("principal") or {}).get("max_mise")
    if not cap:
        return proposals
    max_bet = Decimal(str(cap)) * portfolio.ledger.state.total_estimated(portfolio.marks)
    out = []
    for p in proposals:
        if p.side == "BUY" and p.max_price > 0 and p.shares * p.max_price > max_bet:
            shares = (max_bet / p.max_price).quantize(Decimal("0.01"), rounding=ROUND_DOWN)
            if shares <= 0:
                continue
            log.info("%s ramené de %s à %s parts (plafond %s du capital)", p.op_id, p.shares, shares, cap)
            p = dataclasses.replace(p, shares=shares)
        out.append(p)
    return out


def _cash_room(deps: CycleDeps, portfolio: Portfolio) -> Decimal | None:
    """Argent disponible AU-DESSUS de la réserve de liquidités (27/10/2026, `principal.reserve_liquidites`, part du
    capital estimé), ou None sans réserve. Règle n°1 : la faillite tombe dès que le SOLDE DISPONIBLE touche 0, même si
    les positions valent quelque chose ; ce coussin empêche qu'un achat le vide par accident."""
    share = (deps.settings.get("principal") or {}).get("reserve_liquidites")
    if not share:
        return None
    state = portfolio.ledger.state
    return state.cash - Decimal(str(share)) * state.total_estimated(portfolio.marks)


# --------------------------------------------------------------------------- ordres posés

def _place_resting(deps: CycleDeps, portfolio: Portfolio, orders: list[Proposal],
                   by_market: dict[str, MarketInfo], report: CycleReport,
                   fresh_books: dict | None = None) -> list[Proposal]:
    """Pose les ordres d'achat à cours limité (base de données, pas d'argent). Un ordre déjà exécutable
    (achat >= meilleur vendeur) est renvoyé pour le circuit habituel : il serait exécuté tout de suite
    sur le vrai marché."""
    cfg = deps.settings.get("limit_orders", {})
    max_open = int(cfg.get("max_open_per_portfolio", 30))
    max_age = float(cfg.get("max_age_hours", 6))
    ledger, now = portfolio.ledger, deps.now()
    open_orders = deps.db.open_orders(portfolio.name)
    marketable = []
    for order in orders:
        market = by_market.get(order.market_id)
        if market is None or order.side != "BUY":
            continue
        try:  # carnet relu au moment de la pose (celui du début du cycle peut avoir vieilli)
            book = _reread_book(deps, order.token_id, fresh_books)
        except (SourceUnavailable, ValueError) as exc:
            log.info("ordre non posé %s : carnet non relu (%s)", order.op_id, exc)
            continue
        if book is not None and book.best_ask is not None and order.max_price >= book.best_ask:
            marketable.append(order)
            continue
        verdict = evaluate(order, ledger.state, market_context(market, book, order.token_id),
                           deps.limits, now, portfolio.marks)
        others = [o for o in open_orders if not (o["token_id"] == order.token_id and o["side"] == order.side)]
        if verdict.accepted and len(others) >= max_open:
            verdict.accepted = False
            verdict.reasons.append(f"trop d'ordres posés ({max_open} au plus)")
        if verdict.accepted:
            # Réservation : les achats posés ne peuvent pas promettre plus que le solde disponible.
            reserved = sum((Decimal(o["remaining"]) * Decimal(json.loads(o["proposal"])["max_price"])
                            for o in others if o["side"] == "BUY"), Decimal("0"))
            room = _cash_room(deps, portfolio)
            if reserved + Decimal(verdict.checks.get("cost_bound", "0")) > (ledger.state.cash if room is None else room):
                verdict.accepted = False
                verdict.reasons.append(f"solde déjà réservé par les ordres posés ({reserved:.2f})"
                                       + ("" if room is None else " ou réserve de liquidités"))
        verdict.checks["order_type"] = "limite"
        if verdict.accepted:
            hours = min(max(float(order.horizon_hours), 0.0), max_age)
            if deps.db.add_pending_order(portfolio.name, order, now, now + timedelta(hours=hours)):
                portfolio.counts["orders_placed"] = portfolio.counts.get("orders_placed", 0) + 1
                open_orders = deps.db.open_orders(portfolio.name)
                log.info("ordre posé %s : achat de %s parts à %s", order.op_id, order.shares, order.max_price)
        deps.db.save_decision(report.cycle_id, now, order, verdict)
    return marketable


def _fill_resting(deps: CycleDeps, portfolio: Portfolio, by_market: dict[str, MarketInfo],
                  books: dict[str, OrderBook], report: CycleReport, now: datetime) -> None:
    """Exécute les ordres posés dont le prix a été traversé (execution_simulator, règle prudente).
    Chaque exécution repasse par le contrôleur et le journal, comme un achat immédiat."""
    ledger = portfolio.ledger
    consumed = portfolio.taken  # partagé avec les achats immédiats du même cycle
    # Stratégies dont les ordres posés tombent au coup d'envoi (`annuler_au_coup_d_envoi` de config/strategies.yaml).
    cancel_ids = {s.strategy_id for s in portfolio.strategies if getattr(s, "annuler_au_coup_d_envoi", False)}
    for order in deps.db.open_orders(portfolio.name):
        proposal = Proposal.from_dict(json.loads(order["proposal"]))
        remaining, n = Decimal(order["remaining"]), int(order["fills"])
        while f"{proposal.op_id}-L{n + 1}" in ledger.state.op_ids:
            n += 1  # exécution déjà au journal (cycle interrompu avant la mise à jour de la base)

        def close(status: str) -> None:
            deps.db.update_order(order["op_id"], remaining=remaining, fills=n, status=status, now=now)

        if now >= datetime.fromisoformat(order["expires_at"]):
            close("EXPIRE")
            continue
        market, book = by_market.get(proposal.market_id), books.get(proposal.token_id)
        if proposal.side != "BUY" or (market is not None and (market.closed or not market.active or not market.accepting_orders)):
            close("ANNULE")
            continue
        if (proposal.strategy_id in cancel_ids and market is not None
                and getattr(market, "game_start", None) is not None and now >= market.game_start):
            close("ANNULE")  # enquête du 28/10/2026 : un ordre posé servi en match est une sélection adverse (but, blessure)
            continue
        if market is None or book is None or book.fetched_at <= datetime.fromisoformat(order["created_at"]):
            continue  # pas de carnet relu après la pose : on attend
        key = (proposal.token_id, "BUY")
        fill = simulate_resting_buy(book, remaining, proposal.max_price, market.fees, consumed.get(key, Decimal("0")))
        if not fill.executed:
            continue
        part = dataclasses.replace(proposal, op_id=f"{proposal.op_id}-L{n + 1}", shares=fill.filled_shares)
        verdict = evaluate(part, ledger.state, market_context(market, book, part.token_id),
                           deps.limits, now, portfolio.marks)
        verdict.checks["order_type"] = "limite"
        room = _cash_room(deps, portfolio)
        if verdict.accepted and room is not None and fill.filled_shares * proposal.max_price + fill.fee > room:
            verdict.accepted = False  # l'ordre reste posé ; il sera servi quand l'argent sera revenu
            verdict.reasons.append(f"réserve de liquidités : {max(room, Decimal('0')):.2f} disponibles au-dessus de la réserve")
        if not verdict.accepted:
            deps.db.save_decision(report.cycle_id, now, part, verdict, fill)
            log.info("exécution d'ordre posé refusée %s : %s", part.op_id, verdict.reasons)
            continue
        try:
            ledger.record_buy(
                op_id=part.op_id, token_id=part.token_id, market_id=part.market_id,
                event_id=part.event_id, outcome=part.outcome, shares=fill.filled_shares,
                price=proposal.max_price, fee=fill.fee, strategy_id=part.strategy_id,
                strategy_version=part.strategy_version, now=now, group=correlation_group(market),
                extra={"cycle_id": report.cycle_id, "portfolio": portfolio.name, "order_type": "limite",
                       "resting_order": proposal.op_id, "fill_status": fill.status, "levels": fill.levels,
                       "est_probability": str(part.est_probability), "prob_low": str(part.prob_low),
                       "prob_high": str(part.prob_high), "net_edge": str(part.net_edge),
                       "horizon_hours": str(part.horizon_hours), **_depuis_coup_d_envoi(market, now)})
        except LedgerError as exc:
            fill.status, fill.reason = "REFUSE_JOURNAL", str(exc)
            deps.db.save_decision(report.cycle_id, now, part, verdict, fill)
            log.warning("le journal refuse %s : %s", part.op_id, exc)
            continue
        consumed[key] = consumed.get(key, Decimal("0")) + fill.filled_shares
        remaining, n = remaining - fill.filled_shares, n + 1
        close("EXECUTE" if remaining <= 0 else "OUVERT")
        portfolio.counts["resting_fills"] = portfolio.counts.get("resting_fills", 0) + 1
        deps.db.save_decision(report.cycle_id, now, part, verdict, fill)
        log.info("ordre posé exécuté %s : %s parts à %s (+%s de frais)", part.op_id,
                 fill.filled_shares, proposal.max_price, fill.fee)
        if ledger.state.bankrupt:
            break
