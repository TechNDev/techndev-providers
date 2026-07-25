#!/usr/bin/env python3
"""
techndev-providers  ebay/inventory.py  v1.0.0
==============================================
eBay Sell Inventory API — Schreib-Pfad (Angebot anlegen + veroeffentlichen).
Endpoints (User-Token, SCOPE_INVENTORY):
  PUT  /sell/inventory/v1/inventory_item/{sku}      createOrReplaceInventoryItem
  POST /sell/inventory/v1/offer                      createOffer            → offerId
  POST /sell/inventory/v1/offer/{offerId}/publish    publishOffer           → listingId
Account-Helfer (User-Token, SCOPE_ACCOUNT):
  GET  /sell/account/v1/fulfillment_policy | payment_policy | return_policy
  GET  /sell/inventory/v1/location                                          → merchantLocationKey

Hinweis Signaturen: Digitale Signaturen (RFC-9421) sind laut eBay nur fuer die
Finances API, issueRefund (Fulfillment) und Trading GetAccount Pflicht — NICHT
fuer die Inventory API. Dieser Schreib-Pfad kommt ohne ebay_signature.py aus.

Voraussetzungen (operativ, siehe README des Consumers):
  - Keyset mit sell.inventory-Scope + User-Refresh-Token (Re-Auth).
  - Business Policies (Versand/Zahlung/Rueckgabe) + merchantLocationKey.

CHANGELOG
---------
v1.0.0  (2026-07-25)
  - build_inventory_item()/build_offer_payload(): reine Body-Builder aus
    EbayOfferDraft (netzfrei testbar).
  - create_or_replace_inventory_item()/create_offer()/publish_offer().
  - get_business_policies()/get_inventory_locations(): Account/Location-Helfer.
"""
from __future__ import annotations

import json

import requests

from ._auth import get_user_token, api_base, SCOPE_INVENTORY, SCOPE_ACCOUNT
from ._rate import _retry, inventory_limiter

__version__ = "1.0.0"

TIMEOUT = 30

# eBay-Marketplace → Content-Language-Header (Inventory-Item verlangt ihn).
_CONTENT_LANG = {
    "EBAY_DE": "de-DE", "EBAY_AT": "de-AT", "EBAY_FR": "fr-FR",
    "EBAY_IT": "it-IT", "EBAY_ES": "es-ES", "EBAY_NL": "nl-NL",
    "EBAY_BE": "nl-BE", "EBAY_GB": "en-GB",
}


# ══════════════════════════════════════════════════════════════════════════════
# Body-Builder — reine Funktionen (netzfrei testbar)
# ══════════════════════════════════════════════════════════════════════════════

def build_inventory_item(draft) -> dict:
    """EbayOfferDraft → Body fuer createOrReplaceInventoryItem."""
    product: dict = {
        "title":     draft.title,
        "aspects":   {k: list(v) for k, v in (draft.aspects or {}).items()},
    }
    if draft.description:
        product["description"] = draft.description
    if draft.images:
        product["imageUrls"] = list(draft.images)
    if draft.ean:
        product["ean"] = [draft.ean]
    if draft.brand:
        product["brand"] = draft.brand
    if draft.mpn:
        product["mpn"] = draft.mpn

    body: dict = {
        "availability": {"shipToLocationAvailability": {"quantity": max(1, int(draft.quantity or 1))}},
        "condition":    draft.condition or "NEW",
        "product":      product,
    }

    pkg = _package(draft)
    if pkg:
        body["packageWeightAndSize"] = pkg
    return body


def _package(draft) -> dict | None:
    pkg: dict = {}
    if draft.weight_kg:
        pkg["weight"] = {"value": float(draft.weight_kg), "unit": "KILOGRAM"}
    dims = {}
    if draft.length_cm: dims["length"] = float(draft.length_cm)
    if draft.width_cm:  dims["width"]  = float(draft.width_cm)
    if draft.height_cm: dims["height"] = float(draft.height_cm)
    if len(dims) == 3:
        dims["unit"] = "CENTIMETER"
        pkg["dimensions"] = dims
    return pkg or None


