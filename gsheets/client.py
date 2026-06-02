#!/usr/bin/env python3
"""
techndev-providers  gsheets/client.py  v1.0.0
===============================================
Zentraler Google-Sheets-Connector (REST v4 via urllib, keine Google-SDKs).

Bidirektional — jede Komponente kann konsumieren:
  EXPORT:  write_table(tab, rows)   — Tab leeren + Zeilen schreiben
           append_rows(tab, rows)   — Zeilen anhaengen
  IMPORT:  read_table(tab)          — Zeilen als list[dict] (Kopfzeile = Keys)
           read_values(range)       — rohe Zellmatrix

Zeilen sind list[dict] (Spalten = Keys) ODER list[list] (rohe Werte).

CHANGELOG
---------
v1.0.0  (2026-05-30)
  - Initiales Release. read_table/write_table/append_rows/read_values/
    update_values/clear/list_tabs/ensure_tab/create_spreadsheet.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from ._auth import GSheetsAuthError, get_access_token, load_credentials

__version__ = "1.0.0"

_API = "https://sheets.googleapis.com/v4/spreadsheets"


class GSheetsClient:
    """
    Google-Sheets-Connector. spreadsheet_id kann im Konstruktor gesetzt oder
    je Aufruf uebergeben werden.

        gs = GSheetsClient("13xRjy...")
        gs.write_table("Preise", produkte)          # Export (list[dict])
        rows = gs.read_table("Preise")              # Import -> list[dict]
    """

    def __init__(self, spreadsheet_id: str = "", *, token_path: str | None = None,
                 client_secret_path: str | None = None, timeout: float = 30.0):
        self.spreadsheet_id = spreadsheet_id
        self.timeout = timeout
        self._creds = load_credentials(token_path=token_path,
                                       client_secret_path=client_secret_path)

    # ── HTTP ──────────────────────────────────────────────────────────────────
    def _request(self, method: str, url: str, *, body: dict | None = None,
                 _retry_auth: bool = True):
        data = json.dumps(body).encode() if body is not None else None
        headers = {
            "Authorization": "Bearer " + get_access_token(self._creds),
            "Content-Type": "application/json",
        }
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    raw = r.read().decode("utf-8")
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as e:
                code = e.code
                if code == 401 and _retry_auth:
                    # Token evtl. abgelaufen -> Cache leeren, einmal neu
                    from . import _auth
                    _auth._token_cache.pop(self._creds["client_id"], None)
                    return self._request(method, url, body=body, _retry_auth=False)
                if code == 429 and attempt < 2:
                    time.sleep(2 ** attempt * 2)
                    continue
                detail = e.read().decode("utf-8", "replace")[:300]
                raise RuntimeError(f"Sheets HTTP {code}: {detail}") from e
            except urllib.error.URLError as e:
                if attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                raise RuntimeError(f"Sheets Netzwerkfehler: {e.reason}") from e

    def _sid(self, spreadsheet_id: str | None) -> str:
        sid = spreadsheet_id or self.spreadsheet_id
        if not sid:
            raise ValueError("spreadsheet_id fehlt (Konstruktor oder Argument).")
        return sid

    def _values_url(self, sid: str, rng: str, suffix: str = "") -> str:
        return f"{_API}/{sid}/values/{urllib.parse.quote(rng, safe='')}{suffix}"

    # ── IMPORT ────────────────────────────────────────────────────────────────
    def read_values(self, rng: str, *, spreadsheet_id: str | None = None) -> list[list]:
        """Rohe Zellmatrix eines Bereichs/Tabs (leere Tabs -> [])."""
        sid = self._sid(spreadsheet_id)
        # UNFORMATTED_VALUE: Zahlen bleiben Zahlen (keine Locale-Formatierung).
        url = self._values_url(sid, rng) + "?valueRenderOption=UNFORMATTED_VALUE"
        res = self._request("GET", url)
        return res.get("values", [])

    def read_table(self, tab: str, *, spreadsheet_id: str | None = None,
                   header: bool = True) -> list[dict]:
        """Tab als list[dict] (erste Zeile = Spaltennamen). header=False -> Indizes."""
        vals = self.read_values(tab, spreadsheet_id=spreadsheet_id)
        if not vals:
            return []
        if not header:
            return [{i: v for i, v in enumerate(r)} for r in vals]
        cols = [str(x) for x in vals[0]]
        out = []
        for r in vals[1:]:
            r = list(r) + [""] * (len(cols) - len(r))      # kurze Zeilen auffuellen
            out.append(dict(zip(cols, r)))
        return out

    # ── EXPORT ────────────────────────────────────────────────────────────────
    @staticmethod
    def _to_matrix(rows, header):
        """rows (list[dict] | list[list]) -> (matrix inkl. Kopfzeile)."""
        if not rows:
            return [header] if header else []
        if isinstance(rows[0], dict):
            if header is None:
                header = list(rows[0].keys())
            matrix = [list(header)]
            for d in rows:
                matrix.append([_cell(d.get(h)) for h in header])
            return matrix
        # list[list]
        matrix = [list(header)] if header else []
        matrix.extend([_cell(v) for v in r] for r in rows)
        return matrix

    def update_values(self, rng: str, values: list[list], *,
                      spreadsheet_id: str | None = None) -> dict:
        sid = self._sid(spreadsheet_id)
        url = self._values_url(sid, rng) + "?valueInputOption=RAW"
        return self._request("PUT", url, body={"values": values})

    def clear(self, rng: str, *, spreadsheet_id: str | None = None) -> dict:
        sid = self._sid(spreadsheet_id)
        return self._request("POST", self._values_url(sid, rng, ":clear"), body={})

    def write_table(self, tab: str, rows, *, header: list | None = None,
                    clear: bool = True, spreadsheet_id: str | None = None) -> dict:
        """
        EXPORT: Tab (an)legen, optional leeren, Tabelle ab A1 schreiben.
        rows: list[dict] (Keys = Spalten) oder list[list].
        """
        sid = self._sid(spreadsheet_id)
        self.ensure_tab(tab, spreadsheet_id=sid)
        if clear:
            self.clear(f"{tab}!A1:ZZ100000", spreadsheet_id=sid)
        matrix = self._to_matrix(rows, header)
        if not matrix:
            return {"updatedRows": 0}
        return self.update_values(f"{tab}!A1", matrix, spreadsheet_id=sid)

    def append_rows(self, tab: str, rows, *, header: list | None = None,
                    spreadsheet_id: str | None = None) -> dict:
        """EXPORT: Zeilen ans Tab-Ende anhaengen (ohne Kopfzeile, ausser header gesetzt)."""
        sid = self._sid(spreadsheet_id)
        self.ensure_tab(tab, spreadsheet_id=sid)
        matrix = self._to_matrix(rows, header)
        if header is None and matrix and isinstance(rows[0], dict):
            matrix = matrix[1:]                            # generierte Kopfzeile weglassen
        if not matrix:
            return {"updates": {"updatedRows": 0}}
        url = (self._values_url(sid, f"{tab}!A1", ":append")
               + "?valueInputOption=RAW&insertDataOption=INSERT_ROWS")
        return self._request("POST", url, body={"values": matrix})

    # ── Struktur ──────────────────────────────────────────────────────────────
    def list_tabs(self, *, spreadsheet_id: str | None = None) -> list[str]:
        sid = self._sid(spreadsheet_id)
        res = self._request("GET", f"{_API}/{sid}?fields=sheets.properties.title")
        return [s["properties"]["title"] for s in res.get("sheets", [])]

    def ensure_tab(self, tab: str, *, spreadsheet_id: str | None = None) -> None:
        sid = self._sid(spreadsheet_id)
        if tab in self.list_tabs(spreadsheet_id=sid):
            return
        self._request("POST", f"{_API}/{sid}:batchUpdate",
                      body={"requests": [{"addSheet": {"properties": {"title": tab}}}]})

    def delete_tab(self, tab: str, *, spreadsheet_id: str | None = None) -> bool:
        """Loescht einen Tab (False wenn nicht vorhanden)."""
        sid = self._sid(spreadsheet_id)
        meta = self._request("GET", f"{_API}/{sid}?fields=sheets.properties")
        match = next((s["properties"]["sheetId"] for s in meta.get("sheets", [])
                      if s["properties"]["title"] == tab), None)
        if match is None:
            return False
        self._request("POST", f"{_API}/{sid}:batchUpdate",
                      body={"requests": [{"deleteSheet": {"sheetId": match}}]})
        return True

    def create_spreadsheet(self, title: str) -> str:
        res = self._request("POST", _API, body={"properties": {"title": title}})
        self.spreadsheet_id = res["spreadsheetId"]
        return self.spreadsheet_id


def _cell(v):
    """Zellwert fuer Sheets serialisieren (None -> '', bool/num/str bleibt)."""
    if v is None:
        return ""
    if isinstance(v, (str, int, float, bool)):
        return v
    return str(v)
