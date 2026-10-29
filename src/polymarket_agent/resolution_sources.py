"""Source de résolution de chaque marché (chantier prioritaire n°0, décision d'Etienne du 15/10/2026).

Chaque marché Polymarket indique d'après quoi il sera tranché : champ `resolutionSource` de Gamma
(une URL) et règles en clair dans `description`. Ce module :
1. archive, pour les marchés surveillés, la source, son domaine et les règles (table `market_rules`) ;
2. tient l'inventaire des domaines les plus fréquents, avec pour chacun le lecteur dédié qui lit
   directement cette source (ou la raison de son absence) : CATALOG ci-dessous, libre ;
3. donne aux stratégies, pour un marché, le domaine qui le tranche (`domain_for`).

Inventaire du 15/10/2026 (500 marchés les plus actifs, échéance < 30 jours) : uefa.com 183,
binance.com 69, nfl.com 23, x.com 18, ncaa.com 17, hltv.org 11, mlb.com 11, nhl.com 11, gol.gg 10,
dotabuff.com 9, pyth 11, federalreserve.gov 5, weather.gov 3… ; 77 sans champ (souvent une URL
dans la description : ice.com, dadosabertos.tse.jus.br…). Détail : experiments/sources_resolution.md.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urlparse

SCHEMA = """
CREATE TABLE IF NOT EXISTS market_rules (
    market_id TEXT PRIMARY KEY,
    question TEXT,
    end_date TEXT,
    resolution_source TEXT,
    domain TEXT,
    description TEXT,
    first_seen TEXT NOT NULL,
    last_seen TEXT NOT NULL
);
"""
MAX_DESCRIPTION = 4000
_URL = re.compile(r"https?://[^\s)\"'<>\]]+")

# Domaine → (organisme, lecteur dans le projet ou None, remarque). Le chercheur complète.
CATALOG: dict[str, tuple[str, str | None, str]] = {
    "binance.com": ("Binance (bourse crypto)", "crypto_prices",
                    "bougies officielles via data-api.binance.vision (données de marché publiques)"),
    "mlb.com": ("Major League Baseball", "mlb_stats", "statsapi.mlb.com, API publique de la ligue"),
    "nhl.com": ("National Hockey League", "nhl_stats", "api-web.nhle.com, API publique de la ligue"),
    "uefa.com": ("UEFA", "football_data",
                 "pas d'API publique UEFA ; football-data.org (clé gratuite FOOTBALL_DATA_KEY) donne les scores"),
    "nfl.com": ("National Football League", None,
                "pas d'API publique officielle ; en complément : cotes des bookmakers (odds_api, ODDS_API_KEY)"),
    "nba.com": ("NBA", None, "pas d'API publique officielle documentée ; en complément : odds_api"),
    "x.com": ("X (Twitter)", None, "API payante : interdit n°2"),
    "ncaa.com": ("NCAA", None, "pas d'API publique documentée"),
    "hltv.org": ("HLTV (esport CS)", None, "pas d'API ; pages protégées contre les robots : on ne contourne pas"),
    "gol.gg": ("Games of Legends (esport LoL)", None, "pas d'API publique"),
    "dotabuff.com": ("Dotabuff (esport Dota 2)", None, "pas d'API ; api.opendota.com est l'équivalent ouvert (chantier n°15)"),
    "federalreserve.gov": ("Réserve fédérale américaine", "rss", "flux RSS officiels (déjà lus)"),
    "weather.gov": ("NOAA (station d'aéroport citée)", "meteo_metar",
                    "relevés METAR officiels via aviationweather.gov ; max METAR ≤ max officiel"),
    "aviationweather.gov": ("NOAA (METAR de la station citée dans le lien)", "meteo_metar",
                            "nouvelle source des marchés de température depuis le 19-20/10/2026 (199 événements sur 255) : relevés METAR directs"),
    "weather.gov.hk": ("Observatoire de Hong Kong (Daily Extract)", "meteo_hko",
                       "open data data.weather.gov.hk : max / min depuis minuit au dixième ; tranches par troncature"),
    "portwatch.imf.org": ("IMF PortWatch (passages dans les détroits)", "portwatch",
                          "ArcGIS public du FMI (Daily_Chokepoints_Data), mis à jour le mardi jusqu'au dimanche ; 96 événements le 23/10/2026"),
    "app.pyth.com": ("Pyth (oracle de prix)", None, "hermes.pyth.network, API publique : à étudier"),
    "pythdata.app": ("Pyth (oracle de prix)", None, "hermes.pyth.network, API publique : à étudier"),
    "arena.ai": ("LMArena (classement des modèles d'IA)", None, "chantier n°9"),
    "atptour.com": ("ATP (tennis)", None, "pas d'API publique"),
    "wtatennis.com": ("WTA (tennis)", None, "pas d'API publique"),
    "euroleaguebasketball.net": ("Euroleague", None, "pas d'API publique documentée"),
    "ice.com": ("ICE (bourse de matières premières)", None, "données payantes"),
    "dadosabertos.tse.jus.br": ("Tribunal électoral du Brésil", None, "données ouvertes officielles : à étudier"),
}


def domain_of(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    try:
        host = urlparse(url if "://" in url else f"https://{url}").netloc.lower()
    except ValueError:
        return ""
    host = host.split("@")[-1].split(":")[0]
    return host.removeprefix("www.")


def source_of(resolution_source: str, description: str) -> tuple[str, str]:
    """(URL, domaine) qui tranche le marché : le champ dédié, sinon la première URL des règles."""
    url = (resolution_source or "").strip()
    if not url:
        found = _URL.search(description or "")
        url = found.group(0).rstrip(".,;") if found else ""
    return url, domain_of(url)


def archive(db, markets, now: datetime) -> int:
    """Enregistre (ou rafraîchit) la source de résolution des marchés ; retourne le nombre archivé."""
    db.conn.executescript(SCHEMA)
    stamp = now.astimezone(timezone.utc).isoformat(timespec="seconds")
    rows = []
    for m in markets:
        url, domain = source_of(getattr(m, "resolution_source", ""), getattr(m, "description", ""))
        rows.append((m.market_id, m.question, m.end_date.isoformat() if m.end_date else None, url, domain,
                     (getattr(m, "description", "") or "")[:MAX_DESCRIPTION], stamp, stamp))
    db.conn.executemany(
        "INSERT INTO market_rules VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(market_id) DO UPDATE SET "
        "question=excluded.question, end_date=excluded.end_date, resolution_source=excluded.resolution_source, "
        "domain=excluded.domain, description=excluded.description, last_seen=excluded.last_seen", rows)
    db.conn.commit()
    return len(rows)


def domain_for(db, market_id: str) -> str:
    db.conn.executescript(SCHEMA)
    row = db.conn.execute("SELECT domain FROM market_rules WHERE market_id=?", (market_id,)).fetchone()
    return row[0] if row else ""


def inventory(db, limit: int = 30) -> list[dict]:
    """Domaines les plus fréquents parmi les marchés archivés, avec leur lecteur."""
    db.conn.executescript(SCHEMA)
    counts = Counter(r[0] or "" for r in db.conn.execute("SELECT domain FROM market_rules"))
    out = []
    for domain, n in counts.most_common(limit):
        org, reader, note = CATALOG.get(domain, ("", None, "à étudier" if domain else "aucune source indiquée"))
        out.append({"domaine": domain or "(aucun)", "marches": n, "organisme": org,
                    "lecteur": reader, "remarque": note})
    return out


def status_text(db, top: int = 5) -> str:
    inv = inventory(db, limit=100_000)
    total = sum(i["marches"] for i in inv)
    if not total:
        return "aucun marché archivé"
    covered = sum(i["marches"] for i in inv if i["lecteur"])
    head = ", ".join(f"{i['domaine']} {i['marches']}" for i in inv[:top])
    return f"{total} marchés ; lus à la source : {covered / total:.0%} ; principales : {head}"[:300]


def markdown(db, now: datetime) -> str:
    lines = [f"# Sources de résolution des marchés surveillés ({now:%d/%m/%Y %H:%M} UTC)", "",
             "| domaine | marchés | organisme | lecteur | remarque |", "|---|---|---|---|---|"]
    lines += [f"| {i['domaine']} | {i['marches']} | {i['organisme']} | {i['lecteur'] or '—'} | {i['remarque']} |"
              for i in inventory(db)]
    return "\n".join(lines) + "\n"
