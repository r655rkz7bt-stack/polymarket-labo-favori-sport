"""Cycle complet avec des API factices : flux de bout en bout et scénarios de panne.

Depuis le 29/10/2026 (plus d'arène), la stratégie trade directement le capital principal."""

from __future__ import annotations

import json
from datetime import timedelta
from decimal import Decimal

import pytest

from conftest import KEY, NOW, ROOT, buy, make_book, raw_market
from polymarket_agent.config import load_settings
from polymarket_agent.http_client import SourceUnavailable
from polymarket_agent.ledger import Ledger
from polymarket_agent.main import EXIT_HALT, CycleDeps, run_cycle
from polymarket_agent.persistence import Database
from polymarket_agent.safety_controller import Proposal, RealModeForbidden
from polymarket_agent.strategy_engine import NaiveFavorite, PatientOrders, load_strategies

D = Decimal


def pipeline_strategy():
    """Stratégie de test du pipeline (le favori naïf à Kelly entier, qui trade facilement)."""
    return NaiveFavorite("1.0.0", "2026-10-14", {
        "min_ask": D("0.80"), "max_ask": D("0.95"), "bias": D("0.05"), "uncertainty": D("0.015"),
        "min_net_edge": D("0.02"), "max_extra_ticks": D("2"), "max_proposals_per_cycle": D("3")})


class FakeGamma:
    def __init__(self, markets=None, fail=False):
        self.markets, self.fail, self.calls = markets or [], fail, 0

    def list_markets(self, **kwargs):
        self.calls += 1
        if self.fail:
            raise SourceUnavailable("Gamma en panne (test)")
        return self.markets

    def get_market(self, market_id):
        if self.fail:
            raise SourceUnavailable("Gamma en panne (test)")
        for raw in self.markets:
            if raw["id"] == market_id:
                return raw
        raise SourceUnavailable("marché inconnu (test)")


class FakeClob:
    def __init__(self, books, fresh=None, fail=False):
        self.books, self.fresh, self.fail = books, fresh, fail

    def get_books(self, token_ids):
        if self.fail:
            raise SourceUnavailable("CLOB en panne (test)")
        return {t: self.books[t] for t in token_ids if t in self.books}

    def get_book(self, token_id):
        return (self.fresh or self.books)[token_id]


@pytest.fixture
def env(tmp_path, ledger, paths, limits):
    db = Database(tmp_path / "state" / "market.sqlite")
    db.set_anchor(ledger.state.last_seq, ledger.state.last_hash, NOW)
    clock = {"now": NOW}

    def make(gamma, clob, settings=None, strategies=None):
        return CycleDeps(settings=settings if settings is not None else load_settings(), limits=limits,
                         strategies=strategies if strategies is not None else [pipeline_strategy()],
                         ledger_path=paths[0], lock_path=paths[1], key=KEY, db=db,
                         gamma=gamma, clob=clob, now=lambda: clock["now"])
    yield make, db, clock
    db.close()


def good_book(token="tokA", ts=NOW):
    return make_book(token, bids=(("0.89", "500"),), asks=(("0.90", "40"), ("0.91", "500")), ts=ts)


def _ledger_types(path):
    return [json.loads(l)["type"] for l in path.read_text().splitlines()]


def test_full_cycle_executes_and_books_a_simulated_trade(env, paths):
    make, db, _ = env
    report = run_cycle(make(FakeGamma([raw_market()]), FakeClob({"tokA": good_book()})))
    assert report.status == "OK", report.messages
    assert report.counts["fills"] == 1
    reopened = Ledger.open(paths[0], KEY, paths[1])
    pos = reopened.state.positions["tokA"]
    # À 0,91 l'avantage passe sous le plancher : prix limite 0,90, 40 parts affichées dont 25 %
    # accessibles (concurrence, délai) → 10 parts, exécution partielle, jamais au-delà du prix limite.
    assert pos.shares == D("10")
    assert reopened.state.fees_paid > 0
    assert reopened.state.cash + pos.cost_basis == D("1000")
    assert _ledger_types(paths[0]) == ["GENESIS", "BUY", "HEARTBEAT"]
    row = db.conn.execute("SELECT accepted, fill_status FROM decisions").fetchone()
    assert row["accepted"] == 1 and row["fill_status"] == "PARTIAL"
    assert db.get_anchor()[0] == reopened.state.last_seq


