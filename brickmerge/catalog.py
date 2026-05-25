#!/usr/bin/env python3
"""
techndev-providers  brickmerge/catalog.py  v1.0.0
===================================================
LEGO-Set-Katalog aus Brickmerge-CSV-Downloads (aktiv + EOL).
Lokaler SQLite-Cache fuer schnelle Lookups ohne HTTP.

Vorher: setcatalog.SetCatalog in mydealz-watcher/setcatalog.py.
Neu:    + EOL-Jahrgangslisten (letzte N Jahre)
        + SQLite-Cache statt reinem dict (EAN-Index, Status-Spalte)

CSV-Quellen (Brickmerge.de, cp1252, Semikolon):
  Aktive Sets:  brickmerge_current_lego_sets.csv        (woechentlich)
  EOL <Jahr>:   brickmerge_<year>_eol_lego_sets.csv    (einmal/Jahr)
  Spalten:      Nummer;Thema;Name;UVP;Jahr;EAN;ASIN;URL
  UVP-Format:   deutsches Dezimal (679,99 -> 679.99)

CHANGELOG
---------
v1.0.0  (2026-05-25)
  - Initiales Release, extrahiert + erweitert aus setcatalog.py.
  - SQLite-Cache: brickmerge_catalog.db (Pfad vom Consumer uebergeben).
  - SetCatalog.get(set_no), SetCatalog.find_by_ean(ean): O(1)-Lookups via dict-Index.
  - Download-Strategie:
      aktiv: MAX_AGE_ACTIVE (7 Tage, mtime-Check)
      EOL:   MAX_AGE_EOL    (30 Tage, mtime via DB-Metadaten)
  - Methoden: refresh_active(), refresh_eol(year), refresh_all_eol(n_years=5)
"""
from __future__ import annotations

import csv
import io
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from ._models import SetInfo

__version__ = "1.0.0"

# ══════════════════════════════════════════════════════════════════════════════
# Konstanten
# ══════════════════════════════════════════════════════════════════════════════

BASE_URL   = "https://www.brickmerge.de/files"
CSV_ACTIVE = f"{BASE_URL}/brickmerge_current_lego_sets.csv"
CSV_EOL    = f"{BASE_URL}/brickmerge_{{year}}_eol_lego_sets.csv"  # .format(year=)

CSV_ENCODING  = "cp1252"
CSV_DELIMITER = ";"

