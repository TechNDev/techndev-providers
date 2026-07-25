#!/usr/bin/env python3
"""
techndev-providers  ebay/_auth.py  v1.1.0
==========================================
eBay OAuth 2.0 — Application Token + User Token.
Thread-safe Token-Cache pro (client_id, scope)-Paar.

CHANGELOG
---------
v1.2.0  (2026-07-25)
  - SCOPE_CATALOG:   commerce.catalog.readonly fuer Commerce Catalog API
                     (Produktsuche per GTIN → ProductSummary, App-Token).
  - SCOPE_TAXONOMY:  api_scope (Basic) genuegt fuer Taxonomy API — Alias fuer Klarheit.
  - SCOPE_INVENTORY: sell.inventory fuer Sell Inventory API (Schreib-Pfad, User-Token).
  - SCOPE_ACCOUNT:   sell.account.readonly fuer Business-Policies/Location (User-Token).

v1.1.0  (2026-05-28)
  - SCOPE_ANALYTICS: sell.analytics.readonly fuer Sell Analytics API.
  - get_user_token(): User Token per Refresh-Token-Grant (Authorization Code Flow).
    Cachet separat von Application-Tokens (key: (client_id, scope, 'user')).
  - make_oauth_url(): Erzeugt Consent-URL fuer erstmaligen User-Token-Abruf.

v1.0.0  (2026-05-25)
  - get_token(): Application Token per Client Credentials.
    Cached bis 60 Sekunden vor Ablauf (konservativ).
    Thread-safe via threading.Lock.
  - api_base(): Production/Sandbox-URL-Router.
  - is_gtin(): EAN/GTIN-Erkennung fuer API-Parameter-Auswahl.
"""
from __future__ import annotations

import base64
import time
from threading import Lock

import requests

__version__ = "1.2.0"

# ── Scopes ────────────────────────────────────────────────────────────────────
SCOPE_BASIC      = "https://api.ebay.com/oauth/api_scope"
SCOPE_SOLD       = "https://api.ebay.com/oauth/api_scope/buy.marketplace.insights"
SCOPE_ANALYTICS  = "https://api.ebay.com/oauth/api_scope/sell.analytics.readonly"
# Taxonomy API kommt mit dem Basic-Scope aus (Kategorie-Baum + Item-Aspects).
SCOPE_TAXONOMY   = SCOPE_BASIC
# Commerce Catalog API: fuer Buying-Apps genuegt commerce.catalog.readonly (App-Token).
SCOPE_CATALOG    = "https://api.ebay.com/oauth/api_scope/commerce.catalog.readonly"
# Sell Inventory API (Schreib-Pfad) + Account (Policies/Location) — beide User-Token.
SCOPE_INVENTORY  = "https://api.ebay.com/oauth/api_scope/sell.inventory"
SCOPE_ACCOUNT    = "https://api.ebay.com/oauth/api_scope/sell.account.readonly"

# ── Token-Cache ───────────────────────────────────────────────────────────────
# key: (client_id, scope)  →  value: (access_token, expires_at: float monotonic)
_token_cache: dict[tuple[str, str], tuple[str, float]] = {}
_cache_lock   = Lock()
_EXPIRY_BUFFER = 60   # Token 60s vor echtem Ablauf als abgelaufen betrachten


def api_base(env: str = "production") -> str:
    """eBay API-Basis-URL fuer production oder sandbox."""
    if env.lower() == "sandbox":
        return "https://api.sandbox.ebay.com"
    return "https://api.ebay.com"


def is_gtin(value: str) -> bool:
    """True wenn value eine reine Ziffernfolge der Laenge 8/12/13/14 ist (EAN/GTIN)."""
    return value.isdigit() and len(value) in {8, 12, 13, 14}


def get_token(
    client_id:     str,
    client_secret: str,
    scope:         str = SCOPE_BASIC,
    env:           str = "production",
) -> str:
    """
    Gibt einen gueltigen Application Token fuer den angegebenen Scope zurueck.
    Holt einen neuen Token per Client Credentials wenn Cache abgelaufen/leer.

    Raises requests.HTTPError bei Auth-Fehler (401/403).
    """
    cache_key = (client_id, scope)
    now = time.monotonic()

    with _cache_lock:
        entry = _token_cache.get(cache_key)
        if entry and entry[1] > now:
            return entry[0]

    # Neuen Token anfordern (ausserhalb des Lock um Blocking zu minimieren)
    token, expires_in = _fetch_token(client_id, client_secret, scope, env)
    expires_at = time.monotonic() + max(0, expires_in - _EXPIRY_BUFFER)

    with _cache_lock:
        _token_cache[cache_key] = (token, expires_at)

    return token


