#!/usr/bin/env python3
"""
techndev-providers  gsheets/_auth.py  v1.0.0
==============================================
OAuth-Credential-Handling fuer den gsheets-Provider (Google Sheets REST v4).
Intern — oeffentliche Exporte via gsheets/__init__.py.

Nutzt das BESTEHENDE OAuth-Setup aus combo-shorts-video weiter:
  - client_secret*.json  (client_id, client_secret, token_uri)
  - youtube-token.json   (refresh_token; MUSS spreadsheets-Scope haben)

Der Access-Token wird per refresh_token-Grant geholt (urllib, keine
Google-SDK-Abhaengigkeit) und modulweit bis kurz vor Ablauf gecacht.

Lade-Reihenfolge je Datei (erste Quelle gewinnt):
  1. explizit uebergebener Pfad
  2. Env GSHEETS_TOKEN / GSHEETS_CLIENT_SECRET
  3. Auto-Discovery: combo-shorts-video/ in uebergeordneten Dirs

CHANGELOG
---------
v1.0.0  (2026-05-30)
  - Initiales Release. load_credentials(), get_access_token() (urllib-Refresh).
"""
from __future__ import annotations

import glob
import json
import os
import time
import urllib.parse
import urllib.request
from pathlib import Path

__version__ = "1.0.0"

_TOKEN_ENDPOINT_DEFAULT = "https://oauth2.googleapis.com/token"
# {client_id: {"access_token": str, "exp": float}}
_token_cache: dict[str, dict] = {}


class GSheetsAuthError(RuntimeError):
    """OAuth-Credentials fehlen/ungueltig oder Token-Refresh fehlgeschlagen."""


# ── Datei-Discovery ───────────────────────────────────────────────────────────

def _discover(filename_glob: str, env: str, explicit: str | None) -> Path | None:
    if explicit:
        p = Path(explicit)
        return p if p.exists() else None
    env_val = os.environ.get(env, "").strip()
    if env_val and Path(env_val).exists():
        return Path(env_val)
    base = Path(__file__).resolve().parent
    for _ in range(6):
        base = base.parent
        hits = sorted(glob.glob(str(base / "combo-shorts-video" / filename_glob)))
        if hits:
            return Path(hits[0])
    return None


def load_credentials(*, token_path: str | None = None,
                     client_secret_path: str | None = None) -> dict:
    """
    Liefert {client_id, client_secret, refresh_token, token_uri}.
    Wirft GSheetsAuthError, wenn etwas fehlt.
    """
    sec = _discover("client_secret*.json", "GSHEETS_CLIENT_SECRET", client_secret_path)
    tok = _discover("youtube-token.json", "GSHEETS_TOKEN", token_path)
    if not sec or not tok:
        raise GSheetsAuthError(
            "OAuth-Dateien nicht gefunden. Erwartet client_secret*.json + "
            "youtube-token.json (combo-shorts-video/ oder via GSHEETS_CLIENT_SECRET/"
            "GSHEETS_TOKEN). Token braucht spreadsheets-Scope."
        )
    raw = json.loads(sec.read_text(encoding="utf-8"))
    c = raw.get("installed") or raw.get("web") or raw
    token = json.loads(tok.read_text(encoding="utf-8"))
    refresh = token.get("refresh_token")
    if not (c.get("client_id") and c.get("client_secret") and refresh):
        raise GSheetsAuthError(
            "Unvollstaendige Credentials (client_id/client_secret/refresh_token)."
        )
    return {
        "client_id":     c["client_id"],
        "client_secret": c["client_secret"],
        "refresh_token": refresh,
        "token_uri":     c.get("token_uri", _TOKEN_ENDPOINT_DEFAULT),
    }


# ── Access-Token (refresh_token-Grant) ────────────────────────────────────────

def get_access_token(creds: dict) -> str:
    """Holt/cacht einen Access-Token (Refresh kurz vor Ablauf)."""
    cid = creds["client_id"]
    cached = _token_cache.get(cid)
    if cached and cached["exp"] - 60 > time.time():
        return cached["access_token"]

    data = urllib.parse.urlencode({
        "client_id":     creds["client_id"],
        "client_secret": creds["client_secret"],
        "refresh_token": creds["refresh_token"],
        "grant_type":    "refresh_token",
    }).encode()
    req = urllib.request.Request(creds["token_uri"], data=data,
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.loads(r.read().decode("utf-8"))
    except Exception as e:                                   # noqa: BLE001
        raise GSheetsAuthError(f"Token-Refresh fehlgeschlagen: {e}") from e
    at = body.get("access_token")
    if not at:
        raise GSheetsAuthError(f"Kein access_token in Antwort: {body}")
    _token_cache[cid] = {"access_token": at,
                         "exp": time.time() + int(body.get("expires_in", 3600))}
    return at
