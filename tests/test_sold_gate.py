#!/usr/bin/env python3
"""
Regressionstests fuer die Gate-Erkennung des eBay-Sold-Scrapers (scraper v1.5.0).

Hintergrund: Am 2026-07-23 hat eBay die Sold-Listings hinter den Login gestellt.
/sch/i.html?LH_Sold=1 antwortet seither mit 302 auf signin.ebay.de. Die Signin-
Seite kommt mit HTTP 200 und ~120 KB — damit griff weder raise_for_status() noch
die alte Groessenheuristik _is_challenge() (< 100 KB). Der Scraper lieferte
(None, [], None), also "erfolgreich 0 Verkaeufe", und der product-catalog schrieb
leere ebay_market-Zeilen ohne jede Fehlermeldung.

Kernaussage dieser Tests: eine leere Item-Liste OHNE error ist nur zulaessig,
wenn wirklich eine Suchergebnisseite geparst wurde.
"""
import pytest

from ebay.scraper import _gate_reason, _is_challenge, _is_signin


# ── Test-Doubles ──────────────────────────────────────────────────────────────

class _Resp:
    """Minimaler requests.Response-Ersatz fuer die Gate-Pruefung."""

    def __init__(self, url, text='', status_code=200, history=()):
        self.url         = url
        self.text        = text
        self.status_code = status_code
        self.history     = list(history)

    @property
    def content(self):
        return self.text.encode('utf-8')


class _Redirect:
    def __init__(self, url, location):
        self.url     = url
        self.headers = {'Location': location}


_SRP  = 'x' * 200_000 + '<li class="s-card"><div class=s-card__title>Test</div></li>'
_SIGN = 'y' * 120_000   # Signin-Seite: gross genug, dass die Groessenheuristik versagt


# ── Login-Gate (der eigentliche Regressionsfall) ──────────────────────────────

def test_signin_redirect_via_history_is_detected():
    """302 -> signin.ebay.de muss erkannt werden, auch wenn die Zielseite gross ist."""
    resp = _Resp(
        url='https://signin.ebay.de/ws/eBayISAPI.dll?SignIn&siteid=77',
        text=_SIGN,
        history=[_Redirect('https://www.ebay.de/sch/i.html?LH_Sold=1',
                           'https://signin.ebay.de/ws/eBayISAPI.dll?SignIn')],
    )
    reason = _gate_reason(resp)
    assert reason is not None
    assert 'Login' in reason


def test_signin_page_larger_than_challenge_threshold_still_flagged():
    """Der alte Bug: 120 KB > 100 KB -> _is_challenge sagte False -> stille Leermenge."""
    resp = _Resp(url='https://signin.ebay.de/ws/eBayISAPI.dll?SignIn', text=_SIGN)
    assert len(resp.content) > 100_000        # genau die Groesse, die frueher durchrutschte
    assert _is_signin(resp)
    assert _is_challenge(resp) is True


def test_signin_detected_from_final_url_without_history():
    resp = _Resp(url='https://signin.ebay.com/ws/eBayISAPI.dll?SignIn', text=_SIGN)
    assert 'Login' in _gate_reason(resp)


# ── Captcha / Challenge ───────────────────────────────────────────────────────

def test_captcha_splash_is_detected_regardless_of_size():
    resp = _Resp(url='https://www.ebay.de/splashui/captcha?ap=2', text='z' * 300_000)
    assert 'Captcha' in _gate_reason(resp)


def test_akamai_challenge_page_is_detected():
    resp = _Resp(url='https://www.ebay.de/sch/i.html',
                 text='Bitte entschuldigen Sie die Stoerung')
    assert 'Challenge' in _gate_reason(resp)


# ── Struktur-Check: keine SRP = keine Marktaussage ────────────────────────────

def test_page_without_srp_container_is_not_a_result_page():
    """Layout-Aenderung/Soft-Block: lieber ein Fehler als eine erfundene Null."""
    resp = _Resp(url='https://www.ebay.de/sch/i.html', text='<html><body>ok</body></html>')
    reason = _gate_reason(resp)
    assert reason is not None and 'Suchergebnisseite' in reason


def test_real_srp_passes_the_gate():
    """Echte Trefferseite -> kein Gate -> leere Item-Liste waere eine echte Aussage."""
    resp = _Resp(url='https://www.ebay.de/sch/i.html?LH_Sold=1', text=_SRP)
    assert _gate_reason(resp) is None
    assert _is_challenge(resp) is False


def test_empty_srp_with_zero_hits_is_allowed():
    """0 Treffer auf einer echten SRP ist KEIN Fehler — nur das darf leer sein."""
    resp = _Resp(url='https://www.ebay.de/sch/i.html',
                 text='<div class="srp-results">Keine Ergebnisse</div>' + 'p' * 150_000)
    assert _gate_reason(resp) is None


# ── Live-Check (optional): dokumentiert den aktuellen eBay-Zustand ────────────

@pytest.mark.skipif(not __import__('os').environ.get('EBAY_LIVE'),
                    reason="Live-Test: mit EBAY_LIVE=1 aktivieren")
def test_live_sold_scrape_reports_error_not_silent_empty():
    """
    Solange das Login-Gate steht, MUSS scrape_sold einen Fehler melden.
    Faellt das Gate (oder kommt die MI-API-Freigabe), schlaegt dieser Test um —
    dann liefert der Scraper wieder Items und der Test ist das Signal, den
    Veraltet-Hinweis im product-catalog wieder abzuschalten.
    """
    from ebay.scraper import scrape_sold
    total, items, error = scrape_sold('LEGO 10294', 'EBAY_DE', 50)
    assert not (items == [] and error is None), \
        "Stille Leermenge: genau der Bug, der ab 2026-07-23 leere Zeilen erzeugt hat"