def build_offer_payload(draft, policies: dict, merchant_location_key: str) -> dict:
    """EbayOfferDraft (+ Policy-IDs + Location) → Body fuer createOffer."""
    body: dict = {
        "sku":               draft.sku,
        "marketplaceId":     draft.marketplace,
        "format":            "FIXED_PRICE",
        "availableQuantity": max(1, int(draft.quantity or 1)),
        "categoryId":        draft.category_id,
        "merchantLocationKey": merchant_location_key,
        "pricingSummary":    {"price": {"value": str(draft.price), "currency": draft.currency or "EUR"}},
    }
    if draft.description:
        body["listingDescription"] = draft.description
    lp = {}
    if policies.get("fulfillment"): lp["fulfillmentPolicyId"] = policies["fulfillment"]
    if policies.get("payment"):     lp["paymentPolicyId"]     = policies["payment"]
    if policies.get("return"):      lp["returnPolicyId"]      = policies["return"]
    if lp:
        body["listingPolicies"] = lp
    return body


# ══════════════════════════════════════════════════════════════════════════════
# Schreib-Aufrufe
# ══════════════════════════════════════════════════════════════════════════════

def _user_headers(creds: dict, scope: str, marketplace: str = "EBAY_DE") -> dict:
    token = get_user_token(creds["client_id"], creds["client_secret"],
                           creds["refresh_token"], scope=scope,
                           env=creds.get("env", "production"))
    h = {
        "Authorization": f"Bearer {token}",
        "Accept":        "application/json",
        "Content-Type":  "application/json",
    }
    if scope == SCOPE_INVENTORY:
        h["Content-Language"] = _CONTENT_LANG.get(marketplace, "de-DE")
    return h


