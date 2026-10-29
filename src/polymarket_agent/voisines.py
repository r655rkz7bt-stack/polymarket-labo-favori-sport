"""Vue du fantôme : les marchés que ses voisines de l'ancien labo lui faisaient voir (décision d'Etienne, 29/10/2026).

« Je veux comme le fantôme exactement. » Dans l'ancien labo (r655rkz7bt-stack/Polymarket-autonomus-lab), le fantôme de
nf_sport_patiente_prix_850_875 voyait les 150 marchés les plus échangés PLUS tous les marchés demandés par les autres
stratégies (`candidates`, jusqu'à `scanner.max_extra_markets`, jamais atteint : 237 ajoutés le 29/10 à 9 h 15 UTC).
Sur ses 86 marchés achetés, beaucoup d'e-sport et de tennis ITF peu échangés venaient de ces demandes.

Ce module reprend MOT POUR MOT (copié de l'ancien labo au 29/10/2026, commit cab602d) la seule partie « quels marchés
demander » des stratégies de l'ancien labo encore actives (non éliminées) dont les demandes peuvent contenir des marchés
de sport (config/voisines.yaml). Leurs paris ne sont PAS repris : elles ne tradent pas ici. Pas reprises : les demandes
limitées à d'autres familles (politique, crypto, météo) ou à d'autres sujets (CPI, emploi, box-office, App Store, séismes,
détroit d'Ormuz, métaux, SpaceX), qui ne portent jamais sur le sport : le filtre `familles: [sports]` de la stratégie les
écarterait de toute façon avant qu'elle choisisse.
Limites : les demandes qui dépendent de sources externes (fatigue_nhl : calendrier NHL ; références Kalshi, PredictIt,
Manifold, transcriptions, tweets) ne sont pas reprises ; et l'ancien labo continue d'évoluer (stratégies créées ou
éliminées) : cette vue est celle du 29/10/2026.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import yaml

from polymarket_agent.correlation import correlation_group
from polymarket_agent.models import MarketInfo
from polymarket_agent.strategy_engine import StrategyConfigError, load_params, market_family


# ------------------------------------------------------------------ classements (ancien pareto.py)
_DISCIPLINES = (  # premier motif trouvé dans le libellé (minuscules) ; l'ordre compte
    ("counter-strike", r"counter-strike|\bcs2?\b"), ("lol", r"\blol\b|league of legends"),
    ("dota", r"dota"), ("valorant", r"valorant"), ("esport", r"\(bo\d\)|map \d|game \d winner"),
    ("tennis", r"\bopen\b|atp|wta|challenger|wimbledon|roland|masters 1000|qualification:"),
    ("nfl", r"\bnfl\b|chiefs|eagles|cowboys|patriots|packers|49ers|steelers|ravens|bills|jets|giants|"
            r"commanders|dolphins|broncos|raiders|chargers|bengals|browns|texans|colts|titans|jaguars|"
            r"vikings|bears|lions|saints|falcons|panthers|buccaneers|cardinals|rams|seahawks"),
    ("mlb", r"\bmlb\b|yankees|dodgers|mets|red sox|cubs|astros|braves|phillies|padres|mariners|blue jays|"
            r"orioles|guardians|rays|brewers|twins|tigers|royals|white sox|athletics|angels|rangers|"
            r"pirates|reds|marlins|nationals|rockies|diamondbacks|giants"),
    ("nba", r"\bnba\b|lakers|celtics|warriors|knicks|bucks|nuggets|heat|suns|76ers|mavericks|clippers"),
    ("nhl", r"\bnhl\b|maple leafs|canadiens|bruins|oilers|flames|canucks|penguins|blackhawks|avalanche|"
            r"lightning|capitals|islanders|predators|kraken|senators|sabres|hurricanes|stars|wild"),
    ("football", r" fc\b|\bfc |\bac\b|\bafc\b|united|city|real madrid|barcelona|inter |juventus|bayern|"
                 r"psg|win on 20\d\d|end in a draw|ligue|premier league|serie a|bundesliga|la liga|uefa"),
    ("ufc", r"\bufc\b|fight night"), ("golf", r"\bpga\b|golf|masters"), ("f1", r"grand prix|\bf1\b"),
)
_BET_TYPES = (("handicap", r"spread|handicap|\(-\d|\(\+\d"), ("total", r"o/u|over/under|total"),
              ("score_exact", r"exact score"), ("partie", r"map \d|game \d|set \d|1h|2h|half|quarter|inning"),
              ("nul", r"draw"))


_BY_SOURCE = {"atptour.com": "tennis", "wtatennis.com": "tennis", "itftennis.com": "tennis",
              "hltv.org": "counter-strike", "vlr.gg": "valorant", "lolesports.com": "lol",
              "liquipedia.net": "esport", "mlb.com": "mlb", "nhl.com": "nhl", "nfl.com": "nfl", "nba.com": "nba",
              "ufc.com": "ufc", "pgatour.com": "golf", "formula1.com": "f1"}


def discipline(question: str, source: str = "") -> str:
    """Discipline d'un marché : d'abord sa source de résolution (atptour.com → tennis…), sinon
    le libellé (la moitié des matchs de tennis n'ont que les noms des joueurs)."""
    from polymarket_agent.resolution_sources import domain_of
    domain = domain_of(source) if source else ""
    for known, name in _BY_SOURCE.items():
        if domain == known or domain.endswith("." + known):
            return name
    q = (question or "").lower()
    for name, pattern in _DISCIPLINES:
        if re.search(pattern, q):
            return name
    return "autre"


def bet_type(question: str, sports_market_type: str | None = None) -> str:
    """vainqueur, handicap, total, score_exact, partie (manche, carte…), nul, autre."""
    if sports_market_type:
        return {"moneyline": "vainqueur", "spreads": "handicap", "totals": "total",
                "child_moneyline": "partie"}.get(sports_market_type, sports_market_type)
    q = (question or "").lower()
    for name, pattern in _BET_TYPES:
        if re.search(pattern, q):
            return name
    return "vainqueur" if re.search(r" vs\.? | v ", q) or re.search(r"will .+ win", q) else "autre"


def excluded(market: MarketInfo, words: tuple[str, ...]) -> bool:
    q = (market.question or "").lower()
    return any(w in q for w in words)


# ------------------------------------------------------------------ demandes (ancien strategy_engine.py, strategies_v3.py)
class _Demande:
    """Une stratégie de l'ancien labo réduite à ses demandes de marchés."""

    strategy_id = "demande"

    def __init__(self, version: str, created_at: str, params: dict[str, Decimal]):
        self.version, self.created_at, self.params = version, created_at, params


class Favori(_Demande):
    """naive_favorite : seulement sa demande de marchés (familles + `max_candidates`)."""

    strategy_id = "naive_favorite"
    families: tuple[str, ...] = ()
    exclude_words: tuple[str, ...] = ()

    def candidates(self, markets: list[MarketInfo]) -> list[MarketInfo]:
        """Variante ciblée (familles + `max_candidates`) : demande ses propres marchés au scanner,
        ceux dont l'issue visée (favori ou outsider) est dans la plage de prix."""
        p = self.params
        if not self.families or p.get("max_candidates", Decimal("0")) <= 0:
            return []
        out = []
        for m in markets:
            if len(m.outcome_prices) != 2 or market_family(m) not in self.families or excluded(m, self.exclude_words):
                continue
            if p.get("holding_only", Decimal("0")) > 0 and not m.holding_rewards:
                continue
            price = min(m.outcome_prices) if p.get("underdog", Decimal("0")) > 0 else max(m.outcome_prices)
            if p["min_ask"] - Decimal("0.02") <= price <= p["max_ask"]:
                out.append(m)
        out.sort(key=lambda m: -m.volume_24h)
        return out[: int(p["max_candidates"])]


FILTER_KEYS = ("familles", "prix_min", "prix_max", "heures_min", "heures_max", "issue", "heure_utc",
               "disciplines", "types_pari", "echeance_min", "echeance_max", "ecart_max", "sources",
               "sources_exclues", "question_contient", "question_exclut", "prix_final", "un_match", "candidats_min", "titre_contient")


class Filtre:
    """« Fille » d'une stratégie (entonnoir, 17/10/2026) : la stratégie mère ne joue qu'UN MORCEAU
    des marchés, défini par `filtres` (YAML). Comparer chaque fille à sa mère dans l'arène montre
    quel morceau fait vraiment gagner (ou perdre). Filtres possibles :
      familles : [sports, politics, …] (market_family) ; heures_min / heures_max : délai jusqu'à
      l'échéance (au coup d'envoi en sport) ; prix_min / prix_max : prix payé ;
      issue : [oui, non, autre] (autre = équipe, joueur…) ; heure_utc : [début, fin[ (heure de la
      journée en temps universel, ex. [4, 12] ; [20, 4] passe minuit) ;
      disciplines : [tennis, counter-strike, nfl, …] et types_pari : [vainqueur, handicap, total, …]
      (20/10/2026, entonnoir 80/20 : même classement que pareto.discipline / pareto.bet_type) ;
      echeance_min / echeance_max : heures jusqu'à l'ÉCHÉANCE du marché (pas le coup d'envoi),
      comme le délai noté à l'achat ; ecart_max : écart achat-vente maximal (marchés liquides) ;
      sources : domaines de la source de résolution acceptés, ex. [itftennis.com] pour le seul
      circuit ITF (20/10/2026 : outsiders ITF sous-cotés, ATP et WTA non) ; sources_exclues :
      domaines refusés (favoris ITF : −13,9 % aux vrais prix, experiments/achat_echelonne.md) ;
      question_contient : le marché est gardé si sa question contient un de ces mots (sans tenir
      compte des majuscules) ; question_exclut : refusé s'il en contient un (21/10/2026 : annonceurs
      NFL gagnants, Coupe du monde perdante, experiments/mentions_annonceurs.md) ;
      titre_contient : le marché est gardé si le TITRE de son événement contient un de ces mots (25/10/2026 : « Games Total »
      ne nomme pas le jeu, le titre oui : « LoL: T1 vs Gen.G (BO3) ») ;
      un_match : true → seulement les paris sur UN match (Gamma donne un `sportsMarketType`) ; false → seulement les
      marchés de saison (gagnant du championnat…). Refuse, si true, les
      marchés de saison ou de prix (Ballon d'Or, Coupe, Série mondiale), que la famille « sports »
      laisse passer (23/10/2026 : 359 € = 30 % du capital sur « Mbappé gagne le Ballon d'Or, NON » à 0,94,
      experiments/enquetes/2026-10-23_marches_de_saison_dans_le_sport.md) ;
      prix_final : true (avec `limit_ticks`) → prix_min / prix_max portent sur le prix de l'ORDRE POSÉ
      et non sur le prix du vendeur (21/10/2026 : sinon une fille « prix payé 0,875-0,90 » achète à 0,84,
      experiments/enquetes/2026-10-21_filtres_et_prix_executable.md) ;
      candidats_min : au moins N marchés « candidat » (même événement, marchés liés `neg_risk`) parmi les marchés vus (24/10/2026,
      experiments/primaires.md : le NON d'un candidat à 10-30 % rapporte surtout dans les champs de 5 candidats et plus).
    Les filtres de marché s'appliquent AVANT la stratégie (elle choisit ses meilleurs paris dans le
    morceau) ; ceux qui dépendent de l'issue choisie (prix, issue) APRÈS. Ventes jamais filtrées."""

    def __init__(self, strategy, filters: dict):
        unknown = set(filters) - set(FILTER_KEYS)
        if unknown:
            raise StrategyConfigError(f"filtres inconnus : {sorted(unknown)}")
        self.inner, self.filters = strategy, dict(filters)
        self._field: dict[str, int] = {}
        self.strategy_id, self.version = strategy.strategy_id, strategy.version
        self.needs_price_history = getattr(strategy, "needs_price_history", False)
        if hasattr(strategy, "exits"):
            self.exits = strategy.exits

    def _reject(self, m: MarketInfo, now) -> str | None:
        """Nom du premier filtre qui écarte ce marché, ou None s'il passe (25/10/2026 : sans cela une fille sourde, `candidats_non`,
        n'avait AUCUNE raison d'abstention et paraissait seulement « muette »)."""
        f = self.filters
        if "familles" in f and market_family(m) not in f["familles"]:
            return "familles"
        if ("echeance_min" in f or "echeance_max" in f) and now is not None:
            if m.end_date is None:
                return "echeance"
            hours = (m.end_date - now).total_seconds() / 3600
            if not float(f.get("echeance_min", -1e9)) <= hours <= float(f.get("echeance_max", 1e9)):
                return "echeance"
        if "un_match" in f and bool(f["un_match"]) != bool(getattr(m, "sports_market_type", None)):
            return "un_match"
        if "candidats_min" in f and (not m.neg_risk or self._field.get(m.event_id, 0) < int(f["candidats_min"])):
            return "candidats_min"
        if "titre_contient" in f:
            titre = (m.event_title or "").lower()
            if not any(str(w).lower() in titre for w in f["titre_contient"]):
                return "titre_contient"
        question = (m.question or "").lower()
        if "question_contient" in f and not any(str(w).lower() in question for w in f["question_contient"]):
            return "question_contient"
        if any(str(w).lower() in question for w in f.get("question_exclut", ())):
            return "question_exclut"
        if "ecart_max" in f and (m.spread is None or float(m.spread) > float(f["ecart_max"])):
            return "ecart_max"
        if "sources" in f or "sources_exclues" in f:
            from polymarket_agent.resolution_sources import domain_of
            domain = domain_of(m.resolution_source) if m.resolution_source else ""
            if "sources" in f and domain not in f["sources"]:
                return "sources"
            if domain and domain in f.get("sources_exclues", ()):
                return "sources_exclues"
        if "disciplines" in f or "types_pari" in f:
            if "disciplines" in f and discipline(m.question, m.resolution_source) not in f["disciplines"]:
                return "disciplines"
            if "types_pari" in f and bet_type(m.question, getattr(m, "sports_market_type", None)) not in f["types_pari"]:
                return "types_pari"
        if ("heures_min" in f or "heures_max" in f) and now is not None:
            ref = m.game_start or m.end_date
            if ref is None:
                return "heures"
            hours = (ref - now).total_seconds() / 3600
            if not float(f.get("heures_min", -1e9)) <= hours <= float(f.get("heures_max", 1e9)):
                return "heures"
        return None

    def _market_ok(self, m: MarketInfo, now) -> bool:
        return self._reject(m, now) is None

    # Filtres assez étroits pour qu'une fille demande ELLE-MÊME ses marchés au scanner (21/10/2026) :
    # sans cela, statu_quo_ia (« best ai model ») et outsider_itf (source itftennis.com) ne voyaient
    # que les 150 marchés les plus échangés, où leurs marchés n'entrent presque jamais.
    OWN_MARKETS = ("question_contient", "sources")
    OWN_MARKETS_MAX = 25

    def _count_field(self, markets) -> None:
        if "candidats_min" in self.filters:
            self._field = {}
            for m in markets:
                if m.neg_risk:
                    self._field[m.event_id] = self._field.get(m.event_id, 0) + 1

    def candidates(self, markets):
        self._count_field(markets)
        inner = getattr(self.inner, "candidates", None)
        out = [m for m in inner(markets) if self._market_ok(m, None)] if inner else []
        if not out and any(k in self.filters for k in self.OWN_MARKETS):
            # 24/10/2026 : la fenêtre d'échéance compte AVANT le plafond de 25 marchés (sinon les 25 plus échangés, hors fenêtre, chassent les bons)
            now = datetime.now(timezone.utc) if ("echeance_min" in self.filters or "echeance_max" in self.filters) else None
            out = sorted((m for m in markets if self._market_ok(m, now)), key=lambda m: -float(m.volume_24h or 0))
            out = out[: self.OWN_MARKETS_MAX]
        return out

    def __getattr__(self, name):
        return getattr(self.inner, name)


def _yes_no(m: MarketInfo) -> bool:
    return len(m.token_ids) == 2 and [str(o).lower() for o in m.outcomes] == ["yes", "no"]


DRAME = re.compile(
    r"\b(invade|invades|invasion|war\b|wars\b|military action|military clash|strikes? on|attacks?\b|airstrikes?|"
    r"missiles?|nuclear|bomb|troops|blockade|annex|annexes|annexation|ceasefire|cease-fire|truce|clash|"
    r"declares? war|hostilities|drone strike|conflict|"
    r"out as|out by|out in|ousted|resigns?|resignation|step down|steps down|leaves? office|removed|impeach|"
    r"impeached|impeachment|coup\b|assassinat\w*|dies?\b|death|dead\b|jail|prison|arrest(ed)?|indicted|"
    r"convicted|extradited|detained|captured|25th amendment|deported|expelled|"
    r"bankrupt\w*|default|recession|collapse|crash|insolven\w*|bank run|hyperinflation|depeg|de-peg|"
    r"delisted|shutdown|nationaliz\w*|"
    r"aliens?|ufo|uap|extraterrestrial|non-human|jesus|rapture|asteroid|apocalypse|martial law|secede|"
    r"secession|civil war|pandemic|outbreak|lockdown|state of emergency|meteor)", re.I)
STATU_QUO = re.compile(r"\b(remain|remains|still|survive|survives|stay|stays|keep|keeps|alive|retain|hold on)\b"
                       r"|sentenced to between", re.I)


class VagueVolume(_Demande):
    """Vague de volume (22/10/2026, idée des skills Polymarket de mjunaidca, `momentum_scanner`) : un
    marché qui s'échange au moins `surge_min` fois plus que sa moyenne des 7 derniers jours (Gamma :
    volume du jour contre volume de la semaine / 7), et dont le prix du OUI a bougé d'au moins
    `min_move` en 24 h (marchés d'au moins `min_age_days` jours). `direction` = 1 : on SUIT le mouvement (la foule a une information) ;
    `direction` = -1 : on le CONTRE (la foule s'emballe). Chance estimée = milieu du carnet de l'issue
    achetée + `shift`. Pas testée sur le passé (Gamma ne garde pas le volume de la semaine d'hier) :
    l'arène mesure les deux sens. Pourquoi ça peut rater : une vague de volume suit souvent une
    nouvelle déjà dans le prix."""

    strategy_id = "vague_volume"
    required = ("surge_min", "min_move", "side_min", "side_max", "shift", "uncertainty", "min_net_edge",
                "max_proposals_per_cycle")

    def _surge(self, m: MarketInfo, now: datetime | None) -> float | None:
        """Volume du jour ÷ moyenne des jours PRÉCÉDENTS de la semaine. Un marché de moins de
        `min_age_days` jours n'a pas de référence (créé hier : volume de la semaine = volume du jour,
        fausse vague ×7, vu le 22/10 sur les marchés météo du jour)."""
        if not _yes_no(m) or m.created_at is None or now is None:
            return None
        # Près de l'échéance (Bitcoin du jour, match du soir), le volume monte toujours : pas une information.
        if m.end_date is None or (m.end_date - now).total_seconds() / 3600 < float(self.params.get("min_hours_to_end", 48)):
            return None
        age = (now - m.created_at).total_seconds() / 86400
        if age < float(self.params.get("min_age_days", 3)):
            return None
        days = min(7.0, age)
        before = (float(m.volume_1wk or 0) - float(m.volume_24h or 0)) / max(days - 1, 1)
        # Marché endormi (quelques dollars par jour) : un rapport ×8 000 ne veut rien dire.
        if before < float(self.params.get("min_base_volume", 500)):
            return None
        return float(m.volume_24h or 0) / before

    def candidates(self, markets) -> list[MarketInfo]:
        from datetime import timezone
        p, now = self.params, datetime.now(timezone.utc)
        out = [(r, m) for m in markets for r in [self._surge(m, now)] if r is not None and r >= float(p["surge_min"])
               and m.price_change_1d is not None and abs(m.price_change_1d) >= float(p["min_move"])]
        out.sort(key=lambda x: -x[0])
        return [m for _, m in out[: int(p.get("max_candidates", 30))]]


class MoinsDeCartes(_Demande):
    """E-sport en 2 ou 3 manches gagnantes (BO3, BO5) : le marché « Games Total » (nombre de cartes
    jouées) semble calculé comme si chaque carte était un tirage indépendant ; en vrai, l'équipe la
    plus forte du jour gagne souvent 2-0. On achète « Under » entre 6 h et 1 h avant le match.
    Sur le passé, aux prix payés : +13,5 % par pari, 457 paris, 412 matchs, t +2,75, 10 mois sur 12
    (experiments/pistes_sport_coherence.md ; trouvé parmi ~90 essais : fantôme, l'arène tranche).
    Pourquoi ça peut rater : sélection parmi beaucoup d'essais ; marchés minces."""

    strategy_id = "moins_de_cartes"
    required = ("min_hours", "max_hours", "price_min", "price_max", "shift", "uncertainty", "min_net_edge",
                "max_proposals_per_cycle")
    WORDS = ("(BO3)", "(BO5)")

    def _ok(self, m: MarketInfo, now: datetime | None) -> bool:
        if (m.sports_market_type != "totals" or not (m.question or "").startswith("Games Total")
                or [str(o).lower() for o in m.outcomes] != ["over", "under"] or len(m.token_ids) != 2
                or not any(w in (m.event_title or "") for w in self.WORDS) or m.game_start is None or now is None):
            return False
        hours = (m.game_start - now).total_seconds() / 3600
        return float(self.params["min_hours"]) <= hours <= float(self.params["max_hours"])

    def candidates(self, markets):
        from datetime import timezone
        now = datetime.now(timezone.utc)
        out = [m for m in markets if self._ok(m, now)]
        out.sort(key=lambda m: m.game_start)
        return out[: int(self.params.get("max_candidates", 40))]


class MoinsDeKills(_Demande):
    """E-sport, totaux de kills d'une partie (« Total Kills Over/Under 30.5 in Game 1? », LoL) : le « Over » est trop cher, le « Under » un peu trop bon marché.
    Chaque partie a 3 à 6 lignes qui se gagnent ou se perdent ensemble : on n'achète « Under » qu'UNE fois par partie, sur la ligne dont le prix est le plus
    proche de 0,5, entre 12 h et 2 h avant le coup d'envoi. Sur le passé (27/10/2026, 4 jeux, vrais échanges, frais payés, un pari par partie, payé 0,35-0,65, 3 h avant) :
    « Under » +9,5 % (588 paris, 330 gagnés pour 297,3, t par événement 2,75, 8 mois sur 9), « Over » −10,5 % (675 paris, t −3,6) (experiments/lol_kills.md).
    Pourquoi ça peut rater : le gain s'efface quand on exige beaucoup de parts (≥ 400 parts : +4 %), et l'indice vient d'un seul jeu (LoL)."""

    strategy_id = "moins_de_kills"
    required = ("min_hours", "max_hours", "price_min", "price_max", "shift", "uncertainty", "min_net_edge",
                "max_proposals_per_cycle")
    GAME = re.compile(r"in Game (\d)")

    def _ok(self, m: MarketInfo, now: datetime | None) -> bool:
        if (m.sports_market_type != "kill_over_under_game" or [str(o).lower() for o in m.outcomes] != ["over", "under"]
                or len(m.token_ids) != 2 or m.game_start is None or now is None):
            return False
        hours = (m.game_start - now).total_seconds() / 3600
        return float(self.params["min_hours"]) <= hours <= float(self.params["max_hours"])

    def candidates(self, markets):
        from datetime import timezone
        now = datetime.now(timezone.utc)
        out = [m for m in markets if self._ok(m, now)]
        out.sort(key=lambda m: m.game_start)
        return out[: int(self.params.get("max_candidates", 60))]


class NonAuxDrames(_Demande):
    """« Est-ce que <drame> arrivera avant <date> ? » (invasion, chef d'État renversé, faillite,
    extraterrestres…) à 2-15 % plus d'un mois avant l'échéance : la foule paie trop cher le frisson.
    On achète le NON, au plus 2 % du capital par événement. Sur le passé (2024-2026, prix payés) :
    149 paris, +4,6 % par pari, 2 drames arrivés pour 9,3 attendus, t par événement 2,81 ; les marchés
    longs qui ne sont pas des drames : +0,9 % seulement (experiments/pistes_crypto_elections_drames.md).
    Pourquoi ça peut rater : 2 pertes seulement dans l'échantillon ; une guerre fait tout perdre d'un coup."""

    strategy_id = "non_aux_drames"
    required = ("min_days", "max_days", "yes_min", "yes_max", "min_volume", "yes_factor", "uncertainty",
                "stake_fraction", "min_net_edge", "max_proposals_per_cycle")

    def _ok(self, m: MarketInfo, now: datetime | None) -> bool:
        q = m.question or ""
        if not _yes_no(m) or m.end_date is None or now is None or not DRAME.search(q) or STATU_QUO.search(q):
            return False
        days = (m.end_date - now).total_seconds() / 86400
        return (float(self.params["min_days"]) <= days <= float(self.params["max_days"])
                and float(m.volume_total or 0) >= float(self.params["min_volume"]))

    def candidates(self, markets):
        from datetime import timezone
        now = datetime.now(timezone.utc)
        p = self.params
        out = [m for m in markets if self._ok(m, now) and m.outcome_prices
               and float(p["yes_min"]) <= float(m.outcome_prices[0]) <= float(p["yes_max"])]
        out.sort(key=lambda m: -float(m.volume_total or 0))
        return out[: int(p.get("max_candidates", 40))]


# ------------------------------------------------------------------ seuils en dollars (ancien crypto_model.py)
ABOVE = ("above", "over", "greater than", "higher than", "at or above", "au-dessus")
BELOW = ("below", "under", "less than", "lower than", "at or below", "en dessous", "sous")
TOUCH_DOWN = ("dip to", "fall to", "drop to", "dip below", "fall below", "drop below", "crash to")
TOUCH_UP = ("reach", "hit", "rise to", "pump to", "climb to", "surpass", "break")
_PRICE = re.compile(r"\$\s?([\d][\d,]*(?:\.\d+)?)\s?([kKmM])?\b")


@dataclass(frozen=True)
class ThresholdQuestion:
    kind: str  # "above", "below", "touch_up", "touch_down"
    threshold: float


def parse_question(question: str) -> ThresholdQuestion | None:
    q = question.lower()
    if " between " in q or " and $" in q:
        return None
    m = _PRICE.search(question)
    if not m:
        return None
    value = float(m.group(1).replace(",", ""))
    suffix = (m.group(2) or "").lower()
    value *= 1000 if suffix == "k" else 1_000_000 if suffix == "m" else 1
    if value <= 0:
        return None
    for words, kind in ((TOUCH_DOWN, "touch_down"), (TOUCH_UP, "touch_up"), (ABOVE, "above"), (BELOW, "below")):
        if any(re.search(rf"\b{re.escape(w)}\b", q) for w in words):  # mots entiers (« hit » ≠ « white »)
            return ThresholdQuestion(kind, value)
    return None


DEADLINE = re.compile(r"\b(by|before)\b", re.IGNORECASE)


class CrossMarketLadder(_Demande):
    """Échelles de seuils (« BTC au-dessus de 80 k le 15 », « … de 82 k le 15 ») : P(> K bas) >= P(> K haut).

    Si « Oui au-dessus de K bas » + « Non au-dessus de K haut » coûtent moins de 1 (frais compris),
    on achète les deux : au moins l'un des deux paie toujours 1 (S > K bas, ou S <= K haut),
    les deux si K bas < S <= K haut. Gain garanti, sauf risque d'exécution d'une seule jambe.
    """

    strategy_id = "cross_market"
    required = ("min_net_edge", "stake_fraction", "max_proposals_per_cycle")

    @staticmethod
    def _key(market: MarketInfo):
        q = parse_question(market.question)
        if q is None or q.kind not in ("above", "below") or market.end_date is None:
            return None
        return (correlation_group(market), q.kind, market.end_date.isoformat()), q.threshold

    def candidates(self, markets: list[MarketInfo]) -> list[MarketInfo]:
        groups = defaultdict(list)
        for m in markets:
            k = self._key(m)
            if k:
                groups[k[0]].append(m)
        return [m for g in groups.values() if len(g) >= 2 for m in g]


class EchelleDates(_Demande):
    """Même événement, plusieurs échéances (« d'ici juin », « d'ici décembre ») : la plus proche ne
    peut pas valoir plus que la plus lointaine. OUI(lointaine) + NON(proche) paie toujours au
    moins 1 (l'événement arrive avant la date proche, entre les deux, ou jamais)."""

    strategy_id = "echelle_dates"

    @staticmethod
    def _group(markets) -> dict:
        groups = defaultdict(list)
        for m in markets:
            if _yes_no(m) and DEADLINE.search(m.question or "") and m.end_date is not None and m.event_id:
                groups[m.event_id].append(m)
        return {k: sorted(v, key=lambda m: m.end_date) for k, v in groups.items()
                if len({m.end_date.date() for m in v}) >= 2}

    def candidates(self, markets):
        return [m for g in self._group(markets).values() for m in g]


CLASSES = {"naive_favorite": Favori, "vague_volume": VagueVolume, "moins_de_cartes": MoinsDeCartes,
           "moins_de_kills": MoinsDeKills, "non_aux_drames": NonAuxDrames, "cross_market": CrossMarketLadder,
           "echelle_dates": EchelleDates}


def charger(path: Path) -> list:
    """config/voisines.yaml → demandes, montées comme dans l'ancien `load_strategies` (familles, mots exclus, filtres).
    `limit_ticks` ne change pas les demandes (le mode patient renvoie à la stratégie qu'il enveloppe)."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    out = []
    for strategy_id, spec in raw.items():
        if not isinstance(spec, dict) or not spec.get("enabled"):
            continue
        cls = CLASSES.get(str(spec.get("class", strategy_id)))
        if cls is None:
            raise StrategyConfigError(f"voisine inconnue : {spec.get('class', strategy_id)}")
        demande = cls(str(spec["version"]), str(spec["created_at"]), load_params(spec.get("params")))
        demande.strategy_id = strategy_id
        if "familles" in spec:
            demande.families = tuple(str(f) for f in spec["familles"])
        if "exclure_mots" in spec:
            demande.exclude_words = tuple(str(w).lower() for w in spec["exclure_mots"])
        filters = dict(spec.get("filtres") or {})
        filters.pop("prix_final", None)
        if "filtres" in spec:
            demande = Filtre(demande, filters)
        out.append(demande)
    return out


def demandes(voisines: list, markets: list[MarketInfo]) -> list[list[MarketInfo]]:
    """Les marchés de SPORT demandés par chaque voisine, dans l'ordre (comme l'ancien cycle, étape 4 bis). Seul le sport
    est gardé : la stratégie écarte tout autre marché (`familles: [sports]`, même `market_family`) avant de choisir, un
    autre marché ne changerait aucune de ses décisions et coûterait un carnet de plus à lire à chaque cycle."""
    return [[m for m in v.candidates(markets) if market_family(m) == "sports"] for v in voisines]