def test_rerun_in_same_bucket_does_not_double_book(env, paths):
    make, _, clock = env
    deps = make(FakeGamma([raw_market()]), FakeClob({"tokA": good_book()}))
    run_cycle(deps)
    cash = Ledger.open(paths[0], KEY, paths[1]).state.cash
    clock["now"] = NOW + timedelta(seconds=30)
    run_cycle(deps)
    assert Ledger.open(paths[0], KEY, paths[1]).state.cash == cash


def _full_kelly():
    """Réglages du dépôt, mise entière (prudence 1) : comme une stratégie déjà prouvée."""
    settings = load_settings()
    settings["sante_strategies"] = dict(settings["sante_strategies"], base=1.0)
    return settings


def test_each_bet_is_capped_at_a_quarter_of_the_capital(env, paths):
    """`principal.max_mise` (0,25) : Kelly entier sur un biais fixe de +0,05 voudrait bien plus."""
    make, _, _ = env
    deep = make_book("tokA", bids=(("0.89", "5000"),), asks=(("0.90", "100000"),))
    run_cycle(make(FakeGamma([raw_market()]), FakeClob({"tokA": deep}), _full_kelly()))
    pos = Ledger.open(paths[0], KEY, paths[1]).state.positions["tokA"]
    assert D("240") <= pos.cost_basis <= D("251")


def test_unavailable_source_does_not_block_the_program(env, paths):
    make, _, clock = env
    report = run_cycle(make(FakeGamma(fail=True), FakeClob({})))
    assert report.status == "DEGRADE" and report.exit_code == 0
    report = run_cycle(make(FakeGamma([raw_market()]), FakeClob({}, fail=True)))
    assert report.status == "DEGRADE"
    # Le cycle suivant, une fois la source revenue, fonctionne normalement.
    clock["now"] = NOW + timedelta(minutes=5)
    report = run_cycle(make(FakeGamma([raw_market()]), FakeClob({"tokA": good_book(ts=clock["now"])})))
    assert report.status == "OK" and report.counts["fills"] == 1


def test_book_changed_before_execution_is_rechecked(env, paths):
    make, _, _ = env
    moved = make_book("tokA", bids=(("0.93", "500"),), asks=(("0.97", "500"),))
    report = run_cycle(make(FakeGamma([raw_market()]),
                            FakeClob({"tokA": good_book()}, fresh={"tokA": moved})))
    assert report.counts["fills"] == 0
    assert not Ledger.open(paths[0], KEY, paths[1]).state.positions


def test_price_crash_during_the_cycle_postpones_the_buy(env, paths):
    """Enquête du 20/10/2026 : estimation calculée sur le carnet du début de cycle (vente 0,90),
    prix effondré à la relecture (0,80) ; acheter « l'aubaine » avec une mise calculée sur
    l'ancien prix revenait à acheter une mauvaise nouvelle. On reporte au cycle suivant."""
    make, db, _ = env
    crashed = make_book("tokA", bids=(("0.79", "500"),), asks=(("0.80", "500"),))
    report = run_cycle(make(FakeGamma([raw_market()]), FakeClob({"tokA": good_book()}, fresh={"tokA": crashed})))
    assert report.counts.get("fills", 0) == 0
    reasons = [r["reasons"] for r in db.conn.execute("SELECT reasons FROM decisions")]
    assert any("chuté" in r for r in reasons)


