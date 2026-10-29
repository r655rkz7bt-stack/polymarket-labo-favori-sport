"""Tableau de bord : contenu, sécurité de l'affichage, fréquence de génération."""

from __future__ import annotations

import json
import re
from datetime import timedelta
from decimal import Decimal

import yaml

from conftest import KEY, NOW, ROOT, buy, make_book
from polymarket_agent import dashboard
from polymarket_agent.ledger import Ledger
from polymarket_agent.persistence import Database
from polymarket_agent.reporting import read_entries

EVIL = "</script><script>alert(1)</script><img src=x onerror=alert(2)>"
SETTINGS = {"paths": {"ledger": "ledger/ledger.jsonl", "bankruptcy_lock": "ledger/BANKRUPT.lock"},
            "cycle": {"bucket_minutes": 2}, "dashboard": {"output_dir": "site", "every_minutes": 30}}
TEMPLATE = ROOT / "src" / "polymarket_agent" / "dashboard_template.html"


def _setup(tmp_path):
    main = Ledger.create(tmp_path / "ledger" / "ledger.jsonl", KEY, tmp_path / "ledger" / "BANKRUPT.lock",
                         "1000", now=NOW)
    buy(main, "b1", token="tA", shares="10", price="0.5")
    buy(main, "b2", token="tB", shares="100", price="0.8", event="ev2")
    main.record_settlement(op_id="s2", token_id="tB", payout_per_share="1", resolution="Yes", now=NOW)
    db = Database(tmp_path / "db.sqlite")
    db.conn.execute("INSERT INTO market_snapshots (cycle_id, collected_at, market_id, question, eligible) "
                    "VALUES ('c1', ?, 'm-tA', ?, 1)", (NOW.isoformat(), EVIL))
    db.save_books("c1", [make_book("tA", bids=(("0.6", "100"),), asks=(("0.61", "100"),))])
    for i in range(2):
        db.start_cycle(f"c{i}", NOW + timedelta(minutes=5 * i))
        db.finish_cycle(f"c{i}", NOW + timedelta(minutes=5 * i, seconds=30), "OK", summary={})
    return main, db


def test_collect_reads_the_ledger_and_db(tmp_path):
    main, db = _setup(tmp_path)
    data = dashboard.collect(main, db, SETTINGS, ROOT, now=NOW + timedelta(minutes=10))
    assert data["capital"]["palier_suivant"] == "1500.0"
    assert data["capital"]["solde_disponible"] == "1015.0000"  # 1 000 − 5 − 80 + 100
    assert data["capital"]["gains_latents"] == "1.0000"  # 10 parts marquées au bid 0,6
    assert data["positions"][0]["marche"] == EVIL and data["positions"][0]["latent"] == 1.0
    assert data["cloturees"][0]["resultat"] == 20.0
    assert data["surveilles"][0]["marche"] == EVIL
    assert data["strategie"]["confiance"]["operations"] == 1 and data["strategie"]["confiance"]["gagnees"] == 1
    assert [s["nom"] for s in data["strategie"]["strategies"]] == ["nf_sport_patiente_prix_850_875"]
    assert "arene" not in data and "entonnoir" not in data  # plus d'arène depuis le 29/10/2026
    db.close()


def test_strategy_settings_and_situation_are_written_for_etienne(tmp_path):
    info = dashboard.strategy_info(ROOT)["nf_sport_patiente_prix_850_875"]
    assert "prix du vendeur entre 0,850 et 0,875" in info["reglages"]
    assert "mise : 1,00 × Kelly" in info["reglages"] and "ordres posés gardés pendant le match" in info["reglages"]
    s = dashboard.situation({}, [info], {"operations": 0})
    assert s["phrase"] == "Le capital principal est tradé par nf_sport_patiente_prix_850_875."
    assert "Aucun pari terminé" in s["detail"]
    conf = {"operations": 30, "gagnees": 28, "attendues": 25.6, "confiance_pct": 93, "verdict": "indéterminée"}
    assert "28 gagnés pour 25,6 attendus" in dashboard.situation({}, [info], conf)["detail"]
    assert dashboard.situation({"faillite": True, "raison_faillite": "solde 0"}, [info], conf)["detail"] == "solde 0"


