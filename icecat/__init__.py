"""
techndev-providers  icecat  v1.0.0
====================================
Icecat REST API Provider.
Extrahiert aus EAN2JTL (ean2jtl.py v3.15.0).

Exports:
  IcecatClient  — REST-Client fuer live.icecat.biz
"""
from .client import IcecatClient

__all__ = ["IcecatClient"]
__version__ = "1.1.0"