def test_a_one_cent_dip_during_the_cycle_still_trades(env, paths):
    make, _, _ = env
    dip = make_book("tokA", bids=(("0.88", "500"),), asks=(("0.89", "40"), ("0.90", "500")))
    report = run_cycle(make(FakeGamma([raw_market()]), FakeClob({"tokA": good_book()}, fresh={"tokA": dip})))
    assert report.counts["fills"] == 1


def test_book_aged_during_the_cycle_is_reread_not_rejected(env, paths):
    """21/10/2026 : un cycle long vieillit les carnets du début de cycle ; une proposition n'est pas rejetée
    « carnet périmé » avant même la relecture. Le carnet relu, frais, doit suffire."""
    make, _, _ = env
    old = good_book(ts=NOW - timedelta(minutes=4))
    report = run_cycle(make(FakeGamma([raw_market()]), FakeClob({"tokA": old}, fresh={"tokA": good_book()})))
    assert report.counts["fills"] == 1


def test_stale_book_on_reread_means_no_trade(env, paths):
    make, _, _ = env
    stale = good_book(ts=NOW - timedelta(minutes=10))
    report = run_cycle(make(FakeGamma([raw_market()]),
                            FakeClob({"tokA": good_book()}, fresh={"tokA": stale})))
    assert report.counts["fills"] == 0


def test_market_without_reliable_data_does_not_trade(env, paths):
    make, _, _ = env
    report = run_cycle(make(FakeGamma([raw_market(bestBid=None)]), FakeClob({"tokA": good_book()})))
    assert report.counts.get("fills", 0) == 0
    assert report.counts["watchlist"] == 0


def test_bankrupt_engine_does_not_trade_or_scan(env, ledger, paths):
    make, _, _ = env
    buy(ledger, "all-in", token="tokZ", shares="2000", price="0.5")
    gamma = FakeGamma([raw_market()])
    report = run_cycle(make(gamma, FakeClob({"tokA": good_book()})))
    assert report.status == "FAILLITE"
    assert gamma.calls == 0
    assert "tokA" not in Ledger.open(paths[0], KEY, paths[1]).state.positions


def test_corrupt_ledger_halts(env, paths):
    make, _, _ = env
    paths[0].write_text(paths[0].read_text().replace("1000", "9000"))
    report = run_cycle(make(FakeGamma([raw_market()]), FakeClob({})))
    assert report.status == "CORROMPU" and report.exit_code == EXIT_HALT


def test_missing_ledger_halts_without_creating_capital(env, paths):
    make, _, _ = env
    paths[0].unlink()
    report = run_cycle(make(FakeGamma([raw_market()]), FakeClob({})))
    assert report.status == "NON_INITIALISE" and report.exit_code == EXIT_HALT
    assert not paths[0].exists()


def test_truncated_ledger_is_detected_by_anchor(env, ledger, paths):
    make, db, _ = env
    buy(ledger, "op1", token="tokZ", shares="10", price="0.5")
    db.set_anchor(ledger.state.last_seq, ledger.state.last_hash, NOW)
    first = paths[0].read_text().splitlines()[0]
    paths[0].write_text(first + "\n")  # on revient au capital initial
    report = run_cycle(make(FakeGamma([raw_market()]), FakeClob({})))
    assert report.status == "CORROMPU"


def test_real_mode_is_refused(env):
    make, _, _ = env
    settings = load_settings()
    settings["mode"] = "real"
    with pytest.raises(RealModeForbidden):
        run_cycle(make(FakeGamma(), FakeClob({}), settings))


