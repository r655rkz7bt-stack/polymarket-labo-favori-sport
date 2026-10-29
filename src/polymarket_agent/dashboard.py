"""Tableau de bord (cahier des charges §4.K) : une page HTML statique, autonome, sans dépendance.

Publié sur GitHub Pages par le cycle et régénéré toutes les `dashboard.every_minutes`. Toutes les
données viennent du journal vérifié du capital principal et de la base SQLite. Depuis le 29/10/2026
(plus d'arène), la stratégie trade directement ce capital : la page montre ses résultats et ses
indices de confiance.

Sécurité : les textes venus de l'extérieur (questions de marchés, raisons, noms de sources)
sont insérés dans la page avec `textContent`, jamais comme du HTML. Le JSON embarqué
échappe `</` pour qu'aucun texte ne puisse fermer la balise <script>.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from polymarket_agent import antecedents, confiance, markout, sante, today
from polymarket_agent.config import resolve
from polymarket_agent.ledger import Ledger
from polymarket_agent.reporting import closed_trades, equity_curve, max_drawdown, read_entries

MAX_POINTS = 300


def _kv_json(db, key: str):
    try:
        return json.loads(db.get_value(key) or "null")
    except (ValueError, TypeError):
        return None


def _downsample(curve: list[tuple[str, Decimal]], n: int = MAX_POINTS) -> list[list]:
    if len(curve) > n:
        step = len(curve) / n
        curve = [curve[int(i * step)] for i in range(n - 1)] + [curve[-1]]
    return [[ts, float(v)] for ts, v in curve]


def _marks(db, tokens) -> dict[str, Decimal]:
    marks = {}
    for token in tokens:
        row = db.conn.execute("SELECT best_bid FROM book_snapshots WHERE token_id=? "
                              "ORDER BY collected_at DESC LIMIT 1", (token,)).fetchone()
        if row and row[0] is not None:
            marks[token] = Decimal(row[0])
    return marks


BLOCKS = ("resume", "aujourdhui", "organisme", "principal", "capital", "strategie", "positions", "ordres-poses",
          "journal", "decisions-recentes", "systeme")
# Règle n°1 : capital, solde disponible, faillite. Depuis le 17/10/2026, le cœur (« organisme ») porte
# le capital total, le solde disponible et l'état de faillite en tête de page : lui non plus ne se cache pas.
ALWAYS_VISIBLE = ("organisme", "resume", "capital")


def layout(root: Path) -> dict:
    """Mise en page réglée dans config/dashboard.yaml : ordre des blocs, blocs cachés,
    « À la une ». Fichier absent ou invalide : mise en page par défaut. Garde-fous : identifiants
    inconnus ignorés, « resume » et « capital » jamais cachés, au plus 5 points de 280 caractères."""
    import yaml
    try:
        raw = yaml.safe_load((Path(root) / "config" / "dashboard.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        raw = {}
    raw = raw if isinstance(raw, dict) else {}
    ordre = [b for b in (raw.get("ordre") or []) if b in BLOCKS]
    ordre = list(dict.fromkeys(ordre))
    masquer = [b for b in (raw.get("masquer") or []) if b in BLOCKS and b not in ALWAYS_VISIBLE]
    # Blocs repliés au départ (une ligne de résumé ; Etienne les rouvre d'un toucher). Jamais ceux de la règle n°1.
    replier = [b for b in (raw.get("replier") or []) if b in BLOCKS and b not in ALWAYS_VISIBLE]
    une = raw.get("a_la_une") if isinstance(raw.get("a_la_une"), dict) else {}
    points = [str(p).strip()[:280] for p in (une.get("points") or []) if str(p).strip()][:5]
    return {"ordre": ordre, "masquer": masquer, "replier": list(dict.fromkeys(replier)),
            "a_la_une": {"date": str(une.get("date") or ""), "points": points}}


RAW_PREFIX = "https://raw.githubusercontent.com/"


def fiche(root: Path, settings: dict | None = None) -> dict:
    """Courbes de la stratégie (demande d'Etienne, 21/10/2026) : au toucher, la page charge le journal
    PUBLIC du capital principal (branche data, `journaux_url` de config/dashboard.yaml + ledger.jsonl)
    et calcule ses courbes à ce moment-là, dans le navigateur : rien n'est précalculé ici. Seule une
    adresse raw.githubusercontent.com est acceptée ; sinon la page la déduit de son adresse GitHub Pages."""
    import yaml
    try:
        raw = yaml.safe_load((Path(root) / "config" / "dashboard.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        raw = {}
    url = str((raw if isinstance(raw, dict) else {}).get("journaux_url") or "")
    return {"journaux": url if url.startswith(RAW_PREFIX) and url.endswith("/") else ""}


def principal_orders(entries: list[dict], questions: dict[str, str], open_orders: list[dict],
                     limit: int = 60) -> dict:
    """Ordres du capital principal (règle n°1) : exécutions du journal signé, la plus récente d'abord,
    et ordres posés encore ouverts."""
    token_market, token_outcome, rows = {}, {}, []
    counts = {"achats": 0, "ventes": 0, "reglements": 0}
    for e in entries:
        t, d = e.get("type"), e.get("data") or {}
        if t not in ("BUY", "SELL", "SETTLE"):
            continue
        tok = str(d.get("token_id"))
        if t == "BUY":
            token_market[tok], token_outcome[tok] = d.get("market_id", ""), d.get("outcome", "")
        mid = d.get("market_id") or token_market.get(tok, "")
        extra = d.get("extra") or {}
        row = {"date": e["ts"][:16], "sens": {"BUY": "achat", "SELL": "vente", "SETTLE": "règlement"}[t],
               "marche": questions.get(mid, mid), "issue": d.get("outcome") or token_outcome.get(tok, ""),
               "strategie": d.get("strategy_id", ""), "limite": extra.get("order_type") == "limite"}
        if t == "SETTLE":
            counts["reglements"] += 1
            row.update(prix=float(d.get("payout_per_share", 0)), parts=None, montant=None, resolution=d.get("resolution", ""))
        else:
            counts["achats" if t == "BUY" else "ventes"] += 1
            price, shares, fee = float(d.get("price", 0)), float(d.get("shares", 0)), float(d.get("fee", 0))
            row.update(prix=round(price, 4), parts=round(shares, 2), frais=round(fee, 4),
                       montant=round(shares * price + (fee if t == "BUY" else -fee), 2))
            if extra.get("est_probability"):
                row["prevision"] = round(float(extra["est_probability"]), 3)
        rows.append(row)
    rows.reverse()
    posed = []
    for o in open_orders:
        try:
            prop = json.loads(o["proposal"])
        except (TypeError, ValueError):
            continue
        posed.append({"date": str(o.get("created_at", ""))[:16], "sens": "achat" if o.get("side") == "BUY" else "vente",
                      "marche": questions.get(prop.get("market_id", ""), prop.get("market_id", "")),
                      "issue": prop.get("outcome", ""), "prix": float(prop.get("max_price", 0)),
                      "parts": float(o.get("remaining", 0)), "strategie": prop.get("strategy_id", "")})
    return {"executions": rows[:limit], "poses": posed, "compte": counts}


def _fr(x: float, d: int = 1) -> str:
    return f"{x:,.{d}f}".replace(",", "\u202f").replace(".", ",")


def strategy_info(root: Path) -> dict[str, dict]:
    """config/strategies.yaml → {stratégie: réglages lisibles} (phrases pour Etienne)."""
    import yaml
    try:
        raw = yaml.safe_load((Path(root) / "config" / "strategies.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    out = {}
    for sid, spec in raw.items():
        if not isinstance(spec, dict) or not spec.get("enabled"):
            continue
        params = {k: (v.get("value") if isinstance(v, dict) else v) for k, v in (spec.get("params") or {}).items()}
        f = spec.get("filtres") or {}
        reglages = []
        if "prix_min" in f or "prix_max" in f:
            reglages.append(f"prix du vendeur entre {_fr(float(f.get('prix_min', 0)), 3)} et {_fr(float(f.get('prix_max', 1)), 3)}")
        if f.get("familles"):
            reglages.append("marchés : " + ", ".join(str(x) for x in f["familles"]))
        if spec.get("limit_ticks") is not None:
            reglages.append(f"ordre posé {spec['limit_ticks']} cran au-dessus du meilleur acheteur")
        if params.get("bias") is not None:
            reglages.append(f"estimation = prix + {_fr(float(params['bias']), 2)}")
        if params.get("kelly_fraction") is not None:
            reglages.append(f"mise : {_fr(float(params['kelly_fraction']), 2)} × Kelly")
        reglages.append("ordres annulés au coup d'envoi" if spec.get("annuler_au_coup_d_envoi")
                        else "ordres posés gardés pendant le match")
        out[sid] = {"nom": sid, "version": str(spec.get("version", "")), "explication": str(spec.get("explication", "")),
                    "reglages": reglages}
    return out


def situation(capital: dict, strategies: list[dict], conf: dict) -> dict:
    """La situation en UNE ligne (en tête du cœur) et une précision plus petite dessous."""
    if capital.get("faillite"):
        return {"phrase": "Faillite : le trading est arrêté pour de bon.", "detail": capital.get("raison_faillite", "")}
    names = " et ".join(s["nom"] for s in strategies) or "aucune stratégie (config/strategies.yaml)"
    if not conf.get("operations"):
        detail = "Aucun pari terminé pour l'instant : la confiance se mesurera sur les premiers paris réglés."
    else:
        past = (f", dont {conf['anterieures']} dans son ancien fantôme" if conf.get("anterieures") else "")
        detail = (f"{conf['operations']} paris terminés{past} : {conf['gagnees']} gagnés pour {_fr(conf['attendues'])} "
                  f"attendus d'après les prix payés ; confiance {conf['confiance_pct']} % ({conf['verdict']}).")
    phrase = f"Le capital principal est tradé par {names}" + (" aux conditions de son ancien fantôme."
                                                               if conf.get("anterieures") else ".")
    return {"phrase": phrase, "detail": detail}


def collect(main: Ledger, db, settings: dict, root: Path, now: datetime | None = None,
            history: list[dict] | None = None) -> dict:
    """`history` : antécédents de la stratégie (antecedents.load) : leurs paris terminés comptent dans la confiance."""
    now = now or datetime.now(timezone.utc)
    history = history or []
    state = main.state
    questions = {r[0]: r[1] for r in db.conn.execute(
        "SELECT market_id, question FROM market_snapshots GROUP BY market_id")}
    marks = _marks(db, state.positions)
    entries = read_entries(main)
    trades = closed_trades(entries)

    # --- capital principal et cliquet
    curve = equity_curve(entries)
    capital = {k: str(v) for k, v in state.summary(marks).items()}
    capital.update({"capital_realise": str(state.realized_capital().quantize(Decimal("0.01"))),
                    "palier_suivant": str(state.next_target),
                    "drawdown_max": round(max_drawdown(curve), 4),
                    "faillite": state.bankrupt, "raison_faillite": state.bankrupt_reason})

    # --- la stratégie et ses indices de confiance
    info = strategy_info(root)
    past = closed_trades(history)
    conf = dict(confiance.summary(antecedents.trades(history, entries)), anterieures=len(past),
                antecedents_fin=history[-1]["ts"][:16] if history else None)
    strategies = list(info.values())

    # --- positions ouvertes, avec prévision et incertitude
    buys = {e["data"]["token_id"]: e["data"].get("extra") or {} for e in entries if e["type"] == "BUY"}
    positions = []
    for token, pos in state.positions.items():
        extra = buys.get(token, {})
        mark = marks.get(token)
        positions.append({
            "marche": questions.get(pos.market_id, pos.market_id),
            "issue": pos.outcome, "parts": float(pos.shares), "cout": round(float(pos.cost_basis), 2),
            "prix_moyen": round(float(pos.avg_cost), 4), "marque": float(mark) if mark is not None else None,
            "latent": round(float(pos.shares * mark - pos.cost_basis), 2) if mark is not None else None,
            "proba": extra.get("est_probability"), "proba_bas": extra.get("prob_low"),
            "proba_haut": extra.get("prob_high"), "strategie": pos.strategy_id,
            "ouverte": pos.opened_at[:16]})

    # --- opérations clôturées, la plus récente d'abord
    closed = [{"marche": questions.get(t["market_id"], t["market_id"]), "issue": t["outcome"], "strategie": t["strategy"],
               "resultat": round(float(t["pnl"]), 2), "resolution": t.get("resolution", ""), "date": t["closed_at"][:16]}
              for t in trades]
    closed.sort(key=lambda t: t["date"], reverse=True)

    # --- décisions récentes, marchés surveillés, cycles
    decisions = [{"date": r[0][:16], "strategie": r[1], "marche": questions.get(r[2], r[2]), "sens": r[3],
                  "acceptee": bool(r[4]), "raisons": json.loads(r[5] or "[]"), "execution": r[6] or ""}
                 for r in db.conn.execute("SELECT decided_at, strategy_id, market_id, side, accepted, reasons, "
                                          "fill_status FROM decisions ORDER BY decided_at DESC LIMIT 25")]
    last_cycle = db.conn.execute("SELECT cycle_id FROM market_snapshots ORDER BY collected_at DESC LIMIT 1").fetchone()
    watched = []
    if last_cycle:
        for r in db.conn.execute("SELECT question, best_bid, best_ask, end_date, fee_type FROM market_snapshots "
                                 "WHERE cycle_id=? AND eligible=1 ORDER BY CAST(volume_24h AS REAL) DESC LIMIT 15",
                                 (last_cycle[0],)):
            watched.append({"marche": r[0], "bid": r[1], "ask": r[2], "echeance": (r[3] or "")[:16],
                            "categorie": (r[4] or "other").split("_")[0]})
    since = (now - timedelta(hours=24)).isoformat()
    cycles = dict(db.conn.execute("SELECT status, COUNT(*) FROM cycles WHERE started_at >= ? GROUP BY status",
                                  (since,)).fetchall())
    last = db.conn.execute("SELECT started_at, status FROM cycles WHERE finished_at IS NOT NULL "
                           "ORDER BY started_at DESC LIMIT 1").fetchone()
    errors = [{"date": r[0][:16], "erreur": r[1]} for r in db.conn.execute(
        "SELECT started_at, error FROM cycles WHERE error IS NOT NULL AND started_at >= ? "
        "ORDER BY started_at DESC LIMIT 8", (since,))]
    # Pouls : les derniers cycles, du plus ancien au plus récent (durée en secondes, exécutions).
    pulse = []
    for started, finished, status, fills, error in reversed(db.conn.execute(
            "SELECT started_at, finished_at, status, fills, error FROM cycles WHERE finished_at IS NOT NULL "
            "ORDER BY started_at DESC LIMIT 96").fetchall()):
        try:
            seconds = round((datetime.fromisoformat(finished) - datetime.fromisoformat(started)).total_seconds(), 1)
        except (TypeError, ValueError):
            seconds = None
        pulse.append({"date": started[:16], "statut": status, "duree": seconds, "executions": fills or 0,
                      "erreur": bool(error)})
    counts = db.conn.execute("SELECT markets_scanned, watchlist_size, proposals, accepted, fills FROM cycles "
                             "WHERE finished_at IS NOT NULL ORDER BY started_at DESC LIMIT 1").fetchone()
    try:
        main_open = db.open_orders("principal")
    except Exception:  # noqa: BLE001 - base sans table des ordres posés
        main_open = []
    system = {"marches_scannes": counts[0] if counts else 0, "surveilles": counts[1] if counts else 0,
              "propositions": counts[2] if counts else 0, "acceptees": counts[3] if counts else 0,
              "executions": counts[4] if counts else 0, "ordres_poses": len(main_open)}

    # Audit indépendant des exécutions (écrit par le job protégé « audit » dans la branche data).
    audit = None
    audit_file = root / "_data" / "audit" / "etat.json"
    if audit_file.exists():
        try:
            raw_audit = json.loads(audit_file.read_text(encoding="utf-8"))
            audit = {"verifiees": raw_audit.get("verifiees", 0), "non_verifiables": raw_audit.get("non_verifiables", 0),
                     "reglements_verifies": raw_audit.get("reglements_verifies", 0),
                     "anomalies": len(raw_audit.get("anomalies", [])), "derniere": raw_audit.get("derniere_execution"),
                     "dernieres_anomalies": raw_audit.get("anomalies", [])[-5:]}
        except ValueError:
            audit = None
    abstentions = _kv_json(db, "abstentions") or {}
    data = {
        "audit": audit,
        "genere_le": now.isoformat(timespec="seconds"),
        "capital": capital,
        "courbe": _downsample(curve, 600),  # 21/10/2026 : assez de points pour la vue « 24 h »
        "paliers": state.milestones,
        "capital_initial": float(state.initial_capital),
        "strategie": {"strategies": strategies, "confiance": conf, "sante": sante.load(db),
                      "abstentions": abstentions.get("strategies") or {}},
        "positions": positions,
        "cloturees": closed[:20],
        "decisions": decisions,
        "surveilles": watched,
        "sante": {"dernier_battement": state.last_heartbeat_at.isoformat(timespec="seconds")
                  if state.last_heartbeat_at else None,
                  "dernier_cycle": {"date": last[0][:16], "statut": last[1]} if last else None,
                  "cycles_24h": cycles, "erreurs": errors, "pouls": pulse,
                  "minutes_par_cycle": int((settings.get("cycle") or {}).get("bucket_minutes", 5))},
        "systeme": system,
        "etapes_cycle": _kv_json(db, "durees_cycle"),
        "mise_en_page": layout(root),
        "fiche": fiche(root, settings),
        "situation": situation(capital, strategies, conf),
    }
    data["aujourdhui"] = today.build(data, entries, questions, now)
    data["ordres_principal"] = principal_orders(entries, questions, main_open)
    data["ordres_principal"]["clotures"] = closed[:20]
    # Ordres posés : sélection adverse (markout), servis contre manqués.
    data["ordres_poses"] = {"markout": markout.report(db, {"principal": entries}, now)}
    return data


def to_json(data: dict) -> str:
    """JSON sûr à embarquer dans <script> : aucune séquence « </ » ne peut fermer la balise."""
    return json.dumps(data, ensure_ascii=False, default=str).replace("</", "<\\/")


def render(data: dict, full_document: bool = True) -> str:
    """Page complète (GitHub Pages) ou contenu seul (aperçu Artifact, enveloppé à la publication)."""
    template = (Path(__file__).parent / "dashboard_template.html").read_text(encoding="utf-8")
    body = template.replace("__DATA__", to_json(data))
    if not full_document:
        return body
    return ("<!doctype html>\n<html lang=\"fr\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1, viewport-fit=cover\">\n"
            "</head>\n<body>\n" + body + "\n</body>\n</html>\n")


def write_site(data: dict, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "index.html").write_text(render(data, full_document=True), encoding="utf-8")
    (out_dir / "data.json").write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return out_dir / "index.html"


def open_ledger(settings: dict, root: Path, key: bytes) -> Ledger:
    """Journal du capital principal, vérifié (signatures et chaîne)."""
    paths = settings["paths"]
    return Ledger.open(resolve(root, paths["ledger"]), key, resolve(root, paths["bankruptcy_lock"]))


def is_due(out_dir: Path, now: datetime, every_minutes: float) -> bool:
    """Vrai si la page n'existe pas encore ou date de plus de `every_minutes`."""
    data_file = out_dir / "data.json"
    if not data_file.exists():
        return True
    try:
        generated = datetime.fromisoformat(json.loads(data_file.read_text(encoding="utf-8"))["genere_le"])
    except (ValueError, KeyError, TypeError):
        return True
    return (now - generated).total_seconds() >= every_minutes * 60 - 30  # marge : un cycle toutes les quelques minutes


def build(settings: dict, root: Path, key: bytes, db, now: datetime | None = None) -> Path:
    past = settings["paths"].get("antecedents")
    data = collect(open_ledger(settings, root, key), db, settings, root, now,
                   antecedents.load(resolve(root, past) if past else None))
    return write_site(data, resolve(root, settings["dashboard"]["output_dir"]))