@_retry
def create_or_replace_inventory_item(
    sku: str, item_body: dict, creds: dict, marketplace: str = "EBAY_DE",
) -> tuple[bool, str | None]:
    """PUT inventory_item/{sku}. Rueckgabe (ok, error). Erfolg = HTTP 200/204."""
    inventory_limiter.wait()
    url = f"{api_base(creds.get('env', 'production'))}/sell/inventory/v1/inventory_item/{sku}"
    try:
        resp = requests.put(url, headers=_user_headers(creds, SCOPE_INVENTORY, marketplace),
                            data=json.dumps(item_body), timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.HTTPError as e:
        return False, _err(e)
    except requests.RequestException as e:
        return False, f"Netzwerkfehler: {e}"
    return True, None


@_retry
def create_offer(offer_body: dict, creds: dict) -> tuple[str | None, str | None]:
    """POST offer. Rueckgabe (offerId, error)."""
    inventory_limiter.wait()
    url = f"{api_base(creds.get('env', 'production'))}/sell/inventory/v1/offer"
    try:
        resp = requests.post(url, headers=_user_headers(creds, SCOPE_INVENTORY,
                                                        offer_body.get("marketplaceId", "EBAY_DE")),
                            data=json.dumps(offer_body), timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.HTTPError as e:
        # 25002 = Angebot fuer SKU existiert bereits → offerId aus Fehlertext extrahieren waere moeglich.
        return None, _err(e)
    except requests.RequestException as e:
        return None, f"Netzwerkfehler: {e}"
    return (resp.json().get("offerId") or None), None


@_retry
def publish_offer(offer_id: str, creds: dict) -> tuple[str | None, str | None]:
    """POST offer/{offerId}/publish. Rueckgabe (listingId, error)."""
    inventory_limiter.wait()
    url = f"{api_base(creds.get('env', 'production'))}/sell/inventory/v1/offer/{offer_id}/publish"
    try:
        resp = requests.post(url, headers=_user_headers(creds, SCOPE_INVENTORY),
                            timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.HTTPError as e:
        return None, _err(e)
    except requests.RequestException as e:
        return None, f"Netzwerkfehler: {e}"
    return (resp.json().get("listingId") or None), None


@_retry
def withdraw_offer(offer_id: str, creds: dict) -> tuple[bool, str | None]:
    """POST offer/{offerId}/withdraw — beendet das veroeffentlichte Listing
    (Offer bleibt als Entwurf bestehen). Rueckgabe (ok, error)."""
    inventory_limiter.wait()
    url = f"{api_base(creds.get('env', 'production'))}/sell/inventory/v1/offer/{offer_id}/withdraw"
    try:
        resp = requests.post(url, headers=_user_headers(creds, SCOPE_INVENTORY), timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.HTTPError as e:
        return False, _err(e)
    except requests.RequestException as e:
        return False, f"Netzwerkfehler: {e}"
    return True, None


@_retry
def delete_offer(offer_id: str, creds: dict) -> tuple[bool, str | None]:
    """DELETE offer/{offerId} — loescht den Offer-Entwurf endgueltig. (ok, error)."""
    inventory_limiter.wait()
    url = f"{api_base(creds.get('env', 'production'))}/sell/inventory/v1/offer/{offer_id}"
    try:
        resp = requests.delete(url, headers=_user_headers(creds, SCOPE_INVENTORY), timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.HTTPError as e:
        return False, _err(e)
    except requests.RequestException as e:
        return False, f"Netzwerkfehler: {e}"
    return True, None


@_retry
def delete_inventory_item(sku: str, creds: dict) -> tuple[bool, str | None]:
    """DELETE inventory_item/{sku} — entfernt den Inventory-Eintrag. (ok, error)."""
    inventory_limiter.wait()
    url = f"{api_base(creds.get('env', 'production'))}/sell/inventory/v1/inventory_item/{sku}"
    try:
        resp = requests.delete(url, headers=_user_headers(creds, SCOPE_INVENTORY), timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.HTTPError as e:
        return False, _err(e)
    except requests.RequestException as e:
        return False, f"Netzwerkfehler: {e}"
    return True, None


# ══════════════════════════════════════════════════════════════════════════════
# Account/Location-Helfer (Voraussetzungen fuer createOffer)
# ══════════════════════════════════════════════════════════════════════════════

@_retry
def get_business_policies(creds: dict, marketplace: str = "EBAY_DE") -> dict:
    """{'fulfillment': id, 'payment': id, 'return': id} — je erste Policy des
    Marketplace. Leere Werte, wenn keine gepflegt/kein Zugang. error unter '_error'."""
    base = api_base(creds.get("env", "production"))
    out: dict = {"fulfillment": None, "payment": None, "return": None}
    endpoints = {
        "fulfillment": ("fulfillment_policy", "fulfillmentPolicies", "fulfillmentPolicyId"),
        "payment":     ("payment_policy",     "paymentPolicies",     "paymentPolicyId"),
        "return":      ("return_policy",      "returnPolicies",      "returnPolicyId"),
    }
    try:
        headers = _user_headers(creds, SCOPE_ACCOUNT, marketplace)
    except Exception as e:                               # noqa: BLE001
        out["_error"] = f"Token-Fehler: {e}"
        return out
    for key, (path, list_field, id_field) in endpoints.items():
        inventory_limiter.wait()
        url = f"{base}/sell/account/v1/{path}"
        try:
            resp = requests.get(url, headers=headers, params={"marketplace_id": marketplace},
                                timeout=TIMEOUT)
            resp.raise_for_status()
            items = resp.json().get(list_field) or []
            if items:
                out[key] = items[0].get(id_field)
        except requests.RequestException as e:
            out["_error"] = _err(e) if isinstance(e, requests.HTTPError) else str(e)
    return out


@_retry
def get_inventory_locations(creds: dict) -> tuple[list[dict], str | None]:
    """Liste der Inventory-Locations (merchantLocationKey). (locations, error)."""
    inventory_limiter.wait()
    url = f"{api_base(creds.get('env', 'production'))}/sell/inventory/v1/location"
    try:
        resp = requests.get(url, headers=_user_headers(creds, SCOPE_INVENTORY), timeout=TIMEOUT)
        resp.raise_for_status()
    except requests.HTTPError as e:
        return [], _err(e)
    except requests.RequestException as e:
        return [], f"Netzwerkfehler: {e}"
    return (resp.json().get("locations") or []), None


def _err(e: requests.HTTPError) -> str:
    """Kompakte eBay-Fehlermeldung (HTTP-Code + erste API-Fehlerbeschreibung)."""
    code = e.response.status_code if e.response is not None else "?"
    detail = ""
    try:
        errs = e.response.json().get("errors") or []
        if errs:
            detail = " · " + (errs[0].get("message") or errs[0].get("longMessage") or "")
    except Exception:                                    # noqa: BLE001
        pass
    return f"HTTP {code}{detail}"
