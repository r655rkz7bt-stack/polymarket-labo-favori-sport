"""« Aujourd'hui » (lot C, 15/10/2026) : les dernières 24 h racontées en phrases simples.

Pour Etienne, sur le tableau de bord : ce qu'est devenu le capital, ce qu'a fait la stratégie, où en
est sa confiance, si la machine a bien tourné. Tout vient du journal vérifié, de la base et de l'audit ;
aucune phrase n'est écrite par une IA. Les textes venus de l'extérieur (questions de marchés) restent
des données : la page les insère comme du texte, jamais comme du HTML.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from polymarket_agent.reporting import closed_trades


def _eur(x: float) -> str:
    return f"{x:,.2f} €".replace(",", " ").replace(".", ",")


def _signed(x: float) -> str:
    return ("+" if x > 0 else "−" if x < 0 else "") + _eur(abs(x))


def _plural(n: int, one: str, many: str) -> str:
    return f"{n} {one if n == 1 else many}"


def _value_at(curve: list[list], when: str, default: float) -> float:
    value = default
    for ts, v in curve:
        if ts > when:
            break
        value = v
    return value


def build(data: dict, entries: list[dict], questions: dict[str, str], now: datetime) -> list[dict]:
    """Liste de {"sujet", "texte"} ; `entries` = journal du capital principal."""
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

    # 2. Ce qu'a fait la stratégie.
    day = [e for e in entries if e["ts"] >= since]
    buys = sum(1 for e in day if e["type"] == "BUY")
    settled = sum(1 for e in day if e["type"] in ("SELL", "SETTLE"))
    closed_day = [t for t in closed_trades(entries) if t["closed_at"] >= since]
    if buys or settled:
        say("operations", f"{_plural(buys, 'achat', 'achats')} et {_plural(settled, 'règlement', 'règlements')} en 24 h.")
    else:
        say("operations", "Aucune opération en 24 h : la stratégie n'a pas trouvé d'occasion, ou ses ordres posés n'ont pas été servis.")
    if closed_day:
        total = sum(float(t["pnl"]) for t in closed_day)
        wins = sum(1 for t in closed_day if t["pnl"] > Decimal(0))
        say("operations", f"{_plural(len(closed_day), 'pari terminé', 'paris terminés')} : {wins} gagné(s), "
                          f"{len(closed_day) - wins} perdu(s), résultat net {_signed(total)} (frais compris).")
        best = max(closed_day, key=lambda t: t["pnl"])
        worst = min(closed_day, key=lambda t: t["pnl"])
        if best["pnl"] > 0:
            say("operations", f"Meilleur : {_signed(float(best['pnl']))} sur "
                              f"« {questions.get(best['market_id'], best['market_id'])[:90]} » ({best['outcome']}).")
        if worst["pnl"] < 0 and worst is not best:
            say("operations", f"Pire : {_signed(float(worst['pnl']))} sur "
                              f"« {questions.get(worst['market_id'], worst['market_id'])[:90]} » ({worst['outcome']}).")

    # 3. Confiance de la stratégie (tous les paris terminés depuis le départ).
    conf = (data.get("strategie") or {}).get("confiance") or {}
    if conf.get("operations"):
        expected = f"{conf['attendues']:.1f}".replace(".", ",")
        say("strategie", f"Depuis le départ : {_plural(conf['operations'], 'pari terminé', 'paris terminés')}, "
                         f"{conf['gagnees']} gagnés pour {expected} attendus d'après les prix payés.")
        say("strategie", f"Confiance {conf['confiance_pct']} % (par jour {conf['confiance_jour_pct']} %) : {conf['verdict']}.")
    else:
        say("strategie", "Aucun pari terminé depuis le départ : pas encore de mesure de confiance.")

    # 4. La machine.
    health = data.get("sante", {})
    cycles = health.get("cycles_24h") or {}
    total_cycles = sum(cycles.values())
    errors = sum(v for k, v in cycles.items() if k not in ("OK", "DEGRADE"))
    degraded = cycles.get("DEGRADE", 0)
    minutes = health.get("minutes_par_cycle")
    say("machine", f"{_plural(total_cycles, 'cycle', 'cycles')} en 24 h"
                   + (f" (un toutes les {minutes} min)" if minutes else "")
                   + (f", dont {errors} en erreur" if errors else ", aucun en erreur")
                   + (f" et {degraded} sans trading faute de données" if degraded else "") + ".")
    audit = data.get("audit")
    if audit:
        say("machine", f"Audit indépendant : {audit['verifiees']} exécutions vérifiées contre les vrais prix, "
                       f"{audit.get('reglements_verifies', 0)} règlements conformes au résultat officiel, "
                       + (f"{audit['anomalies']} ANOMALIE(S) — voir la console." if audit["anomalies"]
                          else "aucune anomalie."))
    return out
