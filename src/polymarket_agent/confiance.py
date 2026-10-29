"""Indices de confiance de la stratégie, calculés sur les opérations clôturées du capital principal.

Repris de l'arène de l'ancien laboratoire (arena.py, retirée le 29/10/2026) : ce sont les mêmes
formules, appliquées au seul portefeuille qui reste.
- Score t (moyenne / erreur type) des RENDEMENTS par opération (résultat ÷ mise) : la taille des mises
  ne compte pas (18/10/2026 : sur les euros, le score mesurait surtout la taille des mises gagnantes).
- Confiance en % : probabilité, d'après le score t, que le rendement moyen soit vraiment au-dessus de 0.
- Confiance « par jour » : paris gagnés contre paris attendus d'après les prix payés, journée par
  journée ; une seule journée exceptionnelle compte moins qu'avec le score t.
- Verdict : intervalle à 90 % du rendement moyen, par bootstrap sur les ÉVÉNEMENTS (matchs), car deux
  paris sur un même match ne sont pas deux preuves indépendantes.
Mise en garde (docs/CONTEXTE.md) : la stratégie a été choisie parmi des dizaines de cousines ; une
confiance de 90-95 % sur peu de paris peut encore venir de la chance.
"""

from __future__ import annotations

import math

from polymarket_agent.reporting import cluster_mean_ci

MIN_TRADES = 20  # opérations clôturées avant de conclure quoi que ce soit


def trade_returns(trades: list[dict]) -> list[float]:
    """Rendement de chaque opération clôturée (résultat / mise)."""
    return [float(t["pnl"]) / float(t["cost"]) for t in trades if float(t.get("cost") or 0) > 0]


def t_stat(values: list[float]) -> float:
    """Moyenne / erreur type (t de Student) ; 0 sous 2 valeurs ; grand si toutes égales et positives."""
    n = len(values)
    if n < 2:
        return 0.0
    m = sum(values) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in values) / (n - 1))
    return round(m / (sd / math.sqrt(n)), 3) if sd > 0 else (99.0 if m > 0 else 0.0)


def confidence_percent(t: float) -> int:
    """t = 0 → 50 % (pile ou face), 1,5 → 93 %, 2 → 98 %, −1,5 → 7 % (loi normale)."""
    return int(round(100 * 0.5 * (1 + math.erf(float(t) / math.sqrt(2)))))


def day_confidence(trades: list[dict]) -> float:
    """Confiance « par jour », de 0 à 1. Pour chaque opération, le prix payé est la chance de gagner
    annoncée par le marché ; on compare, jour par jour (jour de clôture), les paris gagnés à ceux
    attendus. z = somme des écarts / écart type, l'écart type étant le plus grand des deux : celui du
    hasard (Σ p(1−p)) ou celui mesuré entre les journées."""
    days: dict = {}
    var_model = 0.0
    for t in trades:
        cost, bought = float(t.get("cost") or 0), float(t.get("shares_bought") or 0)
        if cost <= 0 or bought <= 0:
            continue
        p = min(max(cost / bought, 0.01), 0.99)
        day = str(t["closed_at"])[:10]
        days[day] = days.get(day, 0.0) + (float(t["pnl"]) > 0) - p
        var_model += p * (1 - p)
    var = max(var_model, sum(e * e for e in days.values()))
    z = sum(days.values()) / math.sqrt(var) if var > 0 else 0.0
    return round(0.5 * (1 + math.erf(z / math.sqrt(2))), 4)


def summary(trades: list[dict]) -> dict:
    """Chiffres de confiance pour le tableau de bord (tous nuls sans opération clôturée)."""
    rets = trade_returns(trades)
    wins = sum(1 for t in trades if float(t["pnl"]) > 0)
    expected = sum(float(t["cost"]) / float(t["shares_bought"]) for t in trades
                   if float(t.get("cost") or 0) > 0 and float(t.get("shares_bought") or 0) > 0)
    staked = sum(float(t["cost"]) for t in trades)
    pnl = sum(float(t["pnl"]) for t in trades)
    t = t_stat(rets)
    by_event: dict[str, list[float]] = {}
    for trade, r in zip((x for x in trades if float(x.get("cost") or 0) > 0), rets):
        by_event.setdefault(str(trade.get("event_id") or trade.get("market_id")), []).append(r)
    _, lo, hi, n_events = cluster_mean_ci(by_event)
    if len(trades) < MIN_TRADES:
        verdict = f"données insuffisantes (moins de {MIN_TRADES} opérations clôturées)"
    elif hi < 0:
        verdict = "ne fonctionne pas"
    elif lo > 0:
        verdict = "prometteuse"
    else:
        verdict = "indéterminée (le résultat peut être dû au hasard)"
    return {"operations": len(trades), "evenements": n_events, "gagnees": wins,
            "attendues": round(expected, 2), "en_plus": round(wins - expected, 2),
            "mise_totale": round(staked, 2), "resultat": round(pnl, 2),
            "rendement_par_euro": round(pnl / staked, 4) if staked else None,
            "rendement_moyen": round(sum(rets) / len(rets), 4) if rets else None,
            "ic90": [round(lo, 4), round(hi, 4)] if len(rets) > 1 else None,
            "score_t": t, "confiance_pct": confidence_percent(t),
            "confiance_jour_pct": round(100 * day_confidence(trades)) if trades else 50,
            "verdict": verdict}
