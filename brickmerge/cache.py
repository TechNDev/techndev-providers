#!/usr/bin/env python3
"""
techndev-providers  brickmerge/cache.py  v1.0.0
================================================
Zwei-Tier-SQLite-Cache fuer BrickmergeProvider.

Warum ein Cache?
  Brickmerge-Seiten werden live gescrapt; jeder Aufruf kostet
  1 HTTP-Request (~0.5–2 s). Viele Felder aendern sich selten
  oder nie — ein Cache vermeidet wiederholte Fetches.

Tier-Strategie:
  ┌─────────────────────────────────┬────────────────────┐
  │ Felder                          │ TTL (Standard)     │
  ├─────────────────────────────────┼────────────────────┤
  │ best_price_current              │ kein Cache —       │
  │                                 │ immer live         │
  ├─────────────────────────────────┼────────────────────┤
  │ best_price_30d / 180d / alltime │ 6 Stunden          │
  │ seller_count                    │ (price_ttl)        │
  ├─────────────────────────────────┼────────────────────┤
  │ uvp_original / uvp_current      │ 30 Tage            │
  │ piece_count, age_min, weights   │ (static_ttl)       │
  │ box_*, dealer_pack_qty          │                    │
  │ release_month, eol_month        │                    │
  │ plc_months, minifig_*           │                    │
  │ pov, pov_rate                   │                    │
  └─────────────────────────────────┴────────────────────┘

Zugriffsmodell:
  get()      — Cache-first: liefert gecachte Daten wenn Preis-Tier frisch;
               ruft sonst get_live() auf.
  get_live() — Immer live: scrapt brickmerge.de, aktualisiert Cache selektiv.
               Bei Netzwerkfehler: Fallback auf gecachte Daten (inkl.
               moeglicherweise veraltetem best_price_current).

Typische Nutzung:
  cache = BrickmergeCache('brickmerge_cache.db')

  # Im Bot (Nutzer-Interaktion): immer frischer best_price_current
  mp = cache.get_live('10294')

  # Im Batch-Checker (viele Sets): Cache nutzen
  mp = cache.get('75192')

CHANGELOG
---------
v1.0.0  (2026-05-26)
  - Initiales Release.
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from ._models  import MarketPrices, now_iso
from .scraper  import BrickmergeProvider

__version__ = "1.0.0"

log = logging.getLogger('brickmerge.cache')

# Alle MarketPrices-Felder die im Cache gespeichert werden, aufgeteilt in Tiers.
# best_price_current wird immer live geliefert (kein eigenes Tier).

_PRICE_COLS = (
    'best_price_30d',
    'best_price_180d',
    'best_price_alltime',
    'best_price_alltime_days_ago',
    'seller_count',
)

_STATIC_COLS = (
    'name',
    'ean',
    'uvp_original',
    'uvp_current',
    'piece_count',
    'age_min',
    'weight_part_g',
    'weight_set_g',
    'box_l_cm',
    'box_w_cm',
    'box_h_cm',
    'dealer_pack_qty',
    'release_month',
    'eol_month',
    'plc_months',
    'minifig_count',
    'minifig_exclusive_count',
    'pov',
    'pov_rate',
)

_DDL = """
CREATE TABLE IF NOT EXISTS bm_cache (
    set_no                      TEXT    PRIMARY KEY,
    -- Immer live — kein Tier-Timestamp
    best_price_current          REAL,
    current_fetched_at          TEXT,
    -- Preis-Tier (price_ttl)
    prices_fetched_at           TEXT,
    best_price_30d              REAL,
    best_price_180d             REAL,
    best_price_alltime          REAL,
    best_price_alltime_days_ago INTEGER,
    seller_count                INTEGER,
    -- Statisches Tier (static_ttl)
    static_fetched_at           TEXT,
    name                        TEXT,
    ean                         TEXT,
    uvp_original                REAL,
    uvp_current                 REAL,
    piece_count                 INTEGER,
    age_min                     INTEGER,
    weight_part_g               INTEGER,
    weight_set_g                INTEGER,
    box_l_cm                    REAL,
    box_w_cm                    REAL,
    box_h_cm                    REAL,
    dealer_pack_qty             INTEGER,
    release_month               TEXT,
    eol_month                   TEXT,
    plc_months                  INTEGER,
    minifig_count               INTEGER,
    minifig_exclusive_count     INTEGER,
    pov                         REAL,
    pov_rate                    REAL,
    -- Metadaten
    url                         TEXT,
    source                      TEXT
)
"""


class BrickmergeCache:
    """
    Zwei-Tier-SQLite-Cache fuer BrickmergeProvider.

    Parameter:
        db_path         — Pfad zur SQLite-Datei (wird angelegt wenn nicht vorhanden).
        price_ttl_hours — TTL fuer Preis-Tier in Stunden (Standard: 6).
        static_ttl_days — TTL fuer statisches Tier in Tagen (Standard: 30).
        timeout         — HTTP-Timeout in Sekunden fuer BrickmergeProvider.
    """

    DEFAULT_PRICE_TTL_HOURS  = 6
    DEFAULT_STATIC_TTL_DAYS  = 30

    def __init__(
        self,
        db_path:          str | Path,
        *,
        price_ttl_hours:  int = DEFAULT_PRICE_TTL_HOURS,
        static_ttl_days:  int = DEFAULT_STATIC_TTL_DAYS,
        timeout:          int = 20,
    ) -> None:
        self._db_path    = Path(db_path)
        self._price_ttl  = price_ttl_hours * 3600
        self._static_ttl = static_ttl_days  * 86400
        self._provider   = BrickmergeProvider(timeout=timeout)
        self._init_db()

    # ── Oeffentliche API ───────────────────────────────────────────────────────

    def get(
        self,
        set_no:   str,
        *,
        ean_hint: str   | None = None,
        uvp_hint: float | None = None,
        url_hint: str   | None = None,
    ) -> MarketPrices | None:
        """
        Cache-first: liefert gecachte Daten wenn Preis-Tier frisch.
        Fuer interaktive Abfragen (immer live) → get_live() verwenden.

        Gibt None zurueck wenn Set bei Brickmerge nicht vorhanden.
        """
        row = self._load(set_no)
        if row and self._is_fresh(row['prices_fetched_at'], self._price_ttl):
            log.debug('Cache-Hit (Preis-Tier) fuer %s', set_no)
            return self._row_to_mp(row, set_no)
        return self.get_live(
            set_no,
            ean_hint=ean_hint,
            uvp_hint=uvp_hint,
            url_hint=url_hint,
        )

    def get_live(
        self,
        set_no:   str,
        *,
        ean_hint: str   | None = None,
        uvp_hint: float | None = None,
        url_hint: str   | None = None,
    ) -> MarketPrices | None:
        """
        Holt immer frische Daten von brickmerge.de und aktualisiert den Cache.

        Update-Logik:
          - best_price_current: immer
          - Preis-Tier:         wenn aelter als price_ttl
          - Statisches Tier:    wenn aelter als static_ttl

        Bei Netzwerkfehler: Fallback auf gecachte Daten (inkl. Warnung im Log).
        Gibt None zurueck wenn Set bei Brickmerge nicht vorhanden (404).
        """
        try:
            mp = self._provider.get_prices(
                set_no,
                ean_hint=ean_hint,
                uvp_hint=uvp_hint,
                url_hint=url_hint,
            )
        except Exception as exc:
            log.warning('get_live: Fetch fuer %s fehlgeschlagen (%s) — Fallback auf Cache', set_no, exc)
            row = self._load(set_no)
            if row:
                return self._row_to_mp(row, set_no)
            raise

        if mp is None:
            return None

        row          = self._load(set_no)
        price_stale  = not row or not self._is_fresh(row['prices_fetched_at'],  self._price_ttl)
        static_stale = not row or not self._is_fresh(row['static_fetched_at'],  self._static_ttl)

        self._save(mp, update_prices=price_stale, update_static=static_stale)
        log.debug(
            'get_live %s: price_update=%s static_update=%s',
            set_no, price_stale, static_stale,
        )
        return mp

    def invalidate(self, set_no: str) -> None:
        """Loescht den Cache-Eintrag fuer set_no (erzwingt naechsten Fetch)."""
        with sqlite3.connect(self._db_path) as con:
            con.execute('DELETE FROM bm_cache WHERE set_no = ?', (set_no,))
        log.debug('Cache-Eintrag fuer %s geloescht', set_no)

    def clear(self) -> None:
        """Loescht den gesamten Cache."""
        with sqlite3.connect(self._db_path) as con:
            con.execute('DELETE FROM bm_cache')
        log.info('Cache vollstaendig geleert')

    # ── Interna ────────────────────────────────────────────────────────────────

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._db_path) as con:
            con.execute(_DDL)

    def _load(self, set_no: str) -> dict | None:
        with sqlite3.connect(self._db_path) as con:
            con.row_factory = sqlite3.Row
            cur = con.execute('SELECT * FROM bm_cache WHERE set_no = ?', (set_no,))
            row = cur.fetchone()
            return dict(row) if row else None

    @staticmethod
    def _is_fresh(fetched_at: str | None, ttl_seconds: int) -> bool:
        if not fetched_at:
            return False
        try:
            age = (datetime.now() - datetime.fromisoformat(fetched_at)).total_seconds()
            return age < ttl_seconds
        except (ValueError, TypeError):
            return False

    def _save(
        self,
        mp:             MarketPrices,
        *,
        update_prices:  bool,
        update_static:  bool,
    ) -> None:
        """
        Schreibt mp in die Datenbank.

        - best_price_current wird immer aktualisiert.
        - Preis-/Statisches Tier nur wenn entsprechendes Flag gesetzt.
        - Nutzt UPSERT (INSERT OR REPLACE) fuer neuen Eintrag,
          ansonsten gezielte UPDATE-Statements pro Tier.
        """
        now = now_iso()
        row = self._load(mp.set_no)

        if row is None:
            # Erster Eintrag: alle verfuegbaren Felder eintragen
            cols = (
                'set_no',
                'best_price_current', 'current_fetched_at',
                'prices_fetched_at',
                *_PRICE_COLS,
                'static_fetched_at',
                *_STATIC_COLS,
                'url', 'source',
            )
            vals = (
                mp.set_no,
                mp.best_price_current, now,
                now,
                *[getattr(mp, c) for c in _PRICE_COLS],
                now,
                *[getattr(mp, c) for c in _STATIC_COLS],
                mp.url, mp.source,
            )
            placeholders = ', '.join('?' * len(cols))
            col_list     = ', '.join(cols)
            with sqlite3.connect(self._db_path) as con:
                con.execute(
                    f'INSERT OR REPLACE INTO bm_cache ({col_list}) VALUES ({placeholders})',
                    vals,
                )
            return

        with sqlite3.connect(self._db_path) as con:
            # best_price_current: immer
            con.execute(
                'UPDATE bm_cache SET best_price_current = ?, current_fetched_at = ? WHERE set_no = ?',
                (mp.best_price_current, now, mp.set_no),
            )
            if update_prices:
                set_clause = ', '.join(f'{c} = ?' for c in _PRICE_COLS)
                con.execute(
                    f'UPDATE bm_cache SET prices_fetched_at = ?, {set_clause} WHERE set_no = ?',
                    (now, *[getattr(mp, c) for c in _PRICE_COLS], mp.set_no),
                )
            if update_static:
                set_clause = ', '.join(f'{c} = ?' for c in _STATIC_COLS)
                con.execute(
                    f'UPDATE bm_cache SET static_fetched_at = ?, {set_clause} WHERE set_no = ?',
                    (now, *[getattr(mp, c) for c in _STATIC_COLS], mp.set_no),
                )

    def _row_to_mp(self, row: dict, set_no: str) -> MarketPrices:
        """Konvertiert einen DB-Row-Dict in ein MarketPrices-Objekt."""
        return MarketPrices(
            set_no                      = set_no,
            name                        = row.get('name'),
            ean                         = row.get('ean'),
            uvp_original                = row.get('uvp_original'),
            uvp_current                 = row.get('uvp_current'),
            best_price_alltime          = row.get('best_price_alltime'),
            best_price_alltime_days_ago = row.get('best_price_alltime_days_ago'),
            best_price_180d             = row.get('best_price_180d'),
            best_price_30d              = row.get('best_price_30d'),
            best_price_current          = row.get('best_price_current'),
            seller_count                = row.get('seller_count'),
            piece_count                 = row.get('piece_count'),
            age_min                     = row.get('age_min'),
            weight_part_g               = row.get('weight_part_g'),
            weight_set_g                = row.get('weight_set_g'),
            box_l_cm                    = row.get('box_l_cm'),
            box_w_cm                    = row.get('box_w_cm'),
            box_h_cm                    = row.get('box_h_cm'),
            dealer_pack_qty             = row.get('dealer_pack_qty'),
            release_month               = row.get('release_month'),
            eol_month                   = row.get('eol_month'),
            plc_months                  = row.get('plc_months'),
            minifig_count               = row.get('minifig_count'),
            minifig_exclusive_count     = row.get('minifig_exclusive_count'),
            pov                         = row.get('pov'),
            pov_rate                    = row.get('pov_rate'),
            source                      = row.get('source') or 'brickmerge',
            url                         = row.get('url')    or '',
            fetched_at                  = row.get('current_fetched_at') or '',
            # best_price_current kommt aus dem Cache — explizit markieren
            price_is_live               = False,
        )
