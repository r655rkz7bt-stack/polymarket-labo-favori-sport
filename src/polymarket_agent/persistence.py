"""Persistance SQLite : snapshots de marché, carnets, décisions, cycles.

Le capital n'est JAMAIS lu depuis cette base : la seule source de vérité est le
journal signé (ledger.py). La base sert à l'analyse, aux rapports et à
l'ancrage anti-troncature (dernière position connue du journal).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS cycles (
    cycle_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT,
    markets_scanned INTEGER DEFAULT 0,
    watchlist_size INTEGER DEFAULT 0,
    proposals INTEGER DEFAULT 0,
    accepted INTEGER DEFAULT 0,
    fills INTEGER DEFAULT 0,
    summary TEXT,
    error TEXT
);
CREATE TABLE IF NOT EXISTS market_snapshots (
    cycle_id TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    market_id TEXT NOT NULL,
    event_id TEXT,
    question TEXT,
    end_date TEXT,
    best_bid TEXT,
    best_ask TEXT,
    last_trade_price TEXT,
    volume_24h TEXT,
    liquidity TEXT,
    fee_type TEXT,
    eligible INTEGER,
    exclusion TEXT,
    issues TEXT
);
CREATE INDEX IF NOT EXISTS idx_market_snapshots_market ON market_snapshots(market_id, collected_at);
CREATE TABLE IF NOT EXISTS book_snapshots (
    cycle_id TEXT NOT NULL,
    collected_at TEXT NOT NULL,
    book_time TEXT,
    token_id TEXT NOT NULL,
    best_bid TEXT,
    best_ask TEXT,
    bid_depth TEXT,
    ask_depth TEXT,
    levels TEXT
);
CREATE INDEX IF NOT EXISTS idx_book_snapshots_token ON book_snapshots(token_id, collected_at);
CREATE TABLE IF NOT EXISTS decisions (
    op_id TEXT NOT NULL,
    cycle_id TEXT NOT NULL,
    decided_at TEXT NOT NULL,
    strategy_id TEXT,
    strategy_version TEXT,
    market_id TEXT,
    token_id TEXT,
    side TEXT,
    est_probability TEXT,
    prob_low TEXT,
    prob_high TEXT,
    max_price TEXT,
    shares TEXT,
    net_edge TEXT,
    accepted INTEGER,
    reasons TEXT,
    checks TEXT,
    fill_status TEXT,
    filled_shares TEXT,
    avg_price TEXT,
    fee TEXT,
    proposal TEXT,
    PRIMARY KEY (op_id, cycle_id)
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS pending_orders (
    op_id TEXT PRIMARY KEY,
    portfolio TEXT NOT NULL,
    token_id TEXT NOT NULL,
    side TEXT NOT NULL,
    remaining TEXT NOT NULL,
    fills INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    status TEXT NOT NULL,
    proposal TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pending_open ON pending_orders(portfolio, status);
CREATE TABLE IF NOT EXISTS ledger_anchor (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    seq INTEGER NOT NULL,
    hash TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


def _s(value: Any) -> str | None:
    return None if value is None else str(value)


def _iso(ts: datetime | None) -> str | None:
    return ts.astimezone(timezone.utc).isoformat() if ts else None


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Base illisible : mise de côté (jamais effacée) et base neuve. Le capital n'en dépend
        # pas ; seul l'ancrage anti-troncature est perdu, ce qui est signalé.
        self.recovered_from: Path | None = None
        try:
            self._connect()
        except sqlite3.DatabaseError:
            self.conn.close()
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            self.recovered_from = self.path.with_name(f"{self.path.name}.corrompue-{stamp}")
            self.path.rename(self.recovered_from)
            self._connect()

    def _connect(self) -> None:
        self.conn = sqlite3.connect(self.path)
        self.conn.row_factory = sqlite3.Row
        if self.conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise sqlite3.DatabaseError("quick_check en échec")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # ------------------------------------------------------------------ cycles

    def start_cycle(self, cycle_id: str, started_at: datetime) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO cycles (cycle_id, started_at, status) VALUES (?, ?, 'EN_COURS')",
            (cycle_id, _iso(started_at)))
        self.conn.commit()

    def finish_cycle(self, cycle_id: str, finished_at: datetime, status: str, *,
                     counts: dict[str, int] | None = None, summary: dict | None = None,
                     error: str | None = None) -> None:
        counts = counts or {}
        self.conn.execute(
            """UPDATE cycles SET finished_at=?, status=?, markets_scanned=?, watchlist_size=?,
               proposals=?, accepted=?, fills=?, summary=?, error=? WHERE cycle_id=?""",
            (_iso(finished_at), status, counts.get("markets", 0), counts.get("watchlist", 0),
             counts.get("proposals", 0), counts.get("accepted", 0), counts.get("fills", 0),
             json.dumps(summary or {}, ensure_ascii=False), error, cycle_id))
        self.conn.commit()

    # ------------------------------------------------------------------ données

    def save_markets(self, cycle_id: str, collected_at: datetime, markets) -> None:
        rows = [(cycle_id, _iso(collected_at), m.market_id, m.event_id, m.question,
                 _iso(m.end_date), _s(m.best_bid), _s(m.best_ask), _s(m.last_trade_price),
                 _s(m.volume_24h), _s(m.liquidity), m.fees.fee_type, int(m.eligible),
                 m.exclusion, json.dumps(m.issues, ensure_ascii=False)) for m in markets]
        self.conn.executemany(
            "INSERT INTO market_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows)
        self.conn.commit()

    def save_books(self, cycle_id: str, books) -> None:
        rows = []
        for book in books:
            levels = {"bids": [[str(l.price), str(l.size)] for l in book.bids[:10]],
                      "asks": [[str(l.price), str(l.size)] for l in book.asks[:10]]}
            rows.append((cycle_id, _iso(book.fetched_at), _iso(book.timestamp), book.token_id,
                         _s(book.best_bid), _s(book.best_ask),
                         str(sum(l.size for l in book.bids)), str(sum(l.size for l in book.asks)),
                         json.dumps(levels)))
        self.conn.executemany("INSERT INTO book_snapshots VALUES (?,?,?,?,?,?,?,?,?)", rows)
        self.conn.commit()

    def save_decision(self, cycle_id: str, decided_at: datetime, proposal, verdict,
                      fill=None) -> None:
        payload = {k: (list(v) if isinstance(v, tuple) else str(v))
                   for k, v in proposal.__dict__.items()}
        self.conn.execute(
            "INSERT OR REPLACE INTO decisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (proposal.op_id, cycle_id, _iso(decided_at), proposal.strategy_id,
             proposal.strategy_version, proposal.market_id, proposal.token_id, proposal.side,
             str(proposal.est_probability), str(proposal.prob_low), str(proposal.prob_high),
             str(proposal.max_price), str(proposal.shares), str(proposal.net_edge),
             int(verdict.accepted), json.dumps(verdict.reasons, ensure_ascii=False),
             json.dumps(verdict.checks), fill.status if fill else None,
             _s(fill.filled_shares) if fill else None, _s(fill.avg_price) if fill else None,
             _s(fill.fee) if fill else None, json.dumps(payload, ensure_ascii=False)))
        self.conn.commit()

    def get_value(self, key: str) -> str | None:
        """Petite mémoire entre cycles (durées, abstentions). Jamais de capital ici."""
        row = self.conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def set_value(self, key: str, value: str) -> None:
        self.conn.execute("INSERT OR REPLACE INTO kv VALUES (?, ?, ?)",
                          (key, value, datetime.now(timezone.utc).isoformat()))
        self.conn.commit()

    # ------------------------------------------------------------------ ancrage

    def get_anchor(self) -> tuple[int, str] | None:
        row = self.conn.execute("SELECT seq, hash FROM ledger_anchor WHERE id = 1").fetchone()
        return (row["seq"], row["hash"]) if row else None

    def set_anchor(self, seq: int, digest: str, now: datetime) -> None:
        self.conn.execute("INSERT OR REPLACE INTO ledger_anchor VALUES (1, ?, ?, ?)",
                          (seq, digest, _iso(now)))
        self.conn.commit()

    # ------------------------------------------------------------------ ordres posés
    # Ordres à cours limité en attente. Ce n'est PAS de l'argent : seul le journal signé compte.
    # Si la base est perdue, les ordres disparaissent (comme annulés) et rien d'autre ne change.

    def add_pending_order(self, portfolio: str, proposal, created_at: datetime,
                          expires_at: datetime) -> bool:
        """Pose un ordre. Un nouvel ordre remplace l'ordre ouvert du même portefeuille sur le
        même jeton et le même sens (une stratégie met son prix à jour à chaque cycle)."""
        self.conn.execute("UPDATE pending_orders SET status='REMPLACE', updated_at=? WHERE portfolio=? "
                          "AND token_id=? AND side=? AND status='OUVERT' AND op_id != ?",
                          (_iso(created_at), portfolio, proposal.token_id, proposal.side, proposal.op_id))
        payload = {k: (list(v) if isinstance(v, tuple) else str(v)) for k, v in proposal.__dict__.items()}
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO pending_orders VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (proposal.op_id, portfolio, proposal.token_id, proposal.side, str(proposal.shares), 0,
             _iso(created_at), _iso(expires_at), "OUVERT", json.dumps(payload, ensure_ascii=False),
             _iso(created_at)))
        # Historique des ordres terminés : 7 jours suffisent (le journal garde les exécutions).
        self.conn.execute("DELETE FROM pending_orders WHERE status != 'OUVERT' AND updated_at < ?",
                          (_iso(created_at - timedelta(days=7)),))
        self.conn.commit()
        return cur.rowcount > 0

    def cancel_orders_except(self, portfolio: str, now: datetime) -> int:
        """Annule les ordres ouverts des AUTRES portefeuilles (anciens fantômes, avant le 29/10/2026) : plus aucun
        cycle ne les suit ; sans cela ils resteraient « ouverts » pour toujours dans la base et le tableau de bord."""
        cur = self.conn.execute("UPDATE pending_orders SET status='ANNULE', updated_at=? WHERE portfolio != ? "
                                "AND status='OUVERT'", (_iso(now), portfolio))
        self.conn.commit()
        return cur.rowcount

    def open_orders(self, portfolio: str | None = None) -> list[dict]:
        sql, args = "SELECT * FROM pending_orders WHERE status='OUVERT'", ()
        if portfolio is not None:
            sql, args = sql + " AND portfolio=?", (portfolio,)
        return [dict(r) for r in self.conn.execute(sql + " ORDER BY created_at, op_id", args)]

    def update_order(self, op_id: str, *, remaining, fills: int, status: str, now: datetime) -> None:
        self.conn.execute("UPDATE pending_orders SET remaining=?, fills=?, status=?, updated_at=? "
                          "WHERE op_id=?", (str(remaining), fills, status, _iso(now), op_id))
        self.conn.commit()

    # ------------------------------------------------------------------ taille

    def prune(self, max_megabytes: float) -> bool:
        """Base tournante : supprime les plus vieux snapshots si la taille dépasse le plafond."""
        if self.path.stat().st_size <= max_megabytes * 1024 * 1024:
            return False
        for table in ("book_snapshots", "market_snapshots"):
            self.conn.execute(
                f"DELETE FROM {table} WHERE rowid IN (SELECT rowid FROM {table} "
                f"ORDER BY collected_at LIMIT (SELECT COUNT(*) / 4 FROM {table}))")
        self.conn.commit()
        self.conn.execute("VACUUM")
        return True