def test_cycle_settles_resolved_position_before_trading(env, paths):
    make, _, clock = env
    gamma = FakeGamma([raw_market()])
    run_cycle(make(gamma, FakeClob({"tokA": good_book()})))
    pos = Ledger.open(paths[0], KEY, paths[1]).state.positions["tokA"]
    # Le marché se résout en faveur de « Yes » (jeton tokA).
    gamma.markets = [raw_market(closed=True, active=False, umaResolutionStatus="resolved",
                                outcomePrices=json.dumps(["1", "0"]))]
    clock["now"] = NOW + timedelta(days=1)
    report = run_cycle(make(gamma, FakeClob({})))
    assert report.counts["settled"] == 1, report.messages
    state = Ledger.open(paths[0], KEY, paths[1]).state
    assert not state.positions
    assert state.cash == D("1000") - pos.cost_basis + pos.shares
    assert state.realized_pnl == pos.shares - pos.cost_basis


def test_milestone_is_recorded_in_the_cycle(env, paths):
    make, _, clock = env
    gamma = FakeGamma([raw_market()])
    run_cycle(make(gamma, FakeClob({"tokA": good_book()})))
    # Une énorme position gagnante fait franchir 1 500…
    ledger = Ledger.open(paths[0], KEY, paths[1])
    ledger.record_buy(op_id="big", token_id="tokZ", market_id="mZ", event_id="eZ", outcome="Yes",
                      shares="1500", price="0.5", fee="0", strategy_id="t", strategy_version="0", now=NOW)
    ledger.record_settlement(op_id="sZ", token_id="tokZ", payout_per_share="1", resolution="Yes", now=NOW)
    clock["now"] = NOW + timedelta(hours=1)
    report = run_cycle(make(gamma, FakeClob({"tokA": good_book(ts=clock["now"])})))
    assert any("palier 1500" in m for m in report.milestones), report.milestones
    state = Ledger.open(paths[0], KEY, paths[1]).state
    assert state.next_target == D("2250")


def test_repository_config_is_the_single_strategy_with_its_ghost_conditions():
    """29/10/2026 (Etienne) : « les mêmes conditions que sur l'ancien fantôme » : Kelly entier × santé (½ tant que rien
    n'est prouvé, renfort jusqu'à 2), mise ≤ 25 % du capital, pas de réserve, ordres posés gardés en match, antécédents."""
    loaded = load_strategies(ROOT / "config" / "strategies.yaml")
    assert [s.strategy_id for s in loaded] == ["nf_sport_patiente_prix_850_875"]  # laboratoire dédié
    assert loaded[0].params["kelly_fraction"] == D("1.0")
    assert loaded[0].annuler_au_coup_d_envoi is False  # comme dans l'ancien labo (ses 85 positions mesurées)
    settings = load_settings()
    assert settings["principal"] == {"max_mise": 0.25}  # aucune règle du principal de l'ancien labo
    assert settings["sante_strategies"] == {"min_resolved": 20, "base": 0.5, "proven": 1.0, "disproven": 0.25,
                                            "suspension_min_closed": 20,
                                            "renfort": {"min_operations": 30, "confiance_jour_min": 0.8, "paris_fictifs": 30, "max": 2.0}}
    assert (ROOT / settings["paths"]["antecedents"]).exists()


def test_crash_mid_cycle_then_clean_resume(env, paths):
    """Panne inattendue pendant un cycle : rien d'écrit à moitié, le cycle suivant reprend."""
    make, _, clock = env

    class CrashingClob(FakeClob):
        def get_books(self, token_ids):
            raise RuntimeError("panne imprévue (test)")
    report = run_cycle(make(FakeGamma([raw_market()]), CrashingClob({})))
    assert report.status == "ERREUR" and report.exit_code == 1
    before = Ledger.open(paths[0], KEY, paths[1]).state
    assert before.cash == D("1000") and not before.positions
    clock["now"] = NOW + timedelta(minutes=5)
    report = run_cycle(make(FakeGamma([raw_market()]), FakeClob({"tokA": good_book(ts=clock["now"])})))
    assert report.status == "OK" and report.counts["fills"] == 1


# --------------------------------------------------------------------------- ordres posés

def _patient(max_proposals="3"):
    inner = pipeline_strategy()
    inner.params["max_proposals_per_cycle"] = D(max_proposals)
    return PatientOrders(inner, 1)


