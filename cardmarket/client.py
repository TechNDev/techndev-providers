#!/usr/bin/env python3
"""
cardmarket  client.py  v1.0.0
===============================
MKM-API-Client: OAuth1.0a (HMAC-SHA1, Dedicated-App), urllib, kein SDK.

Endpoints:
  /account                       Auth-Check
  /productlist                   Bulk: alle Produkte (gzip+base64 CSV)
  /priceguide                    Bulk: Preis-Guide aller aktiven Produkte (CSV)
  /products/{id}                 Einzel-Detail (priceGuide)
  /articles/{id}                 LIVE: aktuelle Angebote (preis-sortiert)

Die beiden Bulk-Dateien werden nur 1x/Tag serverseitig aktualisiert → fuer das
breite Screening gecacht (Default 24 h). Die Kaufentscheidung nutzt /articles
(Echtzeit).
"""
from __future__ import annotations

import base64
import csv
import gzip
import hashlib
import hmac
import io
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

__version__ = "1.0.0"

_BASE = "https://api.cardmarket.com/ws/v2.0/output.json"
_REQUIRED = ("app_token", "app_secret", "access_token", "access_secret")


# ── Config ────────────────────────────────────────────────────────────────────

def load_config(path: str | Path | None = None) -> dict:
    """
    Laedt MKM-Credentials. Reihenfolge:
      1. expliziter path
      2. Env CARDMARKET_CONFIG
      3. ./cardmarket_config.json (CWD — i.d.R. product-catalog)
      4. Aufwaerts-Suche ab dieser Datei (bis 5 Ebenen)
      5. Sibling product-catalog/cardmarket_config.json
    Akzeptiert {"cardmarket": {...}} oder direkte Felder.
    """
    candidates: list[Path] = []
    if path:
        candidates.append(Path(path))
    env = os.environ.get("CARDMARKET_CONFIG", "").strip()
    if env:
        candidates.append(Path(env))
    candidates.append(Path.cwd() / "cardmarket_config.json")
    here = Path(__file__).resolve()
    for up in here.parents[:5]:
        candidates.append(up / "cardmarket_config.json")
        candidates.append(up / "product-catalog" / "cardmarket_config.json")
    for c in candidates:
        try:
            if c.is_file():
                data = json.loads(c.read_text(encoding="utf-8"))
                sec = data.get("cardmarket", data)
                if all(sec.get(k) for k in _REQUIRED):
                    return {k: sec[k] for k in _REQUIRED}
        except Exception:                                  # noqa: BLE001
            continue
    raise RuntimeError(
        "cardmarket_config.json nicht gefunden/unvollstaendig "
        f"(brauche {_REQUIRED}). Gesucht u.a. in CWD + product-catalog/.")


def _enc(s) -> str:
    return urllib.parse.quote(str(s), safe="~")


# ── Client ────────────────────────────────────────────────────────────────────

