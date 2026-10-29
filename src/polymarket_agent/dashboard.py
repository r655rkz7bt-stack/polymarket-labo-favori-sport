"""Tableau de bord (cahier des charges §4.K) : une page HTML statique, autonome, sans dépendance.

Publié sur GitHub Pages par le cycle (étape 9) et régénéré toutes les 30 minutes.
Toutes les données viennent des journaux vérifiés et de la base SQLite.

Sécurité : les textes venus de l'extérieur (questions de marchés, raisons, noms de sources)
sont insérés dans la page avec `textContent`, jamais comme du HTML. Le JSON embarqué
échappe `</` pour qu'aucun texte ne puisse fermer la balise <script>.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from polymarket_agent import arena_history, liquidity_rewards, strategy_health, today
from polymarket_agent.arena import confidence_percent, days_since_record, ranking, standing
from polymarket_agent.config import resolve
from polymarket_agent.learning_engine import analyze, categories_from_db
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


BLOCKS = ("resume", "aujourdhui", "organisme", "principal", "capital", "arene", "entonnoir", "positions", "ordres-poses",
          "journal", "decisions-recentes", "systeme")
# Règle n°1 : capital, solde disponible, faillite. Depuis le 17/10/2026, le cœur (« organisme ») porte
# le capital total, le solde disponible et l'état de faillite en tête de page : lui non plus ne se cache pas.
ALWAYS_VISIBLE = ("organisme", "resume", "capital")


def layout(root: Path) -> dict:
    """Mise en page réglée par le chercheur (config/dashboard.yaml) : ordre des blocs, blocs cachés,
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
    """Réglages de la fiche d'une stratégie (demande d'Etienne, 21/10/2026) : la page charge le
    journal PUBLIC de la stratégie touchée (branche data, `journaux_url` de config/dashboard.yaml)
    et calcule ses courbes à ce moment-là, dans le navigateur : rien n'est précalculé ici. Seule une
    adresse raw.githubusercontent.com est acceptée ; sinon la page la déduit de son adresse GitHub
    Pages. Les règles de mise (`mise_avantage_mesure`, `garde_fou_jour`) servent aux courbes
    « mise » et « confiance par jour »."""
    import yaml
    try:
        raw = yaml.safe_load((Path(root) / "config" / "dashboard.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        raw = {}
    url = str((raw if isinstance(raw, dict) else {}).get("journaux_url") or "")
    cfg = arena_cfg(settings or {}, root)
    return {"journaux": url if url.startswith(RAW_PREFIX) and url.endswith("/") else "",
            "mise": cfg.get("mise_avantage_mesure") or None, "garde_fou": cfg.get("garde_fou_jour") or None}


def principal_orders(entries: list[dict], questions: dict[str, str], open_orders: list[dict],
                     limit: int = 60) -> dict:
    """Ordres du capital PRINCIPAL (le vrai portefeuille, règle n°1), à part de ceux de l'arène
    (demande d'Etienne, 17/10/2026) : exécutions du journal signé, la plus récente d'abord, et
    ordres posés encore ouverts."""
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


def _hm(iso: str) -> str:
    """« 17/10 15:27 » depuis un horodatage ISO."""
    return f"{iso[8:10]}/{iso[5:7]} {iso[11:16]}" if len(iso) >= 16 else iso


def arena_cfg(settings: dict, root: Path) -> dict:
    """Règles de l'arène (config/promotion.yaml), déjà chargées par le cycle ou lues ici."""
    cfg = settings.get("arena")
    if not isinstance(cfg, dict):
        import yaml
        try:
            cfg = yaml.safe_load((Path(root) / "config" / "promotion.yaml").read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):
            cfg = {}
    return cfg


def arena_rules(settings: dict, root: Path) -> dict:
    """Seuils d'entrée de l'arène, pour expliquer la situation."""
    cfg = arena_cfg(settings, root)
    return {"operations": int(cfg.get("min_closed_trades", 20)), "croissance": float(cfg.get("min_log_growth", 0.02)),
            "heures": float(cfg.get("min_age_hours", 24)),
            "confiance": float(cfg["min_t_stat"]) if cfg.get("min_t_stat") is not None else None}


def promotions(entries: list[dict], limit: int = 6) -> list[dict]:
    """Derniers changements de champion du journal principal (le plus récent en premier)."""
    out = [{"date": e["ts"][:16], "champion": e["data"].get("champion", ""),
            "ancien": (e["data"].get("metrics") or {}).get("from", ""), "raison": e["data"].get("reason", "")}
           for e in entries if e.get("type") == "PROMOTION"]
    return out[::-1][:limit]


def situation(capital: dict, arena: list[dict], history: list[dict], rules: dict, initial: float,
              parts: dict[str, float] | None = None) -> dict:
    """La situation en UNE ligne (demande d'Etienne, 17/10/2026) : qui pilote le capital principal, et
    sinon POURQUOI personne. {"phrase": la ligne en tête, "detail": une précision plus petite dessous}."""
    import math
    if capital.get("faillite"):
        return {"phrase": "Faillite : le trading est arrêté pour de bon.", "detail": capital.get("raison_faillite", "")}
    by_name = {a["nom"]: a for a in arena}
    champ = capital.get("champion") or ""
    pct = lambda a: (a["capital"] / initial - 1) * 100 if initial else 0.0  # noqa: E731
    signed = lambda x: f"{'+' if x >= 0 else '−'}{_fr(abs(x))} %"  # noqa: E731
    name = lambda n: n.rsplit("_v", 1)[0]  # noqa: E731
    parts = {n: w for n, w in (parts or {}).items() if w > 0}
    if len(parts) > 1:  # plusieurs championnes (18/10/2026)
        listed = sorted(parts.items(), key=lambda kv: -kv[1])
        names = [f"{name(n)} ({_fr(w * 100, 0)} %)" for n, w in listed]
        total = sum(parts.values())
        return {"phrase": f"Le capital principal suit {len(parts)} championnes : "
                          + ", ".join(names[:-1]) + f" et {names[-1]}.",
                "detail": (f"{_fr(total * 100, 0)} % du capital engagé au plus ; le reste attend : chaque stratégie mise "
                           "à proportion de son avantage mesuré contre les prix (période d'essai, garde-fou par jour)."
                           if total <= 1 else
                           "Tout le capital peut être engagé : une gagnante prouvée mise davantage (renfort, 27/10/2026) ; "
                           "une petite réserve reste toujours disponible.")}
    if champ:
        since = next((h["date"] for h in history if h["champion"] == champ), "")
        a = by_name.get(champ)
        return {"phrase": f"Le capital principal suit {name(champ)}" + (f" depuis le {_hm(since)} UTC" if since else "") + ".",
                "detail": f"{name(champ)} : {signed(pct(a))} sur {a['operations']} opérations clôturées." if a else ""}
    # Pas de champion : la plus proche du seuil (croissance suffisante d'abord, puis le plus d'opérations).
    need = math.exp(rules["croissance"]) * initial
    live = [a for a in arena if not a.get("eliminee") and not a.get("retiree")]
    near = max(live, key=lambda a: (a["capital"] >= need, a["operations"] if a["capital"] >= need else a["capital"]),
               default=None)
    if near is None:
        tail = "Aucune stratégie en course."
    elif near["capital"] >= need and near["operations"] < rules["operations"]:
        tail = (f"La plus proche du titre : {name(near['nom'])} ({signed(pct(near))}), {near['operations']} opérations "
                f"clôturées sur les {rules['operations']} exigées.")
    elif near["capital"] >= need and rules.get("confiance") and near.get("confiance", 0) < rules["confiance"]:
        tail = (f"La plus proche du titre : {name(near['nom'])} ({signed(pct(near))}), mais ses résultats sont encore "
                f"trop irréguliers (confiance {confidence_percent(near.get('confiance', 0))} %, "
                f"il faut {confidence_percent(rules['confiance'])} %).")
    elif near["capital"] >= need:
        tail = f"{name(near['nom'])} remplit les conditions : promotion attendue au prochain cycle."
    else:
        tail = (f"Il faut +{_fr((math.exp(rules['croissance']) - 1) * 100, 0)} % et {rules['operations']} opérations ; "
                f"la meilleure, {name(near['nom'])}, est à {signed(pct(near))}.")
    last = history[0] if history else None
    if last and not last["champion"] and last["ancien"]:
        old = by_name.get(last["ancien"])
        why = (f"{name(last['ancien'])} est retombé sous ses {_fr(initial, 0)} de départ ({_fr(old['capital'], 2)})"
               if old and old["capital"] <= initial else f"{name(last['ancien'])} ne battait plus « ne rien faire »")
        return {"phrase": f"Plus de champion depuis le {_hm(last['date'])} UTC : {why}.",
                "detail": f"Le capital principal ne prend plus de nouveau pari. {tail}"}
    return {"phrase": "Pas encore de champion : le capital principal attend.", "detail": tail}


def strategy_info(root: Path) -> dict[str, dict]:
    """config/strategies.yaml → {stratégie: {explication, mere, morceau, active}} (phrases pour Etienne).
    `active` : activée et autorisée à proposer (une stratégie coupée garde son fantôme, sans trader)."""
    import yaml
    try:
        raw = yaml.safe_load((Path(root) / "config" / "strategies.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    out = {}
    for sid, spec in raw.items():
        if not isinstance(spec, dict):
            continue
        info = {k: str(spec[k]) for k in ("explication", "mere", "morceau") if spec.get(k)}
        cap = ((spec.get("params") or {}).get("max_proposals_per_cycle") or {})
        info["active"] = bool(spec.get("enabled", True)) and not (isinstance(cap, dict) and cap.get("value") == 0)
        out[sid] = info
    return out


def funnel(arena: list[dict], info: dict[str, dict], initial: float = 1000.0) -> list[dict]:
    """Entonnoir : chaque mère et ses filles (même stratégie sur un morceau des marchés).
    Comparaison ÉQUITABLE : une fille née après sa mère est comparée à ce que la mère a gagné
    DEPUIS la naissance de la fille (courbe de capital de la mère), pas depuis le début."""
    by_id = {a["nom"].rsplit("_v", 1)[0]: a for a in arena}

    def last(curve, default):
        return float(curve[-1][1]) if curve else default

    def value_at(curve, when, default):
        v = float(curve[0][1]) if curve else default
        for t, x in curve or []:
            if t > when:
                break
            v = float(x)
        return v

    out = []
    for mother in sorted({v["mere"] for v in info.values() if v.get("mere")}):
        m = by_id.get(mother)
        kids = [dict(by_id[sid], morceau=v.get("morceau", sid)) for sid, v in info.items()
                if v.get("mere") == mother and sid in by_id]
        if m is None or not kids:
            continue
        keep = ("nom", "capital", "operations", "positions", "croissance")
        m_curve = m.get("courbe") or []
        m_now = last(m_curve, float(m["capital"]))
        rows = []
        for c in kids:
            start = None
            if m_curve and c.get("jours") is not None:
                end = datetime.fromisoformat(m_curve[-1][0])
                start = (end - timedelta(days=float(c["jours"]))).isoformat()
            base = value_at(m_curve, start, initial) if start else initial
            mother_since = 100 * (m_now / base - 1) if base else 0.0
            child = 100 * (last(c.get("courbe") or [], float(c["capital"])) / initial - 1)
            rows.append(dict({k: c[k] for k in keep + ("morceau",)}, mere_depuis=round(mother_since, 2),
                             ecart=round(child - mother_since, 2)))
        out.append({"mere": {k: m[k] for k in keep}, "filles": sorted(rows, key=lambda c: -c["ecart"])})
    return out


def research_log(root: Path, limit: int = 3) -> list[dict]:
    """Derniers passages du chercheur (experiments/journal_recherche.md, écrit par la routine) :
    {"date", "quand", "fait", "details": [{"titre", "texte"}]}. Le texte est une donnée : la page
    l'insère comme du texte, jamais comme du HTML."""
    import re
    path = root / "experiments" / "journal_recherche.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return []
    clean = lambda s: re.sub(r"\s+", " ", s.replace("**", "").replace("`", "")).strip()
    out = []
    for block in re.split(r"^## ", text, flags=re.M)[1:]:
        lines = block.splitlines()
        title = lines[0].strip()
        if "chercheur" not in title.lower():
            continue
        m = re.match(r"(\d{4}-\d{2}-\d{2})\s*(?:\(([^)]*)\))?", title)
        items, current = [], None
        for line in lines[1:]:
            b = re.match(r"^- \*\*([^*]+)\*\*\s*:?\s*(.*)$", line)
            if b:
                current = {"titre": clean(b.group(1)), "texte": clean(b.group(2))}
                items.append(current)
            elif current is not None and line.startswith("  ") and line.strip():
                current["texte"] = clean(current["texte"] + " " + line)
        fait = next((i["texte"] for i in items if i["titre"].lower() == "fait"), "")
        out.append({"date": m.group(1) if m else title[:10],
                    "quand": (m.group(2) or "").replace("passage chercheur, ", "") if m else "",
                    "fait": fait[:600],
                    "details": [dict(i, texte=i["texte"][:600]) for i in items if i["titre"].lower() not in ("fait", "lu")]})
        if len(out) >= limit:
            break
    return out


def collect(main: Ledger, shadows: dict[str, Ledger], db, settings: dict, root: Path,
            now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    state = main.state
    questions = {r[0]: r[1] for r in db.conn.execute(
        "SELECT market_id, question FROM market_snapshots GROUP BY market_id")}
    try:  # les instantanés sont élagués : la question reste dans l'archive des règles (sources de résolution)
        for mid, q in db.conn.execute("SELECT market_id, question FROM market_rules"):
            questions.setdefault(mid, q)
    except Exception:  # noqa: BLE001 - base sans archive (avant le 15/10/2026)
        pass
    learning = analyze(dict(shadows, principal=main), categories_from_db(db))
    marks = _marks(db, state.positions)

    # --- capital principal et cliquet
    curve = equity_curve(read_entries(main))
    capital = {k: str(v) for k, v in state.summary(marks).items()}
    capital.update({"capital_realise": str(state.realized_capital().quantize(Decimal("0.01"))),
                    "palier_suivant": str(state.next_target), "champion": state.champion or "",
                    "drawdown_max": round(max_drawdown(curve), 4),
                    "jours_depuis_palier": round(days_since_record(main, now), 2),
                    "faillite": state.bankrupt, "raison_faillite": state.bankrupt_reason})
    try:  # récompense de détention ESTIMÉE du capital principal (hors capital, holding_rewards.py)
        from polymarket_agent import holding_rewards
        since = state.epoch_started_at.isoformat(timespec="seconds") if state.epoch_started_at else ""
        capital["detention_estimee"] = round(holding_rewards.totals(db, since).get("principal", 0.0), 4)
    except Exception:  # noqa: BLE001 - base sans table de récompenses
        capital["detention_estimee"] = 0.0

    # --- récompenses estimées comptées pour le classement (option A, 17/10/2026), comme dans main.py
    bonus = {}
    rcfg = settings.get("recompenses") or {}
    if rcfg.get("classement_arene"):
        from polymarket_agent import liquidity_rewards
        from polymarket_agent import holding_rewards
        starts = {n: l.state.epoch_started_at for n, l in shadows.items()}
        try:
            bonus = liquidity_rewards.ranking_bonus(db, starts, float(rcfg.get("prudence", 0.5)), now)
            for n, amount in holding_rewards.ranking_bonus(db, starts, float(rcfg.get("prudence", 0.5))).items():
                bonus[n] = round(bonus.get(n, 0.0) + amount, 4)  # récompense de détention (19/10/2026)
        except Exception:  # noqa: BLE001 - base sans table de récompenses
            bonus = {}

    # --- arène (avec la phrase simple de chaque stratégie, config/strategies.yaml → explication)
    info = strategy_info(root)
    arena = []
    acfg = arena_cfg(settings, root)
    # Répartition du capital principal (main._steady_shares) : toutes les championnes et leur part.
    try:
        parts = {n: float(w) for n, w in (json.loads(db.get_value("repartition_principal") or "{}").get("parts") or {}).items()}
    except (ValueError, TypeError, AttributeError):
        parts = {}
    # Qualifiées écartées parce qu'elles font les mêmes paris qu'une autre (arena.allocation, doublons).
    try:
        twins = json.loads(db.get_value("doublons_principal") or "{}")
        twins = twins if isinstance(twins, dict) else {}
    except (ValueError, TypeError, AttributeError):
        twins = {}
    try:
        days = liquidity_rewards.payable_day_counts(db, {n: l.state.epoch_started_at for n, l in shadows.items()}, now)
    except Exception:  # noqa: BLE001 - base sans table de récompenses
        days = {}
    for s in ranking([standing(n, l, state.eliminated, bonus.get(n, 0.0), days.get(n, 0), acfg) for n, l in shadows.items()]):
        verdicts = learning.get(s.name, {}).get("strategies", {})
        verdict = next(iter(verdicts.values()), {}).get("verdict", "aucune opération clôturée")
        arena.append({"nom": s.name, "explication": info.get(s.name.rsplit("_v", 1)[0], {}).get("explication", ""),
                      "operations": s.closed_trades, "capital": round(s.realized_capital, 2),
                      "recompenses_classement": s.rewards, "confiance": s.t_stat,
                      "confiance_pct": confidence_percent(s.t_stat), "mode": s.mode,
                      # sans config/strategies.yaml (tests, copie partielle), personne n'est « retirée »
                      "retiree": bool(info) and not s.eliminated
                      and not info.get(s.name.rsplit("_v", 1)[0], {}).get("active", False),
                      "croissance": round(s.log_growth, 4) if s.log_growth != float("-inf") else None,
                      "jours": round(s.days(now), 1), "eliminee": s.eliminated,
                      "champion": s.name == state.champion or s.name in parts, "verdict": verdict,
                      "part_principal": round(parts.get(s.name, 0.0), 4),
                      "doublon": twins.get(s.name) if s.name not in parts else None,
                      "courbe": _downsample(equity_curve(read_entries(shadows[s.name])), 60),
                      "positions": len(shadows[s.name].state.positions),
                      "sante": strategy_health.load(db, s.name)})
    arena.sort(key=lambda a: a["retiree"])  # ménage : les stratégies qui ne tradent plus passent en bas

    # --- positions ouvertes (tous portefeuilles), avec prévision et incertitude
    buys = {}
    for name, ledger in dict(shadows, principal=main).items():
        for e in read_entries(ledger):
            if e["type"] == "BUY":
                buys[(name, e["data"]["token_id"])] = e["data"].get("extra") or {}
    positions = []
    for name, ledger in dict(shadows, principal=main).items():
        pm = _marks(db, ledger.state.positions)
        for token, pos in ledger.state.positions.items():
            extra = buys.get((name, token), {})
            mark = pm.get(token)
            positions.append({
                "portefeuille": name, "marche": questions.get(pos.market_id, pos.market_id),
                "issue": pos.outcome, "parts": float(pos.shares), "cout": round(float(pos.cost_basis), 2),
                "prix_moyen": round(float(pos.avg_cost), 4), "marque": float(mark) if mark is not None else None,
                "latent": round(float(pos.shares * mark - pos.cost_basis), 2) if mark is not None else None,
                "proba": extra.get("est_probability"), "proba_bas": extra.get("prob_low"),
                "proba_haut": extra.get("prob_high"), "strategie": pos.strategy_id,
                "ouverte": pos.opened_at[:16]})

    # --- opérations clôturées récentes
    closed = []
    for name, ledger in dict(shadows, principal=main).items():
        for t in closed_trades(read_entries(ledger)):
            closed.append({"portefeuille": name, "marche": questions.get(t["market_id"], t["market_id"]),
                           "issue": t["outcome"], "strategie": t["strategy"],
                           "resultat": round(float(t["pnl"]), 2), "resolution": t.get("resolution", ""),
                           "date": t["closed_at"][:16]})
    closed.sort(key=lambda t: t["date"], reverse=True)

    # --- décisions récentes, marchés surveillés, cycles, sources
    decisions = [{"date": r[0][:16], "strategie": r[1], "marche": questions.get(r[2], r[2]), "sens": r[3],
                  "acceptee": bool(r[4]), "raisons": json.loads(r[5] or "[]"), "execution": r[6] or ""}
                 for r in db.conn.execute("SELECT decided_at, strategy_id, market_id, side, accepted, reasons, "
                                          "fill_status FROM decisions ORDER BY decided_at DESC LIMIT 25")]
    # Refus du principal par stratégie et par raison, 48 dernières heures (26/10/2026 : « taille inférieure au minimum du marché (5) » cachait
    # `moins_de_cartes` copiée 0 fois sur 70 ; les 25 décisions récentes ne montraient qu'un cas). Les ordres du principal portent le préfixe « P ».
    refus_principal: dict[str, dict[str, int]] = {}
    try:
        since = (now - timedelta(hours=48)).isoformat()
        for strat, reasons in db.conn.execute("SELECT strategy_id, reasons FROM decisions WHERE accepted=0 AND op_id LIKE 'P%' AND decided_at >= ?",
                                              (since,)):
            for reason in json.loads(reasons or "[]") or ["(sans raison notée)"]:
                bucket = refus_principal.setdefault(strat, {})
                bucket[reason] = bucket.get(reason, 0) + 1
    except Exception:  # noqa: BLE001 - base sans table de décisions : le tableau de bord reste affiché
        refus_principal = {}
    # Écarts silencieux de la copie (budget de la part, plafond de famille, camp opposé : 27/10/2026), gardés 48 h par le cycle.
    try:
        for _when, strat, reason, n in _kv_json(db, "ecartes_principal") or []:
            if _when >= (now - timedelta(hours=48)).isoformat():
                bucket = refus_principal.setdefault(strat, {})
                bucket[reason] = bucket.get(reason, 0) + int(n)
    except Exception:  # noqa: BLE001
        pass
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
    # État de chaque source : le plus récent résultat réel (« pas encore (fréquence) » ne dit rien).
    source_state: dict[str, str] = {}
    for (summary,) in db.conn.execute("SELECT summary FROM cycles WHERE finished_at IS NOT NULL "
                                      "ORDER BY started_at DESC LIMIT 24"):
        for name, status in (json.loads(summary or "{}").get("sources") or {}).items():
            if name not in source_state or source_state[name].startswith("pas encore"):
                source_state[name] = status
    source_runs = dict(db.conn.execute("SELECT source, last_run FROM source_runs").fetchall())
    errors = [{"date": r[0][:16], "erreur": r[1]} for r in db.conn.execute(
        "SELECT started_at, error FROM cycles WHERE error IS NOT NULL AND started_at >= ? "
        "ORDER BY started_at DESC LIMIT 8", (since,))]
    obs_by_source = dict(db.conn.execute("SELECT source, COUNT(*) FROM observations WHERE collected_at >= ? "
                                         "GROUP BY source", (since,)).fetchall())
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
        open_orders = db.conn.execute("SELECT COUNT(*) FROM pending_orders WHERE status='OUVERT'").fetchone()[0]
    except Exception:  # noqa: BLE001 - base d'avant les ordres posés
        open_orders = 0
    system = {"marches_scannes": counts[0] if counts else 0, "surveilles": counts[1] if counts else 0,
              "propositions": counts[2] if counts else 0, "acceptees": counts[3] if counts else 0,
              "executions": counts[4] if counts else 0, "ordres_poses": open_orders,
              "strategies_actives": sum(1 for s in arena if not s["eliminee"] and not s["retiree"]),
              "positions_tous_portefeuilles": len(positions)}

    def listing(folder: str) -> list[dict]:
        path = root / folder
        files = sorted((p for p in path.glob("*.md") if p.name != "README.md"), key=lambda p: p.name, reverse=True)
        return [{"nom": p.name, "date": datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%d")}
                for p in files[:12]] if path.exists() else []

    tests = None
    tests_file = root / "state" / "tests_status.json"
    if tests_file.exists():
        try:
            tests = json.loads(tests_file.read_text(encoding="utf-8"))
        except ValueError:
            tests = None
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
    llm = settings.get("llm", {})
    # Historique du classement : une photo par heure (lot C).
    arena_history.record(db, arena, now, float(settings.get("dashboard", {}).get("arena_history_minutes", 60)))
    data = {
        "audit": audit,
        "genere_le": now.isoformat(timespec="seconds"),
        "capital": capital,
        "courbe": _downsample(curve, 600),  # 21/10/2026 : assez de points pour la vue « 24 h »
        "paliers": state.milestones,
        "capital_initial": float(state.initial_capital),
        "arene": arena,
        "positions": positions,
        "cloturees": closed[:20],
        "decisions": decisions,
        "refus_principal": refus_principal,
        "surveilles": watched,
        "sante": {"dernier_battement": state.last_heartbeat_at.isoformat(timespec="seconds")
                  if state.last_heartbeat_at else None,
                  "dernier_cycle": {"date": last[0][:16], "statut": last[1]} if last else None,
                  "cycles_24h": cycles, "erreurs": errors, "pouls": pulse},
        "systeme": system,
        # 21/10/2026 : durée de chaque étape du dernier cycle, et pourquoi chaque stratégie s'abstient
        # (marchés vus, propositions, raisons les plus fréquentes), pour l'enquête sur les muettes.
        "etapes_cycle": _kv_json(db, "durees_cycle"),
        "abstentions": _kv_json(db, "abstentions"),
        "sources": {"etat": source_state, "dernier_passage": {k: v[:16] for k, v in source_runs.items()},
                    "observations_24h": obs_by_source},
        "tests": tests,
        "couts": {"services_payants_eur": 0, "appels_ia_aujourdhui": db.llm_calls(now.date().isoformat()),
                  "plafond_ia": llm.get("max_calls_per_day", 0)},
        "historique_arene": arena_history.series(db, now),
        "mise_en_page": layout(root),
        "entonnoir": funnel(arena, info),
        "fiche": fiche(root, settings),
    }
    entries = {name: read_entries(ledger) for name, ledger in dict(shadows, principal=main).items()}
    data["aujourdhui"] = today.build(data, entries, questions, db, now)
    try:
        main_open = db.open_orders("principal")
    except Exception:  # noqa: BLE001 - base sans table des ordres posés
        main_open = []
    data["ordres_principal"] = principal_orders(entries["principal"], questions, main_open)
    data["ordres_principal"]["clotures"] = [t for t in closed if t["portefeuille"] == "principal"][:20]
    # La situation en une phrase (en tête du cœur) et les derniers changements de champion.
    data["promotions"] = promotions(entries["principal"])
    data["situation"] = situation(capital, arena, data["promotions"], arena_rules(settings, root),
                                  float(state.initial_capital), parts)
    # Ordres posés : récompenses de liquidité ESTIMÉES (hors capital) et sélection adverse (markout).
    from polymarket_agent import liquidity_rewards, markout
    since = (now - timedelta(days=7)).isoformat(timespec="seconds")
    data["ordres_poses"] = {"recompenses": liquidity_rewards.summary(db, since),
                            "markout": markout.report(db, entries, now)}
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


def open_ledgers(settings: dict, root: Path, key: bytes) -> tuple[Ledger, dict[str, Ledger]]:
    """Journal principal + journaux fantômes, tous vérifiés (signatures et chaîne)."""
    paths = settings["paths"]
    main = Ledger.open(resolve(root, paths["ledger"]), key, resolve(root, paths["bankruptcy_lock"]))
    shadow_dir = resolve(root, paths["shadow_dir"])
    shadows = {p.stem: Ledger.open(p, key, p.with_suffix(".BANKRUPT.lock"))
               for p in sorted(shadow_dir.glob("*.jsonl"))} if shadow_dir.exists() else {}
    return main, shadows


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
    main, shadows = open_ledgers(settings, root, key)
    data = collect(main, shadows, db, settings, root, now)
    return write_site(data, resolve(root, settings["dashboard"]["output_dir"]))
