#!/usr/bin/env python3
"""
techndev-providers  cubegolem/cache.py  v1.0.0
================================================
SQLite-Cache fuer CubeGolemProvider.

Warum?
  Jede Detailseite kostet 1 HTTP-Request (~0,8 s Hoeflichkeits-Delay). Eine
  Sektion wie Ultra Pro (~691 Produkte) ist sonst je Lauf teuer. Stammdaten
  (Name, Bild, EAN, Release-Datum) aendern sich selten — ein Cache vermeidet
  wiederholte Abrufe und erlaubt Resume nach Session-Ablauf.

Zugriffsmodell (analog brickmerge):
  get(slug)      — Cache-first: liefert gecachten Stand wenn der Preis-Tier
                   noch frisch ist (price_ttl), sonst get_live().
  get_live(slug) — Immer live: scrapt die Detailseite, aktualisiert den Cache.
                   Bei Netz-/Session-Fehler: Fallback auf gecachten Stand mit
                   price_is_live=False (als nicht-live erkennbar).

  Zuvor gespeicherte release_date/order_deadline/category (aus dem Grid)
  bleiben erhalten, wenn ein Live-Refresh sie nicht mitliefert.

CHANGELOG
---------
v1.0.0  (2026-05-30)
  - Initiales Release. put/get/get_live, TTL-gesteuerter Preis-Tier.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path

from ._models import Product, now_iso
from .scraper import CubeGolemProvider

__version__ = "1.0.0"

DEFAULT_DB   = os.environ.get("CUBEGOLEM_CACHE_DB", "cubegolem_cache.db")
PRICE_TTL_S  = 6 * 3600     # Preis-Tier: 6 Stunden

_COLUMNS = [
    "slug", "section", "name", "url", "ek_net", "base_net", "discount_pct",
    "currency", "release_date", "order_deadline", "in_stock", "category",
    "manufacturer", "ean", "sku", "image_url", "fetched_at",
]


# ══════════════════════════════════════════════════════════════════════════════
# Cache
# ══════════════════════════════════════════════════════════════════════════════

class CubeGolemCache:
    """Persistenter Cache; umschliesst einen CubeGolemProvider."""

    def __init__(self, db_path: str = DEFAULT_DB, *, session_cookie="",
                 provider: CubeGolemProvider | None = None,
                 price_ttl_s: int = PRICE_TTL_S):
        self.db_path     = str(db_path)
        self.provider    = provider or CubeGolemProvider(session_cookie)
        self.price_ttl_s = price_ttl_s
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        return c

    def _init_db(self) -> None:
        with self._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS products (
                    slug            TEXT PRIMARY KEY,
                    section         TEXT,
                    name            TEXT,
                    url             TEXT,
                    ek_net          REAL,
                    base_net        REAL,
                    discount_pct    REAL,
                    currency        TEXT,
                    release_date    TEXT,
                    order_deadline  TEXT,
                    in_stock        INTEGER,
                    category        TEXT,
                    manufacturer    TEXT,
                    ean             TEXT,
                    sku             TEXT,
                    image_url       TEXT,
                    fetched_at      TEXT,
                    price_fetched_at TEXT
                )
            """)
            c.execute("CREATE INDEX IF NOT EXISTS idx_section ON products(section)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_ean ON products(ean)")

    # ── Persistenz ────────────────────────────────────────────────────────────
    def put(self, p: Product) -> None:
        """Upsert; erhaelt Grid-Felder, falls der neue Stand sie nicht hat."""
        old = self._row(p.slug)
        if old is not None:
            for f in ("release_date", "order_deadline", "category"):
                if getattr(p, f) is None and old[f] is not None:
                    setattr(p, f, old[f])
        # in_stock als 0/1 ablegen; uebrige Felder direkt aus dem Dataclass.
        values = []
        for col in _COLUMNS:
            v = getattr(p, col)
            values.append(int(v) if col == "in_stock" else v)
        values.append(now_iso())            # price_fetched_at
        with self._conn() as c:
            c.execute(f"""
                INSERT INTO products ({",".join(_COLUMNS)}, price_fetched_at)
                VALUES ({",".join("?" * len(_COLUMNS))}, ?)
                ON CONFLICT(slug) DO UPDATE SET
                    {",".join(f"{col}=excluded.{col}" for col in _COLUMNS[1:])},
                    price_fetched_at=excluded.price_fetched_at
            """, values)

    def _row(self, slug: str) -> sqlite3.Row | None:
        with self._conn() as c:
            return c.execute("SELECT * FROM products WHERE slug=?", (slug,)).fetchone()

    @staticmethod
    def _to_product(row: sqlite3.Row, *, price_is_live: bool) -> Product:
        return Product(
            section=row["section"], slug=row["slug"], name=row["name"],
            url=row["url"], ek_net=row["ek_net"], base_net=row["base_net"],
            discount_pct=row["discount_pct"], currency=row["currency"] or "EUR",
            release_date=row["release_date"], order_deadline=row["order_deadline"],
            in_stock=bool(row["in_stock"]), category=row["category"],
            manufacturer=row["manufacturer"], ean=row["ean"], sku=row["sku"],
            image_url=row["image_url"], fetched_at=row["fetched_at"],
            price_is_live=price_is_live,
        )

    def _price_fresh(self, row: sqlite3.Row) -> bool:
        ts = row["price_fetched_at"]
        if not ts:
            return False
        try:
            age = (datetime.now() - datetime.fromisoformat(ts)).total_seconds()
        except ValueError:
            return False
        return age < self.price_ttl_s

    # ── Zugriff ───────────────────────────────────────────────────────────────
    def get(self, slug: str, **detail_kwargs) -> Product:
        """Cache-first; bei abgelaufenem Preis-Tier → get_live()."""
        row = self._row(slug)
        if row is not None and self._price_fresh(row):
            return self._to_product(row, price_is_live=False)
        return self.get_live(slug, **detail_kwargs)

    def get_live(self, slug: str, **detail_kwargs) -> Product:
        """Immer live; bei Fehler Fallback auf Cache (price_is_live=False)."""
        try:
            p = self.provider.get_product(slug, **detail_kwargs)
            self.put(p)
            return p
        except Exception:
            row = self._row(slug)
            if row is not None:
                return self._to_product(row, price_is_live=False)
            raise