def test_resting_order_is_placed_then_filled_only_when_traded_through(env, paths):
    make, db, clock = env
    # Cycle 1 : écart 0,89 / 0,90 → achat posé à 0,89 (1 cran au-dessus de l'acheteur, sous le vendeur).
    report = run_cycle(make(FakeGamma([raw_market()]), FakeClob({"tokA": good_book()}), strategies=[_patient()]))
    assert report.status == "OK", report.messages
    assert "BUY" not in _ledger_types(paths[0])  # rien n'est exécuté au moment de la pose
    orders = db.open_orders()
    assert len(orders) == 1 and orders[0]["portfolio"] == "principal"
    assert json.loads(orders[0]["proposal"])["max_price"] == "0.89"

    # Cycle 2 : le meilleur vendeur touche 0,89 → pas d'exécution (file d'attente inconnue).
    clock["now"] = NOW + timedelta(minutes=5)
    touch = make_book("tokA", bids=(("0.88", "500"),), asks=(("0.89", "500"),), ts=clock["now"])
    run_cycle(make(FakeGamma([raw_market()]), FakeClob({"tokA": touch}), strategies=[_patient()]))
    assert "BUY" not in _ledger_types(paths[0])

    # La stratégie a remis son prix à jour (0,88 / 0,89 → achat posé à 0,88) : l'ancien ordre est remplacé.
    assert {json.loads(o["proposal"])["max_price"] for o in db.open_orders()} == {"0.88"}
    assert db.conn.execute("SELECT COUNT(*) FROM pending_orders WHERE status='REMPLACE'").fetchone()[0] == 1

    # Cycle 3 : des vendeurs à 0,87 → le marché est passé au travers : 30 parts au prix posé 0,88.
    clock["now"] = NOW + timedelta(minutes=10)
    through = make_book("tokA", bids=(("0.86", "500"),), asks=(("0.87", "30"), ("0.95", "500")), ts=clock["now"])
    run_cycle(make(FakeGamma([raw_market()]), FakeClob({"tokA": through}), strategies=[_patient()]))
    state = Ledger.open(paths[0], KEY, paths[1]).state
    pos = state.positions["tokA"]
    # 30 parts passées au travers, moitié accessible : 15 au prix posé 0,88 (sans frais : barème
    # taker seulement).
    assert pos.shares == D("15") and pos.cost_basis == D("15") * D("0.88")
    assert state.fees_paid == 0
    buys = [json.loads(l) for l in paths[0].read_text().splitlines() if json.loads(l)["type"] == "BUY"]
    assert buys[0]["data"]["extra"]["order_type"] == "limite"


def test_resting_order_expires_and_never_fills_after(env, paths):
    make, db, clock = env
    settings = load_settings()
    settings["limit_orders"] = {"max_age_hours": 0.05, "max_open_per_portfolio": 30}  # 3 minutes
    run_cycle(make(FakeGamma([raw_market()]), FakeClob({"tokA": good_book()}), settings, [_patient()]))
    assert len(db.open_orders()) == 1
    clock["now"] = NOW + timedelta(minutes=5)
    through = make_book("tokA", bids=(("0.70", "500"),), asks=(("0.71", "500"),), ts=clock["now"])
    run_cycle(make(FakeGamma([raw_market()]), FakeClob({"tokA": through}), settings, [pipeline_strategy()]))
    assert "BUY" not in _ledger_types(paths[0])
    statuses = {r["status"] for r in db.conn.execute("SELECT status FROM pending_orders")}
    assert statuses == {"EXPIRE"}


