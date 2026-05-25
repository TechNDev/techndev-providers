#!/usr/bin/env python3
"""
amazon_sp  _helpers.py  v1.0.0
================================
Marktplatz-Tabellen, Einheitenumrechnung und Bild-Utilities.
Intern — nicht direkt importieren.
"""
import re

from sp_api.base import Marketplaces

__version__ = "1.0.0"

# ── Marktplatz-Tabellen ────────────────────────────────────────────────────────

_MARKETPLACE_MAP: dict[str, Marketplaces] = {
    'DE': Marketplaces.DE,
    'FR': Marketplaces.FR,
    'IT': Marketplaces.IT,
    'ES': Marketplaces.ES,
    'UK': Marketplaces.UK,
}

_MARKETPLACE_IDS: dict[str, str] = {
    'DE': 'A1PA6795UKMFR9',
    'FR': 'A13V1IB3VIYZZH',
    'IT': 'APJ6JRA9NG5V4',
    'ES': 'A1RKKUPIHCS9HS',
    'UK': 'A1F83G8C2ARO7P',
}

# Bekannte Amazon-Eigenhaendler-IDs je Marktplatz
_AMAZON_SELLER_IDS: dict[str, str] = {
    'DE': 'A3JWKAKR8XB7XF',
    'FR': 'A1X6FK5RDHNB96',
    'IT': 'A11IL2PNWYJU7H',
    'ES': 'A1AT7YVPFBWXBL',
    'UK': 'A3P5ROKL5A1OLE',
}


def get_marketplace(code: str) -> Marketplaces:
    """'DE' -> Marketplaces.DE, Fallback: DE."""
    return _MARKETPLACE_MAP.get(code.upper(), Marketplaces.DE)


def get_marketplace_id(code: str) -> str:
    """'DE' -> 'A1PA6795UKMFR9', Fallback: DE."""
    return _MARKETPLACE_IDS.get(code.upper(), _MARKETPLACE_IDS['DE'])


def get_amazon_seller_id(code: str) -> str:
    """Bekannte Amazon-Eigenhaendler-ID fuer den jeweiligen Marktplatz."""
    return _AMAZON_SELLER_IDS.get(code.upper(), _AMAZON_SELLER_IDS['DE'])


# ── Einheitenumrechnung (Amazon -> metrisch) ──────────────────────────────────

def _to_cm(value, unit: str) -> float:
    """Beliebige Laengeneinheit -> Zentimeter."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    u = unit.lower()
    if u in ('centimeters', 'centimeter', 'cm'):  return v
    if u in ('millimeters', 'millimeter', 'mm'):  return v / 10
    if u in ('meters',      'meter',      'm'):   return v * 100
    if u in ('inches',      'inch',       'in'):  return v * 2.54
    if u in ('feet',        'foot',       'ft'):  return v * 30.48
    return v   # unbekannte Einheit -> Wert unveraendert


def _to_kg(value, unit: str) -> float:
    """Beliebige Gewichtseinheit -> Kilogramm."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    u = unit.lower()
    if u in ('kilograms', 'kilogram', 'kg'):         return v
    if u in ('grams',     'gram',     'g'):           return v / 1000
    if u in ('pounds',    'pound',    'lb', 'lbs'):   return v * 0.453592
    if u in ('ounces',    'ounce',    'oz'):           return v * 0.028350
    return v


# ── Amazon-Bild-Utilities ─────────────────────────────────────────────────────

def _is_full_size_image(url: str) -> bool:
    """False fuer Amazon-Thumbnails kleiner als 300 px (_SL<N>_-Suffix)."""
    m = re.search(r'_SL(\d+)_', url)
    if m:
        return int(m.group(1)) >= 300
    return True   # kein Groessen-Suffix = Originalbild


def _img_base_id(url: str) -> str:
    """Deduplizierungs-Schluessel: '.../I/71abc._AC_SL1500_.jpg' -> '71abc'."""
    m = re.search(r'/images/I/([^._/]+)', url)
    return m.group(1) if m else url


def _img_resolution(url: str) -> int:
    """Aufloesung aus _SL<N>_-Suffix; Originale ohne Suffix -> 9999 (bevorzugt)."""
    m = re.search(r'_SL(\d+)_', url)
    return int(m.group(1)) if m else 9999


def collect_amazon_images(img_sets: list) -> list[str]:
    """
    Sammelt unique Vollbild-URLs aus Amazon SP-API img_sets.
    Dedupliziert nach Basis-ID (_SL-Suffix ignoriert), behaelt jeweils
    die hoechste verfuegbare Aufloesung.
    """
    best: dict[str, tuple[int, str]] = {}   # base_id -> (aufloesung, url)
    for img_set in img_sets:
        for img in img_set.get('images', []):
            link = img.get('link', '')
            if not link or not _is_full_size_image(link):
                continue
            base = _img_base_id(link)
            res  = _img_resolution(link)
            if base not in best or res > best[base][0]:
                best[base] = (res, link)

    result = [url for _, url in best.values()]
    if not result:
        # Fallback: ohne Groessenfilter, weiterhin nach Basis-ID dedupliziert
        best2: dict[str, tuple[int, str]] = {}
        for img_set in img_sets:
            for img in img_set.get('images', []):
                link = img.get('link', '')
                if not link:
                    continue
                base = _img_base_id(link)
                res  = _img_resolution(link)
                if base not in best2 or res > best2[base][0]:
                    best2[base] = (res, link)
        result = [url for _, url in best2.values()]
    return result
