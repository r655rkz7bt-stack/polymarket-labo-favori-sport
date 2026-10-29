"""« Aujourd'hui » (lot C, 15/10/2026) : les dernières 24 h racontées en phrases simples.

Pour Etienne, sur le tableau de bord : ce qu'est devenu le capital, ce qu'ont fait les
stratégies, qui mène l'arène et qui a bougé, si la machine a bien tourné. Tout vient des
journaux vérifiés, de la base et de l'audit ; aucune phrase n'est écrite par une IA.
Les textes venus de l'extérieur (questions de marchés) restent des données : la page les
insère comme du texte, jamais comme du HTML.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from polymarket_agent import arena_history
from polymarket_agent.reporting import closed_trades


def _eur(x: float) -> str:
    return f"{x:,.2f} €".replace(",", " ").replace(".", ",")


def _signed(x: float) -> str:
    return ("+" if x > 0 else "−" if x < 0 else "") + _eur(abs(x))


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _since(entries: list[dict], since: str) -> list[dict]:
    return [e for e in entries if e["ts"] >= since]


def _value_at(curve: list[list], when: str, default: float) -> float:
    value = default
    for ts, v in curve:
        if ts > when:
            break
        value = v
    return value


def build(data: dict, entries: dict[str, list[dict]], questions: dict[str, str], db, now: datetime) -> list[dict]:
    """Liste de {"sujet", "texte"} ; `entries` = journal de chaque portefeuille (dont « principal »)."""
    since = (now - timedelta(hours=24)).isoformat()
    out: list[dict] = []

    def say(subject: str, text: str) -> None:
        out.append({"sujet": subject, "texte": text})

    # 1. Capital principal.
    cap = data["capital"]
    if cap.get("faillite"):
        say("capital", f"FAILLITE : le trading est arrêté ({cap.get('raison_faillite', '')}).")
    curve = data.get("courbe") or []
    now_v = curve[-1][1] if curve else data["capital_initial"]
    before = _value_at(curve, since, data["capital_initial"])
    delta = now_v - before
    say("capital", f"Le capital principal vaut {_eur(now_v)} (positions comptées à leur prix actuel), "
                   f"{'contre' if delta else 'comme'} {_eur(before)} il y a 24 h"
                   + (f" : {_signed(delta)}." if delta else "."))

    # 2. Champion et capital principal.
    promos = [e for e in _since(entries.get("principal", []), since) if e["type"] == "PROMOTION"]
    for e in promos:
        who = e["data"].get("champion") or ""
        hour = e["ts"][11:16]
        say("arene", (f"À {hour} UTC, {who} est devenue championne : le capital principal suit ses décisions."
                      if who else f"À {hour} UTC, plus aucune championne : le capital principal ne prend plus de nouvelles positions."))
    if not promos:
        champ = cap.get("champion")
        say("arene", f"Championne en titre : {champ}." if champ else
            "Aucune stratégie n'a encore prouvé qu'elle gagne : le capital principal attend.")

    # 3. Ce qu'ont fait les stratégies (tous portefeuilles).
    buys = sells = 0
    closed_day = []
    for name, ents in entries.items():
        day = _since(ents, since)
        buys += sum(1 for e in day if e["type"] == "BUY")
        sells += sum(1 for e in day if e["type"] in ("SELL", "SETTLE"))
        closed_day += [dict(t, portefeuille=name) for t in closed_trades(ents) if t["closed_at"] >= since]
    if buys or sells:
        say("operations", f"{_plural(buys, 'achat', 'achats')} et {_plural(sells, 'vente ou règlement', 'ventes ou règlements')} "
                          f"en 24 h, tous portefeuilles confondus (principal et fantômes).")
    else:
        say("operations", "Aucune opération en 24 h : aucune stratégie n'a trouvé d'occasion assez bonne.")
    if closed_day:
        total = sum(float(t["pnl"]) for t in closed_day)
        wins = sum(1 for t in closed_day if t["pnl"] > Decimal(0))
        say("operations", f"{_plural(len(closed_day), 'opération clôturée', 'opérations clôturées')} : {wins} gagnante(s), "
                          f"{len(closed_day) - wins} perdante(s), résultat net {_signed(total)} (frais compris).")
        best = max(closed_day, key=lambda t: t["pnl"])
        worst = min(closed_day, key=lambda t: t["pnl"])
        if best["pnl"] > 0:
            say("operations", f"Meilleure : {_signed(float(best['pnl']))} par {best['strategy'].split(' v')[0]} sur "
                              f"« {questions.get(best['market_id'], best['market_id'])[:90]} » ({best['outcome']}).")
        if worst["pnl"] < 0 and worst is not best:
            say("operations", f"Pire : {_signed(float(worst['pnl']))} par {worst['strategy'].split(' v')[0]} sur "
                              f"« {questions.get(worst['market_id'], worst['market_id'])[:90]} » ({worst['outcome']}).")

    # 4. Arène : qui mène, qui a bougé depuis hier.
    arena = [a for a in data.get("arene", []) if not a["eliminee"]]
    if arena:
        initial = float(data["capital_initial"])
        traded = sorted((a for a in arena if a["operations"] > 0), key=lambda a: -a["capital"])
        idle = len(arena) - len(traded)
        if traded:
            lead = traded[0]
            say("arene", f"Meilleure stratégie qui a tradé : {lead['nom']}, {_eur(lead['capital'])} réalisés "
                         f"({_signed(lead['capital'] - initial)}) sur "
                         f"{_plural(lead['operations'], 'opération clôturée', 'opérations clôturées')}.")
            winners = sum(1 for a in traded if a["capital"] > initial)
            say("arene", f"{winners} sur {len(traded)} stratégie(s) ayant tradé gagnent de l'argent"
                         + (f" ; {idle} n'ont encore clôturé aucune opération." if idle else "."))
        else:
            say("arene", f"Aucune des {len(arena)} stratégies n'a encore clôturé d'opération.")
        before_rank = arena_history.rank_at(db, now - timedelta(hours=24))
        moves = [(before_rank[a["nom"]] - (i + 1), a["nom"]) for i, a in enumerate(arena) if a["nom"] in before_rank]
        up = max(moves, default=(0, ""))
        down = min(moves, default=(0, ""))
        if up[0] > 0:
            say("arene", f"Plus forte remontée : {up[1]}, +{_plural(up[0], 'place', 'places')} depuis hier.")
        if down[0] < 0:
            say("arene", f"Plus forte chute : {down[1]}, −{_plural(-down[0], 'place', 'places')} depuis hier.")
        suspended = [a["nom"] for a in arena if (a.get("sante") or {}).get("suspendue")]
        if suspended:
            say("arene", "Suspendue(s) car les frais mangent tout leur avantage : " + ", ".join(suspended) + ".")

    # 5. La machine.
    health = data.get("sante", {})
    cycles = health.get("cycles_24h") or {}
    total_cycles = sum(cycles.values())
    errors = sum(v for k, v in cycles.items() if k not in ("OK", "DEGRADE"))
    degraded = cycles.get("DEGRADE", 0)
    say("machine", f"{_plural(total_cycles, 'cycle', 'cycles')} en 24 h (un toutes les 2 min)"
                   + (f", dont {errors} en erreur" if errors else ", aucun en erreur")
                   + (f" et {degraded} sans trading faute de données" if degraded else "") + ".")
    audit = data.get("audit")
    if audit:
        say("machine", f"Audit indépendant : {audit['verifiees']} exécutions vérifiées contre les vrais prix, "
                       f"{audit.get('reglements_verifies', 0)} règlements conformes au résultat officiel, "
                       + (f"{audit['anomalies']} ANOMALIE(S) — voir la console." if audit["anomalies"]
                          else "aucune anomalie."))
    states = (data.get("sources") or {}).get("etat") or {}
    down = sorted(n for n, s in states.items() if s.startswith("indisponible"))
    obs = sum(((data.get("sources") or {}).get("observations_24h") or {}).values())
    say("machine", f"{obs} informations collectées en 24 h"
                   + (f" ; en panne au dernier essai : {', '.join(down)}." if down else " ; toutes les sources actives répondent."))
    return out
