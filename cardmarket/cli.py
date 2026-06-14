#!/usr/bin/env python3
"""
cardmarket  cli.py  v1.0.0
============================
Test-CLI fuer den MKM-Provider.

  python -m cardmarket.cli account
  python -m cardmarket.cli priceguide <idProduct>
  python -m cardmarket.cli offer <idProduct> [--commercial] [--country D]
  python -m cardmarket.cli bulk-stats        # Bulk-Dateien laden + Eckdaten
"""
from __future__ import annotations

import argparse
import json
import sys

from .client import CardmarketClient, load_config

__version__ = "1.0.0"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="cardmarket", description="MKM-Provider Test-CLI")
    ap.add_argument("--version", action="version", version=f"cardmarket v{__version__}")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("account")
    pg = sub.add_parser("priceguide"); pg.add_argument("id")
    of = sub.add_parser("offer"); of.add_argument("id")
    of.add_argument("--commercial", action="store_true"); of.add_argument("--country", default=None)
    sub.add_parser("bulk-stats")
    a = ap.parse_args(argv)

    cm = CardmarketClient(load_config())
    if a.cmd == "account":
        print(json.dumps(cm.account(), ensure_ascii=False))
    elif a.cmd == "priceguide":
        print(json.dumps(cm.get_price_guide(a.id), ensure_ascii=False))
    elif a.cmd == "offer":
        print(json.dumps(cm.get_cheapest_offer(a.id, commercial_only=a.commercial,
                                               country=a.country), ensure_ascii=False))
    elif a.cmd == "bulk-stats":
        pl = cm.fetch_product_list()
        pg = cm.fetch_price_guide()
        print(json.dumps({"products": len(pl), "priced": len(pg)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
