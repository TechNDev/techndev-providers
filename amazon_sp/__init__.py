"""
amazon_sp  v1.7.0
==================
Amazon Selling Partner API Provider fuer TechNDev Tools.
Gemeinsame Bibliothek fuer EAN2JTL und amz-einkauf.

Oeffentliche API
----------------
  from amazon_sp import search_by_ean, search_by_asin   # Katalog (EAN/ASIN)
  from amazon_sp import search_catalog, search_by_brand  # Katalog (Stichwort/Marke)
  from amazon_sp import get_offers, get_item_price       # Preise & Angebote
  from amazon_sp import estimate_fba_fees                # FBA-Gebuehren (Summe)
  from amazon_sp import get_fees_breakdown               # FBA-Gebuehren (Detail)
  from amazon_sp import get_last_fee_error               # Letzter Fees-Fehler
  from amazon_sp import check_restrictions               # Verkaufserlaubnis
  from amazon_sp import CatalogResult, OffersResult      # Datenmodelle
  from amazon_sp import configure, get_credentials       # Credential-Management

Credential-Management
---------------------
  Alle Funktionen akzeptieren credentials=None (Auto-Load):
    1. Explizit uebergeben:  search_by_ean(ean, credentials={...})
    2. Modul konfigurieren:  amazon_sp.configure({'refresh_token': ...})
    3. Env-Var:              AMZ_EINKAUF_CONFIG=/pfad/amz_einkauf_config.json
    4. Auto-Discovery:       sucht amz_einkauf_config.json in uebergeordneten Dirs

Import-Pattern (Git-Submodul unter providers/)
----------------------------------------------
  import sys as _sys
  from pathlib import Path as _Path
  _PROV = _Path(__file__).resolve().parent / 'providers'
  if str(_PROV) not in _sys.path:
      _sys.path.insert(0, str(_PROV))

  from amazon_sp import search_by_ean, CatalogResult
"""
from .catalog      import CatalogResult, search_by_ean, search_by_asin, search_by_brand, search_catalog
from .pricing      import OffersResult, get_offers, get_item_price
from .fees         import estimate_fba_fees, get_fees_breakdown, get_last_fee_error
from .restrictions import check_restrictions
from ._credentials import configure, get_credentials

__version__ = "1.7.0"

__all__ = [
    # Credential-Management
    'configure',
    'get_credentials',
    # Datenmodelle
    'CatalogResult',
    'OffersResult',
    # Katalog
    'search_by_ean',
    'search_by_asin',
    'search_by_brand',
    'search_catalog',
    # Preise & Angebote
    'get_offers',
    'get_item_price',
    # Gebuehren
    'estimate_fba_fees',
    'get_fees_breakdown',
    'get_last_fee_error',
    # Verkaufserlaubnis
    'check_restrictions',
]