MAX_AGE_ACTIVE = 7    # Tage bis naechster Download der aktiven Liste
MAX_AGE_EOL    = 30   # Tage bis EOL-Liste erneut heruntergeladen wird

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# SQLite-Schema
_SCHEMA = """
CREATE TABLE IF NOT EXISTS sets (
    set_no          TEXT PRIMARY KEY,
    name            TEXT NOT NULL DEFAULT '',
    theme           TEXT NOT NULL DEFAULT '',
    uvp             REAL,
    year            INTEGER,
    ean             TEXT,
    asin            TEXT,
    brickmerge_url  TEXT,
    status          TEXT NOT NULL DEFAULT 'active',
    eol_year        INTEGER,
    updated_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ean ON sets (ean);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


# ══════════════════════════════════════════════════════════════════════════════
# Hilfsfunktionen
# ══════════════════════════════════════════════════════════════════════════════

def _fetch_csv(url: str) -> bytes | None:
    """
    Laedt die CSV von url. Gibt None bei HTTP-404 zurueck.
    Alle anderen Fehler werden als Exception weitergegeben.
    """
    req = Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(req, timeout=30) as resp:
            return resp.read()
    except HTTPError as e:
        if e.code == 404:
            return None
        raise


def _parse_csv(raw_bytes: bytes, status: str, eol_year: int | None) -> list[SetInfo]:
    """
    Parst eine Brickmerge-CSV (cp1252, Semikolon).
    Zeilen mit Parsing-Fehlern werden still uebersprungen.
    Spalten: Nummer;Thema;Name;UVP;Jahr;EAN;ASIN;URL
    """
    sets: list[SetInfo] = []
    updated = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        text   = raw_bytes.decode(CSV_ENCODING, errors="replace")
        reader = csv.reader(io.StringIO(text), delimiter=CSV_DELIMITER)
        next(reader, None)  # Header
        for row in reader:
            if len(row) < 7:
                continue
            try:
                uvp_raw  = row[3].replace(",", ".").strip()
                uvp      = float(uvp_raw) if uvp_raw else None
                year_raw = row[4].strip()
                yr       = int(year_raw) if year_raw.isdigit() else None
                sets.append(SetInfo(
                    set_no         = row[0].strip(),
                    theme          = row[1].strip(),
                    name           = row[2].strip(),
                    uvp            = uvp,
                    year           = yr,
                    ean            = row[5].strip() or None,
                    asin           = row[6].strip() or None,
                    brickmerge_url = (row[7].strip() if len(row) > 7 else None) or None,
                    status         = status,
                    eol_year       = eol_year,
                ))
            except (ValueError, IndexError):
                continue
    except Exception as exc:
        print(f"brickmerge.catalog: CSV-Parse-Fehler: {exc}", file=sys.stderr)
    return [s for s in sets if s.set_no]


def _current_year() -> int:
    return datetime.now().year


# ══════════════════════════════════════════════════════════════════════════════
# SetCatalog
# ══════════════════════════════════════════════════════════════════════════════

class SetCatalog:
    """
    Schneller Katalog-Cache fuer LEGO-Sets aus Brickmerge-CSV-Downloads.

    Speichert aktive und EOL-Sets in einer lokalen SQLite-DB.
    Laed beim ersten Zugriff automatisch, danach nur noch wenn veraltet.

    Verwendung:
        catalog = SetCatalog(db_path=Path('brickmerge_catalog.db'))
        info    = catalog.get('10294')          # SetInfo oder None
        result  = catalog.find_by_ean('...')    # SetInfo oder None
    """

    def __init__(
        self,
        db_path:     Path,
        auto_update: bool = True,
        eol_years:   int  = 5,
    ) -> None:
        self._db_path   = Path(db_path)
        self._eol_years = eol_years
        self._lock      = Lock()

        self._con = sqlite3.connect(
            str(self._db_path), check_same_thread=False, timeout=10
        )
        self._con.row_factory = sqlite3.Row
        self._con.executescript(_SCHEMA)
        self._con.commit()

        if auto_update:
            self._auto_update()

        self._index:     dict[str, SetInfo] = {}
        self._ean_index: dict[str, SetInfo] = {}
        self._build_index()

    # ── Public API ─────────────────────────────────────────────────────────────

    def get(self, set_no: str) -> SetInfo | None:
        """Liefert SetInfo fuer set_no oder None."""
        return self._index.get(str(set_no))

    def find_by_ean(self, ean: str) -> SetInfo | None:
        """Liefert SetInfo fuer eine EAN oder None (aktive vor EOL-Sets priorisiert)."""
        return self._ean_index.get(str(ean).strip())

    def all_sets(self, status: str | None = None) -> list[SetInfo]:
        """
        Alle Sets aus dem Cache.
        status='active' oder 'eol' zum Filtern; None = alle.
        """
        if status is None:
            return list(self._index.values())
        return [s for s in self._index.values() if s.status == status]

    def __len__(self) -> int:
        return len(self._index)

    def __contains__(self, set_no: str) -> bool:
        return str(set_no) in self._index

    # ── Update-Logik ───────────────────────────────────────────────────────────

    def _auto_update(self) -> None:
        """Prueft Alter von aktiver Liste + EOL-Listen und laedt bei Bedarf."""
        self.refresh_active(force=False)
        self.refresh_all_eol(n_years=self._eol_years, force=False)

    def refresh_active(self, force: bool = False) -> bool:
        """
        Laedt die aktive-Sets-CSV herunter wenn veraltet oder force=True.
        Gibt True zurueck wenn ein Download stattgefunden hat.
        """
        key = "active_downloaded_at"
        if not force:
            age_days = self._meta_age_days(key)
            if age_days is not None and age_days < MAX_AGE_ACTIVE:
                return False

        print("brickmerge.catalog: Lade aktive Sets ...", file=sys.stderr)
        raw = _fetch_csv(CSV_ACTIVE)
        if raw is None:
            print("brickmerge.catalog: Aktive-CSV nicht gefunden (404)", file=sys.stderr)
            return False

        sets = _parse_csv(raw, status="active", eol_year=None)
        self._upsert_sets(sets)
        self._set_meta(key, datetime.now(timezone.utc).isoformat(timespec="seconds"))
        print(
            f"brickmerge.catalog: {len(sets)} aktive Sets geladen.",
            file=sys.stderr,
        )
        self._build_index()
        return True

    def refresh_eol(self, year: int, force: bool = False) -> bool:
        """
        Laedt die EOL-CSV fuer ein bestimmtes Jahr.
        Gibt True zurueck wenn ein Download stattgefunden hat.
        """
        key = f"eol_{year}_downloaded_at"
        if not force:
            age_days = self._meta_age_days(key)
            if age_days is not None and age_days < MAX_AGE_EOL:
                return False

        url = CSV_EOL.format(year=year)
        print(f"brickmerge.catalog: Lade EOL-Liste {year} ...", file=sys.stderr)
        raw = _fetch_csv(url)
        if raw is None:
            print(
                f"brickmerge.catalog: EOL-Liste {year} nicht verfuegbar (404) — uebersprungen.",
                file=sys.stderr,
            )
            # Trotzdem Timestamp setzen: verhindert wiederholten 404-Check
            self._set_meta(key, datetime.now(timezone.utc).isoformat(timespec="seconds"))
            return False

        sets = _parse_csv(raw, status="eol", eol_year=year)
        self._upsert_sets(sets)
        self._set_meta(key, datetime.now(timezone.utc).isoformat(timespec="seconds"))
        print(
            f"brickmerge.catalog: {len(sets)} EOL-Sets ({year}) geladen.",
            file=sys.stderr,
        )
        self._build_index()
        return True

    def refresh_all_eol(self, n_years: int = 5, force: bool = False) -> None:
        """Laedt EOL-Listen fuer die letzten n_years Jahre."""
        current = _current_year()
        for year in range(current - n_years + 1, current + 1):
            self.refresh_eol(year, force=force)

    # ── Internes ───────────────────────────────────────────────────────────────

    def _upsert_sets(self, sets: list[SetInfo]) -> None:
        updated = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self._lock:
            self._con.executemany(
                """
                INSERT INTO sets
                    (set_no, name, theme, uvp, year, ean, asin,
                     brickmerge_url, status, eol_year, updated_at)
                VALUES
                    (:set_no, :name, :theme, :uvp, :year, :ean, :asin,
                     :brickmerge_url, :status, :eol_year, :updated_at)
                ON CONFLICT(set_no) DO UPDATE SET
                    name           = excluded.name,
                    theme          = excluded.theme,
                    uvp            = excluded.uvp,
                    year           = excluded.year,
                    ean            = excluded.ean,
                    asin           = excluded.asin,
                    brickmerge_url = excluded.brickmerge_url,
                    status         = excluded.status,
                    eol_year       = excluded.eol_year,
                    updated_at     = excluded.updated_at
                """,
                [
                    {**s.to_dict(), "updated_at": updated}
                    for s in sets
                ],
            )
            self._con.commit()

    def _build_index(self) -> None:
        """Laedt alle Rows aus SQLite in dict-Indizes (set_no + ean)."""
        idx:     dict[str, SetInfo] = {}
        ean_idx: dict[str, SetInfo] = {}
        with self._lock:
            rows = self._con.execute(
                "SELECT * FROM sets ORDER BY status DESC"  # 'eol' < 'active' → aktive gewinnen EAN-Index
            ).fetchall()
        for row in rows:
            s = SetInfo(
                set_no         = row["set_no"],
                name           = row["name"],
                theme          = row["theme"],
                uvp            = row["uvp"],
                year           = row["year"],
                ean            = row["ean"],
                asin           = row["asin"],
                brickmerge_url = row["brickmerge_url"],
                status         = row["status"],
                eol_year       = row["eol_year"],
            )
            idx[s.set_no] = s
            if s.ean:
                ean_idx[s.ean] = s   # aktive ueberschreiben EOL-Eintraege (ORDER BY)
        self._index     = idx
        self._ean_index = ean_idx

    def _meta_age_days(self, key: str) -> float | None:
        """Gibt Alter des Meta-Eintrags in Tagen zurueck oder None wenn nicht vorhanden."""
        row = self._con.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        try:
            ts = datetime.fromisoformat(row["value"])
            delta = datetime.now(timezone.utc) - ts.astimezone(timezone.utc)
            return delta.total_seconds() / 86400
        except (ValueError, OSError):
            return None

    def _set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._con.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                (key, value),
            )
            self._con.commit()

    def __del__(self) -> None:
        try:
            self._con.close()
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════════
# Modul-Level-Singleton (thread-naiv; fuer CLI-Nutzung)
# ══════════════════════════════════════════════════════════════════════════════

_catalog: SetCatalog | None = None
_catalog_db_path: Path | None = None


def get_catalog(
    db_path:     Path | None = None,
    auto_update: bool = True,
    eol_years:   int  = 5,
) -> SetCatalog:
    """
    Gibt den modulweiten SetCatalog zurueck (lazy, beim ersten Aufruf aufgebaut).
    db_path: Pfad zur SQLite-DB. Default: ~/.cache/techndev/brickmerge_catalog.db.
    """
    global _catalog, _catalog_db_path

    if db_path is None:
        db_path = Path.home() / ".cache" / "techndev" / "brickmerge_catalog.db"
        db_path.parent.mkdir(parents=True, exist_ok=True)

    if _catalog is None or _catalog_db_path != db_path:
        _catalog = SetCatalog(db_path, auto_update=auto_update, eol_years=eol_years)
        _catalog_db_path = db_path

    return _catalog