def test_resting_buys_cannot_reserve_more_than_the_cash(env, paths):
    make, db, _ = env
    markets = [raw_market(str(i), yes_token=f"y{i}", no_token=f"n{i}", event_id=f"e{i}") for i in range(6)]
    books = {f"y{i}": good_book(f"y{i}") for i in range(6)}
    run_cycle(make(FakeGamma(markets), FakeClob(books), _full_kelly(), strategies=[_patient("6")]))
    reserved = sum(D(o["remaining"]) * D(json.loads(o["proposal"])["max_price"]) for o in db.open_orders("principal"))
    assert 0 < reserved <= D("1000")
    reasons = [r[0] for r in db.conn.execute("SELECT reasons FROM decisions WHERE accepted = 0")]
    assert any("réservé" in r for r in reasons)  # mises de 25 % : le 5e ordre ne tient plus dans le solde


def test_resting_orders_left_by_the_old_ghost_portfolios_are_cancelled(env, paths):
    """29/10/2026 : plus de fantômes. Leurs ordres posés encore ouverts dans la base sont annulés au premier cycle
    (sinon ils resteraient « ouverts » pour toujours dans la base et le tableau de bord)."""
    make, db, _ = env
    ghost = Proposal(op_id="vieux", side="BUY", market_id="1", event_id="ev1", token_id="tokA", outcome="Yes",
                     est_probability=D("0.9"), prob_low=D("0.88"), prob_high=D("0.92"), max_price=D("0.85"),
                     shares=D("10"), net_edge=D("0.03"), sources=("t",), uncertainty="t", invalidation="t",
                     horizon_hours=D("5"), strategy_id="nf", strategy_version="1")
    db.add_pending_order("fantome:nf_v1", ghost, NOW, NOW + timedelta(hours=5))
    run_cycle(make(FakeGamma([raw_market()]), FakeClob({"tokA": good_book()})))
    assert db.conn.execute("SELECT status FROM pending_orders WHERE op_id='vieux'").fetchone()[0] == "ANNULE"


def test_orders_of_the_same_portfolio_share_the_book_in_a_cycle(env, paths, tmp_path):
    """Deux stratégies sur le même jeton, au même cycle : la seconde ne retrouve pas la liquidité déjà prise
    par la première (et ne rachète pas le même jeton)."""
    from polymarket_agent.main import CycleReport, Portfolio, _execute
    from polymarket_agent.sources.polymarket_gamma import parse_market
    make, _, _ = env
    first, second = pipeline_strategy(), pipeline_strategy()
    second.strategy_id = "naive_bis"
    deps = make(FakeGamma([raw_market()]), FakeClob({"tokA": good_book()}), strategies=[first, second])
    ledger = Ledger.create(tmp_path / "p.jsonl", KEY, tmp_path / "p.lock", "1000", now=NOW)
    portfolio = Portfolio("principal", ledger, [first, second])
    market = parse_market(raw_market())
    _execute(deps, portfolio, [market], {"tokA": good_book()}, CycleReport("c1"), "b1", [market])
    assert ledger.state.positions["tokA"].shares == D("10")  # 10 au total (25 % de 40), pas 20


def test_a_book_reread_is_reused_within_seconds(env):
    """19/10/2026 : un carnet relu il y a moins de 20 s dans le même cycle n'est pas relu une deuxième fois."""
    from polymarket_agent.main import _reread_book
    make, _, clock = env

    class Counting(FakeClob):
        reads = 0

        def get_book(self, token_id):
            Counting.reads += 1
            return good_book(ts=clock["now"])
    deps = make(FakeGamma([raw_market()]), Counting({"tokA": good_book()}))
    cache = {}
    assert _reread_book(deps, "tokA", cache) is _reread_book(deps, "tokA", cache) and Counting.reads == 1
    clock["now"] = NOW + timedelta(seconds=30)  # trop vieux : relu
    _reread_book(deps, "tokA", cache)
    _reread_book(deps, "tokA", None)  # sans mémoire : toujours relu
    assert Counting.reads == 3


