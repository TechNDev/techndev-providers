#!/usr/bin/env python3
"""Tests fuer den cashback-Resolver-Layer (Stapeln/cap/valid_to/min_order/confidence/vat)."""
from cashback import (
    CashbackOffer, best_cashback, cashback_for, resolve_all,
)


# ── Resolver ──────────────────────────────────────────────────────────────────

def test_portal_resolver_match_and_miss():
    cfg = {"portal": {"galeria.de": 0.06}}
    assert len(resolve_all(merchant="https://www.galeria.de/x", ean=None, config=cfg)) >= 1
    assert resolve_all(merchant="unbekannt.de", config=cfg) == []


def test_card_applies_everywhere():
    offs = resolve_all(merchant="irgendwas.de", config={"card": 0.01})
    assert any(o.category == "card" and o.value == 0.01 for o in offs)


def test_manufacturer_matches_ean():
    cfg = {"manufacturer": [{"ean": "123", "value": 20.0, "kind": "fixed"}]}
    assert resolve_all(ean="123", config=cfg)[0].value == 20.0
    assert resolve_all(ean="999", config=cfg) == []


# ── best_cashback ─────────────────────────────────────────────────────────────

def _o(cat, kind, val, **kw):
    return CashbackOffer(merchant="*", kind=kind, value=val, category=cat, **kw)


def test_stacks_across_categories_with_confidence():
    offers = [_o("portal", "percent", 0.06, label="P"), _o("card", "percent", 0.01, label="C")]
    r = best_cashback(offers, 100.0, confidence=0.8)
    assert r.gross_netto == 7.0          # 6 + 1
    assert r.total_netto == 5.6          # * 0.8
    assert "P" in r.label and "C" in r.label


def test_best_per_category_wins():
    offers = [_o("portal", "percent", 0.03), _o("portal", "fixed", 10.0)]
    r = best_cashback(offers, 100.0, confidence=1.0)
    assert r.gross_netto == 10.0         # fixed 10 > 3 % von 100


def test_cap_limits_amount():
    r = best_cashback([_o("portal", "percent", 0.06, cap=20.0)], 1000.0, confidence=1.0)
    assert r.gross_netto == 20.0


def test_min_order_filters():
    r = best_cashback([_o("portal", "percent", 0.06, min_order=50.0)], 40.0, confidence=1.0)
    assert r.total_netto == 0.0


def test_valid_to_expired_filtered():
    r = best_cashback([_o("manufacturer", "fixed", 20.0, valid_to="2020-01-01")],
                      100.0, date="2026-06-05", confidence=1.0)
    assert r.total_netto == 0.0


def test_vat_link_converts_to_net():
    # Rechnungsrabatt 11.90 brutto -> netto 10.00 (/1.19)
    r = best_cashback([_o("manufacturer", "fixed", 11.90, vat_link=True)], 100.0, confidence=1.0)
    assert r.gross_netto == 10.0


def test_cashback_for_end_to_end():
    cfg = {"confidence": 1.0, "card": 0.01, "portal": {"galeria.de": 0.06},
           "manufacturer": [{"ean": "123", "value": 20.0, "kind": "fixed", "valid_to": "2099-12-31"}]}
    r = cashback_for(merchant="galeria.de", ean="123", order_brutto=100.0,
                     config=cfg, date="2026-06-05")
    assert r.total_netto == 27.0         # 6 + 1 + 20


def test_no_offers_zero():
    assert best_cashback([], 100.0).total_netto == 0.0
    assert cashback_for(merchant="x", order_brutto=50.0, config={}).total_netto == 0.0
