#!/usr/bin/env python3
"""
techndev-providers  cubegolem/store.py  v1.0.0
================================================
Persistenter SQLite-Store-of-Record fuer wiederkehrende cubegolem-Laeufe.

Im Unterschied zu cache.py (TTL-Cache fuer LIVE-Einzelabrufe) ist store.py
das Langzeit-Archiv fuer regelmaessige Voll-Laeufe:
  - aktueller Stand je Produkt (Upsert nach slug)
  - Preis-/Verfuegbarkeits-HISTORIE als Zeitreihe (nur bei echter Aenderung)
  - Lauf-Metadaten je (Lauf, Sektion)
CSV ist damit nur noch ein Export-Format on demand.

Transfer in die Haupt-DB (product-catalog):
  to_supplier_items() liefert [{"ean","ek_netto","titel"}] — genau das Format
  von product_catalog.stage_supplier_items(items, lieferant="cubegolem", ...).
  Der Provider importiert product-catalog NICHT (Abhaengigkeit laeuft andersrum);
  der Aufruf erfolgt product-catalog-seitig.

DB-Pfad: Env CUBEGOLEM_DB, sonst 'cubegolem_store.db'.

CHANGELOG
---------
v1.0.0  (2026-05-30)
  - Initiales Release. record_run() + Historie, latest()/history(),
    to_supplier_items(), export_csv(), stats().
"""
from __future__ import annotations

import csv
import os
import sqlite3
from pathlib import Path

from ._models import Product, now_iso

__version__ = "1.0.0"

DEFAULT_DB = os.environ.get("CUBEGOLEM_DB", "cubegolem_store.db")

# Produkt-Spalten (Reihenfolge = Dataclass-Felder, die in products gespiegelt werden)
_PROD_COLS = [
    "slug", "section", "name", "url", "ek_net", "base_net", "discount_pct",
    "currency", "release_date", "order_deadline", "in_stock", "category",
    "manufacturer", "ean", "sku", "image_url",
]
# Felder, deren Aenderung einen neuen History-Eintrag ausloest
_HIST_FIELDS = ("ek_net", "base_net", "in_stock", "release_date")


