#!/usr/bin/env python3
"""
amazon_sp  _credentials.py  v1.0.0
=====================================
Zentrales Credential-Management fuer amazon_sp.

Lade-Reihenfolge (erste vollstaendige Quelle gewinnt):
  1. Explizit uebergebenes dict (rueckwaertskompatibel)
  2. Modul-Level Cache (gesetzt via configure())
  3. Env-Var AMZ_EINKAUF_CONFIG -> Pfad zu JSON-Datei (sp_api-Section)
  4. Auto-Discovery: sucht amz_einkauf_config.json in uebergeordneten Dirs
  5. RuntimeError mit hilfreicher Fehlermeldung

Akzeptiertes Dateiformat (amz_einkauf_config.json):
  { "sp_api": { "refresh_token": "...", "lwa_app_id": "...", "lwa_client_secret": "..." } }

  Direkte Felder ohne sp_api-Wrapper werden ebenfalls akzeptiert:
  { "refresh_token": "...", "lwa_app_id": "...", "lwa_client_secret": "..." }

CHANGELOG
---------
v1.0.0  (2026-05-30)
  - Initiales Release.
  - configure(), get_credentials() als oeffentliche API.
  - Auto-Discovery sucht bis 5 Ebenen nach oben nach amz_einkauf_config.json.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

__version__ = "1.0.0"

_REQUIRED = ('refresh_token', 'lwa_app_id', 'lwa_client_secret')
_OPTIONAL = ('aws_access_key', 'aws_secret_key', 'role_arn', 'seller_id')
_PLACEHOLDER = frozenset(('...', 'TODO', 'Atza|...', 'amzn1.application-oa2-client...'))

# Modul-Level Cache — gesetzt via configure() oder beim ersten Auto-Load
_cached: Optional[dict] = None


# ── Oeffentliche API ──────────────────────────────────────────────────────────

def configure(credentials: dict) -> None:
    """
    Setzt Credentials fuer die laufende Python-Session (Modul-Level Cache).
    Alle nachfolgenden Aufrufe ohne explizites credentials-Argument nutzen
    diesen Wert, ohne dass erneut geladen wird.

    Akzeptiert sowohl { 'refresh_token': ..., ... } als auch
    { 'sp_api': { 'refresh_token': ..., ... } }.
    """
    global _cached
    _cached = _extract_and_validate(credentials)


def get_credentials(credentials: Optional[dict] = None) -> dict:
    """
    Gibt Credentials zurueck — aus explizitem Parameter, Cache oder Auto-Load.

    Rueckgabe: dict mit refresh_token, lwa_app_id, lwa_client_secret
               (+ optionale Felder wenn vorhanden).
    Wirft RuntimeError wenn keine vollstaendige Quelle gefunden wird.
    """
    if credentials is not None:
        return _extract_and_validate(credentials)
    if _cached is not None:
        return _cached
    return _auto_load()


# ── Interne Helfer ────────────────────────────────────────────────────────────

def _is_real(value) -> bool:
    """True wenn Wert gesetzt und kein Platzhalter."""
    return bool(value) and str(value).strip() not in _PLACEHOLDER


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def _extract_and_validate(source: dict) -> dict:
    """
    Extrahiert SP-API-Keys aus source (direkt oder unter 'sp_api').
    Wirft ValueError wenn Pflichtfelder fehlen oder Platzhalter enthalten.
    """
    sp = source.get('sp_api', source)
    if not all(_is_real(sp.get(k)) for k in _REQUIRED):
        missing = [k for k in _REQUIRED if not _is_real(sp.get(k))]
        raise ValueError(
            f"Unvollstaendige SP-API-Credentials — fehlend/Platzhalter: {', '.join(missing)}"
        )
    result = {k: sp[k] for k in _REQUIRED}
    for opt in _OPTIONAL:
        if _is_real(sp.get(opt)):
            result[opt] = sp[opt]
    return result


def _try_file(path: Path) -> Optional[dict]:
    """Liest JSON-Datei und gibt valide Creds zurueck, sonst None."""
    if not path.exists():
        return None
    data = _read_json(path)
    sp = data.get('sp_api', data)
    if all(_is_real(sp.get(k)) for k in _REQUIRED):
        result = {k: sp[k] for k in _REQUIRED}
        for opt in _OPTIONAL:
            if _is_real(sp.get(opt)):
                result[opt] = sp[opt]
        return result
    return None


def _auto_load() -> dict:
    """
    Versucht Credentials aus Env-Var und Auto-Discovery zu laden.
    Setzt _cached bei Erfolg. Wirft RuntimeError wenn nichts gefunden.
    """
    global _cached

    # 1. AMZ_EINKAUF_CONFIG Env-Var
    env_path = os.environ.get('AMZ_EINKAUF_CONFIG', '').strip()
    if env_path:
        creds = _try_file(Path(env_path))
        if creds:
            _cached = creds
            return _cached

    # 2. Auto-Discovery: von __file__ (amazon_sp/_credentials.py) aufwaerts
    #    Sucht bis 6 Ebenen nach oben; prueft typische Ablage-Orte.
    base = Path(__file__).resolve().parent
    for _ in range(6):
        base = base.parent
        for candidate in (
            base / 'amazon-vorqualifizierung' / 'amz_einkauf_config.json',
            base / 'amz_einkauf_config.json',
        ):
            creds = _try_file(candidate)
            if creds:
                _cached = creds
                return _cached

    raise RuntimeError(
        "Keine SP-API-Credentials gefunden. Moegliche Loesungen:\n"
        "  1. Explizit uebergeben:   search_by_ean(ean, credentials={...})\n"
        "  2. Modul konfigurieren:   amazon_sp.configure({'refresh_token': ...})\n"
        "  3. Env-Var setzen:        AMZ_EINKAUF_CONFIG=/pfad/amz_einkauf_config.json\n"
        "  4. Datei ablegen:         <projekt>/amazon-vorqualifizierung/amz_einkauf_config.json\n"
        "     Format: { \"sp_api\": { \"refresh_token\": \"...\","
        " \"lwa_app_id\": \"...\", \"lwa_client_secret\": \"...\" } }"
    )
