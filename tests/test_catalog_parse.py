#!/usr/bin/env python3
"""Parser-Tests fuer amazon_sp.catalog._parse_item — Kategorie-Extraktion.
Regression fuer den Bug "Kategorie immer leer" (classifications marktplatz-gruppiert
mit geschachtelter innerer Liste)."""
from amazon_sp.catalog import _parse_item

DE = "A1PA6795UKMFR9"


def _item(classifications=None, sales_ranks=None):
    return {
        "asin": "B0TEST123",
        "summaries": [{"marketplaceId": DE, "itemName": "Test LEGO Set", "brand": "LEGO"}],
        "classifications": classifications or [],
        "salesRanks": sales_ranks or [],
    }


def test_category_from_nested_classifications():
    # echte SP-API-Struktur: marktplatz-gruppiert -> innere classifications-Liste
    item = _item(classifications=[
        {"marketplaceId": DE,
         "classifications": [{"classificationId": "123", "displayName": "Konstruktionsspielzeug"}]},
    ])
    res = _parse_item(item, "4000000000000", DE)
    assert res.category == "Konstruktionsspielzeug"


def test_category_falls_back_to_bsr_display_title():
    # keine classifications -> PDP-Hauptkategorie aus displayGroupRank-Titel
    item = _item(sales_ranks=[
        {"marketplaceId": DE,
         "displayGroupRanks": [{"rank": 410923, "title": "Spielzeug"}],
         "classificationRanks": [{"rank": 5000, "title": "Baukloetze"}]},
    ])
    res = _parse_item(item, "4000000000000", DE)
    assert res.category == "Spielzeug"
    assert res.bsr == 410923


def test_category_falls_back_to_classification_rank_title():
    # nur classificationRanks -> dessen Titel
    item = _item(sales_ranks=[
        {"marketplaceId": DE, "classificationRanks": [{"rank": 5000, "title": "Modellbau"}]},
    ])
    res = _parse_item(item, "4000000000000", DE)
    assert res.category == "Modellbau"


def test_category_empty_when_no_data():
    res = _parse_item(_item(), "4000000000000", DE)
    assert res.category == ""


def test_classifications_prefers_matching_marketplace():
    item = _item(classifications=[
        {"marketplaceId": "OTHER", "classifications": [{"displayName": "Wrong"}]},
        {"marketplaceId": DE,      "classifications": [{"displayName": "Spielzeug & Spiele"}]},
    ])
    res = _parse_item(item, "4000000000000", DE)
    assert res.category == "Spielzeug & Spiele"