def test_confidence_figures_follow_the_old_arena_formulas():
    from polymarket_agent import confiance
    trades = [{"pnl": Decimal(p), "cost": Decimal(c), "shares_bought": Decimal("100"), "closed_at": f"2026-10-{d}T10:00",
               "event_id": f"e{i}", "market_id": f"m{i}"}
              for i, (p, c, d) in enumerate([("15", "85", 20), ("14", "86", 20), ("-85", "85", 21), ("13", "87", 22)])]
    s = confiance.summary(trades)
    assert s["operations"] == 4 and s["gagnees"] == 3 and s["attendues"] == 3.43 and s["en_plus"] == -0.43
    assert s["score_t"] == confiance.t_stat(confiance.trade_returns(trades))
    assert s["confiance_pct"] == confiance.confidence_percent(s["score_t"])
    assert s["verdict"].startswith("données insuffisantes")
    assert confiance.summary([])["operations"] == 0 and confiance.summary([])["confiance_pct"] == 50
    assert confiance.confidence_percent(0) == 50 and confiance.confidence_percent(2) == 98


def test_untrusted_text_cannot_close_the_script_tag(tmp_path):
    main, db = _setup(tmp_path)
    data = dashboard.collect(main, db, SETTINGS, ROOT, now=NOW)
    db.close()
    page = dashboard.render(data)
    blob = re.search(r'<script type="application/json" id="data">(.*?)</script>', page, re.S).group(1)
    assert "</script" not in blob
    assert json.loads(blob)["surveilles"][0]["marche"] == EVIL  # le texte reste intact une fois lu