def test_every_cycle_beats_and_publishes_timings_and_abstentions(env, paths):
    make, db, clock = env
    deps = make(FakeGamma([raw_market()]), FakeClob({"tokA": good_book()}))

    def beats():
        return sum(t == "HEARTBEAT" for t in _ledger_types(paths[0]))
    run_cycle(deps)
    clock["now"] = NOW + timedelta(minutes=5)
    run_cycle(deps)
    assert beats() == 2
    timings = json.loads(db.get_value("durees_cycle"))["etapes"]
    assert {"journal", "reglements", "scanner", "carnets", "strategie", "total"} <= set(timings)
    reasons = json.loads(db.get_value("abstentions"))["strategies"]["naive_favorite"]
    assert reasons["marches"] == 1


def test_once_gamma_reads_each_market_once():
    from polymarket_agent.main import _OnceGamma
    gamma = FakeGamma([raw_market("m1")])
    calls = []
    real = gamma.get_market
    gamma.get_market = lambda mid: calls.append(mid) or real(mid)
    once = _OnceGamma(gamma)
    assert once.get_market("m1") is once.get_market("m1") and calls == ["m1"]
    for _ in range(2):  # l'erreur aussi est gardée : un seul appel
        with pytest.raises(SourceUnavailable):
            once.get_market("inconnu")
    assert calls == ["m1", "inconnu"]
    assert once.list_markets() == gamma.markets  # le reste passe au vrai client


def test_settlement_reads_held_markets_by_batches():
    """21/10/2026 : 723 marchés détenus relus un par un (pause de 0,2 s entre deux requêtes) = 79 s
    de règlements. Lus par lots (fermés puis ouverts) ; un marché absent des lots ou un lot en erreur
    retombe sur la lecture une par une."""
    from polymarket_agent.main import _OnceGamma

    class Batch(FakeGamma):
        def __init__(self, markets):
            super().__init__(markets)
            self.batches, self.singles = [], []

        def markets_by_ids(self, ids, *, closed):
            self.batches.append((len(ids), closed))
            if closed and len(ids) == 1:
                raise SourceUnavailable("lot en panne (test)")
            return [m for m in self.markets if m["id"] in ids and bool(m.get("closed")) == closed]

        def get_market(self, market_id):
            self.singles.append(market_id)
            return super().get_market(market_id)
    gamma = Batch([dict(raw_market("1"), closed=True), raw_market("2"), raw_market("3")])
    once = _OnceGamma(gamma)
    once.prefetch(["1", "2", "3", "absent"], batch=3)
    assert gamma.batches == [(3, True), (3, False), (1, True), (1, False)]
    assert once.get_market("1")["closed"] is True and once.get_market("2")["id"] == "2" and gamma.singles == []
    with pytest.raises(SourceUnavailable):
        once.get_market("absent")  # absent des lots : lu une fois seul (inconnu du faux Gamma)
    assert gamma.singles == ["absent"]
    once.prefetch(["1"], batch=1)  # lot d'un seul : rien à gagner, lecture normale
    assert len(gamma.batches) == 4

    class Poisoned(Batch):  # Gamma refuse tout lot qui contient un marché supprimé
        def markets_by_ids(self, ids, *, closed):
            self.batches.append((len(ids), closed))
            if "mort" in ids:
                raise SourceUnavailable("HTTP 404 (test)")
            return [m for m in self.markets if m["id"] in ids and bool(m.get("closed")) == closed]
    ids = [str(i) for i in range(16)] + ["mort"]
    poisoned = Poisoned([dict(raw_market(i), closed=True) for i in ids if i != "mort"])
    once = _OnceGamma(poisoned)
    once.prefetch(ids, batch=100)
    # Coupé en deux (deux fois) : 12 marchés lus par lots ; le petit lot du marché supprimé est lu un par un.
    assert len(once.cache) == 12 and not once.down
    assert once.get_market("15")["id"] == "15" and poisoned.singles == ["15"]

    class Down(Batch):
        def markets_by_ids(self, ids, *, closed):
            self.batches.append((len(ids), closed))
            raise SourceUnavailable("HTTP 404 (panne, test)")
    down = Down([raw_market("1")])
    once = _OnceGamma(down)
    once.prefetch([str(i) for i in range(250)], batch=100)
    assert once.down and len(down.batches) <= 30  # premier lot seulement, puis arrêt
    with pytest.raises(SourceUnavailable):
        once.get_market("1")
    assert down.singles == []  # pas 250 lectures une par une pendant une panne


