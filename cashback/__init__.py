#!/usr/bin/env python3
"""
cashback — Cashback-Layer (Portale + Hersteller + Karte)
========================================================
Quer ueber alle Preisquellen liegender Resolver-Layer (analog sources/). Ermittelt je
Merchant/EAN den anwendbaren Cashback und liefert einen NETTO-Wert fuer die ANZEIGE
(reseller-profitability.annotate_cashback) — beeinflusst NICHT die Gates.

Schnellstart (Consumer):
    from cashback import cashback_for, load_config
    cfg = load_config("cashback_config.json")
    r = cashback_for(merchant="galeria", ean="5702017153292",
                     order_brutto=89.99, config=cfg)
    # r.total_netto -> an annotate_cashback(result, r.total_netto)

Stapel-Logik: bestes Angebot je Kategorie (portal/card/manufacturer), dann SUMME ueber
Kategorien (verschiedene Zahler stapeln), zentraler Confidence-Haircut (Tracking-Risiko).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date as _date
from pathlib import Path
from typing import Optional

from .base import CashbackOffer, CashbackResolver, _norm_merchant
from .portal import PortalResolver
from .card import CardResolver
from .manufacturer import ManufacturerResolver

__all__ = [
    "CashbackOffer", "CashbackResolver", "CashbackResult",
    "REGISTRY", "resolve_all", "best_cashback", "cashback_for",
    "load_config", "config_template", "CASHBACK_CONFIDENCE",
]

__version__ = "1.0.0"

# Default-Confidence: nur ein Teil des Nominal-Cashbacks geht in die Anzeige ein
# (Tracking-Fail / spaete Auszahlung). Per Config 'confidence' uebersteuerbar.
CASHBACK_CONFIDENCE = 0.8

REGISTRY: list[CashbackResolver] = [PortalResolver(), CardResolver(), ManufacturerResolver()]


@dataclass
class CashbackResult:
    total_netto: float = 0.0                 # nach Confidence-Haircut -> an annotate_cashback
    gross_netto: float = 0.0                 # vor Confidence
    confidence:  float = CASHBACK_CONFIDENCE
    applied:     list[CashbackOffer] = field(default_factory=list)
    label:       str = ""                    # z.B. "Portal 6 % + Karte 1 %"


def resolve_all(merchant: str = "", ean: Optional[str] = None,
                config: Optional[dict] = None) -> list[CashbackOffer]:
    """Alle anwendbaren Angebote aus allen Resolvern (ungefiltert nach Gueltigkeit)."""
    offers: list[CashbackOffer] = []
    for r in REGISTRY:
        try:
            offers.extend(r.resolve(merchant=merchant, ean=ean, config=config))
        except Exception:                    # noqa: BLE001  (ein Resolver darf andere nicht kippen)
            continue
    return offers


def _amount_netto(o: CashbackOffer, order_brutto: float, mwst_rate: float) -> float:
    amount = (o.value * order_brutto) if o.kind == "percent" else float(o.value)
    if o.cap is not None:
        amount = min(amount, float(o.cap))
    # vat_link=True (Rechnungsrabatt) -> Netto-EK sinkt um amount/1.19 (weniger Vorsteuer);
    # False (Portal/Karte, 3rd party Cash) -> voller Betrag zaehlt netto.
    return round(amount / (1 + mwst_rate) if o.vat_link else amount, 2)


def best_cashback(offers: list[CashbackOffer], order_brutto: float, *,
                  date: Optional[str] = None, confidence: float = CASHBACK_CONFIDENCE,
                  mwst_rate: float = 0.19) -> CashbackResult:
    """Waehlt das beste Angebot je Kategorie, summiert ueber Kategorien, wendet den
    Confidence-Haircut an. Respektiert valid_to / min_order / cap."""
    today = date or _date.today().isoformat()
    best_per_cat: dict[str, tuple[float, CashbackOffer]] = {}
    for o in offers:
        if o.valid_to and str(o.valid_to) < today:
            continue                          # abgelaufen
        if o.min_order is not None and order_brutto < float(o.min_order):
            continue
        net = _amount_netto(o, order_brutto, mwst_rate)
        if net <= 0:
            continue
        cur = best_per_cat.get(o.category)
        if cur is None or net > cur[0]:
            best_per_cat[o.category] = (net, o)

    if not best_per_cat:
        return CashbackResult(confidence=confidence)

    picked = [v[1] for v in best_per_cat.values()]
    gross = round(sum(v[0] for v in best_per_cat.values()), 2)
    total = round(gross * confidence, 2)
    label = " + ".join(o.label for o in picked if o.label)
    return CashbackResult(total_netto=total, gross_netto=gross, confidence=confidence,
                          applied=picked, label=label)


def cashback_for(*, merchant: str = "", ean: Optional[str] = None, order_brutto: float,
                 config: Optional[dict] = None, date: Optional[str] = None,
                 mwst_rate: float = 0.19) -> CashbackResult:
    """Bequemlichkeit: resolve_all + best_cashback. confidence aus config['confidence']."""
    cfg = config or {}
    offers = resolve_all(merchant=merchant, ean=ean, config=cfg)
    conf = float(cfg.get("confidence", CASHBACK_CONFIDENCE))
    return best_cashback(offers, order_brutto, date=date, confidence=conf, mwst_rate=mwst_rate)


def config_template() -> dict:
    """Referenz fuer cashback_config.json (gitignored, *_config.json-Pattern)."""
    return {
        "confidence": 0.8,
        "card": 0.01,                                   # 1 % Kreditkarten-Cashback ueberall
        "portal": {                                      # Shop-Domain/-Slug -> Satz
            "galeria.de": 0.06,
            "example-shop.de": 0.05,
        },
        "manufacturer": [                                # Hersteller-Aktionen je EAN
            {"ean": "5702017153292", "value": 20.0, "kind": "fixed",
             "valid_to": "2026-12-31", "label": "LEGO Aktion 20 EUR"},
        ],
    }


def load_config(path) -> dict:
    """Laedt cashback_config.json; fehlt sie -> {} (Cashback dann inaktiv)."""
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