def test_template_never_injects_html():
    """Les textes externes passent par textContent : aucune insertion de HTML brut."""
    template = TEMPLATE.read_text(encoding="utf-8")
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval("):
        assert forbidden not in template
    assert template.count("__DATA__") == 1
    assert "<script src" not in template  # aucune dépendance externe
    for gone in ("D.arene", "D.entonnoir", "historique_arene", "D.sources", "D.couts", "recompenses"):
        assert gone not in template


def test_full_document_and_fragment(tmp_path):
    main, db = _setup(tmp_path)
    data = dashboard.collect(main, db, SETTINGS, ROOT, now=NOW)
    db.close()
    full, fragment = dashboard.render(data), dashboard.render(data, full_document=False)
    assert full.startswith("<!doctype html>") and '<html lang="fr">' in full
    assert "<html" not in fragment and "<!doctype" not in fragment
    assert fragment.startswith("<title>")


def test_build_writes_site_and_is_due(tmp_path):
    main, db = _setup(tmp_path)
    out = tmp_path / "site"
    assert dashboard.is_due(out, NOW, 30)
    page = dashboard.build(SETTINGS, tmp_path, KEY, db, now=NOW)
    db.close()
    assert page == out / "index.html" and page.exists()
    assert json.loads((out / "data.json").read_text(encoding="utf-8"))["genere_le"] == NOW.isoformat()
    assert not dashboard.is_due(out, NOW + timedelta(minutes=20), 30)
    assert dashboard.is_due(out, NOW + timedelta(minutes=30), 30)


def test_downsample_keeps_last_point():
    curve = [(f"t{i}", Decimal(i)) for i in range(1000)]
    out = dashboard._downsample(curve, 50)
    assert len(out) == 50 and out[-1] == ["t999", 999.0] and out[0] == ["t0", 0.0]


def test_page_refreshes_itself_from_its_own_data_file():
    """Actualisation automatique : la page relit data.json (même site, sans cache) et se recharge
    seulement si le cycle a publié de nouvelles données."""
    template = TEMPLATE.read_text(encoding="utf-8")
    assert 'fetch("data.json?t="' in template and 'cache: "no-store"' in template
    assert "fresh.genere_le !== D.genere_le" in template and "document.hidden" in template
    settings = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    assert 6 <= settings["dashboard"]["every_minutes"] <= 30  # GitHub Pages : ~10 publications par heure au plus


def test_pulse_and_system_vitals_feed_the_beating_heart(tmp_path):
    """Organisme vivant : le pouls (derniers cycles, durée, erreurs) et les constantes du système
    nourrissent le cœur et le journal vital de la page ; la règle n°1 l'arrête (aucun battement)."""
    main, db = _setup(tmp_path)
    data = dashboard.collect(main, db, SETTINGS, ROOT, now=NOW + timedelta(minutes=10))
    db.close()
    pulse = data["sante"]["pouls"]
    assert [c["date"] for c in pulse] == sorted(c["date"] for c in pulse) and len(pulse) == 2
    assert pulse[0]["duree"] == 30.0 and pulse[0]["statut"] == "OK" and pulse[0]["erreur"] is False
    assert data["systeme"]["ordres_poses"] == 0 and data["sante"]["minutes_par_cycle"] == 2
    template = TEMPLATE.read_text(encoding="utf-8")
    assert 'id="heart"' in template and "C.faillite ? 0" in template  # faillite : le cœur s'arrête
    assert "prefers-reduced-motion" in template
    # Cœur anatomique : les constantes sont des légendes posées autour, hors de portée du battement.
    for part in ("aorte", "veine cave", "nœud sinusal", "ventricule gauche", "ventricule droit · confiance", "apex"):
        assert part in template
    assert "placeLabels" in template


def test_independent_audit_is_shown(tmp_path):
    """Le résultat de l'audit indépendant (branche data, audit/etat.json) apparaît sur la page."""
    main, db = _setup(tmp_path)
    (tmp_path / "_data" / "audit").mkdir(parents=True)
    (tmp_path / "_data" / "audit" / "etat.json").write_text(json.dumps(
        {"verifiees": 80, "non_verifiables": 2, "anomalies": [{"portefeuille": "principal", "detail": "achat à 0.1"}],
         "derniere_execution": "2026-10-15T19:30:00+00:00"}), encoding="utf-8")
    data = dashboard.collect(main, db, SETTINGS, tmp_path, now=NOW)
    db.close()
    assert data["audit"]["verifiees"] == 80 and data["audit"]["anomalies"] == 1
    assert data["audit"]["dernieres_anomalies"][0]["detail"] == "achat à 0.1"


def test_today_tells_the_last_24_hours_in_plain_french(tmp_path):
    main, db = _setup(tmp_path)
    data = dashboard.collect(main, db, SETTINGS, ROOT, now=NOW + timedelta(minutes=10))
    texts = {t["sujet"]: [] for t in data["aujourdhui"]}
    for t in data["aujourdhui"]:
        texts[t["sujet"]].append(t["texte"])
    assert set(texts) == {"capital", "strategie", "operations", "machine"}
    assert any(t.startswith("2 achats et 1 règlement en 24 h") for t in texts["operations"])
    assert any("1 pari terminé : 1 gagné(s), 0 perdu(s), résultat net +20,00 €" in t for t in texts["operations"])
    assert any("1 pari terminé, 1 gagnés pour 0,8 attendus" in t for t in texts["strategie"])
    assert any(t.startswith("Confiance ") for t in texts["strategie"])
    assert any(t.startswith("2 cycles en 24 h (un toutes les 2 min)") and "aucun en erreur" in t for t in texts["machine"])
    # Le texte venu de l'extérieur (question du marché) reste une donnée : la page l'insère en texte.
    assert "textContent" in TEMPLATE.read_text(encoding="utf-8")
    db.close()


def test_layout_is_set_in_the_config_with_guardrails(tmp_path):
    """17/10/2026 : la mise en page se règle dans config/dashboard.yaml ; le résumé et le capital
    (règle n°1) ne peuvent jamais être cachés ; « À la une » est borné."""
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "dashboard.yaml").write_text(yaml.safe_dump({
        "ordre": ["strategie", "inconnu", "resume", "strategie", "arene"], "masquer": ["capital", "resume", "systeme", "x"],
        "replier": ["organisme", "journal", "capital", "journal", "y"],
        "a_la_une": {"date": "2026-10-17", "points": [EVIL] + [f"point {i}" for i in range(9)] + ["y" * 400]}}),
        encoding="utf-8")
    lay = dashboard.layout(tmp_path)
    assert lay["ordre"] == ["strategie", "resume"] and lay["masquer"] == ["systeme"]
    assert lay["replier"] == ["journal"]  # le cœur, le résumé et le capital ne se replient jamais
    assert len(lay["a_la_une"]["points"]) == 5 and lay["a_la_une"]["points"][0] == EVIL
    assert dashboard.layout(tmp_path / "absent") == {"ordre": [], "masquer": [], "replier": [],
                                                      "a_la_une": {"date": "", "points": []}}
    main, db = _setup(tmp_path)
    data = dashboard.collect(main, db, SETTINGS, tmp_path, now=NOW)
    page = dashboard.render(data)
    assert data["mise_en_page"] == lay and EVIL not in page  # texte de la config échappé comme le reste
    repo = dashboard.layout(ROOT)  # la configuration du dépôt est valide
    assert repo["ordre"][0] in ("organisme", "resume") and not set(repo["masquer"]) & {"organisme", "resume", "capital"}
    assert "strategie" in repo["ordre"]


