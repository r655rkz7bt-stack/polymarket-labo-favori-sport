"""Santé de la stratégie : la taille de ses mises, aux mêmes règles que son ancien fantôme.

Reprise (décision d'Etienne, 29/10/2026) de strategy_health.py du labo d'origine, qui réglait la mise du fantôme
(config/settings.yaml → `sante_strategies`) :
1. Mise prudente : tant que moins de `min_resolved` prévisions sont jugées (paris réglés), la mise est réduite à `base`
   (½ Kelly) ; prouvée meilleure que le marché (« avance » = Brier du prix payé − Brier de l'estimation > 0) → `proven`
   (mise entière) ; prouvée moins bonne → `disproven` (¼).
2. Renfort : après `renfort.min_operations` paris terminés et avec une confiance par jour d'au moins
   `renfort.confiance_jour_min`, une stratégie qui prouve PLUS d'avantage qu'elle n'en suppose mise davantage :
   (paris gagnés − attendus) / (paris + `paris_fictifs`) ÷ avantage supposé, entre 1 et `renfort.max`.
3. Suspension : gagnante avant frais mais perdante après, au-delà de `suspension_min_closed` paris : plus aucun achat.
Le tout est calculé sur ses antécédents (antecedents.py) PUIS sur le journal d'ici, comme si le fantôme continuait.
Seule différence : une prévision est jugée quand son pari est réglé dans un des deux journaux (l'ancien labo
interrogeait aussi Gamma pour les paris revendus, que cette stratégie ne fait jamais).
"""

from __future__ import annotations

import json

from polymarket_agent.antecedents import since_last_start
from polymarket_agent.confiance import day_confidence

DEFAULTS = {"min_resolved": 20, "base": 0.5, "proven": 1.0, "disproven": 0.25, "suspension_min_closed": 20}


def config(settings: dict) -> dict:
    return dict(DEFAULTS, **(settings.get("sante_strategies") or {}))


def forecasts(entries: list[dict]) -> list[dict]:
    """Achats d'un journal : {token_id, p, price}. Le premier achat d'un jeton compte (un renforcement n'est pas une
    nouvelle prévision)."""
    out, seen = [], set()
    for e in entries:
        if e.get("type") != "BUY":
            continue
        d = e["data"]
        p = (d.get("extra") or {}).get("est_probability")
        if d.get("token_id") in seen or p is None:
            continue
        try:
            out.append({"token_id": d["token_id"], "p": float(p), "price": float(d["price"])})
        except (KeyError, TypeError, ValueError):
            continue
        seen.add(d["token_id"])
    return out


def settled_outcomes(entries: list[dict]) -> dict[str, float]:
    """Ce qu'a payé chaque jeton réglé (SETTLE) du journal."""
    out: dict[str, float] = {}
    for e in entries:
        if e.get("type") == "SETTLE":
            try:
                out[str(e["data"]["token_id"])] = float(e["data"]["payout_per_share"])
            except (KeyError, TypeError, ValueError):
                continue
    return out


def score(journals: list[list[dict]]) -> dict:
    """{"jugees", "brier", "brier_marche", "avance"} sur les prévisions réglées de chaque journal (antécédents, puis ici)."""
    n, b_s, b_m = 0, 0.0, 0.0
    for entries in journals:
        settled = settled_outcomes(entries)
        for f in forecasts(entries):
            payout = settled.get(f["token_id"])
            if payout is not None:
                n += 1
                b_s += (f["p"] - payout) ** 2
                b_m += (f["price"] - payout) ** 2
    return {"jugees": n, "brier": round(b_s / n, 4) if n else None,
            "brier_marche": round(b_m / n, 4) if n else None, "avance": round((b_m - b_s) / n, 4) if n else None}


def prudence(s: dict, cfg: dict) -> float:
    """Fraction de la mise habituelle (Kelly de la stratégie) autorisée."""
    if s["jugees"] < int(cfg["min_resolved"]) or s["avance"] is None:
        return float(cfg["base"])
    return float(cfg["proven"]) if s["avance"] > 0 else float(cfg["disproven"])


def edge_row(trades: list[dict]) -> dict:
    """Paris terminés, paris gagnés en plus de l'attendu, avantage supposé moyen à l'achat, confiance par jour."""
    excess, assumed = 0.0, []
    for t in trades:
        cost, bought = float(t.get("cost") or 0), float(t.get("shares_bought") or 0)
        if cost <= 0 or bought <= 0:
            continue
        price = cost / bought
        excess += (float(t["pnl"]) > 0) - price
        if t.get("est_probability") not in (None, ""):
            assumed.append(float(t["est_probability"]) - price)
    return {"closed_trades": len(trades), "excess_wins": round(excess, 4),
            "assumed_edge": round(sum(assumed) / len(assumed), 4) if assumed else None,
            "day_confidence": day_confidence(trades) if trades else None}


def boost(row: dict, cfg: dict) -> float:
    """Renfort, entre 1 et `renfort.max` (1 sans réglage `renfort`)."""
    rule = cfg.get("renfort") or {}
    n = int(row.get("closed_trades") or 0)
    excess, assumed, conf = row.get("excess_wins"), row.get("assumed_edge"), row.get("day_confidence")
    if not rule or n < int(rule.get("min_operations", 30)) or excess is None or not assumed or float(assumed) <= 0:
        return 1.0
    if conf is None or float(conf) < float(rule.get("confiance_jour_min", 0.8)):
        return 1.0
    measured = float(excess) / (n + float(rule.get("paris_fictifs", 30)))
    return round(min(float(rule.get("max", 2.0)), max(1.0, measured / float(assumed))), 4)


def suspension(trades: list[dict], cfg: dict) -> str | None:
    """Raison de suspendre (ou None) : gagnante avant frais, perdante après, après assez de paris terminés."""
    if len(trades) < int(cfg["suspension_min_closed"]):
        return None
    net = sum(float(t["pnl"]) for t in trades)
    fees = sum(float(t.get("fees") or 0) for t in trades)
    if net < 0 and net + fees >= 0:
        return f"ne gagne pas ses frais : gains bruts {net + fees:.2f}, frais {fees:.2f} sur {len(trades)} paris terminés"
    return None


def assess(history: list[dict], live: list[dict], trades: list[dict], settings: dict) -> dict:
    """Facteur appliqué à chaque mise de la stratégie (0 si suspendue) et ce qui le justifie (affiché par le tableau de bord)."""
    cfg = config(settings)
    s = score([history, since_last_start(live)])
    row = edge_row(trades)
    p, r, why = prudence(s, cfg), boost(row, cfg), suspension(trades, cfg)
    return dict(s, **row, prudence=p, renfort=r, suspendue=why, facteur=0.0 if why else round(p * r, 4))


def save(db, data: dict) -> None:
    db.set_value("sante", json.dumps(data, ensure_ascii=False))


def load(db) -> dict | None:
    try:
        return json.loads(db.get_value("sante") or "null")
    except ValueError:
        return None
