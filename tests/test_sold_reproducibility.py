#!/usr/bin/env python3
"""
Regressionstests fuer die eBay-Sold-Reproduzierbarkeit (ebay v2.1.0 / scraper v1.4.0).

Hintergrund: median_sold schwankte zwischen zwei Scrapes desselben Artikels massiv
(Titanic 10294: +655 EUR vs -270 EUR Marge) trotz stabilem sold_count. Ursachen:
Best-Match-Rotation, zu breite Titel-Query (Fehl-Matches), fehlendes Robust-Trimmen,
und als Sold geparste aktive/verwandte Auffuell-Listings. Diese Tests sichern die
vier Bausteine der Loesung ab.

Die OFFLINE-Tests laufen immer (deterministisch, kein Netz). Der LIVE-Test
(mehrfacher echter Scrape, drift-Pruefung) ist per Default uebersprungen; aktivieren
mit  EBAY_LIVE=1 pytest tests/test_sold_reproducibility.py
"""
import os
import statistics

import pytest

from ebay._models import _robust_trim, _price_stats
from ebay.scraper import _is_relevant, _significant_numbers, _parse_items


# ══════════════════════════════════════════════════════════════════════════════
# _robust_trim — Ausreisser-/Cluster-Robustheit
# ══════════════════════════════════════════════════════════════════════════════

def test_robust_trim_removes_cent_junk():
    # dichter Set-Cluster ~530-570 + Zubehoer-Cent-Junk -> Junk faellt, Median bleibt im Cluster
    prices = [1.59, 4.5, 4.66, 530, 540, 550, 560, 570, 565, 555, 545]
    trimmed = _robust_trim(prices)
    assert 1.59 not in trimmed and 4.5 not in trimmed and 4.66 not in trimmed
    assert 520 <= statistics.median(trimmed) <= 575


def test_robust_trim_is_order_independent():
    # Reproduzierbarkeit: Ergebnis haengt NUR von der Preismenge ab, nicht von der
    # Reihenfolge (in der eBay die Treffer liefert). Das ist der Kern des Fixes.
    a = [1.59, 297, 530, 560, 570, 545, 555, 4.5, 934.99]
    b = list(reversed(a))
    assert _robust_trim(a) == _robust_trim(b)


def test_robust_trim_no_trim_below_min_keep():
    # Zu wenig Datenpunkte -> keine Bandbreite -> unveraendert (nur sortiert)
    assert _robust_trim([10.0, 500.0]) == [10.0, 500.0]


def test_robust_trim_stable_cluster_untouched():
    prices = [60.0, 61.0, 62.0, 63.0, 64.0, 65.0]
    assert _robust_trim(prices) == sorted(prices)


def test_price_stats_over_trimmed_matches_expectation():
    prices = [1.0, 300, 305, 310, 315, 320, 2000]
    med, mean, mn, mx = _price_stats(_robust_trim(prices))
    assert 300 <= med <= 320
    assert mn >= 150 and mx <= 640   # 1.0 und 2000 sind raus


# ══════════════════════════════════════════════════════════════════════════════
# _significant_numbers / _is_relevant — Fehl-Match-Filter
# ══════════════════════════════════════════════════════════════════════════════

def test_significant_numbers_picks_set_number():
    assert _significant_numbers("LEGO Titanic 10294") == ["10294"]


def test_significant_numbers_excludes_ean():
    # 13-stellige EAN ist KEIN Titel-Pflichttoken (eBay matcht GTIN katalogseitig)
    assert _significant_numbers("4013575044542") == []


def test_relevant_matches_correct_set():
    assert _is_relevant("LEGO Icons 10294 Titanic 9090 Teile NEU", "LEGO Titanic 10294") is True


def test_relevant_rejects_wrong_set():
    # der reale Fehl-Match: 10305 Ritterburg in einer 10294-Suche
    assert _is_relevant("Lego 10305 Lion Knights Castle Ritterburg", "LEGO Titanic 10294") is False