class CardmarketClient:
    def __init__(self, creds: dict, *, cache_dir: str | Path | None = None,
                 cache_ttl_s: int = 24 * 3600):
        self.creds = creds
        self.cache_dir = Path(cache_dir or (Path.home() / ".cache" / "techndev" / "cardmarket"))
        self.cache_ttl_s = cache_ttl_s

    # ── OAuth1 ──────────────────────────────────────────────────────────────
    def _auth_header(self, method: str, url: str) -> str:
        oauth = {
            "oauth_consumer_key":     self.creds["app_token"],
            "oauth_token":            self.creds["access_token"],
            "oauth_nonce":            os.urandom(8).hex(),
            "oauth_timestamp":        str(int(time.time())),
            "oauth_signature_method": "HMAC-SHA1",
            "oauth_version":          "1.0",
        }
        p = urllib.parse.urlsplit(url)
        base_url = f"{p.scheme}://{p.netloc}{p.path}"
        allp = dict(urllib.parse.parse_qsl(p.query))
        allp.update(oauth)
        pstr = "&".join(f"{_enc(k)}={_enc(allp[k])}" for k in sorted(allp))
        base = f"{method}&{_enc(base_url)}&{_enc(pstr)}"
        key = f"{_enc(self.creds['app_secret'])}&{_enc(self.creds['access_secret'])}"
        oauth["oauth_signature"] = base64.b64encode(
            hmac.new(key.encode(), base.encode(), hashlib.sha1).digest()).decode()
        parts = [f'realm="{base_url}"'] + [f'{k}="{_enc(v)}"' for k, v in oauth.items()]
        return "OAuth " + ", ".join(parts)

    def _get(self, path: str, timeout: int = 180):
        url = _BASE + path
        req = urllib.request.Request(url, headers={"Authorization": self._auth_header("GET", url)})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            # 206 (Partial Content, paginierte Artikel) ist OK
            body = e.read().decode("utf-8", "replace")
            if e.code == 206:
                try:
                    return 206, json.loads(body)
                except Exception:                          # noqa: BLE001
                    pass
            raise RuntimeError(f"MKM {path} -> HTTP {e.code}: {body[:160]}") from e

    # ── Auth-Check ──────────────────────────────────────────────────────────
    def account(self) -> dict:
        return (self._get("/account")[1] or {}).get("account", {})

    # ── Bulk-Dateien (gecacht) ──────────────────────────────────────────────
    def _cached_csv(self, path: str, file_key: str, force: bool,
                    cache_key: str | None = None) -> str:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        cache = self.cache_dir / f"{cache_key or file_key}.csv"
        if not force and cache.is_file() and (time.time() - cache.stat().st_mtime) < self.cache_ttl_s:
            return cache.read_text(encoding="utf-8")
        payload = self._get(path)[1] or {}
        raw = payload.get(file_key)
        if not raw:
            raise RuntimeError(f"MKM {path}: kein '{file_key}' in der Antwort")
        text = gzip.decompress(base64.b64decode(raw)).decode("utf-8", "replace")
        cache.write_text(text, encoding="utf-8")
        return text

    def fetch_product_list(self, force: bool = False) -> list[dict]:
        """Alle Produkte (alle Spiele): [{idProduct, Name, Category ID, Category,
        Expansion ID, Metacard ID, Date Added}]."""
        return list(csv.DictReader(io.StringIO(self._cached_csv(
            "/productlist", "productsfile", force))))

    def fetch_price_guide(self, id_game: int | None = None, force: bool = False) -> dict[str, dict]:
        """Preis-Guide je idProduct: {low, low_ex, trend, sell, de_pro_low, uvp,
        avg7, avg30}. Der Bulk ist SPIELSPEZIFISCH — ohne id_game = Magic (Default).
        Fuer andere Spiele id_game setzen (Pokémon=6, One Piece=18, Lorcana=19)."""
        path = "/priceguide" + (f"?idGame={id_game}" if id_game else "")
        ck = "priceguidefile" if not id_game else f"priceguide_{id_game}"
        out: dict[str, dict] = {}
        for r in csv.DictReader(io.StringIO(self._cached_csv(
                path, "priceguidefile", force, cache_key=ck))):
            pid = r.get("idProduct")
            if not pid:
                continue
            out[pid] = {
                "low":        _f(r.get("Low Price")),
                "low_ex":     _f(r.get("Low Price Ex+")),
                "trend":      _f(r.get("Trend Price")),
                "sell":       _f(r.get("Avg. Sell Price")),
                "de_pro_low": _f(r.get("German Pro Low")),
                "uvp":        _f(r.get("Suggested Price")),
                "avg7":       _f(r.get("AVG7")),
                "avg30":      _f(r.get("AVG30")),
            }
        return out

    # ── Einzel-Detail / LIVE ────────────────────────────────────────────────
    def get_price_guide(self, id_product) -> dict:
        pr = (self._get(f"/products/{id_product}")[1] or {}).get("product", {})
        return pr.get("priceGuide", {}) or {}

    def get_cheapest_offer(self, id_product, *, commercial_only: bool = False,
                           country: str | None = None, min_count: int = 1,
                           condition: str = "MT", max_results: int = 20) -> dict | None:
        """
        LIVE guenstigstes passendes Angebot via /articles (preis-sortiert).
        Filter: optional gewerblich + Land + Mindestmenge. Rueckgabe:
        {price, shipping, count, is_commercial, country, vat, seller, condition}
        oder None.
        """
        path = (f"/articles/{id_product}?minCondition={condition}"
                f"&start=0&maxResults={max_results}")
        arts = (self._get(path, timeout=60)[1] or {}).get("article", []) or []
        for a in arts:                                     # bereits aufsteigend nach Preis
            s = a.get("seller", {}) or {}
            addr = s.get("address", {}) or {}
            if commercial_only and not s.get("isCommercial"):
                continue
            if country and addr.get("country") != country:
                continue
            if (a.get("count") or 0) < min_count:
                continue
            return {
                "price":         _f(a.get("price")),
                "shipping":      _f(a.get("shippingCost")) or 0.0,
                "count":         a.get("count"),
                "is_commercial": bool(s.get("isCommercial")),
                "country":       addr.get("country"),
                "vat":           s.get("vat") or None,
                "seller":        s.get("username"),
                "condition":     a.get("condition"),
            }
        return None


def _f(v):
    try:
        f = float(v)
        return f if f != 0 else None
    except (TypeError, ValueError):
        return None
