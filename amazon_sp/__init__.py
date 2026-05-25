"""
amazon_sp  v1.0.0
==================
Amazon Selling Partner API Provider fuer TechNDev Tools.
Gemeinsame Bibliothek fuer EAN2JTL und amz-einkauf.

Oeffentliche API
----------------
  from amazon_sp import search_by_ean, search_by_asin   # Katalog
  from amazon_sp import get_offers, get_item_price       # Preise & Angebote
  from amazon_sp import estimate_fba_fees                # FBA-Gebuehren
  from amazon_sp import check_restrictions               # Verkaufserlaubnis
  from amazon_sp import CatalogResult, OffersResult      # Datenmodelle

Import-Pattern (Git-Submodul unter providers/)
----------------------------------------------
  import sys as _sys
  from pathlib import Path as _Path
  _PROV = _Path(__file__).resolve().parent / 'providers'
  if str(_PROV) not in _sys.path:
      _sys.path.insert(0, str(_PROV))

  from amazon_sp import search_by_ean, CatalogResult
"""
from .catalog      import CatalogResult, search_by_ean, search_by_asin
from .pricing      import OffersResult, get_offers, get_item_price
from .fees         import estimate_fba_fees
from .restrictions import check_restrictions

__version__ = "1.0.0"

__all__ = [
    # Datenmodelle
    'CatalogResult',
    'OffersResult',
    # Katalog
    'search_by_ean',
    'search_by_asin',
    # Preise & Angebote
    'get_offers',
    'get_item_price',
    # Gebuehren
    'estimate_fba_fees',
    # Verkaufserlaubnis
    'check_restrictions',
]
