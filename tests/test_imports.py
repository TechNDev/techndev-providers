#!/usr/bin/env python3
"""Smoke-Tests: jedes Provider-Paket ist installiert + importierbar (kein Netzwerk,
keine Credentials beim Import). Verifiziert, dass das Packaging alle Top-Level-
Pakete erfasst und die Importe nebenwirkungsfrei sind.
"""
import importlib

import pytest

# (Modul, erwartetes oeffentliches Symbol)
STANDALONE = [
    ("amazon_sp", "search_by_ean"),
    ("ebay", "get_market_snapshot"),
    ("icecat", "IcecatClient"),
    ("brickmerge", "get_catalog"),
    ("cubegolem", "CubeGolemProvider"),
    ("gsheets", "GSheetsClient"),
]


@pytest.mark.parametrize("mod_name,symbol", STANDALONE)
def test_package_imports_and_exposes_symbol(mod_name, symbol):
    mod = importlib.import_module(mod_name)
    assert hasattr(mod, symbol), f"{mod_name}.{symbol} fehlt"


@pytest.mark.parametrize("mod_name,_s", STANDALONE)
def test_package_has_version(mod_name, _s):
    mod = importlib.import_module(mod_name)
    assert isinstance(getattr(mod, "__version__", None), str)


def test_pipelines_importable_or_needs_sibling():
    # pipelines.arbitrage importiert reseller_profitability (Schwester-Repo, kein
    # PyPI-Dep). Wenn vorhanden -> Import muss klappen; sonst sauber ueberspringen.
    try:
        import reseller_profitability  # noqa: F401
    except ImportError:
        pytest.skip("reseller_profitability nicht installiert (Schwester-Repo)")
    pipelines = importlib.import_module("pipelines")
    assert hasattr(pipelines, "evaluate_arbitrage")