def test_principal_orders_are_listed_most_recent_first(ledger):
    buy(ledger, "b1", token="tA", shares="10", price="0.5", fee="0.1")
    ledger.record_sell(op_id="s1", token_id="tA", shares="4", price="0.6", fee="0.05", now=NOW + timedelta(minutes=5))
    ledger.record_settlement(op_id="r1", token_id="tA", payout_per_share="1", resolution="Yes", now=NOW + timedelta(minutes=9))
    opened = [{"created_at": "2026-10-14T12:30:00", "side": "BUY", "remaining": "5",
               "proposal": json.dumps({"market_id": "m-tB", "outcome": "No", "max_price": "0.42", "strategy_id": "mm"})}]
    out = dashboard.principal_orders(read_entries(ledger), {"m-tA": "Question A ?", "m-tB": "Question B ?"}, opened)
    assert [o["sens"] for o in out["executions"]] == ["règlement", "vente", "achat"]
    assert out["executions"][2]["marche"] == "Question A ?" and out["executions"][2]["montant"] == 5.1
    assert out["executions"][1]["montant"] == 2.35 and out["executions"][0]["marche"] == "Question A ?"
    assert out["compte"] == {"achats": 1, "ventes": 1, "reglements": 1}
    assert out["poses"] == [{"date": "2026-10-14T12:30", "sens": "achat", "marche": "Question B ?", "issue": "No",
                             "prix": 0.42, "parts": 5.0, "strategie": "mm"}]


def test_strategy_curves_are_computed_on_click_from_the_public_journal(tmp_path):
    """Demande d'Etienne (21/10/2026) : les courbes de la stratégie sont calculées au moment du clic à partir
    du journal public du capital principal ; rien n'est précalculé par le cycle."""
    root = tmp_path / "depot"
    (root / "config").mkdir(parents=True)
    (root / "config" / "dashboard.yaml").write_text('journaux_url: "https://exemple.org/x/"\n', encoding="utf-8")
    assert dashboard.fiche(root)["journaux"] == ""  # seule une adresse raw.githubusercontent.com
    real = dashboard.fiche(ROOT)
    assert real["journaux"] == "https://raw.githubusercontent.com/r655rkz7bt-stack/polymarket-labo-favori-sport/data/ledger/"
    template = TEMPLATE.read_text(encoding="utf-8")
    assert 'base + "ledger.jsonl"' in template and "!holder._done" in template  # chargé au premier toucher seulement


def test_strategy_curves_match_the_python_figures(tmp_path):
    """Les chiffres des courbes (JavaScript) sont ceux de confiance.py (Python) : opérations, résultat,
    paris gagnés en plus de l'attendu, confiance (t et par jour)."""
    import shutil
    import subprocess

    import pytest
    from polymarket_agent.confiance import confidence_percent, day_confidence, t_stat, trade_returns
    from polymarket_agent.reporting import closed_trades
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node absent : calcul JavaScript non vérifiable ici")
    path = tmp_path / "f.jsonl"
    main = Ledger.create(path, KEY, tmp_path / "f.lock", "1000", now=NOW)
    for i, (price, won) in enumerate([("0.80", True), ("0.85", False), ("0.90", True), ("0.82", True), ("0.88", True)]):
        main.record_buy(op_id=f"b{i}", token_id=f"t{i}", market_id=f"m{i}", event_id=f"e{i}", outcome="Yes",
                        shares="50", price=price, fee="0.1", strategy_id="f", strategy_version="0",
                        now=NOW + timedelta(hours=i), extra={"est_probability": str(float(price) + 0.05)})
        main.record_settlement(op_id=f"s{i}", token_id=f"t{i}", payout_per_share="1" if won else "0",
                               resolution="test", now=NOW + timedelta(hours=i, minutes=30) + timedelta(days=i // 3))
    main.heartbeat("c1", "OK", NOW + timedelta(days=2), total_estimated="1040")
    template = TEMPLATE.read_text(encoding="utf-8")
    code = (template[template.index("  function readJournal("):template.index("  // Courbe générique de la fiche")]
            + "var J = readJournal(require('fs').readFileSync(process.argv[2], 'utf8')), P = perTrade(J), l = P[P.length - 1];"
              "console.log(JSON.stringify({n: J.trades.length, pnl: J.trades.reduce(function (a, t) { return a + t.pnl; }, 0),"
              "excess: l.excess, conf: l.conf, day: l.confDay, beats: J.beats.length}));")
    script = tmp_path / "fiche.js"
    script.write_text(code, encoding="utf-8")
    js = json.loads(subprocess.run([node, str(script), str(path)], capture_output=True, text=True, check=True).stdout)
    trades = closed_trades(read_entries(main))
    excess = sum((float(t["pnl"]) > 0) - float(t["cost"]) / float(t["shares_bought"]) for t in trades)
    assert js["n"] == len(trades) == 5 and js["beats"] >= 1
    assert js["pnl"] == pytest.approx(sum(float(t["pnl"]) for t in trades))
    assert js["excess"] == pytest.approx(excess, abs=1e-4)
    assert js["conf"] == pytest.approx(confidence_percent(t_stat(trade_returns(trades))), abs=0.6)
    assert js["day"] == pytest.approx(100 * day_confidence(trades), abs=0.05)
