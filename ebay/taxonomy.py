#!/usr/bin/env python3
"""
techndev-providers  ebay/taxonomy.py  v1.0.0
=============================================
eBay Taxonomy API — Kategorie-Vorschlaege + Pflicht-Item-Specifics.
Endpoints (App-Token, SCOPE_BASIC genuegt — kein Business-Approval):
  GET /commerce/taxonomy/v1/get_default_category_tree_id?marketplace_id=<mp>
  GET /commerce/taxonomy/v1/category_tree/{treeId}/get_category_suggestions?q=<text>
  GET /commerce/taxonomy/v1/category_tree/{treeId}/get_item_aspects_for_category?category_id=<id>

Rolle im Listing-Workflow: liefert (a) die passende eBay-Leaf-Kategorie zu einem
Titel und (b) welche Item-Specifics diese Kategorie *verlangt* bzw. empfiehlt.
Das ist die verlaessliche Basis — unabhaengig davon, ob die Commerce Catalog API
fuer das Keyset freigeschaltet ist.

CHANGELOG
---------
v1.0.0  (2026-07-25)
  - get_default_category_tree_id(): Tree-ID je Marketplace, modulweit gecacht.
  - get_category_suggestions(): Titel → Liste passender Kategorien (Leaf + Pfad).
  - suggest_category_id(): bequemer Top-Treffer (Leaf-categoryId oder None).
  - get_item_aspects(): categoryId → list[AspectRequirement] (required/mode/values).
"""
from __future__ import annotations

from threading import Lock

import requests

from ._auth   import get_token, api_base, SCOPE_TAXONOMY
from ._models import AspectRequirement
from ._rate   import _retry, taxonomy_limiter

__version__ = "1.0.0"

TIMEOUT = 30

# Marketplace → categoryTreeId ist konstant; modulweit cachen (spart einen Call je Lauf).
_tree_cache: dict[tuple[str, str], str] = {}   # (marketplace, env) → treeId
_tree_lock = Lock()


def _app_token(credentials: dict) -> str:
    return get_token(
        credentials["client_id"],
        credentials["client_secret"],
        scope = SCOPE_TAXONOMY,
        env   = credentials.get("env", "production"),
    )


def _headers(token: str, marketplace: str) -> dict:
    return {
        "Authorization":           f"Bearer {token}",
        "X-EBAY-C-MARKETPLACE-ID": marketplace,
        "Accept":                  "application/json",
    }


@_retry
def get_default_category_tree_id(
    credentials: dict,
    marketplace: str = "EBAY_DE",
) -> str | None:
    """Die categoryTreeId fuer einen Marketplace (EBAY_DE → '77'). None bei Fehler."""
    env = credentials.get("env", "production")
    key = (marketplace, env)
    with _tree_lock:
        cached = _tree_cache.get(key)
    if cached:
        return cached

    try:
        token = _app_token(credentials)
    except Exception:                                    # noqa: BLE001
        return None

    taxonomy_limiter.wait()
    url = f"{api_base(env)}/commerce/taxonomy/v1/get_default_category_tree_id"
    try:
        resp = requests.get(url, headers=_headers(token, marketplace),
                            params={"marketplace_id": marketplace}, timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException:
        return None

    tree_id = str(resp.json().get("categoryTreeId") or "").strip() or None
    if tree_id:
        with _tree_lock:
            _tree_cache[key] = tree_id
    return tree_id


@_retry
def get_category_suggestions(
    query:       str,
    credentials: dict,
    marketplace: str = "EBAY_DE",
) -> list[dict]:
    """
    Kategorie-Vorschlaege zu einem Freitext (i.d.R. dem Angebotstitel).

    Rueckgabe: Liste (bester Treffer zuerst) von
      {category_id, category_name, is_leaf, ancestors: [{id, name}, ...]}
    Leere Liste bei Fehler oder ohne Treffer.
    """
    tree_id = get_default_category_tree_id(credentials, marketplace)
    if not tree_id or not query.strip():
        return []

    try:
        token = _app_token(credentials)
    except Exception:                                    # noqa: BLE001
        return []

    taxonomy_limiter.wait()
    url = (f"{api_base(credentials.get('env', 'production'))}"
           f"/commerce/taxonomy/v1/category_tree/{tree_id}/get_category_suggestions")
    try:
        resp = requests.get(url, headers=_headers(token, marketplace),
                            params={"q": query}, timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException:
        return []

    out: list[dict] = []
    for sug in resp.json().get("categorySuggestions") or []:
        cat = sug.get("category") or {}
        ancestors = [
            {"id": str(a.get("categoryId") or ""), "name": a.get("categoryName") or ""}
            for a in (sug.get("categoryTreeNodeAncestors") or [])
        ]
        out.append({
            "category_id":   str(cat.get("categoryId") or ""),
            "category_name": cat.get("categoryName") or "",
            "is_leaf":       True,   # get_category_suggestions liefert stets Leaf-Kategorien
            "ancestors":     ancestors,
        })
    return out


def suggest_category_id(
    query:       str,
    credentials: dict,
    marketplace: str = "EBAY_DE",
) -> str | None:
    """Bequemer Top-Treffer: die wahrscheinlichste Leaf-categoryId (oder None)."""
    sugs = get_category_suggestions(query, credentials, marketplace)
    if not sugs:
        return None
    cid = sugs[0].get("category_id") or ""
    return cid or None


@_retry
def get_item_aspects(
    category_id: str,
    credentials: dict,
    marketplace: str = "EBAY_DE",
) -> list[AspectRequirement]:
    """
    Pflicht/empfohlene Item-Specifics einer Leaf-Kategorie (getItemAspectsForCategory).

    Rueckgabe: list[AspectRequirement] (leer bei Fehler). required=True markiert
    Felder, ohne die eBay das Angebot ablehnt.
    """
    tree_id = get_default_category_tree_id(credentials, marketplace)
    if not tree_id or not str(category_id).strip():
        return []

    try:
        token = _app_token(credentials)
    except Exception:                                    # noqa: BLE001
        return []

    taxonomy_limiter.wait()
    url = (f"{api_base(credentials.get('env', 'production'))}"
           f"/commerce/taxonomy/v1/category_tree/{tree_id}/get_item_aspects_for_category")
    try:
        resp = requests.get(url, headers=_headers(token, marketplace),
                            params={"category_id": str(category_id)}, timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException:
        return []

    return _parse_aspects(resp.json().get("aspects") or [])


def _parse_aspects(raw_aspects: list[dict]) -> list[AspectRequirement]:
    """Taxonomy-Aspect-JSON → list[AspectRequirement]."""
    out: list[AspectRequirement] = []
    for asp in raw_aspects:
        name = str(asp.get("localizedAspectName") or "").strip()
        if not name:
            continue
        con  = asp.get("aspectConstraint") or {}
        card = str(con.get("itemToAspectCardinality") or "SINGLE").upper()
        mode = str(con.get("aspectMode") or "FREE_TEXT").upper()
        values = [
            str(v.get("localizedValue") or "").strip()
            for v in (asp.get("aspectValues") or [])
            if str(v.get("localizedValue") or "").strip()
        ]
        out.append(AspectRequirement(
            name        = name,
            required    = bool(con.get("aspectRequired", False)),
            cardinality = "MULTI" if "MULTI" in card else "SINGLE",
            mode        = "SELECTION_ONLY" if "SELECTION" in mode else "FREE_TEXT",
            values      = values,
        ))
    return out