def test_relevant_requires_all_query_numbers():
    assert _is_relevant("LEGO 42143 Ferrari Daytona SP3", "LEGO 42143") is True
    assert _is_relevant("LEGO 42115 Lamborghini", "LEGO 42143") is False


def test_relevant_ean_query_fail_open():
    # reine EAN-Query -> kein Titel-Pflichttoken -> nicht faelschlich alles wegfiltern
    assert _is_relevant("irgendein beliebiger Titel", "4013575044542") is True


def test_relevant_alpha_majority_without_number():
    assert _is_relevant("Sonic the Hedgehog Plush Toy", "Sonic Hedgehog Plush") is True
    assert _is_relevant("Mario Kart Poster", "Sonic Hedgehog Plush") is False


# ══════════════════════════════════════════════════════════════════════════════
# _parse_items — Sold-Marker-Pflicht, Relevanz, kein limit-Schnitt
# ══════════════════════════════════════════════════════════════════════════════

def _card(item_id, title, price, sold=True):
    marker = ('<span aria-label="Verkaufter Artikel">Verkauft 24. Mai 2026</span>'
              if sold else '')
    return (
        f'<li class="s-card"><a href=https://www.ebay.de/itm/{item_id}>x</a>'
        f'<div class=s-card__title><span>{title}</span></div>'
        f'<span class="s-card__price">EUR {price}</span>{marker}</li>'
    )


def test_parse_items_requires_sold_marker():
    html = _card("111", "LEGO 10294 Titanic", "550,00", sold=False) \
         + _card("222", "LEGO 10294 Titanic", "560,00", sold=True)
    ids = [i.item_id for i in _parse_items(html, query="LEGO 10294")]
    assert ids == ["222"]   # aktiver/verwandter Fuelltreffer (ohne Marker) faellt raus


def test_parse_items_applies_relevance_gate():
    html = _card("111", "LEGO 10294 Titanic", "560,00") \
         + _card("222", "LEGO 10305 Ritterburg", "540,00")   # falsches Set
    ids = [i.item_id for i in _parse_items(html, query="LEGO 10294")]
    assert ids == ["111"]


def test_parse_items_no_limit_slice():
    # _parse_items schneidet NICHT auf ein limit; der volle Pool geht an sold.py
    html = "".join(_card(str(i), "LEGO 10294 Titanic", "560,00") for i in range(70))
    assert len(_parse_items(html, query="LEGO 10294")) == 70


def test_parse_items_without_query_keeps_all_sold():
    html = _card("1", "Etwas ganz anderes", "10,00") + _card("2", "Noch was", "20,00")
    assert len(_parse_items(html)) == 2   # ohne query kein Relevanz-Filter


# ══════════════════════════════════════════════════════════════════════════════
# LIVE — echte Reproduzierbarkeit (Default: uebersprungen)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.mark.skipif(os.getenv("EBAY_LIVE") != "1",
                    reason="Live-Scrape; aktivieren mit EBAY_LIVE=1")
@pytest.mark.parametrize("query", ["LEGO Titanic 10294", "LEGO 42143", "LEGO 75452"])
def test_live_median_reproducible(query):
    """Zwei echte Scrapes derselben Query -> Median darf nicht driften."""
    import time
    from ebay import get_sold_listings
    creds = {"client_id": "x", "client_secret": "x", "env": "production"}
    r1 = get_sold_listings(query, creds)
    time.sleep(3)
    r2 = get_sold_listings(query, creds)
    if not (r1.median_price and r2.median_price):
        pytest.skip(f"kein Sold-Signal fuer {query} (transient/leer)")
    drift = abs(r1.median_price - r2.median_price)
    assert drift <= max(2.0, 0.05 * r1.median_price), (
        f"{query}: median driftet {r1.median_price} -> {r2.median_price}"
    )
    assert r1.count >= 5   # belastbare Basis
