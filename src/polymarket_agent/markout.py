"""Sélection adverse des ordres POSÉS : « markout » (16/10/2026).

Le piège n°1 des « faiseurs » : un ordre posé est surtout servi quand il a tort (le prix part
contre lui juste après). On le mesure, sans rien changer au capital :
- markout d'une EXÉCUTION posée = (prix du milieu plus tard − prix payé) pour un achat,
  (prix reçu − milieu plus tard) pour une vente, par part, à 5 min, 1 h, 6 h et 24 h, et à la
  résolution (1 ou 0) quand le marché est réglé ;
- même calcul pour les ordres posés JAMAIS servis (expirés ou remplacés) : ce qu'ils auraient
  « gagné ». Si les ordres servis font nettement moins bien que les ordres manqués, la stratégie
  se fait choisir contre elle (exemple de forum : 55 % de réussite pour les servis, 80 % pour les
  manqués).
Milieux lus dans `book_snapshots` (photos de chaque cycle, base tournante : les vieux points
disparaissent, d'où le nombre de cas affiché par horizon).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

HORIZONS = {"5 min": timedelta(minutes=5), "1 h": timedelta(hours=1), "6 h": timedelta(hours=6),
            "24 h": timedelta(hours=24)}
TOLERANCE = 0.5  # un point accepté jusqu'à 1,5 × l'horizon (cycles irréguliers)


def _mid_after(db, token: str, when: datetime, delta: timedelta) -> float | None:
    start = when + delta
    row = db.conn.execute(
        "SELECT best_bid, best_ask FROM book_snapshots WHERE token_id=? AND collected_at >= ? "
        "AND collected_at <= ? AND best_bid IS NOT NULL AND best_ask IS NOT NULL ORDER BY collected_at LIMIT 1",
        (token, start.isoformat(), (start + delta * TOLERANCE).isoformat())).fetchone()
    if row is None:
        return None
    try:
        return (float(row[0]) + float(row[1])) / 2
    except (TypeError, ValueError):
        return None


def _settled(entries: list[dict]) -> dict[str, float]:
    return {e["data"]["token_id"]: float(e["data"]["payout_per_share"])
            for e in entries if e["type"] == "SETTLE" and "payout_per_share" in e["data"]}


def resting_fills(entries: list[dict]) -> list[dict]:
    """Exécutions d'ordres posés du journal : jeton, sens, prix, parts, heure, stratégie."""
    out = []
    for e in entries:
        if e["type"] not in ("BUY", "SELL"):
            continue
        d = e["data"]
        if (d.get("extra") or {}).get("order_type") != "limite":
            continue
        out.append({"token": d["token_id"], "side": e["type"], "price": float(d["price"]),
                    "shares": float(d["shares"]), "at": datetime.fromisoformat(e["ts"]),
                    "strategy": d.get("strategy_id") or (d.get("extra") or {}).get("strategy_id", "")})
    return out


def _score(side: str, price: float, later: float) -> float:
    return later - price if side == "BUY" else price - later


def _aggregate(cases: list[tuple[str, float, float, datetime, str]], db, settled: dict[str, float]) -> dict:
    """cases : (sens, prix, poids, heure, jeton) → markout moyen pondéré par horizon."""
    out = {}
    for name, delta in HORIZONS.items():
        tot = w = 0.0
        n = 0
        for side, price, weight, at, token in cases:
            later = _mid_after(db, token, at, delta)
            if later is None:
                continue
            tot += _score(side, price, later) * weight
            w += weight
            n += 1
        out[name] = {"cas": n, "moyenne": round(tot / w, 4) if w else None}
    tot = w = 0.0
    n = 0
    for side, price, weight, at, token in cases:
        if token in settled:
            tot += _score(side, price, settled[token]) * weight
            w += weight
            n += 1
    out["résolution"] = {"cas": n, "moyenne": round(tot / w, 4) if w else None}
    return out


def unfilled_orders(db, portfolio: str, since: datetime, limit: int = 1500) -> list[dict]:
    rows = db.conn.execute(
        "SELECT token_id, side, created_at, proposal FROM pending_orders WHERE portfolio=? AND fills=0 "
        "AND status IN ('EXPIRE', 'REMPLACE') AND created_at >= ? ORDER BY created_at DESC LIMIT ?",
        (portfolio, since.isoformat(), limit)).fetchall()
    out = []
    for token, side, created, proposal in rows:
        try:
            price = float(json.loads(proposal)["max_price"])
        except (ValueError, KeyError, TypeError):
            continue
        out.append({"token": token, "side": side, "price": price, "at": datetime.fromisoformat(created)})
    return out


def report(db, entries_by_portfolio: dict[str, list[dict]], now: datetime, days: int = 7) -> dict[str, dict]:
    """{portefeuille: {"servis": {...}, "manques": {...}}} pour les portefeuilles qui posent des ordres."""
    since = now - timedelta(days=days)
    out = {}
    for name, entries in entries_by_portfolio.items():
        fills = [f for f in resting_fills(entries) if f["at"] >= since]
        missed = unfilled_orders(db, name, since)
        if not fills and not missed:
            continue
        settled = _settled(entries)
        out[name] = {
            "servis": dict(_aggregate([(f["side"], f["price"], f["shares"], f["at"], f["token"]) for f in fills],
                                      db, settled), executions=len(fills)),
            "manques": dict(_aggregate([(o["side"], o["price"], 1.0, o["at"], o["token"]) for o in missed],
                                       db, {}), ordres=len(missed)),
        }
    return out