class CubeGolemStore:
    """SQLite-Store mit Produkt-Stammdaten + Preis-/Verfuegbarkeits-Historie."""

    def __init__(self, db_path: str = DEFAULT_DB):
        self.db_path = str(db_path)
        parent = Path(self.db_path).parent
        if str(parent):
            parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.db_path)
        c.row_factory = sqlite3.Row
        return c

    def _init_db(self) -> None:
        with self._conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS products (
                    slug           TEXT PRIMARY KEY,
                    section        TEXT,
                    name           TEXT,
                    url            TEXT,
                    ek_net         REAL,
                    base_net       REAL,
                    discount_pct   REAL,
                    currency       TEXT,
                    release_date   TEXT,
                    order_deadline TEXT,
                    in_stock       INTEGER,
                    category       TEXT,
                    manufacturer   TEXT,
                    ean            TEXT,
                    sku            TEXT,
                    image_url      TEXT,
                    first_seen     TEXT,
                    last_seen      TEXT
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS price_history (
                    slug         TEXT,
                    run_ts       TEXT,
                    ek_net       REAL,
                    base_net     REAL,
                    in_stock     INTEGER,
                    release_date TEXT,
                    PRIMARY KEY (slug, run_ts)
                )
            """)
            c.execute("""
                CREATE TABLE IF NOT EXISTS runs (
                    run_ts        TEXT,
                    section       TEXT,
                    product_count INTEGER,
                    new_count     INTEGER,
                    changed_count INTEGER,
                    PRIMARY KEY (run_ts, section)
                )
            """)
            c.execute("CREATE INDEX IF NOT EXISTS idx_prod_ean ON products(ean)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_prod_section ON products(section)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_hist_slug ON price_history(slug)")

    # ── Schreiben ─────────────────────────────────────────────────────────────
    def record_run(self, products: list[Product], *, run_ts: str | None = None) -> dict:
        """
        Speichert einen Lauf: Produkte upserten, Historie nur bei echter
        Preis-/Verfuegbarkeitsaenderung anhaengen, Lauf-Metadaten je Sektion.
        Gibt {run_ts, total, new, changed} zurueck.
        """
        ts = run_ts or now_iso()
        per_section: dict[str, list[int]] = {}   # section -> [count, new, changed]
        new_total = changed_total = 0

        with self._conn() as c:
            for p in products:
                old = c.execute(
                    "SELECT first_seen, ek_net, base_net, in_stock, release_date "
                    "FROM products WHERE slug=?", (p.slug,)).fetchone()
                is_new = old is None
                first_seen = ts if is_new else old["first_seen"]

                # Produkt-Stammdaten upserten
                vals = []
                for col in _PROD_COLS:
                    v = getattr(p, col)
                    vals.append(int(v) if col == "in_stock" else v)
                c.execute(f"""
                    INSERT INTO products ({",".join(_PROD_COLS)}, first_seen, last_seen)
                    VALUES ({",".join("?" * len(_PROD_COLS))}, ?, ?)
                    ON CONFLICT(slug) DO UPDATE SET
                        {",".join(f"{col}=excluded.{col}" for col in _PROD_COLS[1:])},
                        last_seen=excluded.last_seen
                """, vals + [first_seen, ts])

                # Historie nur bei Aenderung (oder erstem Auftreten)
                changed = is_new or any(
                    _norm(getattr(p, f)) != _norm(old[f]) for f in _HIST_FIELDS
                )
                if changed:
                    c.execute(
                        "INSERT OR REPLACE INTO price_history "
                        "(slug, run_ts, ek_net, base_net, in_stock, release_date) "
                        "VALUES (?,?,?,?,?,?)",
                        (p.slug, ts, p.ek_net, p.base_net,
                         int(p.in_stock), p.release_date))

                sec = p.section or ""
                st = per_section.setdefault(sec, [0, 0, 0])
                st[0] += 1
                if is_new:
                    st[1] += 1; new_total += 1
                if changed and not is_new:
                    st[2] += 1; changed_total += 1

            for sec, (cnt, nw, ch) in per_section.items():
                c.execute(
                    "INSERT OR REPLACE INTO runs "
                    "(run_ts, section, product_count, new_count, changed_count) "
                    "VALUES (?,?,?,?,?)", (ts, sec, cnt, nw, ch))

        return {"run_ts": ts, "total": len(products),
                "new": new_total, "changed": changed_total}

    # ── Lesen ─────────────────────────────────────────────────────────────────
    def latest(self, section: str | None = None) -> list[Product]:
        """Aktueller Stand aller (oder einer Sektion) Produkte."""
        q = f"SELECT {','.join(_PROD_COLS)} FROM products"
        params: tuple = ()
        if section:
            q += " WHERE section=?"; params = (section,)
        q += " ORDER BY section, name"
        with self._conn() as c:
            return [_row_to_product(r) for r in c.execute(q, params).fetchall()]

    def history(self, slug: str) -> list[dict]:
        """Preis-/Verfuegbarkeits-Zeitreihe eines Produkts (aelteste zuerst)."""
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT run_ts, ek_net, base_net, in_stock, release_date "
                "FROM price_history WHERE slug=? ORDER BY run_ts", (slug,)).fetchall()]

    def stats(self) -> dict:
        with self._conn() as c:
            prod = c.execute("SELECT COUNT(*) n FROM products").fetchone()["n"]
            hist = c.execute("SELECT COUNT(*) n FROM price_history").fetchone()["n"]
            runs = c.execute("SELECT COUNT(DISTINCT run_ts) n FROM runs").fetchone()["n"]
            with_ean = c.execute(
                "SELECT COUNT(*) n FROM products WHERE ean IS NOT NULL AND ean!=''"
            ).fetchone()["n"]
        return {"products": prod, "history_rows": hist, "runs": runs, "with_ean": with_ean}

    # ── Export / Transfer ─────────────────────────────────────────────────────
    def to_supplier_items(self, section: str | None = None) -> list[dict]:
        """
        Exportform fuer product_catalog.stage_supplier_items():
        [{"ean","ek_netto","titel"}] — nur Produkte mit EAN und EK.
        """
        out = []
        for p in self.latest(section):
            if p.ean and p.ek_net is not None:
                out.append({"ean": p.ean, "ek_netto": p.ek_net, "titel": p.name})
        return out

    def export_csv(self, path: str, *, section: str | None = None) -> int:
        cols = _PROD_COLS  # Stammdaten-Spalten
        n = 0
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(cols)
            for p in self.latest(section):
                d = p.to_dict()
                w.writerow([d.get(k, "") for k in cols])
                n += 1
        return n


# ══════════════════════════════════════════════════════════════════════════════
# Helfer
# ══════════════════════════════════════════════════════════════════════════════

def _norm(v):
    """Vergleichsnormalisierung (SQLite gibt in_stock als 0/1 zurueck)."""
    if isinstance(v, bool):
        return int(v)
    return v


def _row_to_product(r: sqlite3.Row) -> Product:
    return Product(
        section=r["section"], slug=r["slug"], name=r["name"], url=r["url"],
        ek_net=r["ek_net"], base_net=r["base_net"], discount_pct=r["discount_pct"],
        currency=r["currency"] or "EUR", release_date=r["release_date"],
        order_deadline=r["order_deadline"], in_stock=bool(r["in_stock"]),
        category=r["category"], manufacturer=r["manufacturer"], ean=r["ean"],
        sku=r["sku"], image_url=r["image_url"],
    )
