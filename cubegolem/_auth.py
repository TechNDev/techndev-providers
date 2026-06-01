#!/usr/bin/env python3
"""
techndev-providers  cubegolem/_auth.py  v1.0.0
================================================
Session-Cookie-Handling fuer cubegolem.de.
Intern — nicht direkt importieren; oeffentliche Exporte via cubegolem/__init__.py.

Warum Cookie statt Login?
  Preise sind nur eingeloggt sichtbar. Aus Sicherheitsgruenden automatisiert
  der Provider KEINEN passwortbasierten Login. Der Consumer exportiert die
  Session-Cookie einmalig aus dem eingeloggten Browser (DevTools → Application
  → Cookies) und uebergibt sie an CubeGolemProvider(session_cookie=...).

Akzeptierte Cookie-Formate (normalize_cookie):
  - Roh-Header-String:  "PrestaShop-abc=...; other=..."
  - dict:               {"PrestaShop-abc": "...", ...}
  - Liste von Eintraegen aus Browser-Export:
                        [{"name": "...", "value": "..."}, ...]

CHANGELOG
---------
v1.0.0  (2026-05-30)
  - SessionExpiredError, normalize_cookie(), assert_logged_in().
"""
from __future__ import annotations

__version__ = "1.0.0"

# Marker im HTML, der eine NICHT-eingeloggte Session verraet
# (Shop blendet Preise hinter diesem Hinweis aus).
_LOGGED_OUT_MARKERS = (
    "logge dich ein, um Preise",
    "um Preise und Verf",          # "...Verfuegbarkeiten zu sehen"
)


class SessionExpiredError(RuntimeError):
    """Session-Cookie fehlt/ist abgelaufen — Preise nicht abrufbar."""


def normalize_cookie(cookie) -> str:
    """
    Bringt die verschiedenen Cookie-Eingabeformate auf einen Cookie-Header-String.
    Leere/ungueltige Eingaben ⇒ leerer String (Validierung erfolgt separat).
    """
    if not cookie:
        return ""
    if isinstance(cookie, str):
        return cookie.strip()
    if isinstance(cookie, dict):
        return "; ".join(f"{k}={v}" for k, v in cookie.items() if k)
    if isinstance(cookie, (list, tuple)):
        parts = []
        for c in cookie:
            if isinstance(c, dict) and c.get("name"):
                parts.append(f"{c['name']}={c.get('value', '')}")
        return "; ".join(parts)
    return str(cookie).strip()


def is_logged_out(html: str) -> bool:
    """True, wenn das HTML den 'bitte einloggen'-Hinweis enthaelt."""
    return any(m in html for m in _LOGGED_OUT_MARKERS)


def assert_logged_in(html: str) -> None:
    """
    Wirft SessionExpiredError, wenn die Antwort eine ausgeloggte Session zeigt.
    Aufrufer nutzen das nach dem ersten Request zur Frueh-Erkennung.
    """
    if is_logged_out(html):
        raise SessionExpiredError(
            "cubegolem-Session ausgeloggt/abgelaufen — Preise nicht sichtbar. "
            "Bitte Session-Cookie neu aus dem eingeloggten Browser exportieren."
        )