def _fetch_token(
    client_id:     str,
    client_secret: str,
    scope:         str,
    env:           str,
) -> tuple[str, int]:
    """Holt einen frischen Token. Gibt (access_token, expires_in_seconds) zurueck."""
    url   = f"{api_base(env)}/identity/v1/oauth2/token"
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode("ascii")

    resp = requests.post(
        url,
        headers={
            "Content-Type":  "application/x-www-form-urlencoded",
            "Authorization": f"Basic {basic}",
        },
        data={
            "grant_type": "client_credentials",
            "scope":      scope,
        },
        timeout=25,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["access_token"], int(data.get("expires_in", 7200))


# ── User Token (Authorization Code / Refresh Token Flow) ──────────────────────

def get_user_token(
    client_id:     str,
    client_secret: str,
    refresh_token: str,
    scope:         str = SCOPE_ANALYTICS,
    env:           str = "production",
) -> str:
    """
    Gibt einen gueltigen User Access Token zurueck (Refresh-Token-Grant).
    Benoetigt: refresh_token aus OAuth Authorization Code Flow.

    Credentials-Format fuer User-APIs:
      {
        'client_id':     '...',
        'client_secret': '...',
        'refresh_token': '...',   # aus eBay OAuth Consent Flow
        'env':           'production',
      }

    Raises requests.HTTPError bei Auth-Fehler.
    """
    cache_key = (client_id, scope, "user")
    now = time.monotonic()

    with _cache_lock:
        entry = _token_cache.get(cache_key)
        if entry and entry[1] > now:
            return entry[0]

    token, expires_in = _fetch_user_token(client_id, client_secret, refresh_token, scope, env)
    expires_at = time.monotonic() + max(0, expires_in - _EXPIRY_BUFFER)

    with _cache_lock:
        _token_cache[cache_key] = (token, expires_at)

    return token


def _fetch_user_token(
    client_id:     str,
    client_secret: str,
    refresh_token: str,
    scope:         str,
    env:           str,
) -> tuple[str, int]:
    """Tauscht Refresh-Token gegen neuen Access-Token."""
    url   = f"{api_base(env)}/identity/v1/oauth2/token"
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode("ascii")

    resp = requests.post(
        url,
        headers={
            "Content-Type":  "application/x-www-form-urlencoded",
            "Authorization": f"Basic {basic}",
        },
        data={
            "grant_type":    "refresh_token",
            "refresh_token": refresh_token,
            "scope":         scope,
        },
        timeout=25,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["access_token"], int(data.get("expires_in", 7200))


def make_oauth_url(
    client_id:    str,
    ru_name:      str,
    scopes:       list[str] | None = None,
    env:          str = "production",
) -> str:
    """
    Erzeugt die eBay OAuth Consent-URL fuer den Authorization Code Flow.
    Der User muss diese URL im Browser oeffnen, sich einloggen und bestaetigen.
    eBay leitet danach auf ru_name (RuName aus eBay Developer Portal) weiter
    mit ?code=<auth_code> → diesen Code gegen Tokens tauschen.

    ru_name: RuName aus dem eBay Developer Portal (App-Einstellungen → Auth Accepted URL).
    scopes:  Liste von Scopes (Default: [SCOPE_ANALYTICS]).
    """
    import urllib.parse
    if scopes is None:
        scopes = [SCOPE_ANALYTICS]
    base = (
        "https://auth.sandbox.ebay.com/oauth2/authorize"
        if env.lower() == "sandbox"
        else "https://auth.ebay.com/oauth2/authorize"
    )
    params = {
        "client_id":     client_id,
        "response_type": "code",
        "redirect_uri":  ru_name,
        "scope":         " ".join(scopes),
    }
    return f"{base}?{urllib.parse.urlencode(params)}"