def test_strategie_muette_sans_raison_dit_pourquoi():
    """28/10/2026 : une stratégie qui ne propose rien et ne note aucune raison dit pourquoi malgré elle."""
    from polymarket_agent.main import _raison_par_defaut
    from polymarket_agent.strategy_engine import StrategyOutput
    out = StrategyOutput()
    _raison_par_defaut(out, 427)
    assert list(out.abstentions.values()) == [427]
    assert "raison non notée" in next(iter(out.abstentions))
    notee = StrategyOutput()
    notee.abstain("carnet trop écarté")
    _raison_par_defaut(notee, 427)
    assert list(notee.abstentions) == ["carnet trop écarté"]  # une raison déjà notée n'est pas écrasée
    vide = StrategyOutput()
    _raison_par_defaut(vide, 0)
    assert vide.abstentions == {}  # aucun marché vu : rien à dire


def _kickoff_run(make, clock, flag):
    """Ordre posé 7 minutes avant le coup d'envoi, puis un vendeur qui traverse son prix 3 minutes après."""
    start = (NOW + timedelta(minutes=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
    market = raw_market(gameStartTime=start)

    def strategies():
        s = _patient()
        s.annuler_au_coup_d_envoi = flag
        return [s]
    run_cycle(make(FakeGamma([market]), FakeClob({"tokA": good_book()}), strategies=strategies()))
    clock["now"] = NOW + timedelta(minutes=10)  # le match a commencé
    through = make_book("tokA", bids=(("0.86", "500"),), asks=(("0.87", "500"),), ts=clock["now"])
    run_cycle(make(FakeGamma([market]), FakeClob({"tokA": through}), strategies=strategies()))


def test_resting_buy_is_served_during_the_match_by_default(env, paths):
    """Réglage du dépôt (`annuler_au_coup_d_envoi: false`) : comme dans l'ancien labo, l'ordre posé avant le match
    reste posé et peut être servi pendant le jeu."""
    make, _, clock = env
    _kickoff_run(make, clock, False)
    assert "BUY" in _ledger_types(paths[0])


def test_strategy_flagged_annuler_au_coup_d_envoi_loses_its_resting_order_at_kickoff(env, paths):
    """Enquête du 28/10/2026 : les ordres posés servis en match perdaient −9,8 % par pari (sélection adverse).
    Avec `annuler_au_coup_d_envoi`, l'ordre posé tombe au coup d'envoi."""
    make, db, clock = env
    _kickoff_run(make, clock, True)
    assert "BUY" not in _ledger_types(paths[0])
    statuses = {r["status"] for r in db.conn.execute("SELECT status FROM pending_orders WHERE status != 'REMPLACE'")}
    assert statuses == {"ANNULE"}


def test_resting_buy_before_the_match_is_still_served_when_flagged(env, paths):
    make, _, clock = env
    start = (NOW + timedelta(hours=3)).strftime("%Y-%m-%dT%H:%M:%SZ")
    market = raw_market(gameStartTime=start)
    s = _patient()
    s.annuler_au_coup_d_envoi = True
    run_cycle(make(FakeGamma([market]), FakeClob({"tokA": good_book()}), strategies=[s]))
    clock["now"] = NOW + timedelta(minutes=10)
    through = make_book("tokA", bids=(("0.86", "500"),), asks=(("0.87", "500"),), ts=clock["now"])
    run_cycle(make(FakeGamma([market]), FakeClob({"tokA": through}), strategies=[s]))
    assert "BUY" in _ledger_types(paths[0])
